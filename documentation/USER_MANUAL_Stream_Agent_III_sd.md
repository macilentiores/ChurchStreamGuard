# Stream Agent III sd User Manual and Reference

Version target: current Stream Agent III sd Axis-only build with Web HUD, Director previews, config coaching, vertical audio faders, and blind-delay countdown.

## 1\. Introduction

Stream Agent III sd is a live-streaming assistant for a church service. It is designed to make a two-camera Axis PTZ streaming setup safer and easier to operate, especially when one person is managing multiple jobs.

The app does not replace OBS or Proclaim. Instead, it coordinates them:

* OBS handles the actual live stream and scene output.
* Proclaim sends service cues through MIDI.
* Axis cameras move to saved presets.
* Stream Agent decides when to move cameras and when to cut scenes.
* The Web HUD gives the operator browser-based controls.

The “sd” version is the Axis-only version of Stream Agent III. It uses two Axis cameras and no longer depends on the FoMaKo camera path.

## 2\. Why the app is needed

A live church service can require many actions in a short time:

* start the stream at the correct time,
* play an intro video,
* cut from intro to live camera,
* follow the speaker, choir, pulpit, altar, or music team,
* keep audio at a usable level,
* avoid showing camera movement on stream,
* recover from wrong scenes or missed cues,
* stop the stream safely.

Without automation, the operator must do all of this manually. Stream Agent reduces the load by letting Proclaim cues and prepared rules handle the normal sequence while still preserving manual fallback.

## 3\. Core concepts

### Current view

A current view is the shot needed now, such as Pulpit, Choir, or Panorama. Proclaim normally sends this on MIDI channel 1.

### Next view

A next view is a shot expected soon. Proclaim sends this on MIDI channel 2. The app can move the off-air camera in advance so it is ready when the current view is later requested.

### Blind delay

A blind delay is a wait time before cutting to a camera. It gives the camera time to finish moving off-air. This prevents viewers from seeing PTZ movement.

### Ready camera

A camera is considered ready when the app believes it is already at the requested preset and any configured travel/delay time has expired.

### Manual fallback

The Director page can be used to manually cut to West/East scenes or directly steer a camera to a view. Manual actions cancel pending automatic cuts where necessary.

## 4\. Hardware and network assumptions

The current design assumes:

* one Windows streaming PC running OBS and Stream Agent,
* two Axis M5525-E cameras on the church camera LAN,
* OBS WebSocket available locally,
* Proclaim able to send MIDI cues,
* a tablet or browser device able to reach the Web HUD,
* a stable LAN for cameras, mixer/control devices, and the streaming PC.

The exact IP addresses can be edited in Config → Cameras.

## 5\. OBS setup

### Required OBS scenes

The app expects scenes matching these names unless config is changed:

* `Introduction`
* `West View`
* `East View`

Scene names must match exactly.

### Required OBS inputs/sources

Typical source names:

* `Intro\\\_Video`
* `West\\\_axis`
* `East\\\_axis`
* `ASIO\\\_audio` if using shared ASIO mode

The Director preview uses the configured West/East source names. If a source name is wrong, the preview or screenshot function may fail.

### OBS WebSocket

OBS WebSocket should be enabled. The default app settings use:

```text
OBS\\\_HOST = 127.0.0.1
OBS\\\_PORT = 4455
```

If a password is configured in OBS WebSocket, the app’s `OBS\\\_PASSWORD` must match. Password fields are intentionally read-only in the Web HUD.

### OBS profile safety

OBS profile checking can be enabled to prevent starting the stream with the wrong OBS profile or stream key. The app can warn, block, or switch depending on configuration.

## 6\. Axis camera setup

The app controls Axis cameras using named server presets.

Preset names must match the Axis camera preset names exactly. This is a common source of problems. For example:

```text
Podium   works if the Axis preset is named Podium
podium   does not match Podium
```

Typical preset/view table:

|View #|Label|Typical use|
|-:|-|-|
|1|Pulpit|Speaker/sermon|
|2|Panorama|Wide safe view|
|3|Children’s Time|Children’s area|
|4|Altar|Altar view|
|5|Choir|Choir view|
|6|Screen|Projection/screen view|
|7|Band|Band/music team|
|8|Piano|Piano area|
|9|Communion|Communion view|
|10|Podium|Podium/lectern view|

Camera settings live under Config → Cameras.

Important camera config fields:

* `WEST\\\_AXIS\\\_IP`
* `EAST\\\_AXIS\\\_IP`
* `AXIS\\\_VIEW\\\_PRESET\\\_NAMES`
* `WEST\\\_CAMERA\\\_ENABLED`
* `EAST\\\_CAMERA\\\_ENABLED`
* `INITIAL\\\_CAMERA\\\_VIEWS`

## 7\. MIDI and Proclaim setup

The app listens to a MIDI input port. The configured port substring is usually:

```text
proclaim
```

The app reacts to note-on messages with velocity greater than zero.

### Control notes

|Note|Meaning|
|-:|-|
|60|Start stream|
|61|Stop stream|
|62|Record toggle|

### View notes

The same note numbers are used for current and next views. The channel determines the purpose.

|MIDI|Meaning|
|-|-|
|Channel 1 note 70|Current view 1: Pulpit|
|Channel 1 note 71|Current view 2: Panorama|
|Channel 1 note 74|Current view 5: Choir|
|Channel 2 note 71|Next view 2: Panorama|
|Channel 2 note 74|Next view 5: Choir|

General formula:

```text
View number = note - NOTE\\\_PRESET\\\_FIRST + 1
```

With the normal first preset note of 70:

```text
70 = view 1
71 = view 2
...
79 = view 10
```

## 8\. Web HUD overview

The main Web HUD is the browser home page for the app.

It includes:

* stream status banner,
* Start, Stop, and REC buttons,
* Live Tools buttons,
* requested view buttons,
* manual camera cut buttons,
* audio master fader,
* health/log area,
* camera trace when enabled.

Typical URL:

```text
http://127.0.0.1:8765/
```

From another device on the LAN:

```text
http://streaming-pc-ip:8765/
```

## 9\. Live Tools buttons

### Director

Opens the operator camera-control/fallback page.

### Config

Opens categorized configuration.

### Manual

Opens the built-in reference manual.

### View Live

Opens the public YouTube live page.

### Embedded

Opens the embedded YouTube viewer page inside the Web HUD.

### Sync

Appears when shared ASIO audio mode is active.

### Audio Config

Opens directly to the Audio / Sync config area.

## 10\. Director page

The Director page is the main manual fallback page.

It shows:

* West preview,
* East preview,
* manual view buttons,
* manual West/East scene cut buttons,
* camera status,
* blind delay countdown,
* vertical audio faders beside each camera preview.

### Direct camera steering vs service view requests

Direct camera steering moves a named camera. It is useful for setup and fallback, but it is not the same as a normal service view request.

A normal service view request lets the app choose the best camera and preserve blind-delay logic.

### Blind delay countdown

When a delayed cut is pending, the Director page shows a countdown such as:

```text
Blind wait: 10.0s
```

This is reassurance that the app has not failed. It is intentionally waiting before cutting to the prepared camera.

When the wait ends, the status returns to:

```text
Ready: now
```

## 11\. Audio controls

The app has a Web HUD master audio fader and Director vertical audio faders. They control the same audio master value.

### Axis embedded mode

```text
AUDIO\\\_MODE = axis\\\_embedded
```

Audio is carried through the Axis media sources. The sync page is hidden because the app is not controlling a separate shared audio source.

### Shared ASIO mode

```text
AUDIO\\\_MODE = asio\\\_shared
```

Audio is carried by the shared OBS input, normally:

```text
ASIO\\\_audio
```

The Sync page becomes available and can adjust the OBS audio sync offset.

## 12\. Intro sequence

When intro sequence is enabled, a successful stream start triggers this flow:

1. Cut to the configured Introduction scene.
2. Confirm the Introduction scene is actually live.
3. Restart the intro media input.
4. Wait for the media state to end.
5. Hold briefly if configured.
6. Cut to the post-intro live scene.

Important config fields:

* `INTRO\\\_SEQUENCE\\\_ENABLED`
* `OBS\\\_INTRO\\\_INPUT\\\_NAME`
* `OBS\\\_INTRO\\\_SCENE\\\_NAME`
* `INTRO\\\_END\\\_SCENE\\\_NAME`
* `INTRO\\\_MAX\\\_SECONDS`
* `INTRO\\\_POST\\\_HOLD\\\_SECONDS`

## 13\. Timer Auto-Start

The Timer category controls automatic stream start.

Important fields:

* `USE\\\_TIMER\\\_START`
* `TIMER\\\_START\\\_HHMM`
* `TIMER\\\_WEEKDAY`
* `TIMEZONE`
* `TIMER\\\_FIRE\\\_GRACE\\\_MINUTES`
* `TIMER\\\_PERSIST\\\_STATE`

The app records whether the timer has already fired today so it does not repeatedly restart the stream.

For Regina, the timezone should normally remain:

```text
America/Regina
```

## 14\. Config editor

The Web HUD config editor organizes settings into categories:

* Service
* Timer Auto-Start
* Cameras
* Preset Delays
* Audio / Sync
* Director Preview
* OBS / Scenes
* MIDI / Proclaim
* Web HUD
* Logs / End
* Advanced

### Apply behavior

The category Apply button applies only staged changes in that category.

### Restore behavior

Restore Defaults in a category restores only that category. Reset field restores one setting.

### Staging behavior

If a setting is changed and then changed back to its loaded value, it is no longer staged.

### Saved overrides

Web HUD config changes are saved to:

```text
config\\\_overrides.json
```

This is safer than rewriting the Python app file.

## 15\. Preset delays

Preset delays are used to prevent viewers from seeing camera movement.

General guidance:

* 0 seconds: safe/wide shots or already stable shots.
* 5-10 seconds: moderate camera moves.
* 15-20 seconds: long or important camera moves.

If delays feel too long, watch the Director countdown first. If the app is counting down reliably, reduce only the preset values that feel excessive.

## 16\. Logs and troubleshooting

The app writes run logs if logging is enabled. It also shows recent logs in the Web HUD.

Useful checks:

* Main Web HUD status banner
* Health panel
* Camera trace
* Run log file
* OBS logs if stream/encoding problems occur

## 17\. Common troubleshooting cases

### OBS offline

Check that OBS is open, WebSocket is enabled, and the configured host/port/password are correct.

### MIDI not connected

Check that Proclaim is running, the MIDI output exists, and the app’s MIDI port substring matches the port name.

### Camera preset fails

Check network, IP address, Axis login, preset spelling, and whether the camera is enabled in config.

### East or West camera does not obey a view

Check whether the preset name exists on that camera with exactly the same capitalization.

### Sync button missing

This is expected in `axis\\\_embedded` audio mode. Switch to `asio\\\_shared` under Config → Audio / Sync to use the Sync page.

### Long blind delay

Open Director and check the countdown. If it is counting down, the app is working as designed. Adjust the preset delay only if the wait is too conservative.

### Embedded YouTube viewer has no audio

Some browsers restrict embedded playback. Use Open in YouTube as the fallback.

## 18\. Sunday readiness checklist

Before service:

1. Network gear powered and stable.
2. OBS open.
3. Correct OBS profile active.
4. Stream Agent open.
5. Web HUD reachable.
6. OBS status connected.
7. MIDI status connected.
8. Director previews working.
9. Audio meter/fader responding.
10. Timer checked if using auto-start.
11. YouTube placeholder/stream destination checked as needed.

During service:

1. Let Proclaim cues drive normal camera changes.
2. Watch Director if a camera cue seems delayed.
3. Use manual scene cut only when needed.
4. Use audio fader conservatively.

After service:

1. Stop stream.
2. Confirm YouTube/OBS stopped.
3. Save or review logs if there were issues.
4. Shut down or leave systems according to local procedure.

## 19\. Safety and change management

* Keep a known good app version available.
* Test new builds before Sunday when possible.
* Avoid changing risky settings during a live stream.
* Do not publish passwords, stream keys, or private network credentials to GitHub.
* Use config overrides for church-specific settings.

## 20\. Quick reference

```text
Main Web HUD:  /
Director:      /director
Config:        /config
Viewer:        /viewer
Manual:        /manual
Health:        /health

Current views: MIDI channel 1 notes 70-79
Next views:    MIDI channel 2 notes 70-79
Start:         note 60
Stop:          note 61
Record:        note 62

Normal scenes: Introduction, West View, East View
Normal inputs: Intro\\\_Video, West\\\_axis, East\\\_axis
ASIO source:   ASIO\\\_audio
```

