"""P4-01 host-side pan/tilt control with an in-memory output."""

import math
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class AxisConfig:
    minimum_deg: float
    neutral_deg: float
    maximum_deg: float
    direction: int
    gain_deg_s: float
    max_rate_deg_s: float

    def __post_init__(self):
        values = (
            self.minimum_deg,
            self.neutral_deg,
            self.maximum_deg,
            self.gain_deg_s,
            self.max_rate_deg_s,
        )
        if (
            not all(math.isfinite(value) for value in values)
            or not self.minimum_deg < self.neutral_deg < self.maximum_deg
            or self.direction not in (-1, 1)
            or self.gain_deg_s <= 0
            or self.max_rate_deg_s <= 0
        ):
            raise ValueError("Invalid simulated axis configuration")

    def step(self, target, error, dt):
        rate = self.direction * self.gain_deg_s * error
        rate = min(self.max_rate_deg_s, max(-self.max_rate_deg_s, rate))
        requested = target + rate * dt
        bounded = min(self.maximum_deg, max(self.minimum_deg, requested))
        return bounded, bounded != requested


@dataclass(frozen=True)
class PanTiltCommand:
    pan_deg: float
    tilt_deg: float
    state: str = "HOLD"
    saturated: bool = False


@dataclass
class MockTransport:
    """Keep the latest logical command in memory for inspection."""

    latest: PanTiltCommand

    def send(self, command):
        self.latest = command
        return command


class PanTiltController:
    def __init__(self, pan, tilt, *, dead_zone=0.05, max_frame_age_s=0.1, clock=None):
        if not 0 <= dead_zone < 1 or not math.isfinite(max_frame_age_s) or max_frame_age_s <= 0:
            raise ValueError("Invalid dead zone or observation age limit")
        self.pan, self.tilt = pan, tilt
        self.dead_zone, self.max_frame_age_s = dead_zone, max_frame_age_s
        self.transport = MockTransport(PanTiltCommand(pan.neutral_deg, tilt.neutral_deg))
        self._clock = clock
        self._lock = Lock()
        self._stopped = False
        self._last_frame_at = None
        self._previous_observation_at = None
        self._shape = None

    @classmethod
    def simulated(cls, *, clock=None):
        # Synthetic values for P4-01. Physical axis calibration is separate.
        return cls(
            AxisConfig(60, 90, 120, 1, 60, 30), AxisConfig(70, 90, 110, 1, 60, 30), clock=clock
        )

    def hold(self):
        with self._lock:
            return self._hold()

    def stop(self):
        """Latch HOLD for the rest of this session, including in-flight results."""
        with self._lock:
            self._stopped = True
            return self._hold()

    def _hold(self):
        self._previous_observation_at = None
        last = self.transport.latest
        if last.state == "HOLD":
            return last
        return self.transport.send(PanTiltCommand(last.pan_deg, last.tilt_deg))

    def _fresh(self, captured, now):
        return (
            math.isfinite(captured)
            and math.isfinite(now)
            and captured <= now <= captured + self.max_frame_age_s
        )

    def _expire(self, now):
        previous = self._previous_observation_at
        if now is not None and previous is not None and not self._fresh(previous, now):
            self._hold()

    def snapshot(self, now=None):
        """Service the live deadline even when the inference worker is busy."""
        with self._lock:
            self._expire(self._clock() if self._clock is not None else now)
            return self.transport.latest

    def update(self, captured, shape, box, *, now=None):
        """Consume one result. Without a live clock, use the ordered video timeline."""
        with self._lock:
            return self._update(
                captured, shape, box, self._clock() if self._clock is not None else now
            )

    def _update(self, captured, shape, box, now):
        if (
            self._stopped
            or not math.isfinite(captured)
            or (now is not None and not self._fresh(captured, now))
            or (self._last_frame_at is not None and captured <= self._last_frame_at)
        ):
            return self._hold()
        self._last_frame_at = captured
        if box is None:
            return self._hold()

        height, width = shape
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            return self._hold()

        self._expire(now)
        previous = self._previous_observation_at
        if self._shape != shape or previous is None:
            self._hold()
            self._shape = shape
            self._previous_observation_at = captured
            return self.transport.latest

        self._previous_observation_at = captured
        dt = captured - previous
        ex, ey = (x1 + x2) / width - 1, (y1 + y2) / height - 1
        last = self.transport.latest
        pan, pan_limited = self.pan.step(last.pan_deg, ex if abs(ex) > self.dead_zone else 0, dt)
        tilt, tilt_limited = self.tilt.step(
            last.tilt_deg, ey if abs(ey) > self.dead_zone else 0, dt
        )
        return self.transport.send(PanTiltCommand(pan, tilt, "TRACK", pan_limited or tilt_limited))
