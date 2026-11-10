# heating-plant-aas

A weather-compensated heating plant, commissioned on a Siemens S7-1500 (PLCSIM), turned into an **Industrie 4.0 component**: a Python twin of the same control loop, an **OPC UA server** with a typed information model, and an **Asset Administration Shell (AAS)** built from official IDTA templates. A client that knows nothing about the plant reads the AAS, finds the OPC UA endpoint and datapoints in it, and connects. No hand-written tag list.

![Twin on the real coldest week of 2022 in Magdeburg](docs/week_magdeburg_2022-12.png)

*The twin running through 12–18 Dec 2022 with the real hourly outdoor temperature of DWD station Magdeburg. The heating curve sets the flow temperature, the PID moves the mixing valve, and flow follows setpoint.*

## What is in it

```
 DWD hourly outdoor temperature (real data)
            │
            ▼
 ┌──────────────────────────────┐        OPC UA (asyncua)                   AAS (.aasx, IDTA templates)
 │ Plant twin, 100 ms cycle     │  ──►  HeatingPlant                         ├─ Digital Nameplate 3.0
 │ heating curve → PID → valve  │        ├─ TIC101  SP · PV · OP            ├─ Technical Data 2.0
 │ → dead time → PT1            │        ├─ TI102   PV                      └─ Asset Interfaces Description 1.2
 │ (1:1 port of the SCL blocks) │        └─ TI103   PV  (writable)                 endpoint + href per datapoint
 └──────────────────────────────┘        AnalogItemType, EURange, UNECE units        │
                                                    ▲                                │
                                                    └──────── AID client ◄───────────┘
                                                      reads only the AASX, subscribes
```

| Part | File | Standard |
|---|---|---|
| Plant twin | `heating_plant/model.py` | Port of `FC_Heizkurve` and `FB_StreckeVorlauf` from the PLC project; 2-DOF PIDT1 as documented for PID_Compact |
| OPC UA server | `heating_plant/opcua_server.py`, `model/HeatingPlant.NodeSet2.xml` | OPC 10000-8 Data Access (AnalogItemType, EURange, EngineeringUnits), UNECE Rec. 20 units |
| AAS | `heating_plant/aas_builder.py`, `model/HeatingPlant.aasx` | AAS metamodel V3.0; IDTA 02006-3-0 Digital Nameplate, IDTA 02003-2-0 Technical Data, IDTA 02017 1.2 Asset Interfaces Description |
| AID client | `heating_plant/aid_client.py` | AID OPC UA binding: `href = nsu=…;s=…` and `uav:browsePath` (OPC 10101 WoT binding) |

## How it is validated

| Check | Against | Result |
|---|---|---|
| Heating curve, 3 operating points | PLCSIM commissioning log (tests 0, 0b, 6) | exact |
| Steady valve position, 4 operating points | PLCSIM: 20.48 / 43.52 / 38.65 / 33.82 % | within 0.06 % |
| Setpoint step 44 → 56 °C | PLCSIM trace: dead time 10 s, 56 °C after 60 s, peak 56.71 °C at 72 s | 10.4 s, 63 s, 56.70 °C at 73.5 s |
| OPC UA model | OPC 10000-8: type, data type, EURange, unit namespace | automated client test |
| AAS files | IDTA `aas-test-engines` (metamodel, constraints, AASX package) | pass |
| AAS submodels | Element by element against the official IDTA template JSON (mandatory elements, semantic IDs) | pass |
| AID | Every `href` and browse path resolves to the same node on the live server | pass |

The PLCSIM values come from the author's TIA Portal V19 project (commissioned 25 Sep 2026). Every number in this repository is traced in [SOURCES.md](SOURCES.md) as measured, standard/datasheet, design decision, or assumption.

## Run it

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # Linux/macOS: .venv/bin/pip
python -m heating_plant.opcua_server --outdoor dwd --time-lapse 600    # replays the Magdeburg week, 1 h = 6 s
python -m heating_plant.aid_client model/HeatingPlant.aasx 30           # second terminal: connects using only the AAS
python -m pytest                                                        # all checks
```

`--outdoor manual` makes `TI103.PV` writable from any OPC UA client (e.g. UaExpert), so you can change the weather by hand and watch the loop react.

## Limits (on purpose)

- **Security:** the server runs `None` security on localhost for the demo. A plant network needs Sign & Encrypt and user authentication.
- **PID_Compact** is reproduced from its documented structure. Siemens-internal details are not public, so the dynamic test uses tolerances.
- **Technical Data properties** use concept descriptions defined in this repo, because no ECLASS/IEC CDD properties could be verified free of charge for these quantities.
- **One loop:** the network-pump Δp loop (PDIC301) of the PLC project is not in the twin yet.
- **Not a product:** the nameplate says so. It describes a demonstrator, not something you can order.

## Kurz auf Deutsch

Eine Heizzentrale mit witterungsgeführter Vorlauftemperaturregelung (S7-1500, PLCSIM) als Industrie-4.0-Komponente: Python-Zwilling derselben Regelstrecke, OPC-UA-Server mit typisiertem Informationsmodell und eine Verwaltungsschale aus offiziellen IDTA-Vorlagen. Ein Client liest nur die Verwaltungsschale (Asset Interfaces Description) und verbindet sich damit selbstständig. Der Zwilling ist gegen die PLCSIM-Inbetriebnahme geprüft und läuft mit echten DWD-Außentemperaturen aus Magdeburg.

## Licence

Code: MIT. Weather data: Deutscher Wetterdienst, CC BY 4.0. IDTA submodel templates in `standards/idta/`: CC BY 4.0, unchanged copies from github.com/admin-shell-io/submodel-templates.
