import re
from dataclasses import asdict
from typing import List, Dict, Optional, Any

from .models import (
    Signal, ConditionNode, RegisterAssignment, FSMInfo,
    SequentialBlockMeta, Instance, ModuleInfo
)
from .utils import (
    SV_KEYWORDS_STRICT, IDENT_RE, HIER_IDENT_RE, NUMBER_RE,
    strip_comments_and_strings, find_matching, line_of, col_of,
    _match_kw, _skip_ws, looks_reset_related, normalize_condition
)

# --- Regex Definitions ---
SIZED_LITERAL_RE = re.compile(r"\d*'[sS]?[bBoOdDhH][0-9a-fA-Fxz_XZ]+")
MODULE_HEADER_RE = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)")
ANSI_PORT_RE = re.compile(r"\b(input|output|inout)\b\s*(reg|wire|logic|signed|unsigned)?\s*(\[[^\]]*\])?\s*([A-Za-z_][A-Za-z0-9_$]*)")
PORT_DECL_RE = re.compile(r"\b(input|output|inout)\b\s*(reg|wire|logic|signed|unsigned)?\s*(\[[^\]]*\])?\s*([A-Za-z0-9_,\s$]+?)\s*;")
REG_DECL_RE = re.compile(r"\b(?:reg|logic)\b\s*(?:signed|unsigned)?\s*(\[[^\]]*\])?\s*([A-Za-z0-9_,\s$]+?)\s*(?:\[[^\]]*\])?\s*;")
PARAM_DECL_RE = re.compile(r"\b(?:parameter|localparam)\b\s*(?:\[[^\]]*\])?\s*([A-Za-z_][A-Za-z0-9_$]*)\s*=")
CONTINUOUS_ASSIGN_RE = re.compile(r"\bassign\s+([A-Za-z_][A-Za-z0-9_$]*)\s*(?:\[[^\]]*\])?\s*=\s*(.+?);", re.DOTALL)
ALWAYS_RE = re.compile(r"\balways(_ff)?\s*@\s*\(([^)]*)\)")
ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_$]*)\s*(?:\[[^\]]*\])?\s*(<=|=)\s*(.+?)\s*;?\s*$", re.DOTALL)
CONCAT_ASSIGN_RE = re.compile(r"^\s*\{([^{}]*)\}\s*(<=|=)\s*(.+?)\s*;?\s*$", re.DOTALL)
NONBLOCKING_ASSIGN_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_$]*(?:\s*\[[^\]]*\])?)\s*<=")
CONCAT_TARGET_RE = re.compile(r"\{([^{}]*)\}\s*<=")
CASE_SELECTOR_RE = re.compile(r"\bcase[xz]?\s*\(\s*([A-Za-z_][A-Za-z0-9_$]*)\s*\)")
EQ_COMPARE_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_$]*)\s*==\s*([A-Za-z_][A-Za-z0-9_$]*)\b")
CASE_LABEL_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_$]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_$]*)*)\s*:", re.MULTILINE)
CASE_LABEL_SPLIT_RE = re.compile(r"\s*,\s*")

# --- Parsers ---
def parse_statement(text: str, i: int):
    i = _skip_ws(text, i)
    n = len(text)
    if i >= n: return None, i

    for modifier in ("unique0", "unique", "priority"):
        if _match_kw(text, i, modifier):
            i = _skip_ws(text, i + len(modifier))
            break

    if _match_kw(text, i, "begin"):
        j = i + 5
        close = find_matching(text, "begin", "end", j)
        if close == -1: return {"kind": "other", "text": text[i:], "pos": i}, n
        stmts = parse_statement_list(text, j, close)
        return {"kind": "block", "stmts": stmts, "pos": i}, close + 3

    if _match_kw(text, i, "if"):
        j = _skip_ws(text, i + 2)
        if j < n and text[j] == "(":
            close_paren = find_matching(text, "(", ")", j + 1)
            cond_text = text[j + 1:close_paren].strip()
            after = _skip_ws(text, close_paren + 1)
            true_stmt, next_idx = parse_statement(text, after)
            save = _skip_ws(text, next_idx)
            if _match_kw(text, save, "else"):
                after_else = _skip_ws(text, save + 4)
                false_stmt, next_idx2 = parse_statement(text, after_else)
                return {"kind": "if", "condition": cond_text, "if_pos": i, "true": true_stmt, "false": false_stmt}, next_idx2
            return {"kind": "if", "condition": cond_text, "if_pos": i, "true": true_stmt, "false": None}, next_idx

    for case_kw in ("casez", "casex", "case"):
        if _match_kw(text, i, case_kw):
            j = _skip_ws(text, i + len(case_kw))
            if j < n and text[j] == "(":
                close_paren = find_matching(text, "(", ")", j + 1)
                selector = text[j + 1:close_paren].strip()
                endcase_m = re.search(r"\bendcase\b", text[close_paren:])
                if not endcase_m: return {"kind": "other", "text": text[i:], "pos": i}, n
                endcase_start = close_paren + endcase_m.start()
                items = parse_case_items(text, close_paren + 1, endcase_start)
                return {"kind": "case", "selector": selector, "items": items, "case_pos": i}, endcase_start + len("endcase")
            break

    for loop_kw in ("for", "while", "repeat"):
        if _match_kw(text, i, loop_kw):
            j = _skip_ws(text, i + len(loop_kw))
            if j < n and text[j] == "(":
                close_paren = find_matching(text, "(", ")", j + 1)
                if close_paren != -1:
                    after = _skip_ws(text, close_paren + 1)
                    body_stmt, next_idx = parse_statement(text, after)
                    return {"kind": "loop", "pos": i, "body": body_stmt}, next_idx

    if _match_kw(text, i, "forever"):
        j = _skip_ws(text, i + 7)
        body_stmt, next_idx = parse_statement(text, j)
        return {"kind": "loop", "pos": i, "body": body_stmt}, next_idx

    semi = text.find(";", i)
    if semi == -1: return {"kind": "other", "text": text[i:], "pos": i}, n
    return {"kind": "other", "text": text[i:semi + 1], "pos": i}, semi + 1

def parse_statement_list(text: str, start: int, end: int):
    stmts = []
    i = start
    while True:
        i = _skip_ws(text, i)
        if i >= end: break
        stmt, next_i = parse_statement(text, i)
        if stmt is None: break
        stmts.append(stmt)
        if next_i <= i: break
        i = next_i
    return stmts

def parse_case_items(text: str, start: int, end: int):
    items = []
    i = start
    while True:
        i = _skip_ws(text, i)
        if i >= end: break
        colon = text.find(":", i)
        if colon == -1 or colon > end: break
        label_blob = text[i:colon].strip()
        labels = [lbl.strip() for lbl in CASE_LABEL_SPLIT_RE.split(label_blob) if lbl.strip()]
        stmt, next_i = parse_statement(text, colon + 1)
        items.append({"labels": labels, "stmt": stmt, "pos": i})
        if next_i <= i: break
        i = next_i
    return items

# --- Fan-in Classification & Hierarchy ---
def extract_fan_in_identifiers(cond: str) -> List[str]:
    idents, seen, covered = [], set(), []
    for m in SIZED_LITERAL_RE.finditer(cond): covered.append((m.start(), m.end()))
    for m in HIER_IDENT_RE.finditer(cond):
        tok = m.group(0)
        covered.append((m.start(), m.end()))
        if tok not in seen:
            seen.add(tok)
            idents.append(tok)
    
    def inside(pos): return any(s <= pos < e for s, e in covered)
    
    for m in IDENT_RE.finditer(cond):
        if inside(m.start()): continue
        tok = m.group(0)
        if NUMBER_RE.match(tok) or tok in SV_KEYWORDS_STRICT or tok in seen: continue
        seen.add(tok)
        idents.append(tok)
    return idents

def resolve_alias_role(name: str, module: ModuleInfo, alias_map: Dict[str, str], depth: int = 0, seen=None):
    if seen is None: seen = set()
    if name in seen or depth > 6: return None
    seen.add(name)
    if name in module.fsm_states: return "fsm_state"
    if name in module.registers: return "register"
    if name in alias_map:
        for ident in extract_fan_in_identifiers(alias_map[name]):
            role = resolve_alias_role(ident, module, alias_map, depth + 1, seen)
            if role in ("register", "fsm_state"): return role
    return None

def classify_signal_flat(name: str, module: ModuleInfo, widths: Dict[str, Optional[str]], driver_map: Dict[str, str], alias_map: Optional[Dict[str, str]] = None) -> Signal:
    if name in module.parameters: return Signal(name, "constant")
    if name in module.fsm_states: return Signal(name, "fsm_state", width=widths.get(name), driven_by=driver_map.get(name))
    if name in module.registers: return Signal(name, "register", width=widths.get(name), driven_by=driver_map.get(name))
    if name in module.ports:
        d = module.ports[name]
        cat = "input" if d == "input" else ("output" if d == "output" else "inout")
        return Signal(name, cat, width=widths.get(name))
    if alias_map and name in alias_map:
        role = resolve_alias_role(name, module, alias_map)
        if role in ("register", "fsm_state"):
            return Signal(name, "combinational_alias", width=widths.get(name), resolved_role=role, alias_expr=alias_map[name])
    return Signal(name, "wire_internal", width=widths.get(name))

def classify_hierarchical(name: str, module: ModuleInfo, widths: Dict[str, Optional[str]], driver_map: Dict[str, str], module_registry: Dict[str, ModuleInfo], instance_maps: Dict[str, Dict[str, str]], all_widths: Dict[str, Dict[str, Optional[str]]], all_driver_maps: Dict[str, Dict[str, str]], all_alias_maps: Optional[Dict[str, Dict[str, str]]] = None, alias_map: Optional[Dict[str, str]] = None) -> Signal:
    if "." not in name:
        return classify_signal_flat(name, module, widths, driver_map, alias_map)

    head, rest = name.split(".", 1)
    inst_map = instance_maps.get(module.name, {})
    child_name = inst_map.get(head)
    if child_name and child_name in module_registry:
        child_module = module_registry[child_name]
        child_widths = all_widths.get(child_name, {})
        child_driver_map = all_driver_maps.get(child_name, {})
        child_alias_map = (all_alias_maps or {}).get(child_name, {})
        resolved = classify_hierarchical(rest, child_module, child_widths, child_driver_map, module_registry, instance_maps, all_widths, all_driver_maps, all_alias_maps, child_alias_map)
        if resolved.category == "cross_module":
            final_role = resolved.resolved_role
            final_module = resolved.resolved_module or child_name
        else:
            final_role = resolved.category
            final_module = child_name
        return Signal(name, "cross_module", width=resolved.width, driven_by=resolved.driven_by, instance=head, resolved_module=final_module, resolved_role=final_role)

    return Signal(name, "cross_module")

def classify_condition(fan_in: List[Signal]):
    reg_like = [s for s in fan_in if s.category in ("register", "fsm_state") or (s.category == "combinational_alias" and s.resolved_role in ("register", "fsm_state"))]
    cross = [s for s in fan_in if s.category == "cross_module"]
    if cross: return "Type C", "Contains a cross-module / hierarchical signal reference."
    if len(reg_like) >= 2: return "Type C", f"Depends on {len(reg_like)} registered signals — multi-register / complex temporal path."
    if len(reg_like) == 1:
        r = reg_like[0]
        if r.category == "combinational_alias":
            kind = "FSM register" if r.resolved_role == "fsm_state" else "register"
            return "Type B", (f"Depends on a combinational alias ('{r.name} = {r.alias_expr}') of {kind} — not directly registered, but functionally a single-register update rule.")
        kind = "FSM register" if r.category == "fsm_state" else "register"
        return "Type B", f"Depends on {kind} '{r.name}' — simple single-register update rule."
    return "Type A", "All fan-in signals are primary inputs / unregistered — pure combinational, no register boundary."

# --- Engine Utilities ---
class Counters:
    def __init__(self): self.n = 0
    def next_id(self):
        self.n += 1
        return f"COND_{self.n:03d}"

def _join_path(path_conditions, this_condition):
    terms = path_conditions + ([this_condition] if this_condition else [])
    if len(terms) <= 1: return terms[0] if terms else ""
    return " && ".join(f"({t})" for t in terms)

def _concat_target_names(chunk: str):
    names = set()
    for m in CONCAT_TARGET_RE.finditer(chunk):
        for part in m.group(1).split(","):
            part = re.sub(r"\s*\[[^\]]*\]", "", part).strip()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", part):
                names.add(part)
    return names

# --- Node Walking ---
def walk_statement(node, ctx, path_conditions, condition_nodes, assignments, case_state_ctx=None):
    if node is None: return
    kind = node["kind"]

    if kind == "block":
        for s in node["stmts"]: walk_statement(s, ctx, path_conditions, condition_nodes, assignments, case_state_ctx)
        return

    if kind == "loop":
        walk_statement(node["body"], ctx, path_conditions, condition_nodes, assignments, case_state_ctx)
        return

    if kind == "if":
        cond_text = node["condition"]
        abs_idx_local = node["if_pos"]
        abs_idx_full = ctx["header_end"] + abs_idx_local
        line = line_of(ctx["clean_full"], abs_idx_full)
        col = col_of(ctx["body"], abs_idx_local)

        effective = _join_path(path_conditions, cond_text)
        fan_in_names = extract_fan_in_identifiers(cond_text)
        fan_in_signals = [classify_hierarchical(nm, ctx["module"], ctx["widths"], ctx["driver_map"], ctx["module_registry"], ctx["instance_maps"], ctx["all_widths"], ctx["all_driver_maps"], ctx["all_alias_maps"], ctx["alias_map"]) for nm in fan_in_names]
        cls, reason = classify_condition(fan_in_signals)

        cn = ConditionNode(
            condition_id=ctx["counters"].next_id(), file=ctx["file"], module=ctx["module"].name,
            always_block=ctx["always_block"], line=line, column=col, condition=cond_text,
            effective_condition=effective, normalized_condition=normalize_condition(cond_text),
            path_conditions=list(path_conditions), fan_in=fan_in_signals, classification=cls,
            reason=reason, likely_reset_related=looks_reset_related(cond_text, ctx["reset_name"])
        )
        condition_nodes.append(cn)

        walk_statement(node["true"], ctx, path_conditions + [cond_text], condition_nodes, assignments, case_state_ctx)
        if node["false"] is not None:
            walk_statement(node["false"], ctx, path_conditions + [f"!({cond_text})"], condition_nodes, assignments, case_state_ctx)
        return

    if kind == "case":
        selector = node["selector"].strip()
        fsm_reg = selector if selector in ctx["module"].fsm_states else None
        for item in node["items"]:
            labels = item["labels"]
            label_desc = "default" if labels == ["default"] else " or ".join(labels)
            pseudo_cond = f"{selector} == {label_desc}" if labels != ["default"] else f"{selector} == <default>"
            new_case_ctx = (fsm_reg, labels[0] if (fsm_reg and labels and labels != ["default"]) else None)

            abs_idx_local = item["pos"]
            abs_idx_full = ctx["header_end"] + abs_idx_local
            line = line_of(ctx["clean_full"], abs_idx_full)
            col = col_of(ctx["body"], abs_idx_local)

            effective = _join_path(path_conditions, pseudo_cond)
            fan_in_names = extract_fan_in_identifiers(pseudo_cond)
            fan_in_signals = [classify_hierarchical(nm, ctx["module"], ctx["widths"], ctx["driver_map"], ctx["module_registry"], ctx["instance_maps"], ctx["all_widths"], ctx["all_driver_maps"], ctx["all_alias_maps"], ctx["alias_map"]) for nm in fan_in_names]
            cls, reason = classify_condition(fan_in_signals)

            cn = ConditionNode(
                condition_id=ctx["counters"].next_id(), file=ctx["file"], module=ctx["module"].name,
                always_block=ctx["always_block"], line=line, column=col, condition=pseudo_cond,
                effective_condition=effective, normalized_condition=normalize_condition(pseudo_cond),
                path_conditions=list(path_conditions), fan_in=fan_in_signals, classification=cls,
                reason=reason, likely_reset_related=looks_reset_related(pseudo_cond, ctx["reset_name"])
            )
            condition_nodes.append(cn)
            walk_statement(item["stmt"], ctx, path_conditions + [pseudo_cond], condition_nodes, assignments, new_case_ctx)
        return

    if kind == "other":
        stmt_text = node["text"]
        m = ASSIGN_RE.match(stmt_text)
        if m:
            lhs, _op, rhs = m.group(1), m.group(2), m.group(3).rstrip().rstrip(";").strip()
            abs_idx_local = node["pos"]
            line = line_of(ctx["clean_full"], ctx["header_end"] + abs_idx_local)
            effective = _join_path(path_conditions, "")
            assignments.append(RegisterAssignment(register=lhs, value=rhs, condition=effective or "(unconditional)", always_block=ctx["always_block"], line=line))
            if case_state_ctx and case_state_ctx[0] == lhs:
                ctx["fsm_transitions"].setdefault(lhs, []).append({"from": case_state_ctx[1] or "ANY", "to": rhs, "condition": effective or "(unconditional)", "line": line})
            return

        cm = CONCAT_ASSIGN_RE.match(stmt_text)
        if cm:
            targets = _concat_target_names(stmt_text)
            rhs = cm.group(3).rstrip().rstrip(";").strip()
            abs_idx_local = node["pos"]
            line = line_of(ctx["clean_full"], ctx["header_end"] + abs_idx_local)
            effective = _join_path(path_conditions, "")
            for lhs in sorted(targets):
                assignments.append(RegisterAssignment(register=lhs, value=f"(from concat) {rhs}", condition=effective or "(unconditional)", always_block=ctx["always_block"], line=line))
        return

# --- Extraction Functions ---
def find_modules(clean_text: str):
    modules = []
    for m in MODULE_HEADER_RE.finditer(clean_text):
        name = m.group(1)
        i = m.end()
        depth = 0
        while i < len(clean_text):
            if clean_text[i] == "(": depth += 1
            elif clean_text[i] == ")": depth -= 1
            elif clean_text[i] == ";" and depth == 0: break
            i += 1
        header_end = i + 1
        end_match = re.search(r"\bendmodule\b", clean_text[header_end:])
        body_end = header_end + end_match.start() if end_match else len(clean_text)
        modules.append((name, m.start(), header_end, body_end))
    return modules

def extract_ports(full_source: str, header_start: int, header_end: int, body: str):
    ports, widths = {}, {}
    header_region = full_source[header_start:header_end]
    for m in ANSI_PORT_RE.finditer(header_region):
        direction, _qual, bus, name = m.groups()
        ports[name] = direction
        widths[name] = bus.strip() if bus else None
    for m in PORT_DECL_RE.finditer(header_region + "\n" + body):
        direction, _qual, bus, names_blob = m.groups()
        for nm in names_blob.split(","):
            nm = nm.strip()
            if nm and re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", nm):
                ports[nm] = direction
                widths.setdefault(nm, bus.strip() if bus else None)
    return ports, widths

def find_declared_reg_like(body: str, ports: Dict[str, str]):
    declared = set()
    widths = {}
    for m in REG_DECL_RE.finditer(body):
        bus, names_blob = m.groups()
        for nm in names_blob.split(","):
            nm = nm.strip()
            if nm and re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", nm) and nm not in ports:
                declared.add(nm)
                widths[nm] = bus.strip() if bus else None
    return sorted(declared), widths

def find_parameters(body: str) -> List[str]:
    params = set()
    for m in PARAM_DECL_RE.finditer(body): params.add(m.group(1))
    for line in body.split(";"):
        if re.search(r"\b(parameter|localparam)\b", line):
            for nm in re.findall(r"([A-Za-z_][A-Za-z0-9_$]*)\s*=", line): params.add(nm)
    return sorted(params)

def find_continuous_aliases(body: str) -> Dict[str, str]:
    aliases = {}
    for m in CONTINUOUS_ASSIGN_RE.finditer(body): aliases[m.group(1)] = m.group(2).strip()
    return aliases

def parse_port_connections(port_text: str):
    entries = []
    depth = 0
    cur = []
    saw_comma = False
    for ch in port_text:
        if ch in "([{":
            depth += 1
            cur.append(ch)
        elif ch in ")]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            entries.append("".join(cur).strip())
            cur = []
            saw_comma = True
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail or saw_comma: entries.append(tail)

    port_map = []
    for e in entries:
        if e.startswith("."):
            mm = re.match(r"\.([A-Za-z_][A-Za-z0-9_$]*)\s*\(\s*(.*?)\s*\)\s*$", e, re.DOTALL)
            if mm: port_map.append({"port": mm.group(1), "signal": mm.group(2)})
            else: port_map.append({"port": None, "signal": e})
        else:
            port_map.append({"port": None, "signal": e})
    return port_map

def find_instantiations(body: str, known_modules: List[str], self_name: str):
    results = []
    for mod_type in known_modules:
        if mod_type == self_name: continue
        for m in re.finditer(rf"\b{re.escape(mod_type)}\b", body):
            i = m.end()
            j = _skip_ws(body, i)
            if j < len(body) and body[j] == "#":
                paren_start = body.find("(", j)
                if paren_start == -1: continue
                close = find_matching(body, "(", ")", paren_start + 1)
                if close == -1: continue
                j = _skip_ws(body, close + 1)
            name_m = re.match(r"[A-Za-z_][A-Za-z0-9_$]*", body[j:])
            if not name_m or name_m.group(0) in SV_KEYWORDS_STRICT: continue
            instance_name = name_m.group(0)
            k = _skip_ws(body, j + len(instance_name))
            if k >= len(body) or body[k] != "(": continue
            close_port = find_matching(body, "(", ")", k + 1)
            if close_port == -1: continue
            semi_check = _skip_ws(body, close_port + 1)
            if semi_check >= len(body) or body[semi_check] != ";": continue
            port_map = parse_port_connections(body[k + 1:close_port])
            results.append({"instance_name": instance_name, "child_module": mod_type, "port_map": port_map, "pos": m.start()})
    results.sort(key=lambda x: x["pos"])
    return results

def extract_sequential_blocks(body: str, module_name: str):
    blocks = []
    counter = 0
    for m in ALWAYS_RE.finditer(body):
        sens_list = m.group(2)
        if "posedge" not in sens_list and "negedge" not in sens_list: continue
        after = _skip_ws(body, m.end())
        stmt, next_idx = parse_statement(body, after)
        span = (after, next_idx)

        clk = None
        rst = None
        edge = "posedge"
        for tok in re.split(r"\bor\b|,", sens_list):
            tok = tok.strip()
            mm = re.match(r"(posedge|negedge)\s+([A-Za-z_][A-Za-z0-9_$]*)", tok)
            if mm:
                if clk is None: clk, edge = mm.group(2), mm.group(1)
                else: rst = mm.group(2)

        always_id = f"always_{counter}"
        meta = SequentialBlockMeta(always_block=always_id, clock=clk, reset=rst, edge=edge, start_line=line_of(body, m.start()), end_line=line_of(body, next_idx), raw_sensitivity=sens_list.strip())
        blocks.append({"meta": meta, "span": span, "stmt": stmt, "_raw_start": m.start(), "_raw_end": next_idx})
        counter += 1
    return blocks

def find_registers(body: str, seq_spans) -> List[str]:
    regs = set()
    for (start, end) in seq_spans:
        chunk = body[start:end]
        for m in NONBLOCKING_ASSIGN_RE.finditer(chunk):
            name = re.sub(r"\s*\[[^\]]*\]", "", m.group(1)).strip()
            regs.add(name)
        regs.update(_concat_target_names(chunk))
    return sorted(regs)

def find_fsm_states_and_labels(body: str, registers: List[str]):
    fsm = set()
    labels_map = {}
    for m in CASE_SELECTOR_RE.finditer(body):
        name = m.group(1)
        if name in registers:
            fsm.add(name)
            endcase_m = re.search(r"\bendcase\b", body[m.end():])
            case_body = body[m.end():m.end() + endcase_m.start()] if endcase_m else body[m.end():m.end() + 400]
            for lm in CASE_LABEL_LINE_RE.finditer(case_body):
                for lbl in lm.group(1).split(","):
                    lbl = lbl.strip()
                    if lbl and lbl != "default":
                        labels_map.setdefault(name, set()).add(lbl)

    compare_targets = {}
    for m in EQ_COMPARE_RE.finditer(body):
        lhs, rhs = m.group(1), m.group(2)
        for reg_name, other in ((lhs, rhs), (rhs, lhs)):
            if reg_name in registers and re.match(r"^[A-Z][A-Z0-9_]*$", other):
                compare_targets.setdefault(reg_name, set()).add(other)
    for reg_name, targets in compare_targets.items():
        if len(targets) >= 2 or reg_name in fsm:
            fsm.add(reg_name)
            labels_map.setdefault(reg_name, set()).update(targets)

    return sorted(fsm), {k: sorted(v) for k, v in labels_map.items()}

# --- Core Analyzer Driver ---
def analyze_rtl(source_text: str, file_name: str = "pasted_input.sv") -> Dict:
    clean = strip_comments_and_strings(source_text)
    modules_meta = find_modules(clean)
    known_module_names = [name for (name, _, _, _) in modules_meta]
    counters = Counters()

    pass1 = {}
    module_registry = {}
    all_widths = {}
    all_driver_maps = {}
    all_alias_maps = {}
    raw_instances_by_module = {}
    header_end_by_module = {}

    for (name, header_start, header_end, body_end) in modules_meta:
        body = clean[header_end:body_end]
        ports, port_widths = extract_ports(clean, header_start, header_end, body)
        seq_blocks = extract_sequential_blocks(body, name)
        seq_spans = [b["span"] for b in seq_blocks]

        registers = set(find_registers(body, seq_spans))
        declared_regs, declared_widths = find_declared_reg_like(body, ports)
        registers.update(declared_regs)
        registers = sorted(registers)

        fsm_states, fsm_labels = find_fsm_states_and_labels(body, registers)
        parameters = find_parameters(body)
        alias_map = find_continuous_aliases(body)

        widths = dict(port_widths)
        widths.update(declared_widths)

        module = ModuleInfo(name=name, ports=ports, registers=registers, fsm_states=fsm_states, parameters=parameters)
        driver_map = {}
        for b in seq_blocks:
            chunk = body[b["span"][0]:b["span"][1]]
            for m in NONBLOCKING_ASSIGN_RE.finditer(chunk):
                reg_name = re.sub(r"\s*\[[^\]]*\]", "", m.group(1)).strip()
                driver_map.setdefault(reg_name, b["meta"].always_block)
            for reg_name in _concat_target_names(chunk):
                driver_map.setdefault(reg_name, b["meta"].always_block)

        raw_instances = find_instantiations(body, known_module_names, name)

        pass1[name] = {
            "body": body, "header_end": header_end, "seq_blocks": seq_blocks,
            "fsm_labels": fsm_labels, "module": module, "widths": widths,
            "driver_map": driver_map, "alias_map": alias_map,
        }
        module_registry[name] = module
        all_widths[name] = widths
        all_driver_maps[name] = driver_map
        all_alias_maps[name] = alias_map
        raw_instances_by_module[name] = raw_instances
        header_end_by_module[name] = header_end

    instance_maps = {}
    for name, raw_instances in raw_instances_by_module.items():
        module = module_registry[name]
        header_end = header_end_by_module[name]
        inst_map = {}
        for raw in raw_instances:
            child_name = raw["child_module"]
            child_module = module_registry.get(child_name)
            port_map = raw["port_map"]
            if child_module and port_map and all(p["port"] is None for p in port_map):
                child_port_names = list(child_module.ports.keys())
                resolved = []
                for idx, p in enumerate(port_map):
                    pname = child_port_names[idx] if idx < len(child_port_names) else None
                    resolved.append({"port": pname, "signal": p["signal"]})
                port_map = resolved

            line = line_of(clean, header_end + raw["pos"])
            module.instances.append(Instance(instance_name=raw["instance_name"], child_module=child_name, parent_module=name, port_map=port_map, line=line))
            inst_map[raw["instance_name"]] = child_name
        instance_maps[name] = inst_map

    all_condition_nodes = []
    all_assignments = []
    all_fsm_info = []

    for name, p1 in pass1.items():
        module = p1["module"]
        fsm_transitions_acc = {}

        for b in p1["seq_blocks"]:
            meta = b["meta"]
            meta.start_line = line_of(clean, p1["header_end"] + b["_raw_start"])
            meta.end_line = line_of(clean, p1["header_end"] + b["_raw_end"])
            ctx = {
                "body": p1["body"], "clean_full": clean, "header_end": p1["header_end"],
                "file": file_name, "module": module, "widths": p1["widths"],
                "driver_map": p1["driver_map"], "always_block": meta.always_block,
                "reset_name": meta.reset, "counters": counters,
                "fsm_transitions": fsm_transitions_acc, "module_registry": module_registry,
                "instance_maps": instance_maps, "all_widths": all_widths,
                "all_driver_maps": all_driver_maps, "all_alias_maps": all_alias_maps,
                "alias_map": p1["alias_map"],
            }
            walk_statement(b["stmt"], ctx, [], all_condition_nodes, all_assignments)
            module.sequential_blocks.append(meta)

        for reg in module.fsm_states:
            all_fsm_info.append(FSMInfo(register=reg, states=p1["fsm_labels"].get(reg, []), transitions=fsm_transitions_acc.get(reg, [])))

    all_modules_out = [pass1[name]["module"] for (name, _, _, _) in modules_meta]

    type_a = [asdict(c) for c in all_condition_nodes if c.classification == "Type A"]
    type_b = [asdict(c) for c in all_condition_nodes if c.classification == "Type B"]
    type_c = [asdict(c) for c in all_condition_nodes if c.classification == "Type C"]

    hierarchy_edges = [{
        "parent_module": inst.parent_module, "instance_name": inst.instance_name,
        "child_module": inst.child_module, "child_resolved": inst.child_module in module_registry,
        "line": inst.line,
    } for m in all_modules_out for inst in m.instances]

    return {
        "file": file_name,
        "modules": [asdict(m) for m in all_modules_out],
        "hierarchy_edges": hierarchy_edges,
        "conditions": [asdict(c) for c in all_condition_nodes],
        "type_a": type_a, "type_b": type_b, "type_c": type_c,
        "register_dependency_graph": [asdict(a) for a in all_assignments],
        "fsm_info": [asdict(f) for f in all_fsm_info],
        "summary": {
            "num_modules": len(all_modules_out),
            "num_sequential_blocks": sum(len(m.sequential_blocks) for m in all_modules_out),
            "num_conditions": len(all_condition_nodes),
            "type_a_count": len(type_a), "type_b_count": len(type_b), "type_c_count": len(type_c),
            "num_register_assignments": len(all_assignments),
            "num_fsm_registers": len(all_fsm_info),
            "num_instances": len(hierarchy_edges),
        }
    }