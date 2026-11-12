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


async def _secure_client(cert, key, password, server_cert):
    c = Client(ENDPOINT)
    c.application_uri = security.CLIENT_URI
    await c.set_security(SecurityPolicyBasic256Sha256, str(cert), str(key), server_certificate=str(server_cert),
                         mode=ua.MessageSecurityMode.SignAndEncrypt)
    c.set_user(security.USERNAME)
    c.set_password(password)
    return c


async def _read_pv(c: Client) -> float:
    async with c:
        idx = await c.get_namespace_index(NAMESPACE)
        return await c.get_node(ua.NodeId("TIC101.SP", idx)).read_value()


def _run(coro):
    return asyncio.run(coro)


def test_trusted_client_with_password_reads(tmp_path):
    async def go():
        cert, key = await _client_cert(tmp_path, "good")

        async def check():
            return await _read_pv(await _secure_client(cert, key, PASSWORD, tmp_path / "server_cert.der"))
        return await _with_server(tmp_path, check)
    assert _run(go()) == pytest.approx(44.0)  # 0 °C outdoor -> 44 °C (heating curve)


# How each refusal shows up at the client (asyncua 2.1.0). A server that cannot decrypt the request of a client
# that encrypted for another certificate stays silent, so that case ends in the client's timeout.
REFUSAL = {"no_security": "No matching endpoints", "wrong_password": "BadUserAccessDenied",
           "untrusted_certificate": "BadCertificateUntrusted", "wrong_server": "TimeoutError"}


@pytest.mark.parametrize("case", ["no_security", "wrong_password", "untrusted_certificate", "wrong_server"])
def test_refused(tmp_path, case):
    async def go():
        cert, key = await _client_cert(tmp_path, case, trusted=case != "untrusted_certificate")
        good_cert, good_key = await _client_cert(tmp_path, "good")
        server_cert = tmp_path / "server_cert.der"
        if case == "wrong_server":  # the client expects another server's certificate than the one answering
            server_cert, _ = await security.ensure_certificate("other", security.SERVER_URI, server=True,
                                                               pki=tmp_path / "other")

        async def attempt():
            try:
                if case == "no_security":
                    c = Client(ENDPOINT)
                    c.set_user(security.USERNAME)
                    c.set_password(PASSWORD)
                else:
                    c = await _secure_client(cert, key, "wrong" if case == "wrong_password" else PASSWORD,
                                             server_cert)
                await asyncio.wait_for(_read_pv(c), 10)
                outcome = "accepted"
            except Exception as e:
                outcome = f"{type(e).__module__}.{type(e).__name__}: {e}"
            # The refusal must come from the running server, not from a dead one: a good client still reads.
            alive = await _read_pv(await _secure_client(good_cert, good_key, PASSWORD, tmp_path / "server_cert.der"))
            return outcome, alive
        return await _with_server(tmp_path, attempt)
    outcome, alive = _run(go())
    assert alive == pytest.approx(44.0)
    assert REFUSAL[case] in outcome, outcome
