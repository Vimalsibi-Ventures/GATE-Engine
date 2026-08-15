"""
main.py — RTL Analyser + Augmentation Engine CLI
=================================================
Usage
-----
Single file:
    python main.py path/to/design.sv

Directory (all .sv / .v files collected recursively):
    python main.py path/to/rtl_project/

Multiple explicit files:
    python main.py file1.sv file2.sv file3.v

Output
------
gate_results_YYYYMMDD_HHMMSS/ (saved in the target project directory)
    rtl_analysis_output.json
    augmentation_report.json
    augmented_tb.sv
"""

import os
import sys
import json
import glob
import textwrap
from datetime import datetime
from pathlib import Path

# ── Internal imports ──────────────────────────────────────────────────────────
from analyzer.ast_engine    import analyze_rtl
from analyzer.slang_frontend import run_slang_frontend
from augmentation.report    import build_report
from augmentation.generator import generate_testbench


# ── File collection ───────────────────────────────────────────────────────────

SUPPORTED_EXT = {".sv", ".v"}

def collect_files(args: list[str]) -> list[str]:
    """
    Accept: one directory, one file, or multiple files.
    Returns sorted list of absolute paths to .sv / .v files.
    """
    files = []
    for arg in args:
        p = Path(arg).resolve()
        if p.is_dir():
            for ext in SUPPORTED_EXT:
                files.extend(p.rglob(f"*{ext}"))
        elif p.is_file():
            if p.suffix in SUPPORTED_EXT:
                files.append(p)
            else:
                print(f"[warn] Skipping unsupported file type: {p}")
        else:
            print(f"[warn] Path not found: {p}")
    # Deduplicate and sort
    seen, out = set(), []
    for f in sorted(files):
        if f not in seen:
            seen.add(f)
            out.append(str(f))
    return out


# ── Project name derivation ───────────────────────────────────────────────────

def derive_project_name(args: list[str], files: list[str]) -> str:
    """
    If input is a directory → use directory name.
    If single file → use file stem.
    If multiple files → use common parent directory name, else 'project'.
    """
    if not files:
        return "project"
    p0 = Path(args[0]).resolve()
    if p0.is_dir():
        return p0.name
    if len(files) == 1:
        return Path(files[0]).stem
    # Multiple files — try common parent
    parents = {Path(f).parent for f in files}
    if len(parents) == 1:
        return parents.pop().name
    return "project"


# ── Results folder ────────────────────────────────────────────────────────────

def make_results_dir(target_root: str, project_name: str, timestamp: str) -> str:
    folder_name = f"gate_results_{timestamp}"
    out_dir = os.path.join(target_root, folder_name)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


# ── Source concatenation ──────────────────────────────────────────────────────

def concatenate_sources(file_paths: list[str]) -> tuple[str, list[str]]:
    """
    Read all files, concatenate with file-separator comments so the engine
    receives one source string while per-file attribution is still possible
    via line numbers.
    Returns (combined_source, list_of_paths_that_were_read).
    """
    parts = []
    read_ok = []
    for fp in file_paths:
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            parts.append(f"// === FILE: {fp} ===\n{content}\n")
            read_ok.append(fp)
        except OSError as e:
            print(f"[warn] Could not read {fp}: {e}")
    return "\n".join(parts), read_ok


# ── Pretty summary ────────────────────────────────────────────────────────────

def print_summary(analysis: dict, out_dir: str) -> None:
    s = analysis.get("summary", {})
    print()
    print("  ┌─────────────────────────────────────────┐")
    print("  │           RTL Analysis Summary           │")
    print("  ├─────────────────────────────────────────┤")
    print(f"  │  Modules          : {s.get('num_modules', 0):<20} │")
    print(f"  │  Sequential blocks: {s.get('num_sequential_blocks', 0):<20} │")
    print(f"  │  Conditions found : {s.get('num_conditions', 0):<20} │")
    print(f"  │    Type A         : {s.get('type_a_count', 0):<20} │")
    print(f"  │    Type B         : {s.get('type_b_count', 0):<20} │")
    print(f"  │    Type C         : {s.get('type_c_count', 0):<20} │")
    print(f"  │  FSM registers    : {s.get('num_fsm_registers', 0):<20} │")
    print(f"  │  Instances        : {s.get('num_instances', 0):<20} │")
    print("  └─────────────────────────────────────────┘")
    print()
    print(f"  Output folder: {out_dir}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print(textwrap.dedent("""\
            RTL Analyser + Augmentation Engine
            -----------------------------------
            Usage:
              python main.py <file.sv>
              python main.py <rtl_directory/>
              python main.py file1.sv file2.sv ...
        """))
        sys.exit(1)

    args = sys.argv[1:]

    # 1. Collect files
    files = collect_files(args)
    if not files:
        print("[error] No .sv / .v files found.")
        sys.exit(1)

    project_name = derive_project_name(args, files)
    timestamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Determine the target root folder to drop results into
    first_path = Path(args[0]).resolve()
    target_root = str(first_path if first_path.is_dir() else first_path.parent)
    
    out_dir = make_results_dir(target_root, project_name, timestamp)

    print()
    print(f"  Project  : {project_name}")
    print(f"  Files    : {len(files)}")
    for f in files:
        print(f"             {f}")
    print(f"  Output   : {out_dir}")
    print()

    # 2. Concatenate sources
    source, read_files = concatenate_sources(files)
    if not source.strip():
        print("[error] All source files were empty or unreadable.")
        sys.exit(1)

    # 3. Optional slang validation (non-fatal)
    print("  [slang]      validating syntax...")
    try:
        slang_result = run_slang_frontend(source)
        diags = slang_result.get("diagnostics", [])
        if diags:
            print(f"  [slang]      {len(diags)} diagnostic(s) — see rtl_analysis_output.json")
        else:
            print("  [slang]      OK")
    except Exception as e:
        print(f"  [slang]      skipped ({e})")
        diags = []

    # 4. RTL analysis
    print("  [analyzer]   running AST engine...")
    analysis = analyze_rtl(source, file_name=", ".join(read_files))
    analysis["slang_diagnostics"] = diags

    analysis_path = os.path.join(out_dir, "rtl_analysis_output.json")
    with open(analysis_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2)
    print(f"  [analyzer]   rtl_analysis_output.json  "
          f"({analysis['summary']['num_conditions']} conditions)")

    # 5. Augmentation report
    print("  [report]     building augmentation report...")
    report = build_report(
        analysis      = analysis,
        project_name  = project_name,
        timestamp     = timestamp,
        source_files  = read_files,
        out_dir       = out_dir,
    )

    # 6. Testbench generation
    print("  [generator]  synthesising testbench...")
    generate_testbench(report, out_dir)

    # 7. Summary
    print_summary(analysis, out_dir)


if __name__ == "__main__":
    main()