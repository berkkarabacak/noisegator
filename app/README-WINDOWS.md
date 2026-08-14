# NoiseGator Modern — Windows

Portable package. This is **not** a silent installer; it runs the Python app.

## Quick start

1. Install **Python 3.10+** from https://www.python.org/downloads/  
   Tick **Add python.exe to PATH**.
2. Unzip this folder.
3. Open a terminal in the folder and run:

   ```bat
   pip install -r requirements.txt
   ```

4. Double-click `NoiseGator.bat` (or `NoiseGator.exe` if present).

`pythonw app.py` starts the UI without a console window. `NoiseGator.bat` prefers `pythonw`, then `python`.

## Audio / PortAudio

`sounddevice` on Windows is installed from a **pip wheel that usually bundles PortAudio**. You should not need a separate PortAudio DLL.

For Discord / Zoom / Teams you still need a **virtual cable** (VB-Cable). Set:

- Input → your real microphone  
- Output → VB-Cable Input  
- Echo-back device → headphones (hear yourself) or **Off**  
- In the chat app, pick the cable’s playback device as the microphone

## Build a one-file exe (on Windows)

```bat
pip install pyinstaller sounddevice numpy pywebview
pyinstaller --noconfirm --onefile --noconsole --name NoiseGator --add-data "web;web" app.py
```

Or use the checked-in spec:

```bat
pyinstaller --noconfirm NoiseGator.spec
```

The result lands in `dist\NoiseGator.exe`.

## Echo-back

**Volume boost** is the send gain (dB) to the output / VoIP device.  
**Echo back** is a separate 0–100% local monitor. If the echo-back device equals Output, the monitor scale is applied on the main stream instead of opening two PortAudio streams to the same device.

## GitHub Actions (real Windows PE)

The Linux box cannot cross-compile a Windows PE. The repo workflow
`.github/workflows/windows-exe.yml` (working directory `app/`) runs on
`windows-latest`, installs Python 3.12 + PyInstaller, and uploads
`NoiseGator.exe` as an artifact. Tags and `workflow_dispatch` also publish
it to a GitHub Release (`windows` or the tag name).
