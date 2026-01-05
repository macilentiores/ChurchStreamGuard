# Church Stream Guard (OBS + Proclaim MIDI)

A small Windows app (Python + Tkinter) that helps automate YouTube streaming for a church setup:

- Controls OBS (Open Broadcaster Software) via OBS WebSocket
- Accepts MIDI (Musical Instrument Digital Interface) cues from Proclaim (via loopMIDI)
- Starts/stops streaming and recording
- Wakes/sleeps a FoMaKo PTZ camera and recalls camera presets via VISCA over UDP

> Intended for a volunteer-friendly workflow: Proclaim slide cues can trigger streaming and camera preset changes without volunteers touching OBS.

---

## Features

- **OBS control**
  - Start Stream (manual button and/or MIDI)
  - Stop Stream with configurable delay (manual button and/or MIDI)
  - Record toggle (manual button and/or MIDI)

- **Camera control (FoMaKo PTZ)**
  - Power ON / OFF via VISCA-over-UDP
  - Recall presets 1–10 via MIDI notes 70–79
  - Optional auto-wake before preset recall

- **HUD (small window)**
  - Shows current status and last action
  - Provides Start/Stop/Record buttons for manual fallback
  - Provides preset buttons for quick confidence testing

---

## Requirements

- Windows 10/11
- OBS Studio with **OBS WebSocket enabled** (OBS 28+ includes it)
- Python 3.10+ recommended
- Optional: loopMIDI (if using Proclaim MIDI)

Python packages:
- `obsws-python`
- `mido`
- `python-rtmidi`

---

## Installation

1. **Clone or download** this repository.
2. Install Python packages:

   ```powershell
   py -m pip install -r requirements.txt

Run with a console (debug/testing)
py C:\ChurchAutomation\church_stream_guard.py

Run without a console window (recommended for Sunday use)
pythonw C:\ChurchAutomation\church_stream_guard.py


MIDI Mapping
Camera presets (notes 70–79)

70 → Preset 1 (Pulpit)

71 → Preset 2 (Panorama)

72 → Preset 3 (Children’s Time)

73 → Preset 4 (Altar)

74 → Preset 5 (Choir)

75 → Preset 6 (Screen)

76 → Preset 7 (Band)

77 → Preset 8 (Piano)

78 → Preset 9 (Unassigned)

79 → Preset 10 (Unassigned)

Service control (notes 80+)

(Adjust to match your script configuration)

80 → Start Stream

81 → End Service (Stop Stream after delay + camera power off)

82 → Start Recording

83 → Stop Recording

84 → Camera Power ON (optional)

85 → Camera Power OFF (optional)


Configuration

Most settings are at the top of church_stream_guard.py.

Key settings:

HOME_TEST_MODE

True for home testing (no camera required, simulates camera actions)

False at church (sends real VISCA commands)

OBS_HOST, OBS_PORT, OBS_PASSWORD

Must match OBS WebSocket server settings

CAMERA_IP, VISCA_UDP_PORT

Must match the camera’s network IP and VISCA port

NDI_INPUT_NAME

If you use NDI gating, set this to the exact OBS source/input name

STOP_DELAY_SECONDS

Delay between “End Service” cue and actual stop

Preset numbering note

Some cameras treat preset “1” as VISCA pp=00 (zero-based), others use pp=01.
If preset recall is “off by one”, adjust:

PRESET_NUMBER_BASE = 0 or 1

Safety / Notes

Do not commit stream keys or passwords to GitHub.

If you use Custom RTMPS + reusable stream key, YouTube behavior depends on your channel/live settings (Auto-start/Auto-stop).

Always keep a manual fallback plan (HUD buttons, or direct OBS control if needed).

License

Add your chosen license here (MIT, Apache-2.0, etc.), or leave it private if this is a church-only repo.


::contentReference[oaicite:0]{index=0}

