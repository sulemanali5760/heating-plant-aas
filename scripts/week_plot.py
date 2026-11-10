"""Run the twin through the real Magdeburg week (DWD, 12-18 Dec 2022) and plot it.

Outdoor temperature: hourly DWD values, held constant within each hour (no invented
interpolation). Plant and controller: heating_plant.model, 100 ms cycle like OB30.
Output: docs/week_magdeburg_2022-12.png and docs/week_magdeburg_2022-12.csv (1 sample/min).
"""
import csv
import datetime as dt
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from heating_plant.model import HeatingPlant
from heating_plant.opcua_server import DWD_FILE

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs"


def main() -> None:
    with DWD_FILE.open() as f:
        hours = [(dt.datetime.fromisoformat(r["time_utc"]), float(r["t_outdoor_degC"])) for r in csv.DictReader(f)]
    p = HeatingPlant(t_outdoor=hours[0][1])
    p.settle()
    rows = []
    steps_per_hour = round(3600 / p.cycle)
    for h, (t0, ta) in enumerate(hours):
        p.t_outdoor = ta
        for k in range(steps_per_hour):
            p.step()
            if k % 600 == 0:  # once per plant minute
                rows.append((t0 + dt.timedelta(seconds=k * p.cycle), ta, p.t_flow_set, p.circuit.t_flow,
                             p.valve, p.circuit.t_return))
    OUT.mkdir(exist_ok=True)
    with (OUT / "week_magdeburg_2022-12.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_utc", "t_outdoor", "t_flow_set", "t_flow", "valve_pct", "t_return"])
        w.writerows((r[0].isoformat(), *(round(x, 3) for x in r[1:])) for r in rows)
    err = max(abs(r[2] - r[3]) for r in rows[60:])
    t = [r[0] for r in rows]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 6.2), sharex=True, height_ratios=[2, 1])
    a1.plot(t, [r[2] for r in rows], label="TIC101 setpoint (heating curve)", lw=1.6, color="#c0392b")
    a1.plot(t, [r[3] for r in rows], label="TIC101 flow temperature", lw=1.0, color="#2c3e50", ls="--")
    a1.plot(t, [r[5] for r in rows], label="TI102 return temperature", lw=1.0, color="#7f8c8d")
    a1.set_ylabel("°C")
    a1.set_ylim(top=max(r[2] for r in rows) + 7)
    a1b = a1.twinx()
    a1b.plot(t, [r[1] for r in rows], label="TI103 outdoor (DWD Magdeburg)", lw=1.2, color="#2980b9")
    a1b.set_ylabel("outdoor °C", color="#2980b9")
    a1b.set_ylim(top=max(r[1] for r in rows) + 4)
    h1, l1 = a1.get_legend_handles_labels()
    h2, l2 = a1b.get_legend_handles_labels()
    a1.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8, ncol=2)
    a2.plot(t, [r[4] for r in rows], color="#27ae60", lw=1.0)
    a2.set_ylabel("TV101 valve %")
    a2.set_xlabel("UTC")
    fig.suptitle("Heating-plant twin on the real coldest week 2022 in Magdeburg (DWD station 03126)", fontsize=11)
    fig.text(0.01, 0.005, "Data: Deutscher Wetterdienst, CC BY 4.0. Controller: PID parameters from PLCSIM "
             f"commissioning. Max |setpoint - flow| {err:.2f} K, only right after the hourly outdoor steps.", fontsize=7, color="#555")
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(OUT / "week_magdeburg_2022-12.png", dpi=130)
    print(f"{len(rows)} samples, max tracking error {err:.3f} K, valve "
          f"{min(r[4] for r in rows):.1f}-{max(r[4] for r in rows):.1f} %")


if __name__ == "__main__":
    main()
