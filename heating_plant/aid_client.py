"""Connect to the plant using ONLY its Asset Administration Shell.

The client knows nothing about the plant: it opens the AASX, finds the Asset Interfaces
Description submodel (IDTA 02017) by its semantic ID, takes the OPC UA endpoint and the
datapoints (href = nsu=...;s=..., unit, title) from it, then subscribes to every datapoint.
This is the "plug and produce" use of the AAS: no hand-written tag list.
"""
import asyncio
import csv
import pathlib
import sys
from dataclasses import dataclass

from asyncua import Client, ua
from basyx.aas import model
from basyx.aas.adapter import aasx

AID_SEMANTIC_ID = "https://admin-shell.io/idta/AssetInterfacesDescription/1/2/Submodel"
OPCUA_BINDING = "http://opcfoundation.org/UA/WoT-Binding/"


@dataclass
class Datapoint:
    name: str
    title: str
    unit: str
    namespace: str
    identifier: str


def _sem(e) -> str | None:
    return e.semantic_id.key[0].value if e.semantic_id else None


def _val(coll, id_short):
    return coll.get_referable(id_short).value


def read_interface(aasx_path: pathlib.Path) -> tuple[str, list[Datapoint]]:
    store, files = model.DictIdentifiableStore(), aasx.DictSupplementaryFileContainer()
    with aasx.AASXReader(str(aasx_path)) as r:
        r.read_into(store, files)
    sm = next(o for o in store if isinstance(o, model.Submodel) and _sem(o) == AID_SEMANTIC_ID)
    itf = next(e for e in sm.submodel_element
               if any(s.key[0].value == OPCUA_BINDING for s in e.supplemental_semantic_id))
    base = _val(itf.get_referable("EndpointMetadata"), "base")
    points = []
    for p in itf.get_referable("InteractionMetadata").get_referable("properties").value:
        href = _val(p.get_referable("forms"), "href")
        nsu, sid = href.split(";s=", 1)
        points.append(Datapoint(_val(p, "key"), _val(p, "title"), _val(p, "unit"), nsu.removeprefix("nsu="), sid))
    return base, points


class _Recorder:
    def __init__(self, names):
        self.latest = dict.fromkeys(names)

    def datachange_notification(self, node, val, data):
        self.latest[self.by_node[node.nodeid]] = val


async def watch(aasx_path: pathlib.Path, seconds: float, out_csv: pathlib.Path | None = None,
                endpoint_override: str | None = None, period: float = 0.5) -> list[dict]:
    base, points = read_interface(aasx_path)
    endpoint = endpoint_override or base
    print(f"AAS says: OPC UA endpoint {base}, {len(points)} datapoints")
    rows = []
    async with Client(endpoint) as c:
        rec = _Recorder([p.name for p in points])
        rec.by_node = {}
        nodes = []
        for p in points:
            idx = await c.get_namespace_index(p.namespace)
            n = c.get_node(ua.NodeId(p.identifier, idx))
            rec.by_node[n.nodeid] = p.name
            nodes.append(n)
        sub = await c.create_subscription(100, rec)
        await sub.subscribe_data_change(nodes)
        header = "  ".join(f"{p.name} [{p.unit}]".rjust(16) for p in points)
        print(header)
        t = 0.0
        while t < seconds:
            await asyncio.sleep(period)
            t += period
            if None in rec.latest.values():
                continue
            rows.append({"t_s": round(t, 2), **rec.latest})
            print("  ".join(f"{rec.latest[p.name]:16.2f}" for p in points))
        await sub.delete()
    if out_csv:
        with out_csv.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return rows


if __name__ == "__main__":
    path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "model/HeatingPlant.aasx")
    asyncio.run(watch(path, seconds=float(sys.argv[2]) if len(sys.argv) > 2 else 10))
