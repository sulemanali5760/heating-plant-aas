"""Plant twin of the heating plant (Heizzentrale), ported 1:1 from the PLC source.

Source of every formula: heizzentrale-tia/src/Heizzentrale_Kern.scl and Heizzentrale_Netz.scl
  - heating_curve      <- FUNCTION "FC_Heizkurve"
  - MixingCircuit      <- FUNCTION_BLOCK "FB_StreckeVorlauf"
  - PumpRelease        <- FUNCTION_BLOCK "FB_Netzpumpe"
  - HeatingNetwork     <- FUNCTION_BLOCK "FB_ModellNetz"
Controller parameters: heizzentrale-tia/docs/Inbetriebnahmeprotokoll.md (TIC101: test 4, 25 Sep 2026;
PDIC301: PI after fine tuning, 2 Oct 2026).
See SOURCES.md for the tag of each constant.
"""
import random
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
    # Not in the PLC model (0 = off, the 1:1 port). See HeatingPlant.realistic().
    valve_runtime: float = 0.0    # s for 0 -> 100 %: a motorised valve moves at constant speed
    valve_pos: float | None = None  # % actual position (follows the command at that speed)
    _buf: list = field(default_factory=list)
    _w: int = 0

    def step(self, valve_pct: float, t_boiler: float, t_outdoor: float, dt: float,
             flow_ratio: float = 1.0) -> float:
        """flow_ratio = nominal flow / actual flow: the water needs that much longer through the pipe."""
        if dt <= 0 or self.time_constant <= 0:
            return self.t_flow
        size = BUFFER if self.valve_runtime <= 0 and flow_ratio == 1.0 else 4 * BUFFER
        if len(self._buf) != size:
            self._buf = [self._buf[self._w - 1] if self._buf else self.t_flow] * size
            self._w = 0
        # Load: the colder outside, the larger the spread. Return never below room temperature.
        spread = min(max(0.4 * (20.0 - t_outdoor), 2.0), 20.0)
        self.t_return = max(self.t_flow - spread, 20.0)
        command = min(max(valve_pct, 0.0), 100.0)
        if self.valve_runtime > 0:
            if self.valve_pos is None:
                self.valve_pos = command
            travel = 100.0 * dt / self.valve_runtime
            self.valve_pos += min(max(command - self.valve_pos, -travel), travel)
        else:
            self.valve_pos = command
        valve = self.valve_pos / 100.0
        t_mix = self.t_return + valve * (t_boiler - self.t_return)
        # Dead time via ring buffer
        self._buf[self._w] = t_mix
        delay = min(max(round(self.dead_time * flow_ratio / dt), 0), size - 1)
        r = (self._w - delay) % size
        self._w = (self._w + 1) % size
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

    def reset(self) -> None:
        """PID_Compact Reset input: output 0, integral cleared."""
        self.integral, self._d, self._d_in_prev, self.output = 0.0, 0.0, None, 0.0

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
class PumpRelease:
    """When may network pump P301 run? Same logic as FB_Netzpumpe (control, not closed-loop)."""
    heating_limit: float = 15.0   # °C, heating demand below this
    frost_limit: float = 3.0      # °C, pump always runs below this
    hysteresis: float = 1.0       # K
    demand: bool = False
    frost: bool = False

    def step(self, t_outdoor: float, plant_released: bool, drive_ok: bool) -> bool:
        if t_outdoor < self.heating_limit:
            self.demand = True
        elif t_outdoor > self.heating_limit + self.hysteresis:
            self.demand = False
        if t_outdoor < self.frost_limit:
            self.frost = True
        elif t_outdoor > self.frost_limit + self.hysteresis:
            self.frost = False
        return (self.demand or self.frost) and plant_released and drive_ok


@dataclass
class HeatingNetwork:
    """Pump with quadratic curve against pipes and consumers. Same equations as FB_ModellNetz:
        pump: dp = dp0*(n/100)^2 - k_pump*Q^2   (affinity law, H ~ n^2)
        pipes: dp = k_pipe*Q^2 ;  consumers: dp = Q^2/Kv^2, Kv = Kv_max*load/100
        operating point: dp0*(n/100)^2 = Q^2*(k_pump + k_pipe + 1/Kv^2); measured dp across consumers.
    PT1 for the drive ramp (speed) and the hydraulics (pressure)."""
    dp0: float = 2.2          # bar, shut-off head at 100 %
    k_pump: float = 0.00311   # bar/(m³/h)²
    k_pipe: float = 0.00133   # bar/(m³/h)²
    kv_max: float = 13.7      # m³/h at 1 bar, all consumer valves open
    t_speed: float = 2.0      # s, drive ramp as PT1
    t_pressure: float = 1.0   # s
    speed: float = 0.0        # % actual
    dp: float = 0.0           # bar, "measured" PDT301
    flow: float = 0.0         # m³/h

    def step(self, speed_set: float, load_pct: float, pump_on: bool, dt: float) -> None:
        n_set = min(max(speed_set, 0.0), 100.0) if pump_on else 0.0
        self.speed += (n_set - self.speed) * dt / self.t_speed
        s = self.speed / 100.0
        kv = self.kv_max * min(max(load_pct, 1.0), 100.0) / 100.0
        q2 = self.dp0 * s * s / (self.k_pump + self.k_pipe + 1.0 / (kv * kv))
        self.dp += (q2 / (kv * kv) - self.dp) * dt / self.t_pressure
        self.flow = q2 ** 0.5


@dataclass
class PumpLoop:
    """Loop PDIC301: release -> PI on differential pressure -> drive speed -> network.
    PI, not PID: chosen in the PLC project because a real dp signal is noisy and the D part would pass
    the noise to the drive (commissioning log, 2 Oct 2026)."""
    dp_set: float = 1.0           # bar, DB_Netz.dpSoll
    load: float = 50.0            # %, consumer valve opening, DB_Netz.lastVerbraucher
    plant_released: bool = True  # emergency heating switch K0 not pressed (Anlage_Frei)
    drive_ok: bool = True         # no drive fault (FU_Stoerung, normally closed)
    release: PumpRelease = field(default_factory=PumpRelease)
    network: HeatingNetwork = field(default_factory=HeatingNetwork)
    pid: Pid2Dof = field(default_factory=lambda: Pid2Dof(kp=119.3, ti=2.72, td=0.0, b=0.8, c=0.0, ts=0.1))
    pump_on: bool = False
    speed_set: float = 0.0

    def step(self, t_outdoor: float, dt: float) -> None:
        self.pump_on = self.release.step(t_outdoor, self.plant_released, self.drive_ok)
        if self.pump_on:
            self.speed_set = self.pid.update(self.dp_set, self.network.dp, dt)
        else:
            self.pid.reset()
            self.speed_set = 0.0
        self.network.step(self.speed_set, self.load, self.pump_on, dt)


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
    pump: PumpLoop = field(default_factory=PumpLoop)
    t_flow_set: float = 0.0
    valve: float = 0.0
    time: float = 0.0
    # Effects a real plant has and the PLC model does not (all off by default = 1:1 port):
    sensor_noise: float = 0.0        # K, standard deviation of the flow temperature measurement
    nominal_flow: float = 0.0        # m³/h at which dead_time holds; 0 = dead time independent of flow
    t_flow_measured: float = 0.0     # what the controller sees (TIC101.PV)
    _rng: random.Random = field(default_factory=lambda: random.Random(0))

    @classmethod
    def realistic(cls, **kw) -> "HeatingPlant":
        """Same plant with a valve actuator that needs 90 s for full travel, a noisy temperature
        measurement (0.05 K) and a pipe dead time that follows the network flow (10 s at 6.85 m³/h,
        the flow at 50 % load). Values and their origin: SOURCES.md, 'Realistic mode'."""
        p = cls(sensor_noise=0.05, nominal_flow=6.85, **kw)
        p.circuit.valve_runtime = 90.0
        return p

    def step(self) -> None:
        self.t_flow_set = heating_curve(self.t_outdoor, self.t_room_set, self.slope, self.level,
                                        self.t_flow_min, self.t_flow_max)
        noise = self._rng.gauss(0.0, self.sensor_noise) if self.sensor_noise > 0 else 0.0
        self.t_flow_measured = self.circuit.t_flow + noise
        if self.auto:
            self.valve = self.pid.update(self.t_flow_set, self.t_flow_measured, self.cycle)
        else:
            self.valve = self.manual_valve
        self.pump.step(self.t_outdoor, self.cycle)
        ratio = 1.0
        if self.nominal_flow > 0:  # standing water (pump off) is treated as the slowest flow the buffer holds
            ratio = self.nominal_flow / max(self.pump.network.flow, self.nominal_flow / 7.9)
        self.circuit.step(self.valve, self.t_boiler, self.t_outdoor, self.cycle, ratio)
        self.time += self.cycle

    def settle(self) -> None:
        """Put the plant at its steady state for the current outdoor temperature (warm start)."""
        w = heating_curve(self.t_outdoor, self.t_room_set, self.slope, self.level,
                          self.t_flow_min, self.t_flow_max)
        v = steady_valve(self.t_outdoor, w, self.t_boiler)
        self.circuit = MixingCircuit(time_constant=self.circuit.time_constant,
                                     dead_time=self.circuit.dead_time, t_flow=w,
                                     valve_runtime=self.circuit.valve_runtime, valve_pos=v)
        self.circuit._buf = [w] * BUFFER
        self.pid.preset(w, w, v)
        self.t_flow_set, self.valve, self.t_flow_measured = w, v, w
        if self.nominal_flow > 0:  # pump loop at its operating point too, so the dead time starts right
            for _ in range(600):
                self.pump.step(self.t_outdoor, self.cycle)

    def run(self, seconds: float):
        n = round(seconds / self.cycle)
        for _ in range(n):
            self.step()
            yield self.time, self.t_outdoor, self.t_flow_set, self.circuit.t_flow, self.valve, self.circuit.t_return
