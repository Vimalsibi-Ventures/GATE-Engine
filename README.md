# RTL Analysis Engine

A lightweight, standalone Python CLI tool for structural SystemVerilog analysis. This engine parses RTL without requiring a full commercial synthesis tool, extracting clock-gating conditions, resolving hierarchy, and building register dependency graphs.

## Features

* **AST Parsing:** Uses `pyslang` to analyze SystemVerilog syntax and structure.
* **Condition Classification:** Evaluates `if` and `case` conditions across edge-triggered blocks (Type A: Combinational, Type B: Single Register, Type C: Complex/Cross-Module).
* **FSM Detection:** Automatically recognizes FSM state registers and maps transitions.
* **JSON Export:** Dumps a structured, machine-readable intelligence file for downstream testbench augmentation and simulation metrics.

## Folder Structure

```text
rtl_analyzer/
├── main.py                 # The CLI handler
├── requirements.txt        # Python dependencies
├── analyzer/               # Core package
│   ├── models.py           # Dataclasses
│   ├── slang_frontend.py   # pyslang AST parsing wrapper
│   ├── utils.py            # Helpers & regex matchers
│   └── ast_engine.py       # Core statement walking logic
```

## Setup & Installation

Create a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

Run the engine by passing your SystemVerilog file as an argument:

```bash
python main.py path/to/your/design.sv
```