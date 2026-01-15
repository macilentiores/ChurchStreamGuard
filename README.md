# ChurchStreamGuard / Stream Agent II

A small Windows app (Python + Tkinter) that helps automate church live streaming:

- Controls **OBS** (Open Broadcaster Software) via **OBS WebSocket**
- Accepts **MIDI** (Musical Instrument Digital Interface) cues from Proclaim (typically via loopMIDI)
- Starts/stops streaming and toggles recording
- Wakes/sleeps a **PTZ** (Pan‑Tilt‑Zoom) camera and recalls camera presets via **VISCA** (Video System Control Architecture) over UDP
- Optional **Web HUD** (mobile-friendly control page on the same LAN)

> Volunteer-friendly workflow: Proclaim slide cues can trigger streaming and camera preset changes without volunteers touching OBS.

---

## Which script should I run?

This repo can include **two variants**:

1) **PC-only** (Tkinter window only)  
   Run on the streaming PC; manual fallback buttons + presets are inside the desktop HUD.

2) **Web HUD** (Tkinter window + built-in web server)  
   Adds a phone/tablet control page on the same LAN and uses a WebSocket connection to the app.

If you only keep one script, Stream Agent II also supports a `WEB_HUD_ENABLED` config flag you can flip on/off.

---

## Requirements

- Windows 10/11
- OBS Studio with **OBS WebSocket enabled** (OBS 28+ includes it)
- Python 3.10+ recommended
- Optional: loopMIDI (if using Proclaim MIDI)

Python packages (typical):
- `obsws-python` (OBS WebSocket client)
- `mido` + `python-rtmidi` (MIDI input)
- `aiohttp` (required only if Web HUD is enabled)

---

## Installation

1) Clone or download this repository.

2) Install dependencies (example):

```powershell
py -m pip install obsws-python mido python-rtmidi aiohttp
```

3) Run (console, for debugging):

```powershell
py .\stream_agent.py
```

Run without a console window (recommended for Sunday use):

```powershell
pythonw .\stream_agent.py
```

---

## Church test checklist (settings to review)

All key settings are near the top in the `Config` section.

### Mode
- `HOME_TEST_MODE`
  - `True` = simulate camera power/presets (safe for home testing)
  - `False` = send real VISCA UDP commands and power off camera at the end of service

### OBS
- `OBS_HOST`, `OBS_PORT`, `OBS_PASSWORD` must match OBS WebSocket server settings.
  - On the same PC as OBS, `OBS_HOST = "127.0.0.1"` is correct.

**Camera-in-OBS check (optional but recommended)**
The app can check whether the camera feed exists in OBS and warn if missing. Configure either:
- `OBS_CAMERA_INPUT_NAME` (exact OBS input name), or
- `OBS_CAMERA_NDI_SENDER_NAME` (string it searches for inside input settings; helpful for NDI sources)

**NDI note:** NDI (Network Device Interface) is commonly used for Proclaim → OBS as a network video source.

### Camera (FoMaKo PTZ)
- `CAMERA_IP` should be the camera’s LAN IP (example: `192.168.88.20`)
- `CAMERA_VISCA_PORT` (example: `1259`) must match the camera’s VISCA-over-IP/UDP port
- `CAMERA_AUTO_WAKE_ON_PRESET`
  - If `True`, recalling a preset can automatically wake the camera first
- `PRESET_NUMBER_BASE`
  - Some cameras treat “preset 1” as VISCA `pp=00` (0‑based); others as `pp=01`. If presets are “off by one”, adjust this.

### Stop delay (end-of-service)
- `STOP_DELAY_SECONDS`: delay between a “stop” command and the actual stop action.
  - The app uses this delay before stopping stream and (in church mode) powering off the camera.

### Timer auto-start (optional)
- `USE_TIMER_START`, `TIMER_START_HHMM`, `TIMER_WEEKDAY`, `TIMEZONE`
  - `TIMER_WEEKDAY` follows Python’s `weekday()` convention: Monday=0 … Sunday=6 (so `6` = Sunday).
  - `TIMEZONE` is typically `America/Regina` for Regina, Saskatchewan.

---

## Web HUD (phone/tablet control page)

### Enable / disable
- `WEB_HUD_ENABLED` turns the web page on/off.
- `WEB_HUD_HOST` and `WEB_HUD_PORT` control where it listens (default port: `8765`).

### How to open it
From a phone/tablet on the same LAN:
- `http://<PC_LAN_IP>:8765/`

Example:
- `http://192.168.88.21:8765/`

> Note: `127.0.0.1` works only on the same device. On a phone/tablet you must use the PC’s LAN IP.

### Safety: double-tap confirmation (Web HUD)
The Web HUD uses a “double tap” confirmation pattern for the high-risk buttons (Start / Stop / Record):
- First tap arms the action for ~2 seconds
- Second tap confirms within that window
- Preset buttons remain immediate

### Web HUD token (optional)
`WEB_HUD_TOKEN` is an optional shared secret. If you set a token, you must open the HUD with `?token=YOURTOKEN`.

Example:
- `http://192.168.88.21:8765/?token=YOURTOKEN`

If the token is missing or wrong, the server returns **403 Forbidden**.

**Note:** This is not full “internet security” (no TLS/HTTPS by default). It’s a simple shared token intended for same-LAN use. For remote access over the internet, use a VPN.

---

## MIDI mapping

### Current defaults in Stream Agent II
- Start stream: note `60`
- Stop stream: note `61`
- Record toggle: note `62`
- Presets: notes `70–79` → presets `1–10`

### Preset names
Camera presets (notes 70–79):

- 70 → Preset 1 (**Pulpit**)  
- 71 → Preset 2 (**Panorama**)  
- 72 → Preset 3 (**Children’s Time**)  
- 73 → Preset 4 (**Altar**)  
- 74 → Preset 5 (**Choir**)  
- 75 → Preset 6 (**Screen**)  
- 76 → Preset 7 (**Band**)  
- 77 → Preset 8 (**Piano**)  
- 78 → Preset 9 (**Unassigned**)  
- 79 → Preset 10 (**Unassigned**)

If your script includes `PRESET_LABELS`, you can edit it so the on-screen labels match the names above.

### Older “service control” mapping
If you prefer using a different note range (e.g., 80+ for start/stop/record/power), just change the note numbers in the config (`NOTE_START_STREAM`, etc.) to match your Proclaim MIDI cues.

---

## Optional feature: per‑preset delay (MIDI/automation only)

Some moments (like “Children’s Time” or “Choir”) may need a small delay so the camera lands after people have moved.

- Enable with `ENABLE_PRESET_DELAYS = True`
- Set delay seconds per preset in `PRESET_DELAYS_SECONDS` (clamped to 0–30 seconds)
- HUD presets are always immediate (operator judgment)

---

## Safety / notes

- Don’t commit stream keys, passwords, or private tokens to GitHub.
- Keep a manual fallback plan (desktop HUD buttons, or direct OBS control).
- Keep phones/tablets on the correct LAN/SSID (avoid guest Wi‑Fi isolation).
- If your camera must be powered off between services (to avoid crashing), confirm church mode is active (`HOME_TEST_MODE = False`) so the app powers off the camera at the end of service.
