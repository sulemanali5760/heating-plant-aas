"""Hardware path: run loop TIC101 on a TCLab (Temperature Control Lab, the Arduino rig used in
university automation labs) instead of the simulated mixing circuit.

Same steps as a lab exercise:
1. Step test on the plant (heater Q1 0 -> 50 %), record T1.
2. Identify a first-order-plus-dead-time model with the two-point method
   (t28 and t63: tau = 1.5*(t63 - t28), theta = t63 - tau; Smith 1972).
3. Tune a PI controller with SIMC rules (Skogestad 2003, tau_c = theta):
   Kc = tau / (K * (tau_c + theta)),  Ti = min(tau, 4 * (tau_c + theta)).

Backends: TCLabModel (the emulator shipped with the tclab package, deterministic clock) for tests;
a real TCLab on USB for the hardware run. The hardware run is NOT tested here (no hardware available):
identify and tune again on the real rig before trusting the parameters.
"""
from dataclasses import dataclass

import tclab


@dataclass
class Fopdt:
    gain: float        # K per % heater
    tau: float         # s
    theta: float       # s

    def simc_pi(self) -> tuple[float, float]:
        tau_c = max(self.theta, 1.0)
        kc = self.tau / (self.gain * (tau_c + self.theta))
        ti = min(self.tau, 4 * (tau_c + self.theta))
        return kc, ti


def step_test(q_step: float = 50.0, seconds: float = 1500.0, dt: float = 1.0) -> list[tuple[float, float]]:
    """Heater 1 from 0 to q_step % at t = 0 on the emulator; returns (t, T1)."""
    lab = tclab.TCLabModel(synced=False)
    lab.Q1(q_step)
    data, t = [], 0.0
    while t <= seconds:
        lab.update(t)
        data.append((t, lab.T1))
        t += dt
    lab.close()
    return data


def identify(data: list[tuple[float, float]], q_step: float = 50.0) -> Fopdt:
    t0, y0 = data[0]
    y_end = data[-1][1]
    dy = y_end - y0

    def t_at(frac):
        return next(t for t, y in data if y - y0 >= frac * dy) - t0
    t28, t63 = t_at(0.283), t_at(0.632)
    tau = 1.5 * (t63 - t28)
    return Fopdt(gain=dy / q_step, tau=tau, theta=max(t63 - tau, 0.0))


class TCLabCircuit:
    """Drop-in for MixingCircuit: the valve output drives heater Q1, the flow temperature is T1.
    TI102 is mapped to T2 (second sensor, heater 2 off) so the OPC UA model stays the same."""

    def __init__(self, hardware: bool = False):
        self.hardware = hardware
        self.lab = tclab.TCLab() if hardware else tclab.TCLabModel(synced=False)
        self.t = 0.0
        self.t_flow = self.lab.T1
        self.t_return = self.lab.T2
        self.time_constant, self.dead_time = 0.0, 0.0  # not used; kept for interface parity

    def step(self, valve_pct: float, t_boiler: float, t_outdoor: float, dt: float) -> float:
        self.lab.Q1(min(max(valve_pct, 0.0), 100.0))
        self.t += dt
        if not self.hardware:
            self.lab.update(self.t)
        self.t_flow, self.t_return = self.lab.T1, self.lab.T2
        return self.t_flow

    def close(self) -> None:
        self.lab.Q1(0)
        self.lab.close()
