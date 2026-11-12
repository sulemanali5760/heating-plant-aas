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

## Pump loop PDIC301 (`heating_plant/model.py`: PumpRelease, HeatingNetwork, PumpLoop)

| Item | Value | Tag | Source |
|---|---|---|---|
| Network model | pump dp = dp0·(n/100)² − k_pump·Q² (affinity law), pipes k_pipe·Q², consumers Q²/Kv², Kv = Kv_max·load | [D] | `heizzentrale-tia/src/Heizzentrale_Netz.scl`, `FB_ModellNetz` |
| dp0, k_pump, k_pipe, Kv_max | 2.2 bar, 0.00311, 0.00133 bar/(m³/h)², 13.7 m³/h | [D] | `FB_ModellNetz` inputs; design point 100 % speed, all valves open: 15 m³/h at 1.2 bar |
| Drive ramp, hydraulics | PT1 2.0 s, 1.0 s | [D] | `FB_ModellNetz.iTDrehzahl`, `iTDruck` |
| Release logic | heating limit 15 °C, frost limit 3 °C, hysteresis 1 K, emergency switch and drive fault block the pump | [D] | `FB_Netzpumpe` |
| Controller | PI, Gain 119.3 %/bar, Ti 2.72 s, b 0.8, sample 0.1 s, dp setpoint 1.0 bar | [M] | Commissioning log 2 Oct 2026, "Vergleich PI"; PI chosen over PID because a real dp signal is noisy |
| Validation | open loop 36 % → 0.236 bar, 3.33 m³/h; 30 % → 0.1639 bar; load 50/90/20 % → 74.11/87.26/68.53 %, 6.85/12.33/2.74 m³/h; restart without overshoot; release tests 13–18 | [M] | Commissioning log 2 Oct 2026, tests 2, 3, 5, 10–18 |
| Unit bar | UNECE code BAR | [S] | UNECE Recommendation 20 |

## Validation targets (`tests/test_model.py`)

| Check | PLCSIM value | Tag | Log test |
|---|---|---|---|
| Setpoint at 0 °C / −12 °C | 44.0 / 58.4 °C | [M] | 0, 0b |
| Setpoint limit | 50.0 °C | [M] | 6 |
| Steady valve at 0 / −12 / −10 °C, limit 50 | 20.48 / 43.52 / 38.65 / 33.82 % | [M] | 0, 3, 5, 6 |
| Step 44 → 56 °C | dead time 10 s, 56 °C after 60 s, max 56.71 °C at 72 s | [M] | 5 (trace, 1 s resolution) |
| Manual 44 %, −12 °C | 58.67 °C, return = flow − 12.80 K | [M] | 1c, 1d |

## PLCSIM trace (`data/plcsim/`, `heating_plant/plcsim.py`)

| Item | Value | Tag | Source |
|---|---|---|---|
| Trace file | `Trace1_test5_setpoint_step_2026-09-25.csv`, 528 samples at 1 s, SHA-256 `b7e485d5be71f16aadb4e71b815c557313f18bd857fbd5c6a9e7d020477f0b0c` | [M] | Exported from the author's TIA Portal V19 project (Traces → Measurements → CSV), recorded 25 Sep 2026 15:07 UTC in PLCSIM; unchanged copy |
| Signals | `DB_Heizzentrale.tAussen`, `tVorlaufSoll`, `tVorlaufIst`, `ventilStellung`, `tRuecklauf` | [M] | Column headers of the file |
| Step sample | 222 (first sample with tAussen = −10) | [M] | Read from the file |
| Step time inside the second | 0.5 s (middle) | [A] | The trace cannot resolve it; with 0 … 1 s the flow RMSE is 0.22 … 0.14 K. Not fitted. |
| Result | flow RMSE 0.17 K, max 0.52 K, valve RMSE 2.1 % | [M] | `python scripts/plcsim_overlay.py` |
| Meaning | checks the port and the PID_Compact rebuild; the PLCSIM plant is the same model, so it is not a check against a real plant | — | — |

## Realistic mode and robustness (`HeatingPlant.realistic()`, `heating_plant/tuning.py`)

| Item | Value | Tag | Source |
|---|---|---|---|
| Valve actuator run time | 90 s for 0 → 100 %, constant speed | [A] | Order of magnitude of motorised mixing-valve actuators; take the value from the actuator datasheet of a real plant |
| Sensor noise | 0.05 K standard deviation, white, per 100 ms cycle | [A] | Plausible for a Pt1000 with transmitter on an analog input; measure on a real plant |
| Dead time vs flow | dead time = 10 s · 6.85 m³/h / flow; pump off = slowest case (79 s) | [D] | Plug flow: transport time = pipe volume / flow. 6.85 m³/h is the PLCSIM flow at 50 % load (commissioning 2 Oct 2026) |
| Linearised plant | G(s) = Kv·e^(−θs) / (τs + 1 − (1 − v)·e^(−θs)), Kv = (T_boiler − T_return)/100 | [D] | Derived from `FB_StreckeVorlauf` (return temperature = flow − spread, fed back into the mixing valve) |
| Margin targets | gain margin ≥ 2, phase margin ≥ 45°, Ms ≤ 2 | [S] | K. J. Åström, T. Hägglund, *Advanced PID Control*, ISA, 2006; Skogestad 2003 (below) |
| Robust PI | Kp 4 %/K, Ti 120 s, b 0.6, no D | [D] | Chosen from `tuning.sweep()`: Ms ≤ 1.3 at −12 … +10 °C, no overshoot with the 90 s actuator |
| Finding | PLCSIM PID: gain margin 1.59 / 1.22 / 1.02 at −12 / 0 / +10 °C; sustained oscillation at +12 °C in simulation | [M] | `python -m heating_plant.tuning`, `tests/test_tuning.py`. Model result; not yet repeated in PLCSIM |
| Input limits | Slope 0 … 3, Level ±10 K, FlowMax 25 … 75 °C, outdoor −30 … +50 °C, load 0 … 100 % | [D] | FlowMax within the PLC limits (`tVorlaufMin`, `tKessel`); outdoor = EMSR range of TI103; the others are plausibility limits |

## Gateway (library `aidconnect`, used by `heating_plant/aid_client.py` and `heating_plant/live.py`)

| Item | Tag | Source |
|---|---|---|
| Node references `nsu=<uri>;s=…`, `;i=…`, `ns=<n>;…` | [S] | OPC 10101 WoT binding 6.1; OPC 10000-6 NodeId string format |
| `OperationalData` submodel, semantic ID `…/semantics/OperationalData/1/0` | [A] | Own definition; no IDTA template for plain live values was used. Linking AID datapoints to submodel properties is what IDTA 02027 (Asset Interfaces Mapping Configuration) standardises; not implemented |
| Remote file: https + SHA-256, 20 MB limit | [D] | The AAS decides where credentials are sent, so it is treated like code |
| Server certificate pinning | [S] | OPC 10000-4, 6.1: a client shall validate the server certificate; here by exact match |
| Concept descriptions: IEC 61360 data specification | [S] | AAS Part 3a (IDTA 01003-a-3-0); IRI as checked by `aas-test-engines` 1.0.3; a number without unit is `REAL_COUNT` because `REAL_MEASURE` requires a unit (AASc-3a-009) |

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
| Signal names TIC101, TI102, TI103, TV101, PDIC301, SC301 | [D] | `heizzentrale-tia/docs/EMSR_Stellenliste.csv` (tags per IEC 62424 style) |
| Ranges 0…120 °C, −30…+50 °C, 0…100 %, 0…2.5 bar | [D] | EMSR list, column "Messbereich" |
| Pump object P301: `Speed`, `Flow` (AnalogItems), `Running`, `DriveFault` (Boolean), `Simulation.DriveFault` | [D] | `FB_Netzpumpe` release logic and `FB_ModellNetz` (speed, flow). Flow is a model value: the plant has no flow meter in the EMSR list. Unit m³/h = UNECE code MQH [S] |
| Simulation inputs in their own object (`Simulation.OutdoorTemperature`, `Simulation.ConsumerLoad`), all measurements read-only | [D] | Keeps simulated world inputs apart from plant signals |
| href format `nsu=<namespace>;s=<id>` | [S] | OPC 10101 OPC UA for WoT Binding, 6.1 (reference.opcfoundation.org/specs/OPC-10101/6.1) |
| Security policy Basic256Sha256, mode SignAndEncrypt, UserName token | [S] | OPC 10000-7 (profiles), OPC 10000-4 (ActivateSession, identity tokens); value names as in OPC 10101, 6.3.3 (uav:securityMode, uav:securityPolicy, uav:userIdentityToken) |
| Certificate checks: time range, application URI, extended key usage, trust list | [S] | OPC 10000-4, 6.1 (certificate validation); asyncua CertificateValidator |
| Password storage: PBKDF2-HMAC-SHA256, 200 000 iterations, 16-byte salt, constant-time compare | [S] | NIST SP 800-132; Python `hashlib.pbkdf2_hmac`, `hmac.compare_digest` |
| Self-signed certificates | [A] | Demo; a plant uses its own CA / OPC UA GDS. |
| `--insecure` mode | [A] | Local demo only; secure is the default. |

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
| Time Series Data template | [S] | IDTA 02008-1-1, v1.1.3, `standards/idta/IDTA_02008-1-1-3_TimeSeriesData.json`, git blob `dc678243…`; spec PDF `IDTA 02008-1-1_Submodel_TimeSeriesData.pdf`: time semantic `UtcTime` (table 4), State enumeration `completed` (table 7), SamplingInterval in s |
| Time series values | [S]+[D] | `TI103_PV` = DWD measurement; the other variables are the twin's response at minute 59 of each hour (`docs/week_magdeburg_2022-12.csv`) |
| Template qualifiers removed from instances | [S] | Cardinality / AllowedIdShort / Select / Constraint qualifiers describe templates (AAS Part 1, SMT conventions), not instances |
| Template licence | [S] | CC BY 4.0, github.com/admin-shell-io/submodel-templates |
| AID property `type` = `number`, `unit` = UNECE code | [S] | IDTA 02017 1.2, section 2.9 (type: object, array, string, number, integer, boolean, null; unit: schema.org unitCode) |
| Manufacturer, address | [D] | Author's name and city only (Magdeburg, DE). No street address on purpose (privacy); the template marks street as optional. |
| Order code "not orderable (demonstrator)" | [A] | Mandatory in the nameplate, but the plant is not a product. Stated openly instead of inventing a number. |
| Technical-data concept descriptions (`…/cd/<name>`) | [A] | ECLASS/IEC CDD would be the industry choice; no matching entries could be verified free of charge, so each property gets its own concept description with definition, unit and source. |
| Endpoint `opc.tcp://127.0.0.1:4840/heating-plant/` | [D] | Local demo endpoint; 4840 is the IANA-registered OPC UA port |

## AAS REST API (`heating_plant/aas_server.py`)

| Item | Tag | Source |
|---|---|---|
| API | [S] | IDTA 01002 Specification of the AAS Part 2: Application Programming Interfaces, v3.1 (base path `/api/v3.1`, identifiers base64url-encoded) |
| Server | [S] | Eclipse BaSyx Python server (`basyx-python-server`), MIT, pinned to the 2.2.0 release commit `a846219d8de7145a144fa832de926c452bab2f17` of github.com/eclipse-basyx/basyx-python-sdk; adds Werkzeug 3.1.9 (BSD-3-Clause). Not on PyPI, so `pip-audit` lists it as not auditable; its dependencies are audited. |
| `$value` endpoint | [S] | Answered 501 Not Implemented by BaSyx 2.2.0 (`app/interfaces/repository.py`); tests read the full element instead |
| Read-only, localhost | [D] | The AASX stays the single source of truth; the server publishes it, it does not edit it |

## Hardware path: TCLab (`heating_plant/tclab_backend.py`)

| Item | Tag | Source |
|---|---|---|
| TCLab rig and emulator | [S] | `tclab` 1.0.0 (Apache-2.0, BYU PRISM / github.com/jckantor/TCLab), `TCLabModel` energy balance in `tclab/tclab.py`; depends on pyserial |
| Two-point FOPDT identification (t28, t63) | [S] | C. L. Smith, *Digital Computer Process Control*, 1972; tau = 1.5·(t63 − t28), theta = t63 − tau |
| SIMC PI tuning, tau_c = theta | [S] | S. Skogestad, "Simple analytic rules for model reduction and PID controller tuning", J. Process Control 13 (2003) 291–309 |
| Identified emulator model | [M] | K ≈ 0.60 K/% (analytic from the emulator equations: 0.5994), tau ≈ 140 s, theta ≈ 20 s; small run-to-run spread from the emulator's measurement noise |
| Real hardware | [A] | Not tested (no rig available). The server refuses `--plant tclab` without PI values from a hardware step test, and runs it only in real time. |

## Plots (`scripts/week_plot.py`, `docs/`)

| Item | Tag | Source |
|---|---|---|
| Outdoor temperature held constant within each hour | [D] | DWD gives one value per hour; no interpolation is invented |
| matplotlib 3.11.2 | [S] | Matplotlib licence (PSF-based); `pip-audit` clean on 10 Nov 2026 |
