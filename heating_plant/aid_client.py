"""Connect to the plant using ONLY its Asset Administration Shell.

The client knows nothing about the plant: it opens the AASX, finds the Asset Interfaces
Description submodel (IDTA 02017) by its semantic ID, takes the OPC UA endpoint and the
datapoints (href = nsu=...;s=..., unit, title) from it, then subscribes to every datapoint.
This is the "plug and produce" use of the AAS: no hand-written tag list.
"""
import asyncio
import csv
import pathlib
import sys
from dataclasses import dataclass

from asyncua import Client, ua
from basyx.aas import model
from asyncua.crypto import security_policies
from basyx.aas.adapter import aasx

from heating_plant.security import CLIENT_URI, USERNAME

AID_SEMANTIC_ID = "https://admin-shell.io/idta/AssetInterfacesDescription/1/2/Submodel"
OPCUA_BINDING = "http://opcfoundation.org/UA/WoT-Binding/"


@dataclass
class Datapoint:
    name: str
    title: str
    unit: str
    namespace: str
    identifier: str


def _sem(e) -> str | None:
    return e.semantic_id.key[0].value if e.semantic_id else None


def _val(coll, id_short):
    return coll.get_referable(id_short).value


@dataclass
class Security:
    """From the AID securityDefinitions (OPC 10101 terms). Empty strings = no such requirement."""
    mode: str = ""          # uav_securityMode, e.g. SignAndEncrypt
    policy: str = ""        # uav_securityPolicy, e.g. Basic256Sha256
    user_token: str = ""    # uav_userIdentityToken, e.g. UserName


def read_security(itf) -> Security:
    sec = Security()
    for d in itf.get_referable("EndpointMetadata").get_referable("securityDefinitions").value:
        scheme = _val(d, "scheme")
        if scheme == "uav_channelsec":
            sec.mode, sec.policy = _val(d, "uav_securityMode"), _val(d, "uav_securityPolicy")
        elif scheme == "uav_authentication":
            sec.user_token = _val(d, "uav_userIdentityToken")
    return sec


def read_interface(aasx_path: pathlib.Path) -> tuple[str, list[Datapoint]]:
    base, points, _ = read_interface_with_security(aasx_path)
    return base, points


def read_interface_with_security(aasx_path: pathlib.Path) -> tuple[str, list[Datapoint], Security]:
    store, files = model.DictIdentifiableStore(), aasx.DictSupplementaryFileContainer()
    with aasx.AASXReader(str(aasx_path)) as r:
        r.read_into(store, files)
    sm = next(o for o in store if isinstance(o, model.Submodel) and _sem(o) == AID_SEMANTIC_ID)
    itf = next(e for e in sm.submodel_element
               if any(s.key[0].value == OPCUA_BINDING for s in e.supplemental_semantic_id))
    base = _val(itf.get_referable("EndpointMetadata"), "base")
    points = []
    for p in itf.get_referable("InteractionMetadata").get_referable("properties").value:
        href = _val(p.get_referable("forms"), "href")
        nsu, sid = href.split(";s=", 1)
        points.append(Datapoint(_val(p, "key"), _val(p, "title"), _val(p, "unit"), nsu.removeprefix("nsu="), sid))
    return base, points, read_security(itf)


async def _apply_security(c: Client, sec: Security, cert, key, password) -> None:
    """Configure the client exactly as the AID demands; refuse to guess."""
    if sec.mode and sec.mode != "None":
        policy = getattr(security_policies, f"SecurityPolicy{sec.policy}", None)
        if policy is None:
            raise ValueError(f"AID asks for unsupported security policy {sec.policy}")
        if cert is None or key is None:
            raise ValueError("AID requires a secure channel: client certificate and key needed")
        c.application_uri = CLIENT_URI
        await c.set_security(policy, str(cert), str(key), mode=ua.MessageSecurityMode[sec.mode])
    if sec.user_token == "UserName":
        if password is None:
            raise ValueError("AID requires UserName login: password needed")
        c.set_user(USERNAME)
        c.set_password(password)


class _Recorder:
    def __init__(self, names):
        self.latest = dict.fromkeys(names)

    def datachange_notification(self, node, val, data):
        self.latest[self.by_node[node.nodeid]] = val


async def watch(aasx_path: pathlib.Path, seconds: float, out_csv: pathlib.Path | None = None,
                endpoint_override: str | None = None, period: float = 0.5,
                cert=None, key=None, password: str | None = None) -> list[dict]:
    base, points, sec = read_interface_with_security(aasx_path)
    endpoint = endpoint_override or base
    print(f"AAS says: OPC UA endpoint {base}, {len(points)} datapoints, "
          f"security {sec.policy or 'None'} {sec.mode or ''}, login {sec.user_token or 'Anonymous'}")
    rows = []
    client = Client(endpoint)
    await _apply_security(client, sec, cert, key, password)
    async with client as c:
        rec = _Recorder([p.name for p in points])
        rec.by_node = {}
        nodes = []
        for p in points:
            idx = await c.get_namespace_index(p.namespace)
            n = c.get_node(ua.NodeId(p.identifier, idx))
            rec.by_node[n.nodeid] = p.name
            nodes.append(n)
        sub = await c.create_subscription(100, rec)
        await sub.subscribe_data_change(nodes)
        header = "  ".join(f"{p.name} [{p.unit}]".rjust(16) for p in points)
        print(header)
        t = 0.0
        while t < seconds:
            await asyncio.sleep(period)
            t += period
            if None in rec.latest.values():
                continue
            rows.append({"t_s": round(t, 2), **rec.latest})
            print("  ".join(f"{rec.latest[p.name]:16.2f}" for p in points))
        await sub.delete()
    if out_csv:
        with out_csv.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return rows


async def _main(path: pathlib.Path, seconds: float) -> None:
    from heating_plant import security
    cert, key = await security.ensure_certificate("client", security.CLIENT_URI, server=False)
    await watch(path, seconds, cert=cert, key=key, password=security.password_from_env())


if __name__ == "__main__":
    # First run creates pki/client_cert.der; copy it into pki/trusted/ (security.trust) before the server starts.
    path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "model/HeatingPlant.aasx")
    asyncio.run(_main(path, float(sys.argv[2]) if len(sys.argv) > 2 else 10))
