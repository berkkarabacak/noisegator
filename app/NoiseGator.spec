# -*- mode: python ; coding: utf-8 -*-
# One-file Windows build (run on Windows, not Linux):
#   pip install -r requirements.txt pyinstaller
#   pyinstaller --noconfirm NoiseGator.spec
#
# --onefile --noconsole --name NoiseGator
# web/ is bundled via datas (PyInstaller uses ";" on Windows, ":" on POSIX).

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

block_cipher = None
root = Path(SPECPATH)

datas = [(str(root / "web"), "web")]
binaries = []
hiddenimports = [
    "sounddevice",
    "numpy",
    "webview",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "cffi",
    "_cffi_backend",
    "win_default_mic",
]

# PortAudio shared libs live next to the sounddevice / _sounddevice_data wheels.
binaries += collect_dynamic_libs("sounddevice")
try:
    binaries += collect_dynamic_libs("_sounddevice_data")
except Exception:
    pass

for pkg in ("sounddevice", "_sounddevice_data", "numpy", "webview"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

# De-dupe while keeping order
def _uniq(items):
    seen = set()
    out = []
    for item in items:
        key = item if isinstance(item, str) else tuple(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out

datas = _uniq(datas)
binaries = _uniq(binaries)
hiddenimports = _uniq(hiddenimports)

a = Analysis(
    [str(root / "app.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="NoiseGator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
