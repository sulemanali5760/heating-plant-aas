"""Plant twin of the heating plant (Heizzentrale), ported 1:1 from the PLC source.

Source of every formula: heizzentrale-tia/src/Heizzentrale_Kern.scl
  - heating_curve      <- FUNCTION "FC_Heizkurve"
  - MixingCircuit      <- FUNCTION_BLOCK "FB_StreckeVorlauf"
Controller parameters: heizzentrale-tia/docs/Inbetriebnahmeprotokoll.md, fine tuning (test 4).
See SOURCES.md for the tag of each constant.
"""
from dataclasses import dataclass, field


def heating_curve(t_outdoor: float, t_room_set: float = 20.0, slope: float = 1.2,
                  level: float = 0.0, t_flow_min: float = 25.0, t_flow_max: float = 75.0) -> float:
    """Linear heating curve, limited. Same formula as FC_Heizkurve:
    T_flow_set = T_room_set + level + slope * (T_room_set - T_outdoor), limited to [min, max]."""
    raw = t_room_set + level + slope * (t_room_set - t_outdoor)
    return min(max(raw, t_flow_min), t_flow_max)


BUFFER = 200  # dead-time ring buffer slots, as in the SCL (Array[0..199])


@dataclass
class MixingCircuit:
    """3-way mixing valve + pipe dead time + PT1 storage mass. Same steps as FB_StreckeVorlauf."""
    time_constant: float = 60.0   # s, PT1
    dead_time: float = 10.0       # s
    t_flow: float = 20.0          # PT1 state, starts at 20 °C like the SCL
    t_return: float = 20.0
    _buf: list = field(default_factory=list)
    _w: int = 0

    def step(self, valve_pct: float, t_boiler: float, t_outdoor: float, dt: float) -> float:
        if dt <= 0 or self.time_constant <= 0:
            return self.t_flow
        if not self._buf:
            self._buf = [self.t_flow] * BUFFER
        # Load: the colder outside, the larger the spread. Return never below room temperature.
        spread = min(max(0.4 * (20.0 - t_outdoor), 2.0), 20.0)
        self.t_return = max(self.t_flow - spread, 20.0)
        valve = min(max(valve_pct, 0.0), 100.0) / 100.0
        t_mix = self.t_return + valve * (t_boiler - self.t_return)
        # Dead time via ring buffer
        self._buf[self._w] = t_mix
        delay = min(max(round(self.dead_time / dt), 0), BUFFER - 1)
        r = (self._w - delay) % BUFFER
        self._w = (self._w + 1) % BUFFER
        # PT1, explicit Euler
        self.t_flow += (self._buf[r] - self.t_flow) * dt / self.time_constant
        return self.t_flow


@dataclass
class Pid2Dof:
    """2-DOF PIDT1 in the parallel form documented for PID_Compact:
        y = Kp * [ (b*w - x) + 1/(Ti*s) * (w - x) + Td*s/(a*Td*s + 1) * (c*w - x) ]
    with output limits and conditional-integration anti-windup.
    PID_Compact's exact internals are Siemens-specific, so dynamic results are
    compared to the PLCSIM measurements with a tolerance, not claimed identical."""
    kp: float = 20.66
    ti: float = 20.17
    td: float = 5.11
    a: float = 0.1
    b: float = 0.257
    c: float = 0.0
    ts: float = 0.4          # PID algorithm sample time, s
    out_min: float = 0.0
    out_max: float = 100.0
    integral: float = 0.0
    _d: float = 0.0
    _d_in_prev: float | None = None
    _t: float = 0.0
    output: float = 0.0

    def preset(self, w: float, x: float, y: float) -> None:
        """Bumpless start at a known operating point (manual -> automatic)."""
        self.integral = y - self.kp * (self.b * w - x)
        self._d_in_prev = self.c * w - x
        self._d = 0.0
        self.output = y

    def update(self, w: float, x: float, dt: float) -> float:
        self._t += dt
        if self._t + 1e-9 < self.ts:
            return self.output
        h, self._t = self._t, 0.0
        p = self.kp * (self.b * w - x)
        d_in = self.c * w - x
        if self._d_in_prev is None:
            self._d_in_prev = d_in
        tf = self.a * self.td
        # Backward-Euler discretisation of Td*s/(a*Td*s + 1)
        self._d = (tf * self._d + self.kp * self.td * (d_in - self._d_in_prev)) / (tf + h)
        self._d_in_prev = d_in
        i_step = self.kp * h / self.ti * (w - x)
        y = p + self.integral + i_step + self._d
        # Anti-windup: only integrate when not pushing further into a limit
        if not ((y > self.out_max and i_step > 0) or (y < self.out_min and i_step < 0)):
            self.integral += i_step
        self.output = min(max(p + self.integral + self._d, self.out_min), self.out_max)
        return self.output


def steady_valve(t_outdoor: float, t_flow: float, t_boiler: float = 75.0) -> float:
    """Valve position the plant needs at steady state (hand calculation).
    From t_flow = t_ret + v*(t_boiler - t_ret) and t_ret = t_flow - spread:
        v = spread / (t_boiler - t_flow + spread)"""
    spread = min(max(0.4 * (20.0 - t_outdoor), 2.0), 20.0)
    return 100.0 * spread / (t_boiler - t_flow + spread)


@dataclass
class HeatingPlant:
    """Loop TIC101: heating curve -> PID -> mixing valve -> flow temperature. Cycle 100 ms like OB30."""
    t_outdoor: float = 0.0
    t_boiler: float = 75.0
    t_room_set: float = 20.0
    slope: float = 1.2
    level: float = 0.0
    t_flow_min: float = 25.0
    t_flow_max: float = 75.0
    auto: bool = True
    manual_valve: float = 0.0
    cycle: float = 0.1
    circuit: MixingCircuit = field(default_factory=MixingCircuit)
    pid: Pid2Dof = field(default_factory=Pid2Dof)
    t_flow_set: float = 0.0
    valve: float = 0.0
    time: float = 0.0

    def step(self) -> None:
        self.t_flow_set = heating_curve(self.t_outdoor, self.t_room_set, self.slope, self.level,
                                        self.t_flow_min, self.t_flow_max)
        if self.auto:
            self.valve = self.pid.update(self.t_flow_set, self.circuit.t_flow, self.cycle)
        else:
            self.valve = self.manual_valve
        self.circuit.step(self.valve, self.t_boiler, self.t_outdoor, self.cycle)
        self.time += self.cycle

    def settle(self) -> None:
        """Put the plant at its steady state for the current outdoor temperature (warm start)."""
        w = heating_curve(self.t_outdoor, self.t_room_set, self.slope, self.level,
                          self.t_flow_min, self.t_flow_max)
        v = steady_valve(self.t_outdoor, w, self.t_boiler)
        self.circuit = MixingCircuit(time_constant=self.circuit.time_constant,
                                     dead_time=self.circuit.dead_time, t_flow=w)
        self.circuit._buf = [w] * BUFFER
        self.pid.preset(w, w, v)
        self.t_flow_set, self.valve = w, v

    def run(self, seconds: float):
        n = round(seconds / self.cycle)
        for _ in range(n):
            self.step()
            yield self.time, self.t_outdoor, self.t_flow_set, self.circuit.t_flow, self.valve, self.circuit.t_return
