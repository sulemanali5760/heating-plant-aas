"""Secure mode: only an encrypted, trusted, logged-in client gets data."""
import asyncio
import secrets

import pytest
from asyncua import Client, ua
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256

from heating_plant import security
from heating_plant.opcua_server import NAMESPACE, run

ENDPOINT = "opc.tcp://127.0.0.1:48404/heating-plant/"
PASSWORD = secrets.token_urlsafe(16)  # random per test run, never stored


async def _with_server(pki, check):
    ready = asyncio.Event()
    srv = asyncio.create_task(run(seconds=20, outdoor="manual", endpoint=ENDPOINT, ready=ready,
                                  password=PASSWORD, pki=pki))
    await asyncio.wait_for(ready.wait(), 15)
    try:
        return await check()
    finally:
        srv.cancel()


async def _client_cert(pki, name, trusted=True):
    """Create the client certificate; an admin puts it into the server trust list BEFORE the server starts."""
    cert, key = await security.ensure_certificate(name, security.CLIENT_URI, server=False, pki=pki / name)
    if trusted:
        security.trust(cert, pki)
    return cert, key


async def _secure_client(cert, key, password):
    c = Client(ENDPOINT)
    c.application_uri = security.CLIENT_URI
    await c.set_security(SecurityPolicyBasic256Sha256, str(cert), str(key),
                         mode=ua.MessageSecurityMode.SignAndEncrypt)
    c.set_user(security.USERNAME)
    c.set_password(password)
    return c


async def _read_pv(c: Client) -> float:
    async with c:
        idx = await c.get_namespace_index(NAMESPACE)
        return await c.get_node(ua.NodeId("TIC101.SP", idx)).read_value()


def _run(coro):
    try:
        return asyncio.run(coro)
    except asyncio.CancelledError:
        return None


def test_trusted_client_with_password_reads(tmp_path):
    async def go():
        cert, key = await _client_cert(tmp_path, "good")

        async def check():
            return await _read_pv(await _secure_client(cert, key, PASSWORD))
        return await _with_server(tmp_path, check)
    assert _run(go()) == pytest.approx(44.0)  # 0 °C outdoor -> 44 °C (heating curve)


@pytest.mark.parametrize("case", ["no_security", "wrong_password", "untrusted_certificate"])
def test_refused(tmp_path, case):
    async def go():
        cert, key = await _client_cert(tmp_path, case, trusted=case != "untrusted_certificate")

        async def attempt():
            with pytest.raises(Exception):
                if case == "no_security":
                    c = Client(ENDPOINT)
                    c.set_user(security.USERNAME)
                    c.set_password(PASSWORD)
                else:
                    c = await _secure_client(cert, key, "wrong" if case == "wrong_password" else PASSWORD)
                await asyncio.wait_for(_read_pv(c), 10)
            return "refused"
        return await _with_server(tmp_path, attempt)
    assert _run(go()) == "refused"
