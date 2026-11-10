"""OPC UA server for the heating-plant twin.

Address-space rules (sources in SOURCES.md):
- Measured values are AnalogItemType variables with EURange and EngineeringUnits
  (OPC 10000-8 Data Access, 5.3.2). Units use UNECE Rec. 20 common codes
  (CEL = degree Celsius, P1 = percent), encoded as the OPC UA unitId
  (OPC 10000-8, 5.6.3: unitId = (c1 << 16) | (c2 << 8) | c3).
- BrowseNames are the EMSR loop tags from heizzentrale-tia/docs/EMSR_Stellenliste.csv,
  so PLC, EPLAN and OPC UA share one name per signal.
- String NodeIds ("TIC101.PV") are stable, so the AAS Asset Interfaces Description
  can point at them with href = "nsu=<namespace>;s=<id>" (OPC 10101 WoT binding).
"""
import asyncio
import logging
import csv
import pathlib
from dataclasses import dataclass

from asyncua import Server, ua
from asyncua.common.instantiate_util import instantiate

from heating_plant.model import HeatingPlant

logging.getLogger("asyncua.common.instantiate_util").setLevel(logging.ERROR)  # type-attribute copy notes

NAMESPACE = "https://github.com/sulemanali5760/heating-plant-aas/UA/"
ENDPOINT = "opc.tcp://127.0.0.1:4840/heating-plant/"
UNECE_NS = "http://www.opcfoundation.org/UA/units/un/cefact"
ROOT = pathlib.Path(__file__).resolve().parents[1]
DWD_FILE = ROOT / "data/dwd_03126_magdeburg_2022-12-12_to_18.csv"


def unece_unit_id(code: str) -> int:
    v = 0
    for ch in code:
        v = (v << 8) | ord(ch)
    return v


UNITS = {
    "CEL": ua.EUInformation(NamespaceUri=UNECE_NS, UnitId=unece_unit_id("CEL"),
                            DisplayName=ua.LocalizedText("°C"),
                            Description=ua.LocalizedText("degree Celsius")),
    "P1": ua.EUInformation(NamespaceUri=UNECE_NS, UnitId=unece_unit_id("P1"),
                           DisplayName=ua.LocalizedText("%"),
                           Description=ua.LocalizedText("percent")),
}


@dataclass(frozen=True)
class Point:
    """One data point. node_id is also the AAS AID property key."""
    node_id: str
    parent: str            # EMSR tag of the parent object ("" = plant level)
    browse_name: str
    description: str
    unit: str
    low: float
    high: float
    writable: bool = False


# Ranges: EMSR list (TIC101/TI102 0..120 °C, TI103 -30..+50 °C, TV101 0..100 %).
POINTS = [
    Point("TIC101.SP", "TIC101", "SP", "Flow temperature setpoint from the heating curve", "CEL", 0, 120),
    Point("TIC101.PV", "TIC101", "PV", "Flow temperature after mixing valve (TI101)", "CEL", 0, 120),
    Point("TIC101.OP", "TIC101", "OP", "Mixing valve TV101 position (controller output)", "P1", 0, 100),
    Point("TI102.PV", "TI102", "PV", "Return temperature", "CEL", 0, 120),
    Point("TI103.PV", "TI103", "PV", "Outdoor temperature (heating-curve input)", "CEL", -30, 50, writable=True),
]
PARAMETERS = [
    # node_id, browse name, description, default
    ("HeatingCurve.Slope", "Slope", "Heating curve slope (-)", 1.2),
    ("HeatingCurve.Level", "Level", "Heating curve parallel shift (K)", 0.0),
    ("HeatingCurve.FlowMax", "FlowMax", "Flow temperature upper limit (°C)", 75.0),
]


async def _analog_item(server: Server, parent, idx: int, p: Point):
    # Instantiate the standard AnalogItemType (i=2368) so the type definition, the mandatory
    # EURange and the optional EngineeringUnits are the ones defined by OPC 10000-8.
    analog_type = server.get_node(ua.NodeId(ua.ObjectIds.AnalogItemType))
    var = (await instantiate(parent, analog_type, ua.NodeId(p.node_id, idx),
                             ua.QualifiedName(p.browse_name, idx), ua.LocalizedText(p.browse_name),
                             idx=idx, instantiate_optional=False))[0]
    # The type is abstract "Number" with any rank; an instance must be concrete: scalar Double.
    for attr, val in [(ua.AttributeIds.DataType, ua.Variant(ua.NodeId(ua.ObjectIds.Double))),
                      (ua.AttributeIds.ValueRank, ua.Variant(-1, ua.VariantType.Int32)),
                      (ua.AttributeIds.AccessLevel, ua.Variant(ua.AccessLevel.CurrentRead.mask, ua.VariantType.Byte)),
                      (ua.AttributeIds.UserAccessLevel, ua.Variant(ua.AccessLevel.CurrentRead.mask, ua.VariantType.Byte))]:
        await var.write_attribute(attr, ua.DataValue(val))
    await var.write_value(ua.Variant(0.0, ua.VariantType.Double))
    await var.write_attribute(ua.AttributeIds.Description,
                              ua.DataValue(ua.Variant(ua.LocalizedText(p.description))))
    eu_range = await var.get_child(["0:EURange"])
    await eu_range.write_value(ua.Range(Low=p.low, High=p.high))
    await var.add_property(ua.NodeId(p.node_id + ".EngineeringUnits", idx),
                           ua.QualifiedName("EngineeringUnits", 0), UNITS[p.unit])
    if p.writable:
        await var.set_writable()
    return var


async def build(server: Server) -> tuple[int, dict]:
    idx = await server.register_namespace(NAMESPACE)
    objects = server.nodes.objects
    plant = await objects.add_object(ua.NodeId("HeatingPlant", idx), ua.QualifiedName("HeatingPlant", idx))
    parents = {"": plant}
    for tag, text in [("TIC101", "Flow temperature control loop (heating curve -> PID -> mixing valve)"),
                      ("TI102", "Return temperature indication"),
                      ("TI103", "Outdoor temperature indication")]:
        o = await plant.add_object(ua.NodeId(tag, idx), ua.QualifiedName(tag, idx))
        await o.write_attribute(ua.AttributeIds.Description, ua.DataValue(ua.Variant(ua.LocalizedText(text))))
        parents[tag] = o
    nodes = {p.node_id: await _analog_item(server, parents[p.parent], idx, p) for p in POINTS}
    hc = await plant.add_object(ua.NodeId("HeatingCurve", idx), ua.QualifiedName("HeatingCurve", idx))
    for nid, bn, desc, default in PARAMETERS:
        v = await hc.add_variable(ua.NodeId(nid, idx), ua.QualifiedName(bn, idx),
                                  ua.Variant(default, ua.VariantType.Double))
        await v.write_attribute(ua.AttributeIds.Description, ua.DataValue(ua.Variant(ua.LocalizedText(desc))))
        await v.set_writable()
        nodes[nid] = v
    nodes["_plant"] = plant
    return idx, nodes


def load_dwd() -> list[float]:
    with DWD_FILE.open() as f:
        return [float(r["t_outdoor_degC"]) for r in csv.DictReader(f)]


async def run(seconds: float | None = None, time_lapse: float = 60.0, outdoor: str = "dwd",
              endpoint: str = ENDPOINT, ready: asyncio.Event | None = None) -> None:
    """Run the twin and serve it.
    outdoor="dwd": replay the real Magdeburg week, one DWD hour every 3600/time_lapse s.
    outdoor="manual": TI103.PV is only changed by OPC UA writes."""
    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    server.set_server_name("Heating plant twin (heating-plant-aas)")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])  # demo on localhost; see README
    idx, n = await build(server)
    hours = load_dwd() if outdoor == "dwd" else None
    plant = HeatingPlant(t_outdoor=hours[0] if hours else 0.0)
    plant.settle()
    await n["TI103.PV"].write_value(plant.t_outdoor)
    async with server:
        if ready:
            ready.set()
        elapsed = 0.0
        while seconds is None or elapsed < seconds:
            if hours:
                plant.t_outdoor = hours[int(elapsed * time_lapse / 3600) % len(hours)]
                await n["TI103.PV"].write_value(plant.t_outdoor)
            else:
                plant.t_outdoor = await n["TI103.PV"].read_value()
            plant.slope = await n["HeatingCurve.Slope"].read_value()
            plant.level = await n["HeatingCurve.Level"].read_value()
            plant.t_flow_max = await n["HeatingCurve.FlowMax"].read_value()
            # One 100 ms OB30 cycle of plant time per 100 ms of wall time, times the time-lapse factor
            for _ in range(max(1, round(time_lapse))):
                plant.step()
            for nid, val in [("TIC101.SP", plant.t_flow_set), ("TIC101.PV", plant.circuit.t_flow),
                             ("TIC101.OP", plant.valve), ("TI102.PV", plant.circuit.t_return)]:
                await n[nid].write_value(float(val))
            await asyncio.sleep(0.1)
            elapsed += 0.1


async def export_nodeset(path: pathlib.Path) -> None:
    server = Server()
    await server.init()
    _, n = await build(server)
    plant = n["_plant"]
    nodes = [plant] + await plant.get_children()
    queue = list(nodes)
    while queue:
        for c in await queue.pop().get_children():
            if c.nodeid.NamespaceIndex != 0 and c not in nodes:
                nodes.append(c)
                queue.append(c)
    await server.export_xml(nodes, str(path))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Heating-plant twin as OPC UA server")
    ap.add_argument("--outdoor", choices=["dwd", "manual"], default="dwd")
    ap.add_argument("--time-lapse", type=float, default=60.0, help="plant seconds per wall second")
    ap.add_argument("--export-nodeset", type=pathlib.Path)
    a = ap.parse_args()
    if a.export_nodeset:
        asyncio.run(export_nodeset(a.export_nodeset))
    else:
        print(f"Serving {ENDPOINT}  (Ctrl+C to stop)")
        asyncio.run(run(time_lapse=a.time_lapse, outdoor=a.outdoor))
