"""TCLab path, on the emulator from the tclab package (no hardware in CI)."""
import asyncio

import pytest
from asyncua import Client, ua

from heating_plant import tclab_backend as tb
from heating_plant.model import Pid2Dof
from heating_plant.opcua_server import NAMESPACE, run

ENDPOINT = "opc.tcp://127.0.0.1:48411/heating-plant/"


def _analytic_gain(q=50.0, p1=200.0):
    """Steady state of the emulator's own energy balance (tclab.TCLabModel.update):
    0 = P1*Q/5720 - x/20 - (x-y)/100 and 0 = -y/20 + (x-y)/100, with x = H1-Ta, y = H2-Ta -> y = x/6."""
    a = p1 * q / 5720
    x = a / (1 / 20 + (1 - 1 / 6) / 100)
    return x / q


def test_identified_gain_matches_emulator_physics():
    m = tb.identify(tb.step_test())
    assert m.gain == pytest.approx(_analytic_gain(), rel=0.02)
    assert 100 < m.tau < 200 and 5 < m.theta < 40     # first order + dead time, seconds


def test_simc_pi_controls_t1_without_large_overshoot():
    kc, ti = tb.identify(tb.step_test()).simc_pi()
    circuit, pid = tb.TCLabCircuit(), Pid2Dof(kp=kc, ti=ti, td=0.0, b=1.0, ts=1.0)
    peak = 0.0
    for _ in range(2400):                                # 40 min, 1 s steps
        circuit.step(pid.update(44.0, circuit.t_flow, 1.0), 75.0, 0.0, 1.0)
        peak = max(peak, circuit.t_flow)
    circuit.close()
    assert circuit.t_flow == pytest.approx(44.0, abs=0.5)
    assert peak < 44.0 + 1.5                           # SIMC with tau_c = theta: little overshoot


async def _served():
    ready = asyncio.Event()
    srv = asyncio.create_task(run(seconds=20, time_lapse=100, outdoor="manual", endpoint=ENDPOINT,
                                  ready=ready, plant_kind="tclab-emulator"))
    await asyncio.wait_for(ready.wait(), 60)
    async with Client(ENDPOINT) as c:
        idx = await c.get_namespace_index(NAMESPACE)
        await asyncio.sleep(3)
        pv = await c.get_node(ua.NodeId("TIC101.PV", idx)).read_value()
        op = await c.get_node(ua.NodeId("TIC101.OP", idx)).read_value()
    srv.cancel()
    return pv, op


def test_same_opcua_model_on_tclab_emulator():
    pv, op = asyncio.run(_served())
    assert pv > 21.0          # heated above the emulator's 21 °C ambient
    assert 0.0 < op <= 100.0  # heater driven by the same TIC101 loop


def test_real_tclab_requires_hardware_tuning():
    with pytest.raises(ValueError):
        asyncio.run(run(seconds=1, plant_kind="tclab", time_lapse=60, endpoint=ENDPOINT))
