"""Serve the plant's AAS through the standard AAS HTTP/REST API (IDTA 01002, AAS Part 2, v3.1).

Uses the Eclipse BaSyx Python server (repository interface: /shells, /submodels, ...), pinned to the
BaSyx 2.2.0 release commit. Identifiers in URLs are base64url-encoded, as the API specifies.
Read-only on purpose: the AASX file stays the single source; the server does not persist changes.
"""
import argparse
import pathlib

from app.interfaces.repository import WSGIApp
from basyx.aas import model
from basyx.aas.adapter import aasx
from werkzeug.serving import make_server, run_simple

ROOT = pathlib.Path(__file__).resolve().parents[1]
AASX = ROOT / "model/HeatingPlant.aasx"
BASE_PATH = "/api/v3.1"


class _ReadOnly:
    """WSGI middleware: allow GET/HEAD only (a published asset description, not an editing endpoint)."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if environ["REQUEST_METHOD"] not in ("GET", "HEAD"):
            start_response("405 Method Not Allowed", [("Allow", "GET, HEAD"), ("Content-Type", "text/plain")])
            return [b"read-only AAS repository"]
        return self.app(environ, start_response)


def create_app(aasx_path: pathlib.Path = AASX):
    store, files = model.DictIdentifiableStore(), aasx.DictSupplementaryFileContainer()
    with aasx.AASXReader(str(aasx_path)) as r:
        r.read_into(store, files)
    return _ReadOnly(WSGIApp(store, files, base_path=BASE_PATH))


def serve_in_thread(port: int, aasx_path: pathlib.Path = AASX):
    """For tests and demos: returns the werkzeug server; call .shutdown() to stop."""
    import threading
    srv = make_server("127.0.0.1", port, create_app(aasx_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AAS Part 2 REST API for the heating plant (read-only)")
    ap.add_argument("--port", type=int, default=8081)
    a = ap.parse_args()
    print(f"AAS repository on http://127.0.0.1:{a.port}{BASE_PATH}/shells  (localhost only)")
    run_simple("127.0.0.1", a.port, create_app())
