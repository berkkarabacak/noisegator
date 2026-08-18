#!/usr/bin/env python3
"""NoiseGator Modern — desktop noise-gate remake.

Serves the glass UI and (when available) opens it in a pywebview window.
On this Linux box, falls back to a local HTTP server + system browser.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from audio_io import AudioEngine, list_devices
from gate import BUFFER_FRAMES, CHANNELS, SAMPLE_RATE

VERSION = "0.10"
APP_NAME = "NoiseGator"
SUPPORT_URL = "https://github.com/berkkarabacak/noisegator"
VB_CABLE_URL = "https://vb-audio.com/Cable/"
ALLOWED_OPEN_URLS = frozenset({
    SUPPORT_URL,
    SUPPORT_URL + "/",
    VB_CABLE_URL,
    "https://vb-audio.com/Cable",
})


def _web_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")) / "web"  # type: ignore[arg-type]
    return Path(__file__).resolve().parent / "web"


WEB_DIR = _web_dir()

DEFAULTS: dict[str, Any] = {
    "input": "",
    "output": "",
    "threshold": -32,
    "hysteresis": 5,
    "attack": 30,
    "release": 1000,
    "volume": 0,
    "auto_activate": False,
    "mute": False,
    "voice_filter": False,
    "minimize_on_launch": False,
    "minimize_to_tray": True,
    "drift_compensation": False,
    "check_for_updates": True,
    "echo_back": 50,
    "echo_back_device": "",
}


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    folder = base / "noisegator"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return folder


def config_path() -> Path:
    return config_dir() / "prefs.json"


def trial_path() -> Path:
    return config_dir() / "trial.json"


def trial_day(first_launch: Any) -> int:
    """Day number of the unlimited trial. Fail-open: always returns >= 1."""
    try:
        ts = float(first_launch)
        elapsed = time.time() - ts
        if not (elapsed >= 0):
            return 1
        return max(1, int(elapsed // 86400) + 1)
    except Exception:
        return 1


def ensure_first_launch() -> float:
    """Persist first-launch time next to prefs. Fail-open: never raise."""
    now = time.time()
    path = trial_path()
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                ts = float(raw.get("first_launch", 0))
                if ts > 0:
                    return ts
    except Exception:
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"first_launch": now}, indent=2), encoding="utf-8")
    except Exception:
        pass
    return now


def migrate_threshold(value: Any) -> float:
    """Map a stored threshold onto dBFS in [-60, 0].

    Old prefs used the 0–91 display scale (dBFS + 91), typically 2–80.
    Values already in [-60, 0] are kept.
    """
    try:
        stored = float(value)
    except (TypeError, ValueError):
        return -32.0
    if stored > 0:
        stored = stored - 91.0
    return max(-60.0, min(0.0, stored))


def migrate_hysteresis(value: Any) -> float:
    try:
        h = float(value)
    except (TypeError, ValueError):
        return 5.0
    return max(1.0, min(12.0, h))


_CABLE_MARKERS = (
    "vb-audio",
    "vb audio",
    "vb-cable",
    "vb cable",
    "cable input",
    "cable output",
    "voicemeeter",
    "virtual cable",
    "virtual audio cable",
)


def device_looks_like_cable(name: str) -> bool:
    n = (name or "").casefold()
    return any(marker in n for marker in _CABLE_MARKERS)


def pick_cable_output(outputs: list[Any]) -> str:
    """Playback device the gate should send into (typically CABLE Input)."""
    names = [getattr(d, "name", "") for d in outputs]
    for name in names:
        if "cable input" in name.casefold():
            return name
    for name in names:
        if device_looks_like_cable(name):
            return name
    return ""


def any_virtual_cable(inputs: list[Any], outputs: list[Any]) -> bool:
    for d in list(inputs) + list(outputs):
        if device_looks_like_cable(getattr(d, "name", "")):
            return True
    return False


def load_prefs() -> dict[str, Any]:
    path = config_path()
    data = dict(DEFAULTS)
    if path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                data.update(saved)
        except Exception:
            pass
    data["threshold"] = migrate_threshold(data.get("threshold", -32))
    data["hysteresis"] = migrate_hysteresis(data.get("hysteresis", 5))
    return data


def save_prefs(prefs: dict[str, Any]) -> None:
    path = config_path()
    merged = dict(DEFAULTS)
    merged.update(prefs)
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")


class AppState:
    def __init__(self, demo: bool) -> None:
        self.prefs = load_prefs()
        self.first_launch = ensure_first_launch()
        self.engine = AudioEngine(demo=demo)
        self.engine.apply_params(
            threshold=float(self.prefs.get("threshold", -32)),
            hysteresis=float(self.prefs.get("hysteresis", 5)),
            attack_ms=int(self.prefs.get("attack", 30)),
            release_ms=int(self.prefs.get("release", 1000)),
            volume=int(self.prefs.get("volume", 0)),
            voice_filter=bool(self.prefs.get("voice_filter", False)),
            muted=bool(self.prefs.get("mute", False)),
            echo_back=int(self.prefs.get("echo_back", 50)),
        )
        self.engine.set_drift(bool(self.prefs.get("drift_compensation", False)))
        self.inputs, self.outputs, self.have_live = list_devices()
        if demo:
            self.have_live = False
        self._maybe_autoselect_cable_output()

    def refresh_devices(self) -> None:
        self.inputs, self.outputs, self.have_live = list_devices()
        if self.engine.demo_forced:
            self.have_live = False

    def _set_default_recording(self) -> dict[str, Any]:
        """Point Windows default/comms recording at the cable capture device."""
        try:
            from win_default_mic import set_default_recording_to_cable
            return set_default_recording_to_cable()
        except Exception:
            return {"ok": False, "device": "", "error": "helper"}

    def apply_virtual_cable(self, force: bool = False) -> dict[str, Any]:
        """Select cable playback + set default recording. Fail-open."""
        self.refresh_devices()
        present = any_virtual_cable(self.inputs, self.outputs)
        cable = pick_cable_output(self.outputs)
        if present and cable and (force or not str(self.prefs.get("output") or "").strip()):
            self.prefs["output"] = cable
            try:
                save_prefs(self.prefs)
            except Exception:
                pass
        default_mic: dict[str, Any] = {"ok": False, "device": "", "error": None}
        if present:
            default_mic = self._set_default_recording()
        return {
            "ok": present,
            "present": present,
            "output": cable or str(self.prefs.get("output") or ""),
            "default_mic": default_mic,
            "devices": self.devices_payload(),
            "prefs": self.prefs,
        }

    def _maybe_autoselect_cable_output(self) -> None:
        """First-run only: pick a virtual-cable playback device if none saved.

        Also sets Windows default recording + communications recording to the
        cable capture device (usually CABLE Output) so call apps can stay on
        Default microphone. Fail-open. Never overwrites a saved output.
        """
        if str(self.prefs.get("output") or "").strip():
            return
        if not any_virtual_cable(self.inputs, self.outputs):
            return
        self.apply_virtual_cable(force=False)

    def devices_payload(self) -> dict[str, Any]:
        cable = pick_cable_output(self.outputs)
        return {
            "inputs": [d.to_dict() for d in self.inputs],
            "outputs": [d.to_dict() for d in self.outputs],
            "live": self.have_live and not self.engine.demo_forced,
            "virtual_cable": {
                "present": any_virtual_cable(self.inputs, self.outputs),
                "output": cable,
            },
        }

    def full_state(self) -> dict[str, Any]:
        snap = self.engine.snapshot()
        snap["prefs"] = self.prefs
        snap["devices"] = self.devices_payload()
        snap["version"] = VERSION
        snap["sample_rate"] = SAMPLE_RATE
        snap["buffer"] = BUFFER_FRAMES
        snap["channels"] = CHANNELS
        snap["trial"] = {
            "first_launch": self.first_launch,
            "day": trial_day(self.first_launch),
            "unlimited": True,
        }
        return snap

    def update_prefs(self, patch: dict[str, Any]) -> None:
        mapping = {
            "threshold": ("threshold", lambda v: float(v)),
            "hysteresis": ("hysteresis", lambda v: float(v)),
            "attack": ("attack_ms", lambda v: int(v)),
            "release": ("release_ms", lambda v: int(v)),
            "volume": ("volume", lambda v: int(v)),
            "voice_filter": ("voice_filter", lambda v: bool(v)),
            "mute": ("muted", lambda v: bool(v)),
            "echo_back": ("echo_back", lambda v: int(v)),
        }
        kwargs: dict[str, Any] = {}
        for key, val in patch.items():
            if key in DEFAULTS:
                self.prefs[key] = val
            if key in mapping:
                eng_key, cast = mapping[key]
                kwargs[eng_key] = cast(val)
        if kwargs:
            self.engine.apply_params(**kwargs)
        if "drift_compensation" in patch:
            self.engine.set_drift(bool(patch["drift_compensation"]))
        save_prefs(self.prefs)

    def reset_defaults(self) -> None:
        keep_io = {
            "input": self.prefs.get("input", ""),
            "output": self.prefs.get("output", ""),
            "echo_back_device": self.prefs.get("echo_back_device", ""),
        }
        self.prefs = dict(DEFAULTS)
        self.prefs.update(keep_io)
        self.engine.apply_params(
            threshold=-32,
            hysteresis=5,
            attack_ms=30,
            release_ms=1000,
            volume=0,
            voice_filter=False,
            muted=False,
            echo_back=50,
        )
        self.engine.set_drift(False)
        save_prefs(self.prefs)

    def activate(self) -> dict[str, Any]:
        inp = self.prefs.get("input") or (self.inputs[0].name if self.inputs else "")
        out = self.prefs.get("output") or (self.outputs[0].name if self.outputs else "")
        echo = self.prefs.get("echo_back_device") or ""
        if str(echo).strip().lower() in ("off", "none"):
            echo = ""
        self.prefs["input"] = inp
        self.prefs["output"] = out
        self.prefs["echo_back_device"] = echo
        save_prefs(self.prefs)
        return self.engine.activate(inp, out, self.inputs, self.outputs, echo)


STATE: Optional[AppState] = None


def _json_bytes(obj: Any, code: int = 200) -> tuple[int, bytes, str]:
    raw = json.dumps(obj).encode("utf-8")
    return code, raw, "application/json; charset=utf-8"


class Handler(BaseHTTPRequestHandler):
    server_version = "NoiseGator/0.10"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter
        if os.environ.get("NG_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        assert STATE is not None
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/state":
            code, body, ctype = _json_bytes(STATE.full_state())
            self._send(code, body, ctype)
            return
        if path == "/api/devices":
            STATE.refresh_devices()
            code, body, ctype = _json_bytes(STATE.devices_payload())
            self._send(code, body, ctype)
            return
        if path == "/api/prefs":
            code, body, ctype = _json_bytes(STATE.prefs)
            self._send(code, body, ctype)
            return
        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        assert STATE is not None
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        path = urlparse(self.path).path
        if path == "/api/prefs":
            STATE.update_prefs(payload)
            code, body, ctype = _json_bytes({"ok": True, "prefs": STATE.prefs})
            self._send(code, body, ctype)
            return
        if path == "/api/activate":
            if payload:
                STATE.update_prefs(payload)
            result = STATE.activate()
            code, body, ctype = _json_bytes({**result, "state": STATE.full_state()})
            self._send(code, body, ctype)
            return
        if path == "/api/mute":
            muted = bool(payload.get("mute", not STATE.engine.gate.muted))
            STATE.update_prefs({"mute": muted})
            code, body, ctype = _json_bytes({"ok": True, "mute": muted})
            self._send(code, body, ctype)
            return
        if path == "/api/reset":
            STATE.reset_defaults()
            code, body, ctype = _json_bytes({"ok": True, "prefs": STATE.prefs})
            self._send(code, body, ctype)
            return
        if path == "/api/apply-virtual-cable":
            force = bool(payload.get("force", True))
            result = STATE.apply_virtual_cable(force=force)
            code, body, ctype = _json_bytes(result)
            self._send(code, body, ctype)
            return
        if path == "/api/open-url":
            url = str(payload.get("url") or SUPPORT_URL).strip()
            if url not in ALLOWED_OPEN_URLS:
                url = SUPPORT_URL
            opened = False
            try:
                webbrowser.open(url)
                opened = True
            except Exception:
                opened = False
            code, body, ctype = _json_bytes({"ok": opened, "url": url})
            self._send(code, body, ctype)
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    def _serve_static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        rel = path.lstrip("/").replace("\\", "/")
        if ".." in rel.split("/"):
            self._send(403, b"forbidden", "text/plain")
            return
        target = (WEB_DIR / rel).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ext = target.suffix.lower()
        ctypes = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".json": "application/json",
            ".woff2": "font/woff2",
        }
        data = target.read_bytes()
        self._send(200, data, ctypes.get(ext, "application/octet-stream"))


def pick_port(preferred: int) -> int:
    for port in (preferred, preferred + 1, preferred + 2, 0):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            chosen = sock.getsockname()[1]
            sock.close()
            return chosen
        except OSError:
            continue
    return preferred


def start_server(port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def try_webview(url: str, minimize: bool) -> bool:
    try:
        import webview  # type: ignore
    except Exception:
        return False
    try:
        window = webview.create_window(
            f"{APP_NAME}  {VERSION}",
            url,
            width=1100,
            height=720,
            min_size=(980, 640),
            background_color="#070b14",
        )
        if minimize:
            def _min() -> None:
                time.sleep(0.4)
                try:
                    window.minimize()
                except Exception:
                    pass

            threading.Thread(target=_min, daemon=True).start()
        webview.start()
        return True
    except Exception as exc:
        print(f"pywebview unavailable ({exc}); using browser fallback.", file=sys.stderr)
        return False


def open_browser(url: str) -> None:
    chrome = None
    for candidate in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        from shutil import which

        chrome = which(candidate)
        if chrome:
            break
    if chrome:
        try:
            import subprocess

            subprocess.Popen(
                [
                    chrome,
                    f"--app={url}",
                    "--window-size=1100,720",
                    "--new-window",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception:
            pass
    webbrowser.open(url)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="NoiseGator Modern — microphone noise gate")
    p.add_argument("--demo", action="store_true", help="Animate fake levels / waveform (no audio I/O)")
    p.add_argument("--port", type=int, default=8765, help="Local HTTP port (default 8765)")
    p.add_argument("--no-browser", action="store_true", help="Do not open a window; just serve")
    p.add_argument("--webview", action="store_true", help="Prefer pywebview even if it failed before")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    global STATE
    args = parse_args(argv)
    STATE = AppState(demo=args.demo)
    port = pick_port(args.port)
    httpd = start_server(port)
    url = f"http://127.0.0.1:{port}/"

    if args.demo or STATE.prefs.get("auto_activate"):
        STATE.activate()

    print(f"{APP_NAME} {VERSION}")
    print(f"UI:  {url}")
    print(f"Prefs: {config_path()}")
    if args.demo or not STATE.have_live:
        print("Audio: demo meters (no live capture on this machine, or --demo)")
    else:
        print("Audio: live PortAudio devices available")

    try:
        if args.no_browser:
            while True:
                time.sleep(1)
        else:
            opened = False
            if not os.environ.get("NG_NO_WEBVIEW"):
                opened = try_webview(url, bool(STATE.prefs.get("minimize_on_launch")))
            if not opened:
                open_browser(url)
                print("Window: browser / Chrome app mode (pywebview not available)")
                while True:
                    time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        try:
            STATE.engine.stop()
        except Exception:
            pass
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
