"""Plot the model on top of the PLCSIM trace of test 5. Output: docs/plcsim_overlay.png"""
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from heating_plant.plcsim import compare, metrics

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> None:
    rows = compare()
    m = metrics(rows)
    t = [r["t"] for r in rows]
    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(10, 7.5), sharex=True, height_ratios=[2, 1, 1.4])
    a1.plot(t, [r["flow"] for r in rows], color="#2c3e50", lw=2.2, label="PLCSIM (S7-1500, PID_Compact)")
    a1.plot(t, [r["model_flow"] for r in rows], color="#e67e22", lw=1.2, ls="--", label="Python model (Pid2Dof)")
    a1.axhline(56, color="#999", lw=0.6)
    a1.set_ylabel("flow temperature °C")
    a1.legend(loc="lower right", fontsize=8)
    a2.plot(t, [r["model_flow"] - r["flow"] for r in rows], color="#c0392b", lw=1.0)
    a2.axhline(0, color="#999", lw=0.6)
    a2.set_ylabel("model − PLCSIM  K")
    a3.plot(t, [r["valve"] for r in rows], color="#2c3e50", lw=2.2)
    a3.plot(t, [r["model_valve"] for r in rows], color="#e67e22", lw=1.2, ls="--")
    a3.set_ylabel("valve TV101 %")
    a3.set_xlabel("s after the outdoor step 0 → −10 °C (setpoint 44 → 56 °C)")
    fig.suptitle("Commissioning test 5: Python model against the PLCSIM trace, sample by sample", fontsize=11)
    fig.text(0.01, 0.005, f"{m['n']} samples at 1 s. Flow temperature: RMSE {m['flow_rmse']:.2f} K, largest "
             f"difference {m['flow_max']:.2f} K; peak {m['plcsim_peak']:.2f} °C (PLCSIM) vs {m['model_peak']:.2f} °C. "
             "Step time known to 1 s; middle of that second used.", fontsize=7, color="#555")
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(ROOT / "docs/plcsim_overlay.png", dpi=130)
    print(m)


if __name__ == "__main__":
    main()
