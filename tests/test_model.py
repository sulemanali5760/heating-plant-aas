"""The twin must reproduce the PLCSIM commissioning measurements (heizzentrale-tia,
docs/Inbetriebnahmeprotokoll.md, 25 Sep 2026). Test numbers refer to that log."""
import pytest

from heating_plant.model import HeatingPlant, heating_curve, steady_valve


@pytest.mark.parametrize("t_outdoor, t_max, expected", [
    (0.0, 75.0, 44.0),     # test 0
    (-12.0, 75.0, 58.4),   # test 0b
    (-12.0, 50.0, 50.0),   # test 6, limit
])
def test_heating_curve(t_outdoor, t_max, expected):
    assert heating_curve(t_outdoor, t_flow_max=t_max) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("t_outdoor, t_max, measured_valve", [
    (0.0, 75.0, 20.48),    # test 0, automatic, stationary
    (-12.0, 75.0, 43.52),  # test 3
    (-10.0, 75.0, 38.65),  # test 5, after the step
    (-12.0, 50.0, 33.82),  # test 6
])
def test_steady_valve_matches_plcsim(t_outdoor, t_max, measured_valve):
    p = HeatingPlant(t_outdoor=t_outdoor, t_flow_max=t_max)
    p.settle()
    for _ in p.run(900):
        pass
    assert p.circuit.t_flow == pytest.approx(p.t_flow_set, abs=0.01)
    assert p.valve == pytest.approx(measured_valve, abs=0.2)
    assert p.valve == pytest.approx(steady_valve(t_outdoor, p.t_flow_set), abs=0.01)


def test_setpoint_step_44_to_56_matches_plcsim_trace():
    """Test 5: outdoor 0 -> -10 °C, setpoint 44 -> 56 °C.
    PLCSIM trace: dead time 10 s, 56 °C first reached after 60 s, max 56.71 °C after 72 s."""
    p = HeatingPlant(t_outdoor=0.0)
    p.settle()
    for _ in p.run(60):
        pass
    p.t_outdoor = -10.0
    t0, start = p.time, p.circuit.t_flow
    dead = first = None
    peak, t_peak = 0.0, 0.0
    for t, _, _, x, _, _ in p.run(400):
        if dead is None and x > start + 0.01:
            dead = t - t0
        if first is None and x >= 56.0:
            first = t - t0
        if x > peak:
            peak, t_peak = x, t - t0
    assert dead == pytest.approx(10.0, abs=1.0)
    assert first == pytest.approx(60.0, abs=8.0)
    assert peak - 56.0 == pytest.approx(0.71, abs=0.3)
    assert t_peak == pytest.approx(72.0, abs=8.0)
    assert x == pytest.approx(56.0, abs=0.05)


def test_manual_valve_open_44pct_reaches_58_7():
    """Test 1c: manual 44 %, outdoor -12 °C -> flow ≈ 58.7 °C, return = flow - 12.8 K (test 1d)."""
    p = HeatingPlant(t_outdoor=-12.0, auto=False, manual_valve=44.0)
    for _ in p.run(30 * 60):
        pass
    assert p.circuit.t_flow == pytest.approx(58.67, abs=0.3)
    assert p.circuit.t_return == pytest.approx(p.circuit.t_flow - 12.8, abs=0.01)
