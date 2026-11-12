"""The gateway gets one AAS file and does the rest: connect as described, publish live values, reconnect.
The second half runs it against an asset it has never seen, to show nothing is specific to the heating plant."""
import asyncio
import base64
import hashlib
import json
import pathlib
import secrets
import urllib.request

import pytest
from asyncua import Server, ua

from heating_plant import aas_builder as b
from heating_plant import security
from heating_plant.aid_client import parse_href
from heating_plant.gateway import BASE_PATH, Gateway, fetch_aasx
from heating_plant.opcua_server import Point, run

AASX = pathlib.Path(__file__).resolve().parents[1] / "model/HeatingPlant.aasx"
PLANT_ENDPOINT = "opc.tcp://127.0.0.1:48430/heating-plant/"
TANK_ENDPOINT = "opc.tcp://127.0.0.1:48431/tank/"
REST_PORT = 48432


def _live(port: int, gw: Gateway) -> dict:
    """OperationalData as a REST client sees it: idShort -> value."""
    sm_id = base64.urlsafe_b64encode(gw.live.id.encode()).decode().rstrip("=")
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{BASE_PATH}/submodels/{sm_id}", timeout=10) as r:
        return {e["idShort"]: e.get("value") for e in json.loads(r.read())["submodelElements"]}


async def _rest(port, gw):
    return await asyncio.to_thread(_live, port, gw)


async def _stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _plant_scenario(pki):
    password = secrets.token_urlsafe(16)
    cert, key = await security.ensure_certificate("client", security.CLIENT_URI, server=False, pki=pki / "client")
    security.trust(cert, pki)

    async def start_plant():
        ready = asyncio.Event()
        task = asyncio.create_task(run(seconds=60, outdoor="manual", endpoint=PLANT_ENDPOINT, ready=ready,
                                       password=password, pki=pki))
        await asyncio.wait_for(ready.wait(), 15)
        return task
    plant = await start_plant()
    gw = Gateway(AASX, cert=cert, key=key, server_cert=pki / "server_cert.der", password=password,
                 endpoint_override=PLANT_ENDPOINT, period=0.2)
    rest = gw.serve_in_thread(REST_PORT)
    connected = asyncio.Event()
    gw_task = asyncio.create_task(gw.run(seconds=60, connected=connected))
    try:
        await asyncio.wait_for(connected.wait(), 15)
        first = await _rest(REST_PORT, gw)
        # The plant goes away (power cut, cable): the gateway must say so and come back on its own
        await _stop(plant)
        for _ in range(100):
            if not connected.is_set():
                break
            await asyncio.sleep(0.1)
        down = await _rest(REST_PORT, gw)
        plant = await start_plant()
        await asyncio.wait_for(connected.wait(), 30)
        back = await _rest(REST_PORT, gw)
        shells = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{REST_PORT}{BASE_PATH}/shells", timeout=10).read())
    finally:
        rest.shutdown()
        await _stop(gw_task)
        await _stop(plant)
    return first, down, back, shells, gw


def test_plant_live_values_and_reconnect(tmp_path):
    first, down, back, shells, gw = asyncio.run(_plant_scenario(tmp_path))
    assert first["ConnectionState"].startswith("connected")
    assert float(first["TIC101_SP"]) == pytest.approx(44.0)        # 0 °C outdoor -> 44 °C
    assert float(first["PDIC301_SP"]) == pytest.approx(1.0)
    assert {"TIC101_PV", "TIC101_OP", "TI102_PV", "TI103_PV", "PDIC301_PV", "PDIC301_OP"} <= set(first)
    assert down["ConnectionState"].startswith("disconnected")
    assert back["ConnectionState"].startswith("connected") and back["LastUpdate"] > first["LastUpdate"]
    # The shell now lists the live submodel next to the four static ones
    refs = {r["keys"][0]["value"] for r in shells["result"][0]["submodels"]}
    assert gw.live.id in refs and len(refs) == 5


# ---------------------------------------------------------------- an asset the gateway has never seen
TANK_NS = "urn:example:tank:ua"
TANK_POINTS = [
    Point("Level", "", "Level", "Fill level of the tank", "MTR", 0, 4),
    Point("PumpOn", "", "PumpOn", "Feed pump running", "", 0, 0, kind="boolean"),
    Point("Cycles", "", "Cycles", "Pump start counter", "C62", 0, 1e6, kind="integer"),
    Point("Name", "", "Name", "Tank designation", "", 0, 0, kind="string"),
]


async def _tank_server(ready: asyncio.Event, stop: asyncio.Event):
    server = Server()
    await server.init()
    server.set_endpoint(TANK_ENDPOINT)
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    idx = await server.register_namespace(TANK_NS)
    tank = await server.nodes.objects.add_object(ua.NodeId("Tank", idx), ua.QualifiedName("Tank", idx))
    for name, value, vt in [("Level", 2.5, ua.VariantType.Double), ("PumpOn", True, ua.VariantType.Boolean),
                            ("Cycles", 7, ua.VariantType.Int64), ("Name", "B101", ua.VariantType.String)]:
        await tank.add_variable(ua.NodeId(name, idx), ua.QualifiedName(name, idx), ua.Variant(value, vt))
    async with server:
        ready.set()
        await stop.wait()


async def _tank_scenario(tmp_path):
    aid = b.asset_interfaces(TANK_ENDPOINT, TANK_NS, TANK_POINTS, secure=False, sm_id="urn:example:tank:sm:aid",
                             title="Tank B101", root="Tank")
    env = {"assetAdministrationShells": [b.shell("urn:example:tank:aas", "urn:example:tank:asset", "Tank", [aid])],
           "submodels": [aid]}
    _, tank_aasx = b.write_env(env, tmp_path, "Tank")
    ready, stop = asyncio.Event(), asyncio.Event()
    srv = asyncio.create_task(_tank_server(ready, stop))
    await asyncio.wait_for(ready.wait(), 15)
    gw = Gateway(tank_aasx, period=0.2)          # no endpoint, no tag list, no credentials: all from the file
    rest = gw.serve_in_thread(REST_PORT + 1)
    connected = asyncio.Event()
    task = asyncio.create_task(gw.run(seconds=20, connected=connected))
    try:
        await asyncio.wait_for(connected.wait(), 15)
        return await _rest(REST_PORT + 1, gw), gw
    finally:
        rest.shutdown()
        await _stop(task)
        stop.set()
        await srv


def test_unknown_asset_needs_no_code_change(tmp_path):
    live, gw = asyncio.run(_tank_scenario(tmp_path))
    assert gw.endpoint == TANK_ENDPOINT and gw.sec.mode == "None"
    assert float(live["Level"]) == 2.5
    assert live["PumpOn"] in (True, "true")
    assert int(live["Cycles"]) == 7
    assert live["Name"] == "B101"


# ---------------------------------------------------------------- where the AAS file may come from
def test_remote_file_must_be_https_and_pinned(tmp_path):
    with pytest.raises(ValueError, match="https"):
        fetch_aasx("http://example.com/plant.aasx", "00" * 32)
    with pytest.raises(ValueError, match="sha256"):
        fetch_aasx("https://example.com/plant.aasx")
    good = hashlib.sha256(AASX.read_bytes()).hexdigest()
    assert fetch_aasx(str(AASX), good) == AASX
    with pytest.raises(ValueError, match="SHA-256"):
        fetch_aasx(str(AASX), "00" * 32)


def test_secure_aas_without_server_certificate_is_refused():
    """The AID asks for SignAndEncrypt: without the server's certificate the gateway does not connect at all."""
    gw = Gateway(AASX, cert="client.der", key="client.pem", password="x", endpoint_override=PLANT_ENDPOINT)
    with pytest.raises(ValueError, match="server's certificate"):
        asyncio.run(gw.run(seconds=1))


@pytest.mark.parametrize("href, expected", [
    ("nsu=urn:a:b;s=TIC101.PV", ("urn:a:b", None, "TIC101.PV")),
    ("nsu=http://x/y/;i=42", ("http://x/y/", None, 42)),
    ("ns=3;s=\"DB\".\"tag\"", ("", 3, "\"DB\".\"tag\"")),
    ("i=2258", ("", 0, 2258)),
])
def test_parse_href(href, expected):
    assert parse_href(href) == expected


def test_parse_href_rejects_other_schemes():
    with pytest.raises(ValueError):
        parse_href("http://example.com/value")
