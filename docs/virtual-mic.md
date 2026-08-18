# Virtual microphone for Discord, Zoom, and Teams

NoiseGator is a userspace noise gate. It reads a real microphone, applies
the gate, and writes the result to a playback device. Call apps only see
**microphones**, so that playback device has to show up as a capture
device too. On Windows that pair is a **virtual audio cable**, and a
virtual cable is a kernel audio driver.

## Why a signed driver is required

Windows will not load an unsigned (or self-signed) kernel audio driver
on a normal PC. Enabling TESTSIGNING, installing a self-signed root, or
shipping an unsigned SysVAD-style driver would weaken the machine. This
project does none of those things.

NoiseGator therefore **cannot create a system microphone by itself**.
There is no hidden driver inside the one-file exe.

## Why VB-CABLE is not bundled

[VB-CABLE](https://vb-audio.com/Cable/) is the usual free, Microsoft-signed
virtual cable. Redistributing the installer or driver files needs
permission from VB-Audio. We do not have that license, so we do **not**
commit VB-CABLE binaries, download them in CI, or run a third-party
installer on the user’s behalf.

The only official download page this project opens is:

https://vb-audio.com/Cable/

No unofficial mirrors. The VB-CABLE license is not accepted for the
user.

## What is automated now

1. **Windows installer** (`NoiseGator-Setup.exe`) installs the already-built
   `NoiseGator.exe`, adds Start Menu and Desktop shortcuts, and can
   uninstall. After setup it launches NoiseGator. A finish-page checkbox
   (on by default) opens the official VB-CABLE page — it does not
   download or silently install that driver.
2. **First-run wizard** in the app looks for virtual-cable device names
   (VB-Audio, CABLE Input, CABLE Output, VoiceMeeter, and similar). If
   none are present, a one-click **Set up virtual microphone** button
   opens the official VB-Audio page. **I already installed it** re-scans
   devices and, when a cable appears, selects the cable playback device
   (typically **CABLE Input**) as NoiseGator’s output. The real
   microphone input is left as-is if it was already chosen.
3. If a cable is already present on launch and the user has no saved
   output device, NoiseGator auto-selects that cable. A saved output is
   never overwritten.
4. The portable `NoiseGator.exe` download stays available.

The wizard does not block the audio engine and does not disable
Activate. Esc or **Not now** dismisses it.

## What remains

A fully silent “one click and Discord just works” path still needs one
of:

- a **VB-Audio redistribution license**, so an official VB-CABLE
  installer can ship next to NoiseGator (the user would still accept
  VB-Audio’s license), or
- a **SysVAD-derived** virtual-audio driver, an **EV code-signing**
  certificate, and **Microsoft attestation** so Windows will load it.

Until then, Discord, Zoom, and Teams must be told to use the virtual
cable as their microphone (usually **CABLE Output**). NoiseGator cannot
do that from userspace.

## Related

- Official VB-CABLE: https://vb-audio.com/Cable/
- App first-run UI: `app/web/index.html` (virtual-mic modal)
- Installer script: `installer/NoiseGator.iss`
