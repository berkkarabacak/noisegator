"""Device enumeration and PortAudio streaming (sounddevice).

Falls back to a demo oscillator / speech envelope when no devices are
present or when --demo is set.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

from gate import BUFFER_FRAMES, CHANNELS, SAMPLE_RATE, NoiseGateEngine

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - optional on headless boxes
    sd = None  # type: ignore


@dataclass
class AudioDevice:
    index: int
    name: str
    hostapi: str
    max_input: int
    max_output: int
    default_sr: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "hostapi": self.hostapi,
            "max_input": self.max_input,
            "max_output": self.max_output,
            "default_sr": self.default_sr,
        }


MOCK_INPUTS = [
    AudioDevice(-1, "Built-in Microphone", "demo", 2, 0, 44100),
    AudioDevice(-2, "USB Condenser Mic", "demo", 1, 0, 44100),
    AudioDevice(-3, "Headset Microphone", "demo", 1, 0, 44100),
]
MOCK_OUTPUTS = [
    AudioDevice(-11, "VB-Cable Input (Virtual)", "demo", 0, 2, 44100),
    AudioDevice(-12, "Built-in Speakers", "demo", 0, 2, 44100),
    AudioDevice(-13, "Headphones", "demo", 0, 2, 44100),
]


def list_devices() -> tuple[list[AudioDevice], list[AudioDevice], bool]:
    """Return (inputs, outputs, live). live=False means mock list."""
    if sd is None:
        return list(MOCK_INPUTS), list(MOCK_OUTPUTS), False
    try:
        hostapis = sd.query_hostapis()
        devices = sd.query_devices()
    except Exception:
        return list(MOCK_INPUTS), list(MOCK_OUTPUTS), False

    inputs: list[AudioDevice] = []
    outputs: list[AudioDevice] = []
    for i, d in enumerate(devices):
        try:
            ha = hostapis[d["hostapi"]]["name"]
        except Exception:
            ha = "unknown"
        dev = AudioDevice(
            index=i,
            name=str(d["name"]),
            hostapi=str(ha),
            max_input=int(d.get("max_input_channels", 0)),
            max_output=int(d.get("max_output_channels", 0)),
            default_sr=float(d.get("default_samplerate", 44100)),
        )
        if dev.max_input > 0:
            inputs.append(dev)
        if dev.max_output > 0:
            outputs.append(dev)
    if not inputs and not outputs:
        return list(MOCK_INPUTS), list(MOCK_OUTPUTS), False
    return inputs, outputs, True


class DemoTalker:
    """Speech-like envelope: bursts of voiced noise + formants, then rest."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sr = sample_rate
        self.phase = 0.0
        self.t = 0.0
        # Start mid-phrase so the first screenshot is alive
        self.cycle = 0.35
        self.talk_len = 0.85
        self.rest_len = 1.15
        rng = np.random.default_rng(7)
        self._rng = rng

    def block(self, frames: int) -> np.ndarray:
        n = frames
        t0 = self.t
        tt = t0 + np.arange(n, dtype=np.float64) / self.sr
        self.t = t0 + n / self.sr

        env = np.zeros(n, dtype=np.float64)
        for i, t in enumerate(tt):
            cyc = (t + self.cycle) % (self.talk_len + self.rest_len)
            if cyc < self.talk_len:
                # raised-cosine burst with syllable wobble
                u = cyc / self.talk_len
                gate = math.sin(math.pi * u) ** 1.2
                syll = 0.65 + 0.35 * math.sin(2 * math.pi * 6.5 * t)
                env[i] = gate * syll
            else:
                env[i] = 0.02 * math.sin(2 * math.pi * 0.4 * t) ** 2

        # cheap voiced source
        f0 = 110.0 + 8.0 * np.sin(2 * math.pi * 3.1 * tt)
        ph = self.phase + 2 * math.pi * np.cumsum(f0) / self.sr
        self.phase = float(ph[-1] % (2 * math.pi))
        buzz = 0.55 * np.sin(ph) + 0.25 * np.sin(2 * ph) + 0.12 * np.sin(3 * ph)
        noise = self._rng.normal(0.0, 0.18, n)
        sig = (buzz + noise) * env * 0.42
        stereo = np.column_stack((sig, sig)).astype(np.float32)
        return stereo


class AudioEngine:
    """Owns the gate, optional PortAudio duplex stream, and demo thread."""

    DRIFT_INTERVAL_S = 15 * 60

    def __init__(self, demo: bool = False) -> None:
        self.demo_forced = demo
        self.gate = NoiseGateEngine()
        self.lock = threading.Lock()
        self.active = False
        self.demo_mode = demo
        self.input_name = ""
        self.output_name = ""
        self.echo_back_name = ""
        self.input_index: Optional[int] = None
        self.output_index: Optional[int] = None
        self.echo_back_index: Optional[int] = None
        self._echo_same = False
        self.use_drift = False
        self._stream = None
        self._echo_stream = None
        self._mon_q: queue.Queue = queue.Queue(maxsize=8)
        self._demo_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._talker = DemoTalker()
        self._started_at = 0.0
        self._next_drift = 0.0
        self.last_error: Optional[str] = None
        self.live_audio = False

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            g = self.gate
            return {
                "active": self.active,
                "demo": self.demo_mode,
                "live_audio": self.live_audio,
                "muted": g.muted,
                "open": bool(g.gate.open and not g.muted),
                "level": round(g.gate.current_level, 2),
                "level_db": round(g.gate.current_level, 2),
                "gain_db": round(g.gain_db, 2),
                "input_meter": round(g.input_meter, 4),
                "output_meter": round(g.output_meter, 4),
                "gate_meter": round(g.gate_meter, 4),
                "waveform": g.waveform_peaks(160),
                "threshold": g.gate.threshold,
                "hysteresis": g.gate.hysteresis,
                "attack": g.gate.attack_ms,
                "release": g.gate.release_ms,
                "volume": g.user_volume,
                "echo_back": g.echo_back,
                "error": self.last_error,
            }

    def apply_params(self, **kwargs: Any) -> None:
        with self.lock:
            self.gate.configure(**kwargs)

    def set_drift(self, use: bool) -> None:
        self.use_drift = bool(use)
        if use:
            self._next_drift = time.monotonic() + self.DRIFT_INTERVAL_S

    def activate(
        self,
        input_name: str,
        output_name: str,
        inputs,
        outputs,
        echo_back_device: str = "",
    ) -> dict[str, Any]:
        self.stop()
        self.input_name = input_name or ""
        self.output_name = output_name or ""
        echo = (echo_back_device or "").strip()
        if echo.lower() in ("off", "none"):
            echo = ""
        self.echo_back_name = echo
        self.input_index = None
        self.output_index = None
        self.echo_back_index = None
        self._echo_same = False
        for d in inputs:
            if d.name == input_name:
                self.input_index = d.index
                break
        for d in outputs:
            if d.name == output_name:
                self.output_index = d.index
                break
        if echo:
            for d in outputs:
                if d.name == echo:
                    self.echo_back_index = d.index
                    break
            self._echo_same = bool(echo) and (
                echo == self.output_name
                or (
                    self.echo_back_index is not None
                    and self.output_index is not None
                    and self.echo_back_index == self.output_index
                )
            )

        use_live = (
            not self.demo_forced
            and sd is not None
            and self.input_index is not None
            and self.input_index >= 0
            and self.output_index is not None
            and self.output_index >= 0
        )

        self._stop.clear()
        self.gate.reset()
        self.last_error = None
        self._started_at = time.monotonic()
        self._next_drift = self._started_at + self.DRIFT_INTERVAL_S

        if use_live:
            try:
                self._start_stream()
                self.demo_mode = False
                self.live_audio = True
                self.active = True
                return {"ok": True, "demo": False}
            except Exception as exc:
                self.last_error = f"Audio start failed ({exc}); running demo meters."
                self._start_demo()
                return {"ok": True, "demo": True, "error": self.last_error}

        self._start_demo()
        return {"ok": True, "demo": True}

    def _callback(self, indata, outdata, frames, time_info, status) -> None:  # noqa: ARG002
        if status:
            pass
        with self.lock:
            out = self.gate.process(indata)
            monitor = np.array(self.gate._last_monitor, copy=True)
            echo_scale = np.float32(self.gate.echo_back / 100.0)
        if self._echo_same:
            out = out * echo_scale
        n = min(outdata.shape[0], out.shape[0])
        ch = min(outdata.shape[1], out.shape[1])
        outdata[:n, :ch] = out[:n, :ch]
        if outdata.shape[1] > ch:
            outdata[:n, ch:] = 0
        if outdata.shape[0] > n:
            outdata[n:] = 0
        if self._echo_stream is not None:
            try:
                self._mon_q.put_nowait(monitor)
            except queue.Full:
                try:
                    self._mon_q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._mon_q.put_nowait(monitor)
                except queue.Full:
                    pass
        self._maybe_drift()

    def _echo_callback(self, outdata, frames, time_info, status) -> None:  # noqa: ARG002
        try:
            block = self._mon_q.get_nowait()
        except queue.Empty:
            outdata.fill(0)
            return
        n = min(outdata.shape[0], block.shape[0], frames)
        ch = min(outdata.shape[1], block.shape[1] if block.ndim > 1 else 1)
        if block.ndim == 1:
            outdata[:n, 0] = block[:n]
            if outdata.shape[1] > 1:
                outdata[:n, 1:] = 0
        else:
            outdata[:n, :ch] = block[:n, :ch]
            if outdata.shape[1] > ch:
                outdata[:n, ch:] = 0
        if outdata.shape[0] > n:
            outdata[n:] = 0

    def _start_stream(self) -> None:
        assert sd is not None
        self._stream = sd.Stream(
            samplerate=SAMPLE_RATE,
            blocksize=BUFFER_FRAMES,
            dtype="float32",
            channels=(CHANNELS, CHANNELS),
            callback=self._callback,
            device=(self.input_index, self.output_index),
            latency="low",
        )
        self._stream.start()
        self._start_echo_stream()

    def _start_echo_stream(self) -> None:
        """Second PortAudio output for the local monitor copy, if needed."""
        if sd is None:
            return
        if self._echo_same or self.echo_back_index is None or self.echo_back_index < 0:
            return
        while True:
            try:
                self._mon_q.get_nowait()
            except queue.Empty:
                break
        self._echo_stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BUFFER_FRAMES,
            dtype="float32",
            channels=CHANNELS,
            callback=self._echo_callback,
            device=self.echo_back_index,
            latency="low",
        )
        self._echo_stream.start()

    def _start_demo(self) -> None:
        self.demo_mode = True
        self.live_audio = False
        self.active = True
        self._talker = DemoTalker()
        self._demo_thread = threading.Thread(target=self._demo_loop, daemon=True)
        self._demo_thread.start()

    def _demo_loop(self) -> None:
        period = BUFFER_FRAMES / SAMPLE_RATE
        while not self._stop.is_set():
            block = self._talker.block(BUFFER_FRAMES)
            with self.lock:
                self.gate.process(block)
            self._maybe_drift()
            time.sleep(period)

    def _maybe_drift(self) -> None:
        if not self.use_drift or not self.active:
            return
        now = time.monotonic()
        if now < self._next_drift:
            return
        self._next_drift = now + self.DRIFT_INTERVAL_S
        # Restart lines — original 0.63 closed/reopened every 15 minutes
        if self.live_audio and self._stream is not None and sd is not None:
            try:
                self._close_echo_stream()
                self._stream.stop()
                self._stream.close()
                self._start_stream()
            except Exception as exc:
                self.last_error = f"Drift reset failed: {exc}"

    def _close_echo_stream(self) -> None:
        if self._echo_stream is not None:
            try:
                self._echo_stream.stop()
                self._echo_stream.close()
            except Exception:
                pass
            self._echo_stream = None
        while True:
            try:
                self._mon_q.get_nowait()
            except queue.Empty:
                break

    def stop(self) -> None:
        self._stop.set()
        self._close_echo_stream()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._demo_thread is not None:
            self._demo_thread.join(timeout=1.0)
            self._demo_thread = None
        self.active = False
        self.live_audio = False
