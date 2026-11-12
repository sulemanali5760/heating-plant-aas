"""Live AAS of the plant: the aidconnect gateway with this project's certificates and login.

    python -m heating_plant.live        (the secure OPC UA server must be running)

Values appear as submodel OperationalData on http://127.0.0.1:8082/api/v3.1/submodels
"""
import asyncio
import pathlib

import aidconnect
from aidconnect.gateway import BASE_PATH

from heating_plant import security

AASX = pathlib.Path(__file__).resolve().parents[1] / "model/HeatingPlant.aasx"


async def main(port: int = 8082) -> None:
    cert, key = await security.ensure_certificate("client", security.CLIENT_URI, server=False)
    gw = aidconnect.Gateway(AASX, client_cert=cert, client_key=key, server_cert=security.PKI / "server_cert.der",
                            username=security.USERNAME, password=security.password_from_env(),
                            app_uri=security.CLIENT_URI)
    gw.serve_in_thread(port)
    print(f"Live AAS on http://127.0.0.1:{port}{BASE_PATH}/submodels  (submodel OperationalData)")
    await gw.run()


if __name__ == "__main__":
    asyncio.run(main())
