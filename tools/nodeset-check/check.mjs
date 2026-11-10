// Independent check of model/HeatingPlant.NodeSet2.xml with a second OPC UA stack (node-opcua).
// It loads the standard UA nodeset plus ours and verifies types, instances and AnalogItem children.
import { AddressSpace } from "node-opcua-address-space";
import { generateAddressSpace } from "node-opcua-address-space/nodeJS.js";
import { nodesets } from "node-opcua-nodesets";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const ours = path.resolve(here, "../../model/HeatingPlant.NodeSet2.xml");
const NS = "https://github.com/sulemanali5760/heating-plant-aas/UA/";

const as = AddressSpace.create();
await generateAddressSpace(as, [nodesets.standard, ours]);
const ns = as.getNamespaceIndex(NS);
const fail = (m) => { console.error("FAIL:", m); process.exitCode = 1; };
const node = (s) => as.findNode(`ns=${ns};s=${s}`);

const expectType = { HeatingPlant: "HeatingPlantType", TIC101: "ControlLoopType", PDIC301: "ControlLoopType", Simulation: "SimulationInputsType",
  TI102: "TemperatureIndicatorType", TI103: "TemperatureIndicatorType", HeatingCurve: "HeatingCurveType" };
for (const [obj, type] of Object.entries(expectType)) {
  const n = node(obj);
  if (!n) { fail(`${obj} missing`); continue; }
  if (n.typeDefinitionObj?.browseName.name !== type) fail(`${obj} has type ${n.typeDefinitionObj?.browseName.name}`);
}
const analog = as.findVariableType("AnalogItemType");
for (const id of ["TIC101.SP", "TIC101.PV", "TIC101.OP", "TI102.PV", "TI103.PV", "PDIC301.SP", "PDIC301.PV", "PDIC301.OP"]) {
  const v = node(id);
  if (!v) { fail(`${id} missing`); continue; }
  if (!v.typeDefinitionObj.isSubtypeOf(analog) && v.typeDefinitionObj !== analog) fail(`${id} not AnalogItemType`);
  if (v.dataType.toString() !== "ns=0;i=11") fail(`${id} data type ${v.dataType.toString()} (expected Double)`);
  for (const c of ["EURange", "EngineeringUnits"]) if (!v.getPropertyByName(c)) fail(`${id} has no ${c}`);
}
const loopType = as.findObjectType("ControlLoopType", ns);
for (const m of ["SP", "PV", "OP"]) {
  const c = loopType?.getComponentByName(m, ns);
  if (!c) fail(`ControlLoopType.${m} missing`);
  else if (c.modellingRule !== "Mandatory") fail(`ControlLoopType.${m} rule ${c.modellingRule}`);
}
if (!process.exitCode) console.log(`node-opcua: NodeSet OK (namespace index ${ns}, ${Object.keys(expectType).length} typed objects, 8 AnalogItems)`);
as.dispose();
