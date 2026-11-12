"""PLCSIM controller setting against the robust PI, both on the realistic plant (90 s valve actuator,
0.05 K sensor noise, flow-dependent dead time). Output: docs/tuning_comparison.png"""
import pathlib
from dataclasses import replace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from heating_plant.model import HeatingPlant
from heating_plant.tuning import PLCSIM, ROBUST, evaluate, margins

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(pid):
    p = HeatingPlant.realistic(t_outdoor=0.0)
    p.pid = replace(pid)
    p.settle()
    t, flow, cmd, pos = [], [], [], []
    for k in range(9000):                    # 15 min, outdoor step 0 -> -10 °C after 2 min
        if k == 1200:
            p.t_outdoor = -10.0
        p.step()
        if k % 5 == 0:
            t.append(p.time / 60)
            flow.append(p.circuit.t_flow)
            cmd.append(p.valve)
            pos.append(p.circuit.valve_pos)
    return t, flow, cmd, pos


def main() -> None:
    fig, axes = plt.subplots(2, 2, figsize=(12, 6.5), sharex=True, height_ratios=[1.3, 1])
    for col, (name, pid) in enumerate((("PLCSIM fine tuning (PID, Kp 20.66, Ti 20 s, Td 5 s)", PLCSIM),
                                       (f"Robust PI (Kp {ROBUST.kp:g}, Ti {ROBUST.ti:g} s)", ROBUST))):
        t, flow, cmd, pos = run(pid)
        m, r = margins(pid, 0.0), evaluate(pid)
        a1, a2 = axes[0][col], axes[1][col]
        a1.plot(t, flow, color="#2c3e50", lw=1.2)
        a1.plot([0, 2, 2, 15], [44, 44, 56, 56], color="#c0392b", lw=1.0, ls="--")
        a1.set_title(name, fontsize=10)
        a1.set_ylim(42, 62)
        a2.plot(t, cmd, color="#bbb", lw=0.6, label="controller output")
        a2.plot(t, pos, color="#27ae60", lw=1.4, label="valve position (90 s actuator)")
        a2.set_ylim(-3, 103)
        a2.set_xlabel("min")
        a2.text(0.02, 0.04, f"gain margin {m.gain_margin:.2f} · phase margin {m.phase_margin:.0f}° · Ms {m.ms:.1f}\n"
                f"valve command travels {r.valve_travel:,.0f} % per hour at rest", transform=a2.transAxes,
                fontsize=8, color="#333", va="bottom", bbox=dict(fc="white", ec="none", alpha=0.8))
    axes[0][0].set_ylabel("flow temperature °C")
    axes[1][0].set_ylabel("valve %")
    axes[1][1].legend(loc="upper right", fontsize=8)
    fig.suptitle("Same setpoint step 44 → 56 °C on the realistic plant: the setting that looks best in PLCSIM "
                 "does not survive a real valve and a noisy sensor", fontsize=10.5)
    fig.tight_layout()
    fig.savefig(ROOT / "docs/tuning_comparison.png", dpi=130)


if __name__ == "__main__":
    main()
