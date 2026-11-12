"""Compare the Python plant model with the PLCSIM trace of commissioning test 5 (25 Sep 2026).

Trace: data/plcsim/Trace1_test5_setpoint_step_2026-09-25.csv, exported from TIA Portal V19
(Traces -> Measurements -> export CSV), 1 s sampling, 528 samples. At sample 222 the outdoor
temperature is switched 0 -> -10 degC, so the setpoint steps 44 -> 56 degC.

What this comparison can and cannot show: the plant in PLCSIM is the author's own SCL model with the
same equations as model.py, so agreement checks (a) that the port is correct and (b) that Pid2Dof
behaves like Siemens PID_Compact. It says nothing about a real heating plant.
"""
import csv
import hashlib
import pathlib

from heating_plant.model import HeatingPlant

TRACE = pathlib.Path(__file__).resolve().parents[1] / "data/plcsim/Trace1_test5_setpoint_step_2026-09-25.csv"
SHA256 = "b7e485d5be71f16aadb4e71b815c557313f18bd857fbd5c6a9e7d020477f0b0c"
STEP_SAMPLE = 222   # first sample with tAussen = -10
# The trace has 1 s resolution, so the step happened somewhere in the second before sample 222.
# The comparison uses the middle of that second; it is not fitted to make the result look better.
STEP_OFFSET = 0.5


def load_trace() -> list[dict]:
    """Samples from the step on: t (s after the first sample at -10 degC), flow, valve, return (PLCSIM)."""
    if hashlib.sha256(TRACE.read_bytes()).hexdigest() != SHA256:
        raise SystemExit("PLCSIM trace file changed")
    with TRACE.open(encoding="utf-8-sig", newline="") as f:
        rows = [[float(x) for x in r] for r in list(csv.reader(f))[1:]]
    t0 = rows[STEP_SAMPLE][1]
    return [{"t": (r[1] - t0) / 1000.0, "flow": r[4], "valve": r[5], "return": r[6]} for r in rows[STEP_SAMPLE:]]


def compare(offset: float = STEP_OFFSET) -> list[dict]:
    """Each PLCSIM sample next to the model value at the same time after the step."""
    p = HeatingPlant(t_outdoor=0.0)
    p.settle()
    for _ in p.run(60):
        pass
    p.t_outdoor = -10.0
    trace = load_trace()
    hist = [(p.circuit.t_flow, p.valve, p.circuit.t_return)]
    for _ in range(round((trace[-1]["t"] + offset + 1) / p.cycle)):
        p.step()
        hist.append((p.circuit.t_flow, p.valve, p.circuit.t_return))
    out = []
    for s in trace:
        flow, valve, ret = hist[round((s["t"] + offset) / p.cycle)]
        out.append({**s, "model_flow": flow, "model_valve": valve, "model_return": ret})
    return out


def metrics(rows: list[dict]) -> dict:
    def rmse(a, b):
        return (sum((r[a] - r[b]) ** 2 for r in rows) / len(rows)) ** 0.5
    return {"n": len(rows), "flow_rmse": rmse("flow", "model_flow"),
            "flow_max": max(abs(r["flow"] - r["model_flow"]) for r in rows),
            "valve_rmse": rmse("valve", "model_valve"),
            "plcsim_peak": max(r["flow"] for r in rows), "model_peak": max(r["model_flow"] for r in rows)}
