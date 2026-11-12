"""Integration test: a plain OPC UA client sees the typed model and drives the plant."""
import asyncio

from asyncua import Client, ua

from heating_plant.opcua_server import NAMESPACE, POINTS, run

ENDPOINT = "opc.tcp://127.0.0.1:48401/heating-plant/"


async def _scenario():
    ready = asyncio.Event()
    server = asyncio.create_task(run(seconds=20, time_lapse=10, outdoor="manual",
                                     endpoint=ENDPOINT, ready=ready))
    await asyncio.wait_for(ready.wait(), 10)
    async with Client(ENDPOINT) as c:
        idx = await c.get_namespace_index(NAMESPACE)
        node = lambda s: c.get_node(ua.NodeId(s, idx))

        for p in POINTS:
            v = node(p.node_id)
            type_def = await v.read_type_definition()
            assert type_def == ua.NodeId(ua.ObjectIds.AnalogItemType), p.node_id
            assert await v.read_data_type() == ua.NodeId(ua.ObjectIds.Double)
            rng = await (await v.get_child(["0:EURange"])).read_value()
            assert (rng.Low, rng.High) == (p.low, p.high)
            eu = await (await v.get_child(["0:EngineeringUnits"])).read_value()
            assert eu.NamespaceUri == "http://www.opcfoundation.org/UA/units/un/cefact"

        # Objects are typed with this namespace's ObjectTypes
        for obj, tname in [("HeatingPlant", "HeatingPlantType"), ("TIC101", "ControlLoopType"), ("PDIC301", "ControlLoopType"), ("Simulation", "SimulationInputsType"),
                           ("P301", "PumpType"),
                           ("TI102", "TemperatureIndicatorType"), ("TI103", "TemperatureIndicatorType"),
                           ("HeatingCurve", "HeatingCurveType")]:
            assert await node(obj).read_type_definition() == ua.NodeId(tname, idx), obj
        loop_type = node("ControlLoopType")
        members = {(await m.read_browse_name()).Name for m in await loop_type.get_children()}
        assert {"SP", "PV", "OP"} <= members

        # Browse path from the Objects folder, as the AAS uses it (uav:browsePath)
        pv = await c.nodes.objects.get_child([f"{idx}:HeatingPlant", f"{idx}:TIC101", f"{idx}:PV"])
        assert pv.nodeid == ua.NodeId("TIC101.PV", idx)

        # Measured values are read-only, the outdoor temperature included
        for ro in ("TIC101.PV", "TI103.PV", "PDIC301.PV"):
            try:
                await node(ro).write_value(ua.DataValue(ua.Variant(99.0, ua.VariantType.Double)))
                raise AssertionError(f"{ro} must be read-only")
            except ua.UaStatusCodeError:
                pass

        # Drive the plant: outdoor -12 °C -> setpoint 58.4 °C (heating curve, test 0b)
        await node("Simulation.OutdoorTemperature").write_value(ua.DataValue(ua.Variant(-12.0, ua.VariantType.Double)))
        await asyncio.sleep(1.0)
        assert abs(await node("TIC101.SP").read_value() - 58.4) < 1e-6
        valve = await node("TIC101.OP").read_value()
        assert 0.0 <= valve <= 100.0
        assert await node("TI103.PV").read_value() == -12.0  # indication follows the simulated weather
        assert abs(await node("PDIC301.SP").read_value() - 1.0) < 1e-9

        # Pump status: running at -12 °C; a drive fault stops it and is reported, clearing it restarts the pump
        assert await node("P301.Running").read_value() is True
        assert await node("P301.Flow").read_value() > 0.0
        await node("Simulation.DriveFault").write_value(ua.DataValue(ua.Variant(True, ua.VariantType.Boolean)))
        await asyncio.sleep(1.0)
        assert await node("P301.DriveFault").read_value() is True
        assert await node("P301.Running").read_value() is False
        assert await node("PDIC301.OP").read_value() == 0.0
        await node("Simulation.DriveFault").write_value(ua.DataValue(ua.Variant(False, ua.VariantType.Boolean)))
        await asyncio.sleep(1.0)
        assert await node("P301.Running").read_value() is True
        for ro in ("P301.Running", "P301.DriveFault"):
            try:
                await node(ro).write_value(ua.DataValue(ua.Variant(True, ua.VariantType.Boolean)))
                raise AssertionError(f"{ro} must be read-only")
            except ua.UaStatusCodeError:
                pass

        # Writable inputs are range-checked: NaN or an impossible value is undone and never reaches the PID
        for nid, bad, good in [("HeatingCurve.Slope", float("nan"), 1.2), ("HeatingCurve.FlowMax", 500.0, 75.0),
                               ("Simulation.OutdoorTemperature", 999.0, -12.0), ("Simulation.ConsumerLoad", -5.0, 50.0)]:
            await node(nid).write_value(ua.DataValue(ua.Variant(bad, ua.VariantType.Double)))
            await asyncio.sleep(0.5)
            assert await node(nid).read_value() == good, nid
        assert abs(await node("TIC101.SP").read_value() - 58.4) < 1e-6
        op = await node("TIC101.OP").read_value()
        assert op == op and 0.0 <= op <= 100.0   # not NaN
    server.cancel()


async def _realistic():
    ready = asyncio.Event()
    server = asyncio.create_task(run(seconds=20, time_lapse=600, outdoor="manual", endpoint=ENDPOINT, ready=ready,
                                     plant_kind="realistic"))
    await asyncio.wait_for(ready.wait(), 10)
    async with Client(ENDPOINT) as c:
        idx = await c.get_namespace_index(NAMESPACE)
        await asyncio.sleep(2.0)                      # 20 plant minutes
        pv = [await c.get_node(ua.NodeId("TIC101.PV", idx)).read_value() for _ in range(5) if not await asyncio.sleep(0.2)]
    server.cancel()
    return pv


def test_realistic_plant_is_served_and_holds_setpoint():
    pv = asyncio.run(_realistic())
    assert all(abs(x - 44.0) < 0.5 for x in pv)     # noisy measurement around the 44 °C setpoint
    assert len(set(pv)) > 1                          # it is a noisy signal, not a constant


def test_opcua_model_and_control():
    asyncio.run(_scenario())
