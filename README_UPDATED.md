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
   - Run on the streaming PC; manual fallback buttons + presets are inside the desktop HUD.

2) **Web HUD** (Tkinter window + built-in web server)  
   - Adds a phone/tablet control page on the same LAN.
   - Uses a WebSocket connection to the app.

If your repo has only one script: the current Stream Agent II code has a `WEB_HUD_ENABLED` config flag you can flip on/off. fileciteturn13file2L27-L33

---

## Requirements

- Windows 10/11
- OBS Studio with **OBS WebSocket enabled** (OBS 28+ includes it)
- Python 3.10+ recommended
- Optional: loopMIDI (if using Proclaim MIDI)

Python packages (typical):
- `obsws-python` (OBS WebSocket client)
- `mido` + `python-rtmidi` (MIDI input)

---

## Installation

1) Clone or download this repository.
2) Install dependencies (example):

```powershell
py -m pip install obsws-python mido python-rtmidi
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

All key settings are near the top in the `Config` class. fileciteturn13file2L6-L40

### Mode
- `HOME_TEST_MODE`
  - `True` = simulate camera power/presets (safe for home testing) fileciteturn12file0L773-L791
  - `False` = send real VISCA UDP commands and power off camera at the end of service fileciteturn13file11L84-L85

### OBS
- `OBS_HOST`, `OBS_PORT`, `OBS_PASSWORD` must match OBS WebSocket server settings. fileciteturn13file2L13-L16  
  - On the same PC as OBS, `OBS_HOST = "127.0.0.1"` is correct.

**Camera-in-OBS check (optional but recommended)**
- The app can periodically check whether the camera feed exists in OBS and warn if missing. fileciteturn12file0L322-L349  
  Configure either:
  - `OBS_CAMERA_INPUT_NAME` (exact OBS input name), or fileciteturn12file0L333-L336
  - `OBS_CAMERA_NDI_SENDER_NAME` (string it searches for inside input settings; helpful for NDI sources). fileciteturn12file0L336-L345

**NDI note:** NDI (Network Device Interface) is the “network video source” tech often used for Proclaim → OBS.

### Camera (FoMaKo PTZ)
- `CAMERA_IP` should be the camera’s LAN IP (example shown is `192.168.88.20`). fileciteturn13file2L34-L36  
- `CAMERA_VISCA_PORT` (example `1259`) must match the camera’s VISCA-over-IP/UDP port. fileciteturn13file2L34-L36  
- `CAMERA_AUTO_WAKE_ON_PRESET`
  - If `True`, recalling a preset can automatically wake the camera first (so you don’t have to manually power it on). fileciteturn12file0L856-L858
- `PRESET_NUMBER_BASE`
  - Some cameras treat “preset 1” as VISCA `pp=00` (0‑based); others as `pp=01`. If presets are “off by one”, adjust this. fileciteturn13file2L40-L41

### Stop delay (end-of-service)
- `STOP_DELAY_SECONDS`: delay between a “stop” command and the actual stop action. fileciteturn13file2L24-L26  
  - The app uses this delay before stopping stream and (in church mode) powering off the camera. fileciteturn13file11L68-L85

### Timer auto-start (optional)
- `USE_TIMER_START`, `TIMER_START_HHMM`, `TIMER_WEEKDAY`, `TIMEZONE` fileciteturn13file2L70-L77  
  - `TIMER_WEEKDAY` follows Python’s `weekday()` convention: Monday=0 … Sunday=6 (so `6` = Sunday). fileciteturn9file7L11-L15  
  - `TIMEZONE` is set to `America/Regina` in current config. fileciteturn13file2L72-L74

---

## Web HUD (phone/tablet control page)

### Enable / disable
- `WEB_HUD_ENABLED` turns the web page on/off. fileciteturn13file2L27-L33  
- `WEB_HUD_HOST` and `WEB_HUD_PORT` control where it listens (default port: `8765`). fileciteturn13file2L27-L32

### How to open it
From a phone/tablet on the same LAN:
- `http://<PC_LAN_IP>:8765/`

Example:
- `http://192.168.88.21:8765/`

### Safety: double-tap confirmation (Web HUD)
The Web HUD supports a “double tap” confirmation pattern for the high-risk buttons (Start / Stop / Record): first tap arms the button, second tap confirms within a short window (this prevents accidental triggers).

### Web HUD token (optional)
`WEB_HUD_TOKEN` is an optional shared secret; leave it blank to disable token checks. fileciteturn13file2L30-L32

If you set a token:
- Open the page with `?token=YOURTOKEN`
- The page forwards `?token=...` to the WebSocket endpoint as well. fileciteturn13file10L33-L39

Example:
- `http://192.168.88.21:8765/?token=YOURTOKEN`

**Note:** This is not full “internet security” (no TLS/HTTPS by default). It’s a simple shared token intended for **same-LAN** use.

---

## MIDI mapping

### Current defaults in Stream Agent II
The current Stream Agent II config defaults use:
- Start stream: note `60`
- Stop stream: note `61`
- Record toggle: note `62` fileciteturn13file2L64-L68  
- Presets: notes `70–79` → presets `1–10` fileciteturn13file2L67-L68

### Preset names (as used in the older README)
Camera presets (notes 70–79): fileciteturn13file0L7-L28

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

**Label note:** the current config labels preset 1 as `"lectern"` by default. You can change `PRESET_LABELS` so it matches your real-world names (e.g., change `"lectern"` → `"Pulpit"`). fileciteturn13file5L37-L48

### Service control (older README mapping)
The older README used a different “service control” note range (80+). fileciteturn13file0L30-L45  
If you prefer that mapping, you can change the note numbers in the config (`NOTE_START_STREAM`, etc.). fileciteturn13file2L64-L68

---

## Optional feature: per‑preset delay (MIDI/automation only)

Some moments (like “Children’s Time” or “Choir”) may need a small delay so the camera lands after people have moved.

- Enable with `ENABLE_PRESET_DELAYS = True` fileciteturn13file2L42-L44
- Set delay seconds per preset in `PRESET_DELAYS_SECONDS` (clamped to 0–30 seconds). fileciteturn12file0L825-L832
- Important: **HUD presets are always immediate** (operator judgment), and will cancel any pending delayed preset. fileciteturn13file12L39-L41

---

## Safety / notes

- Don’t commit stream keys, passwords, or private tokens to GitHub.
- Keep a manual fallback plan (desktop HUD buttons, or direct OBS control).
- If your camera must be powered off between services (to avoid crashing), confirm church mode is active (`HOME_TEST_MODE = False`) so the app powers off the camera at the end of service. fileciteturn13file11L84-L85
