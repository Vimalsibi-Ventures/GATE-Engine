"""
augmentation/generator.py
Reads the augmentation report (built by report.py) and generates augmented_tb.sv.

Strategy per condition type
---------------------------
Type A  : one weighted-random initial block per primary_input fan-in signal
Type B  : one task per register/fsm_state fan-in signal
           FSM  → go_to_<COND_ID>_<STATE>() skeleton
           reg  → warmup_<COND_ID>_<sig>() write-driver loop
Type C  : one bounded force/release task per force_release fan-in signal

Run block calls each task by its exact generated name — no wildcards.
"""

import os
from typing import Dict, Any, List, Set, Tuple

from .templates import (
    file_header, interface_block, interface_instantiation,
    dut_instantiation, clock_driver, reset_driver,
    type_a_constraint, type_b_warmup_task, type_b_fsm_sequence,
    type_c_force_stub, fsdb_dump_block, tb_module_wrapper, run_block,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _active_low_reset(name: str) -> bool:
    n = name.lower()
    return n.endswith("_n") or n.endswith("_b") or "n_" in n


def _fsm_transitions_for(report: Dict, reg_name: str) -> List[Dict]:
    for info in report.get("fsm_info", []):
        if info["register"] == reg_name:
            return info.get("transitions", [])
    return []


def _fsm_states_for(report: Dict, reg_name: str) -> List[str]:
    for info in report.get("fsm_info", []):
        if info["register"] == reg_name:
            return info.get("states", [])
    return []


# ── Main generation function ──────────────────────────────────────────────────

def generate_testbench(report: Dict[str, Any], out_dir: str) -> str:
    """
    Generate augmented_tb.sv from the augmentation report.
    Returns the path of the written file.
    """
    top_module  = report["top_module"]
    ports       = report["ports"]               # name → direction
    port_widths = report.get("port_widths", {}) # name → "[hi:lo]" or None
    clocks      = report["clock_signals"]
    resets      = report["reset_signals"]
    project     = report["meta"]["project"]
    timestamp   = report["meta"]["timestamp"]

    clk_name = clocks[0] if clocks else "clk"
    rst_name = resets[0] if resets else None

    # Filter out reset-related and skipped conditions
    conditions = [
        c for c in report.get("conditions", [])
        if not c.get("likely_reset_related") and not c.get("skip_condition")
    ]

    # ── Collect body sections ─────────────────────────────────────────────────
    body_sections: List[str] = []

    # 1. Clock declaration + driver
    body_sections.append(clock_driver(clk_name))

    # 2. Interface instantiation (clk drives it)
    body_sections.append(interface_instantiation(top_module, clk_name))

    # 3. Reset driver
    if rst_name:
        body_sections.append(reset_driver(rst_name, _active_low_reset(rst_name)))

    # 4. DUT instantiation
    body_sections.append(dut_instantiation(top_module, ports, clk_name))

    # 5. FSDB dump
    body_sections.append(fsdb_dump_block(top_module))

    # ── Per-condition stimulus ────────────────────────────────────────────────
    generated_a:  Set[str] = set()   # (cid, sig_name)
    generated_b:  Set[str] = set()   # (cid, sig_name)
    generated_c:  Set[str] = set()   # (cid, sig_path)

    type_a_count = 0
    type_b_calls: List[str] = []
    type_c_calls: List[str] = []

    for cond in conditions:
        cid    = cond["condition_id"]
        cls    = cond["classification"]
        fan_in = cond.get("fan_in", [])

        # Primary-input signals (used as driver hints for Type B)
        primary_inputs = [s["name"] for s in fan_in
                          if s.get("controllability") == "primary_input"]

        if cls == "Type A":
            type_a_count += 1
            for sig in fan_in:
                if sig.get("controllability") == "primary_input":
                    key = (cid, sig["name"])
                    if key not in generated_a:
                        generated_a.add(key)
                        body_sections.append(
                            type_a_constraint(cid, sig["name"])
                        )

        elif cls == "Type B":
            for sig in cond.get("warmup_signals", []):
                key = (cid, sig["name"])
                if key in generated_b:
                    continue
                generated_b.add(key)

                if sig["category"] == "fsm_state":
                    transitions = _fsm_transitions_for(report, sig["name"])
                    states      = _fsm_states_for(report, sig["name"])
                    # Pick the most meaningful target state — prefer the
                    # state referenced in the condition itself
                    cond_text = cond.get("condition", "")
                    target = next(
                        (s for s in states if s in cond_text),
                        states[0] if states else "TARGET_STATE"
                    )
                    body_sections.append(
                        type_b_fsm_sequence(
                            cid, sig["name"], target,
                            transitions, primary_inputs
                        )
                    )
                    type_b_calls.append(f"go_to_{cid}_{target}()")

                else:
                    driver_sig = primary_inputs[0] if primary_inputs else "wr_en"
                    depth      = sig.get("warmup_depth", 16)
                    body_sections.append(
                        type_b_warmup_task(cid, sig["name"], driver_sig, depth)
                    )
                    type_b_calls.append(f"warmup_{cid}_{sig['name']}()")

        elif cls == "Type C":
            for sig in cond.get("force_signals", []):
                sig_path = (
                    f"{sig['instance']}.{sig['name']}"
                    if sig.get("instance") else sig["name"]
                )
                key = (cid, sig_path)
                if key in generated_c:
                    continue
                generated_c.add(key)
                body_sections.append(type_c_force_stub(cid, sig_path))
                safe = sig_path.replace(".", "_")
                type_c_calls.append(f"force_{cid}_{safe}()")

    # 6. Run block with exact task names
    body_sections.append(
        run_block(type_a_count, type_b_calls, type_c_calls)
    )

    # ── Assemble ──────────────────────────────────────────────────────────────
    header = file_header(project, timestamp, top_module)
    iface  = interface_block(top_module, ports, port_widths, clk_name)
    body   = "\n".join(body_sections)
    module = tb_module_wrapper(top_module, body)

    full_sv = header + "\n" + iface + "\n" + module

    out_path = os.path.join(out_dir, "augmented_tb.sv")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(full_sv)

    na = len(generated_a)
    nb = len(generated_b)
    nc = len(generated_c)
    print(f"  [generator]  augmented_tb.sv  "
          f"(A-drivers:{na}  B-tasks:{nb}  C-stubs:{nc}  total:{na+nb+nc})")
    return out_path