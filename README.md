# ChurchStreamGuard — Stream Agent III sd

ChurchStreamGuard is the repository for **Stream Agent III sd**, a Windows/Python app and supporting documentation used to help operate the church live-streaming system.

The current Stream Agent III sd workflow is a **two-Axis-camera OBS automation system** with a PC HUD, browser-based Web HUD, Director page, Proclaim MIDI control, OBS WebSocket control, YouTube Live streaming support, and the current **Axis embedded-audio** sound path.

> **Purpose:** Stream Agent III sd is part of a church AV workflow designed so one trained operator can run the stream, coordinate camera changes, monitor/control the OBS stream audio path, use the Mackie/Master Fader mixer workflow, and follow Proclaim slide sequencing/timing cues. The goal is to reduce the need for the typical three-person team for roughly 60 services per year, because the church does not have enough permanently trained and committed volunteers to staff that size of team every Sunday and special service.

---

## Current status

Current working build from the June 2026 update stream:

- **Stream Agent III sd v3.1.3**
- Build label: `v3.1.3 2026-06-20 preflight-label-update`
- Current production audio method: `axis_embedded`
- Current camera design: two Axis PTZ cameras, West and East
- Current OBS live scenes: `Introduction`, `West View`, `East View`
- Current OBS camera inputs: `West_axis`, `East_axis`
- Current Web HUD port: `8765`

The older Stream Agent II / FoMaKo / VISCA-over-UDP design remains useful as historical reference, but it is **not** the current production architecture.

---

## What Stream Agent III sd does

### OBS streaming and recording control

Stream Agent controls OBS through OBS WebSocket. It can:

- Start the YouTube stream.
- Stop the stream using a countdown safety delay.
- Toggle OBS recording.
- Check the expected OBS profile before stream start.
- Auto-switch to the expected OBS profile when configured to do so.
- Restart or maintain streaming if OBS unexpectedly reports that the stream stopped.
- Run an intro-video sequence after stream start.

### Introduction video sequence

When enabled, the start sequence is:

1. Start OBS streaming.
2. Cut OBS to the `Introduction` scene.
3. Restart the `Intro_Video` media source.
4. Wait for the intro media to end or time out.
5. Cut to the configured post-intro live scene, normally `West View`.

The app includes safeguards so the intro media is not restarted until OBS confirms that the Introduction scene is actually live on Program.

### Axis camera directing

The app treats the service cue as a requested **view**, not as a fixed camera. It chooses the best Axis camera and scene for that view.

- West Axis camera: normally `192.168.88.2`
- East Axis camera: normally `192.168.88.3`
- OBS scene for West camera: `West View`
- OBS scene for East camera: `East View`
- OBS source for West camera: `West_axis`
- OBS source for East camera: `East_axis`

Axis camera movement uses Axis HTTP/VAPIX-style preset recall by **server preset name**. Preset names are exact and case-sensitive.

### Proclaim MIDI cue workflow

Proclaim sends MIDI cues through loopMIDI to Stream Agent.

Default MIDI mapping:

| Function | MIDI note | Notes |
|---|---:|---|
| Start stream | `60` | Starts the stream/start sequence |
| Stop stream | `61` | Starts the stop countdown |
| Record toggle | `62` | Starts/stops OBS recording |
| Service views | `70–79` | Maps to views 1–10 |

Default channel behavior:

| MIDI channel | Meaning |
|---:|---|
| Channel 1 | Current requested view / cut when ready |
| Channel 2 | Next expected view / prepare off-air camera |

Default service views:

| View | MIDI note | Label | Axis preset name |
|---:|---:|---|---|
| 1 | 70 | Pulpit | `Pulpit` |
| 2 | 71 | Panorama | `Panorama` |
| 3 | 72 | Children’s Time | `ChildrensTime` |
| 4 | 73 | Altar | `Altar` |
| 5 | 74 | Choir | `Choir` |
| 6 | 75 | Screen | `Screen` |
| 7 | 76 | Band | `Band` |
| 8 | 77 | Piano | `Piano` |
| 9 | 78 | Communion | `Communion` |
| 10 | 79 | Podium | `Podium` |

The Axis preset names must exist in both cameras if both cameras are expected to cover the same view. `Podium` and `podium` are different names to an Axis camera.

### Blind delays and off-air preparation

For some views, the app can move the off-air camera first, wait for a configured delay, and then cut to that camera. This hides PTZ motion from viewers.

The Director page shows a blind-delay countdown when the app is intentionally waiting before a cut. That countdown is not a fault; it is the app protecting the live stream from visible camera motion.

### Web HUD and Director page

The app serves a browser interface for the streaming PC, tablet, phone, or VPN-connected computer.

Common pages:

| Page | Purpose |
|---|---|
| `/` | Main Web HUD |
| `/director` | Camera Director page with previews and manual fallback controls |
| `/preflight` | Preflight Check report |
| `/config` | Web configuration editor |
| `/manual` | Built-in user manual |
| `/viewer` | YouTube confidence viewer/helper page |
| `/sync` | Audio sync page for the older ASIO method only |
| `/health` | Minimal health endpoint |

Local use on BeelinkOBS:

```text
http://127.0.0.1:8765/
http://127.0.0.1:8765/director
```

LAN/VPN use from another device:

```text
http://192.168.88.102:8765/
http://192.168.88.102:8765/director
```

`127.0.0.1` means “this same computer.” A tablet, phone, or home computer connected by WireGuard must use the streaming PC’s LAN/VPN address instead.

---

## Current audio handling

### Normal church method: Axis embedded audio

The current production sound path is:

```text
Mackie DL32SE live mix
  -> Axis camera audio input path
  -> embedded audio inside Axis RTSP stream
  -> OBS Media Sources: West_axis / East_axis
  -> OBS stream output
  -> YouTube Live
```

Normal setting:

```python
AUDIO_MODE = "axis_embedded"
```

In this mode:

- `West_axis` and `East_axis` carry both video and embedded audio.
- The Web HUD audio master fader controls the configured OBS audio targets, normally `West_axis` and `East_axis`.
- The Director page vertical audio faders also control the OBS audio target level.
- The old Sync page is intentionally disabled, because there is no separate `ASIO_audio` source to delay or advance.
- Audio and video travel together through the Axis/RTSP media-source path.

### Older/fallback method: shared ASIO audio

The older fallback method is:

```text
Mackie DL32SE live mix
  -> USB/ASIO audio interface
  -> OBS source named ASIO_audio
  -> OBS stream output
  -> YouTube Live
```

Fallback setting:

```python
AUDIO_MODE = "asio_shared"
OBS_AUDIO_INPUT_SHARED = "ASIO_audio"
```

In this mode, one OBS source named `ASIO_audio` should be added to scenes using **Add Existing** so it remains one shared OBS input. The `/sync` page applies only to this older ASIO method.

> **Important:** Do not accidentally leave the old `ASIO_audio` source active while using Axis embedded audio, unless it is deliberately muted or removed. Otherwise the stream can have doubled or echoing audio.

---

## Hardware and network assumptions

The current church AV network is based around the `192.168.88.0/24` AV subnet.

Typical production addresses:

| Device | Typical address / role |
|---|---|
| ER605 router | AV subnet router/gateway |
| BeelinkOBS streaming PC | `192.168.88.102` |
| Axis camera — West | `192.168.88.2` |
| Axis camera — East | `192.168.88.3` |
| Web HUD | Port `8765` on BeelinkOBS |
| OBS WebSocket | Port `4455` on BeelinkOBS / localhost |

The app assumes the cameras, BeelinkOBS, DL32SE/Master Fader control path, access point, and operator tablet are on the correct church AV LAN or reachable through the approved VPN path.

---

## Requirements

### Operating system

- Windows 10 or Windows 11 on the streaming PC.
- Normal production operation is on the church BeelinkOBS PC.

### Required applications

- OBS Studio with OBS WebSocket enabled.
- Proclaim, if using slide/MIDI automation.
- loopMIDI, if Proclaim sends MIDI cues to Stream Agent.
- Mackie Master Fader, for mixer control workflow.
- A browser on the streaming PC/tablet for Web HUD and Director page.

### OBS requirements

- OBS Studio 28 or newer is preferred because OBS WebSocket 5.x is built in.
- OBS WebSocket server enabled.
- Typical WebSocket port: `4455`.
- `OBS_HOST = "127.0.0.1"` when Stream Agent runs on the same PC as OBS.
- `OBS_EXPECTED_PROFILE_NAME = "NHLC"` for the current church production profile.
- OBS scenes must match the app config exactly:
  - `Introduction`
  - `West View`
  - `East View`
- OBS sources must match the app config exactly:
  - `Intro_Video`
  - `West_axis`
  - `East_axis`
  - `slides` for the Proclaim overlay source, if used in the current OBS scene collection.

### Python requirements

Recommended:

- Python 3.10 or newer.
- Standard Windows Python installation that includes Tkinter.

The app imports these Python packages or modules:

| Package | Required? | Purpose |
|---|---|---|
| `obsws-python` | Required | OBS WebSocket control |
| `aiohttp` | Required for Web HUD | Built-in web server and WebSocket HUD |
| `mido` | Required for MIDI workflow | MIDI message handling |
| `python-rtmidi` | Required for MIDI workflow on Windows | MIDI backend used by `mido` |
| `psutil` | Optional but recommended | Graceful app closing in optional service-end sequence |

Install command:

```powershell
py -m pip install --upgrade pip
py -m pip install obsws-python mido python-rtmidi aiohttp psutil
```

If using `requirements.txt`, make sure it includes at least:

```text
obsws-python>=1.7.0
mido>=1.3.2
python-rtmidi>=1.5.8
aiohttp>=3.9.0
psutil>=5.9.0
```

`requests` is harmless if already present, but the current Stream Agent III sd app primarily uses Python standard-library HTTP tools plus `aiohttp` for the Web HUD.

---

## Installation

### 1. Clone or download the repository

Recommended production folder:

```powershell
cd C:\ChurchAutomation
git clone https://github.com/macilentiores/ChurchStreamGuard.git
cd C:\ChurchAutomation\ChurchStreamGuard
```

If the repo is already present:

```powershell
cd C:\ChurchAutomation\ChurchStreamGuard
git pull
```

### 2. Create a Python virtual environment

Using a virtual environment avoids breaking other Python projects on the same computer.

```powershell
cd C:\ChurchAutomation\ChurchStreamGuard
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install obsws-python mido python-rtmidi aiohttp psutil
```

If PowerShell blocks activation, either run the script with the venv Python directly:

```powershell
.\.venv\Scripts\python.exe .\stream_agent_III_v3_1_3_preflight_label_update.py
```

or adjust PowerShell execution policy for the current user.

### 3. Choose the production script

Use the newest reviewed Stream Agent III sd script. At the time of this update, that is:

```text
stream_agent_III_v3_1_3_preflight_label_update.py
```

A future release may use a newer `v3_2` or later filename. Keep only one clearly named production candidate in active Task Scheduler use.

### 4. Test-run with a console window

Use this when troubleshooting:

```powershell
cd C:\ChurchAutomation\ChurchStreamGuard
.\.venv\Scripts\python.exe .\stream_agent_III_v3_1_3_preflight_label_update.py
```

The console window shows startup errors that may not appear when using `pythonw.exe`.

### 5. Run without a console window for Sunday use

Use `pythonw.exe` for normal operator-friendly launching:

```powershell
cd C:\ChurchAutomation\ChurchStreamGuard
.\.venv\Scripts\pythonw.exe .\stream_agent_III_v3_1_3_preflight_label_update.py
```

If Task Scheduler is used, point it at the intended `pythonw.exe` and pass the intended Stream Agent `.py` file as the argument.

---

## Configuration files and overrides

The app has default configuration inside the Python file, but Web HUD configuration changes are saved in:

```text
config_overrides.json
```

The config editor is available at:

```text
http://127.0.0.1:8765/config
```

or from another LAN/VPN device:

```text
http://192.168.88.102:8765/config
```

Other local runtime files:

| File | Purpose | Commit to GitHub? |
|---|---|---|
| `config_overrides.json` | Local Web HUD config changes | No, normally local only |
| `config_change_log.jsonl` | Web config audit trail | No |
| `csg_timer_state.json` | Timer fired/missed state | No |
| `stream_agent_run_*.log` | App run logs | No |
| `stream_agent_session_*.log` | Per-service logs | No |

For production, back up `config_overrides.json` before major app changes. If the file is removed, the app falls back to defaults in the Python source.

---

## Important configuration values

The current default church settings are concentrated in the `Config` class near the top of the script.

### Mode

```python
HOME_TEST_MODE = False
```

Use `False` for normal church operation. Use `True` only for bench/home testing where you do not want real camera commands sent.

### OBS

```python
OBS_HOST = "127.0.0.1"
OBS_PORT = 4455
OBS_EXPECTED_PROFILE_NAME = "NHLC"
OBS_PROFILE_CHECK_ENABLED = True
OBS_PROFILE_MISMATCH_ACTION = "switch"
```

The expected OBS profile check is important because the YouTube stream key/destination is commonly tied to the active OBS profile.

### Web HUD

```python
WEB_HUD_ENABLED = True
WEB_HUD_HOST = "0.0.0.0"
WEB_HUD_PORT = 8765
```

`0.0.0.0` lets the Web HUD listen on the PC network interface so tablets and VPN devices can reach it.

### Director preview

```python
DIRECTOR_PREVIEW_ENABLED = True
DIRECTOR_PREVIEW_BACKEND = "obs_screenshot"
```

The normal preview path uses OBS screenshots instead of putting extra live-view load directly on the Axis cameras.

### Audio

```python
AUDIO_MODE = "axis_embedded"
OBS_VIDEO_INPUT_WEST = "West_axis"
OBS_VIDEO_INPUT_EAST = "East_axis"
OBS_AUDIO_INPUT_SHARED = "ASIO_audio"
```

`OBS_AUDIO_INPUT_SHARED` is retained for the older/fallback ASIO method.

### Axis cameras

```python
WEST_AXIS_IP = "192.168.88.2"
EAST_AXIS_IP = "192.168.88.3"
```

Axis preset names must match both cameras exactly.

### Timer

```python
USE_TIMER_START = True
TIMER_START_HHMM = "9:55"
TIMER_WEEKDAY = 6
TIMEZONE = "America/Regina"
```

Python weekday numbering is Monday = `0`, Sunday = `6`.

---

## Sunday operating workflow

### Before service

1. Confirm the streaming PC is awake and logged in.
2. Confirm OBS is open.
3. Confirm OBS is on the correct profile, normally `NHLC`.
4. Confirm the correct scene collection is active.
5. Confirm `Introduction`, `West View`, and `East View` exist.
6. Confirm `West_axis` and `East_axis` show live video in OBS.
7. Confirm Proclaim is open and the loopMIDI port is available.
8. Confirm Stream Agent III sd is running.
9. Open the Web HUD.
10. Open the Director page and confirm West/East previews.
11. Press **Push for report / Preflight check** to open the preflight report.
12. Confirm normal audio mode is `axis_embedded`.
13. When the mixer is on, confirm the Web HUD/Director meter moves and YouTube confidence monitoring is reasonable.

### Start of service

The stream can be started by:

- Web HUD Start button.
- Proclaim MIDI note `60`.
- Timer auto-start, if enabled.
- Manual OBS start as a fallback.

### During service

Normal operation should come from Proclaim MIDI cues. The Director page is the manual fallback when an operator needs to steer cameras or cut scenes by hand.

### End of service

The stream can be stopped by:

- Web HUD Stop button.
- Proclaim MIDI note `61`.
- Manual OBS Stop Streaming as a fallback.

The app uses a stop countdown to reduce the chance of an accidental immediate shutdown.

---

## Preflight Check

The main HUD button is intentionally labelled as an action, not as a guarantee:

```text
Push for report
Preflight check
```

The preflight page builds a fresh report when it is opened. It checks the current app/OBS/MIDI/camera/config state and summarizes whether the system is ready.

Preflight status meanings:

| Status | Meaning |
|---|---|
| GO | Important checks look ready |
| CAUTION | The stream may still work, but something deserves attention |
| NO-GO | A critical item is missing or wrong |

The preflight page is a tool for the operator; it does not replace a quick look at OBS, YouTube Studio, and the live audio/video confidence path.

---

## Manual fallback plan

If Stream Agent misbehaves during a service:

1. Leave OBS running.
2. Use OBS directly to select `Introduction`, `West View`, or `East View`.
3. Start or stop streaming directly in OBS if needed.
4. Use the Axis camera web pages or Director direct-camera controls if MIDI steering fails.
5. Avoid deep troubleshooting during the service unless the stream is already unusable.

The app is intended to reduce operator load, not to remove the need for a manual fallback path.

---

## Documentation

Main repository:

```text
https://github.com/macilentiores/ChurchStreamGuard
```

Documentation folder:

```text
https://github.com/macilentiores/ChurchStreamGuard/tree/main/documentation
```

Important documents currently expected in `documentation/` include:

| Document | Use |
|---|---|
| [`USER_MANUAL_Stream_Agent_III_sd.md`](documentation/USER_MANUAL_Stream_Agent_III_sd.md) | Main Stream Agent user manual |
| [`church_avtech_network_diagram_stream_agent_III_sd_axis_audio_no_pi_helper.pdf`](documentation/church_avtech_network_diagram_stream_agent_III_sd_axis_audio_no_pi_helper.pdf) | Current AV/network diagram with Axis embedded audio and no Raspberry Pi WOL helper |
| [`AV_Tech_OBS_Studio_Setup_and_Rebuild_Guide_v2_axis_audio_NHLC.pdf`](documentation/AV_Tech_OBS_Studio_Setup_and_Rebuild_Guide_v2_axis_audio_NHLC.pdf) | OBS rebuild/setup guide for the current Axis-audio method |
| [`AV_Tech_Proclaim_Programming_Setup_and_Rebuild_Guide_v2.pdf`](documentation/AV_Tech_Proclaim_Programming_Setup_and_Rebuild_Guide_v2.pdf) | Proclaim setup and MIDI cue programming |
| [`AV_Tech_DL32SE_Mixer_Master_Fader_Guide.pdf`](documentation/AV_Tech_DL32SE_Mixer_Master_Fader_Guide.pdf) | Mackie DL32SE and Master Fader guide |
| [`AV_Tech_ER605_Router_and_PoE_Guide_NHLC_corrected.pdf`](documentation/AV_Tech_ER605_Router_and_PoE_Guide_NHLC_corrected.pdf) | Router, VPN, LAN, and PoE notes |
| [`AV_Tech_YouTube_Live_Interface_Setup_and_Rebuild_Guide_v2_NHLC_profile.pdf`](documentation/AV_Tech_YouTube_Live_Interface_Setup_and_Rebuild_Guide_v2_NHLC_profile.pdf) | YouTube Live setup and profile notes |
| [`Stream_Agent_III_Task_Scheduler_AutoLaunch_with_Screenshots.docx`](documentation/Stream_Agent_III_Task_Scheduler_AutoLaunch_with_Screenshots.docx) | Task Scheduler auto-launch setup |

---

## Security and what not to commit

This repository may be public. Treat it accordingly.

Do **not** commit:

- OBS stream keys.
- YouTube OAuth token files.
- Google client secret files.
- Passwords for OBS WebSocket, cameras, routers, Wi-Fi, or YouTube/Google accounts.
- Production `config_overrides.json` if it contains local credentials or private settings.
- OBS profile folders if they contain stream keys or account tokens.
- Log files that may reveal URLs, tokens, IP addresses, or operator notes.
- Private network credentials or VPN private keys.

Recommended local-only patterns for `.gitignore`:

```gitignore
config_overrides.json
config_change_log.jsonl
csg_timer_state.json
*.log
stream_agent_run_*.log
stream_agent_session_*.log
obs-studio/
youtube_client_secret*.json
token*.json
*.key
*.pem
```

If a production credential has ever been committed to a public repository, assume it is exposed and rotate/change it.

---

## Troubleshooting

| Symptom | Likely checks |
|---|---|
| Web HUD does not load | Confirm app is running, `aiohttp` is installed, Windows firewall allows Python, and use `http://127.0.0.1:8765/` locally or `http://192.168.88.102:8765/` from LAN/VPN. |
| OBS shows offline | Start OBS, enable OBS WebSocket, confirm port `4455`, password, and `OBS_HOST`. |
| Wrong OBS profile | Check `OBS_EXPECTED_PROFILE_NAME`; current church default is `NHLC`. |
| MIDI not connected | Start Proclaim, confirm loopMIDI is running, confirm Proclaim output port name contains the configured text such as `proclaim`. |
| Camera does not move | Check Axis IP, network, credentials, preset name spelling, and case. |
| Wrong camera view | Check Proclaim note/channel, Axis preset name, blind delay, and Director camera trace. |
| Director previews stale | Confirm OBS is connected, `West_axis` and `East_axis` exist, and the preview backend is `obs_screenshot`. |
| No stream audio | Confirm DL32SE output reaches the Axis audio input path, Axis audio is enabled, OBS media-source audio is not muted, and `AUDIO_MODE` is `axis_embedded`. |
| Echo/doubled audio | Check for an old active `ASIO_audio` source while Axis embedded audio is also active. |
| Sync page disabled | Expected in `axis_embedded` mode; the Sync page applies only to `asio_shared`. |
| Start was pressed twice | Redundant start requests are intentionally handled as safe/idempotent. |
| Stop note arrives early | The app uses a stop countdown before sending Stop Stream. |

---

## Developer notes

- Keep the current production script filename obvious.
- When making code changes, run a Python compile check before deployment:

```powershell
py -m py_compile .\stream_agent_III_v3_1_3_preflight_label_update.py
```

- For Web HUD changes, test at least these pages before using the build live:

```text
/
/director
/preflight
/config
/manual
/viewer
/health
```

- Avoid changing scene names, source names, Axis preset names, MIDI note numbers, or audio mode immediately before a Sunday service unless there is a clear fault to fix.

---

## License

Church/internal project. Add a formal license before distributing beyond the church or publishing as a general open-source project.
