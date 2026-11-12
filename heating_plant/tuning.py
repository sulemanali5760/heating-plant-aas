"""How robust is a controller setting for loop TIC101, and what does it do to the valve?

Two views of the same question:
1. margins(): frequency response of the linearised loop -> gain margin, phase margin, Ms.
   Plant, linearised at an operating point (valve v, return temperature t_ret):
       G(s) = Kv * exp(-theta*s) / (tau*s + 1 - (1 - v) * exp(-theta*s)),  Kv = (t_boiler - t_ret) / 100
   The term (1 - v)*exp(-theta*s) is the return water coming back into the mixing valve. It makes the
   "60 s" circuit react like a much slower, nearly integrating process.
   Controller (feedback path of the 2-DOF PIDT1; b and c only shape the setpoint response):
       C(s) = Kp * (1 + 1/(Ti*s) + Td*s/(a*Td*s + 1)),  sampled: extra dead time ts/2.
   The valve speed limit is not linear and is left out here; evaluate() covers it by simulation.
2. evaluate(): simulate a setpoint step and a quiet hour on HeatingPlant.realistic() and measure
   overshoot, settling time and how far the valve travels because of measurement noise.

Common targets: gain margin >= 2 (6 dB), phase margin >= 45 deg, Ms <= 2.0 (Astrom and Hagglund,
Advanced PID Control, 2006; Skogestad 2003).
"""
import cmath
import math
from dataclasses import dataclass, replace

from heating_plant.model import HeatingPlant, Pid2Dof, heating_curve, steady_valve

PLCSIM = Pid2Dof()                                              # fine tuning in PLCSIM, test 4
# PI for the realistic plant, found with sweep(): highest gain that keeps Ms <= 1.7 at 0 °C and
# still settles the 12 K step with the 90 s actuator. No D part: with a = 0.1 it multiplies sensor noise by Kp/a.
ROBUST = Pid2Dof(kp=4.0, ti=120.0, td=0.0, b=0.6, c=0.0, ts=0.4)


@dataclass
class Margins:
    gain_margin: float      # factor the gain may rise before the loop oscillates
    phase_margin: float     # degrees
    ms: float               # peak of |1/(1+L)|: closeness to instability (smaller = more robust)
    crossover: float        # rad/s where |L| = 1


def loop_gain(pid: Pid2Dof, w: float, t_outdoor: float, tau: float = 60.0, theta: float = 10.0,
              t_boiler: float = 75.0) -> complex:
    s = 1j * w
    t_set = heating_curve(t_outdoor)
    v = steady_valve(t_outdoor, t_set, t_boiler) / 100.0
    spread = min(max(0.4 * (20.0 - t_outdoor), 2.0), 20.0)
    kv = (t_boiler - (t_set - spread)) / 100.0
    delay = cmath.exp(-theta * s)
    g = kv * delay / (tau * s + 1.0 - (1.0 - v) * delay)
    d = pid.td * s / (pid.a * pid.td * s + 1.0) if pid.td > 0 else 0.0
    c = pid.kp * (1.0 + 1.0 / (pid.ti * s) + d) * cmath.exp(-pid.ts / 2.0 * s)
    return c * g


def margins(pid: Pid2Dof, t_outdoor: float = 0.0, **plant) -> Margins:
    ws = [10 ** (-4 + 5 * k / 20000) for k in range(20001)]     # 1e-4 ... 10 rad/s
    ls = [loop_gain(pid, w, t_outdoor, **plant) for w in ws]
    ms = max(1.0 / abs(1.0 + l) for l in ls)
    gm, pm, wc = math.inf, math.inf, 0.0
    phase = [cmath.phase(ls[0])]
    for a, b in zip(ls, ls[1:]):                                 # unwrap the phase
        step = cmath.phase(b / a)
        phase.append(phase[-1] + step)
    for k in range(len(ws) - 1):
        if (phase[k] + math.pi) * (phase[k + 1] + math.pi) <= 0 and gm == math.inf:   # first -180° crossing
            gm = 1.0 / abs(ls[k])
        if (abs(ls[k]) - 1.0) * (abs(ls[k + 1]) - 1.0) <= 0:                          # |L| = 1 (last one wins)
            pm, wc = math.degrees(phase[k] + math.pi), ws[k]
    return Margins(gm, pm, ms, wc)


@dataclass
class Result:
    overshoot: float        # K above the new setpoint after the 44 -> 56 °C step
    settling: float         # s until the flow temperature stays within 0.5 K
    valve_travel: float     # % of full stroke the actuator is asked to move per hour at steady state
    noise_std: float        # % standard deviation of the valve command at steady state


def evaluate(pid: Pid2Dof, realistic: bool = True, seed: int = 0) -> Result:
    def plant():
        p = HeatingPlant.realistic(t_outdoor=0.0) if realistic else HeatingPlant(t_outdoor=0.0)
        p.pid = replace(pid)
        p._rng.seed(seed)
        p.settle()
        return p
    p = plant()
    for _ in p.run(120):
        pass
    p.t_outdoor = -10.0
    peak, last_out, t0 = 0.0, 0.0, p.time
    for _ in range(round(1800 / p.cycle)):
        p.step()
        peak = max(peak, p.circuit.t_flow)
        if abs(p.circuit.t_flow - 56.0) > 0.5:
            last_out = p.time - t0
    q = plant()
    for _ in q.run(600):
        pass
    travel, prev, cmds = 0.0, q.valve, []
    for _ in range(round(3600 / q.cycle)):
        q.step()
        travel += abs(q.valve - prev)
        prev = q.valve
        cmds.append(q.valve)
    mean = sum(cmds) / len(cmds)
    std = (sum((c - mean) ** 2 for c in cmds) / len(cmds)) ** 0.5
    return Result(peak - 56.0, last_out, travel, std)


def sweep():
    """Candidates for a robust PI: print margins and realistic-plant behaviour."""
    for kp in (2.0, 3.0, 4.0, 6.0, 8.0):
        for ti in (60.0, 120.0, 200.0):
            pid = Pid2Dof(kp=kp, ti=ti, td=0.0, b=0.6, c=0.0, ts=0.4)
            m, r = margins(pid), evaluate(pid)
            print(f"Kp {kp:4.1f} Ti {ti:5.0f} | GM {m.gain_margin:5.2f} PM {m.phase_margin:5.1f} Ms {m.ms:4.2f} | "
                  f"overshoot {r.overshoot:5.2f} K settle {r.settling:5.0f} s travel {r.valve_travel:7.0f} %/h")


if __name__ == "__main__":
    for name, pid in (("PLCSIM fine tuning (PID)", PLCSIM), ("Robust PI", ROBUST)):
        print(name)
        for t in (-12.0, 0.0, 10.0):
            m = margins(pid, t)
            print(f"  outdoor {t:5.1f} °C: gain margin {m.gain_margin:5.2f}, phase margin {m.phase_margin:5.1f}°, Ms {m.ms:4.2f}")
        for mode in (False, True):
            r = evaluate(pid, mode)
            print(f"  {'realistic' if mode else 'ideal    '} plant: overshoot {r.overshoot:5.2f} K, settled after "
                  f"{r.settling:5.0f} s, valve command {r.valve_travel:8.0f} % travel/h (std {r.noise_std:.2f} %)")
    sweep()
