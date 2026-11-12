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
    namespace: str            # namespace URI ("" when the href gives an index instead)
    identifier: str | int
    kind: str = "number"      # AID type: number, integer, boolean, string
    ns_index: int | None = None


def parse_href(href: str) -> tuple[str, int | None, str | int]:
    """OPC UA NodeId as written in an AID form: 'nsu=<namespace URI>;s=<string>' or ';i=<number>',
    or with a namespace index 'ns=<n>;s=...'. Without a namespace part the id is in namespace 0."""
    head, sep, ident = href.rpartition(";")
    kind, _, value = ident.partition("=")
    if kind not in ("s", "i") or (sep and not head.startswith(("nsu=", "ns="))):
        raise ValueError(f"unsupported OPC UA href in AID: {href}")
    node = int(value) if kind == "i" else value
    if head.startswith("nsu="):
        return head[4:], None, node
    return "", int(head[3:]) if head else 0, node


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
        nsu, ns_index, ident = parse_href(_val(p.get_referable("forms"), "href"))
        have = {e.id_short for e in p.value}
        points.append(Datapoint(_val(p, "key"), _val(p, "title") if "title" in have else _val(p, "key"),
                                _val(p, "unit") if "unit" in have else "", nsu, ident,
                                _val(p, "type") if "type" in have else "number", ns_index))
    return base, points, read_security(itf)


async def node_of(c: Client, p: Datapoint):
    idx = p.ns_index if p.ns_index is not None else await c.get_namespace_index(p.namespace)
    return c.get_node(ua.NodeId(p.identifier, idx))


async def _apply_security(c: Client, sec: Security, cert, key, password, server_cert=None,
                          username: str = USERNAME, app_uri: str = CLIENT_URI) -> None:
    """Configure the client exactly as the AID demands; refuse to guess."""
    if sec.mode and sec.mode != "None":
        policy = getattr(security_policies, f"SecurityPolicy{sec.policy}", None)
        if policy is None:
            raise ValueError(f"AID asks for unsupported security policy {sec.policy}")
        if cert is None or key is None:
            raise ValueError("AID requires a secure channel: client certificate and key needed")
        if server_cert is None or not pathlib.Path(server_cert).exists():
            raise ValueError("AID requires a secure channel: the server's certificate is needed, so the "
                             "client can check who it talks to before it sends the password")
        c.application_uri = app_uri
        # Pinned server certificate: the channel is encrypted for this certificate only, and asyncua
        # refuses the session if the server presents a different one.
        await c.set_security(policy, str(cert), str(key), server_certificate=str(server_cert),
                             mode=ua.MessageSecurityMode[sec.mode])
    if sec.user_token == "UserName":
        if password is None:
            raise ValueError("AID requires UserName login: password needed")
        c.set_user(username)
        c.set_password(password)


class _Recorder:
    def __init__(self, names):
        self.latest = dict.fromkeys(names)

    def datachange_notification(self, node, val, data):
        self.latest[self.by_node[node.nodeid]] = val


async def watch(aasx_path: pathlib.Path, seconds: float, out_csv: pathlib.Path | None = None,
                endpoint_override: str | None = None, period: float = 0.5,
                cert=None, key=None, password: str | None = None, server_cert=None) -> list[dict]:
    base, points, sec = read_interface_with_security(aasx_path)
    endpoint = endpoint_override or base
    print(f"AAS says: OPC UA endpoint {base}, {len(points)} datapoints, "
          f"security {sec.policy or 'None'} {sec.mode or ''}, login {sec.user_token or 'Anonymous'}")
    rows = []
    client = Client(endpoint)
    await _apply_security(client, sec, cert, key, password, server_cert)
    async with client as c:
        rec = _Recorder([p.name for p in points])
        rec.by_node = {}
        nodes = []
        for p in points:
            n = await node_of(c, p)
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
    await watch(path, seconds, cert=cert, key=key, password=security.password_from_env(),
                server_cert=security.PKI / "server_cert.der")


if __name__ == "__main__":
    # First run creates pki/client_cert.der; copy it into pki/trusted/ (security.trust) before the server starts.
    # The server creates pki/server_cert.der on its first start; the client only talks to that certificate.
    path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "model/HeatingPlant.aasx")
    asyncio.run(_main(path, float(sys.argv[2]) if len(sys.argv) > 2 else 10))
