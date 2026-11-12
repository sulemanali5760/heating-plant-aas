"""Gateway: give it one AAS file, it connects to the asset and publishes the live values.

    AASX (file or pinned download) -> Asset Interfaces Description -> OPC UA connection as described there
    -> submodel "OperationalData" with the current values -> AAS Part 2 REST API (read-only)

Nothing in this module knows the heating plant. Endpoint, security settings, datapoints, their types and
units all come from the AAS, so the same gateway serves any asset whose AAS carries an AID with an
OPC UA interface (tests/test_gateway.py runs it against a second, unrelated server).

Secure by default:
- A remote AAS file must come over https and match a SHA-256 the operator passes. The file decides where
  the gateway sends a password, so an unpinned download is refused.
- If the AID asks for a secure channel, the gateway needs the server's certificate and talks to no other.
- The REST side is read-only and binds to localhost unless told otherwise.
- It only reads from the asset. It never writes to a controller.

Not covered: AIDs with Modbus/MQTT/HTTP interfaces, structured OPC UA values, and tests on real hardware
(only software servers were available).
"""
import asyncio
import datetime
import hashlib
import pathlib
import re
import urllib.parse
import urllib.request

from asyncua import Client, ua
from basyx.aas import model
from basyx.aas.adapter import aasx
from werkzeug.serving import make_server

from heating_plant.aid_client import AID_SEMANTIC_ID, _apply_security, _sem, node_of, read_interface_with_security
from heating_plant.security import CLIENT_URI, USERNAME

BASE_PATH = "/api/v3.1"
OPERATIONAL_SEMANTIC_ID = "https://github.com/sulemanali5760/heating-plant-aas/semantics/OperationalData/1/0"
MAX_AASX_BYTES = 20 * 1024 * 1024
TYPES = {"number": (model.datatypes.Double, float), "integer": (model.datatypes.Int, int),
         "boolean": (model.datatypes.Boolean, bool), "string": (model.datatypes.String, str)}


def fetch_aasx(source: str, sha256: str | None = None, dest: pathlib.Path | None = None) -> pathlib.Path:
    """Local path, or an https URL together with the expected SHA-256 of the file."""
    if not source.startswith(("http://", "https://")):
        path = pathlib.Path(source)
        if sha256 and hashlib.sha256(path.read_bytes()).hexdigest() != sha256.lower():
            raise ValueError(f"{path} does not match the expected SHA-256")
        return path
    url = urllib.parse.urlparse(source)
    if url.scheme != "https" and url.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("remote AAS files are only fetched over https")
    if not sha256:
        raise ValueError("a remote AAS file needs --sha256: it tells the gateway where to send credentials")
    with urllib.request.urlopen(source, timeout=30) as r:  # noqa: S310 (scheme checked above)
        data = r.read(MAX_AASX_BYTES + 1)
    if len(data) > MAX_AASX_BYTES:
        raise ValueError("AAS file larger than 20 MB, refused")
    if hashlib.sha256(data).hexdigest() != sha256.lower():
        raise ValueError("downloaded AAS file does not match the expected SHA-256")
    dest = dest or pathlib.Path("downloaded.aasx")
    dest.write_bytes(data)
    return dest


def _id_short(key: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", key)
    return s if s[:1].isalpha() else "P_" + s


class _ReadOnly:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if environ["REQUEST_METHOD"] not in ("GET", "HEAD"):
            start_response("405 Method Not Allowed", [("Allow", "GET, HEAD"), ("Content-Type", "text/plain")])
            return [b"read-only AAS repository"]
        return self.app(environ, start_response)


class Gateway:
    def __init__(self, aasx_path: pathlib.Path, cert=None, key=None, server_cert=None,
                 username: str = USERNAME, password: str | None = None, app_uri: str = CLIENT_URI,
                 endpoint_override: str | None = None, period: float = 1.0):
        self.base, self.points, self.sec = read_interface_with_security(aasx_path)
        self.endpoint = endpoint_override or self.base
        self.auth = dict(cert=cert, key=key, password=password, server_cert=server_cert,
                         username=username, app_uri=app_uri)
        self.period = period
        self.store, self.files = model.DictIdentifiableStore(), aasx.DictSupplementaryFileContainer()
        with aasx.AASXReader(str(aasx_path)) as r:
            r.read_into(self.store, self.files)
        aid = next(o for o in self.store if isinstance(o, model.Submodel) and _sem(o) == AID_SEMANTIC_ID)
        self.state = model.Property("ConnectionState", model.datatypes.String, "starting")
        self.updated = model.Property("LastUpdate", model.datatypes.DateTime)
        self.props = {}
        for p in self.points:
            if p.kind not in TYPES:
                raise ValueError(f"AID datapoint {p.name}: type {p.kind} is not supported")
            text = f"{p.title} [{p.unit}]" if p.unit else p.title
            self.props[p.name] = model.Property(_id_short(p.name), TYPES[p.kind][0],
                                                description=model.MultiLanguageTextType({"en": text}))
        self.live = model.Submodel(
            f"{aid.id}/OperationalData", id_short="OperationalData",
            submodel_element=[self.state, self.updated, *self.props.values()],
            semantic_id=model.ExternalReference((model.Key(model.KeyTypes.GLOBAL_REFERENCE, OPERATIONAL_SEMANTIC_ID),)))
        self.store.add(self.live)
        for shell in [o for o in self.store if isinstance(o, model.AssetAdministrationShell)]:
            shell.submodel.add(model.ModelReference.from_referable(self.live))

    # ---- OPC UA side
    def _set_state(self, text: str) -> None:
        if self.state.value != text:
            print(f"gateway: {text}")
        self.state.value = text

    async def run(self, seconds: float | None = None, connected: asyncio.Event | None = None) -> None:
        """Connect as the AID says and keep the values current. A lost connection is retried with a
        growing pause (1 s ... 30 s); a configuration error (missing certificate, password) is raised."""
        loop = asyncio.get_running_loop()
        end = None if seconds is None else loop.time() + seconds
        pause = 1.0
        while end is None or loop.time() < end:
            client = Client(self.endpoint, timeout=5)
            await _apply_security(client, self.sec, **self.auth)
            try:
                async with client as c:
                    nodes = [await node_of(c, p) for p in self.points]
                    self._set_state(f"connected to {self.endpoint}")
                    pause = 1.0
                    while end is None or loop.time() < end:
                        values = await c.read_values(nodes)
                        for p, v in zip(self.points, values):
                            # ponytail: plain attribute writes, read by the REST thread; a lock if a value
                            # ever needs more than one assignment
                            self.props[p.name].value = None if v is None else TYPES[p.kind][1](v)
                        self.updated.value = datetime.datetime.now(datetime.timezone.utc)
                        if connected:
                            connected.set()
                        await asyncio.sleep(self.period)
            except (OSError, asyncio.TimeoutError, ua.UaError) as e:
                self._set_state(f"disconnected ({type(e).__name__}), retrying")
                if connected:
                    connected.clear()
                await asyncio.sleep(pause)
                pause = min(pause * 2, 30.0)

    # ---- AAS side
    def serve_in_thread(self, port: int, host: str = "127.0.0.1"):
        """AAS Part 2 REST API with the asset's own submodels plus OperationalData. Returns the server;
        call .shutdown() to stop."""
        import threading

        from app.interfaces.repository import WSGIApp
        srv = make_server(host, port, _ReadOnly(WSGIApp(self.store, self.files, base_path=BASE_PATH)))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv


async def _main(a) -> None:
    from heating_plant import security
    path = fetch_aasx(a.aasx, a.sha256)
    gw = Gateway(path, endpoint_override=a.endpoint, username=a.username, period=a.period)
    if gw.sec.mode and gw.sec.mode != "None":
        cert, key = await security.ensure_certificate("client", security.CLIENT_URI, server=False)
        gw.auth.update(cert=cert, key=key, server_cert=a.server_cert)
    if gw.sec.user_token == "UserName":
        gw.auth["password"] = security.password_from_env()
    print(f"AAS says: OPC UA endpoint {gw.base}, {len(gw.points)} datapoints, "
          f"security {gw.sec.policy or 'None'} {gw.sec.mode or ''}, login {gw.sec.user_token or 'Anonymous'}")
    gw.serve_in_thread(a.port, a.host)
    print(f"Live AAS on http://{a.host}:{a.port}{BASE_PATH}/submodels  (submodel OperationalData)")
    await gw.run()


if __name__ == "__main__":
    import argparse

    from heating_plant import security
    ap = argparse.ArgumentParser(description="AAS-driven gateway: OPC UA asset -> live AAS REST API")
    ap.add_argument("aasx", help="AASX file, or https URL (then --sha256 is required)")
    ap.add_argument("--sha256", help="expected SHA-256 of the AASX file")
    ap.add_argument("--endpoint", help="use this OPC UA endpoint instead of the one in the AAS")
    ap.add_argument("--server-cert", type=pathlib.Path, default=security.PKI / "server_cert.der",
                    help="certificate of the OPC UA server (needed when the AAS asks for a secure channel)")
    ap.add_argument("--username", default=USERNAME)
    ap.add_argument("--period", type=float, default=1.0, help="seconds between reads")
    ap.add_argument("--host", default="127.0.0.1", help="REST bind address (keep localhost unless behind a proxy)")
    ap.add_argument("--port", type=int, default=8082)
    asyncio.run(_main(ap.parse_args()))
