"""
augmentation/classifier.py
Determines how each fan-in signal can be reached / controlled from a testbench.
Shared by report.py and generator.py — no circular imports.

Controllability categories
--------------------------
primary_input     : module input port → directly driven by testbench rand variable
fsm_state         : FSM register → needs a directed warm-up sequence to reach the target state
register          : ordinary data register → needs warm-up (write to upstream inputs N times)
combinational_alias: wire assigned from a register/FSM → follow to underlying category
cross_module      : hierarchical / cross-module signal → force/release fallback
wire_internal     : internal combinational wire not from an assign alias → force/release fallback
constant          : parameter/localparam → no driving needed, skip
"""

from typing import Dict, Any


# ── Public constants ─────────────────────────────────────────────────────────

# Maps signal category → controllability tag
CATEGORY_TO_CTRL = {
    "input":              "primary_input",
    "output":             "observable_only",   # driven by DUT, not testbench
    "inout":              "primary_input",      # treat as bidirectional but driveable
    "register":           "register",
    "fsm_state":          "fsm_state",
    "combinational_alias": None,               # resolved below via resolved_role
    "cross_module":       "force_release",
    "wire_internal":      "force_release",
    "constant":           "skip",
}

# Warm-up depth estimate (conservative): used for Type B signals
# Actual depth validated by simulation — this seeds the generated task
WARMUP_DEPTH_MULTIPLIER = 2   # cycles = estimated_register_width * multiplier


# ── Core function ─────────────────────────────────────────────────────────────

def classify_signal_controllability(signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Takes one fan_in signal dict from analyze_rtl() output.
    Returns an enriched dict with:
        controllability : str   (primary_input | fsm_state | register |
                                 force_release | skip | observable_only)
        warmup_needed   : bool
        warmup_target   : str | None   (signal name to drive for warm-up)
        force_needed    : bool
        note            : str
    """
    cat = signal.get("category", "wire_internal")
    name = signal.get("name", "?")
    resolved_role = signal.get("resolved_role")
    width_str = signal.get("width")           # e.g. "[7:0]" or None

    # Resolve combinational_alias through its underlying role
    if cat == "combinational_alias":
        cat = resolved_role if resolved_role in ("register", "fsm_state") else "wire_internal"

    ctrl = CATEGORY_TO_CTRL.get(cat, "force_release")

    warmup_needed = ctrl in ("register", "fsm_state")
    force_needed  = ctrl == "force_release"

    # Estimate warm-up depth from bus width
    warmup_depth = None
    if warmup_needed and width_str:
        try:
            # parse [hi:lo] → width = hi - lo + 1
            import re
            m = re.match(r"\[(\d+)\s*:\s*(\d+)\]", width_str.strip())
            if m:
                w = int(m.group(1)) - int(m.group(2)) + 1
                warmup_depth = w * WARMUP_DEPTH_MULTIPLIER
        except Exception:
            pass
    if warmup_needed and warmup_depth is None:
        warmup_depth = 16   # safe default when width unknown

    note_map = {
        "primary_input":   "Directly driveable — add rand variable in testbench class.",
        "fsm_state":       "FSM register — generate go_to_state() directed sequence.",
        "register":        "Data register — generate warm-up task to seed the target value.",
        "force_release":   "Not directly reachable — use bounded force/release (Type C fallback).",
        "observable_only": "Output port — not driven by testbench; observe only.",
        "skip":            "Constant / parameter — no driving required.",
    }

    return {
        **signal,
        "controllability": ctrl,
        "warmup_needed":   warmup_needed,
        "warmup_depth":    warmup_depth,
        "force_needed":    force_needed,
        "note":            note_map.get(ctrl, ""),
    }


def classify_condition_signals(condition: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enriches a full condition record with classified fan_in entries.
    Returns a copy of the condition dict with fan_in replaced by enriched list,
    plus top-level flags:
        has_primary_inputs, has_fsm_signals, has_register_signals,
        has_force_signals, skip_condition
    """
    enriched_fan_in = [classify_signal_controllability(s) for s in condition.get("fan_in", [])]

    ctrls = {s["controllability"] for s in enriched_fan_in}

    return {
        **condition,
        "fan_in":              enriched_fan_in,
        "has_primary_inputs":  "primary_input" in ctrls,
        "has_fsm_signals":     "fsm_state" in ctrls,
        "has_register_signals":"register" in ctrls,
        "has_force_signals":   "force_release" in ctrls,
        "skip_condition":      ctrls == {"skip"} or not enriched_fan_in,
    }