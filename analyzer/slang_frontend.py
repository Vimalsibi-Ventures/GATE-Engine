import pyslang
import pyslang.syntax

_MODULE_LIKE_KINDS = {"ModuleDeclaration", "InterfaceDeclaration", "ProgramDeclaration"}

def _kind_name(node) -> str:
    try:
        return str(node.kind).split(".")[-1]
    except Exception:
        return "?"

def _collect_module_like(root_node, out: list):
    """Handles single module declarations and compilation unit containers."""
    if _kind_name(root_node) in _MODULE_LIKE_KINDS:
        out.append(root_node)
        return
    for member in getattr(root_node, "members", []):
        if _kind_name(member) in _MODULE_LIKE_KINDS:
            out.append(member)

def run_slang_frontend(source_text: str) -> dict:
    result = {"success": False, "diagnostics": [], "modules_seen": [], "notes": []}
    try:
        tree = pyslang.syntax.SyntaxTree.fromText(source_text)
    except Exception as e:
        result["notes"].append(f"pyslang could not parse this source: {e}")
        return result

    result["success"] = True
    try:
        for d in tree.diagnostics:
            result["diagnostics"].append(str(d))
    except Exception as e:
        result["notes"].append(f"Could not read tree.diagnostics: {e}")

    try:
        found = []
        _collect_module_like(tree.root, found)
        for node in found:
            info = {"kind": _kind_name(node)}
            try:
                info["name"] = node.header.name.value
            except Exception:
                pass
            result["modules_seen"].append(info)
        if not found:
            result["notes"].append(
                "No module/interface/program declarations found at the top level of tree.root."
            )
    except Exception as e:
        result["notes"].append(f"Could not walk tree.root for declarations: {e}")

    return result

def print_slang_report(slang_result: dict):
    print(f"Parse successful : {slang_result['success']}")
    diags = slang_result["diagnostics"]
    print(f"Diagnostics      : {len(diags)}")
    for d in diags[:25]:
        print("   ", d)
    if slang_result["modules_seen"]:
        print("Modules seen by slang:")
        for m in slang_result["modules_seen"]:
            print("   ", m)
    for note in slang_result["notes"]:
        print("note:", note)