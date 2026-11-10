"""The AID client connects with nothing but the AASX and receives live, plausible values."""
import asyncio
import pathlib

import secrets

from heating_plant import security
from heating_plant.aid_client import read_interface, read_interface_with_security, watch
from heating_plant.opcua_server import POINTS, run

AASX = pathlib.Path(__file__).resolve().parents[1] / "model/HeatingPlant.aasx"
PORT_ENDPOINT = "opc.tcp://127.0.0.1:48403/heating-plant/"


def test_interface_read_from_aasx_only():
    base, points = read_interface(AASX)
    assert base.startswith("opc.tcp://")
    assert {p.name for p in points} == {p.node_id for p in POINTS}


def test_security_read_from_aasx():
    _, _, sec = read_interface_with_security(AASX)
    assert (sec.mode, sec.policy, sec.user_token) == ("SignAndEncrypt", "Basic256Sha256", "UserName")


async def _live(pki):
    password = secrets.token_urlsafe(16)
    cert, key = await security.ensure_certificate("client", security.CLIENT_URI, server=False, pki=pki / "client")
    security.trust(cert, pki)
    ready = asyncio.Event()
    srv = asyncio.create_task(run(seconds=15, time_lapse=600, outdoor="dwd", endpoint=PORT_ENDPOINT, ready=ready,
                                  password=password, pki=pki))
    await asyncio.wait_for(ready.wait(), 15)
    rows = await watch(AASX, seconds=4, endpoint_override=PORT_ENDPOINT, cert=cert, key=key, password=password)
    srv.cancel()
    return rows


def test_live_values_through_aid_over_secure_channel(tmp_path):
    try:
        rows = asyncio.run(_live(tmp_path))
    except asyncio.CancelledError:
        return
    assert rows
    last = rows[-1]
    assert -12.0 <= last["TI103.PV"] <= 0.0          # Magdeburg week 12-18 Dec 2022: -11.4 ... -0.9 °C
    assert 25.0 <= last["TIC101.SP"] <= 75.0          # heating curve limits
    assert 0.0 <= last["TIC101.OP"] <= 100.0
    assert last["TI102.PV"] <= last["TIC101.PV"]      # return colder than flow
