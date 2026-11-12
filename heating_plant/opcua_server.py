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
from asyncua.crypto.truststore import TrustStore
from asyncua.crypto.validator import CertificateValidator, CertificateValidatorOptions
from asyncua.common.utils import ServiceError

from heating_plant import security
from heating_plant.model import HeatingPlant, Pid2Dof

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
    "BAR": ua.EUInformation(NamespaceUri=UNECE_NS, UnitId=unece_unit_id("BAR"),
                            DisplayName=ua.LocalizedText("bar"), Description=ua.LocalizedText("bar")),
    "MQH": ua.EUInformation(NamespaceUri=UNECE_NS, UnitId=unece_unit_id("MQH"),
                            DisplayName=ua.LocalizedText("m³/h"), Description=ua.LocalizedText("cubic metre per hour")),
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
    kind: str = "number"   # AID/JSON type: number, integer, boolean, string


# Ranges: EMSR list (TIC101/TI102 0..120 °C, TI103 -30..+50 °C, TV101 0..100 %, PDIC301 0..2.5 bar,
# SC301 0..100 %). All measured values are read-only; simulation inputs live in the Simulation object.
POINTS = [
    Point("TIC101.SP", "TIC101", "SP", "Flow temperature setpoint from the heating curve", "CEL", 0, 120),
    Point("TIC101.PV", "TIC101", "PV", "Flow temperature after mixing valve (TI101)", "CEL", 0, 120),
    Point("TIC101.OP", "TIC101", "OP", "Mixing valve TV101 position (controller output)", "P1", 0, 100),
    Point("TI102.PV", "TI102", "PV", "Return temperature", "CEL", 0, 120),
    Point("TI103.PV", "TI103", "PV", "Outdoor temperature (heating-curve input)", "CEL", -30, 50),
    Point("PDIC301.SP", "PDIC301", "SP", "Network differential pressure setpoint", "BAR", 0, 2.5),
    Point("PDIC301.PV", "PDIC301", "PV", "Network differential pressure at the consumers (PDT301)", "BAR", 0, 2.5),
    Point("PDIC301.OP", "PDIC301", "OP", "Speed setpoint of network pump P301 (SC301)", "P1", 0, 100),
    Point("P301.Speed", "P301", "Speed", "Actual speed of network pump P301", "P1", 0, 100),
    Point("P301.Flow", "P301", "Flow", "Network flow (value of the plant model; the plant has no flow meter)", "MQH", 0, 20),
]
# Binary status of the pump: without it a client sees speed 0 and cannot tell why.
STATUS = [
    Point("P301.Running", "P301", "Running", "Network pump P301 released and running", "", 0, 1, kind="boolean"),
    Point("P301.DriveFault", "P301", "DriveFault", "Drive fault of P301 (frequency converter)", "", 0, 1, kind="boolean"),
]
ALL_POINTS = POINTS + STATUS     # everything the AAS describes
SIMULATION = [
    # node_id, browse name, description, default, low, high: inputs that replace the real world in the twin
    ("Simulation.OutdoorTemperature", "OutdoorTemperature", "Outdoor temperature used in manual mode (°C)", 0.0, -30.0, 50.0),
    ("Simulation.ConsumerLoad", "ConsumerLoad", "Opening of all consumer valves (%), drives the pump load", 50.0, 0.0, 100.0),
]
PARAMETERS = [
    # node_id, browse name, description, default, low, high
    ("HeatingCurve.Slope", "Slope", "Heating curve slope (-)", 1.2, 0.0, 3.0),
    ("HeatingCurve.Level", "Level", "Heating curve parallel shift (K)", 0.0, -10.0, 10.0),
    ("HeatingCurve.FlowMax", "FlowMax", "Flow temperature upper limit (°C)", 75.0, 25.0, 75.0),
]
# Accepted range of every writable input (see SOURCES.md). A write outside it, or NaN, is undone.
LIMITS = {nid: (low, high) for nid, _, _, _, low, high in SIMULATION + PARAMETERS}


async def _checked(n: dict, last: dict, nid: str) -> float:
    """Value of a client-writable node, if it is a number inside LIMITS; otherwise the node is set back
    to its last accepted value. NaN fails both comparisons, so it never reaches the controller."""
    v = await n[nid].read_value()
    low, high = LIMITS[nid]
    if isinstance(v, (int, float)) and low <= v <= high:
        last[nid] = float(v)
    else:
        await n[nid].write_value(last[nid])
    return last[nid]


async def _type_member(server: Server, owner, idx: int, type_id: str, name: str, analog: bool, boolean: bool = False):
    """Mandatory child of an ObjectType (ModellingRule Mandatory, OPC 10000-3, 6.4.4)."""
    if analog:
        analog_type = server.get_node(ua.NodeId(ua.ObjectIds.AnalogItemType))
        v = (await instantiate(owner, analog_type, ua.NodeId(f"{type_id}.{name}", idx),
                               ua.QualifiedName(name, idx), ua.LocalizedText(name), idx=idx,
                               instantiate_optional=False))[0]
        await v.write_attribute(ua.AttributeIds.DataType, ua.DataValue(ua.Variant(ua.NodeId(ua.ObjectIds.Double))))
        await v.write_attribute(ua.AttributeIds.ValueRank, ua.DataValue(ua.Variant(-1, ua.VariantType.Int32)))
    else:
        v = await owner.add_variable(ua.NodeId(f"{type_id}.{name}", idx), ua.QualifiedName(name, idx),
                                     ua.Variant(False, ua.VariantType.Boolean) if boolean
                                     else ua.Variant(0.0, ua.VariantType.Double))
    await v.set_modelling_rule(True)
    return v


async def _build_types(server: Server, idx: int) -> dict:
    """ObjectTypes of this namespace, so a client sees WHAT each object is, not only its values."""
    base = server.nodes.base_object_type
    t = {}
    t["TemperatureIndicatorType"] = await base.add_object_type(ua.NodeId("TemperatureIndicatorType", idx),
                                                               ua.QualifiedName("TemperatureIndicatorType", idx))
    await _type_member(server, t["TemperatureIndicatorType"], idx, "TemperatureIndicatorType", "PV", True)
    t["ControlLoopType"] = await base.add_object_type(ua.NodeId("ControlLoopType", idx),
                                                      ua.QualifiedName("ControlLoopType", idx))
    for m in ("SP", "PV", "OP"):
        await _type_member(server, t["ControlLoopType"], idx, "ControlLoopType", m, True)
    t["SimulationInputsType"] = await base.add_object_type(ua.NodeId("SimulationInputsType", idx),
                                                           ua.QualifiedName("SimulationInputsType", idx))
    for _, bn, *_ in SIMULATION:
        await _type_member(server, t["SimulationInputsType"], idx, "SimulationInputsType", bn, False)
    await _type_member(server, t["SimulationInputsType"], idx, "SimulationInputsType", "DriveFault", False, boolean=True)
    t["PumpType"] = await base.add_object_type(ua.NodeId("PumpType", idx), ua.QualifiedName("PumpType", idx))
    for m in ("Speed", "Flow"):
        await _type_member(server, t["PumpType"], idx, "PumpType", m, True)
    for m in ("Running", "DriveFault"):
        await _type_member(server, t["PumpType"], idx, "PumpType", m, False, boolean=True)
    t["HeatingCurveType"] = await base.add_object_type(ua.NodeId("HeatingCurveType", idx),
                                                       ua.QualifiedName("HeatingCurveType", idx))
    for _, bn, *_ in PARAMETERS:
        await _type_member(server, t["HeatingCurveType"], idx, "HeatingCurveType", bn, False)
    t["HeatingPlantType"] = await base.add_object_type(ua.NodeId("HeatingPlantType", idx),
                                                       ua.QualifiedName("HeatingPlantType", idx))
    for name, tname in [("TIC101", "ControlLoopType"), ("TI102", "TemperatureIndicatorType"),
                        ("TI103", "TemperatureIndicatorType"), ("PDIC301", "ControlLoopType"),
                        ("P301", "PumpType"),
                        ("HeatingCurve", "HeatingCurveType"), ("Simulation", "SimulationInputsType")]:
        o = await t["HeatingPlantType"].add_object(ua.NodeId(f"HeatingPlantType.{name}", idx),
                                                   ua.QualifiedName(name, idx))
        await _set_type(o, t[tname])
        await o.set_modelling_rule(True)
    return t


async def _set_type(node, type_node) -> None:
    await node.delete_reference(ua.NodeId(ua.ObjectIds.BaseObjectType), ua.ObjectIds.HasTypeDefinition,
                                bidirectional=False)
    await node.add_reference(type_node, ua.ObjectIds.HasTypeDefinition, bidirectional=False)


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
    types = await _build_types(server, idx)
    objects = server.nodes.objects
    plant = await objects.add_object(ua.NodeId("HeatingPlant", idx), ua.QualifiedName("HeatingPlant", idx))
    await _set_type(plant, types["HeatingPlantType"])
    parents = {"": plant}
    for tag, type_name, text in [
            ("TIC101", "ControlLoopType", "Flow temperature control loop (heating curve -> PID -> mixing valve)"),
            ("TI102", "TemperatureIndicatorType", "Return temperature indication"),
            ("TI103", "TemperatureIndicatorType", "Outdoor temperature indication"),
            ("PDIC301", "ControlLoopType", "Network differential pressure loop (PI -> pump speed P301)"),
            ("P301", "PumpType", "Network pump with frequency converter")]:
        o = await plant.add_object(ua.NodeId(tag, idx), ua.QualifiedName(tag, idx))
        await _set_type(o, types[type_name])
        await o.write_attribute(ua.AttributeIds.Description, ua.DataValue(ua.Variant(ua.LocalizedText(text))))
        parents[tag] = o
    nodes = {p.node_id: await _analog_item(server, parents[p.parent], idx, p) for p in POINTS}
    for p in STATUS:
        v = await parents[p.parent].add_variable(ua.NodeId(p.node_id, idx), ua.QualifiedName(p.browse_name, idx),
                                                 ua.Variant(False, ua.VariantType.Boolean))
        await v.write_attribute(ua.AttributeIds.Description, ua.DataValue(ua.Variant(ua.LocalizedText(p.description))))
        nodes[p.node_id] = v
    hc = await plant.add_object(ua.NodeId("HeatingCurve", idx), ua.QualifiedName("HeatingCurve", idx))
    await _set_type(hc, types["HeatingCurveType"])
    for nid, bn, desc, default, *_ in PARAMETERS:
        v = await hc.add_variable(ua.NodeId(nid, idx), ua.QualifiedName(bn, idx),
                                  ua.Variant(default, ua.VariantType.Double))
        await v.write_attribute(ua.AttributeIds.Description, ua.DataValue(ua.Variant(ua.LocalizedText(desc))))
        await v.set_writable()
        nodes[nid] = v
    sim = await plant.add_object(ua.NodeId("Simulation", idx), ua.QualifiedName("Simulation", idx))
    await _set_type(sim, types["SimulationInputsType"])
    await sim.write_attribute(ua.AttributeIds.Description, ua.DataValue(ua.Variant(ua.LocalizedText(
        "Inputs that replace the real world in the twin; not plant signals"))))
    for nid, bn, desc, default, *_ in SIMULATION:
        v = await sim.add_variable(ua.NodeId(nid, idx), ua.QualifiedName(bn, idx),
                                   ua.Variant(default, ua.VariantType.Double))
        await v.write_attribute(ua.AttributeIds.Description, ua.DataValue(ua.Variant(ua.LocalizedText(desc))))
        await v.set_writable()
        nodes[nid] = v
    v = await sim.add_variable(ua.NodeId("Simulation.DriveFault", idx), ua.QualifiedName("DriveFault", idx),
                               ua.Variant(False, ua.VariantType.Boolean))
    await v.write_attribute(ua.AttributeIds.Description, ua.DataValue(ua.Variant(ua.LocalizedText(
        "Simulated drive fault of P301 (true = fault): the pump must stop"))))
    await v.set_writable()
    nodes["Simulation.DriveFault"] = v
    nodes["_plant"] = plant
    return idx, nodes


async def _client_certificate_validator(trusted_dir: pathlib.Path):
    """Accept a client certificate only if it is in the trust list, valid now, issued for a client and
    for the application URI the client claims. An empty trust list rejects every client."""
    if not any(trusted_dir.glob("*.der")) and not any(trusted_dir.glob("*.pem")):
        async def reject_all(cert, app_description):
            raise ServiceError(ua.StatusCodes.BadCertificateUntrusted)
        return reject_all
    store = TrustStore([trusted_dir], [])
    await store.load()
    return CertificateValidator(
        CertificateValidatorOptions.TIME_RANGE | CertificateValidatorOptions.URI
        | CertificateValidatorOptions.EXT_KEY_USAGE | CertificateValidatorOptions.TRUSTED
        | CertificateValidatorOptions.PEER_CLIENT, store)


def load_dwd() -> list[float]:
    with DWD_FILE.open() as f:
        return [float(r["t_outdoor_degC"]) for r in csv.DictReader(f)]


async def run(seconds: float | None = None, time_lapse: float = 60.0, outdoor: str = "dwd",
              endpoint: str = ENDPOINT, ready: asyncio.Event | None = None,
              password: str | None = None, pki: pathlib.Path = security.PKI, plant_kind: str = "twin",
              tclab_pi: tuple[float, float] | None = None) -> None:
    """Run the twin and serve it.
    outdoor="dwd": replay the real Magdeburg week, one DWD hour every 3600/time_lapse s.
    outdoor="manual": the outdoor temperature comes from Simulation.OutdoorTemperature (OPC UA write).
    password=None: no security (local demo only). Otherwise Basic256Sha256 SignAndEncrypt, trusted client
    certificates only, username "operator" with this password.
    plant_kind: "twin" (the 1:1 PLC model), "realistic" (valve actuator, sensor noise, flow-dependent dead
    time, robust PI from heating_plant.tuning), "tclab-emulator" or "tclab" (real rig on USB, real time).
    TCLab: TIC101 drives heater Q1 and reads T1 with a SIMC PI from a step-test identification."""
    secure = password is not None
    server = Server(user_manager=security.PasswordUserManager(security.USERNAME, password) if secure else None)
    await server.init()
    server.set_endpoint(endpoint)
    server.set_server_name("Heating plant twin (heating-plant-aas)")
    if secure:
        cert, key = await security.ensure_certificate("server", security.SERVER_URI, server=True, pki=pki)
        await server.set_application_uri(security.SERVER_URI)
        await server.load_certificate(cert)
        await server.load_private_key(key)
        server.set_security_policy([ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
        server.set_identity_tokens([ua.UserNameIdentityToken])  # no anonymous login
        server.set_certificate_validator(await _client_certificate_validator(pki / "trusted"))
    else:
        server.set_security_policy([ua.SecurityPolicyType.NoSecurity])  # local demo only; see README
    idx, n = await build(server)
    hours = load_dwd() if outdoor == "dwd" else None
    plant = HeatingPlant(t_outdoor=hours[0] if hours else 0.0)
    if plant_kind == "twin":
        plant.settle()
    elif plant_kind == "realistic":
        from dataclasses import replace

        from heating_plant.tuning import ROBUST
        plant = HeatingPlant.realistic(t_outdoor=plant.t_outdoor)
        plant.pid = replace(ROBUST)
        plant.settle()
    else:
        from heating_plant import tclab_backend as tb
        if plant_kind == "tclab" and (tclab_pi is None or time_lapse != 1):
            raise ValueError("Real TCLab: pass tclab_pi from a hardware step test and run in real time")
        kc, ti = tclab_pi or tb.identify(tb.step_test()).simc_pi()
        plant.circuit = tb.TCLabCircuit(hardware=plant_kind == "tclab")
        plant.pid = Pid2Dof(kp=kc, ti=ti, td=0.0, b=1.0, c=0.0, ts=1.0)
    await n["TI103.PV"].write_value(plant.t_outdoor)
    await n["Simulation.OutdoorTemperature"].write_value(plant.t_outdoor)
    last = {nid: default for nid, _, _, default, *_ in SIMULATION + PARAMETERS}
    last["Simulation.OutdoorTemperature"] = plant.t_outdoor
    async with server:
        if ready:
            ready.set()
        elapsed = 0.0
        while seconds is None or elapsed < seconds:
            if hours:
                plant.t_outdoor = hours[int(elapsed * time_lapse / 3600) % len(hours)]
                await n["TI103.PV"].write_value(plant.t_outdoor)
            else:
                plant.t_outdoor = await _checked(n, last, "Simulation.OutdoorTemperature")
                await n["TI103.PV"].write_value(plant.t_outdoor)
            plant.pump.load = await _checked(n, last, "Simulation.ConsumerLoad")
            plant.pump.drive_ok = (await n["Simulation.DriveFault"].read_value()) is not True
            plant.slope = await _checked(n, last, "HeatingCurve.Slope")
            plant.level = await _checked(n, last, "HeatingCurve.Level")
            plant.t_flow_max = await _checked(n, last, "HeatingCurve.FlowMax")
            # One 100 ms OB30 cycle of plant time per 100 ms of wall time, times the time-lapse factor
            for _ in range(max(1, round(time_lapse))):
                plant.step()
            for nid, val in [("TIC101.SP", plant.t_flow_set), ("TIC101.PV", plant.t_flow_measured),
                             ("TIC101.OP", plant.valve), ("TI102.PV", plant.circuit.t_return),
                             ("PDIC301.SP", plant.pump.dp_set), ("PDIC301.PV", plant.pump.network.dp),
                             ("PDIC301.OP", plant.pump.speed_set), ("P301.Speed", plant.pump.network.speed),
                             ("P301.Flow", plant.pump.network.flow)]:
                await n[nid].write_value(float(val))
            await n["P301.Running"].write_value(plant.pump.pump_on)
            await n["P301.DriveFault"].write_value(not plant.pump.drive_ok)
            await asyncio.sleep(0.1)
            elapsed += 0.1


async def export_nodeset(path: pathlib.Path) -> None:
    server = Server()
    await server.init()
    idx, n = await build(server)
    plant = n["_plant"]
    type_roots = [server.get_node(ua.NodeId(t, idx)) for t in
                  ("TemperatureIndicatorType", "ControlLoopType", "HeatingCurveType", "SimulationInputsType", "PumpType",
                   "HeatingPlantType")]
    nodes = type_roots + [plant] + await plant.get_children()
    queue = list(nodes)
    while queue:
        for c in await queue.pop().get_children():
            if c.nodeid.NamespaceIndex != 0 and c not in nodes:
                nodes.append(c)
                queue.append(c)
    await server.export_xml(nodes, str(path))
    # LF on every OS, so CI can check the committed file is up to date byte for byte
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Heating-plant twin as OPC UA server")
    ap.add_argument("--outdoor", choices=["dwd", "manual"], default="dwd")
    ap.add_argument("--time-lapse", type=float, default=60.0, help="plant seconds per wall second")
    ap.add_argument("--export-nodeset", type=pathlib.Path)
    ap.add_argument("--insecure", action="store_true", help="no encryption, no login (local demo only)")
    ap.add_argument("--plant", choices=["twin", "realistic", "tclab-emulator", "tclab"], default="twin")
    ap.add_argument("--endpoint", default=ENDPOINT,
                    help="address to listen on; use the machine's own IP to serve other devices "
                         "(then write the AAS with: python -m heating_plant.aas_builder --endpoint ... --out site/)")
    ap.add_argument("--tclab-pi", type=float, nargs=2, metavar=("KC", "TI"),
                    help="PI from a step test on the real rig (required with --plant tclab)")
    a = ap.parse_args()
    if a.export_nodeset:
        asyncio.run(export_nodeset(a.export_nodeset))
    else:
        pw = None if a.insecure else security.password_from_env()
        print(f"Serving {a.endpoint} ({'NO security' if a.insecure else 'Basic256Sha256 SignAndEncrypt, user operator'})")
        asyncio.run(run(time_lapse=a.time_lapse, outdoor=a.outdoor, password=pw, plant_kind=a.plant, endpoint=a.endpoint,
                        tclab_pi=tuple(a.tclab_pi) if a.tclab_pi else None))
