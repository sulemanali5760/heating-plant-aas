"""The aidconnect gateway on this plant: it gets the AAS file, connects over the secured channel, publishes
live values on the AAS REST API and comes back by itself after the plant was gone.
(The gateway's own tests, including an unrelated asset, are in github.com/sulemanali5760/aidconnect.)"""
import asyncio
import base64
import json
import pathlib
import secrets
import urllib.request

import aidconnect
import pytest
from aidconnect.gateway import BASE_PATH

from heating_plant import security
from heating_plant.opcua_server import run

AASX = pathlib.Path(__file__).resolve().parents[1] / "model/HeatingPlant.aasx"
PLANT_ENDPOINT = "opc.tcp://127.0.0.1:48430/heating-plant/"
REST_PORT = 48432


def _live(gw) -> dict:
    sm_id = base64.urlsafe_b64encode(gw.live.id.encode()).decode().rstrip("=")
    with urllib.request.urlopen(f"http://127.0.0.1:{REST_PORT}{BASE_PATH}/submodels/{sm_id}", timeout=10) as r:
        return {e["idShort"]: e.get("value") for e in json.loads(r.read())["submodelElements"]}


async def _stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _scenario(pki):
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
    gw = aidconnect.Gateway(AASX, period=0.2, log=lambda _: None, endpoint=PLANT_ENDPOINT,
                            client_cert=cert, client_key=key, server_cert=pki / "server_cert.der",
                            username=security.USERNAME, password=password, app_uri=security.CLIENT_URI)
    rest = gw.serve_in_thread(REST_PORT)
    connected = asyncio.Event()
    gw_task = asyncio.create_task(gw.run(seconds=60, connected=connected))
    try:
        await asyncio.wait_for(connected.wait(), 15)
        first = await asyncio.to_thread(_live, gw)
        await _stop(plant)                       # the plant goes away (power cut, cable)
        for _ in range(100):
            if not connected.is_set():
                break
            await asyncio.sleep(0.1)
        down = await asyncio.to_thread(_live, gw)
        plant = await start_plant()              # and comes back
        await asyncio.wait_for(connected.wait(), 30)
        back = await asyncio.to_thread(_live, gw)
        with urllib.request.urlopen(f"http://127.0.0.1:{REST_PORT}{BASE_PATH}/shells", timeout=10) as r:
            shells = json.loads(r.read())
    finally:
        rest.shutdown()
        await _stop(gw_task)
        await _stop(plant)
    return first, down, back, shells, gw


def test_plant_live_values_and_reconnect(tmp_path):
    first, down, back, shells, gw = asyncio.run(_scenario(tmp_path))
    assert first["ConnectionState"].startswith("connected")
    assert float(first["TIC101_SP"]) == pytest.approx(44.0)        # 0 °C outdoor -> 44 °C
    assert float(first["PDIC301_SP"]) == pytest.approx(1.0)
    assert first["P301_Running"] in (True, "true") and first["P301_DriveFault"] in (False, "false")
    assert {"TIC101_PV", "TIC101_OP", "TI102_PV", "TI103_PV", "PDIC301_PV", "PDIC301_OP", "P301_Flow"} <= set(first)
    assert down["ConnectionState"].startswith("disconnected")
    assert back["ConnectionState"].startswith("connected") and back["LastUpdate"] > first["LastUpdate"]
    # The shell now lists the live submodel next to the four static ones
    refs = {r["keys"][0]["value"] for r in shells["result"][0]["submodels"]}
    assert gw.live.id in refs and len(refs) == 5
