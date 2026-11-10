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
        for obj, tname in [("HeatingPlant", "HeatingPlantType"), ("TIC101", "TemperatureControlLoopType"),
                           ("TI102", "TemperatureIndicatorType"), ("TI103", "TemperatureIndicatorType"),
                           ("HeatingCurve", "HeatingCurveType")]:
            assert await node(obj).read_type_definition() == ua.NodeId(tname, idx), obj
        loop_type = node("TemperatureControlLoopType")
        members = {(await m.read_browse_name()).Name for m in await loop_type.get_children()}
        assert {"SP", "PV", "OP"} <= members

        # Browse path from the Objects folder, as the AAS uses it (uav:browsePath)
        pv = await c.nodes.objects.get_child([f"{idx}:HeatingPlant", f"{idx}:TIC101", f"{idx}:PV"])
        assert pv.nodeid == ua.NodeId("TIC101.PV", idx)

        # Read-only measured value cannot be written
        try:
            await node("TIC101.PV").write_value(99.0)
            raise AssertionError("TIC101.PV must be read-only")
        except ua.UaStatusCodeError:
            pass

        # Drive the plant: outdoor -12 °C -> setpoint 58.4 °C (heating curve, test 0b)
        await node("TI103.PV").write_value(ua.DataValue(ua.Variant(-12.0, ua.VariantType.Double)))
        await asyncio.sleep(1.0)
        assert abs(await node("TIC101.SP").read_value() - 58.4) < 1e-6
        valve = await node("TIC101.OP").read_value()
        assert 0.0 <= valve <= 100.0
    server.cancel()


def test_opcua_model_and_control():
    try:
        asyncio.run(_scenario())
    except asyncio.CancelledError:
        pass
