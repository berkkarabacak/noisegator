"""Noise-gate DSP: attack/release, fade, voice-clarity filter.

Mirrors NoiseGator 0.63 behaviour (AttackReleaseTimer + NoiseGateProcessor
+ Filters) with a streaming-friendly implementation.
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Deque, Optional, Tuple

import numpy as np

SAMPLE_RATE = 44100
CHANNELS = 2
BIT_DEPTH = 16
BUFFER_FRAMES = 512


def compute_level(samples: np.ndarray) -> float:
    """Map a block to the original 0.63 'level' scale (dBFS + 91)."""
    if samples.size == 0:
        return 1.0
    x = np.asarray(samples, dtype=np.float64).ravel()
    rms = float(np.sqrt(np.mean(np.square(x))))
    if rms < 1e-12:
        db = -90.0
    else:
        db = 20.0 * math.log10(rms)
        if not math.isfinite(db):
            db = -90.0
    return max(0.0, db + 91.0)


def compute_dbfs(samples: np.ndarray) -> float:
    if samples.size == 0:
        return -90.0
    x = np.asarray(samples, dtype=np.float64).ravel()
    rms = float(np.sqrt(np.mean(np.square(x))))
    if rms < 1e-12:
        return -90.0
    db = 20.0 * math.log10(rms)
    return db if math.isfinite(db) else -90.0


class Biquad:
    """Direct-form I biquad, stereo-capable (independent channel state)."""

    def __init__(self, b0: float, b1: float, b2: float, a1: float, a2: float, channels: int = 2):
        self.b0, self.b1, self.b2 = b0, b1, b2
        self.a1, self.a2 = a1, a2
        self.x1 = np.zeros(channels, dtype=np.float64)
        self.x2 = np.zeros(channels, dtype=np.float64)
        self.y1 = np.zeros(channels, dtype=np.float64)
        self.y2 = np.zeros(channels, dtype=np.float64)

    def process(self, x: np.ndarray) -> np.ndarray:
        # x: (frames, channels)
        frames, ch = x.shape
        y = np.empty_like(x, dtype=np.float64)
        b0, b1, b2, a1, a2 = self.b0, self.b1, self.b2, self.a1, self.a2
        for c in range(ch):
            x1, x2, y1, y2 = self.x1[c], self.x2[c], self.y1[c], self.y2[c]
            xc = x[:, c]
            yc = y[:, c]
            for i in range(frames):
                xi = float(xc[i])
                yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
                x2, x1 = x1, xi
                y2, y1 = y1, yi
                yc[i] = yi
            self.x1[c], self.x2[c], self.y1[c], self.y2[c] = x1, x2, y1, y2
        return y.astype(np.float32, copy=False)

    def reset(self) -> None:
        self.x1.fill(0)
        self.x2.fill(0)
        self.y1.fill(0)
        self.y2.fill(0)


def _highpass(fc: float, fs: float, q: float = 0.707) -> Tuple[float, float, float, float, float]:
    w0 = 2.0 * math.pi * fc / fs
    alpha = math.sin(w0) / (2.0 * q)
    cosw = math.cos(w0)
    b0 = (1.0 + cosw) / 2.0
    b1 = -(1.0 + cosw)
    b2 = (1.0 + cosw) / 2.0
    a0 = 1.0 + alpha
    a1 = -2.0 * cosw
    a2 = 1.0 - alpha
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


def _lowpass(fc: float, fs: float, q: float = 0.707) -> Tuple[float, float, float, float, float]:
    w0 = 2.0 * math.pi * fc / fs
    alpha = math.sin(w0) / (2.0 * q)
    cosw = math.cos(w0)
    b0 = (1.0 - cosw) / 2.0
    b1 = 1.0 - cosw
    b2 = (1.0 - cosw) / 2.0
    a0 = 1.0 + alpha
    a1 = -2.0 * cosw
    a2 = 1.0 - alpha
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


class VoiceClarityFilter:
    """High-pass 40 Hz, low-pass 1500 Hz, plus slow adaptive gain (0.63 Filters)."""

    def __init__(self, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS):
        self.hp = Biquad(*_highpass(40.0, sample_rate), channels=channels)
        self.lp = Biquad(*_lowpass(1500.0, sample_rate), channels=channels)
        self.current_gain = 0.0
        self.db_levels: list[float] = []
        self.gain_attack_ms = 30.0
        self.last_sample = time.monotonic()

    def reset(self) -> None:
        self.hp.reset()
        self.lp.reset()
        self.current_gain = 0.0
        self.db_levels.clear()
        self.last_sample = time.monotonic()

    def process(self, block: np.ndarray) -> np.ndarray:
        y = self.lp.process(block.astype(np.float32, copy=False))
        y = self.hp.process(y)
        db = compute_dbfs(y)
        self._adapt_gain(db)
        # currentGain in the original is used as a linear multiplier (GainProcessor)
        gain = max(0.0, self.current_gain)
        if gain != 1.0:
            y = y * np.float32(gain if gain > 0.0 else 0.0)
        return y

    def _adapt_gain(self, db: float) -> None:
        now = time.monotonic()
        self.db_levels.append(db)
        if (now - self.last_sample) * 1000.0 < self.gain_attack_ms:
            return
        avg = sum(self.db_levels) / max(1, len(self.db_levels))
        g = self.current_gain
        if avg < -28.0 and g < 5.0:
            g += 0.1
        elif avg > -8.0 and g > 0.5:
            g -= 0.1
        elif g < 0.0:
            g += 0.1
        elif g > 0.0:
            g -= 0.1
        self.current_gain = g
        self.db_levels.clear()
        self.last_sample = now


class AttackReleaseGate:
    """Rolling-window equivalent of AttackReleaseTimer, driven in dBFS.

    Closed: average dBFS over `attack_ms` must stay >= threshold to open.
    Open: remain open while the release-window average stays at or above
    (threshold − hysteresis); close and fade out when it drops below that.
    """

    def __init__(
        self,
        attack_ms: int = 30,
        release_ms: int = 1000,
        threshold: float = -32.0,
        hysteresis: float = 5.0,
    ):
        self.attack_ms = int(attack_ms)
        self.release_ms = int(release_ms)
        self.threshold = float(threshold)
        self.hysteresis = float(hysteresis)
        self.open = False
        self.fade_out = True
        self.current_level = -90.0
        self._hist: Deque[Tuple[float, float]] = deque()
        now = time.monotonic()
        self._attack_t0 = now
        self._release_t0 = now

    def reset(self) -> None:
        self.open = False
        self.fade_out = True
        self.current_level = -90.0
        self._hist.clear()
        now = time.monotonic()
        self._attack_t0 = now
        self._release_t0 = now

    def _trim(self, now: float, window_s: float) -> None:
        cutoff = now - max(window_s, 0.002)
        while self._hist and self._hist[0][0] < cutoff:
            self._hist.popleft()

    def _average(self, now: float, window_ms: int) -> float:
        window_s = max(window_ms, 1) / 1000.0
        self._trim(now, window_s)
        if not self._hist:
            return self.current_level
        return sum(v for _, v in self._hist) / len(self._hist)

    def update(self, level: float, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        self.current_level = float(level)
        self._hist.append((now, self.current_level))

        if not self.open:
            avg = self._average(now, self.attack_ms)
            if (now - self._attack_t0) * 1000.0 >= self.attack_ms:
                if avg >= self.threshold:
                    self.open = True
                    self.fade_out = False
                    self._release_t0 = now
                else:
                    self.open = False
        else:
            avg = self._average(now, self.release_ms)
            if (now - self._release_t0) * 1000.0 >= self.release_ms:
                close_at = self.threshold - self.hysteresis
                if avg < close_at:
                    self.open = False
                    self.fade_out = True
                    self._attack_t0 = now
                else:
                    self.open = True
                    self._release_t0 = now
        return self.open


class NoiseGateEngine:
    """Per-block processor: level → gate → optional voice filter → fade/boost."""

    def __init__(self) -> None:
        self.gate = AttackReleaseGate()
        self.voice = VoiceClarityFilter()
        self.voice_filter_enabled = False
        self.muted = False
        self.user_volume = 0  # dB, -15 .. +5
        self.echo_back = 50  # percent 0..100, local monitor / hear-yourself
        self.gain_db = -80.0
        self.sample_rate = SAMPLE_RATE
        self._last_input = np.zeros((BUFFER_FRAMES, CHANNELS), dtype=np.float32)
        self._last_output = np.zeros((BUFFER_FRAMES, CHANNELS), dtype=np.float32)
        self._last_monitor = np.zeros((BUFFER_FRAMES, CHANNELS), dtype=np.float32)
        self._env = deque([0.0] * 160, maxlen=160)
        self.input_meter = 0.0
        self.output_meter = 0.0
        self.gate_meter = 0.0

    def configure(
        self,
        threshold: Optional[float] = None,
        hysteresis: Optional[float] = None,
        attack_ms: Optional[int] = None,
        release_ms: Optional[int] = None,
        volume: Optional[int] = None,
        voice_filter: Optional[bool] = None,
        muted: Optional[bool] = None,
        echo_back: Optional[int] = None,
    ) -> None:
        if threshold is not None:
            self.gate.threshold = max(-60.0, min(0.0, float(threshold)))
        if hysteresis is not None:
            self.gate.hysteresis = max(1.0, min(12.0, float(hysteresis)))
        if attack_ms is not None:
            self.gate.attack_ms = max(3, int(attack_ms))
        if release_ms is not None:
            self.gate.release_ms = max(10, int(release_ms))
        if volume is not None:
            self.user_volume = int(max(-15, min(5, volume)))
        if voice_filter is not None:
            self.voice_filter_enabled = bool(voice_filter)
        if muted is not None:
            self.set_mute(bool(muted))
        if echo_back is not None:
            self.echo_back = int(max(0, min(100, echo_back)))

    def set_mute(self, mute: bool) -> None:
        self.muted = mute
        if mute:
            self.gate.open = False
            self.gate.fade_out = True

    def reset(self) -> None:
        self.gate.reset()
        self.voice.reset()
        self.gain_db = -80.0
        self.input_meter = 0.0
        self.output_meter = 0.0
        self.gate_meter = 0.0

    def _step_gain(self) -> float:
        """Linear gate envelope (0..~1), independent of the send volume boost."""
        passing = self.gate.open and not self.muted
        if passing:
            if self.gain_db < 0.0:
                if self.gain_db < -20.0:
                    self.gain_db = -20.0
                self.gain_db = min(0.0, self.gain_db + 1.0)
            else:
                self.gain_db = 0.0
        else:
            if self.gate.fade_out or self.muted or not self.gate.open:
                if self.gain_db > -70.0:
                    self.gain_db -= 2.4  # ~similar close time to 0.1 dB / 128-frame buf
                if self.gain_db <= -69.9:
                    self.gain_db = -80.0
                    self.gate.fade_out = False
        if self.gain_db <= -80.0:
            return 0.0
        return 10.0 ** (self.gain_db / 20.0)

    def process(self, indata: np.ndarray) -> np.ndarray:
        if indata.ndim == 1:
            stereo = np.column_stack((indata, indata))
        elif indata.shape[1] == 1:
            stereo = np.repeat(indata, 2, axis=1)
        else:
            stereo = indata[:, :2]

        mono = stereo.mean(axis=1)
        level_db = compute_dbfs(mono)
        if not self.muted:
            self.gate.update(level_db)
        else:
            self.gate.current_level = level_db

        audio = stereo.astype(np.float32, copy=True)
        if self.voice_filter_enabled:
            audio = self.voice.process(audio)

        env = self._step_gain()
        gated = audio * np.float32(env)
        vol_lin = 10.0 ** (self.user_volume / 20.0)
        out = gated * np.float32(vol_lin)
        np.clip(out, -1.0, 1.0, out=out)
        monitor = gated * np.float32(self.echo_back / 100.0)
        np.clip(monitor, -1.0, 1.0, out=monitor)
        self._last_monitor = monitor

        self._last_input = stereo.astype(np.float32, copy=False)
        self._last_output = out
        # scroll a few envelope bins per block so the chart reads as live
        mono_abs = np.abs(mono)
        n = 4
        step = max(1, mono_abs.size // n)
        for i in range(n):
            sl = mono_abs[i * step : (i + 1) * step]
            self._env.append(float(np.max(sl)) if sl.size else 0.0)
        # meters 0..1 from dBFS, mapped −60 … 0
        self.input_meter = min(1.0, max(0.0, (level_db + 60.0) / 60.0))
        out_db = compute_dbfs(out.mean(axis=1))
        self.output_meter = min(1.0, max(0.0, (out_db + 60.0) / 60.0))
        self.gate_meter = (1.0 if self.gate.open and not self.muted else 0.0) * max(
            0.12, self.input_meter
        )
        if self.muted:
            self.gate_meter = 0.0
        return out

    def waveform_peaks(self, bins: int = 160) -> list[float]:
        data = list(self._env)
        if len(data) < bins:
            data = [0.0] * (bins - len(data)) + data
        elif len(data) > bins:
            data = data[-bins:]
        return data
