"""The AAS Part 2 REST API serves exactly what is in the AASX."""
import base64
import json
import pathlib
import urllib.error
import urllib.request

import pytest

from heating_plant import aas_builder as b
from heating_plant.aas_server import BASE_PATH, serve_in_thread

PORT = 48410
ENV = json.loads((pathlib.Path(__file__).resolve().parents[1] / "model/HeatingPlant.aas.json").read_text(encoding="utf-8"))


def _b64(s: str) -> str:
    """AAS Part 2: identifiers in paths are base64url-encoded without padding."""
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


@pytest.fixture(scope="module")
def api():
    srv = serve_in_thread(PORT)
    base = f"http://127.0.0.1:{PORT}{BASE_PATH}"

    def get(path):
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return json.loads(r.read())
    yield get, base
    srv.shutdown()


def test_shell_listed(api):
    get, _ = api
    shells = get("/shells")["result"]
    assert [s["id"] for s in shells] == [b.AAS_ID]
    assert {r["keys"][0]["value"] for r in shells[0]["submodels"]} == set(b.SM_IDS.values())


@pytest.mark.parametrize("name", ["Nameplate", "TechnicalData", "AssetInterfacesDescription", "TimeSeries"])
def test_submodel_by_id_equals_file(api, name):
    get, _ = api
    served = get(f"/submodels/{_b64(b.SM_IDS[name])}")
    in_file = next(s for s in ENV["submodels"] if s["id"] == b.SM_IDS[name])
    assert served["semanticId"] == in_file["semanticId"]
    assert [e["idShort"] for e in served["submodelElements"]] == [e["idShort"] for e in in_file["submodelElements"]]


def test_single_value_by_path(api):
    """One element addressed by its idShort path: what a data-space client would fetch.
    ($value serialization is answered with 501 by BaSyx 2.2.0, so the full element is read.)"""
    get, _ = api
    sm = _b64(b.SM_IDS["AssetInterfacesDescription"])
    e = get(f"/submodels/{sm}/submodel-elements/InterfaceOPCUA.EndpointMetadata.base")
    assert (e["modelType"], e["value"]) == ("Property", b.ENDPOINT)


def test_read_only(api):
    _, base = api
    req = urllib.request.Request(f"{base}/submodels/{_b64(b.SM_IDS['Nameplate'])}", method="DELETE")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert e.value.code == 405
