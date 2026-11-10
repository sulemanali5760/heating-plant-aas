# Sources

Every number, device, formula and data file in this repository is listed here with its origin.

Tags: **[M]** measured · **[S]** standard, datasheet or official dataset · **[D]** design decision from the PLC project · **[A]** assumption (reason given)

`heizzentrale-tia` = the author's TIA Portal V19 project (S7-1500 CPU 1511-1 PN in PLCSIM) that this twin is ported from. Its commissioning log is `docs/Inbetriebnahmeprotokoll.md` (25 Sep 2026, Phase 1; 2 Oct 2026, Phase 5).

## Plant model (`heating_plant/model.py`)

| Item | Value | Tag | Source |
|---|---|---|---|
| Heating curve formula | T_set = T_room + level + slope·(T_room − T_out), limited | [D] | `heizzentrale-tia/src/Heizzentrale_Kern.scl`, `FC_Heizkurve`. Linear form; real controllers use a curved line (radiator exponent ≈ 1.3). Kept linear to match the PLC 1:1. |
| Room setpoint, slope, level | 20 °C, 1.2, 0 K | [D] | `DB_Heizzentrale` start values |
| Flow limits | 25 … 75 °C | [D] | `DB_Heizzentrale.tVorlaufMin/Max` |
| Boiler (hot-side) temperature | 75 °C | [D] | `DB_Heizzentrale.tKessel` |
| Mixing valve | T_mix = T_ret + v·(T_boiler − T_ret) | [D] | `FB_StreckeVorlauf`. Ideal 3-way mixing by energy balance (equal heat capacity of both streams). |
| Spread (load) | 0.4·(20 − T_out), limited 2 … 20 K | [D] | `FB_StreckeVorlauf`. Simple load model: heat demand rises linearly as the outdoor temperature falls. |
| Dead time | 10 s | [D] | `FB_StreckeVorlauf.iTotzeit`, pipe transport delay |
| PT1 time constant | 60 s | [D] | `FB_StreckeVorlauf.iZeitkonstante`, storage mass of the circuit |
| Cycle | 100 ms | [D] | OB30 cyclic interrupt in the PLC project |
| PID Gain, Ti, Td | 20.66, 20.17 s, 5.11 s | [M] | Fine tuning result, log test 4 |
| PID b, c, D-filter, sample time | 0.257, 0, 0.1, 0.4 s | [M] | Fine tuning result, log test 4 |
| PID structure | y = Kp·[(b·w − x) + (w − x)/(Ti·s) + Td·s/(a·Td·s + 1)·(c·w − x)] | [S] | Siemens, "SIMATIC S7-1200/S7-1500 PID control" function manual, PID_Compact algorithm (2-DOF PIDT1 with anti-windup). The twin uses this documented structure; Siemens-internal details (e.g. exact anti-windup) are not public, so dynamic tests use tolerances. |

## Validation targets (`tests/test_model.py`)

| Check | PLCSIM value | Tag | Log test |
|---|---|---|---|
| Setpoint at 0 °C / −12 °C | 44.0 / 58.4 °C | [M] | 0, 0b |
| Setpoint limit | 50.0 °C | [M] | 6 |
| Steady valve at 0 / −12 / −10 °C, limit 50 | 20.48 / 43.52 / 38.65 / 33.82 % | [M] | 0, 3, 5, 6 |
| Step 44 → 56 °C | dead time 10 s, 56 °C after 60 s, max 56.71 °C at 72 s | [M] | 5 (trace, 1 s resolution) |
| Manual 44 %, −12 °C | 58.67 °C, return = flow − 12.80 K | [M] | 1c, 1d |

## Outdoor temperature data (`data/`)

| Item | Value | Tag | Source |
|---|---|---|---|
| Dataset | Hourly air temperature TT_TU, station 03126 Magdeburg (52.1029 N, 11.5827 E, 79 m), 1955-01-01 … 2025-12-31 | [S] | Deutscher Wetterdienst, Climate Data Center: https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/hourly/air_temperature/historical/ |
| Raw file | `data/raw/stundenwerte_TU_03126_19550101_20251231_hist.zip`, SHA-256 `f6bde2f112be985be318b91c5ac8c58993e1c56ffbef06747bf46fac6f04f807`, downloaded 10 Nov 2026 | [S] | as above |
| Sensor | PT 100 (Luft), electronic, since 2006-04-06 | [S] | `Metadaten_Geraete_Lufttemperatur_03126.txt` inside the zip |
| Licence | CC BY 4.0, "Datenbasis: Deutscher Wetterdienst" | [S] | https://opendata.dwd.de/climate_environment/CDC/Terms_of_use.pdf (status May 2024) |
| Week used | 12–18 Dec 2022 (UTC), 168 h, min −11.4 °C, max −0.9 °C, mean −5.9 °C, no gaps, quality level QN 3 | [S] | Coldest 7-day mean of the winters 2021/22 … 2024/25 at this station (selection in `scripts/prepare_dwd.py`) |
| Intake check | zip contains only .txt/.html (13 files), no executables; read as text only | — | quarantine check before copying into the repo |

## OPC UA address space (`heating_plant/opcua_server.py`, `model/HeatingPlant.NodeSet2.xml`)

| Item | Tag | Source |
|---|---|---|
| ObjectTypes with ModellingRule Mandatory (i=78) | [S] | OPC 10000-3 Address Space Model, 6.4.4 |
| AnalogItemType (i=2368), EURange, EngineeringUnits | [S] | OPC 10000-8 UA Part 8: Data Access, 5.3.2 and 5.6 |
| Units CEL (°C) = 4408652, P1 (%) = 20529 | [S] | UNECE Recommendation 20 common codes; unitId encoding OPC 10000-8, 5.6.3; namespace `http://www.opcfoundation.org/UA/units/un/cefact` |
| Signal names TIC101, TI102, TI103, TV101 | [D] | `heizzentrale-tia/docs/EMSR_Stellenliste.csv` (tags per IEC 62424 style) |
| Ranges 0…120 °C, −30…+50 °C, 0…100 % | [D] | EMSR list, column "Messbereich" |
| href format `nsu=<namespace>;s=<id>` | [S] | OPC 10101 OPC UA for WoT Binding, 6.1 (reference.opcfoundation.org/specs/OPC-10101/6.1) |
| Security "None", localhost only | [A] | Demo on one PC. A plant network needs Sign&Encrypt (Basic256Sha256) and user tokens. |

## Software (versions pinned in `requirements.txt`, checked 10 Nov 2026)

| Package | Version | Licence | Why |
|---|---|---|---|
| asyncua | 2.1.0 | LGPL-3.0+ | OPC UA server and client (FreeOpcUa) |
| basyx-python-sdk | 2.2.0 | MIT | AAS metamodel V3, AASX/JSON (Eclipse BaSyx) |
| aas-test-engines | 1.0.3 | Apache-2.0 (github.com/admin-shell-io/aas-test-engines) | Official IDTA compliance checker for AAS files |
| pip-audit | 2.10.1 | Apache-2.0 | Known-vulnerability scan of the dependency tree: no findings on 10 Nov 2026 |

## Asset Administration Shell (`heating_plant/aas_builder.py`, `model/`)

| Item | Tag | Source |
|---|---|---|
| Metamodel | [S] | AAS Specification Part 1, metamodel V3.0 (IDTA 01001-3-0); files checked with `aas-test-engines` 1.0.3 |
| Digital Nameplate template | [S] | IDTA 02006-3-0, v3.0.3, `standards/idta/IDTA_02006-3-0-3_DigitalNameplate.json`, git blob `d2f95ca8…` = published file |
| Contact/Address drop-in | [S] | IDTA 02002-1-0, v1.0.4, `standards/idta/IDTA_02002-1-0-4_ContactInformation.json`, git blob `06f1abf6…` |
| Technical Data template | [S] | IDTA 02003-2-0, v2.0.4, `standards/idta/IDTA_02003-2-0-4_TechnicalData.json`, git blob `61364f12…` |
| Asset Interfaces Description template | [S] | IDTA 02017, v1.2 (May 2026), `standards/idta/IDTA_02017-1-2_AssetInterfacesDescription.json`, git blob `2b68a486…`; spec PDF `IDTA 02017_Submodel_Asset Interfaces Description.pdf`, tables 28 and 66 (OPC UA binding) |
| Template licence | [S] | CC BY 4.0, github.com/admin-shell-io/submodel-templates |
| AID property `type` = `number`, `unit` = UNECE code | [S] | IDTA 02017 1.2, section 2.9 (type: object, array, string, number, integer, boolean, null; unit: schema.org unitCode) |
| Manufacturer, address | [D] | Author's name and city only (Magdeburg, DE). No street address on purpose (privacy); the template marks street as optional. |
| Order code "not orderable (demonstrator)" | [A] | Mandatory in the nameplate, but the plant is not a product. Stated openly instead of inventing a number. |
| Technical-data concept descriptions (`…/cd/<name>`) | [A] | ECLASS/IEC CDD would be the industry choice; no matching entries could be verified free of charge, so each property gets its own concept description with definition, unit and source. |
| Endpoint `opc.tcp://127.0.0.1:4840/heating-plant/` | [D] | Local demo endpoint; 4840 is the IANA-registered OPC UA port |

## Plots (`scripts/week_plot.py`, `docs/`)

| Item | Tag | Source |
|---|---|---|
| Outdoor temperature held constant within each hour | [D] | DWD gives one value per hour; no interpolation is invented |
| matplotlib 3.11.2 | [S] | Matplotlib licence (PSF-based); `pip-audit` clean on 10 Nov 2026 |
