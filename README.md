# GATE Engine (Gating Analysis & Targetted Exercise Engine)

A comprehensive, two-phase Python CLI pipeline designed to automatically evaluate and optimize Clock-Gating Efficiency (CGE) in SystemVerilog designs.

The GATE Engine operates entirely locally without requiring a commercial synthesis tool. It structurally analyzes entire RTL project directories, resolves cross-module hierarchies, builds a strategic testing plan, and automatically synthesizes a SystemVerilog testbench specifically targeted to activate "starved" clock-gating conditions.

---

## 🏗️ Architecture & Workflow

The engine is split into two distinct, decoupled pipelines to ensure a clean separation of concerns between static logic extraction and dynamic testbench generation.

### Phase 1: Static Analyzer (`analyzer/`)

Extracts the "Anatomy" of the design.

* **Multi-File AST Parsing:** Ingests entire RTL directories, concatenating sources to resolve cross-module instantiations globally while preserving exact file/line traceability.
* **Condition Classification:** Categorizes `if` and `case` conditions into Type A (Combinational), Type B (Single Register/FSM), and Type C (Complex/Cross-Module).
* **Dependency Mapping:** Automatically identifies FSM state registers, maps state transitions, and builds a complete register dependency graph.
* **Output:** Generates `rtl_analysis_output.json`.

### Phase 2: Testbench Augmentation (`augmentation/`)

Builds the "Battle Plan" and executes it.

* **Testability Classification:** Analyzes the static anatomy to determine exactly how each fan-in signal can be controlled from a testbench (e.g., primary inputs, FSM warm-ups, hierarchical forces).
* **Strategic Planning:** Transforms the raw AST data into an actionable testing strategy, exporting it as `augmentation_report.json`.
* **Targeted Generation:** Reads the battle plan and generates a ready-to-run `augmented_tb.sv`.

  * **Type A:** Uses `$urandom_range()` for direct primary input constraints.
  * **Type B:** Generates sequential warm-up tasks and targeted FSM state-driver sequences.
  * **Type C:** Implements bounded `force`/`release` sequences as a fallback for buried logic.

---

## 📂 Directory Structure

```text
GATE-Engine/
├── main.py                     # CLI entry point orchestrating both pipelines
├── requirements.txt            # Python dependencies
├── analyzer/                   # Phase 1: Structural Analysis
│   ├── ast_engine.py           # Core AST walking & hierarchy mapping
│   ├── models.py               # Strict dataclasses for logic representation
│   ├── slang_frontend.py       # pyslang wrapper and diagnostics
│   └── utils.py                # Regex matchers & string manipulation
└── augmentation/               # Phase 2: Testbench Generation
    ├── classifier.py           # Signal controllability mapping
    ├── report.py               # Builds the strategic augmentation_report.json
    ├── generator.py            # Orchestrates testbench creation
    └── templates.py            # SystemVerilog string templates
```

---

## 🚀 Setup & Installation

### 1. Create and activate a clean virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Install required dependencies

```bash
pip install -r requirements.txt
```

> **Requires Python 3.8+**

---

## 💻 Usage

The CLI handles single files, multiple explicit files, or entire directories recursively.

### Analyze a single file

```bash
python main.py path/to/design.sv
```

### Analyze multiple specific files

```bash
python main.py file1.sv file2.sv file3.v
```

### Analyze an entire RTL project directory *(Recommended)*

```bash
python main.py path/to/rtl_project/
```

---

## 📤 Outputs Generated

Every run generates a timestamped session folder containing the three core outputs:

```text
results/<project_name>_YYYYMMDD_HHMMSS/
├── rtl_analysis_output.json    # The raw structural logic extraction
├── augmentation_report.json    # The refined controllability plan
└── augmented_tb.sv             # The ready-to-run targeted testbench
```

---

## 📝 Known Limitations

* **FSM Heuristics:** Register roles are determined via heuristics (e.g., acting as `case` selectors or being compared against ALL_CAPS constants).
* **Condition Normalization:** Only top-level `&&` terms are normalized. Ternary operators and complex De Morgan-equivalent forms are mapped as written.