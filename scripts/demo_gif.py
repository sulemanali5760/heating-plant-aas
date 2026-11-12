"""Record a real run (secure server + AID client) and render the client's console output as a GIF.

Nothing is staged: the text in the GIF is exactly what heating_plant.aid_client printed while it
connected to the twin using only model/HeatingPlant.aasx. Output: docs/demo_aid_client.gif
"""
import asyncio
import contextlib
import io
import pathlib
import secrets
import tempfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter

from heating_plant import security
from heating_plant.aid_client import watch
from heating_plant.opcua_server import run

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENDPOINT = "opc.tcp://127.0.0.1:48420/heating-plant/"


async def record() -> list[str]:
    pki = pathlib.Path(tempfile.mkdtemp())
    pw = secrets.token_urlsafe(16)
    cert, key = await security.ensure_certificate("client", security.CLIENT_URI, server=False, pki=pki / "client")
    security.trust(cert, pki)
    ready = asyncio.Event()
    srv = asyncio.create_task(run(seconds=30, time_lapse=3600, outdoor="dwd", endpoint=ENDPOINT, ready=ready,
                                  password=pw, pki=pki))
    await asyncio.wait_for(ready.wait(), 15)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        await watch(ROOT / "model/HeatingPlant.aasx", seconds=8, endpoint_override=ENDPOINT, period=1.0,
                    cert=cert, key=key, password=pw, server_cert=pki / "server_cert.der")
    srv.cancel()
    return ["$ python -m heating_plant.aid_client model/HeatingPlant.aasx"] + buf.getvalue().splitlines()


def render(lines: list[str]) -> None:
    fig = plt.figure(figsize=(12, 3.6), dpi=90)
    fig.patch.set_facecolor("#1e1e1e")
    ax = fig.add_axes((0, 0, 1, 1))
    ax.axis("off")
    text = ax.text(0.01, 0.97, "", va="top", ha="left", family="monospace", fontsize=8.2, color="#d4d4d4")

    def frame(i):
        text.set_text("\n".join(lines[: i + 1]))
        return (text,)
    anim = FuncAnimation(fig, frame, frames=len(lines) + 4, interval=700, blit=True)
    anim.save(ROOT / "docs/demo_aid_client.gif", writer=PillowWriter(fps=1.4))


if __name__ == "__main__":
    out = None
    out = asyncio.run(record())
    render(out)
    print("\n".join(out))
