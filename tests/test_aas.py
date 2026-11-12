"""The AAS must (1) pass the IDTA test engine, (2) conform to the official IDTA templates,
(3) describe the OPC UA interface so that every href and browse path resolves on the server."""
import asyncio
import json
import pathlib

import aas_test_engines.file as engine
import pytest
from asyncua import Client, ua

from heating_plant import aas_builder as b
from heating_plant.opcua_server import NAMESPACE, run

ROOT = pathlib.Path(__file__).resolve().parents[1]
JSON_FILE, AASX_FILE = b.write()
ENV = json.loads(JSON_FILE.read_text(encoding="utf-8"))
SM = {s["idShort"]: s for s in ENV["submodels"]}
CARD = "SMT/Cardinality"
# Template placeholders whose instances may carry any idShort / own semantic IDs
WILDCARD_IDSHORTS = {"ArbitraryProperty", "ArbitraryMLP", "ArbitraryRange", "ArbitrarySMC", "ArbitrarySML",
                     "Section", "property_name"}
# IDTA 02008-1-1 table 4: Time may use any of these time scales; record variables are {Variable}, 0..n
TIME_SEMANTICS = {f"https://admin-shell.io/idta/TimeSeries/{t}/1/1"
                  for t in ("UtcTime", "TaiTime", "RelativePointInTime", "RelativeTimeDuration")}
VARIABLE_PLACEHOLDERS = {"sampleAccelerationX", "sampleAccelerationY", "sampleAccelerationZ"}
# AID 1.2: the submodel needs one or more interfaces; the template shows one example per protocol.
ALIASES = {"InterfaceOPCUA": "InterfaceTemplateForOPCUA"}


def _is_placeholder(name):
    return (name in WILDCARD_IDSHORTS or name in VARIABLE_PLACEHOLDERS
            or (name or "").startswith("InterfaceTemplateFor"))


def test_idta_test_engine_json():
    with JSON_FILE.open(encoding="utf-8") as f:
        assert engine.check_json_file(f).ok()


def test_idta_test_engine_aasx():
    with AASX_FILE.open("rb") as f:
        assert engine.check_aasx_file(f).ok()


def _sem(e):
    return (e.get("semanticId") or {}).get("keys", [{}])[0].get("value")


def _card(e):
    # Newer templates write "SMT/Cardinality", older ones (e.g. Time Series 1.1) "Cardinality"
    return next((q["value"] for q in e.get("qualifiers", []) if q.get("type") in (CARD, "Cardinality")), "ZeroToOne")


def _kids(e):
    v = e.get("value") if "value" in e else e.get("submodelElements")
    return v if isinstance(v, list) and v and isinstance(v[0], dict) and "modelType" in v[0] else []


def _conform(inst, tpl, path, errors):
    """Each template child with cardinality One/OneToMany must exist; each instance child must match a
    template child by idShort (or a placeholder) and carry the template's semanticId."""
    tkids = _kids(tpl)
    by_name = {t.get("idShort"): t for t in tkids}
    placeholders = [t for t in tkids if t.get("idShort") in WILDCARD_IDSHORTS or "idShort" not in t]
    if any((t.get("idShort") or "").startswith("InterfaceTemplateFor") for t in tkids) and not _kids(inst):
        errors.append(f"{path}: at least one interface required")
    for t in tkids:
        if _card(t) in ("One", "OneToMany") and not _is_placeholder(t.get("idShort")) and "idShort" in t:
            if not any(i.get("idShort") == t["idShort"] for i in _kids(inst)):
                errors.append(f"{path}/{t['idShort']}: mandatory element missing")
    for i in _kids(inst):
        name = i.get("idShort", "")
        t = by_name.get(ALIASES.get(name, name))
        if t is None:  # repeated element: template idShort + suffix (Record001, InternalSegment...)
            t = next((c for c in tkids if c.get("idShort") and name.startswith(c["idShort"])
                      and _card(c) in ("ZeroToMany", "OneToMany") and c["modelType"] == i["modelType"]), None)
        if t is None:
            t = next((p for p in placeholders if p["modelType"] == i["modelType"]), None)
        if t is None and i["modelType"] == "Property":
            t = next((c for c in tkids if c.get("idShort") in VARIABLE_PLACEHOLDERS), None)
        if t is None:
            errors.append(f"{path}/{i.get('idShort')}: not defined in template")
            continue
        free = t.get("idShort") in {"ArbitraryProperty", "ArbitraryMLP", "ArbitraryRange"} | VARIABLE_PLACEHOLDERS
        time_ok = t.get("idShort") == "Time" and _sem(i) in TIME_SEMANTICS
        if not free and not time_ok and _sem(i) != _sem(t):
            errors.append(f"{path}/{i.get('idShort')}: semanticId {_sem(i)} != template {_sem(t)}")
        _conform(i, t, f"{path}/{i.get('idShort', '[]')}", errors)


@pytest.mark.parametrize("name, template", [
    ("Nameplate", b.NAMEPLATE), ("TechnicalData", b.TECHDATA), ("AssetInterfacesDescription", b.AID),
    ("TimeSeries", b.TIMESERIES)])
def test_conforms_to_official_template(name, template):
    inst = SM[name]
    assert _sem(inst) == _sem(template)
    errors = []
    if name == "Nameplate":  # AddressInformation is the Contact Information drop-in
        tpl = json.loads(json.dumps(template))
        addr = next(e for e in tpl["submodelElements"] if e["idShort"] == "AddressInformation")
        addr["value"] = b.find(b.CONTACT, "ContactInformation")["value"]
        template = tpl
    _conform(inst, template, name, errors)
    assert not errors, "\n".join(errors)


def _aid_properties():
    itf = b.find(SM["AssetInterfacesDescription"], "InterfaceOPCUA")
    props = b.find(itf, "InteractionMetadata", "properties")["value"]
    base = b.find(itf, "EndpointMetadata", "base")["value"]
    out = []
    for p in props:
        f = {e["idShort"]: e["value"] for e in b.find(p, "forms")["value"]}
        key = next(e["value"] for e in p["value"] if e["idShort"] == "key")
        out.append((key, f["href"], f["uav_browsePath"]))
    return base, out


async def _resolve_all():
    base, props = _aid_properties()
    endpoint = base.replace(":4840", ":48402")  # same server, test port
    ready = asyncio.Event()
    srv = asyncio.create_task(run(seconds=15, outdoor="manual", endpoint=endpoint, ready=ready))
    await asyncio.wait_for(ready.wait(), 10)
    async with Client(endpoint) as c:
        idx = await c.get_namespace_index(NAMESPACE)
        for key, href, path in props:
            nsu, sid = href.split(";s=")
            assert nsu == f"nsu={NAMESPACE}" and sid == key
            by_id = c.get_node(ua.NodeId(sid, idx))
            await by_id.read_value()
            parts = path.split("/")[3:]  # after /Root/Objects/
            assert all(p.startswith(f"{idx}:") for p in parts), (path, idx)
            by_path = await c.nodes.objects.get_child(parts)
            assert by_path.nodeid == by_id.nodeid, key
    srv.cancel()


def test_aid_hrefs_and_browse_paths_resolve_on_server():
    asyncio.run(_resolve_all())


def test_units_are_machine_readable():
    """Every own concept description carries IEC 61360 content: a program gets the unit as a field."""
    cds = {c["id"]: c["embeddedDataSpecifications"][0]["dataSpecificationContent"] for c in ENV["conceptDescriptions"]}
    assert len(cds) == len(b.TECH) + len(b.TS_VARS)
    assert cds[b._cd_id("MixingCircuit/DeadTime")]["unit"] == "s"
    assert cds[b._cd_id("ControllerPDIC301/ProportionalGain")]["unit"] == "%/bar"
    assert cds[b._cd_id("TIC101_PV")]["unit"] == "°C"
    slope = cds[b._cd_id("HeatingCurve/Slope")]                          # dimensionless
    assert "unit" not in slope and slope["dataType"] == "REAL_COUNT"
    assert cds[b._cd_id("Controller/CPU")]["dataType"] == "STRING"
    # each technical property points at its concept description
    for sec in b.find(SM["TechnicalData"], "TechnicalPropertyAreas")["value"][0]["value"]:
        for p in sec["value"]:
            assert _sem(p) in cds, p["idShort"]


def test_every_opcua_point_is_described():
    _, props = _aid_properties()
    assert {k for k, _, _ in props} == {p.node_id for p in b.ALL_POINTS} and len(props) == 12


def test_time_series_holds_the_real_week():
    seg = b.find(SM["TimeSeries"], "Segments", "InternalSegmentMagdeburgWeek")
    records = b.find(seg, "Records")["value"]
    assert len(records) == 168 == int(b.find(seg, "RecordCount")["value"])
    first = {e["idShort"]: e["value"] for e in records[0]["value"]}
    assert first["Time"] == "2022-12-12T00:59:00Z"
    assert first["TI103_PV"] == "-2.4"  # DWD 2022-12-12 00 UTC: -2.4 °C
    outdoor = [float(next(e["value"] for e in r["value"] if e["idShort"] == "TI103_PV")) for r in records]
    assert (min(outdoor), max(outdoor)) == (-11.4, -0.9)
