"""Connect to the plant using ONLY its Asset Administration Shell.

The client knows nothing about the plant: the aidconnect library opens the AASX, finds the Asset
Interfaces Description submodel (IDTA 02017), takes the OPC UA endpoint, the security settings and the
datapoints from it and connects. This is the "plug and produce" use of the AAS: no hand-written tag list.
(The code that does this started in this repository and now lives in github.com/sulemanali5760/aidconnect.)
"""
import asyncio
import csv
import pathlib
import sys

import aidconnect

from heating_plant.security import CLIENT_URI, USERNAME


async def watch(aasx_path: pathlib.Path, seconds: float, out_csv: pathlib.Path | None = None,
                endpoint_override: str | None = None, period: float = 0.5,
                cert=None, key=None, password: str | None = None, server_cert=None) -> list[dict]:
    """Print (and return) all datapoints every `period` seconds."""
    itf = aidconnect.read_interface(aasx_path)
    sec, points = itf.security, itf.points
    print(f"AAS says: OPC UA endpoint {itf.endpoint}, {len(points)} datapoints, "
          f"security {sec.policy or 'None'} {sec.mode or ''}, login {sec.user_token or 'Anonymous'}")
    rows = []
    async with aidconnect.connect(itf, endpoint=endpoint_override, client_cert=cert, client_key=key,
                                  server_cert=server_cert, username=USERNAME, password=password,
                                  app_uri=CLIENT_URI) as session:
        print("  ".join((f"{p.name} [{p.unit}]" if p.unit else p.name).rjust(16) for p in points))
        t = 0.0
        while t < seconds:
            await asyncio.sleep(period)
            t += period
            values = await session.read()
            rows.append({"t_s": round(t, 2), **values})
            print("  ".join(f"{values[p.name]:16.2f}" if p.kind == "number" else str(values[p.name]).rjust(16)
                            for p in points))
    if out_csv:
        with out_csv.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return rows


async def _main(path: pathlib.Path, seconds: float) -> None:
    from heating_plant import security
    cert, key = await security.ensure_certificate("client", security.CLIENT_URI, server=False)
    await watch(path, seconds, cert=cert, key=key, password=security.password_from_env(),
                server_cert=security.PKI / "server_cert.der")


if __name__ == "__main__":
    # First run creates pki/client_cert.der; copy it into pki/trusted/ (security.trust) before the server starts.
    # The server creates pki/server_cert.der on its first start; the client only talks to that certificate.
    path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "model/HeatingPlant.aasx")
    asyncio.run(_main(path, float(sys.argv[2]) if len(sys.argv) > 2 else 10))
