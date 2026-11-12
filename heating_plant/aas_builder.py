"""Build the Asset Administration Shell of the heating plant.

Every submodel element is COPIED from the official IDTA template JSON in standards/idta/
(semanticId, supplementalSemanticIds, description come from IDTA, not typed here) and then
filled with a value. Template-only qualifiers (SMT/Cardinality etc.) are removed.

Output: model/HeatingPlant.aas.json and model/HeatingPlant.aasx (AAS metamodel V3.0).
"""
import copy
import json
import pathlib

from heating_plant.model import Pid2Dof
from heating_plant.opcua_server import ENDPOINT, NAMESPACE, POINTS

ROOT = pathlib.Path(__file__).resolve().parents[1]
IDTA = ROOT / "standards/idta"
BASE = "https://github.com/sulemanali5760/heating-plant-aas"
AAS_ID = f"{BASE}/aas/HeatingPlant-0001"
ASSET_ID = f"{BASE}/asset/HeatingPlant-0001"
SM_IDS = {
    "Nameplate": f"{BASE}/sm/Nameplate",
    "TechnicalData": f"{BASE}/sm/TechnicalData",
    "AssetInterfacesDescription": f"{BASE}/sm/AssetInterfacesDescription",
}


def _load(name: str) -> dict:
    return json.loads((IDTA / name).read_text(encoding="utf-8-sig"))["submodels"][0]


NAMEPLATE = _load("IDTA_02006-3-0-3_DigitalNameplate.json")
CONTACT = _load("IDTA_02002-1-0-4_ContactInformation.json")
TECHDATA = _load("IDTA_02003-2-0-4_TechnicalData.json")
AID = _load("IDTA_02017-1-2_AssetInterfacesDescription.json")


def _children(e: dict) -> list:
    v = e.get("value")
    return v if isinstance(v, list) and v and isinstance(v[0], dict) and "modelType" in v[0] else []


def find(container: dict, *path: str) -> dict:
    """Template element by idShort path. A list child has no idShort: use '[]'."""
    elems = container.get("submodelElements") or _children(container)
    head, rest = path[0], path[1:]
    for e in elems:
        if (head == "[]" and "idShort" not in e) or e.get("idShort") == head:
            return find(e, *rest) if rest else e
    raise KeyError(f"{head} not in template")


def _clean(e: dict) -> dict:
    e = copy.deepcopy(e)
    # Qualifiers in IDTA templates describe the template (cardinality, allowed idShort, select/constraint
    # hints; some files tag them inconsistently), never the instance: drop all of them.
    e.pop("qualifiers", None)
    e.pop("embeddedDataSpecifications", None)
    if e["modelType"] in ("SubmodelElementCollection", "SubmodelElementList"):
        e["value"] = []
    return e


def prop(tpl: dict, value, id_short: str | None = None) -> dict:
    e = _clean(tpl)
    if id_short:
        e["idShort"] = id_short
    if e["modelType"] == "MultiLanguageProperty":
        e["value"] = [{"language": k, "text": v} for k, v in value.items()]
    elif e["modelType"] == "Range":
        e["min"], e["max"] = str(value[0]), str(value[1])
    else:
        e["value"] = str(value).lower() if isinstance(value, bool) else str(value)
    return e


def coll(tpl: dict, children: list, id_short: str | None = None) -> dict:
    e = _clean(tpl)
    if id_short:
        e["idShort"] = id_short
    elif "idShort" not in e and tpl.get("modelType") != "SubmodelElementList":
        pass
    e["value"] = children
    return e


def submodel(tpl: dict, sm_id: str, elements: list) -> dict:
    sm = {k: copy.deepcopy(tpl[k]) for k in ("idShort", "semanticId", "description") if k in tpl}
    sm.update({"id": sm_id, "kind": "Instance", "modelType": "Submodel", "submodelElements": elements})
    if "administration" in tpl:
        sm["administration"] = copy.deepcopy(tpl["administration"])
        sm["administration"].pop("templateId", None)
    return sm


# ---------------------------------------------------------------- Nameplate (IDTA 02006-3-0)
DESIGNATION = {"en": "Heating plant twin, weather-compensated flow temperature control TIC101 and "
                     "network pump differential pressure control PDIC301",
               "de": "Heizzentrale-Zwilling, witterungsgeführte Vorlauftemperaturregelung TIC101 und "
                     "Netzpumpen-Differenzdruckregelung PDIC301"}
MANUFACTURER = {"en": "Suleman Ali (portfolio project, not a commercial product)"}
ORDER_CODE = "not orderable (demonstrator)"


def nameplate() -> dict:
    t = NAMEPLATE
    addr = coll(find(t, "AddressInformation"), [
        prop(find(CONTACT, "ContactInformation", "CityTown"), {"de": "Magdeburg", "en": "Magdeburg"}),
        prop(find(CONTACT, "ContactInformation", "NationalCode"), {"en": "DE"}),
    ])
    return submodel(t, SM_IDS["Nameplate"], [
        prop(find(t, "URIOfTheProduct"), BASE),
        prop(find(t, "ManufacturerName"), MANUFACTURER),
        prop(find(t, "ManufacturerProductDesignation"), DESIGNATION),
        addr,
        prop(find(t, "OrderCodeOfManufacturer"), ORDER_CODE),
        prop(find(t, "SerialNumber"), "0001"),
        prop(find(t, "YearOfConstruction"), "2026"),
        prop(find(t, "SoftwareVersion"), "1.0.0"),
        prop(find(t, "CountryOfOrigin"), "DE"),
    ])


# ---------------------------------------------------------------- Technical Data (IDTA 02003-2-0)
# Own concept descriptions: no free, verifiable ECLASS/IEC CDD entries were used (see SOURCES.md).
TECH = [
    # section, idShort, valueType, value, unit, definition, source tag
    ("HeatingCurve", "RoomTemperatureSetpoint", "xs:double", 20.0, "°C", "Room temperature the heating curve is designed for", "[D] DB_Heizzentrale.tRaumSoll"),
    ("HeatingCurve", "Slope", "xs:double", 1.2, "-", "Heating-curve slope: flow temperature change per K of outdoor temperature", "[D] DB_Heizzentrale.steigung"),
    ("HeatingCurve", "Level", "xs:double", 0.0, "K", "Parallel shift of the heating curve", "[D] DB_Heizzentrale.niveau"),
    ("HeatingCurve", "FlowTemperatureMin", "xs:double", 25.0, "°C", "Lower limit of the flow temperature setpoint", "[D] DB_Heizzentrale.tVorlaufMin"),
    ("HeatingCurve", "FlowTemperatureMax", "xs:double", 75.0, "°C", "Upper limit of the flow temperature setpoint", "[D] DB_Heizzentrale.tVorlaufMax"),
    ("MixingCircuit", "BoilerTemperature", "xs:double", 75.0, "°C", "Hot-side temperature at the 3-way mixing valve", "[D] DB_Heizzentrale.tKessel"),
    ("MixingCircuit", "DeadTime", "xs:double", 10.0, "s", "Transport delay of the flow pipe", "[D] FB_StreckeVorlauf.iTotzeit"),
    ("MixingCircuit", "TimeConstant", "xs:double", 60.0, "s", "PT1 time constant of the circuit storage mass", "[D] FB_StreckeVorlauf.iZeitkonstante"),
    ("ControllerTIC101", "ProportionalGain", "xs:double", Pid2Dof.kp, "%/K", "PID_Compact gain after fine tuning", "[M] commissioning test 4"),
    ("ControllerTIC101", "IntegralTime", "xs:double", Pid2Dof.ti, "s", "PID_Compact integral time after fine tuning", "[M] commissioning test 4"),
    ("ControllerTIC101", "DerivativeTime", "xs:double", Pid2Dof.td, "s", "PID_Compact derivative time after fine tuning", "[M] commissioning test 4"),
    ("ControllerTIC101", "ProportionalWeighting", "xs:double", Pid2Dof.b, "-", "Setpoint weighting b of the P part", "[M] commissioning test 4"),
    ("ControllerTIC101", "SampleTime", "xs:double", Pid2Dof.ts, "s", "PID algorithm sample time", "[M] commissioning test 4"),
    ("NetworkPumpP301", "ShutOffHead", "xs:double", 2.2, "bar", "Pump differential pressure at zero flow and 100 % speed", "[D] FB_ModellNetz.iDpNull"),
    ("NetworkPumpP301", "ConsumerKvMax", "xs:double", 13.7, "m³/h", "Kv of all consumer valves fully open (flow at 1 bar)", "[D] FB_ModellNetz.iKvMax"),
    ("NetworkPumpP301", "DesignFlow", "xs:double", 15.0, "m³/h", "Flow at 100 % speed, all valves open", "[D] FB_ModellNetz design note"),
    ("ControllerPDIC301", "DifferentialPressureSetpoint", "xs:double", 1.0, "bar", "Setpoint of the network differential pressure", "[D] DB_Netz.dpSoll"),
    ("ControllerPDIC301", "ProportionalGain", "xs:double", 119.3, "%/bar", "PI gain after fine tuning (PI chosen over PID: noisy dp signal)", "[M] commissioning 2 Oct 2026"),
    ("ControllerPDIC301", "IntegralTime", "xs:double", 2.72, "s", "PI integral time after fine tuning", "[M] commissioning 2 Oct 2026"),
    ("NetworkPumpP301", "HeatingLimit", "xs:double", 15.0, "°C", "Pump released below this outdoor temperature (1 K hysteresis)", "[D] FB_Netzpumpe.iHeizgrenze"),
    ("NetworkPumpP301", "FrostLimit", "xs:double", 3.0, "°C", "Pump always runs below this outdoor temperature", "[D] FB_Netzpumpe.iFrostgrenze"),
    ("Controller", "CPU", "xs:string", "SIMATIC S7-1500 CPU 1511-1 PN (PLCSIM)", "", "PLC the control program is commissioned on", "[D] TIA project hardware configuration"),
    ("Controller", "CycleTime", "xs:double", 0.1, "s", "Cyclic interrupt OB30 period", "[D] OB30"),
]


def _cd_id(id_short: str) -> str:
    return f"{BASE}/cd/{id_short}"


# IRI as the IDTA test engine (aas-test-engines 1.0.3) expects it; published templates also use ".../DataSpecificationIEC61360/3/0"
IEC61360 = "https://admin-shell.io/DataSpecificationTemplates/DataSpecificationIec61360/3"


def _iec61360(name: str, definition: str, unit: str, data_type: str) -> list:
    """Data specification IEC 61360 (AAS Part 3a): name, definition, data type and unit as separate
    fields a program can read, instead of a unit hidden in a description text."""
    content = {"modelType": "DataSpecificationIec61360",
               "preferredName": [{"language": "en", "text": name}],
               "dataType": data_type,
               "definition": [{"language": "en", "text": definition}]}
    if unit and unit != "-":
        content["unit"] = unit
    elif data_type == "REAL_MEASURE":
        content["dataType"] = "REAL_COUNT"  # a number without unit; REAL_MEASURE must have one (AASc-3a-009)
    return [{"dataSpecification": {"type": "ExternalReference", "keys": [{"type": "GlobalReference", "value": IEC61360}]},
             "dataSpecificationContent": content}]


def concept_descriptions() -> list:
    return [{
        "id": _cd_id(f"{sec}/{i}"), "idShort": f"{sec}_{i}", "modelType": "ConceptDescription",
        "description": [{"language": "en", "text": f"{d}. Unit: {u or 'none'}. Source: {s}."}],
        "embeddedDataSpecifications": _iec61360(i, d, u, "STRING" if vt == "xs:string" else "REAL_MEASURE"),
    } for sec, i, vt, _, u, d, s in TECH]


def technical_data() -> dict:
    t = TECHDATA
    general = coll(find(t, "GeneralInformation"), [
        prop(find(t, "GeneralInformation", "ManufacturerName"), MANUFACTURER["en"]),
        prop(find(t, "GeneralInformation", "ManufacturerProductDesignation"), DESIGNATION),
        prop(find(t, "GeneralInformation", "ManufacturerArticleNumber"), "heating-plant-aas"),
        prop(find(t, "GeneralInformation", "ManufacturerOrderCode"), ORDER_CODE),
    ])
    area_tpl = find(t, "TechnicalPropertyAreas", "[]")
    section_tpl = find(t, "TechnicalPropertyAreas", "[]", "Section")
    arb_tpl = find(t, "TechnicalPropertyAreas", "[]", "Section", "ArbitraryProperty")
    sections = {}
    for sec, i, vt, v, u, d, s in TECH:
        p = prop(arb_tpl, v, id_short=i)
        p["valueType"] = vt
        p["semanticId"] = {"type": "ExternalReference",
                           "keys": [{"type": "GlobalReference", "value": _cd_id(f"{sec}/{i}")}]}
        p.pop("supplementalSemanticIds", None)
        p["description"] = [{"language": "en", "text": f"{d} [{u or '-'}]"}]
        sections.setdefault(sec, []).append(p)
    area = coll(area_tpl, [coll(section_tpl, props, id_short=sec) for sec, props in sections.items()])
    further = coll(find(t, "FurtherInformation"), [
        prop(find(t, "FurtherInformation", "TextStatement"),
             {"en": "Values from the PLCSIM commissioning of 25 Sep 2026 and the PLC source; every value is "
                    "traced in SOURCES.md of the repository."}),
        prop(find(t, "FurtherInformation", "ValidDate"), "2026-11-10"),
    ])
    areas = coll(find(t, "TechnicalPropertyAreas"), [area])
    return submodel(t, SM_IDS["TechnicalData"], [general, areas, further])


# ---------------------------------------------------------------- Asset Interfaces Description (IDTA 02017 1.2)
def browse_path(p, root: str = "HeatingPlant") -> str:
    """uav:browsePath (OPC 10101): absolute path from the Root node. Namespace index 2 is the index
    the running server assigns to NAMESPACE (checked in tests/test_aas.py)."""
    parts = [root] + ([p.parent] if p.parent else []) + [p.browse_name]
    return "/Root/Objects/" + "/".join(f"2:{x}" for x in parts)


def asset_interfaces(endpoint: str = ENDPOINT, namespace: str = NAMESPACE, points=POINTS, secure: bool = True,
                     sm_id: str | None = None, title: str = "Heating plant twin (TIC101, TI102, TI103, PDIC301)",
                     root: str = "HeatingPlant") -> dict:
    """AID submodel for ANY OPC UA server: endpoint, security and one entry per datapoint. The defaults
    describe the heating plant; another asset passes its own endpoint, namespace and points."""
    sm_id = sm_id or SM_IDS["AssetInterfacesDescription"]
    t = AID
    itf = find(t, "InterfaceTemplateForOPCUA")
    ep = find(t, "InterfaceTemplateForOPCUA", "EndpointMetadata")
    sd = find(ep, "securityDefinitions")
    chan_tpl, auth_tpl = find(sd, "opcua_channel_sc"), find(sd, "opcua_authentication_sc")
    # Values as defined in OPC 10101 (uav:securityMode / uav:securityPolicy / uav:userIdentityToken)
    channel = coll(chan_tpl, [prop(find(chan_tpl, "scheme"), "uav_channelsec"),
                              prop(find(chan_tpl, "uav_securityMode"), "SignAndEncrypt" if secure else "None"),
                              prop(find(chan_tpl, "uav_securityPolicy"), "Basic256Sha256" if secure else "None")])
    auth = coll(auth_tpl, [prop(find(auth_tpl, "scheme"), "uav_authentication"),
                           prop(find(auth_tpl, "uav_userIdentityToken"), "UserName" if secure else "Anonymous")])

    def sec_ref(name: str) -> dict:
        path = [("Submodel", sm_id), ("SubmodelElementCollection", "InterfaceOPCUA"),
                ("SubmodelElementCollection", "EndpointMetadata"),
                ("SubmodelElementCollection", "securityDefinitions"), ("SubmodelElementCollection", name)]
        r = _clean(find(ep, "security", "[]"))
        r["value"] = {"type": "ModelReference", "keys": [{"type": k, "value": v} for k, v in path]}
        return r

    endpoint = coll(ep, [
        prop(find(ep, "base"), endpoint),
        prop(find(ep, "contentType"), "application/octet-stream"),
        coll(find(ep, "security"), [sec_ref("opcua_channel_sc"), sec_ref("opcua_authentication_sc")]),  # both apply
        coll(sd, [channel, auth]),
    ])
    pn = find(itf, "InteractionMetadata", "properties", "property_name")
    forms_tpl = find(pn, "forms")
    props = []
    for p in points:
        forms = coll(forms_tpl, [
            prop(find(forms_tpl, "href"), f"nsu={namespace};s={p.node_id}"),
            prop(find(forms_tpl, "uav_browsePath"), browse_path(p, root)),
        ])
        children = [prop(find(pn, "key"), p.node_id), prop(find(pn, "type"), p.kind),
                    prop(find(pn, "title"), p.description), prop(find(pn, "observable"), True)]
        if p.kind in ("number", "integer"):  # unit and range only make sense for numbers
            mm = prop(find(pn, "min_max"), (float(p.low), float(p.high)))
            mm["valueType"] = "xs:float"
            children += [prop(find(pn, "unit"), p.unit), mm]
        props.append(coll(pn, children + [forms], id_short=p.node_id.replace(".", "_")))
    interaction = coll(find(itf, "InteractionMetadata"),
                       [coll(find(itf, "InteractionMetadata", "properties"), props)])
    interface = coll(itf, [
        prop(find(itf, "title"), title),
        endpoint,
        interaction,
    ], id_short="InterfaceOPCUA")
    return submodel(t, sm_id, [interface])


# ---------------------------------------------------------------- Time Series Data (IDTA 02008-1-1)
TIMESERIES = _load("IDTA_02008-1-1-3_TimeSeriesData.json")
SM_IDS["TimeSeries"] = f"{BASE}/sm/TimeSeries"
WEEK_CSV = ROOT / "docs/week_magdeburg_2022-12.csv"
UTC_TIME = "https://admin-shell.io/idta/TimeSeries/UtcTime/1/1"  # IDTA 02008-1-1, table 4
# Record variables: CSV column -> (idShort, definition, unit, origin)
TS_VARS = [
    ("t_outdoor", "TI103_PV", "Outdoor temperature", "°C", "measured, DWD station 03126 Magdeburg"),
    ("t_flow_set", "TIC101_SP", "Flow temperature setpoint", "°C", "twin"),
    ("t_flow", "TIC101_PV", "Flow temperature", "°C", "twin"),
    ("t_return", "TI102_PV", "Return temperature", "°C", "twin"),
    ("valve_pct", "TIC101_OP", "Mixing valve position", "%", "twin"),
]


def _ts_var(tpl: dict, id_short: str, value) -> dict:
    p = prop(tpl, value, id_short=id_short)
    p["valueType"] = "xs:double"
    p["semanticId"] = {"type": "ExternalReference", "keys": [{"type": "GlobalReference", "value": _cd_id(id_short)}]}
    p.pop("description", None)
    return p


def _ts_time(tpl: dict, value: str) -> dict:
    p = prop(tpl, value)
    p["valueType"] = "xs:dateTime"
    p["semanticId"] = {"type": "ExternalReference", "keys": [{"type": "GlobalReference", "value": UTC_TIME}]}
    p.pop("description", None)
    return p


def time_series() -> dict:
    import csv
    t = TIMESERIES
    rec_tpl = find(t, "Metadata", "Record")
    time_tpl = find(rec_tpl, "Time")
    var_tpl = find(rec_tpl, "sampleAccelerationX")
    with WEEK_CSV.open() as f:
        rows = [r for r in csv.DictReader(f) if r["time_utc"][14:16] == "59"]  # last minute of each hour
    metadata = coll(find(t, "Metadata"), [
        prop(find(t, "Metadata", "Name"), {"en": "Heating plant twin, Magdeburg week 12-18 Dec 2022"}),
        prop(find(t, "Metadata", "Description"),
             {"en": "Hourly values. TI103_PV is measured (Deutscher Wetterdienst, CC BY 4.0); all other "
                    "values are the twin's response, recorded at minute 59 of each hour."}),
        coll(rec_tpl, [_ts_time(time_tpl, "")] + [_ts_var(var_tpl, i, "") for _, i, _, _, _ in TS_VARS]),
    ])
    for e in metadata["value"][2]["value"]:
        e.pop("value", None)  # metadata record lists the variables, without values
    seg_tpl = find(t, "Segments", "InternalSegment")
    rec_list = [coll(find(seg_tpl, "Records", "Record"),
                     [_ts_time(time_tpl, r["time_utc"] + "Z")] +
                     [_ts_var(var_tpl, i, round(float(r[c]), 3)) for c, i, _, _, _ in TS_VARS],
                     id_short=f"Record{n:03d}") for n, r in enumerate(rows, 1)]
    segment = coll(seg_tpl, [
        prop(find(seg_tpl, "Name"), {"en": "Coldest week 2022, Magdeburg"}),
        prop(find(seg_tpl, "RecordCount"), len(rows)),
        prop(find(seg_tpl, "StartTime"), rows[0]["time_utc"] + "Z"),
        prop(find(seg_tpl, "EndTime"), rows[-1]["time_utc"] + "Z"),
        prop(find(seg_tpl, "SamplingInterval"), 3600),
        prop(find(seg_tpl, "State"), "completed"),  # enumeration value, IDTA 02008-1-1
        coll(find(seg_tpl, "Records"), rec_list),
    ], id_short="InternalSegmentMagdeburgWeek")
    return submodel(t, SM_IDS["TimeSeries"], [metadata, coll(find(t, "Segments"), [segment])])


def ts_concept_descriptions() -> list:
    return [{"id": _cd_id(i), "idShort": i, "modelType": "ConceptDescription",
             "description": [{"language": "en", "text": f"{d}. Unit: {u}. Origin: {o}."}],
             "embeddedDataSpecifications": _iec61360(i, f"{d} ({o})", u, "REAL_MEASURE")}
            for _, i, d, u, o in TS_VARS]


# ---------------------------------------------------------------- environment
def shell(aas_id: str, asset_id: str, id_short: str, sms: list) -> dict:
    return {
        "id": aas_id, "idShort": id_short, "modelType": "AssetAdministrationShell",
        "assetInformation": {"assetKind": "Instance", "globalAssetId": asset_id},
        "submodels": [{"type": "ModelReference", "keys": [{"type": "Submodel", "value": s["id"]}]} for s in sms],
    }


def environment(endpoint: str = ENDPOINT) -> dict:
    sms = [nameplate(), technical_data(), asset_interfaces(endpoint), time_series()]
    return {"assetAdministrationShells": [shell(AAS_ID, ASSET_ID, "HeatingPlant", sms)], "submodels": sms,
            "conceptDescriptions": concept_descriptions() + ts_concept_descriptions()}


def write_env(env: dict, out_dir: pathlib.Path, name: str) -> tuple[pathlib.Path, pathlib.Path]:
    """Write any AAS environment as <name>.aas.json and <name>.aasx."""
    from basyx.aas.adapter import aasx
    from basyx.aas.adapter.json import read_aas_json_file

    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / f"{name}.aas.json"
    # LF on every OS, so CI can check the committed file is up to date byte for byte
    js.write_text(json.dumps(env, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    with js.open(encoding="utf-8") as f:
        store = read_aas_json_file(f, failsafe=False)  # strict: raises on any metamodel violation
    ax = out_dir / f"{name}.aasx"
    with aasx.AASXWriter(str(ax)) as w:
        w.write_aas(env["assetAdministrationShells"][0]["id"], store, aasx.DictSupplementaryFileContainer(),
                    write_json=True)
    return js, ax


def write(out_dir: pathlib.Path = ROOT / "model", endpoint: str = ENDPOINT) -> tuple[pathlib.Path, pathlib.Path]:
    return write_env(environment(endpoint), out_dir, "HeatingPlant")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Write the plant's AAS (JSON + AASX)")
    ap.add_argument("--endpoint", default=ENDPOINT,
                    help="OPC UA endpoint the AAS should point at, e.g. opc.tcp://192.168.0.10:4840/heating-plant/")
    ap.add_argument("--out", type=pathlib.Path, default=ROOT / "model",
                    help="output folder (use another one than model/ for a site-specific endpoint)")
    a = ap.parse_args()
    for p in write(a.out, a.endpoint):
        print("wrote", p)
