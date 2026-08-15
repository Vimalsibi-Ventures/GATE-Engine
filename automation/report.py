"""
augmentation/report.py
Transforms the raw analyze_rtl() output into a structured augmentation report
that the generator can consume directly.

Output: augmentation_report.json saved into the session results folder.

Report structure
----------------
{
  "meta": { project, timestamp, source_files, num_modules, ... },
  "top_module": "<name>",
  "ports": { name: direction, ... },          # from top-level module
  "clock_signals": ["clk", ...],
  "reset_signals": ["rst_n", ...],
  "conditions": [                             # one record per condition
    {
      "condition_id", "module", "always_block",
      "file", "line", "column",
      "condition", "effective_condition",
      "classification",                       # Type A / B / C
      "likely_reset_related",
      "fan_in": [ { ...signal... + controllability fields } ],
      "has_primary_inputs", "has_fsm_signals",
      "has_register_signals", "has_force_signals",
      "skip_condition",
      "warmup_signals": [ {name, warmup_depth} ],   # Type B helpers
      "force_signals":  [ {name} ],                 # Type C helpers
    }
  ],
  "fsm_info": [...],
  "register_dependency_graph": [...],
  "hierarchy_edges": [...],
  "summary": { type_a_count, type_b_count, type_c_count, ... }
}
"""

import json
import os
from datetime import datetime
import re
from typing import Dict, Any, List, Optional

from .classifier import classify_condition_signals


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_clock_reset(modules: List[Dict]) -> tuple:
    """
    Heuristically identify clock and reset port names from the top-level
    module's port list.  Returns (clock_names, reset_names).
    """
    import re
    clk_hints   = re.compile(r"clk|clock", re.IGNORECASE)
    rst_hints   = re.compile(r"rst|reset|clr|clear", re.IGNORECASE)

    clocks, resets = [], []
    if not modules:
        return clocks, resets

    top = modules[0]   # first module treated as top-level
    for port_name, direction in top.get("ports", {}).items():
        if direction != "input":
            continue
        if clk_hints.search(port_name):
            clocks.append(port_name)
        elif rst_hints.search(port_name):
            resets.append(port_name)

    return clocks, resets


def _top_module(modules: List[Dict]) -> Optional[Dict]:
    """Return the first module that is NOT instantiated by any other module
    in the design — i.e. the true top-level.  Falls back to modules[0]."""
    if not modules:
        return None
    instantiated = set()
    for m in modules:
        for inst in m.get("instances", []):
            instantiated.add(inst.get("child_module"))
    for m in modules:
        if m["name"] not in instantiated:
            return m
    return modules[0]


# ── Main entry point ──────────────────────────────────────────────────────────

def build_report(
    analysis: Dict[str, Any],
    project_name: str,
    timestamp: str,
    source_files: List[str],
    out_dir: str,
) -> Dict[str, Any]:
    """
    Build the augmentation report from the analyze_rtl() result dict.
    Saves augmentation_report.json into out_dir.
    Returns the report dict.
    """

    modules   = analysis.get("modules", [])
    top       = _top_module(modules)
    top_name  = top["name"] if top else "unknown"
    clocks, resets = _detect_clock_reset(modules)

    # Enrich every condition with controllability info
    all_conditions = analysis.get("conditions", [])
    enriched = []
    for cond in all_conditions:
        ec = classify_condition_signals(cond)

        # Convenience lists for generator
        warmup_signals = [
            {"name": s["name"], "warmup_depth": s.get("warmup_depth", 16),
             "category": s["controllability"]}
            for s in ec["fan_in"]
            if s.get("warmup_needed")
        ]
        force_signals = [
            {"name": s["name"], "category": s["controllability"]}
            for s in ec["fan_in"]
            if s.get("force_needed")
        ]

        enriched.append({
            **ec,
            "warmup_signals": warmup_signals,
            "force_signals":  force_signals,
        })

    # Split by classification for summary convenience
    type_a = [c for c in enriched if c["classification"] == "Type A"]
    type_b = [c for c in enriched if c["classification"] == "Type B"]
    type_c = [c for c in enriched if c["classification"] == "Type C"]

    report = {
        "meta": {
            "project":      project_name,
            "timestamp":    timestamp,
            "source_files": source_files,
            "generated_by": "RTL-Analyser augmentation/report.py",
        },
        "top_module":    top_name,
        "ports":         top.get("ports", {}) if top else {},
        "port_widths":   analysis.get("port_widths", {}).get(top_name, {}),
        "clock_signals": clocks,
        "reset_signals": resets,
        "conditions":    enriched,
        "type_a":        type_a,
        "type_b":        type_b,
        "type_c":        type_c,
        "fsm_info":                  analysis.get("fsm_info", []),
        "register_dependency_graph": analysis.get("register_dependency_graph", []),
        "hierarchy_edges":           analysis.get("hierarchy_edges", []),
        "summary": {
            **analysis.get("summary", {}),
            "top_module":       top_name,
            "clock_signals":    clocks,
            "reset_signals":    resets,
            "type_a_count":     len(type_a),
            "type_b_count":     len(type_b),
            "type_c_count":     len(type_c),
            "skipped_conditions": sum(1 for c in enriched if c.get("skip_condition")),
        },
    }

    out_path = os.path.join(out_dir, "augmentation_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"  [report]     augmentation_report.json  ({len(enriched)} conditions — "
          f"A:{len(type_a)} B:{len(type_b)} C:{len(type_c)})")
    return report