import os
import sys
import json
import pandas as pd

from analyzer.slang_frontend import run_slang_frontend, print_slang_report
from analyzer.ast_engine import analyze_rtl

def to_df(rows):
    cols = ["condition_id", "module", "always_block", "line", "column",
            "condition", "effective_condition", "fan_in", "reason"]
    if not rows:
        return pd.DataFrame(columns=cols)
    
    dict_rows = []
    for r in rows:
        fan = ", ".join(
            f"{s['name']}({s['category']}" + (f"->{s['resolved_module']}.{s['resolved_role']})" if s.get("resolved_module") else ")")
            for s in r["fan_in"]
        )
        dict_rows.append({
            "condition_id": r["condition_id"],
            "module": r["module"],
            "always_block": r["always_block"],
            "line": r["line"],
            "column": r["column"],
            "condition": r["condition"],
            "effective_condition": r["effective_condition"],
            "fan_in": fan,
            "reason": r["reason"],
        })
    return pd.DataFrame(dict_rows)

def main():
    print("="*78)
    print(" RTL Analysis Engine - CLI ")
    print("="*78)

    if len(sys.argv) > 1:
        file_path = sys.argv[1]
    else:
        file_path = input("Enter the path to your .v or .sv file: ").strip().strip('"').strip("'")

    if not os.path.isfile(file_path):
        print(f"Error: File '{file_path}' not found.")
        sys.exit(1)

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    file_name = os.path.basename(file_path)

    print("\n" + "=" * 78)
    print("STAGE 1a  —  Slang setup & parse")
    print("=" * 78)
    slang_result = run_slang_frontend(source)
    print_slang_report(slang_result)

    print("\n" + "=" * 78)
    print("STAGE 1b  —  AST traversal & classification")
    print("=" * 78)
    result = analyze_rtl(source, file_name=file_name)
    print(json.dumps(result["summary"], indent=2))

    print("\n--- MODULE HIERARCHY ---")
    if not result["hierarchy_edges"]:
        print("  (no instantiations found)")
    for e in result["hierarchy_edges"]:
        resolved = "" if e["child_resolved"] else "  [unresolved/external]"
        print(f"  {e['parent_module']} --instantiates--> {e['instance_name']} : {e['child_module']}{resolved}  (line {e['line']})")

    # Configure pandas to look good in a wide terminal
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 1000)

    print("\nType A — Pure Combinational")
    print(to_df(result["type_a"]))
    
    print("\nType B — Single Register")
    print(to_df(result["type_b"]))
    
    print("\nType C — Complex / Cross-Module")
    print(to_df(result["type_c"]))

    if result["fsm_info"]:
        print("\nFSM registers")
        print(pd.DataFrame(result["fsm_info"]))

    if result["register_dependency_graph"]:
        print("\nRegister dependency graph")
        print(pd.DataFrame(result["register_dependency_graph"]))

    out_path = "rtl_analysis_output.json"
    with open(out_path, "w") as f:
        json.dump({"slang_frontend": slang_result, **result}, f, indent=2)
        
    print(f"\n[SUCCESS] Structured RTL representation saved -> {os.path.abspath(out_path)}")

if __name__ == "__main__":
    main()