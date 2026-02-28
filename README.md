# ChurchStreamGuard (Stream Agent II) — v8.0

ChurchStreamGuard (a.k.a. **Stream Agent II**) is a small **Windows** automation app written in **Python + Tkinter** that helps volunteers run reliable church live streams.

It integrates:

- **OBS** (Open Broadcaster Software) via **OBS WebSocket** (the OBS remote-control API)
- **Proclaim** slide cues via **MIDI** (Musical Instrument Digital Interface), typically through **loopMIDI**
- A **PTZ** (Pan‑Tilt‑Zoom) camera via **VISCA** (Video System Control Architecture) over UDP
- Optional **Web HUD** (Heads-Up Display) for phone/tablet control on the same LAN
- Optional **Web Config UI** for safe, structured configuration editing (writes `config_overrides.json`)

> Design goal: volunteers can run the stream without “touching OBS” during the service, while still keeping safe manual fallback controls.

---

## What it can do

### Streaming + recording control (OBS)
- Start/stop streaming (manual buttons, timer, and/or MIDI cues)
- Toggle recording (manual and/or MIDI)
- Start requests are **idempotent**: redundant Start signals (timer + Proclaim + manual) are tolerated without false “stream trouble” alarms

### OBS safety checks
- Optional “expected profile” safety check (because stream destination/key is commonly profile-dependent)
- Actions on mismatch: **block**, **warn**, or **switch** (auto-switch profile before start)
- Profile mismatch handling runs early after OBS connects (when OBS is ready to report status)

### PTZ camera automation (VISCA over UDP)
- Power on/off (optional)
- Recall presets (MIDI notes → preset numbers)
- Optional “auto-wake on preset”

### Timer auto-start
- Start stream at a specified local time (e.g., 09:55 on Sundays)
- Timezone-aware (default: `America/Regina`)
- Persisted “fired today” state to avoid multiple timer starts per day

### Web HUD + double-tap safety
- Mobile-friendly control page for Start/Stop/Record and camera presets
- “Double-tap to confirm” on high-risk actions

### Preset delays (automation only)
- Optional per-preset delay (e.g., give people time to walk to the lectern)
- **Important:** HUD preset buttons remain immediate (operator judgment)

---

## Repository layout (typical)

- `stream_agent.py` (or another single `.py` script) — the Stream Agent II app
- `README.md` — this file
- `requirements.txt` — Python dependencies
- Local-only files (should NOT be committed):
  - `config_overrides.json` (saved Config UI overrides)
  - `config_change_log.jsonl` (audit log of config changes)
  - `csg_timer_state.json` (timer “fired today” persistence)
  - `stream_agent_run_*.log`, `stream_agent_session_*.log` (logs)

---

## Requirements

### OS + apps
- Windows 10/11
- **OBS Studio** 28+ (OBS WebSocket is built-in)
- Optional: **loopMIDI** (if you want Proclaim → MIDI triggers)

### Python
- Python 3.10+ recommended (3.11/3.12 are fine)

### Python packages
Install from `requirements.txt`, then add the extra packages used by the Web HUD and service-end helpers:

```powershell
py -m pip install -r requirements.txt
py -m pip install aiohttp psutil
```

Notes:
- `python-rtmidi` is commonly needed on Windows for MIDI input with `mido`.
- `psutil` is optional and only used for graceful close operations in the service-end sequence.

---

## Installation

1) Clone the repo (or download ZIP) into a folder like:

`C:\ChurchAutomation\ChurchStreamGuard\`

2) Create a virtual environment (recommended):

```powershell
cd C:\ChurchAutomation\ChurchStreamGuard
py -m venv .venv
.\.venv\Scriptsctivate
```

3) Install dependencies (see above).

4) Run (with console — best for testing):

```powershell
py .\stream_agent.py
```

Run without console (Sunday / operator-friendly):

```powershell
pythonw .\stream_agent.py
```

---

## OBS setup

### Enable OBS WebSocket
In OBS:

- **Tools → WebSocket Server Settings**
- Enable the server
- Default port is typically `4455`
- Set a password (recommended)

Match these in Stream Agent config:
- `OBS_HOST` (use `127.0.0.1` if Stream Agent runs on the same PC as OBS)
- `OBS_PORT` (usually `4455`)
- `OBS_PASSWORD`

### Profile safety (recommended)
Because stream destination/key is often tied to the current OBS Profile:

- `OBS_PROFILE_CHECK_ENABLED = True`
- `OBS_EXPECTED_PROFILE_NAME = "…"`, exactly matching OBS (case/spaces matter)
- `OBS_PROFILE_MISMATCH_ACTION`:
  - `"switch"`: auto-switch to expected profile before starting (recommended when profiles are well-maintained)
  - `"warn"`: warn but do not switch
  - `"block"`: refuse to start if mismatched

> The app will not auto-switch profiles while OBS is actively streaming or recording.

### Stream key whitespace
If you ever see intermittent “won’t start” behaviour, confirm the Stream Key field has **no leading/trailing spaces**.

---

## PTZ camera setup (VISCA over UDP)

Key settings:
- `CAMERA_IP`
- `CAMERA_VISCA_PORT` (FoMaKo VISCA-over-IP commonly uses `1259`)
- `CAMERA_BOOT_SECONDS` (time needed after power-on)
- `CAMERA_AUTO_WAKE_ON_PRESET`
- `PRESET_NUMBER_BASE` (use this if presets are “off by one”)

### Home testing
Set:

- `HOME_TEST_MODE = True`

This simulates camera power/presets (safe to test MIDI/OBS logic at home without touching the camera).

---

## MIDI mapping (Proclaim cues)

Typical defaults (configure Proclaim to send these notes):

- Start stream: `NOTE_START_STREAM = 60`
- Stop stream: `NOTE_STOP_STREAM = 61`
- Record toggle: `NOTE_REC_TOGGLE = 62`
- Presets: `NOTE_PRESET_FIRST = 70` to `NOTE_PRESET_LAST = 79` → presets `1–10`

Preset labels are defined in:

- `PRESET_LABELS`

Those labels appear on the HUD, and also in the Config UI “Preset Delay” editor.

---

## Timer auto-start (optional)

Key settings:
- `USE_TIMER_START = True`
- `TIMER_START_HHMM` (e.g., `"9:55"`)
- `TIMER_WEEKDAY` (Python weekday: Mon=0 … Sun=6)
- `TIMEZONE` (e.g., `"America/Regina"`)
- `TIMER_PERSIST_STATE = True` (uses `csg_timer_state.json`)
- `TIMER_FIRE_GRACE_MINUTES` (prevents late/duplicate timer fires)

---

## Web HUD (phone/tablet)

Enable:
- `WEB_HUD_ENABLED = True`
- `WEB_HUD_HOST = "0.0.0.0"`
- `WEB_HUD_PORT = 8765`

Open from a device on the same LAN:

- `http://<PC_LAN_IP>:8765/`

Optional shared token:
- Set `WEB_HUD_TOKEN = "yourtoken"`
- Open with:
  - `http://<PC_LAN_IP>:8765/?token=yourtoken`

### Double-tap safety
Start/Stop/Record use a “double tap to confirm” pattern:
- First tap arms (short window)
- Second tap confirms

Presets remain single-tap.

---

## Web Config UI

If enabled, open:

- `http://<PC_LAN_IP>:8765/config`
- If token is set:
  - `http://<PC_LAN_IP>:8765/config?token=yourtoken`

The Config UI writes overrides to:

- `config_overrides.json`

If that file is missing/corrupted, the app falls back to **defaults in the .py file**.

**Best practice:**
- Export/backup your overrides before big changes
- Keep venue-specific overrides (home vs church) in separate backed-up copies

---

## Sunday workflow (suggested)

1) Start OBS, confirm correct Profile + Scene Collection
2) Start Stream Agent II (pythonw)
3) Confirm HUD shows **OBS connected**
4) Confirm camera source health (if enabled)
5) Confirm Web HUD opens on your phone/tablet
6) If using timer start, confirm:
   - Timezone is correct
   - Timer HH:MM + weekday are correct
7) Start Proclaim and run a quick MIDI test slide (optional)

---

## Logs + troubleshooting

The app writes:
- `stream_agent_run_YYYY-MM-DD_HH-MM-SS.log`
- `stream_agent_session_YYYY-MM-DD_HH-MM-SS.log`

If you share logs publicly, **redact**:
- OBS passwords
- Web HUD tokens
- Stream keys / OAuth tokens (if you include OBS profile files)

Common causes:
- OBS not ready (during startup) — wait a few seconds, then retry
- Profile mismatch — fix OBS profile name or update `OBS_EXPECTED_PROFILE_NAME`
- Redundant Start signals — safe; app tolerates them (idempotent start)

---

## Security / what NOT to commit

Do **not** commit:
- OBS profiles (`service.json`, `basic.ini`) — can contain stream keys/tokens
- `config_overrides.json` (local secrets/tokens/passwords)
- `config_change_log.jsonl`
- `csg_timer_state.json`
- `*.log`

If needed, add these to `.gitignore`.

---

## License
Church/internal project. Add a license if you plan to distribute publicly (MIT/Apache-2.0/etc.).
