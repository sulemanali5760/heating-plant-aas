"""Robustness of the TIC101 controller settings: linear margins, and the same finding by simulation."""
from dataclasses import replace

from heating_plant.model import HeatingPlant
from heating_plant.tuning import PLCSIM, ROBUST, evaluate, margins


def test_plcsim_tuning_has_almost_no_margin_in_mild_weather():
    assert margins(PLCSIM, -12.0).gain_margin > 1.5
    assert margins(PLCSIM, 10.0).gain_margin < 1.1          # 2 % more loop gain and it oscillates
    assert margins(PLCSIM, 0.0).ms > 4.0


def test_robust_pi_keeps_margins_over_the_whole_heating_season():
    for t_outdoor in (-12.0, 0.0, 10.0):
        m = margins(ROBUST, t_outdoor)
        assert m.gain_margin > 4.0 and m.phase_margin > 55.0 and m.ms < 1.4, (t_outdoor, m)


def _valve_swing(pid, t_outdoor):
    """Ideal plant (the 1:1 PLC port), +1 K setpoint step, valve swing in minutes 10-15."""
    p = HeatingPlant(t_outdoor=t_outdoor)
    p.pid = replace(pid)
    p.settle()
    for _ in p.run(60):
        pass
    p.level = 1.0
    valves = [v for t, _, _, _, v, _ in p.run(900) if t > 660]
    return max(valves) - min(valves)


def test_simulation_confirms_the_margins():
    """The linear analysis is not trusted alone: at +12 °C the PLCSIM setting keeps oscillating on the
    unchanged PLC model, the robust PI does not. At 0 °C (where it was tuned) both are calm."""
    assert _valve_swing(PLCSIM, 12.0) > 5.0
    assert _valve_swing(ROBUST, 12.0) < 0.5
    assert _valve_swing(PLCSIM, 0.0) < 0.5


def test_realistic_plant_noise_and_actuator():
    """With a 90 s actuator and 0.05 K sensor noise the D part of the PLCSIM setting drives the valve
    back and forth; the robust PI settles the 12 K step without overshoot."""
    hot, calm = evaluate(PLCSIM), evaluate(ROBUST)
    assert hot.valve_travel > 20000 and hot.overshoot > 2.0
    assert calm.valve_travel < 2500 and calm.overshoot < 0.5 and calm.settling < 400


def test_realistic_dead_time_follows_the_flow():
    """Less consumer load -> less flow -> the water needs longer through the pipe."""
    def dead_time(load):
        p = HeatingPlant.realistic(t_outdoor=0.0, auto=False)
        p.pump.load = load
        p.settle()
        p.manual_valve = p.valve
        for _ in p.run(120):
            pass
        start = p.circuit.t_flow
        p.manual_valve += 20.0
        p.circuit.valve_runtime = 0.0   # isolate the dead time from the actuator
        for _ in p.run(120):
            if p.circuit.t_flow > start + 0.01:
                return p.time - 120.0  # s after the valve step
    quiet, nominal, busy = dead_time(20.0), dead_time(50.0), dead_time(90.0)
    assert quiet > nominal > busy
    assert abs(nominal - 10.0) < 1.5
