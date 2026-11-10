"""Print an IDTA template as a tree: idShort [type] cardinality semanticId valueType."""
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8-sig"))
maxdepth = int(sys.argv[2]) if len(sys.argv) > 2 else 9
def walk(e, depth=0):
    if depth > maxdepth: return
    card = next((q.get("value") for q in e.get("qualifiers", []) if q.get("type") in ("SMT/Cardinality", "Cardinality", "Multiplicity")), "")
    sem = (e.get("semanticId") or {}).get("keys", [{}])[0].get("value", "")
    v = e.get("value")
    print("  " * depth + f"{e.get('idShort')} [{e.get('modelType')}] {card} {sem} {e.get('valueType','')}")
    for c in (v if isinstance(v, list) else []):
        if isinstance(c, dict) and "modelType" in c: walk(c, depth + 1)
for sm in d["submodels"]:
    for e in sm["submodelElements"]: walk(e)
