import re
from typing import Optional

SV_KEYWORDS = {
    "if", "else", "begin", "end", "always", "always_ff", "always_comb",
    "always_latch", "posedge", "negedge", "or", "and", "not", "module",
    "endmodule", "input", "output", "inout", "wire", "reg", "logic",
    "parameter", "localparam", "case", "casex", "casez", "endcase",
    "default", "assign", "function", "endfunction", "task", "endtask",
    "for", "while", "repeat", "generate", "endgenerate", "genvar",
    "integer", "real", "signed", "unsigned", "typedef", "enum", "struct",
    "packed", "unpacked", "initial", "final", "return", "break",
    "continue", "clk", "clock",
}
SV_KEYWORDS_STRICT = SV_KEYWORDS - {"clk", "clock"}

IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_$]*\b")
HIER_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)+\b")
NUMBER_RE = re.compile(r"^\d+$|^\d*'[sSbBoOdDhH][0-9a-fA-Fxz_XZ]+$")
RESET_NAME_HINTS = re.compile(r"rst|reset|clr|clear", re.IGNORECASE)

def strip_comments_and_strings(text: str) -> str:
    out = []
    i = 0
    n = len(text)
    in_line_comment = in_block_comment = in_string = False
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_line_comment:
            if c == "\n":
                in_line_comment = False
                out.append(c)
            else:
                out.append(" ")
            i += 1
            continue
        if in_block_comment:
            if c == "*" and nxt == "/":
                in_block_comment = False
                out.append("  ")
                i += 2
                continue
            out.append("\n" if c == "\n" else " ")
            i += 1
            continue
        if in_string:
            if c == "\\" and i + 1 < n:
                out.append("  ")
                i += 2
                continue
            if c == '"':
                in_string = False
                out.append(" ")
                i += 1
                continue
            out.append(" ")
            i += 1
            continue
        if c == "/" and nxt == "/":
            in_line_comment = True
            out.append("  ")
            i += 2
            continue
        if c == "/" and nxt == "*":
            in_block_comment = True
            out.append("  ")
            i += 2
            continue
        if c == '"':
            in_string = True
            out.append(" ")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)

def _is_word_boundary(text: str, i: int, tok_len: int) -> bool:
    before_ok = (i == 0) or (not (text[i - 1].isalnum() or text[i - 1] == "_"))
    j = i + tok_len
    after_ok = (j >= len(text)) or (not (text[j].isalnum() or text[j] == "_"))
    return before_ok and after_ok

def find_matching(text: str, open_tok: str, close_tok: str, start_idx: int) -> int:
    depth = 1
    i = start_idx
    word_mode = len(open_tok) > 1
    n = len(text)
    while i < n:
        if word_mode:
            if text[i:i + len(open_tok)] == open_tok and _is_word_boundary(text, i, len(open_tok)):
                depth += 1
                i += len(open_tok)
                continue
            if text[i:i + len(close_tok)] == close_tok and _is_word_boundary(text, i, len(close_tok)):
                depth -= 1
                if depth == 0:
                    return i
                i += len(close_tok)
                continue
            i += 1
        else:
            if text[i] == open_tok:
                depth += 1
            elif text[i] == close_tok:
                depth -= 1
                if depth == 0:
                    return i
            i += 1
    return -1

def line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1

def col_of(text: str, idx: int) -> int:
    nl = text.rfind("\n", 0, idx)
    return idx - nl

def _match_kw(text: str, i: int, kw: str) -> bool:
    return text[i:i + len(kw)] == kw and _is_word_boundary(text, i, len(kw))

def _skip_ws(text: str, i: int) -> int:
    n = len(text)
    while i < n and text[i] in " \t\r\n":
        i += 1
    return i

def looks_reset_related(cond: str, reset_name: Optional[str]) -> bool:
    if reset_name and re.search(rf"\b{re.escape(reset_name)}\b", cond):
        return True
    return bool(RESET_NAME_HINTS.search(cond))

def normalize_condition(cond: str) -> str:
    terms = []
    depth = 0
    cur = []
    i = 0
    n = len(cond)
    while i < n:
        c = cond[i]
        if c in "([{":
            depth += 1
            cur.append(c)
        elif c in ")]}":
            depth -= 1
            cur.append(c)
        elif depth == 0 and cond[i:i + 2] == "&&":
            terms.append("".join(cur).strip())
            cur = []
            i += 2
            continue
        else:
            cur.append(c)
        i += 1
    terms.append("".join(cur).strip())
    terms = [t for t in terms if t]
    if len(terms) <= 1:
        return cond.strip()
    return " && ".join(sorted(terms))