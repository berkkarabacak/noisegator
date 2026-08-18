# NoiseGator Modern

Version **0.10**. A desktop microphone **noise gate**: mic input → gate → output device (usually a virtual cable for VoIP). This is a from-scratch remake of the NoiseGator 0.63 idea with a dark glass dashboard UI.

The gate is driven in **dBFS**. It **opens** when the attack-window average stays at or above the threshold, and **closes** when the release-window average drops below (threshold − hysteresis). Closed audio is faded out; open audio passes with an optional volume boost.

## Run

```bash
pip install -r requirements.txt
python app.py
```

Demo meters (no live capture — useful on headless machines or for a preview):

```bash
python app.py --demo
```

`python app.py` starts a local UI server and opens a desktop window via **pywebview** when that stack is available. If pywebview cannot create a window (common on minimal Linux boxes), the same UI is served on `http://127.0.0.1:8765/` and opened in Chrome / the system browser.

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--demo` | Animate a fake speech envelope and waveform |
| `--port 8765` | HTTP port |
| `--no-browser` | Serve only; do not open a window |

Preferences (devices, threshold, hysteresis, attack, release, volume, echo-back, toggles) are stored as JSON in the user config directory:

- Linux: `~/.config/noisegator/prefs.json`
- macOS: `~/Library/Application Support/noisegator/prefs.json`
- Windows: `%APPDATA%\noisegator\prefs.json`

A sidecar `trial.json` next to that file stores the first-launch timestamp for the unlimited-trial nag. The app stays fully usable if that file is missing or unreadable.

Old 0.9 prefs stored threshold on a 0–91 scale (`dBFS + 91`). Those values are converted on load: `threshold_db = stored - 91`, then clamped to **−60 … 0**.

## VoIP / virtual cable

NoiseGator does **not** create a virtual microphone. For Discord, Zoom, Teams, etc. you still need a virtual audio cable, then:

1. Set **Input** to your real microphone.
2. Set **Output** to the virtual cable’s input (e.g. VB-Cable on Windows, BlackHole / Loopback on macOS, Pulse/PipeWire null sink or `snd-aloop` on Linux).
3. On Windows, first run can set the default recording device to the cable capture side (usually **CABLE Output**), so Discord / Zoom / Teams can stay on **Default microphone**. Apps that already saved a specific mic still need Default or that cable.

## Controls

- **Threshold** — circular knob in **dB** (−60 … 0, default **−32 dB**). Gate opens when the attack-window average ≥ this value. A dashed line on the waveform marks the same level.
- **Hysteresis** — 1–12 dB (default **5 dB**). Gate closes only when the release-window average < (threshold − hysteresis), so the gate does not chatter around the line.
- **Attack / Release** — how long the level must stay above threshold / below the close line (defaults 30 ms / 1000 ms).
- **Volume boost** — −15 to +5 dB send gain to the output / VoIP device (default 0).
- **Echo back** — 0–100% local monitor / hear-yourself volume (default 50%). Separate from volume boost.
- **Echo-back device** — playback device for the monitor copy, or **Off** (no extra stream). If it matches Output, echo-back is applied as a linear scale on the main stream instead of opening two streams to the same device.
- **Mute**, **Auto-activate**, **Voice filter** (high-pass 40 Hz, low-pass 1500 Hz + adaptive gain).
- **Activate** — starts the stream; stays selected while running (same as 0.63).
- Settings: minimize on launch, minimize to tray, drift compensation (restart lines every 15 minutes), check-for-updates preference (the actual update ping is skipped).

Audio format matches the original: 44.1 kHz, 16-bit, stereo PCM, 512-frame buffer.

## Windows one-file exe

A real `NoiseGator.exe` is built on **windows-latest** by GitHub Actions (`.github/workflows/windows-exe.yml` in the [berkkarabacak/noisegator](https://github.com/berkkarabacak/noisegator) repo, working directory `app/`). It is not cross-compiled from Linux.

Locally on a Windows machine:

```bat
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm NoiseGator.spec
```

The result lands in `dist\NoiseGator.exe`.

## License note

Original NoiseGator was released under **CC BY-NC 2.0**. This project is a UI remake of the same idea, not a redistribution of the 2014 Swing application. A small credit lives in Settings.

## Layout

```
app.py          entry (HTTP + window)
gate.py         noise gate, attack/release, hysteresis, voice filter
audio_io.py     devices + PortAudio stream / demo talker
win_default_mic.py  Windows default recording helper (IPolicyConfig)
web/            HTML / CSS / JS dashboard
NoiseGator.spec PyInstaller one-file spec (build on Windows)
```
