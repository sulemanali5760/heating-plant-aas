"""Pump loop PDIC301 must reproduce the PLCSIM commissioning of 2 Oct 2026
(heizzentrale-tia/docs/Inbetriebnahmeprotokoll.md, Phase 5; test numbers refer to that log)."""
import pytest

from heating_plant.model import HeatingNetwork, PumpLoop, PumpRelease


def _settle(obj, seconds, *args):
    for _ in range(round(seconds / 0.1)):
        obj.step(*args)


@pytest.mark.parametrize("speed, dp, flow", [(36.0, 0.236, 3.33), (30.0, 0.1639, None)])  # tests 2, 3
def test_network_model_open_loop(speed, dp, flow):
    n = HeatingNetwork()
    _settle(n, 60, speed, 50.0, True, 0.1)
    assert n.dp == pytest.approx(dp, abs=0.0005)
    if flow:
        assert n.flow == pytest.approx(flow, abs=0.005)


def test_load_steps_at_constant_dp():
    """Tests 5, 10, 11, 12 (PI): dp held at 1.0 bar, speed and flow follow the load."""
    loop = PumpLoop()
    for load, speed, flow in [(50, 74.11, 6.85), (90, 87.26, 12.33), (20, 68.53, 2.74), (50, 74.11, 6.85)]:
        loop.load = load
        _settle(loop, 60, 0.0, 0.1)
        assert loop.network.dp == pytest.approx(1.0, abs=0.001)
        assert loop.network.speed == pytest.approx(speed, abs=0.02)
        assert loop.network.flow == pytest.approx(flow, abs=0.01)


def test_restart_without_overshoot():
    """Test 14: pump starts from standstill, dp rises to 1.0 bar without overshoot, 74.1 %."""
    loop = PumpLoop()
    peak = 0.0
    for _ in range(600):
        loop.step(14.0, 0.1)
        peak = max(peak, loop.network.dp)
    assert peak <= 1.0 + 0.01
    assert loop.network.speed == pytest.approx(74.1, abs=0.05)


def test_release_logic():
    """Tests 13, 14, 15, 16, 17: heating limit with hysteresis, emergency switch before frost protection,
    drive fault."""
    r = PumpRelease()
    assert r.step(17.0, True, True) is False      # 13: above heating limit
    assert r.step(14.0, True, True) is True       # 14: below heating limit
    assert r.step(15.5, True, True) is True       # 15: inside hysteresis band, stays on
    assert r.step(-10.0, False, True) is False    # 16: emergency switch wins over frost protection
    assert r.frost and r.demand
    assert r.step(0.0, True, False) is False      # 17: drive fault
    assert r.step(0.0, True, True) is True        # 18: everything released


def test_pump_off_resets_controller():
    loop = PumpLoop()
    _settle(loop, 30, 0.0, 0.1)
    assert loop.pump_on and loop.speed_set > 0
    _settle(loop, 30, 17.0, 0.1)
    assert not loop.pump_on and loop.speed_set == 0.0 and loop.pid.integral == 0.0
    assert loop.network.dp < 0.01
