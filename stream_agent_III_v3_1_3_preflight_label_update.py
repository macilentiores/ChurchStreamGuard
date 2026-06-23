"""
stream_agent_III_sd_2026-04-04_Sat_afternoon_churchready1.py

Stream Agent III sd — PC HUD + Web HUD (tablet/phone)

Version: v1.1 beta 2026-04-05 Axis cleanup phase 1

What this build is designed to do
---------------------------------
This Stream Agent III sd build keeps the strengths of the late Stream Agent II and III
work, but now focuses on an Axis-only directing model:
requested service view -> best camera choice -> optional off-air move -> cut to scene.

Main goals in this build
- Keep the existing stream start / stop / record controls.
- Keep the Proclaim MIDI workflow for view notes 70–79.
- Interpret channel 1 as the current requested service view.
- Interpret channel 2 as the next expected service view for the off-air camera.
- Switch OBS scenes directly (West View / East View) for normal camera directing.
- Respect blind delays when enabled so PTZ motion happens off-air whenever possible.
- Support either shared ASIO audio or Axis embedded RTSP audio for testing.
- Preserve operator override. Manual Web HUD actions always take priority over pending auto
  scene cuts.

Important scope note for this build
-----------------------------------
This build is now being cleaned up into a pure two-Axis design with the FoMaKo/NDI path removed.
The welcome sequence remains:
- Start stream
- Play Introduction scene (when enabled)
- Detect intro end
- Cut to the configured post-intro scene (default: West View)
- Allow the operator or later MIDI view requests to take over from there

This is intentional so the Axis-only Stream Agent III sd build stays understandable, testable,
and safe for live use.
"""

# -----------------------------

from __future__ import annotations

# -----------------------------
# App identity / version
# -----------------------------
APP_NAME = "Stream Agent III"
APP_VERSION = "v3.1.3 2026-06-20 preflight-label-update"
APP_DISPLAY = f"{APP_NAME} {APP_VERSION}"
BUILD_ID = "stream-agent-iii-v3-1-3-preflight-label-update-2026-06-20"


import asyncio
import datetime as dt
import json
import os
import base64
import socket
import threading
import math
import queue
import time
import glob
import shutil
import subprocess
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as urlerror, parse as urlparse, request as urlrequest

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

try:
    import mido
except Exception:
    mido = None

try:
    from obsws_python import EventClient, ReqClient, Subs
except Exception:
    EventClient = None
    ReqClient = None
    Subs = None

try:
    import psutil  # Optional — used for graceful app closing in service-end sequence
except Exception:
    psutil = None

import tkinter as tk
from tkinter import ttk
from tkinter import scrolledtext
from tkinter import messagebox
from tkinter import font as tkfont

# -----------------------------
# UI helpers: outlined banner + outlined buttons (Tkinter Canvas)
# -----------------------------
def _color_is_white(c: str) -> bool:
    try:
        return (c or "").strip().lower() in ("#fff", "#ffffff", "white")
    except Exception:
        return False

def _draw_outlined_text(
    canvas: tk.Canvas,
    x: int,
    y: int,
    text: str,
    font,
    fill: str,
    outline: str = "black",
    outline_px: int = 1,
    mode: str = "full",
):
    """Approximate text stroke by drawing the same text around the center in the outline color.

    mode:
      - "full": thicker/stronger outline (good for the main banner)
      - "light": lighter outline (good for small button text)
    """
    if not text:
        return
    if outline_px <= 0:
        canvas.create_text(x, y, text=text, font=font, fill=fill, anchor="center")
        return

    # Radius tuned so outline_px=3 looks like ~3pt around typical HUD fonts
    r = max(1, int(round(outline_px * 0.5)))
    # Draw outline first
    if (mode or "").lower() == "light":
        # 4-direction outline reads cleaner on small text
        for dx, dy in ((-r, 0), (r, 0), (0, -r), (0, r)):
            canvas.create_text(x + dx, y + dy, text=text, font=font, fill=outline, anchor="center")
    else:
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if dx == 0 and dy == 0:
                    continue
                # Diamond-ish mask keeps it from getting too "blobby"
                if abs(dx) + abs(dy) <= r + 1:
                    canvas.create_text(x + dx, y + dy, text=text, font=font, fill=outline, anchor="center")
    # Fill on top
    canvas.create_text(x, y, text=text, font=font, fill=fill, anchor="center")


class OutlinedBanner(tk.Canvas):
    """Full-width status banner with a black outline and optional outlined white text."""
    def __init__(self, parent, height=64, outline_px=4, text_outline_px=1, **kwargs):
        super().__init__(parent, height=height, highlightthickness=0, bd=0, **kwargs)
        self._height = height
        self._outline_px = outline_px
        self._text_outline_px = text_outline_px
        self._text = ""
        self._bg = "#4CAF50"
        self._fg = "white"
        self._font = ("Segoe UI", 18, "bold")
        self.bind("<Configure>", lambda _e: self._redraw())

    def set(self, text: str, bg: str, fg: str):
        self._text = text or ""
        self._bg = bg or "#4CAF50"
        self._fg = fg or "white"
        self._redraw()

    def set_text(self, text: str):
        self._text = text or ""
        self._redraw()

    def set_colors(self, bg: str, fg: str):
        self._bg = bg or self._bg
        self._fg = fg or self._fg
        self._redraw()

    def _redraw(self):
        try:
            self.delete("all")
            w = max(10, int(self.winfo_width()))
            h = max(10, int(self.winfo_height() or self._height))
            pad = int(self._outline_px / 2)

            # Banner rectangle with black outline (solid)
            self.create_rectangle(
                pad, pad, w - pad, h - pad,
                fill=self._bg,
                outline="black",
                width=self._outline_px
            )

            # Centered text
            x = w // 2
            y = h // 2

            # If the message is long, prefer a clean 2-line layout at the em-dash.
            text = self._text
            if " — " in text and len(text) > 18:
                text = text.replace(" — ", "\n", 1)

            # Auto-shrink font so text stays inside the banner
            try:
                base_family = self._font[0] if isinstance(self._font, (tuple, list)) else "Segoe UI"
                base_size = int(self._font[1]) if isinstance(self._font, (tuple, list)) and len(self._font) > 1 else 18
                base_weight = self._font[2] if isinstance(self._font, (tuple, list)) and len(self._font) > 2 else "bold"
                size = base_size
                max_w = max(50, w - 24)
                lines = (text or "").split("\n")
                while size > 11:
                    f = tkfont.Font(family=base_family, size=size, weight=base_weight)
                    widest = 0
                    for ln in lines:
                        widest = max(widest, f.measure(ln))
                    if widest <= max_w:
                        break
                    size -= 1
                font_to_use = (base_family, size, base_weight)
            except Exception:
                font_to_use = self._font

            if _color_is_white(self._fg):
                _draw_outlined_text(self, x, y, text, font_to_use, fill=self._fg,
                                   outline="black", outline_px=self._text_outline_px)
            else:
                self.create_text(x, y, text=text, font=font_to_use, fill=self._fg, anchor="center")
        except Exception:
            pass


class OutlinedCanvasButton(tk.Canvas):
    """Clickable button with a solid black border and optional outlined white text."""
    def __init__(self, parent, text: str, command, bg: str, fg: str = "white",
                 width=120, height=40, border_px: int = 3, text_outline_px: int = 1, font=("Segoe UI", 11, "bold")):
        super().__init__(parent, width=width, height=height, highlightthickness=0, bd=0)
        self._text = text
        self._command = command
        self._bg = bg
        self._fg = fg
        self._border_px = border_px
        self._text_outline_px = text_outline_px
        self._font = font
        self._enabled = True
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self._hover = False
        self._redraw()

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        self._redraw()

    def set_text(self, text: str):
        self._text = text
        self._redraw()

    def set_colors(self, bg: str, fg: str = None):
        self._bg = bg or self._bg
        if fg is not None:
            self._fg = fg
        self._redraw()

    def _on_click(self, _e):
        if not self._enabled:
            return
        try:
            if callable(self._command):
                self._command()
        except Exception:
            pass

    def _on_enter(self, _e):
        self._hover = True
        self._redraw()

    def _on_leave(self, _e):
        self._hover = False
        self._redraw()

    def _redraw(self):
        try:
            self.delete("all")
            w = max(10, int(self.winfo_width()))
            h = max(10, int(self.winfo_height()))
            pad = int(self._border_px / 2)

            bg = self._bg
            fg = self._fg

            if not self._enabled:
                bg = "#777777"
                fg = "#dddddd"
            elif self._hover:
                # Slight hover lift: darken a touch (simple)
                bg = bg

            self.create_rectangle(
                pad, pad, w - pad, h - pad,
                fill=bg,
                outline="black",
                width=self._border_px
            )
            x, y = w // 2, h // 2
            text = self._text
            font_to_use = self._font
            if _color_is_white(fg):
                # Button text looks better with a lighter outline than the main banner
                _draw_outlined_text(
                    self,
                    x,
                    y,
                    text,
                    font_to_use,
                    fill=fg,
                    outline="black",
                    outline_px=self._text_outline_px,
                    mode="light",
                )
            else:
                self.create_text(x, y, text=text, font=font_to_use, fill=fg, anchor="center")
        except Exception:
            pass


@dataclass
class Config:
    """Configuration for Stream Agent III sd v1.0 beta.

    This class deliberately contains generous comments because the goal is to make
    the file usable as both a working application and a church-side reference copy.
    Most of the new Stream Agent III sd behaviour is configured here.
    """

    # ----------------------------
    # MODE / ENVIRONMENT
    # ----------------------------
    HOME_TEST_MODE: bool = False

    # ----------------------------
    # PC HUD WINDOW BEHAVIOUR
    # ----------------------------
    AUTO_MINIMIZE_ENABLED: bool = True
    AUTO_MINIMIZE_AFTER_SECONDS: int = 5
    MINIMIZE_ON_STARTUP: bool = True
    AUTO_RESTORE_ON_ISSUE: bool = False
    AUTO_BRING_TO_FRONT_ON_STREAM_START: bool = False

    # ----------------------------
    # AUTO-RECOVERY
    # ----------------------------
    AUTO_RECOVER_ENABLED: bool = True
    AUTO_RECOVER_MAX_ATTEMPTS: int = 3
    AUTO_RECOVER_BASE_DELAY_SECONDS: int = 10
    AUTO_RECOVER_BACKOFF_MULTIPLIER: float = 2.0
    AUTO_RECOVER_COOLDOWN_SECONDS: int = 300
    AUTO_RECOVER_START_GRACE_SECONDS: int = 30
    # Debounce a reported ON->OFF transition before treating it as a real unexpected stop.
    UNEXPECTED_STOP_DEBOUNCE_SECONDS: float = 2.0

    # ----------------------------
    # LOGGING
    # ----------------------------
    LOG_TO_FILE_ENABLED: bool = True
    LOG_RUN_FILE_PREFIX: str = "stream_agent"
    LOG_SEPARATE_SESSION_FILES: bool = True
    LOG_DIR: str = r"C:\ChurchAutomation\Stream_Agent_III_logs"
    LOG_RETENTION_COUNT: int = 30  # Keep last 30 files

    # ----------------------------
    # OBS CONNECTION
    # ----------------------------
    OBS_HOST: str = "127.0.0.1"
    OBS_PORT: int = 4455
    OBS_PASSWORD: str = ""

    # ----------------------------
    # OBS PROFILE SAFETY (preflight)
    # ----------------------------
    # NOTE: OBS stream destination/service/key is typically tied to the active OBS Profile.
    # This feature verifies (and optionally auto-switches) the current Profile before starting a stream.
    OBS_PROFILE_CHECK_ENABLED: bool = True
    # Exact OBS profile name used for the church YouTube stream.
    # IMPORTANT: OBS profile names are exact-match; this value intentionally includes
    # Church production OBS profile name. Intentionally no trailing spaces.
    OBS_EXPECTED_PROFILE_NAME: str = "NHLC"
    # Actions: "block" (warn & stop), "warn" (warn & continue), "switch" (auto-switch then start)
    OBS_PROFILE_MISMATCH_ACTION: str = "switch"
    # How long to wait after switching profiles before attempting StartStream
    OBS_PROFILE_SWITCH_GRACE_SECONDS: float = 2.0

    AUTO_RECONNECT_OBS: bool = True


    # ----------------------------
    # INTRO VIDEO SEQUENCE
    # ----------------------------
    # Stream Agent III sd now treats the intro as its own OBS scene.
    # On a successful stream start, the app restarts the named media input, waits for
    # playback to end, and then CUTS to the configured post-intro scene.
    INTRO_SEQUENCE_ENABLED: bool = True

    # OBS media input name inside the Introduction scene.
    OBS_INTRO_INPUT_NAME: str = "Intro_Video"

    # Scene name that contains the intro media input.
    OBS_INTRO_SCENE_NAME: str = "Introduction"

    # Scene to cut to when the intro clip ends. In this beta the intended default is
    # the safe West panorama shot.
    INTRO_END_SCENE_NAME: str = "West View"

    # Polling cadence and safety fallback.
    INTRO_POLL_SECONDS: float = 0.5
    # Safety fallback if the media state never reaches ENDED. Keep this close to
    # the real clip length so an off-normal intro cannot stall the startup for minutes.
    INTRO_MAX_SECONDS: int = 90
    INTRO_RESTART_GRACE_SECONDS: float = 1.0
    INTRO_ENDED_GUARD_SECONDS: float = 2.0

    # Hardening added after the May 17 service: the intro media must not restart
    # until OBS confirms the Introduction scene is actually on Program. This avoids
    # playing Intro_Video off-air if OBS is still sitting on West/East.
    INTRO_FORCE_CUT_TRANSITION: bool = False

    INTRO_SCENE_SWITCH_RETRIES: int = 8
    INTRO_SCENE_SWITCH_RETRY_SECONDS: float = 0.35
    INTRO_ABORT_IF_SCENE_NOT_LIVE: bool = True

    # Extra separation after OBS reports the intro media has ended, before the app
    # CUTS to the live post-intro scene. This does not change the live transition
    # type; it simply keeps the Introduction scene on-air briefly so the intro source
    # can go inactive cleanly before the handoff.
    #
    # Suggested starting point: 0.25 s
    # Conservative test value:   0.50 s
    INTRO_POST_HOLD_SECONDS: float = 0.25

    # Legacy reveal mode from late Stream Agent II builds. Leave False for the new
    # scene-switching design. Set True only if you deliberately stack the intro on top
    # of a live source in the same scene and want the old eye-toggle behaviour back.
    INTRO_DISABLE_ON_END: bool = False

    # ----------------------------
    # STREAM SAFETY
    # ----------------------------
    STOP_DELAY_SECONDS: int = 90
    START_DEBOUNCE_SECONDS: float = 5.0

    # ----------------------------
    # WEB HUD
    # ----------------------------
    WEB_HUD_ENABLED: bool = True
    WEB_HUD_HOST: str = "0.0.0.0"
    WEB_HUD_PORT: int = 8765
    WEB_HUD_TOKEN: str = ""
    WEB_HUD_LOG_LINES: int = 30

    # Show extra coaching/help text in the Web HUD config editor.
    # Pilot implementation currently adds richer help for Preset Delays only.
    CONFIG_HELP_ENABLED: bool = True

    # ----------------------------
    # DIRECTOR PREVIEW
    # ----------------------------
    # Default preview path now uses OBS WebSocket screenshots of the two camera inputs
    # instead of reading preview imagery directly from the Axis cameras. This keeps the
    # operator-facing director layout intact while avoiding extra live-view load on the
    # cameras during service.
    DIRECTOR_PREVIEW_ENABLED: bool = True
    DIRECTOR_PREVIEW_BACKEND: str = "obs_screenshot"  # obs_screenshot | obs_mjpeg_stream | axis_snapshot | axis_mjpeg
    DIRECTOR_PREVIEW_REFRESH_MS: int = 500
    DIRECTOR_PREVIEW_WIDTH: int = 640
    DIRECTOR_PREVIEW_HEIGHT: int = 360
    DIRECTOR_PREVIEW_JPEG_QUALITY: int = 70
    DIRECTOR_PREVIEW_STALE_AFTER_MS: int = 3000

    # Optional test/debug pane for camera-routing confidence checks on the Web HUD.
    # When enabled, the HUD shows the most recent scene cut plus the most recent Axis
    # command decisions, delayed-cut scheduling, fallbacks, and home-test simulation.
    # Credentials are always masked.
    HUD_CAMERA_TRACE_ENABLED: bool = True
    HUD_CAMERA_TRACE_MAX_LINES: int = 80
    MIDI_CUE_AUDIT_ENABLED: bool = True
    MIDI_CUE_AUDIT_TO_FILE: bool = True


    # ----------------------------
    # WEB HUD — YouTube "View Live" helper
    # ----------------------------
    YOUTUBE_LIVE_URL: str = "https://www.youtube.com/@NewHopeLutheranChurchRegina/live"
    # Used for the embedded viewer mode (/viewer). Found from your channel URL:
    # https://www.youtube.com/channel/UCNg9iyVIF5ks6hO1P-VIqKQ
    YOUTUBE_CHANNEL_ID: str = "UCNg9iyVIF5ks6hO1P-VIqKQ"

    # ----------------------------
    # WEB HUD SYNC OFFSET PAGE
    # ----------------------------
    SYNC_OFFSET_WEB_ENABLED: bool = True
    SYNC_OFFSET_STEP_MS: int = 20
    SYNC_OFFSET_COARSE_STEP_MS: int = 100
    SYNC_OFFSET_UNLOCK_SECONDS: int = 30
    SYNC_OFFSET_MIN_MS: int = -950
    SYNC_OFFSET_MAX_MS: int = 20000

    # ----------------------------
    # OBS SCENE ROUTING (Stream Agent III sd)
    # ----------------------------
    # These scene names must match OBS exactly. The app will CUT between these scenes.
    OBS_SCENE_INTRO: str = "Introduction"
    OBS_SCENE_WEST: str = "West View"
    OBS_SCENE_EAST: str = "East View"

    # The main automatic fallback scene if a requested view cannot be satisfied cleanly.
    OBS_SAFE_FALLBACK_SCENE: str = "West View"

    # Force the scene transition to Cut before app-directed camera changes. For this
    # two-camera Axis workflow, Cut is the safe default.
    FORCE_CUT_TRANSITION: bool = False
    OBS_CUT_TRANSITION_NAME: str = "Cut"

    # AUDIO MODE
    # "asio_shared"    = original setup: one shared OBS input named ASIO_audio carries the live mix.
    # "axis_embedded" = new test setup: West_axis and East_axis Media Sources carry video + camera-injected audio.
    # For the current Axis-audio test build this defaults to axis_embedded. Change back to
    # "asio_shared" for the older Sunday scene collection.
    AUDIO_MODE: str = "axis_embedded"

    # OBS input name used for shared live audio control in AUDIO_MODE="asio_shared".
    # In OBS, create ONE source named "ASIO_audio" and place it into West / East / Introduction
    # using Add Existing. Volume and sync offset are global to that shared source.
    OBS_AUDIO_INPUT_SHARED: str = "ASIO_audio"

    # OBS video input names for operator-facing status, logging, previews, and Axis embedded-audio targets.
    OBS_VIDEO_INPUT_WEST: str = "West_axis"
    OBS_VIDEO_INPUT_EAST: str = "East_axis"

    # ----------------------------
    # AXIS CAMERA CONTROL (West / East)
    # ----------------------------
    # Stream Agent III sd uses the Axis PTZ CGI named server-preset command:
    #   /axis-cgi/com/ptz.cgi?gotoserverpresetname=<PresetName>
    #
    # IMPORTANT:
    # - Axis presets are matched by NAME.
    # - The preset names below must exactly match the server preset names stored in
    #   BOTH Axis cameras. Keep them simple and URL-safe where possible.
    # - Authentication is attempted using both Digest and Basic auth handlers because
    #   Axis firmware / browser setups vary.
    AXIS_USERNAME: str = "admin"
    AXIS_PASSWORD: str = "oneroom"
    AXIS_USE_HTTPS: bool = False
    AXIS_COMMAND_TIMEOUT_SECONDS: float = 3.0

    # Live church IPs (confirmed by user): West=.2, East=.3
    WEST_AXIS_IP: str = "192.168.88.2"
    WEST_AXIS_CAMERA_ID: int = 1
    EAST_AXIS_IP: str = "192.168.88.3"
    EAST_AXIS_CAMERA_ID: int = 1

    # Axis preset names keyed by the common logical view number. These names must match
    # the actual Axis server preset names exactly.
    AXIS_VIEW_PRESET_NAMES: Dict[int, str] = field(default_factory=lambda: {
        1: "Pulpit",
        2: "Panorama",
        3: "ChildrensTime",
        4: "Altar",
        5: "Choir",
        6: "Screen",
        7: "Band",
        8: "Piano",
        9: "Communion",
        10: "Podium",
    })

    # Manual enable / disable flags for routing. These are the first layer of the
    # planned failover model. If a camera is unavailable, set its flag to False and the
    # routing logic will skip it.
    WEST_CAMERA_ENABLED: bool = True
    EAST_CAMERA_ENABLED: bool = True

    # ----------------------------
    # VIEW ROUTING
    # ----------------------------
    # Axis-only simplified model:
    # - if either camera is already confirmed on the requested view, use that ready shot
    # - otherwise prepare the off-air camera
    # - only consider moving the on-air camera when there is no off-air option
    # This removes per-view camera preference tables because the two Axis cameras are now
    # treated as near-equivalent coverage positions.

    # Startup parking assumptions. These are just the app's initial beliefs. They can be
    # changed later if you want different defaults.
    INITIAL_CAMERA_VIEWS: Dict[str, int] = field(default_factory=lambda: {
        "west": 2,
        "east": 2,
    })

    # If True, incoming routed preset/view notes are ignored once stop countdown has begun.
    # This prevents a late panorama or other view note from changing scenes during stop.
    IGNORE_VIEW_NOTES_DURING_STOP_COUNTDOWN: bool = True

    # ----------------------------
    # WEB HUD AUDIO MASTER CONTROL
    # ----------------------------
    # The Web HUD master fader writes one dB value to the active audio target(s).
    # asio_shared mode: one OBS input named ASIO_audio.
    # axis_embedded mode: both camera Media Sources, West_axis and East_axis.
    WEB_HUD_AUDIO_MASTER_ENABLED: bool = True
    AUDIO_MASTER_MIN_DB: float = -40.0
    AUDIO_MASTER_MAX_DB: float = 6.0
    AUDIO_MASTER_DEFAULT_DB: float = 0.0
    # If OBS meter events stop arriving, treat the HUD meter as stale and drop it
    # back to silence instead of holding the last good value forever.
    AUDIO_METER_STALE_AFTER_SECONDS: float = 5.0

    # ----------------------------
    # PRESET DELAYS
    # ----------------------------
    ENABLE_PRESET_DELAYS: bool = True
    PRESET_DELAYS_SECONDS: Dict[int, int] = field(default_factory=lambda: {
        1: 0,  #pulpit
        2: 0,   #panorama
        3: 10,  #Children's Time
        4: 10,  #alter
        5: 10,  #choir
        6: 0,   #screen
        7: 0,   #band
        8: 0,   #piano
        9: 10,  #communion
        10: 0,  #podium
    })

    # ----------------------------
    # MIDI INPUT
    # ----------------------------
    MIDI_INPUT_PORT_SUBSTRING: str = "proclaim"
    MIDI_CHANNEL_1_BASED: int = 1
    MIDI_NEXT_CHANNEL_1_BASED: int = 2
    NOTE_START_STREAM: int = 60
    NOTE_STOP_STREAM: int = 61
    NOTE_REC_TOGGLE: int = 62
    NOTE_PRESET_FIRST: int = 70
    NOTE_PRESET_LAST: int = 79
    AXIS_TRAVEL_TIME_SECONDS: float = 4.0

    # ----------------------------
    # TIMER AUTO-START
    # ----------------------------
    USE_TIMER_START: bool = True
    TIMER_START_HHMM: str = "9:55"
    TIMER_WEEKDAY: int = 6
    TIMEZONE: str = "America/Regina"
    TIMER_PERSIST_STATE: bool = True
    TIMER_STATE_FILE: str = "csg_timer_state.json"
    TIMER_FIRE_GRACE_MINUTES: int = 15

    TZ_FALLBACK_MODE: str = "local"
    TZ_FALLBACK_UTC_OFFSET_HOURS: int = -6

    PRESET_LABELS: Dict[int, str] = field(default_factory=lambda: {
        1: "pulpit",
        2: "Panorama",
        3: "Children's Time",
        4: "Altar",
        5: "Choir",
        6: "Screen",
        7: "Band",
        8: "Piano",
        9: "Communion",
        10: "Podium",
    })


    # ----------------------------
    # SERVICE-END MASTER SEQUENCE (v7.14)
    # ----------------------------
    # Safety defaults:
    # - Entire feature is OFF unless you enable BOTH SERVICE_END_SEQUENCE_ENABLED and MIDI_STOP_TRIGGERS_FULL_SEQUENCE.
    # - Windows shutdown is also OFF unless SERVICE_END_WINDOWS_SHUTDOWN is True.
    SERVICE_END_SEQUENCE_ENABLED: bool = False  # master switch
    MIDI_STOP_TRIGGERS_FULL_SEQUENCE: bool = False  # only a MIDI stop note triggers the full sequence

    SERVICE_END_USB_ROOT: str = r"D:\stream data"  # base folder on USB/external drive (dated subfolder created)
    SERVICE_END_POST_STOP_WAIT_SECONDS: int = 60  # extra wait after OBS reports fully stopped
    SERVICE_END_COPY_PREVIOUS_LOGS: bool = True  # include previous run/session logs too
    SERVICE_END_COPY_TODAYS_MP4: bool = True  # copy the most recent MP4 whose date == "today" in cfg timezone
    OBS_RECORDING_PATH: str = r""  # required if copying MP4s (example: r"C:\Stream Recordings")

    SERVICE_END_CLOSE_PROCLAIM: bool = True
    SERVICE_END_CLOSE_MASTER_FADER: bool = True
    SERVICE_END_CLOSE_OBS: bool = True

    PROCLAIM_PROCESS_NAME: str = "Proclaim.exe"
    MASTER_FADER_PROCESS_NAME: str = "MasterFader.exe"
    SERVICE_END_OBS_PROCESS_NAME: str = "obs64.exe"  # change to "obs32.exe" if using 32-bit OBS

    SERVICE_END_WINDOWS_SHUTDOWN: bool = False  # must be True to actually shut down Windows
    SERVICE_END_SHUTDOWN_DELAY_SECONDS: int = 60  # shutdown /s /t N; abort with shutdown /a
# -----------------------------
# Config override + Web HUD config editor support (v8.0 prep)
# -----------------------------
DEFAULT_CFG = Config()
CFG = Config()

def _cfg_base_dir() -> str:
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except Exception:
        return os.getcwd()

CFG_OVERRIDE_PATH = os.path.join(_cfg_base_dir(), "config_overrides.json")
CFG_CHANGELOG_PATH = os.path.join(_cfg_base_dir(), "config_change_log.jsonl")

# Fields that are visible in the Web HUD config editor but NEVER editable there.
# (Tier A: always read-only)
CFG_UI_READONLY_ALWAYS = {
    # Credentials / tokens (edit locally)
    "OBS_PASSWORD", "WEB_HUD_TOKEN",
    "AXIS_PASSWORD",
    # Network bindings (avoid stranding the Web HUD mid-session)
    "WEB_HUD_HOST", "WEB_HUD_PORT",
    # OBS connection identity (avoid confusing mid-session changes)
    "OBS_HOST", "OBS_PORT",
    # Preset labels not editable (only delays)
    "PRESET_LABELS",
}

# Fields that are allowed to be edited while STREAMING, but only after override unlock.
# (Tier B: live-tunable)
CFG_UI_LIVE_EDITABLE_FIELDS = {
    "OBS_PROFILE_SWITCH_GRACE_SECONDS",
    "INTRO_MAX_SECONDS",
    "INTRO_POST_HOLD_SECONDS",
    "INTRO_FORCE_CUT_TRANSITION",
    "INTRO_SCENE_SWITCH_RETRIES",
    "INTRO_SCENE_SWITCH_RETRY_SECONDS",
    "INTRO_ABORT_IF_SCENE_NOT_LIVE",
    "AUTO_RECOVER_ENABLED",
    "AUTO_RECOVER_MAX_ATTEMPTS",
    "AUTO_RECOVER_BASE_DELAY_SECONDS",
    "AUTO_RECOVER_BACKOFF_MULTIPLIER",
    "AUTO_RECOVER_COOLDOWN_SECONDS",
    "AUTO_RECOVER_START_GRACE_SECONDS",
    "WEB_HUD_LOG_LINES",
    "HUD_CAMERA_TRACE_ENABLED",
    "HUD_CAMERA_TRACE_MAX_LINES",
    "DIRECTOR_PREVIEW_ENABLED",
    "DIRECTOR_PREVIEW_BACKEND",
    "DIRECTOR_PREVIEW_REFRESH_MS",
    "DIRECTOR_PREVIEW_WIDTH",
    "DIRECTOR_PREVIEW_HEIGHT",
    "DIRECTOR_PREVIEW_JPEG_QUALITY",
    "DIRECTOR_PREVIEW_STALE_AFTER_MS",
    "SYNC_OFFSET_WEB_ENABLED",
    "SYNC_OFFSET_STEP_MS",
    "SYNC_OFFSET_COARSE_STEP_MS",
    "SYNC_OFFSET_UNLOCK_SECONDS",
    "STOP_DELAY_SECONDS",
}

# Timer page fields (restore-to-default scope)
CFG_UI_TIMER_FIELDS = [
    "USE_TIMER_START",
    "TIMER_START_HHMM",
    "TIMER_WEEKDAY",
    "TIMEZONE",
    "TIMER_PERSIST_STATE",
    "TIMER_STATE_FILE",
    "TIMER_FIRE_GRACE_MINUTES",
    "TZ_FALLBACK_MODE",
    "TZ_FALLBACK_UTC_OFFSET_HOURS",
]

# Enum-like fields → dropdown options
CFG_UI_ENUM_OPTIONS = {
    "AUDIO_MODE": ["asio_shared", "axis_embedded"],
    "OBS_PROFILE_MISMATCH_ACTION": ["block", "warn", "switch"],
    "TZ_FALLBACK_MODE": ["local", "fixed_offset"],
    "DIRECTOR_PREVIEW_BACKEND": ["obs_screenshot", "obs_mjpeg_stream", "axis_snapshot", "axis_mjpeg"],
}

# UI grouping (order) for general config page
CFG_UI_GENERAL_SECTIONS = [
    ("MODE / ENVIRONMENT", ["HOME_TEST_MODE"]),
    ("OBS SCENE ROUTING", [
        "OBS_SCENE_INTRO", "OBS_SCENE_WEST", "OBS_SCENE_EAST",
        "OBS_SAFE_FALLBACK_SCENE", "FORCE_CUT_TRANSITION", "OBS_CUT_TRANSITION_NAME",
        "OBS_VIDEO_INPUT_WEST", "OBS_VIDEO_INPUT_EAST",
    ]),
    ("AXIS CAMERA CONTROL", [
        "AXIS_USERNAME", "AXIS_PASSWORD", "AXIS_USE_HTTPS", "AXIS_COMMAND_TIMEOUT_SECONDS",
        "WEST_AXIS_IP", "WEST_AXIS_CAMERA_ID", "EAST_AXIS_IP", "EAST_AXIS_CAMERA_ID",
        "AXIS_VIEW_PRESET_NAMES",
        "WEST_CAMERA_ENABLED", "EAST_CAMERA_ENABLED",
    ]),
    ("VIEW ROUTING", [
        "INITIAL_CAMERA_VIEWS",
    ]),
    ("AUDIO MODE", [
        "AUDIO_MODE", "OBS_AUDIO_INPUT_SHARED",
    ]),
    ("WEB HUD AUDIO MASTER", [
        "WEB_HUD_AUDIO_MASTER_ENABLED", "AUDIO_MASTER_MIN_DB", "AUDIO_MASTER_MAX_DB", "AUDIO_MASTER_DEFAULT_DB", "AUDIO_METER_STALE_AFTER_SECONDS",
    ]),
    ("PC HUD WINDOW BEHAVIOUR", [
        "AUTO_MINIMIZE_ENABLED",
        "AUTO_MINIMIZE_AFTER_SECONDS",
        "MINIMIZE_ON_STARTUP",
        "AUTO_RESTORE_ON_ISSUE",
        "AUTO_BRING_TO_FRONT_ON_STREAM_START",
    ]),
    ("AUTO-RECOVERY", [
        "AUTO_RECOVER_ENABLED",
        "AUTO_RECOVER_MAX_ATTEMPTS",
        "AUTO_RECOVER_BASE_DELAY_SECONDS",
        "AUTO_RECOVER_BACKOFF_MULTIPLIER",
        "AUTO_RECOVER_COOLDOWN_SECONDS",
        "AUTO_RECOVER_START_GRACE_SECONDS",
    ]),
    ("LOGGING", [
        "LOG_TO_FILE_ENABLED",
        "LOG_RUN_FILE_PREFIX",
        "LOG_SEPARATE_SESSION_FILES",
        "LOG_DIR",
        "LOG_RETENTION_COUNT",
    ]),
    ("OBS PROFILE SAFETY", [
        "OBS_PROFILE_CHECK_ENABLED",
        "OBS_EXPECTED_PROFILE_NAME",
        "OBS_PROFILE_MISMATCH_ACTION",
        "OBS_PROFILE_SWITCH_GRACE_SECONDS",
    ]),
    ("INTRO VIDEO SEQUENCE", [
        "INTRO_SEQUENCE_ENABLED",
        "OBS_INTRO_INPUT_NAME",
        "OBS_INTRO_SCENE_NAME",
        "INTRO_END_SCENE_NAME",
        "INTRO_POLL_SECONDS",
        "INTRO_MAX_SECONDS",
        "INTRO_RESTART_GRACE_SECONDS",
        "INTRO_ENDED_GUARD_SECONDS",
        "INTRO_FORCE_CUT_TRANSITION",
        "INTRO_SCENE_SWITCH_RETRIES",
        "INTRO_SCENE_SWITCH_RETRY_SECONDS",
        "INTRO_ABORT_IF_SCENE_NOT_LIVE",
        "INTRO_POST_HOLD_SECONDS",
        "INTRO_DISABLE_ON_END",
    ]),
    ("STREAM SAFETY", [
        "STOP_DELAY_SECONDS",
        "START_DEBOUNCE_SECONDS",
    ]),
    ("TIMER AUTO-START", CFG_UI_TIMER_FIELDS),
    ("WEB HUD", [
        "WEB_HUD_ENABLED",
        "WEB_HUD_LOG_LINES",
        "CONFIG_HELP_ENABLED",
    ]),
    ("DIRECTOR PREVIEW", [
        "DIRECTOR_PREVIEW_ENABLED",
        "DIRECTOR_PREVIEW_BACKEND",
        "DIRECTOR_PREVIEW_REFRESH_MS",
        "DIRECTOR_PREVIEW_WIDTH",
        "DIRECTOR_PREVIEW_HEIGHT",
        "DIRECTOR_PREVIEW_JPEG_QUALITY",
        "DIRECTOR_PREVIEW_STALE_AFTER_MS",
    ]),
    ("WEB HUD — YouTube viewer", [
        "YOUTUBE_LIVE_URL",
        "YOUTUBE_CHANNEL_ID",
    ]),
    ("WEB HUD — Sync page", [
        "SYNC_OFFSET_WEB_ENABLED",
        "SYNC_OFFSET_STEP_MS",
        "SYNC_OFFSET_COARSE_STEP_MS",
        "SYNC_OFFSET_UNLOCK_SECONDS",
        "SYNC_OFFSET_MIN_MS",
        "SYNC_OFFSET_MAX_MS",
    ]),
    ("PRESET DELAYS", [
        "ENABLE_PRESET_DELAYS",
        "PRESET_DELAYS_SECONDS",
    ]),
    ("MIDI INPUT", [
        "MIDI_INPUT_PORT_SUBSTRING",
        "MIDI_CHANNEL_1_BASED",
        "MIDI_NEXT_CHANNEL_1_BASED",
        "NOTE_START_STREAM",
        "NOTE_STOP_STREAM",
        "NOTE_REC_TOGGLE",
        "NOTE_PRESET_FIRST",
        "NOTE_PRESET_LAST",
    ]),
    ("SERVICE-END MASTER SEQUENCE", [
        "SERVICE_END_SEQUENCE_ENABLED",
        "MIDI_STOP_TRIGGERS_FULL_SEQUENCE",
        "SERVICE_END_USB_ROOT",
        "SERVICE_END_POST_STOP_WAIT_SECONDS",
        "SERVICE_END_COPY_PREVIOUS_LOGS",
        "SERVICE_END_COPY_TODAYS_MP4",
        "OBS_RECORDING_PATH",
        "SERVICE_END_CLOSE_PROCLAIM",
        "SERVICE_END_CLOSE_MASTER_FADER",
        "SERVICE_END_CLOSE_OBS",
        "SERVICE_END_WINDOWS_SHUTDOWN",
        "SERVICE_END_SHUTDOWN_DELAY_SECONDS",
    ]),
    ("READ-ONLY (edit in VS Code)", [
        "OBS_HOST",
        "OBS_PORT",
        "OBS_PASSWORD",
        "WEB_HUD_HOST",
        "WEB_HUD_PORT",
        "WEB_HUD_TOKEN",
    ]),
]

# Short coaching text used by the Web HUD config help page.
CONFIG_FIELD_COACHING = {'HOME_TEST_MODE': 'Bench-test mode. It simulates camera moves and suppresses some church-only actions; keep it off for normal service use.', 'OBS_SCENE_INTRO': 'OBS scene that contains the introduction video. The app cuts here before restarting the intro media.', 'OBS_SCENE_WEST': 'OBS Program scene used when the West camera is selected live.', 'OBS_SCENE_EAST': 'OBS Program scene used when the East camera is selected live.', 'OBS_SAFE_FALLBACK_SCENE': 'Safe scene used when routing cannot confidently provide the requested shot.', 'FORCE_CUT_TRANSITION': 'Forces app-directed camera switches to use Cut. This is safest for buffered RTSP camera sources.', 'OBS_CUT_TRANSITION_NAME': 'Exact OBS transition name used when a forced cut is requested. Usually Cut.', 'OBS_VIDEO_INPUT_WEST': 'OBS source/input name for the West camera video. Director previews and checks use this name.', 'OBS_VIDEO_INPUT_EAST': 'OBS source/input name for the East camera video. Director previews and checks use this name.', 'AXIS_USERNAME': 'Axis login username for camera control. Change only if camera accounts change.', 'AXIS_PASSWORD': 'Axis camera password. It is read-only in the Web HUD for safety.', 'AXIS_USE_HTTPS': 'Use HTTPS only if the Axis cameras are configured for it. The current church setup normally uses plain HTTP on the private camera LAN.', 'AXIS_COMMAND_TIMEOUT_SECONDS': 'How long Stream Agent waits for an Axis preset command before calling it a camera-control error.', 'WEST_AXIS_IP': 'Network address for the West Axis camera. A wrong IP means West presets and previews will fail.', 'WEST_AXIS_CAMERA_ID': 'Usually 1. Change only if this Axis unit exposes multiple internal camera channels.', 'EAST_AXIS_IP': 'Network address for the East Axis camera. A wrong IP means East presets and previews will fail.', 'EAST_AXIS_CAMERA_ID': 'Usually 1. Change only if this Axis unit exposes multiple internal camera channels.', 'AXIS_VIEW_PRESET_NAMES': 'Exact preset names stored inside both Axis cameras. These are case-sensitive; Podium and podium are different.', 'WEST_CAMERA_ENABLED': 'Disables West camera routing if the camera is unavailable. Useful as a temporary fault workaround.', 'EAST_CAMERA_ENABLED': 'Disables East camera routing if the camera is unavailable. Useful as a temporary fault workaround.', 'INITIAL_CAMERA_VIEWS': 'Startup assumption for where each camera is parked. This helps routing before the app has moved the cameras itself.', 'AUDIO_MODE': 'Selects the audio design: shared ASIO input or embedded Axis camera audio. This controls whether the Sync page is useful.', 'OBS_AUDIO_INPUT_SHARED': 'OBS input name for the single shared ASIO audio source, normally ASIO_audio.', 'WEB_HUD_AUDIO_MASTER_ENABLED': 'Shows the Web HUD audio master fader and allows browser volume control of the configured audio targets.', 'AUDIO_MASTER_MIN_DB': 'Lowest dB value available on the Web HUD audio fader.', 'AUDIO_MASTER_MAX_DB': 'Highest dB value available on the Web HUD audio fader. Keep conservative to avoid accidental overload.', 'AUDIO_MASTER_DEFAULT_DB': 'Initial master fader value used by the Web HUD when the app starts.', 'AUDIO_METER_STALE_AFTER_SECONDS': 'How long the Web HUD audio meter may hold the last OBS meter value if new meter events stop arriving.', 'AUTO_MINIMIZE_ENABLED': 'Allows the small PC HUD window to minimize itself after startup or stream start.', 'AUTO_MINIMIZE_AFTER_SECONDS': 'Delay before the PC HUD auto-minimizes.', 'MINIMIZE_ON_STARTUP': 'Minimizes the PC HUD shortly after launching the app.', 'AUTO_RESTORE_ON_ISSUE': 'Reserved safety behavior for restoring the PC HUD if an issue occurs.', 'AUTO_BRING_TO_FRONT_ON_STREAM_START': 'Optional desktop behavior to bring the PC HUD forward when streaming starts.', 'AUTO_RECOVER_ENABLED': 'Allows the app to attempt a stream restart if OBS unexpectedly stops while streaming was desired.', 'AUTO_RECOVER_MAX_ATTEMPTS': 'Maximum restart attempts before the app pauses recovery.', 'AUTO_RECOVER_BASE_DELAY_SECONDS': 'Delay before the first recovery retry. Later retries can be longer.', 'AUTO_RECOVER_BACKOFF_MULTIPLIER': 'Multiplier that increases the delay between repeated recovery attempts.', 'AUTO_RECOVER_COOLDOWN_SECONDS': 'Pause time after maximum recovery attempts are reached.', 'AUTO_RECOVER_START_GRACE_SECONDS': 'Grace time after a start request before the app judges stream-off as a failure.', 'UNEXPECTED_STOP_DEBOUNCE_SECONDS': 'How long stream-off must persist before it is treated as an unexpected stop.', 'LOG_TO_FILE_ENABLED': 'Turns run log file writing on or off.', 'LOG_RUN_FILE_PREFIX': 'Filename prefix used for Stream Agent run and session logs.', 'LOG_SEPARATE_SESSION_FILES': 'Creates an additional per-stream session log for easier Sunday troubleshooting.', 'LOG_DIR': 'Optional folder for log files. Blank means use the app folder.', 'LOG_RETENTION_COUNT': 'How many older run logs to keep before cleanup removes extras.', 'OBS_PROFILE_CHECK_ENABLED': 'Checks the active OBS profile before starting, to reduce wrong stream-key/profile mistakes.', 'OBS_EXPECTED_PROFILE_NAME': 'Exact OBS profile name expected for normal church streaming. Church default is NHLC with no trailing spaces.', 'OBS_PROFILE_MISMATCH_ACTION': 'What to do when OBS is on the wrong profile: block, warn, or switch.', 'OBS_PROFILE_SWITCH_GRACE_SECONDS': 'Wait time after switching OBS profiles before trying to start streaming.', 'INTRO_SEQUENCE_ENABLED': 'Enables automatic Introduction scene playback after a successful stream start.', 'OBS_INTRO_INPUT_NAME': 'OBS media source/input name for the intro video.', 'OBS_INTRO_SCENE_NAME': 'OBS scene that must be live before the intro video is restarted.', 'INTRO_END_SCENE_NAME': 'Scene to cut to after the intro finishes or times out.', 'INTRO_POLL_SECONDS': 'How often the app checks the intro media state.', 'INTRO_MAX_SECONDS': 'Maximum time to wait for the intro before falling through to the live scene.', 'INTRO_RESTART_GRACE_SECONDS': 'Short wait after restarting the intro media before checking its state.', 'INTRO_ENDED_GUARD_SECONDS': 'Prevents a stale ENDED state from immediately skipping a freshly restarted intro.', 'INTRO_FORCE_CUT_TRANSITION': 'Uses the Cut transition for intro scene changes, independent of normal service transitions.', 'INTRO_SCENE_SWITCH_RETRIES': 'How many times to try confirming the Introduction scene is actually live.', 'INTRO_SCENE_SWITCH_RETRY_SECONDS': 'Delay between intro scene verification attempts.', 'INTRO_ABORT_IF_SCENE_NOT_LIVE': 'If enabled, skips the intro rather than playing the intro source off-air.', 'INTRO_POST_HOLD_SECONDS': 'Small hold after intro end before cutting to the live scene.', 'INTRO_DISABLE_ON_END': 'Legacy option for stacked intro-source designs. Leave off for the current scene-based intro workflow.', 'STOP_DELAY_SECONDS': 'Countdown time before OBS Stop Stream is sent. Helps avoid accidental immediate shutdown.', 'START_DEBOUNCE_SECONDS': 'Minimum spacing between repeated start requests to avoid double-start behavior.', 'USE_TIMER_START': 'Enables the automatic Sunday service start timer.', 'TIMER_START_HHMM': 'Local 24-hour time for the timer, such as 9:55.', 'TIMER_WEEKDAY': 'Day for the timer: Monday=0 through Sunday=6.', 'TIMEZONE': 'Timezone used by the timer. Regina should normally stay America/Regina.', 'TIMER_PERSIST_STATE': 'Stores whether the timer already fired today so it does not fire repeatedly.', 'TIMER_STATE_FILE': 'Small local file used to remember today’s timer fired/missed state.', 'TIMER_FIRE_GRACE_MINUTES': 'How many minutes after the scheduled time the timer is still allowed to start the stream.', 'TZ_FALLBACK_MODE': 'Fallback time mode used only if the named timezone cannot be loaded.', 'TZ_FALLBACK_UTC_OFFSET_HOURS': 'Fixed UTC offset used only by fallback time mode.', 'WEB_HUD_ENABLED': 'Enables the browser-based HUD served by Stream Agent.', 'WEB_HUD_LOG_LINES': 'Number of recent log lines shown in the Web HUD.', 'CONFIG_HELP_ENABLED': 'Shows coaching notes and Help buttons in config pages.', 'DIRECTOR_PREVIEW_ENABLED': 'Enables preview images on the Director page.', 'DIRECTOR_PREVIEW_BACKEND': 'Chooses whether previews come from OBS screenshots or directly from Axis snapshots/MJPEG.', 'DIRECTOR_PREVIEW_REFRESH_MS': 'How often Director preview images update.', 'DIRECTOR_PREVIEW_WIDTH': 'Requested preview image width.', 'DIRECTOR_PREVIEW_HEIGHT': 'Requested preview image height.', 'DIRECTOR_PREVIEW_JPEG_QUALITY': 'JPEG quality used for Director previews. Higher looks better but uses more bandwidth.', 'DIRECTOR_PREVIEW_STALE_AFTER_MS': 'How old a preview can be before the HUD marks it stale.', 'YOUTUBE_LIVE_URL': 'Public YouTube live URL opened by the View Live button.', 'YOUTUBE_CHANNEL_ID': 'YouTube channel ID used by the embedded viewer page.', 'SYNC_OFFSET_WEB_ENABLED': 'Enables Web HUD sync controls when shared ASIO audio mode is active.', 'SYNC_OFFSET_STEP_MS': 'Small sync-adjust step size in milliseconds.', 'SYNC_OFFSET_COARSE_STEP_MS': 'Large sync-adjust step size in milliseconds.', 'SYNC_OFFSET_UNLOCK_SECONDS': 'How long sync controls stay unlocked after pressing unlock.', 'SYNC_OFFSET_MIN_MS': 'Minimum allowed ASIO sync offset.', 'SYNC_OFFSET_MAX_MS': 'Maximum allowed ASIO sync offset.', 'ENABLE_PRESET_DELAYS': 'Master switch for blind delays. Leave enabled for normal service so off-air PTZ moves are hidden before the cut.', 'PRESET_DELAYS_SECONDS': 'Delay table for each service view. These values protect viewers from seeing camera motion.', 'MIDI_INPUT_PORT_SUBSTRING': 'Text used to find the Proclaim MIDI input port.', 'MIDI_CHANNEL_1_BASED': 'MIDI channel used for immediate/current view cues.', 'MIDI_NEXT_CHANNEL_1_BASED': 'MIDI channel used for next-view preparation cues.', 'NOTE_START_STREAM': 'MIDI note that requests Start Stream.', 'NOTE_STOP_STREAM': 'MIDI note that starts the stop countdown.', 'NOTE_REC_TOGGLE': 'MIDI note that toggles OBS recording.', 'NOTE_PRESET_FIRST': 'First MIDI note in the service-view range.', 'NOTE_PRESET_LAST': 'Last MIDI note in the service-view range.', 'SERVICE_END_SEQUENCE_ENABLED': 'Master switch for optional end-of-service copy/close/shutdown automation.', 'MIDI_STOP_TRIGGERS_FULL_SEQUENCE': 'Allows the MIDI stop cue to trigger the full service-end sequence.', 'SERVICE_END_USB_ROOT': 'Destination root folder for copied service logs/recordings.', 'SERVICE_END_POST_STOP_WAIT_SECONDS': 'Cooldown after OBS stops before service-end copy/close steps run.', 'SERVICE_END_COPY_PREVIOUS_LOGS': 'Also copy a small number of previous logs for troubleshooting context.', 'SERVICE_END_COPY_TODAYS_MP4': 'Copy today’s most recent MP4 recording if a recording path is configured.', 'OBS_RECORDING_PATH': 'Folder where OBS saves MP4 recordings, used by the service-end copy step.', 'SERVICE_END_CLOSE_PROCLAIM': 'Closes Proclaim during the optional service-end sequence.', 'SERVICE_END_CLOSE_MASTER_FADER': 'Closes Master Fader during the optional service-end sequence.', 'SERVICE_END_CLOSE_OBS': 'Closes OBS during the optional service-end sequence.', 'SERVICE_END_WINDOWS_SHUTDOWN': 'If enabled, Windows shutdown is started after service-end tasks.', 'SERVICE_END_SHUTDOWN_DELAY_SECONDS': 'Abort window before Windows shutdown completes.', 'OBS_HOST': 'OBS WebSocket host. Read-only in the HUD to avoid disconnecting control mid-service.', 'OBS_PORT': 'OBS WebSocket port. Read-only in the HUD to avoid disconnecting control mid-service.', 'OBS_PASSWORD': 'OBS WebSocket password. Hidden from browser editing for safety.', 'WEB_HUD_HOST': 'Network bind address for the Web HUD. Read-only here to avoid stranding the page.', 'WEB_HUD_PORT': 'Web HUD port. Read-only here because changing it would move the page you are using.', 'WEB_HUD_TOKEN': 'Optional Web HUD access token. Read-only in the browser for safety.'}


def _cfg_type_name(val) -> str:
    if isinstance(val, bool):
        return "bool"
    if isinstance(val, int) and not isinstance(val, bool):
        return "int"
    if isinstance(val, float):
        return "float"
    if isinstance(val, str):
        return "str"
    if isinstance(val, dict):
        return "Dict"
    return type(val).__name__

def _cfg_make_display_line(key: str, val) -> str:
    tname = _cfg_type_name(val)
    if isinstance(val, str):
        v = json.dumps(val)  # adds quotes + escapes
        return f"{key}: {tname} = {v}"
    if isinstance(val, bool):
        return f"{key}: {tname} = {str(val)}"
    if isinstance(val, float):
        # Keep one decimal if it looks like an integer, else trim lightly
        if abs(val - round(val)) < 1e-9:
            return f"{key}: {tname} = {val:.1f}"
        return f"{key}: {tname} = {val:g}"
    return f"{key}: {tname} = {val}"

def _cfg_bool_from_value(value) -> bool:
    """Coerce browser/imported values to a real bool without treating 'false' as True."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    s = str(value or "").strip().lower()
    if s in ("1", "true", "yes", "on", "enabled"):
        return True
    if s in ("0", "false", "no", "off", "disabled", ""):
        return False
    return bool(value)

def _cfg_load_overrides_file(path: str = CFG_OVERRIDE_PATH) -> dict:
    try:
        if not path or not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "overrides" in data and isinstance(data["overrides"], dict):
            return data["overrides"]
        if isinstance(data, dict):
            # allow legacy flat dict
            return data
        return {}
    except Exception:
        return {}

def _cfg_save_overrides_file(overrides: dict, path: str = CFG_OVERRIDE_PATH) -> bool:
    try:
        payload = {"version": 1, "saved_utc": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z", "overrides": overrides or {}}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        return True
    except Exception:
        return False

def _cfg_append_changelog(entry: dict, path: str = CFG_CHANGELOG_PATH):
    try:
        entry = dict(entry or {})
        entry.setdefault("ts_utc", dt.datetime.utcnow().isoformat(timespec="seconds") + "Z")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\\n")
    except Exception:
        pass

def _cfg_apply_overrides(cfg: Config, overrides: dict):
    if not overrides:
        return
    # Apply only known + allowed fields
    for k, v in list(overrides.items()):
        if k in CFG_UI_READONLY_ALWAYS:
            continue
        if not hasattr(cfg, k):
            continue
        try:
            if k == "PRESET_DELAYS_SECONDS" and isinstance(v, dict):
                nd = {}
                for kk, vv in v.items():
                    try:
                        nd[int(kk)] = int(vv)
                    except Exception:
                        pass
                if nd:
                    setattr(cfg, k, nd)
                continue

            cur = getattr(cfg, k)
            # Enum normalization
            if k in CFG_UI_ENUM_OPTIONS:
                if isinstance(v, str) and v in CFG_UI_ENUM_OPTIONS[k]:
                    setattr(cfg, k, v)
                continue

            if isinstance(cur, bool):
                setattr(cfg, k, _cfg_bool_from_value(v))
            elif isinstance(cur, int) and not isinstance(cur, bool):
                setattr(cfg, k, int(v))
            elif isinstance(cur, float):
                setattr(cfg, k, float(v))
            elif isinstance(cur, str):
                if k == "TIMER_START_HHMM":
                    raw = str(v or "").strip()
                    parts = raw.split(":")
                    if len(parts) != 2:
                        continue
                    hh, mm = int(parts[0]), int(parts[1])
                    if not (0 <= hh <= 23 and 0 <= mm <= 59):
                        continue
                    setattr(cfg, k, f"{hh}:{mm:02d}")
                else:
                    setattr(cfg, k, str(v))
            else:
                # skip unknown types
                pass
        except Exception:
            pass

_CFG_OVERRIDES = _cfg_load_overrides_file()
_cfg_apply_overrides(CFG, _CFG_OVERRIDES)


def _safe_lower(s: str) -> str:
    return (s or "").lower().strip()


def get_tz(cfg: Config):
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(cfg.TIMEZONE)
    except Exception:
        return None


def now_in_cfg_tz(cfg: Config) -> dt.datetime:
    tz = get_tz(cfg)
    if tz is not None:
        return dt.datetime.now(tz)
    if cfg.TZ_FALLBACK_MODE == "fixed_offset":
        off = dt.timezone(dt.timedelta(hours=cfg.TZ_FALLBACK_UTC_OFFSET_HOURS))
        return dt.datetime.now(off)
    return dt.datetime.now()


def parse_hhmm(hhmm: str) -> Tuple[int, int]:
    """Parse an H:MM / HH:MM time string and validate the 24-hour range."""
    raw = str(hhmm or "").strip()
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time '{raw}' (expected HH:MM)")
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"Invalid time '{raw}' (expected 00:00..23:59)")
    return hh, mm


def fmt_hms(seconds: int) -> str:
    if seconds < 0:
        seconds = 0
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"



class AxisPresetCamera:
    """Minimal Axis preset-recall helper for West / Center cameras.

    Uses the Axis PTZ CGI named server-preset recall command:
      /axis-cgi/com/ptz.cgi?gotoserverpresetname=<PresetName>&camera=<id>

    Authentication is attempted with urllib digest/basic handlers first, and then with
    an explicit Basic Authorization header fallback for compatibility with different
    Axis firmware behaviors.
    """

    def __init__(self, base_url: str, username: str, password: str, camera_id: int = 1, timeout_s: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.username = username or ""
        self.password = password or ""
        self.camera_id = int(camera_id or 1)
        self.timeout_s = float(timeout_s or 3.0)

    def _build_url(self, preset_name: str) -> str:
        params = {
            "gotoserverpresetname": str(preset_name),
            "camera": self.camera_id,
        }
        return self.base_url + "/axis-cgi/com/ptz.cgi?" + urlparse.urlencode(params)

    def recall_preset_name(self, preset_name: str):
        preset_name = (preset_name or "").strip()
        if not preset_name:
            raise ValueError("Axis preset name is blank")

        url = self._build_url(preset_name)

        last_err = None
        if self.username or self.password:
            try:
                pw_mgr = urlrequest.HTTPPasswordMgrWithDefaultRealm()
                pw_mgr.add_password(None, self.base_url + "/", self.username, self.password)
                handlers = [
                    urlrequest.HTTPDigestAuthHandler(pw_mgr),
                    urlrequest.HTTPBasicAuthHandler(pw_mgr),
                ]
                opener = urlrequest.build_opener(*handlers)
                with opener.open(url, timeout=self.timeout_s) as resp:
                    code = getattr(resp, "status", None) or getattr(resp, "code", None) or 0
                    if int(code) < 200 or int(code) >= 300:
                        raise RuntimeError(f"Axis preset HTTP {code}")
                    return
            except Exception as e:
                last_err = e

        try:
            req = urlrequest.Request(url)
            if self.username or self.password:
                import base64
                token = base64.b64encode((f"{self.username}:{self.password}").encode("utf-8")).decode("ascii")
                req.add_header("Authorization", "Basic " + token)
            with urlrequest.urlopen(req, timeout=self.timeout_s) as resp:
                code = getattr(resp, "status", None) or getattr(resp, "code", None) or 0
                if int(code) < 200 or int(code) >= 300:
                    raise RuntimeError(f"Axis preset HTTP {code}")
                return
        except Exception as e:
            if last_err is not None:
                raise RuntimeError(f"{last_err}; fallback auth error: {e}")
            raise

class ObsController:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client: Optional[ReqClient] = None
        self.last_error: str = ""
        self.connected: bool = False
        self._version_info = None
        self._chosen_screenshot_format = None

    def connect(self) -> bool:
        if ReqClient is None:
            self.last_error = "obsws-python not installed"
            return False
        try:
            self.client = ReqClient(host=self.cfg.OBS_HOST, port=self.cfg.OBS_PORT,
                                    password=self.cfg.OBS_PASSWORD or None, timeout=5)
            try:
                self._version_info = self.client.get_version()
            except Exception:
                self._version_info = None
            self.connected = True
            self.last_error = ""
            return True
        except Exception as e:
            self.client = None
            self.connected = False
            self.last_error = str(e)
            return False

    def _ok(self) -> bool:
        return self.connected and self.client is not None

    def get_status(self) -> Tuple[bool, bool, str]:
        if not self._ok():
            return False, False, self.last_error or "OBS offline"
        try:
            out = self.client.get_stream_status()
            streaming = bool(getattr(out, "output_active", False))
            rec = self.client.get_record_status()
            recording = bool(getattr(rec, "output_active", False))
            return streaming, recording, ""
        except Exception as e:
            emsg = str(e)
            code = getattr(e, "code", None)
            msg_l = emsg.lower()
            # OBS sometimes reports NotReady while it is starting, switching profiles,
            # or shutting down. That is a transient state, not proof the WebSocket is dead.
            if (code == 207) or ("notready" in msg_l) or ("not ready" in msg_l):
                self.last_error = emsg
                return False, False, self.last_error
            self.connected = False
            self.last_error = emsg
            return False, False, self.last_error

    def start_stream(self) -> Tuple[bool, str]:
        """Idempotent start:
        - If already streaming, treat redundant start as success (no alarm).
        - Do NOT mark OBS disconnected for benign websocket errors like OutputRunning / NotReady.
        """
        if not self._ok():
            return False, "OBS not connected"

        # Pre-check: if already streaming, no-op success
        try:
            st = self.client.get_stream_status()
            if bool(getattr(st, "output_active", False)):
                return True, "already streaming (no action)"
        except Exception:
            # If status can't be read, still attempt start below.
            pass

        try:
            self.client.start_stream()
            return True, "start stream sent"
        except Exception as e:
            emsg = str(e)
            code = getattr(e, "code", None)

            # Best-effort verify: if OBS reports streaming, treat as success even if the request errored.
            try:
                st = self.client.get_stream_status()
                if bool(getattr(st, "output_active", False)):
                    return True, f"already streaming (verified after error: {code if code is not None else 'n/a'})"
            except Exception:
                pass

            # Benign / expected cases
            msg_l = emsg.lower()
            if (code == 500) or ("outputrunning" in msg_l) or ("output running" in msg_l) or ("already running" in msg_l) or ("already active" in msg_l):
                return True, "already streaming (OutputRunning)"

            if (code == 207) or ("notready" in msg_l) or ("not ready" in msg_l):
                # Not a disconnect; OBS is still starting/busy.
                self.last_error = emsg
                return False, emsg

            # Unknown error: treat as disconnect/offline
            self.connected = False
            self.last_error = emsg
            return False, emsg

    def stop_stream(self) -> Tuple[bool, str]:
        """Idempotent stop.

        A Stop command should be safe even if OBS has already stopped. Treat
        "already stopped / output not running" as a successful no-op instead of
        marking OBS disconnected.
        """
        if not self._ok():
            return False, "OBS not connected"

        try:
            st = self.client.get_stream_status()
            if not bool(getattr(st, "output_active", False)):
                return True, "already stopped (no action)"
        except Exception:
            # If status cannot be read, still attempt StopStream below.
            pass

        try:
            self.client.stop_stream()
            return True, "stop stream sent"
        except Exception as e:
            emsg = str(e)
            code = getattr(e, "code", None)
            msg_l = emsg.lower()
            if ("outputnotrunning" in msg_l) or ("output not running" in msg_l) or ("not running" in msg_l) or ("already stopped" in msg_l):
                return True, "already stopped (OutputNotRunning)"
            if (code == 207) or ("notready" in msg_l) or ("not ready" in msg_l):
                self.last_error = emsg
                return False, emsg
            self.connected = False
            self.last_error = emsg
            return False, self.last_error

    def toggle_record(self) -> Tuple[bool, str]:
        if not self._ok():
            return False, "OBS not connected"
        try:
            st = self.client.get_record_status()
            active = bool(getattr(st, "output_active", False))
            if active:
                self.client.stop_record()
                return True, "stop record sent"
            self.client.start_record()
            return True, "start record sent"
        except Exception as e:
            self.connected = False
            self.last_error = str(e)
            return False, self.last_error

    def _safe_call(self, method_name: str, **kwargs):
        if not self.connected or not self.client:
            return None, "OBS not connected"
        fn = getattr(self.client, method_name, None)
        if fn is None:
            return None, f"missing method: {method_name}"
        try:
            resp = fn(**kwargs) if kwargs else fn()
            return resp, ""
        except Exception as e:
            return None, str(e)

    @staticmethod
    def _get(obj, key: str, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @staticmethod
    def _contains_text(container, needle: str) -> bool:
        if not needle:
            return False
        try:
            if isinstance(container, str):
                return needle.lower() in container.lower()
            if isinstance(container, dict):
                return any(ObsController._contains_text(v, needle) for v in container.values())
            if isinstance(container, (list, tuple)):
                return any(ObsController._contains_text(v, needle) for v in container)
        except Exception:
            return False
        return False


    # -----------------------------
    # OBS Profile helpers (preflight safety)
    # -----------------------------
    def get_current_profile_name(self) -> Tuple[str, str]:
        """Return (current_profile_name, err)."""
        resp, err = self._safe_call("get_profile_list")
        if err or resp is None:
            return "", err or "get_profile_list failed"
        current = (self._get(resp, "currentProfileName")
                   or self._get(resp, "current_profile_name")
                   or "")
        return str(current), ""

    def set_current_profile_name(self, name: str) -> Tuple[bool, str]:
        """Switch OBS to the specified profile. Returns (ok, err).

        For the church build, leading/trailing whitespace in the configured
        expected profile is treated as accidental and removed before switching.
        This avoids an invisible trailing-space typo blocking a service start.
        """
        name = "" if name is None else str(name).strip()
        if not name:
            return False, "empty profile name"
        if not self._ok():
            return False, self.last_error or "OBS offline"

        fn = getattr(self.client, "set_current_profile", None) if self.client else None
        if fn is None:
            return False, "missing method: set_current_profile"

        # Prefer positional argument (most compatible across obsws-python versions).
        last_type_error = None
        try:
            fn(name)
            return True, ""
        except TypeError as e:
            last_type_error = e
        except Exception as e:
            return False, str(e)

        # Fallback: try common keyword parameter names.
        for kw in ("profileName", "profile_name", "profile", "name"):
            try:
                fn(**{kw: name})
                return True, ""
            except TypeError as e:
                last_type_error = e
                continue
            except Exception as e:
                return False, str(e)

        if last_type_error is not None:
            return False, f"{last_type_error}"
        return False, "could not call set_current_profile() with supported parameters"
    def get_supported_image_formats(self) -> List[str]:
        info = getattr(self, "_version_info", None)
        vals = []
        try:
            vals = self._get(info, "supportedImageFormats") or self._get(info, "supported_image_formats") or []
        except Exception:
            vals = []
        out: List[str] = []
        for v in (vals or []):
            try:
                s = str(v or "").strip().lower()
            except Exception:
                s = ""
            if s and s not in out:
                out.append(s)
        return out

    def choose_screenshot_format(self, preferred: str = "jpeg") -> str:
        cached = str(getattr(self, "_chosen_screenshot_format", "") or "").strip().lower()
        if cached:
            return cached
        fmts = self.get_supported_image_formats()
        preferreds = []
        raw_pref = str(preferred or "jpeg").strip().lower()
        if raw_pref:
            preferreds.extend([raw_pref, raw_pref.replace("image/", ""), raw_pref.replace("jpg", "jpeg")])
        preferreds.extend(["jpeg", "jpg", "png", "webp"])
        for cand in preferreds:
            cand = str(cand or "").strip().lower()
            if not cand:
                continue
            if (not fmts) or (cand in fmts):
                self._chosen_screenshot_format = cand
                return cand
        self._chosen_screenshot_format = fmts[0] if fmts else "jpeg"
        return self._chosen_screenshot_format

    def get_source_screenshot_bytes(
        self,
        source_name: str,
        image_width: int,
        image_height: int,
        image_quality: int = 70,
        image_format: str = "image/jpeg",
    ) -> Tuple[Optional[bytes], str, str]:
        """Return (bytes, content_type, err) for an OBS source screenshot."""
        if not self._ok():
            return None, "", self.last_error or "OBS offline"

        source_name = (source_name or "").strip()
        if not source_name:
            return None, "", "blank source name"

        try:
            w = max(8, min(4096, int(image_width or 0)))
            h = max(8, min(4096, int(image_height or 0)))
            q = max(-1, min(100, int(image_quality if image_quality is not None else 70)))
        except Exception:
            return None, "", "invalid screenshot dimensions/quality"

        fmt = self.choose_screenshot_format(image_format or "jpeg")

        def _decode_image_data(resp_obj) -> Tuple[Optional[bytes], str, str]:
            img_data = self._get(resp_obj, "imageData") or self._get(resp_obj, "image_data")
            if not isinstance(img_data, str) or not img_data:
                return None, "", "OBS screenshot returned no image data"
            content_type = ("image/" + fmt.lstrip(".")) if "/" not in fmt else fmt
            payload = img_data
            if img_data.startswith("data:"):
                try:
                    header, payload = img_data.split(",", 1)
                    if ";base64" in header:
                        content_type = header[5:].split(";", 1)[0] or fmt
                except Exception:
                    payload = img_data
            try:
                return base64.b64decode(payload), content_type or fmt, ""
            except Exception as e:
                return None, "", f"OBS screenshot decode failed: {e}"

        last_err = "GetSourceScreenshot failed"
        fn = getattr(self.client, "get_source_screenshot", None) if self.client else None
        if fn is not None:
            try_variants = [
                lambda: fn(source_name, fmt, w, h, q),
                lambda: fn(source_name, fmt, image_width=w, image_height=h, image_compression_quality=q),
                lambda: fn(source_name, fmt, width=w, height=h, compression_quality=q),
                lambda: fn(source_name, image_format=fmt, image_width=w, image_height=h, image_compression_quality=q),
            ]
            for caller in try_variants:
                try:
                    resp = caller()
                    data, ctype, derr = _decode_image_data(resp)
                    if not derr:
                        return data, ctype, ""
                    last_err = derr
                except TypeError as e:
                    last_err = str(e)
                    continue
                except Exception as e:
                    last_err = str(e)
                    break

        for kwargs in (
            {"sourceName": source_name, "imageFormat": fmt, "imageWidth": w, "imageHeight": h, "imageCompressionQuality": q},
            {"source_name": source_name, "image_format": fmt, "image_width": w, "image_height": h, "image_compression_quality": q},
            {"source": source_name, "image_format": fmt, "width": w, "height": h, "quality": q},
        ):
            resp, err = self._safe_call("get_source_screenshot", **kwargs)
            if not err:
                data, ctype, derr = _decode_image_data(resp)
                if not derr:
                    return data, ctype, ""
                last_err = derr
                continue
            last_err = err
            if "unexpected keyword argument" in str(err):
                continue

        send_fn = getattr(self.client, "send", None) if self.client else None
        if send_fn is not None:
            payload = {
                "sourceName": source_name,
                "imageFormat": fmt,
                "imageWidth": w,
                "imageHeight": h,
                "imageCompressionQuality": q,
            }
            for raw_flag in (True, False):
                try:
                    resp = send_fn("GetSourceScreenshot", payload, raw=raw_flag)
                    data, ctype, derr = _decode_image_data(resp)
                    if not derr:
                        return data, ctype, ""
                    last_err = derr
                except TypeError:
                    try:
                        resp = send_fn("GetSourceScreenshot", payload)
                        data, ctype, derr = _decode_image_data(resp)
                        if not derr:
                            return data, ctype, ""
                        last_err = derr
                    except Exception as e:
                        last_err = str(e)
                except Exception as e:
                    last_err = str(e)

        return None, "", str(last_err or "GetSourceScreenshot failed")

    def set_current_program_scene_name(self, scene_name: str) -> Tuple[bool, str]:
        if not self._ok():
            return False, self.last_error or "OBS offline"
        scene_name = (scene_name or "").strip()
        if not scene_name:
            return False, "blank scene name"

        fn = getattr(self.client, "set_current_program_scene", None)
        last_err = None
        if fn is not None:
            try:
                fn(scene_name)
                return True, "scene switched"
            except TypeError as e:
                last_err = e
            except Exception as e:
                # Some obsws-python builds expose the convenience method but reject the
                # positional call style. Fall through to keyword and generic request paths
                # instead of failing immediately.
                last_err = e

            for kw in ("sceneName", "scene_name", "scene", "name"):
                try:
                    fn(**{kw: scene_name})
                    return True, "scene switched"
                except TypeError as e:
                    last_err = e
                    continue
                except Exception as e:
                    last_err = e
                    break

        for kwargs in (
            {"sceneName": scene_name},
            {"scene_name": scene_name},
            {"scene": scene_name},
            {"name": scene_name},
        ):
            r, err = self._safe_call("set_current_program_scene", **kwargs)
            if not err:
                return True, "scene switched"
            last_err = err
            if "unexpected keyword argument" in str(err):
                continue
            break

        return False, str(last_err or "set_current_program_scene failed")

    def set_current_scene_transition_name(self, transition_name: str) -> Tuple[bool, str]:
        if not self._ok():
            return False, self.last_error or "OBS offline"
        transition_name = (transition_name or "").strip()
        if not transition_name:
            return False, "blank transition name"

        fn = getattr(self.client, "set_current_scene_transition", None)
        last_err = None
        if fn is not None:
            try:
                fn(transition_name)
                return True, "transition set"
            except TypeError as e:
                last_err = e
            except Exception as e:
                last_err = e

            for kw in ("transitionName", "transition_name", "transition", "name"):
                try:
                    fn(**{kw: transition_name})
                    return True, "transition set"
                except TypeError as e:
                    last_err = e
                    continue
                except Exception as e:
                    last_err = e
                    break

        for kwargs in (
            {"transitionName": transition_name},
            {"transition_name": transition_name},
            {"transition": transition_name},
            {"name": transition_name},
        ):
            r, err = self._safe_call("set_current_scene_transition", **kwargs)
            if not err:
                return True, "transition set"
            last_err = err
            if "unexpected keyword argument" in str(err):
                continue
            break

        return False, str(last_err or "set_current_scene_transition failed")

    def get_input_names(self) -> Tuple[List[str], str]:
        if not self._ok():
            return [], self.last_error or "OBS offline"
        resp, err = self._safe_call("get_input_list")
        if err:
            return [], str(err)
        items = self._get(resp, "inputs") or self._get(resp, "input_list") or []
        names: List[str] = []
        try:
            for item in items:
                name = self._get(item, "inputName") or self._get(item, "input_name") or self._get(item, "sourceName") or self._get(item, "source_name")
                if name:
                    s = str(name)
                    if s not in names:
                        names.append(s)
        except Exception:
            pass
        return names, ""

    def get_input_volume_db(self, input_name: str) -> Tuple[Optional[float], str]:
        if not self._ok():
            return None, self.last_error or "OBS offline"
        r, err = self._safe_call("get_input_volume", inputName=input_name)
        if err and "unexpected keyword argument" in str(err):
            r, err = self._safe_call("get_input_volume", input_name=input_name)
        if err:
            return None, str(err)
        val = self._get(r, "inputVolumeDb")
        if val is None:
            val = self._get(r, "input_volume_db")
        try:
            return float(val), ""
        except Exception:
            return None, "volume unavailable"

    def set_input_volume_db(self, input_name: str, value_db: float) -> Tuple[bool, str]:
        """Set OBS input volume in dB.

        obsws-python wrapper signatures differ by release. Be generous here:
        - preferred direct wrapper call: set_input_volume(name, vol_db=...)
        - older positional fallback:      set_input_volume(name, None, db)
        - raw request fallback via client.send(...)
        - final _safe_call fallback for any future adapter we may add
        """
        if not self._ok():
            return False, self.last_error or "OBS offline"
        try:
            fn = getattr(self.client, "set_input_volume", None)
            if fn is not None:
                try:
                    fn(input_name, vol_db=float(value_db))
                    return True, "volume set"
                except TypeError:
                    try:
                        fn(input_name, None, float(value_db))
                        return True, "volume set"
                    except TypeError:
                        pass

            send_fn = getattr(self.client, "send", None)
            if send_fn is not None:
                try:
                    send_fn("SetInputVolume", {"inputName": input_name, "inputVolumeDb": float(value_db)})
                    return True, "volume set"
                except Exception:
                    pass
        except Exception as e:
            return False, str(e)

        r, err = self._safe_call("set_input_volume", inputName=input_name, inputVolumeDb=float(value_db))
        if err and "unexpected keyword argument" in str(err):
            r, err = self._safe_call("set_input_volume", input_name=input_name, vol_db=float(value_db))
        if err and "unexpected keyword argument" in str(err):
            r, err = self._safe_call("set_input_volume", input_name=input_name, input_volume_db=float(value_db))
        if err:
            return False, str(err)
        return True, "volume set"


    def get_input_audio_sync_offset_ms(self, input_name: str) -> Tuple[Optional[int], str]:
        if not self._ok():
            return None, self.last_error or "OBS offline"
        input_name = (input_name or "").strip()
        if not input_name:
            return None, "blank input name"

        def _parse(resp_obj):
            val = self._get(resp_obj, "inputAudioSyncOffset")
            if val is None:
                val = self._get(resp_obj, "input_audio_sync_offset")
            try:
                return int(round(float(val))), ""
            except Exception:
                return None, "audio sync offset unavailable"

        last_err = "GetInputAudioSyncOffset failed"
        fn = getattr(self.client, "get_input_audio_sync_offset", None) if self.client else None
        if fn is not None:
            try_variants = [
                lambda: fn(input_name),
                lambda: fn(inputName=input_name),
                lambda: fn(input_name=input_name),
            ]
            for caller in try_variants:
                try:
                    resp = caller()
                    val, err = _parse(resp)
                    if not err:
                        return val, ""
                    last_err = err
                except TypeError as e:
                    last_err = str(e)
                    continue
                except Exception as e:
                    last_err = str(e)
                    break

        for kwargs in (
            {"inputName": input_name},
            {"input_name": input_name},
            {"name": input_name},
        ):
            resp, err = self._safe_call("get_input_audio_sync_offset", **kwargs)
            if not err:
                val, perr = _parse(resp)
                if not perr:
                    return val, ""
                last_err = perr
                continue
            last_err = err
            if "unexpected keyword argument" in str(err):
                continue

        send_fn = getattr(self.client, "send", None) if self.client else None
        if send_fn is not None:
            payload = {"inputName": input_name}
            for raw_flag in (True, False):
                try:
                    resp = send_fn("GetInputAudioSyncOffset", payload, raw=raw_flag)
                    val, err = _parse(resp)
                    if not err:
                        return val, ""
                    last_err = err
                except TypeError:
                    try:
                        resp = send_fn("GetInputAudioSyncOffset", payload)
                        val, err = _parse(resp)
                        if not err:
                            return val, ""
                        last_err = err
                    except Exception as e:
                        last_err = str(e)
                except Exception as e:
                    last_err = str(e)

        return None, str(last_err or "GetInputAudioSyncOffset failed")

    def set_input_audio_sync_offset_ms(self, input_name: str, value_ms: int) -> Tuple[bool, str]:
        if not self._ok():
            return False, self.last_error or "OBS offline"
        input_name = (input_name or "").strip()
        if not input_name:
            return False, "blank input name"
        try:
            value_ms = int(round(float(value_ms)))
        except Exception:
            return False, "invalid sync offset"

        fn = getattr(self.client, "set_input_audio_sync_offset", None) if self.client else None
        last_err = None
        if fn is not None:
            try_variants = [
                lambda: fn(input_name, value_ms),
                lambda: fn(input_name, input_audio_sync_offset=value_ms),
                lambda: fn(inputName=input_name, inputAudioSyncOffset=value_ms),
                lambda: fn(input_name=input_name, input_audio_sync_offset=value_ms),
            ]
            for caller in try_variants:
                try:
                    caller()
                    return True, "audio sync offset set"
                except TypeError as e:
                    last_err = e
                    continue
                except Exception as e:
                    last_err = e
                    break

        for kwargs in (
            {"inputName": input_name, "inputAudioSyncOffset": value_ms},
            {"input_name": input_name, "input_audio_sync_offset": value_ms},
            {"name": input_name, "inputAudioSyncOffset": value_ms},
        ):
            resp, err = self._safe_call("set_input_audio_sync_offset", **kwargs)
            if not err:
                return True, "audio sync offset set"
            last_err = err
            if "unexpected keyword argument" in str(err):
                continue

        send_fn = getattr(self.client, "send", None) if self.client else None
        if send_fn is not None:
            payload = {"inputName": input_name, "inputAudioSyncOffset": value_ms}
            for raw_flag in (True, False):
                try:
                    send_fn("SetInputAudioSyncOffset", payload, raw=raw_flag)
                    return True, "audio sync offset set"
                except TypeError:
                    try:
                        send_fn("SetInputAudioSyncOffset", payload)
                        return True, "audio sync offset set"
                    except Exception as e:
                        last_err = e
                except Exception as e:
                    last_err = e

        return False, str(last_err or "SetInputAudioSyncOffset failed")

    # ----------------------------
    # Intro / Media helpers (OBS WebSocket 5.x)
    # ----------------------------
    def trigger_media_restart(self, input_name: str) -> Tuple[bool, str]:
        """Restart playback for a Media Source input.

        obsws-python has changed parameter styles across versions, so we try the
        convenience method positionally first, then fall back to several keyword
        forms used by older/newer wrappers.
        """
        if not self._ok():
            return False, self.last_error or "OBS offline"
        action_names = (
            "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART",
            "OBS_MEDIA_INPUT_ACTION_RESTART",
            "RESTART",
        )
        try:
            fn = getattr(self.client, "trigger_media_input_action", None)
            last_err = None
            if fn is not None:
                for action in action_names:
                    try:
                        fn(input_name, action)
                        return True, "Intro restarted"
                    except TypeError as e:
                        last_err = e
                    except Exception as e:
                        last_err = e
                        break
            # Fall back to keyword variants.
            for action in action_names:
                for kwargs in (
                    {"inputName": input_name, "mediaAction": action},
                    {"input_name": input_name, "media_action": action},
                    {"input": input_name, "mediaAction": action},
                    {"name": input_name, "media_action": action},
                ):
                    r, err = self._safe_call("trigger_media_input_action", **kwargs)
                    if not err:
                        return True, "Intro restarted"
                    last_err = err
                    if "unexpected keyword argument" in str(err):
                        continue
                    break
            return False, str(last_err or "trigger_media_input_action failed")
        except Exception as e:
            return False, str(e)

    def get_media_state(self, input_name: str) -> Tuple[Optional[str], str]:
        """Return OBS media state string, e.g. OBS_MEDIA_STATE_PLAYING / ENDED."""
        if not self._ok():
            return None, self.last_error or "OBS offline"
        try:
            fn = getattr(self.client, "get_media_input_status", None)
            last_err = None
            if fn is not None:
                try:
                    r = fn(input_name)
                    state = self._get(r, "mediaState") or self._get(r, "media_state")
                    if isinstance(state, str):
                        return state, ""
                    return None, ""
                except TypeError as e:
                    last_err = e
                except Exception as e:
                    last_err = e
            for kwargs in (
                {"inputName": input_name},
                {"input_name": input_name},
                {"input": input_name},
                {"name": input_name},
            ):
                r, err = self._safe_call("get_media_input_status", **kwargs)
                if not err:
                    state = self._get(r, "mediaState") or self._get(r, "media_state")
                    if isinstance(state, str):
                        return state, ""
                    return None, ""
                last_err = err
                if "unexpected keyword argument" in str(err):
                    continue
                break
            return None, str(last_err or "get_media_input_status failed")
        except Exception as e:
            return None, str(e)

    def trigger_media_stop(self, input_name: str) -> Tuple[bool, str]:
        """Stop a Media Source input so the next restart begins from a clean state."""
        if not self._ok():
            return False, self.last_error or "OBS offline"
        action_names = (
            "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP",
            "OBS_MEDIA_INPUT_ACTION_STOP",
            "STOP",
        )
        try:
            fn = getattr(self.client, "trigger_media_input_action", None)
            last_err = None
            if fn is not None:
                for action in action_names:
                    try:
                        fn(input_name, action)
                        return True, "Intro stopped"
                    except TypeError as e:
                        last_err = e
                    except Exception as e:
                        last_err = e
                        break
            for action in action_names:
                for kwargs in (
                    {"inputName": input_name, "mediaAction": action},
                    {"input_name": input_name, "media_action": action},
                    {"input": input_name, "mediaAction": action},
                    {"name": input_name, "media_action": action},
                ):
                    r, err = self._safe_call("trigger_media_input_action", **kwargs)
                    if not err:
                        return True, "Intro stopped"
                    last_err = err
                    if "unexpected keyword argument" in str(err):
                        continue
                    break
            return False, str(last_err or "trigger_media_input_action failed")
        except Exception as e:
            return False, str(e)

    def _get_current_program_scene_name(self) -> Tuple[Optional[str], str]:
        r, err = self._safe_call("get_current_program_scene")
        if err:
            return None, str(err)
        name = self._get(r, "currentProgramSceneName") or self._get(r, "current_program_scene_name") or self._get(r, "sceneName") or self._get(r, "scene_name")
        return (str(name) if name else None), ""

    def _get_scene_names(self) -> Tuple[List[str], str]:
        r, err = self._safe_call("get_scene_list")
        if err:
            return [], str(err)
        scenes = self._get(r, "scenes") or []
        names: List[str] = []
        try:
            for s in scenes:
                n = self._get(s, "sceneName") or self._get(s, "scene_name")
                if n:
                    names.append(str(n))
        except Exception:
            pass
        return names, ""

    def _get_scene_item_id_for_source(self, scene_name: str, source_name: str) -> Tuple[Optional[int], str]:
        r, err = self._safe_call("get_scene_item_id", sceneName=scene_name, sourceName=source_name)
        if err and "unexpected keyword argument" in str(err):
            r, err = self._safe_call("get_scene_item_id", scene_name=scene_name, source_name=source_name)
        if err:
            return None, str(err)
        sid = self._get(r, "sceneItemId") or self._get(r, "scene_item_id")
        try:
            return int(sid), ""
        except Exception:
            return None, ""

    def _set_scene_item_enabled(self, scene_name: str, scene_item_id: int, enabled: bool) -> Tuple[bool, str]:
        r, err = self._safe_call("set_scene_item_enabled", sceneName=scene_name, sceneItemId=int(scene_item_id), sceneItemEnabled=bool(enabled))
        if err and "unexpected keyword argument" in str(err):
            r, err = self._safe_call("set_scene_item_enabled", scene_name=scene_name, scene_item_id=int(scene_item_id), enabled=bool(enabled))
        if err:
            return False, str(err)
        return True, ""

    def disable_source_in_scene_auto(self, source_name: str, preferred_scene: str = "") -> Tuple[bool, str]:
        """Disable a source's scene item. If preferred_scene is blank, uses current Program scene, then searches all scenes."""
        if not self._ok():
            return False, self.last_error or "OBS offline"

        # 1) Preferred scene
        scene_candidates: List[str] = []
        if preferred_scene:
            scene_candidates.append(preferred_scene)

        # 2) Current Program scene
        prog, err = self._get_current_program_scene_name()
        if prog and prog not in scene_candidates:
            scene_candidates.append(prog)

        # 3) All scenes
        all_scenes, serr = self._get_scene_names()
        for s in all_scenes:
            if s not in scene_candidates:
                scene_candidates.append(s)

        last_err = ""
        for scene in scene_candidates:
            sid, e1 = self._get_scene_item_id_for_source(scene, source_name)
            if e1:
                last_err = e1
                continue
            if sid is None:
                continue
            ok, e2 = self._set_scene_item_enabled(scene, sid, False)
            if ok:
                return True, f"Disabled '{source_name}' in scene '{scene}'"
            last_err = e2 or last_err

        return False, last_err or f"Could not locate '{source_name}' as a scene item (check OBS source name)."


class MidiListener:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.inport = None
        self.connected_name: str = ""
        self.last_error: str = "not attempted"

    def connect(self) -> bool:
        if mido is None:
            self.last_error = "mido not installed"
            return False
        try:
            names = mido.get_input_names()
            wanted = _safe_lower(self.cfg.MIDI_INPUT_PORT_SUBSTRING)
            match = next((n for n in names if wanted in _safe_lower(n)), None)
            if match is None:
                available = ', '.join(names) if names else 'none found'
                self.last_error = f"no port matching '{self.cfg.MIDI_INPUT_PORT_SUBSTRING}' (available: {available})"
                self.inport = None
                self.connected_name = ""
                return False
            self.inport = mido.open_input(match)
            self.connected_name = match
            self.last_error = ""
            return True
        except Exception as e:
            self.inport = None
            self.connected_name = ""
            self.last_error = str(e)
            return False

    def is_connected(self) -> bool:
        return self.inport is not None

    def pending(self):
        if self.inport is None:
            return []
        try:
            return list(self.inport.iter_pending())
        except Exception:
            self.last_error = "read error"
            self.inport = None
            self.connected_name = ""
            return []

    def is_note_on_channel(self, msg, note: int, channel_1_based: int) -> bool:
        """Return True only for a real NOTE_ON (velocity > 0) on the requested MIDI channel."""
        if getattr(msg, "channel", -1) + 1 != int(channel_1_based):
            return False
        if getattr(msg, "note", None) != note:
            return False
        if getattr(msg, "type", "") != "note_on":
            return False
        vel = getattr(msg, "velocity", 0) or 0
        return vel > 0

    def is_note_on(self, msg, note: int) -> bool:
        return self.is_note_on_channel(msg, note, self.cfg.MIDI_CHANNEL_1_BASED)

    def is_note_in_range_channel(self, msg, lo: int, hi: int, channel_1_based: int) -> Optional[int]:
        """Return the note number for a real NOTE_ON (velocity > 0) within [lo, hi] on the requested channel."""
        if getattr(msg, "channel", -1) + 1 != int(channel_1_based):
            return None
        if getattr(msg, "type", "") != "note_on":
            return None
        vel = getattr(msg, "velocity", 0) or 0
        if vel <= 0:
            return None
        n = getattr(msg, "note", None)
        if n is None:
            return None
        if lo <= n <= hi:
            return n
        return None

    def is_note_in_range(self, msg, lo: int, hi: int) -> Optional[int]:
        return self.is_note_in_range_channel(msg, lo, hi, self.cfg.MIDI_CHANNEL_1_BASED)



class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.start_time = time.time()
        self.root = tk.Tk()
        self.root.title(APP_DISPLAY)
        self.root.geometry("420x720")
        self.root.minsize(400, 600)

        self.running = True

        # UI thread safety: worker thread never touches Tk widgets directly
        self._ui_actions = queue.Queue()
        self._ui_lock = threading.Lock()
        self._ui_state = {}
        self._ui_dirty = False
        self._was_streaming = False

        # Shared state for Web HUD
        self._log_buf = deque(maxlen=400)  # stores full formatted lines
        self._camera_trace_buf = deque(maxlen=max(1, int(getattr(cfg, "HUD_CAMERA_TRACE_MAX_LINES", 8) or 8)))
        self._midi_audit_seq: int = 0
        self._last_scene_action: str = ""
        self._web_dirty = False
        self._state_version = 0
        self._ws_clients = set()
        self._web_runner = None
        self._web_site = None
        self._async_loop = None

        # Director preview cache. Previews are now produced centrally by the app and
        # served to all director clients from shared cached JPEGs. This avoids having
        # each browser open its own live preview stream against the Axis cameras.
        self._director_preview_cache: Dict[str, Dict[str, Any]] = {
            "west": {"seq": 0, "updated_ts": 0.0, "data": b"", "content_type": "image/jpeg", "error": "", "source": ""},
            "east": {"seq": 0, "updated_ts": 0.0, "data": b"", "content_type": "image/jpeg", "error": "", "source": ""},
        }
        self._director_preview_next_at: float = 0.0
        self._director_preview_logged_backend: str = ""
        self._director_preview_last_error: Dict[str, str] = {"west": "", "east": ""}
        self._director_preview_missing_sources: set[str] = set()

        # Commands from UI/web are funneled to the worker loop thread
        self._cmd_queue = None  # created inside async loop

        self.running = True
        self.obs = ObsController(cfg)
        self.midi = MidiListener(cfg)

        axis_proto = "https" if getattr(cfg, "AXIS_USE_HTTPS", False) else "http"
        self.west_cam = AxisPresetCamera(
            f"{axis_proto}://{cfg.WEST_AXIS_IP}",
            cfg.AXIS_USERNAME,
            cfg.AXIS_PASSWORD,
            camera_id=cfg.WEST_AXIS_CAMERA_ID,
            timeout_s=cfg.AXIS_COMMAND_TIMEOUT_SECONDS,
        )
        self.east_cam = AxisPresetCamera(
            f"{axis_proto}://{cfg.EAST_AXIS_IP}",
            cfg.AXIS_USERNAME,
            cfg.AXIS_PASSWORD,
            camera_id=cfg.EAST_AXIS_CAMERA_ID,
            timeout_s=cfg.AXIS_COMMAND_TIMEOUT_SECONDS,
        )


        # Scene-cut scheduling after a camera has been moved off-air.
        self._pending_scene_switch: Optional[str] = None
        self._pending_scene_switch_due: float = 0.0
        self._pending_scene_switch_camera: str = ""
        self._pending_scene_switch_view: Optional[int] = None
        self._pending_scene_switch_source: str = ""
        self._pending_scene_switch_audit_id: str = ""

        # If a channel-2 "next" note arrives while a delayed current-view cut is still pending,
        # the on-air camera must not be moved yet. We defer that prepare until after the cut fires.
        self._deferred_next_view_num: Optional[int] = None
        self._deferred_next_source: str = ""
        self._deferred_next_audit_id: str = ""

        # Camera routing state. The app keeps track of where it *believes* each camera is parked.
        self.camera_positions: Dict[str, Optional[int]] = dict(getattr(cfg, "INITIAL_CAMERA_VIEWS", {}) or {})
        self.camera_positions.setdefault("west", 2)
        self.camera_positions.setdefault("east", 2)
        self.camera_positions_confirmed: Dict[str, bool] = {
            "west": bool(getattr(cfg, "HOME_TEST_MODE", False)),
            "east": bool(getattr(cfg, "HOME_TEST_MODE", False)),
        }
        self.camera_ready_at: Dict[str, float] = {"west": 0.0, "east": 0.0}
        self.camera_available: Dict[str, bool] = {
            "west": bool(getattr(cfg, "WEST_CAMERA_ENABLED", True)),
            "east": bool(getattr(cfg, "EAST_CAMERA_ENABLED", True)),
        }
        # Web HUD audio master state. The fader writes one dB value to the configured
        # audio target(s): either shared ASIO_audio or the Axis Media Source audio inputs.
        # Meter values are read from OBS input-meter events when EventClient support is available.
        self.audio_master_db: float = float(getattr(cfg, "AUDIO_MASTER_DEFAULT_DB", 0.0) or 0.0)
        self.audio_master_meter_db: float = -60.0
        self._audio_levels_db: Dict[str, float] = {}
        self._audio_event_client = None
        self._audio_event_started = False
        self._audio_warned_missing_event_client = False
        self._audio_mode_logged = False
        self._audio_mode_status_next_at: float = 0.0
        self._obs_connected_at: float = 0.0
        # Audio meter diagnostics / rate-limit flags. These do not affect the actual
        # OBS audio path; they only make the Web HUD meter parser observable.
        self._audio_meter_seen_event: bool = False
        self._audio_meter_logged_success: bool = False
        self._audio_meter_last_warn_at: float = 0.0
        self._audio_meter_last_error_at: float = 0.0
        self._audio_meter_last_update_ts: float = 0.0
        self.sync_offsets_ms: Dict[str, int] = {"shared": 0}
        self._sync_offset_errors: Dict[str, str] = {"shared": ""}
        self._sync_resolved_inputs: Dict[str, str] = {"shared": ""}
        self._sync_offsets_next_poll: float = 0.0
        self._sync_unlock_until: float = 0.0
        self._pending_stream_start: bool = False
        self._pending_start_reason: str = ""
        self._pending_start_not_before: float = 0.0
        self._intro_thread = None
        self._intro_cancel = threading.Event()

        self._stop_pending: bool = False
        self._stop_at: float = 0.0
        self._last_stop_was_midi: bool = False
        self._service_end_running: bool = False

        self._timer_done_today_date: Optional[dt.date] = None
        self._timer_done_status: Optional[str] = None
        self._timer_done_time_hhmm: Optional[str] = None
        self._last_start_request_ts: float = 0.0
        self._start_grace_until: float = 0.0  # suppress auto-recover retries right after a start request
        self._startup_settle_until: float = 0.0  # keep false stream-off hiccups from being treated as real stops right after start
        self._unexpected_stop_candidate_since: float = 0.0  # first observed OFF time waiting for confirmation
        self._load_timer_state()

        self.minimized: bool = False
        self.minimized_this_stream: bool = False
        self.stream_stable_since: Optional[float] = None
        self.stream_ended_at: Optional[float] = None  # For "STREAM ENDED" display

        # Streaming intent tracking (used for auto-recovery)
        self._desired_streaming: bool = False
        self._ever_requested_stream: bool = False  # True after first Start request
        self._stop_intent: bool = False
        self._stop_intent_set_at: float = 0.0

        # Auto-recovery state
        self._recovering: bool = False
        self._recover_attempts: int = 0
        self._recover_next_at: float = 0.0
        self._recover_reason: str = ""
        self._recover_hold_until: float = 0.0  # pause after max attempts
        self._recovered_until: float = 0.0  # brief "RECOVERED" indicator window

        # Error/health reporting (sticky on Web HUD)
        self._last_obs_err: str = ""
        self._last_critical_msg: str = ""
        self._last_critical_ts: str = ""

        # OBS profile check: run once after (re)connect to warn early
        self._obs_profile_checked_on_connect: bool = False
        self._obs_profile_check_last_attempt: float = 0.0

        # File logging
        self._run_log_fp = None
        self._session_log_fp = None
        self._run_log_path = ""
        self._session_log_path = ""
        self._init_file_logging()
        
        # Cleanup old logs
        self._cleanup_old_logs()
        
        if self._run_log_path:
            self._post(f"Run log file: {self._run_log_path}")

        self._build_ui()
        self._ui_pump()  # start UI pump on main thread
        self._post("Started — initializing connections...")

        # Optional: minimize shortly after launch (even before streaming starts)
        if self.cfg.AUTO_MINIMIZE_ENABLED and getattr(self.cfg, "MINIMIZE_ON_STARTUP", False):
            try:
                delay_s = float(self.cfg.AUTO_MINIMIZE_AFTER_SECONDS)
            except Exception:
                delay_s = 0.0
            delay_ms = int(max(0.0, delay_s) * 1000)
            self.root.after(delay_ms, self._startup_minimize)

        self.thread = threading.Thread(target=self._runner, daemon=True)
        self.thread.start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use('clam')

        # ttk presets still used below; banner + top buttons are custom Canvas widgets.
        style.configure("Banner.TLabel", font=("Segoe UI", 18, "bold"), padding=14, anchor="center")
        style.configure("Live.Banner.TLabel", background="#2E7D32", foreground="white")  # Green = streaming
        style.configure("Stopping.Banner.TLabel", background="#FF9800", foreground="black")
        style.configure("Countdown.Banner.TLabel", background="#FFC107", foreground="black")
        style.configure("Ready.Banner.TLabel", background="#4CAF50", foreground="white")
        style.configure("Ended.Banner.TLabel", background="#2196F3", foreground="white")  # Blue for ended
        style.configure("Error.Banner.TLabel", background="#F44336", foreground="white")

        # Outer HUD border (3pt solid black)
        outer = tk.Frame(self.root, highlightthickness=3, highlightbackground="black", bd=0)
        outer.pack(fill="both", expand=True)

        main = ttk.Frame(outer, padding=12)
        main.pack(fill="both", expand=True)

        # App version label (shown above the status banner)
        self.app_version_var = tk.StringVar(value=APP_DISPLAY)
        ttk.Label(main, textvariable=self.app_version_var, font=("Segoe UI", 10), anchor="center").pack(fill="x", pady=(0, 4))

        # Banner widget (4pt black outline; outlined white text)
        self._banner_style_map = {
            "Live.Banner.TLabel": ("#2E7D32", "white"),
            "Stopping.Banner.TLabel": ("#FF9800", "black"),
            "Countdown.Banner.TLabel": ("#FFC107", "black"),
            "Ready.Banner.TLabel": ("#4CAF50", "white"),
            "Ended.Banner.TLabel": ("#2196F3", "white"),
            "Error.Banner.TLabel": ("#F44336", "white"),
        }

        self.banner_var = tk.StringVar(value="INITIALIZING — Launch OBS/Proclaim as needed")
        self.banner_widget = OutlinedBanner(main, height=72, outline_px=4, text_outline_px=1)
        bg, fg = self._banner_style_map.get("Ready.Banner.TLabel", ("#4CAF50", "white"))
        self.banner_widget.set(self.banner_var.get(), bg, fg)
        self.banner_widget.pack(fill="x", pady=(0, 12))

        mode_var = tk.StringVar(value="HOME TEST MODE" if self.cfg.HOME_TEST_MODE else "CHURCH MODE")
        ttk.Label(main, textvariable=mode_var, font=("Segoe UI", 12, "bold")).pack(anchor="w")

        self.timer_var = tk.StringVar()
        ttk.Label(main, textvariable=self.timer_var, font=("Segoe UI", 10)).pack(anchor="w", pady=(6, 0))

        ttk.Separator(main, orient="horizontal").pack(fill="x", pady=12)

        self.obs_var = tk.StringVar(value="OBS: connecting...")
        self.midi_var = tk.StringVar(value="MIDI: scanning for port...")
        self.cam_var = tk.StringVar(value="CAM: SLEEP")
        ttk.Label(main, textvariable=self.obs_var, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(main, textvariable=self.midi_var, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(main, textvariable=self.cam_var, font=("Segoe UI", 10, "bold")).pack(anchor="w")

        log_frame = ttk.LabelFrame(main, text="Log")
        log_frame.pack(fill="both", expand=True, pady=(12, 0))
        self.log_text = scrolledtext.ScrolledText(log_frame, height=6, font=("Consolas", 9), state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)

        # Controls row (outlined button text + 3pt solid black border)
        controls = ttk.Frame(main)
        controls.pack(fill="x", pady=(12, 0))

        def _start(): self._ui_fire("start")
        def _stop():  self._ui_fire("stop")
        def _rec():   self._ui_fire("rec")

        self.btn_start = OutlinedCanvasButton(controls, text="Start Stream", command=_start,
                                              bg="#4CAF50", fg="white",
                                              width=120, height=40, border_px=3, text_outline_px=1)
        self.btn_stop = OutlinedCanvasButton(controls, text="Stop Stream", command=_stop,
                                             bg="#F44336", fg="white",
                                             width=120, height=40, border_px=3, text_outline_px=1)
        self.rec_btn = OutlinedCanvasButton(controls, text="REC Toggle", command=_rec,
                                            bg="#FF9800", fg="white",
                                            width=120, height=40, border_px=3, text_outline_px=1)

        self.btn_start.grid(row=0, column=0, padx=8, pady=4, sticky="ew")
        self.btn_stop.grid(row=0, column=1, padx=8, pady=4, sticky="ew")
        self.rec_btn.grid(row=0, column=2, padx=8, pady=4, sticky="ew")
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(2, weight=1)

        # Camera presets area with 3pt solid black border
        presets_outer = tk.Frame(main, highlightthickness=3, highlightbackground="black", bd=0)
        presets_outer.pack(fill="x", pady=(15, 0))

        presets_inner = ttk.Frame(presets_outer, padding=(10, 8))
        presets_inner.pack(fill="x")

        presets_label = ttk.Label(presets_inner, text="Camera Presets", font=("Segoe UI", 12, "bold"), anchor="center")
        presets_label.grid(row=0, column=0, columnspan=2, pady=(0, 8))

        for i in range(1, 11):
            label = self.cfg.PRESET_LABELS.get(i, f"Preset {i}")
            ttk.Button(presets_inner, text=f"{i}: {label}",
                       width=24,
                       command=lambda p=i: self._ui_preset(p)).grid(
                row=((i-1)//2) + 1, column=(i-1)%2, padx=10, pady=4, sticky="ew")
        presets_inner.columnconfigure(0, weight=1)
        presets_inner.columnconfigure(1, weight=1)

    def _ui_action(self, fn):
        """Enqueue a callable to run on the Tkinter/UI thread."""
        try:
            self._ui_actions.put(fn)
        except Exception:
            pass

    def _set_ui_state(self, **kwargs):
        """Set latest UI state snapshot from the worker thread."""
        with self._ui_lock:
            self._ui_state.update(kwargs)
            self._ui_dirty = True
            # Also mark Web HUD dirty
            self._state_version += 1
            self._web_dirty = True

    def _ui_pump(self):
        """Runs on UI thread; applies latest state and executes queued UI actions."""
        # Apply coalesced state updates
        state = None
        with self._ui_lock:
            if self._ui_dirty:
                state = dict(self._ui_state)
                self._ui_dirty = False

        if state is not None:
            try:
                if "obs_line" in state:
                    self.obs_var.set(state["obs_line"])
                if "midi_line" in state:
                    self.midi_var.set(state["midi_line"])
                if "cam_line" in state:
                    self.cam_var.set(state["cam_line"])
                if "timer_text" in state:
                    self.timer_var.set(state["timer_text"])
                if "rec_on" in state:
                    # Canvas button uses colors instead of ttk styles
                    try:
                        if state["rec_on"]:
                            self.rec_btn.set_colors(bg="#D32F2F", fg="white")
                            self.rec_btn.set_text("REC ON")
                        else:
                            self.rec_btn.set_colors(bg="#FF9800", fg="white")
                            self.rec_btn.set_text("REC Toggle")
                    except Exception:
                        pass
                if "banner_text" in state:
                    self.banner_var.set(state["banner_text"])
                    try:
                        self.banner_widget.set_text(state["banner_text"])
                    except Exception:
                        pass
                if "banner_style" in state:
                    try:
                        bg, fg = self._banner_style_map.get(state["banner_style"], self._banner_style_map.get("Ready.Banner.TLabel"))
                        self.banner_widget.set_colors(bg, fg)
                    except Exception:
                        pass
            except Exception:
                # Avoid crashing the UI pump
                pass

        # Execute one-off UI actions
        try:
            while True:
                fn = self._ui_actions.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
        except Exception:
            pass

        if self.running:
            self.root.after(50, self._ui_pump)

    def _startup_minimize(self):
        """Minimize the PC HUD shortly after launch (UI thread)."""
        try:
            if not self.running:
                return
            if self.minimized:
                return
            self.root.iconify()
            self.minimized = True
            self._post("Startup — minimizing HUD")
        except Exception:
            # Never crash the UI thread for minimize logic
            pass

    def _post(self, msg: str):
        ts = dt.datetime.now().strftime("%H:%M:%S")
        full = f"[{ts}] {msg}\n"
        self._write_log_line(full)
        with self._ui_lock:
            self._log_buf.append(full)
            self._state_version += 1
            self._web_dirty = True

        def _append():
            if not self.running:
                return
            try:
                self.log_text.config(state="normal")
                self.log_text.insert("end", full)
                self.log_text.see("end")
                self.log_text.config(state="disabled")
            except Exception:
                pass

        self._ui_action(_append)

    def _camera_trace_enabled(self) -> bool:
        return bool(getattr(self.cfg, "HUD_CAMERA_TRACE_ENABLED", False))

    def _trace_camera(self, msg: str):
        if not self._camera_trace_enabled():
            return
        ts = dt.datetime.now().strftime("%H:%M:%S")
        full = f"[{ts}] {msg}"
        with self._ui_lock:
            max_lines = max(1, int(getattr(self.cfg, "HUD_CAMERA_TRACE_MAX_LINES", 8) or 8))
            cur = getattr(self, "_camera_trace_buf", None)
            if cur is None or cur.maxlen != max_lines:
                prior = list(cur) if cur is not None else []
                self._camera_trace_buf = deque(prior[-max_lines:], maxlen=max_lines)
            self._camera_trace_buf.append(full)
            self._state_version += 1
            self._web_dirty = True

    def _midi_cue_audit_enabled(self) -> bool:
        return bool(getattr(self.cfg, "MIDI_CUE_AUDIT_ENABLED", False))

    def _next_midi_audit_id(self) -> str:
        self._midi_audit_seq += 1
        return f"E{self._midi_audit_seq:04d}"

    def _midi_msg_detail(self, msg: Any) -> str:
        typ = getattr(msg, "type", "?")
        note = getattr(msg, "note", "?")
        vel = getattr(msg, "velocity", 0) or 0
        ch = (getattr(msg, "channel", -1) + 1) if hasattr(msg, "channel") else "?"
        return f"type={typ} note={note} vel={vel} ch={ch}"

    def _trace_or_audit(self, msg: str, audit_id: Optional[str] = None):
        tagged = f"[{audit_id}] {msg}" if audit_id else msg
        self._trace_camera(tagged)
        if audit_id and self._midi_cue_audit_enabled() and bool(getattr(self.cfg, "MIDI_CUE_AUDIT_TO_FILE", True)):
            self._post(f"AUDIT {tagged}")

    def _audit_midi_rx(self, msg: Any, audit_id: str):
        if not self._midi_cue_audit_enabled():
            return
        self._trace_or_audit(f"RX -> {self._midi_msg_detail(msg)}", audit_id)

    def _audit_midi_dec(self, audit_id: Optional[str], detail: str):
        if not audit_id or not self._midi_cue_audit_enabled():
            return
        self._trace_or_audit(f"DEC -> {detail}", audit_id)

    def _set_last_scene_action(self, msg: str):
        ts = dt.datetime.now().strftime("%H:%M:%S")
        full = f"[{ts}] {msg}"
        with self._ui_lock:
            self._last_scene_action = full
            self._state_version += 1
            self._web_dirty = True

    def _axis_command_trace(self, camera_key: str, preset_name: str) -> str:
        ctrl = self._camera_controller(camera_key)
        try:
            url = ctrl._build_url(preset_name)  # type: ignore[attr-defined]
        except Exception:
            base = getattr(ctrl, "base_url", "")
            cam_id = getattr(ctrl, "camera_id", 1)
            params = {"gotoserverpresetname": str(preset_name), "camera": int(cam_id or 1)}
            url = (str(base).rstrip("/") + "/axis-cgi/com/ptz.cgi?" + urlparse.urlencode(params)).strip()
        timeout_s = float(getattr(ctrl, "timeout_s", getattr(self.cfg, "AXIS_COMMAND_TIMEOUT_SECONDS", 3.0)) or 3.0)
        user = getattr(ctrl, "username", "") or getattr(self.cfg, "AXIS_USERNAME", "")
        user_txt = f" user={user}" if user else ""
        return f"AXIS GET {url}{user_txt} auth=masked timeout={timeout_s:.1f}s"

    def _cancel_intro_sequence(self, source: str = "", log: bool = True):
        thr = getattr(self, "_intro_thread", None)
        alive = bool(getattr(thr, "is_alive", lambda: False)())
        self._intro_cancel.set()
        if alive and log:
            self._post(f"{source}: INTRO cancel requested")

    # -----------------------------
    # File logging (run log + per-stream session log)
    # -----------------------------
    def _log_base_dir(self) -> str:
        base = (getattr(self.cfg, "LOG_DIR", "") or "").strip()
        if base:
            return base
        return os.path.dirname(os.path.abspath(__file__))

    def _init_file_logging(self):
        if not getattr(self.cfg, "LOG_TO_FILE_ENABLED", False):
            return
        try:
            base_dir = self._log_base_dir()
            os.makedirs(base_dir, exist_ok=True)
            ts = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            prefix = getattr(self.cfg, "LOG_RUN_FILE_PREFIX", "stream_agent")
            self._run_log_path = os.path.join(base_dir, f"{prefix}_run_{ts}.log")
            self._run_log_fp = open(self._run_log_path, "a", encoding="utf-8", buffering=1)
            self._run_log_fp.write(f"=== Stream Agent run started {ts} ===\n")
            self._run_log_fp.flush()
        except Exception:
            self._run_log_fp = None
            self._run_log_path = ""
            
    def _cleanup_old_logs(self):
        """Keep only the most recent N log files to prevent clutter."""
        if not getattr(self.cfg, "LOG_TO_FILE_ENABLED", False):
            return
        try:
            base_dir = self._log_base_dir()
            prefix = getattr(self.cfg, "LOG_RUN_FILE_PREFIX", "stream_agent")
            retention = getattr(self.cfg, "LOG_RETENTION_COUNT", 30)
            
            # Pattern match for run logs
            pattern = os.path.join(base_dir, f"{prefix}_run_*.log")
            files = glob.glob(pattern)
            
            if len(files) <= retention:
                return
                
            # Sort by modification time (oldest first)
            files.sort(key=os.path.getmtime)
            
            # Delete excess
            to_delete = files[:-retention]
            count = 0
            for fpath in to_delete:
                try:
                    os.remove(fpath)
                    count += 1
                except Exception:
                    pass
            
            if count > 0:
                print(f"Cleanup: removed {count} old log files.")
                
        except Exception as e:
            print(f"Cleanup error: {e}")

    def _write_log_line(self, line: str):
        if not getattr(self.cfg, "LOG_TO_FILE_ENABLED", False):
            return
        try:
            if self._run_log_fp:
                self._run_log_fp.write(line)
                self._run_log_fp.flush()
        except Exception:
            pass
        try:
            if self._session_log_fp:
                self._session_log_fp.write(line)
                self._session_log_fp.flush()
        except Exception:
            pass

    def _open_session_log(self, reason: str = ""):
        if not getattr(self.cfg, "LOG_TO_FILE_ENABLED", False):
            return
        if not getattr(self.cfg, "LOG_SEPARATE_SESSION_FILES", True):
            return
        # Close any previous session file (safety)
        self._close_session_log("rotate")
        try:
            base_dir = self._log_base_dir()
            os.makedirs(base_dir, exist_ok=True)
            ts = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            prefix = getattr(self.cfg, "LOG_RUN_FILE_PREFIX", "stream_agent")
            self._session_log_path = os.path.join(base_dir, f"{prefix}_session_{ts}.log")
            self._session_log_fp = open(self._session_log_path, "a", encoding="utf-8", buffering=1)
            hdr = f"=== STREAM SESSION START {ts}"
            if reason:
                hdr += f" ({reason})"
            hdr += " ===\n"
            self._session_log_fp.write(hdr)
            self._session_log_fp.flush()
        except Exception:
            self._session_log_fp = None
            self._session_log_path = ""

    def _close_session_log(self, reason: str = ""):
        try:
            if self._session_log_fp:
                ts = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                trailer = f"=== STREAM SESSION END {ts}"
                if reason:
                    trailer += f" ({reason})"
                trailer += " ===\n"
                self._session_log_fp.write(trailer)
                self._session_log_fp.flush()
                self._session_log_fp.close()
        except Exception:
            pass
        finally:
            self._session_log_fp = None
            self._session_log_path = ""

    # -----------------------------
    # Sticky critical-event tracking (for Web HUD)
    # -----------------------------
    def _note_critical(self, msg: str):
        self._last_critical_msg = msg or ""
        self._last_critical_ts = dt.datetime.now().strftime("%H:%M:%S")

    def _clear_unexpected_stop_candidate(self):
        self._unexpected_stop_candidate_since = 0.0

    def _begin_unexpected_stop_candidate(self):
        now = time.time()
        if getattr(self, "_unexpected_stop_candidate_since", 0.0) <= 0.0:
            self._unexpected_stop_candidate_since = now
            settle_until = getattr(self, "_startup_settle_until", 0.0) or 0.0
            if now < settle_until:
                self._post("WARN: transient stream-off detected during startup settle window — waiting to confirm")

    def _confirm_unexpected_stop_if_needed(self, streaming: bool):
        if streaming or self._stop_intent or (not self._desired_streaming) or (not self._ever_requested_stream):
            self._clear_unexpected_stop_candidate()
            return

        candidate_since = getattr(self, "_unexpected_stop_candidate_since", 0.0) or 0.0
        if candidate_since <= 0.0:
            return

        now = time.time()
        settle_until = getattr(self, "_startup_settle_until", 0.0) or 0.0
        debounce_s = max(0.0, float(getattr(self.cfg, "UNEXPECTED_STOP_DEBOUNCE_SECONDS", 2.0) or 0.0))
        confirm_at = max(candidate_since + debounce_s, settle_until)

        if now < confirm_at:
            return

        self.stream_stable_since = None
        self.minimized_this_stream = False
        self._note_critical("Stream stopped unexpectedly")
        self._post("ERROR: Stream stopped unexpectedly")
        self._close_session_log("unexpected_stop")
        self._clear_unexpected_stop_candidate()
        if self.cfg.AUTO_RECOVER_ENABLED:
            self._arm_recovery("Unexpected stream stop")

    # -----------------------------
    # Auto-recovery (self-healing) helpers
    # -----------------------------
    def _reset_recovery_state(self):
        """Reset all auto-recovery state (safe to call any time)."""
        self._recovering = False
        self._recover_attempts = 0
        self._recover_next_at = 0.0
        self._recover_reason = ""
        self._recover_hold_until = 0.0
        self._recovered_until = 0.0
        # Suppress "maintain live" retries briefly after a start request
        self._start_grace_until = 0.0
        self._startup_settle_until = 0.0
        self._unexpected_stop_candidate_since = 0.0

    def _arm_recovery(self, reason: str):
        """Arm the recovery loop (does not necessarily attempt immediately)."""
        if not getattr(self.cfg, "AUTO_RECOVER_ENABLED", False):
            return
        if not self._desired_streaming:
            return
        now = time.time()
        if now < self._recover_hold_until:
            return
        if not self._recovering:
            self._recovering = True
            self._recover_attempts = 0
            self._recover_reason = reason or "auto"
            self._recover_next_at = now  # try ASAP
            self._post(f"Auto-recover armed: {self._recover_reason}")

    def _recovery_tick(self, streaming: bool):
        """Attempt to restore streaming when desired but not currently live."""
        if not getattr(self.cfg, "AUTO_RECOVER_ENABLED", False):
            return
        now = time.time()

        if streaming:
            self._start_grace_until = 0.0  # streaming is live; clear any pending start grace window
            if self._recovering:
                # streaming is back — clear recovery
                self._recovering = False
                self._recover_attempts = 0
                self._recover_next_at = 0.0
                self._recover_reason = ""
            return

        if not self._desired_streaming:
            self._recovering = False
            return

        if now < self._recover_hold_until:
            return

        grace_until = getattr(self, "_start_grace_until", 0.0) or 0.0
        if now < grace_until:
            return

        if not self._recovering:
            # Desired live, not live, but no active recovery cycle yet.
            self._recovering = True
            self._recover_reason = "maintain live"
            self._recover_attempts = 0
            self._recover_next_at = now

        if now < self._recover_next_at:
            return

        max_attempts = int(getattr(self.cfg, "AUTO_RECOVER_MAX_ATTEMPTS", 3))
        if self._recover_attempts >= max_attempts:
            # Pause before trying again.
            cool = int(getattr(self.cfg, "AUTO_RECOVER_COOLDOWN_SECONDS", 300))
            self._recovering = False
            self._recover_hold_until = now + cool
            self._note_critical("Auto-recover paused (max attempts reached)")
            self._post(f"ERROR: Auto-recover paused for {cool}s (max {max_attempts} attempts reached)")
            return

        if not self.obs.connected:
            # OBS reconnect is handled elsewhere; we just wait.
            if self._recover_attempts == 0:
                self._post("Auto-recover: OBS offline — waiting for reconnect")
            self._recover_next_at = now + 3.0
            return

        self._recover_attempts += 1
        ok, msg = self.obs.start_stream()
        if ok:
            self._post(f"Auto-recover: {msg} (attempt {self._recover_attempts}/{max_attempts})")
        else:
            self._post(f"ERROR: Auto-recover start failed ({msg}) (attempt {self._recover_attempts}/{max_attempts})")

        base = float(getattr(self.cfg, "AUTO_RECOVER_BASE_DELAY_SECONDS", 10))
        mult = float(getattr(self.cfg, "AUTO_RECOVER_BACKOFF_MULTIPLIER", 2.0))
        delay = int(base * (mult ** max(0, self._recover_attempts - 1)))
        delay = max(3, delay)
        self._recover_next_at = now + delay


    def _update_banner(self, streaming: bool, recording: bool, error_msg: str = "") -> Tuple[str, str]:
        """Compute banner text/style without touching Tk widgets (thread-safe)."""
        now = time.time()

        if self._stop_pending:
            rem = int(self._stop_at - now)
            return f"STOPPING IN T-{fmt_hms(rem)}", "Stopping.Banner.TLabel"

        if streaming:
            return "🟢 LIVE — NOW STREAMING", "Live.Banner.TLabel"

        # Show "STREAM ENDED" for 60s after stop
        if self.stream_ended_at and (now - self.stream_ended_at) < 60:
            return "STREAM ENDED", "Ended.Banner.TLabel"

        target = self._timer_target_today()
        if target:
            delta = int((target - now_in_cfg_tz(self.cfg)).total_seconds())
            if 0 < delta < 600:
                return f"AUTO-START IN T-{fmt_hms(delta)}", "Countdown.Banner.TLabel"

        if error_msg:
            return f"⚠️ {error_msg}", "Error.Banner.TLabel"

        return "READY", "Ready.Banner.TLabel"

    def _enqueue_cmd(self, cmd: dict):
        """Thread-safe enqueue into the worker loop."""
        loop = self._async_loop
        q = self._cmd_queue
        if loop is None or q is None:
            # Early startup fallback (should be rare)
            self._post(f"HUD: command queued too early: {cmd}")
            return

        def _put():
            try:
                q.put_nowait(cmd)
            except Exception as e:
                self._post(f"CMD queue error: {e}")

        try:
            loop.call_soon_threadsafe(_put)
        except Exception as e:
            self._post(f"CMD enqueue failed: {e}")

    def _ui_fire(self, action: str):
        self._enqueue_cmd({"type": "action", "action": action, "source": "HUD"})

    def _ui_preset(self, preset_num: int):
        self._enqueue_cmd({"type": "preset", "preset": int(preset_num), "source": "HUD"})

    # ------------------------------------------------------------------
    # Stream Agent III sd routing helpers
    # ------------------------------------------------------------------
    def _scene_name_for_camera(self, camera_key: str) -> str:
        mapping = {
            "west": getattr(self.cfg, "OBS_SCENE_WEST", "West View"),
            "east": getattr(self.cfg, "OBS_SCENE_EAST", "East View"),
        }
        return mapping.get(camera_key, getattr(self.cfg, "OBS_SAFE_FALLBACK_SCENE", "West View"))

    def _video_input_name_for_camera(self, camera_key: str) -> str:
        mapping = {
            "west": getattr(self.cfg, "OBS_VIDEO_INPUT_WEST", "West_axis"),
            "east": getattr(self.cfg, "OBS_VIDEO_INPUT_EAST", ""),
        }
        return mapping.get(camera_key, "")

    def _audio_mode(self) -> str:
        """Return normalized audio mode.

        asio_shared: original shared ASIO_audio source.
        axis_embedded: camera Media Source audio carried inside West_axis/East_axis RTSP inputs.
        """
        mode = str(getattr(self.cfg, "AUDIO_MODE", "asio_shared") or "asio_shared").strip().lower()
        aliases = {
            "asio": "asio_shared",
            "shared_asio": "asio_shared",
            "asio_audio": "asio_shared",
            "axis": "axis_embedded",
            "camera": "axis_embedded",
            "camera_embedded": "axis_embedded",
            "embedded": "axis_embedded",
        }
        mode = aliases.get(mode, mode)
        if mode not in ("asio_shared", "axis_embedded"):
            return "asio_shared"
        return mode

    def _using_axis_embedded_audio(self) -> bool:
        return self._audio_mode() == "axis_embedded"

    def _axis_audio_input_names(self) -> List[str]:
        names: List[str] = []
        for cam_key in ("west", "east"):
            n = (self._video_input_name_for_camera(cam_key) or "").strip()
            if n and n not in names:
                names.append(n)
        return names

    def _audio_inputs(self) -> List[str]:
        if self._using_axis_embedded_audio():
            return self._axis_audio_input_names()
        shared = str(getattr(self.cfg, "OBS_AUDIO_INPUT_SHARED", "ASIO_audio") or "ASIO_audio").strip()
        return [shared] if shared else []

    def _audio_mode_label(self) -> str:
        if self._using_axis_embedded_audio():
            names = self._axis_audio_input_names()
            return "Axis camera audio" if not names else "Axis camera audio (" + " / ".join(names) + ")"
        shared = str(getattr(self.cfg, "OBS_AUDIO_INPUT_SHARED", "ASIO_audio") or "ASIO_audio").strip()
        return f"Shared ASIO audio ({shared})" if shared else "Shared ASIO audio"

    def _audio_mode_description(self) -> str:
        if self._using_axis_embedded_audio():
            return "One Web HUD fader controls the Axis Media Source audio from West_axis and East_axis. Sync offset controls are disabled in this mode."
        return "One Web HUD fader controls the shared ASIO input reused in all live scenes."

    def _log_audio_mode_status(self) -> bool:
        """Log and verify the configured OBS audio targets.

        Returns True when the check is complete enough that it does not need an
        immediate retry. Returns False when OBS is merely warming up.
        """
        mode = self._audio_mode()
        targets = self._audio_inputs()
        self._post(f"AUDIO MODE: {mode} — targets: {', '.join(targets) if targets else 'none'}")
        if not self.obs.connected:
            return False
        names, err = self.obs.get_input_names()
        if err:
            err_l = str(err).lower()
            if ("not ready" in err_l) or ("notready" in err_l) or ("code 207" in err_l):
                self._post("AUDIO MODE: OBS is still initializing — input list check will retry")
                return False
            self._post(f"AUDIO MODE: could not read OBS input list ({err})")
            return True
        missing = [n for n in targets if n and n not in names]
        if missing:
            self._post(f"AUDIO MODE WARN: missing OBS audio target(s): {', '.join(missing)}")
        elif targets:
            self._post("AUDIO MODE: all configured audio targets found in OBS")
        return True

    def _camera_key_for_scene(self, scene_name: Optional[str]) -> Optional[str]:
        scene_name = (scene_name or "").strip()
        if not scene_name:
            return None
        if scene_name == getattr(self.cfg, "OBS_SCENE_WEST", "West View"):
            return "west"
        if scene_name == getattr(self.cfg, "OBS_SCENE_EAST", "East View"):
            return "east"
        return None

    def _current_program_camera_key(self) -> str:
        if self._pending_scene_switch and self._pending_scene_switch_camera:
            return self._pending_scene_switch_camera
        scene_name, _ = self.obs._get_current_program_scene_name()
        cam = self._camera_key_for_scene(scene_name)
        if cam:
            return cam
        return "west"

    def _other_axis_camera(self, camera_key: str) -> str:
        return "east" if str(camera_key) == "west" else "west"

    def _camera_remaining_ready_seconds(self, camera_key: str) -> float:
        due = float((getattr(self, "camera_ready_at", {}) or {}).get(camera_key, 0.0) or 0.0)
        return max(0.0, due - time.time())

    def _actual_program_camera_key(self) -> Optional[str]:
        try:
            scene_name, _ = self.obs._get_current_program_scene_name()
            return self._camera_key_for_scene(scene_name)
        except Exception:
            return None

    def _axis_base_url_for_camera(self, camera_key: str) -> str:
        scheme = "https" if bool(getattr(self.cfg, "AXIS_USE_HTTPS", False)) else "http"
        if str(camera_key) == "west":
            ip = str(getattr(self.cfg, "WEST_AXIS_IP", "") or "").strip()
        else:
            ip = str(getattr(self.cfg, "EAST_AXIS_IP", "") or getattr(self.cfg, "CENTER_AXIS_IP", "") or "").strip()
        if not ip:
            raise ValueError(f"No Axis IP configured for camera '{camera_key}'")
        return f"{scheme}://{ip}"

    def _axis_camera_id_for_key(self, camera_key: str) -> int:
        if str(camera_key) == "west":
            return int(getattr(self.cfg, "WEST_AXIS_CAMERA_ID", 1) or 1)
        return int(getattr(self.cfg, "EAST_AXIS_CAMERA_ID", getattr(self.cfg, "CENTER_AXIS_CAMERA_ID", 1)) or 1)

    def _axis_snapshot_url(self, camera_key: str, width: int = 640, height: int = 360) -> str:
        params = {
            "camera": self._axis_camera_id_for_key(camera_key),
            "resolution": f"{int(width)}x{int(height)}",
            "compression": 35,
        }
        return self._axis_base_url_for_camera(camera_key) + "/axis-cgi/jpg/image.cgi?" + urlparse.urlencode(params)

    def _axis_mjpeg_url(self, camera_key: str, width: int = 640, height: int = 360, fps: int = 0) -> str:
        params = {
            "camera": self._axis_camera_id_for_key(camera_key),
            "resolution": f"{int(width)}x{int(height)}",
            "compression": 35,
            "fps": max(0, int(fps)),
        }
        return self._axis_base_url_for_camera(camera_key) + "/axis-cgi/mjpg/video.cgi?" + urlparse.urlencode(params)

    def _fetch_axis_snapshot_bytes(self, camera_key: str, width: int = 640, height: int = 360) -> Tuple[bytes, str]:
        url = self._axis_snapshot_url(camera_key, width=width, height=height)
        username = str(getattr(self.cfg, "AXIS_USERNAME", "") or "")
        password = str(getattr(self.cfg, "AXIS_PASSWORD", "") or "")
        timeout_s = max(1.0, float(getattr(self.cfg, "AXIS_COMMAND_TIMEOUT_SECONDS", 3.0) or 3.0) + 2.0)
        last_err = None

        if username or password:
            try:
                pw_mgr = urlrequest.HTTPPasswordMgrWithDefaultRealm()
                base_url = self._axis_base_url_for_camera(camera_key).rstrip("/") + "/"
                pw_mgr.add_password(None, base_url, username, password)
                handlers = [
                    urlrequest.HTTPDigestAuthHandler(pw_mgr),
                    urlrequest.HTTPBasicAuthHandler(pw_mgr),
                ]
                opener = urlrequest.build_opener(*handlers)
                with opener.open(url, timeout=timeout_s) as resp:
                    code = getattr(resp, "status", None) or getattr(resp, "code", None) or 0
                    if int(code) < 200 or int(code) >= 300:
                        raise RuntimeError(f"Axis snapshot HTTP {code}")
                    data = resp.read()
                    ctype = str(resp.headers.get("Content-Type", "image/jpeg") or "image/jpeg")
                    return data, ctype
            except Exception as e:
                last_err = e

        try:
            req = urlrequest.Request(url)
            if username or password:
                import base64
                token = base64.b64encode((f"{username}:{password}").encode("utf-8")).decode("ascii")
                req.add_header("Authorization", "Basic " + token)
            with urlrequest.urlopen(req, timeout=timeout_s) as resp:
                code = getattr(resp, "status", None) or getattr(resp, "code", None) or 0
                if int(code) < 200 or int(code) >= 300:
                    raise RuntimeError(f"Axis snapshot HTTP {code}")
                data = resp.read()
                ctype = str(resp.headers.get("Content-Type", "image/jpeg") or "image/jpeg")
                return data, ctype
        except Exception as e:
            if last_err is not None:
                raise RuntimeError(f"{last_err}; fallback auth error: {e}")
            raise

    def _open_axis_mjpeg_stream(self, camera_key: str, width: int = 640, height: int = 360, fps: int = 0):
        url = self._axis_mjpeg_url(camera_key, width=width, height=height, fps=fps)
        username = str(getattr(self.cfg, "AXIS_USERNAME", "") or "")
        password = str(getattr(self.cfg, "AXIS_PASSWORD", "") or "")
        timeout_s = max(5.0, float(getattr(self.cfg, "AXIS_COMMAND_TIMEOUT_SECONDS", 3.0) or 3.0) + 5.0)
        last_err = None

        if username or password:
            try:
                pw_mgr = urlrequest.HTTPPasswordMgrWithDefaultRealm()
                base_url = self._axis_base_url_for_camera(camera_key).rstrip("/") + "/"
                pw_mgr.add_password(None, base_url, username, password)
                handlers = [
                    urlrequest.HTTPDigestAuthHandler(pw_mgr),
                    urlrequest.HTTPBasicAuthHandler(pw_mgr),
                ]
                opener = urlrequest.build_opener(*handlers)
                resp = opener.open(url, timeout=timeout_s)
                code = getattr(resp, "status", None) or getattr(resp, "code", None) or 0
                if int(code) < 200 or int(code) >= 300:
                    raise RuntimeError(f"Axis MJPEG HTTP {code}")
                ctype = str(resp.headers.get("Content-Type", "multipart/x-mixed-replace") or "multipart/x-mixed-replace")
                return resp, ctype
            except Exception as e:
                last_err = e

        try:
            req = urlrequest.Request(url)
            if username or password:
                import base64
                token = base64.b64encode((f"{username}:{password}").encode("utf-8")).decode("ascii")
                req.add_header("Authorization", "Basic " + token)
            resp = urlrequest.urlopen(req, timeout=timeout_s)
            code = getattr(resp, "status", None) or getattr(resp, "code", None) or 0
            if int(code) < 200 or int(code) >= 300:
                raise RuntimeError(f"Axis MJPEG HTTP {code}")
            ctype = str(resp.headers.get("Content-Type", "multipart/x-mixed-replace") or "multipart/x-mixed-replace")
            return resp, ctype
        except Exception as e:
            if last_err is not None:
                raise RuntimeError(f"{last_err}; fallback auth error: {e}")
            raise

    def _director_preview_enabled(self) -> bool:
        return bool(getattr(self.cfg, "DIRECTOR_PREVIEW_ENABLED", True))

    def _director_preview_backend(self) -> str:
        backend = str(getattr(self.cfg, "DIRECTOR_PREVIEW_BACKEND", "obs_screenshot") or "obs_screenshot").strip().lower()
        if backend not in ("obs_screenshot", "obs_mjpeg_stream", "axis_snapshot", "axis_mjpeg"):
            backend = "obs_screenshot"
        return backend

    def _director_preview_cfg(self) -> Tuple[int, int, int, int, str]:
        width = max(8, min(4096, int(getattr(self.cfg, "DIRECTOR_PREVIEW_WIDTH", 640) or 640)))
        height = max(8, min(4096, int(getattr(self.cfg, "DIRECTOR_PREVIEW_HEIGHT", 360) or 360)))
        refresh_ms = max(100, int(getattr(self.cfg, "DIRECTOR_PREVIEW_REFRESH_MS", 500) or 500))
        quality = max(-1, min(100, int(getattr(self.cfg, "DIRECTOR_PREVIEW_JPEG_QUALITY", 70) or 70)))
        return width, height, refresh_ms, quality, self._director_preview_backend()

    def _director_preview_public_state(self, camera_key: str) -> dict:
        cache = dict((getattr(self, "_director_preview_cache", {}) or {}).get(camera_key, {}) or {})
        stale_ms = max(250, int(getattr(self.cfg, "DIRECTOR_PREVIEW_STALE_AFTER_MS", 3000) or 3000))
        updated_ts = float(cache.get("updated_ts", 0.0) or 0.0)
        age_ms = int(max(0.0, (time.time() - updated_ts) * 1000.0)) if updated_ts > 0 else 10**9
        return {
            "seq": int(cache.get("seq", 0) or 0),
            "updated_ts": updated_ts,
            "age_ms": age_ms,
            "ok": bool(cache.get("data")) and age_ms <= stale_ms,
            "stale": age_ms > stale_ms,
            "error": str(cache.get("error", "") or ""),
            "backend": self._director_preview_backend(),
            "enabled": self._director_preview_enabled(),
            "content_type": str(cache.get("content_type", "image/jpeg") or "image/jpeg"),
            "source": str(cache.get("source", "") or ""),
        }

    def _update_director_preview_cache(self, camera_key: str, data: Optional[bytes], content_type: str, err: str = "", source: str = ""):
        if camera_key not in ("west", "east"):
            return
        try:
            cache = self._director_preview_cache[camera_key]
        except Exception:
            return
        cache["source"] = source or cache.get("source", "")
        if data:
            cache["data"] = bytes(data)
            cache["content_type"] = str(content_type or cache.get("content_type", "image/jpeg") or "image/jpeg")
            cache["updated_ts"] = time.time()
            cache["seq"] = int(cache.get("seq", 0) or 0) + 1
            cache["error"] = ""
            self._state_version += 1
            self._web_dirty = True
        elif err:
            cache["error"] = str(err)
            self._state_version += 1
            self._web_dirty = True

    def _set_director_preview_error(self, camera_key: str, err: str):
        err = str(err or "")
        last = str((getattr(self, "_director_preview_last_error", {}) or {}).get(camera_key, "") or "")
        if err == last:
            return
        self._director_preview_last_error[camera_key] = err
        if err:
            self._post(f"WEB: director preview {camera_key} error: {err}")
        else:
            self._post(f"WEB: director preview {camera_key} recovered")

    def _obs_preview_candidates(self, camera_key: str) -> List[Tuple[str, str]]:
        source_name = (self._video_input_name_for_camera(camera_key) or "").strip()
        scene_name = (self._scene_name_for_camera(camera_key) or "").strip()
        out: List[Tuple[str, str]] = []
        missing = getattr(self, "_director_preview_missing_sources", set()) or set()
        if source_name and source_name not in missing:
            out.append((source_name, "source"))
        if scene_name and scene_name != source_name:
            out.append((scene_name, "scene"))
        return out

    def _refresh_director_previews(self):
        if not self._director_preview_enabled():
            return
        now = time.time()
        width, height, refresh_ms, quality, backend = self._director_preview_cfg()
        if now < float(getattr(self, "_director_preview_next_at", 0.0) or 0.0):
            return
        self._director_preview_next_at = now + (float(refresh_ms) / 1000.0)

        if backend != getattr(self, "_director_preview_logged_backend", ""):
            self._director_preview_logged_backend = backend
            extra = ""
            if backend in ("obs_screenshot", "obs_mjpeg_stream"):
                try:
                    chosen_fmt = self.obs.choose_screenshot_format("jpeg")
                    supported = self.obs.get_supported_image_formats()
                    if supported:
                        extra = f" fmt={chosen_fmt} supported={supported}"
                    else:
                        extra = f" fmt={chosen_fmt}"
                except Exception:
                    pass
            self._post(f"WEB: director preview backend = {backend} @ {width}x{height} q={quality} every {refresh_ms}ms{extra}")

        # During app/OBS startup, OBS may not have opened WebSocket or may be
        # connected but still returning code 207 / NotReady. Do not log this as a
        # Director preview fault; just leave previews stale until OBS is ready.
        if backend in ("obs_screenshot", "obs_mjpeg_stream"):
            connected_at = float(getattr(self, "_obs_connected_at", 0.0) or 0.0)
            if (not self.obs.connected) or (connected_at > 0.0 and (time.time() - connected_at) < 2.0):
                for camera_key in ("west", "east"):
                    self._update_director_preview_cache(camera_key, None, "image/jpeg", err="OBS warming up", source="obs")
                return

        for camera_key in ("west", "east"):
            if backend in ("obs_screenshot", "obs_mjpeg_stream"):
                data = None
                ctype = "image/jpeg"
                final_err = ""
                final_source = ""
                tried = []
                for candidate_name, candidate_kind in self._obs_preview_candidates(camera_key):
                    tried.append(f"{candidate_kind}='{candidate_name}'")
                    data, ctype, err = self.obs.get_source_screenshot_bytes(
                        candidate_name,
                        image_width=width,
                        image_height=height,
                        image_quality=quality,
                        image_format=self.obs.choose_screenshot_format("jpeg"),
                    )
                    if data:
                        final_source = f"{candidate_kind}:{candidate_name}"
                        final_err = ""
                        break
                    err_l = str(err or "").lower()
                    if candidate_kind == "source" and ("no source was found by the name of" in err_l or "returned code 600" in err_l):
                        self._director_preview_missing_sources.add(candidate_name)
                        self._post(f"WEB: director preview skipping missing OBS source '{candidate_name}' for the rest of this run")
                    final_err = err or final_err
                if data:
                    self._update_director_preview_cache(camera_key, data, ctype or "image/jpeg", source=final_source)
                    self._set_director_preview_error(camera_key, "")
                else:
                    if tried and final_err:
                        final_err = final_err + " | tried " + ", ".join(tried)
                    self._update_director_preview_cache(camera_key, None, "image/jpeg", err=final_err or "OBS preview failed", source=final_source or ", ".join(tried))
                    self._set_director_preview_error(camera_key, final_err or "OBS preview failed")
            elif backend == "axis_snapshot":
                try:
                    data, ctype = self._fetch_axis_snapshot_bytes(camera_key, width, height)
                    self._update_director_preview_cache(camera_key, data, ctype or "image/jpeg", source=camera_key)
                    self._set_director_preview_error(camera_key, "")
                except Exception as e:
                    self._update_director_preview_cache(camera_key, None, "image/jpeg", err=str(e), source=camera_key)
                    self._set_director_preview_error(camera_key, str(e))
            else:
                msg = "MJPEG preview handled by proxy route"
                self._update_director_preview_cache(camera_key, None, "image/jpeg", err=msg, source=camera_key)
                self._set_director_preview_error(camera_key, msg)

    def _director_blind_delay_state(self, camera_key: str) -> dict:
        """Return Director-page state for a pending blind/delayed cut on one camera.

        A pending scene switch means the app has already prepared a camera and is
        intentionally waiting before cutting it live. When the configured preset
        delay for that target view is greater than zero, present it to the
        operator as a blind-delay countdown so a long wait does not look like a
        failure.
        """
        try:
            if not getattr(self, "_pending_scene_switch", None):
                return {"active": False, "remaining_s": 0.0}
            if str(getattr(self, "_pending_scene_switch_camera", "") or "") != str(camera_key):
                return {"active": False, "remaining_s": 0.0}

            view_num = getattr(self, "_pending_scene_switch_view", None)
            try:
                view_int = int(view_num) if view_num is not None else 0
            except Exception:
                view_int = 0

            preset_delay = 0
            try:
                preset_delay = int((getattr(self.cfg, "PRESET_DELAYS_SECONDS", {}) or {}).get(view_int, 0) or 0)
            except Exception:
                preset_delay = 0

            # Only label this as a blind delay when the per-view blind delay is actually enabled.
            if not (bool(getattr(self.cfg, "ENABLE_PRESET_DELAYS", True)) and preset_delay > 0):
                return {"active": False, "remaining_s": 0.0}

            due = float(getattr(self, "_pending_scene_switch_due", 0.0) or 0.0)
            remaining = max(0.0, due - time.time())
            label = self._label_for_view(view_int) if view_int else "next view"
            return {
                "active": True,
                "remaining_s": float(remaining),
                "view": view_int if view_int else None,
                "label": label,
                "scene": str(getattr(self, "_pending_scene_switch", "") or ""),
                "source": str(getattr(self, "_pending_scene_switch_source", "") or ""),
                "configured_delay_s": int(preset_delay),
            }
        except Exception:
            return {"active": False, "remaining_s": 0.0}

    def _director_camera_state(self, camera_key: str) -> dict:
        view_num = self.camera_positions.get(camera_key)
        ready_in = self._camera_remaining_ready_seconds(camera_key)
        confirmed = self._camera_position_confirmed(camera_key) if view_num is not None else False
        blind_delay = self._director_blind_delay_state(camera_key)
        blind_active = bool(blind_delay.get("active"))
        blind_remaining = float(blind_delay.get("remaining_s", 0.0) or 0.0)
        if blind_active and blind_remaining > 0.05:
            label = str(blind_delay.get("label") or "selected view")
            status = f"Blind delay applied — {label} ready in {blind_remaining:.1f}s"
        elif ready_in > 0.0:
            status = f"Moving — ready in {ready_in:.1f}s"
        elif confirmed and view_num is not None:
            status = f"Ready — {self._label_for_view(int(view_num))}"
        elif view_num is not None:
            status = f"Assigned — {self._label_for_view(int(view_num))}"
        else:
            status = "Unknown"
        return {
            "camera": camera_key,
            "current_view": int(view_num) if view_num is not None else None,
            "current_label": self._label_for_view(int(view_num)) if view_num is not None else "—",
            "ready_in_s": float(ready_in),
            "confirmed": bool(confirmed),
            "status": status,
            "blind_delay_active": bool(blind_active),
            "blind_delay_remaining_s": float(blind_remaining),
            "blind_delay_view": blind_delay.get("view"),
            "blind_delay_label": blind_delay.get("label", ""),
            "blind_delay_scene": blind_delay.get("scene", ""),
            "blind_delay_configured_s": blind_delay.get("configured_delay_s", 0),
        }

    def _clear_deferred_next_prepare(self, reason: str = ""):
        if self._deferred_next_view_num is None:
            return
        view_num = self._deferred_next_view_num
        label = self._label_for_view(int(view_num))
        audit_id = self._deferred_next_audit_id or None
        self._trace_or_audit(f"CLEAR DEFERRED NEXT -> view={view_num} label={label} ({reason})", audit_id)
        self._deferred_next_view_num = None
        self._deferred_next_source = ""
        self._deferred_next_audit_id = ""
        if reason:
            self._post(f"{reason}: cleared deferred next view {label}")

    def _defer_next_prepare(self, view_num: int, source: str, reason: str, audit_id: Optional[str] = None):
        label = self._label_for_view(int(view_num))
        self._deferred_next_view_num = int(view_num)
        self._deferred_next_source = source
        self._deferred_next_audit_id = audit_id or ""
        self._trace_or_audit(f"DEFER NEXT -> view={view_num} label={label} ({reason})", audit_id)
        self._post(f"{source}: next view {label} deferred until current cut completes")

    def _run_deferred_next_prepare_if_ready(self):
        if self._deferred_next_view_num is None:
            return
        if self._pending_scene_switch:
            return
        view_num = int(self._deferred_next_view_num)
        source = self._deferred_next_source or "AUTO"
        audit_id = self._deferred_next_audit_id or None
        label = self._label_for_view(view_num)
        self._deferred_next_view_num = None
        self._deferred_next_source = ""
        self._deferred_next_audit_id = ""
        self._trace_or_audit(f"RUN DEFERRED NEXT -> view={view_num} label={label}", audit_id)
        self._prepare_next_view_request(view_num, f"{source}: deferred next", audit_id=audit_id)

    def _handle_direct_camera_view(self, camera_key: str, view_num: int, source: str, audit_id: Optional[str] = None):
        camera_key = str(camera_key or "").strip().lower()
        if camera_key not in ("west", "east"):
            self._post(f"{source}: unknown camera '{camera_key}'")
            return
        if not (1 <= int(view_num) <= 10):
            self._post(f"{source}: invalid view {view_num}")
            return
        self._cancel_pending_scene_switch(f"{source}: direct camera steer")
        self._clear_deferred_next_prepare(f"{source}: direct camera steer")
        ok = self._send_camera_to_view(camera_key, int(view_num), f"{source}: direct steer", audit_id=audit_id)
        if ok:
            self._post(f"{source}: {camera_key} manually steered to {self._label_for_view(int(view_num))}")

    def _axis_move_ready_tick(self):
        for cam_key in ("west", "east"):
            due = float((getattr(self, "camera_ready_at", {}) or {}).get(cam_key, 0.0) or 0.0)
            if due > 0.0 and time.time() >= due:
                self.camera_ready_at[cam_key] = 0.0
                self.camera_positions_confirmed[cam_key] = True
                view_num = self.camera_positions.get(cam_key)
                if view_num is not None:
                    self._post(f"AUTO: {cam_key} ready on view {view_num} ({self._label_for_view(view_num)})")

    def _label_for_view(self, view_num: int) -> str:
        return str((getattr(self.cfg, "PRESET_LABELS", {}) or {}).get(view_num, f"Preset {view_num}"))

    def _midi_note_meaning(self, note_num: int) -> str:
        if int(note_num) == int(getattr(self.cfg, "NOTE_START_STREAM", 60)):
            return "start stream"
        if int(note_num) == int(getattr(self.cfg, "NOTE_STOP_STREAM", 61)):
            return "stop stream"
        if int(note_num) == int(getattr(self.cfg, "NOTE_REC_TOGGLE", 62)):
            return "record toggle"
        lo = int(getattr(self.cfg, "NOTE_PRESET_FIRST", 70))
        hi = int(getattr(self.cfg, "NOTE_PRESET_LAST", 79))
        if lo <= int(note_num) <= hi:
            preset = int(note_num) - lo + 1
            return f"view {preset} {self._label_for_view(preset)}"
        return f"note {note_num}"

    def _camera_controller(self, camera_key: str):
        if camera_key == "west":
            return self.west_cam
        return self.east_cam

    def _camera_can_route(self, camera_key: str) -> bool:
        return bool(self.camera_available.get(camera_key, True))

    def _ordered_cameras_for_view(self, view_num: int) -> List[str]:
        # Axis-only variant: no permanent per-view preference.
        return ["west", "east"]

    def _camera_position_confirmed(self, camera_key: str) -> bool:
        return bool(self.camera_positions_confirmed.get(camera_key, False)) and self._camera_remaining_ready_seconds(camera_key) <= 0.0

    def _select_camera_for_view(self, view_num: int) -> Tuple[Optional[str], bool]:
        program = self._current_program_camera_key()
        other = self._other_axis_camera(program)

        # Simplified Axis-only selection:
        # 1) use any already-ready shot first (prefer the off-air camera if both are ready)
        # 2) otherwise use the off-air camera so the on-air shot does not move
        # 3) only fall back to the on-air camera if there is no off-air option
        # If the current Program camera is already on the requested view, keep it live.
        # This prevents repeated Proclaim/manual cues from causing an unnecessary camera flip
        # when both Axis cameras are parked on the same view, such as Panorama.
        ready_order = [program, other]
        for cam_key in ready_order:
            if self._camera_can_route(cam_key) and self.camera_positions.get(cam_key) == view_num and self._camera_position_confirmed(cam_key):
                return cam_key, True

        if self._camera_can_route(other):
            return other, False

        if self._camera_can_route(program):
            return program, self.camera_positions.get(program) == view_num and self._camera_position_confirmed(program)

        return None, False

    def _switch_program_scene(self, scene_name: str, source: str, audit_id: Optional[str] = None) -> bool:
        scene_name = (scene_name or "").strip()
        if not scene_name:
            self._post(f"{source}: blank scene name")
            return False
        if getattr(self.cfg, "FORCE_CUT_TRANSITION", True):
            self.obs.set_current_scene_transition_name(getattr(self.cfg, "OBS_CUT_TRANSITION_NAME", "Cut"))
        ok, msg = self.obs.set_current_program_scene_name(scene_name)
        if ok:
            scene_msg = f"{source}: cut to scene '{scene_name}'"
            self._set_last_scene_action(scene_msg)
            self._trace_or_audit(f"SCENE CUT -> {scene_name} ({source})", audit_id)
            self._post(scene_msg)
            return True
        fail_msg = f"{source}: scene switch failed for '{scene_name}' ({msg})"
        self._set_last_scene_action(fail_msg)
        self._trace_or_audit(f"SCENE CUT FAIL -> {scene_name} ({source}) {msg}", audit_id)
        self._post(fail_msg)
        return False

    def _cancel_pending_scene_switch(self, reason: str = ""):
        if not self._pending_scene_switch:
            return
        sc = self._pending_scene_switch
        vw = self._pending_scene_switch_view
        cam = self._pending_scene_switch_camera
        self._trace_or_audit(f"CANCEL DELAYED CUT -> scene={sc} camera={cam} view={vw} ({reason})", self._pending_scene_switch_audit_id or None)
        self._pending_scene_switch = None
        self._pending_scene_switch_due = 0.0
        self._pending_scene_switch_camera = ""
        self._pending_scene_switch_view = None
        self._pending_scene_switch_source = ""
        self._pending_scene_switch_audit_id = ""
        if reason:
            self._post(f"{reason}: cancelled pending cut to {sc} ({cam} view {vw})")

    def _schedule_scene_switch(self, scene_name: str, camera_key: str, view_num: int, source: str, delay_s: int, audit_id: Optional[str] = None):
        self._cancel_pending_scene_switch(source)
        self._pending_scene_switch = scene_name
        self._pending_scene_switch_due = time.time() + max(0, int(delay_s))
        self._pending_scene_switch_camera = camera_key
        self._pending_scene_switch_view = view_num
        self._pending_scene_switch_source = source
        self._pending_scene_switch_audit_id = audit_id or ""
        self._trace_or_audit(f"DELAYED CUT -> scene={scene_name} camera={camera_key} view={view_num} label={self._label_for_view(view_num)} delay={delay_s}s", audit_id)
        self._post(f"{source}: {self._label_for_view(view_num)} prepared on {camera_key}; cut in {delay_s}s")

    def _scene_switch_tick(self):
        if not self._pending_scene_switch:
            return
        if time.time() < self._pending_scene_switch_due:
            return
        scene_name = self._pending_scene_switch
        source = self._pending_scene_switch_source or "AUTO"
        cam_key = self._pending_scene_switch_camera
        view_num = self._pending_scene_switch_view
        audit_id = self._pending_scene_switch_audit_id or None
        if cam_key and view_num is not None:
            current_view = self.camera_positions.get(cam_key)
            if current_view != view_num:
                self._trace_or_audit(f"SKIP DELAYED CUT -> stale reservation scene={scene_name} camera={cam_key} pending_view={view_num} current_view={current_view}", audit_id)
                self._cancel_pending_scene_switch(f"{source}: stale delayed cut")
                return
            remaining = self._camera_remaining_ready_seconds(cam_key)
            if remaining > 0.0 or not self._camera_position_confirmed(cam_key):
                self._pending_scene_switch_due = time.time() + max(0.25, remaining)
                self._trace_or_audit(f"HOLD DELAYED CUT -> scene={scene_name} camera={cam_key} view={view_num} remaining={remaining:.1f}s", audit_id)
                return
        self._pending_scene_switch = None
        self._pending_scene_switch_due = 0.0
        self._pending_scene_switch_camera = ""
        self._pending_scene_switch_view = None
        self._pending_scene_switch_source = ""
        self._pending_scene_switch_audit_id = ""
        self._trace_or_audit(f"FIRE DELAYED CUT -> scene={scene_name} camera={cam_key} view={view_num}", audit_id)
        self._switch_program_scene(scene_name, f"{source}: delayed cut", audit_id=audit_id)
        if cam_key and view_num is not None:
            self.camera_positions[cam_key] = view_num
            self.camera_positions_confirmed[cam_key] = True
            self.camera_ready_at[cam_key] = 0.0
        self._run_deferred_next_prepare_if_ready()

    def _send_camera_to_view(self, camera_key: str, view_num: int, source: str, audit_id: Optional[str] = None) -> bool:
        label = self._label_for_view(view_num)
        axis_name = str((getattr(self.cfg, "AXIS_VIEW_PRESET_NAMES", {}) or {}).get(view_num, "")).strip()
        if self._pending_scene_switch and self._pending_scene_switch_camera == camera_key and self._pending_scene_switch_view != view_num:
            self._cancel_pending_scene_switch(f"{source}: camera reassigned")
        ready_in = max(0.0, float(getattr(self.cfg, "AXIS_TRAVEL_TIME_SECONDS", 4.0) or 0.0))
        if self.cfg.HOME_TEST_MODE:
            trace_cmd = self._axis_command_trace(camera_key, axis_name or f"view{view_num}")
            self._trace_or_audit(f"CAMERA CMD -> SIMULATED {trace_cmd} label={label}", audit_id)
            self.camera_positions[camera_key] = view_num
            self.camera_positions_confirmed[camera_key] = (ready_in <= 0.0)
            self.camera_ready_at[camera_key] = time.time() + ready_in if ready_in > 0.0 else 0.0
            self._trace_or_audit(f"CAMERA RESULT -> simulated move camera={camera_key} view={view_num} label={label} ready_in={ready_in:.1f}s", audit_id)
            if ready_in > 0.0:
                self._post(f"{source}: {camera_key} -> view {view_num} ({label}) simulated; ready in {ready_in:.1f}s")
            else:
                self._post(f"{source}: {camera_key} -> view {view_num} ({label}) simulated")
            return True

        try:
            if not axis_name:
                raise ValueError(f"No Axis preset name configured for view {view_num}")
            self._trace_or_audit(f"CAMERA CMD -> {self._axis_command_trace(camera_key, axis_name)} label={label}", audit_id)
            if camera_key == "west":
                self.west_cam.recall_preset_name(axis_name)
            else:
                self.east_cam.recall_preset_name(axis_name)
            self.camera_positions[camera_key] = view_num
            self.camera_positions_confirmed[camera_key] = (ready_in <= 0.0)
            self.camera_ready_at[camera_key] = time.time() + ready_in if ready_in > 0.0 else 0.0
            self._trace_or_audit(f"CAMERA RESULT -> success camera={camera_key} view={view_num} label={label} ready_in={ready_in:.1f}s", audit_id)
            extra = f" [Axis preset '{axis_name}']" if axis_name else ""
            if ready_in > 0.0:
                self._post(f"{source}: {camera_key} -> view {view_num} ({label}) sent{extra}; ready in {ready_in:.1f}s")
            else:
                self._post(f"{source}: {camera_key} -> view {view_num} ({label}) sent{extra}")
            return True
        except Exception as e:
            self._trace_or_audit(f"CAMERA RESULT -> ERROR camera={camera_key} view={view_num} label={label} err={e}", audit_id)
            self._post(f"{source}: {camera_key} preset error: {e}")
            return False

    def _route_view_request(self, view_num: int, source: str, audit_id: Optional[str] = None):
        # Any new current-view request replaces any older pending auto cut.
        self._cancel_pending_scene_switch(source)
        self._clear_deferred_next_prepare(f"{source}: new current view")

        label = self._label_for_view(view_num)
        program = self._current_program_camera_key()
        chosen, already_ready = self._select_camera_for_view(view_num)
        if chosen is None:
            fallback_scene = getattr(self.cfg, "OBS_SAFE_FALLBACK_SCENE", "West View")
            self._trace_or_audit(f"ROUTE NOW -> view={view_num} label={label} program={program} chosen=none fallback_scene={fallback_scene}", audit_id)
            self._post(f"{source}: no routable camera for {label} — fallback to {fallback_scene}")
            self._switch_program_scene(fallback_scene, source, audit_id=audit_id)
            return

        target_scene = self._scene_name_for_camera(chosen)
        confirmed = self._camera_position_confirmed(chosen)
        move_needed = not (self.camera_positions.get(chosen) == view_num and confirmed)
        remaining = self._camera_remaining_ready_seconds(chosen)
        self._trace_or_audit(
            f"ROUTE NOW -> view={view_num} label={label} program={program} chosen={chosen} target_scene={target_scene} "
            f"ready={already_ready} move_needed={move_needed} confirmed={confirmed} remaining={remaining:.1f}s",
            audit_id,
        )

        blind_delay = self._clamped_preset_delay(view_num) if getattr(self.cfg, "ENABLE_PRESET_DELAYS", True) else 0

        if already_ready or not move_needed:
            current_scene_name, _ = self.obs._get_current_program_scene_name()
            already_on_target_scene = (self._camera_key_for_scene(current_scene_name) == chosen and (current_scene_name or "").strip() == target_scene)
            if already_on_target_scene:
                self._trace_or_audit(
                    f"ROUTE NOW -> no-op already live on target scene={target_scene} camera={chosen} view={view_num} label={label}",
                    audit_id,
                )
                self.camera_positions[chosen] = view_num
                self.camera_positions_confirmed[chosen] = True
                self.camera_ready_at[chosen] = 0.0
                return

            delay_s = int(math.ceil(max(float(blind_delay), float(remaining))))
            if delay_s > 0:
                self._schedule_scene_switch(target_scene, chosen, view_num, source, delay_s, audit_id=audit_id)
            else:
                self._switch_program_scene(target_scene, source, audit_id=audit_id)
                self.camera_positions[chosen] = view_num
                self.camera_positions_confirmed[chosen] = True
                self.camera_ready_at[chosen] = 0.0
            return

        # Avoid moving the on-air camera for a new current shot unless no other option exists.
        if chosen == program:
            safe_scene = getattr(self.cfg, "OBS_SAFE_FALLBACK_SCENE", "West View")
            self._trace_or_audit(f"FALLBACK -> requested current shot would move on-air camera={chosen}; safe scene={safe_scene} view={view_num} label={label}", audit_id)
            self._post(f"{source}: {label} not ready off-air — safe fallback used")
            self._switch_program_scene(safe_scene, source, audit_id=audit_id)
            return

        if not self._send_camera_to_view(chosen, view_num, source, audit_id=audit_id):
            safe_scene = getattr(self.cfg, "OBS_SAFE_FALLBACK_SCENE", "West View")
            self._trace_or_audit(f"FALLBACK -> safe scene={safe_scene} view={view_num} label={label}", audit_id)
            self._post(f"{source}: no ready fallback for {label} — safe fallback used")
            self._switch_program_scene(safe_scene, source, audit_id=audit_id)
            return

        remaining = self._camera_remaining_ready_seconds(chosen)
        delay_s = int(math.ceil(max(float(blind_delay), float(remaining))))
        if delay_s > 0:
            self._schedule_scene_switch(target_scene, chosen, view_num, source, delay_s, audit_id=audit_id)
        else:
            self._switch_program_scene(target_scene, source, audit_id=audit_id)
            self.camera_positions[chosen] = view_num
            self.camera_positions_confirmed[chosen] = True
            self.camera_ready_at[chosen] = 0.0

    def _prepare_next_view_request(self, view_num: int, source: str, audit_id: Optional[str] = None):
        label = self._label_for_view(view_num)

        # Guard against moving the still-live camera while a delayed current-view cut is pending.
        # Until that cut actually fires, there is no safe free camera to repurpose unless the
        # future off-air camera is already on the requested next shot.
        if self._pending_scene_switch and self._pending_scene_switch_camera:
            future_program = self._pending_scene_switch_camera
            future_off_air = self._other_axis_camera(future_program)
            actual_live = self._actual_program_camera_key() or self._current_program_camera_key()
            if self.camera_positions.get(future_off_air) == view_num and self._camera_position_confirmed(future_off_air):
                self._trace_or_audit(
                    f"NEXT -> view={view_num} label={label} already prepared on future off-air camera={future_off_air} while cut pending from live={actual_live} to program={future_program}",
                    audit_id,
                )
                self._post(f"{source}: next view {label} already prepared on {future_off_air}")
                return
            self._defer_next_prepare(
                view_num,
                source,
                f"pending delayed cut live={actual_live} -> program={future_program}",
                audit_id=audit_id,
            )
            return

        used_now = self._actual_program_camera_key() or self._current_program_camera_key()
        prepare = self._other_axis_camera(used_now)
        if not self._camera_can_route(prepare):
            self._trace_or_audit(f"NEXT -> view={view_num} label={label} program={used_now} prepare=none unavailable", audit_id)
            self._post(f"{source}: next view {label} skipped — no off-air camera available")
            return
        if self.camera_positions.get(prepare) == view_num and self._camera_position_confirmed(prepare):
            self._trace_or_audit(f"NEXT -> view={view_num} label={label} already prepared on {prepare}", audit_id)
            self._post(f"{source}: next view {label} already prepared on {prepare}")
            return
        self._trace_or_audit(f"NEXT -> view={view_num} label={label} program={used_now} prepare={prepare}", audit_id)
        self._send_camera_to_view(prepare, view_num, f"{source}: next prepare", audit_id=audit_id)

    def _handle_manual_scene_cut(self, camera_key: str, source: str):
        self._cancel_pending_scene_switch(source)
        self._clear_deferred_next_prepare(f"{source}: manual cut")
        scene_name = self._scene_name_for_camera(camera_key)
        self._switch_program_scene(scene_name, source)

    def _set_audio_master_db(self, value_db: float, source: str):
        lo = float(getattr(self.cfg, "AUDIO_MASTER_MIN_DB", -40.0))
        hi = float(getattr(self.cfg, "AUDIO_MASTER_MAX_DB", 6.0))
        val = max(lo, min(hi, float(value_db)))
        self.audio_master_db = val
        targets = self._audio_inputs()
        errs = []
        if not targets:
            errs.append("no audio targets configured")
        for input_name in targets:
            ok, msg = self.obs.set_input_volume_db(input_name, val)
            if not ok:
                errs.append(f"{input_name}: {msg}")
        target_label = ", ".join(targets) if targets else "none"
        if errs:
            self._post(f"{source}: audio master set to {val:.1f} dB for {target_label} with warnings: {'; '.join(errs)}")
        else:
            self._post(f"{source}: audio master -> {val:.1f} dB ({target_label})")

    def _sync_offsets_enabled(self) -> bool:
        # In Axis embedded-audio mode, audio/video are carried together in each RTSP Media Source.
        # The old sync page adjusts a separate ASIO audio input, so it is intentionally disabled.
        if self._using_axis_embedded_audio():
            return False
        return bool(getattr(self.cfg, "SYNC_OFFSET_WEB_ENABLED", True))

    def _sync_audio_input_name_configured(self) -> str:
        return str(getattr(self.cfg, "OBS_AUDIO_INPUT_SHARED", "ASIO_audio") or "ASIO_audio").strip()

    @staticmethod
    def _sync_name_norm(name: str) -> str:
        try:
            return "".join(ch for ch in str(name or "").lower() if ch.isalnum())
        except Exception:
            return ""

    def _sync_audio_input_name(self, force_resolve: bool = False) -> str:
        configured = self._sync_audio_input_name_configured()
        cached = str((getattr(self, "_sync_resolved_inputs", {}) or {}).get("shared", "") or "").strip()
        if cached and not force_resolve:
            return cached
        if not self.obs.connected:
            return configured or cached
        names, err = self.obs.get_input_names()
        if err or not names:
            return configured or cached

        choice = ""
        if configured:
            for n in names:
                if str(n) == configured:
                    choice = str(n)
                    break
            if not choice:
                cfold = configured.lower()
                for n in names:
                    if str(n).lower() == cfold:
                        choice = str(n)
                        break
            if not choice:
                ncfg = self._sync_name_norm(configured)
                for n in names:
                    if self._sync_name_norm(n) == ncfg:
                        choice = str(n)
                        break
        if not choice:
            matches = [str(n) for n in names if ("audio" in str(n).lower() or "asio" in str(n).lower())]
            if matches:
                exact_asio = [n for n in matches if self._sync_name_norm(n) == self._sync_name_norm("ASIO_audio")]
                choice = exact_asio[0] if exact_asio else matches[0]

        if choice:
            prev = str((getattr(self, "_sync_resolved_inputs", {}) or {}).get("shared", "") or "")
            self._sync_resolved_inputs["shared"] = choice
            if choice != prev and choice != configured:
                self._post(f"WEB SYNC: resolved shared audio input '{choice}' (configured '{configured or 'blank'}')")
            return choice
        return configured or cached

    def _sync_unlock_remaining_s(self) -> float:
        return max(0.0, float(getattr(self, "_sync_unlock_until", 0.0) or 0.0) - time.time())

    def _sync_offsets_mark_dirty(self):
        with self._ui_lock:
            self._state_version += 1
            self._web_dirty = True

    def _set_sync_error(self, err: str):
        err = str(err or "")
        last = str((getattr(self, "_sync_offset_errors", {}) or {}).get("shared", "") or "")
        if err == last:
            return
        self._sync_offset_errors["shared"] = err
        self._sync_offsets_mark_dirty()
        if err:
            self._post(f"WEB SYNC: shared sync error: {err}")

    def _sync_offsets_snapshot(self) -> dict:
        if self._using_axis_embedded_audio():
            targets = self._axis_audio_input_names()
            target_txt = " / ".join(targets) if targets else "West_axis / East_axis"
            reason = "Axis embedded audio mode: audio and video are carried together inside the camera RTSP Media Sources. The old ASIO sync-offset control is disabled."
            return {
                "enabled": False,
                "mode": self._audio_mode(),
                "disabled_reason": reason,
                "locked": True,
                "unlock_remaining_s": 0.0,
                "step_ms": int(getattr(self.cfg, "SYNC_OFFSET_STEP_MS", 20) or 20),
                "coarse_step_ms": int(getattr(self.cfg, "SYNC_OFFSET_COARSE_STEP_MS", 100) or 100),
                "min_ms": int(getattr(self.cfg, "SYNC_OFFSET_MIN_MS", -950) or -950),
                "max_ms": int(getattr(self.cfg, "SYNC_OFFSET_MAX_MS", 20000) or 20000),
                "shared_ms": 0,
                "shared_input": target_txt,
                "shared_error": reason,
                "west_ms": 0,
                "east_ms": 0,
                "west_input": targets[0] if len(targets) > 0 else "West_axis",
                "east_input": targets[1] if len(targets) > 1 else "East_axis",
                "west_error": reason,
                "east_error": reason,
            }
        shared_ms = int((getattr(self, "sync_offsets_ms", {}) or {}).get("shared", 0) or 0)
        shared_input = self._sync_audio_input_name()
        shared_error = str((getattr(self, "_sync_offset_errors", {}) or {}).get("shared", "") or "")
        return {
            "enabled": self._sync_offsets_enabled(),
            "mode": self._audio_mode(),
            "disabled_reason": "",
            "locked": self._sync_unlock_remaining_s() <= 0.0,
            "unlock_remaining_s": self._sync_unlock_remaining_s(),
            "step_ms": int(getattr(self.cfg, "SYNC_OFFSET_STEP_MS", 20) or 20),
            "coarse_step_ms": int(getattr(self.cfg, "SYNC_OFFSET_COARSE_STEP_MS", 100) or 100),
            "min_ms": int(getattr(self.cfg, "SYNC_OFFSET_MIN_MS", -950) or -950),
            "max_ms": int(getattr(self.cfg, "SYNC_OFFSET_MAX_MS", 20000) or 20000),
            "shared_ms": shared_ms,
            "shared_input": shared_input,
            "shared_error": shared_error,
            # Compatibility aliases for any older clients still expecting west/east fields.
            "west_ms": shared_ms,
            "east_ms": shared_ms,
            "west_input": shared_input,
            "east_input": shared_input,
            "west_error": shared_error,
            "east_error": shared_error,
        }

    def _sync_clamp_ms(self, value_ms: int) -> int:
        lo = int(getattr(self.cfg, "SYNC_OFFSET_MIN_MS", -950) or -950)
        hi = int(getattr(self.cfg, "SYNC_OFFSET_MAX_MS", 20000) or 20000)
        try:
            return max(lo, min(hi, int(round(float(value_ms)))))
        except Exception:
            return max(lo, min(hi, 0))

    def _sync_offsets_poll_tick(self, force: bool = False):
        if not self._sync_offsets_enabled() or not self.obs.connected:
            return
        now = time.time()
        if (not force) and now < float(getattr(self, "_sync_offsets_next_poll", 0.0) or 0.0):
            return
        self._sync_offsets_next_poll = now + 1.0
        changed = False
        input_name = self._sync_audio_input_name(force_resolve=True)
        if not input_name:
            return
        value_ms, err = self.obs.get_input_audio_sync_offset_ms(input_name)
        if value_ms is not None:
            v = int(value_ms)
            if self.sync_offsets_ms.get("shared") != v:
                self.sync_offsets_ms["shared"] = v
                changed = True
            if self._sync_offset_errors.get("shared"):
                self._sync_offset_errors["shared"] = ""
                changed = True
        elif err:
            if self._sync_offset_errors.get("shared") != str(err):
                self._sync_offset_errors["shared"] = str(err)
                changed = True
        if changed:
            self._sync_offsets_mark_dirty()

    def _sync_unlock(self, source: str) -> Tuple[bool, str]:
        if not self._sync_offsets_enabled():
            return False, "Sync page disabled"
        seconds = max(5, int(getattr(self.cfg, "SYNC_OFFSET_UNLOCK_SECONDS", 30) or 30))
        self._sync_unlock_until = time.time() + seconds
        self._sync_offsets_mark_dirty()
        self._post(f"{source}: sync controls unlocked for {seconds}s")
        return True, f"Sync controls unlocked for {seconds}s"

    def _sync_locked(self) -> bool:
        return self._sync_unlock_remaining_s() <= 0.0

    def _sync_set_offset(self, value_ms: int, source: str) -> Tuple[bool, str]:
        if not self._sync_offsets_enabled():
            return False, "Sync page disabled"
        if self._sync_locked():
            return False, "Sync controls are locked"
        if not self.obs.connected:
            return False, "OBS not connected"
        input_name = self._sync_audio_input_name(force_resolve=True)
        if not input_name:
            return False, "No OBS audio input configured"
        clamped = self._sync_clamp_ms(value_ms)
        ok, msg = self.obs.set_input_audio_sync_offset_ms(input_name, clamped)
        if ok:
            self.sync_offsets_ms["shared"] = clamped
            if self._sync_offset_errors.get("shared"):
                self._sync_offset_errors["shared"] = ""
            self._sync_offsets_mark_dirty()
            self._post(f"{source}: shared audio sync -> {clamped} ms")
            return True, f"Shared audio sync set to {clamped} ms"
        self._set_sync_error(msg)
        return False, str(msg)

    def _sync_adjust_offset(self, delta_ms: int, source: str) -> Tuple[bool, str]:
        self._sync_offsets_poll_tick(force=True)
        current = int((getattr(self, "sync_offsets_ms", {}) or {}).get("shared", 0) or 0)
        try:
            delta_ms = int(round(float(delta_ms)))
        except Exception:
            return False, "Invalid delta"
        return self._sync_set_offset(current + delta_ms, source)

    def _safe_event_attr(self, obj: Any, *names: str):
        for name in names:
            try:
                if isinstance(obj, dict) and name in obj:
                    return obj.get(name)
                if hasattr(obj, name):
                    return getattr(obj, name)
            except Exception:
                continue
        return None

    def _audio_meter_mark_dirty(self):
        """Tell the Web HUD to publish a fresh state packet after meter updates."""
        try:
            with self._ui_lock:
                self._state_version += 1
                self._web_dirty = True
        except Exception:
            pass

    def _audio_meter_event_payloads(self, data: Any) -> List[Any]:
        """Return possible OBS event payload objects for different obsws-python shapes.

        Depending on obsws-python and OBS WebSocket versions, the callback argument may
        be the event-data object itself, a wrapper with event_data/eventData, or a dict.
        """
        payloads: List[Any] = []

        def add(obj: Any):
            if obj is None:
                return
            try:
                if not any(obj is existing for existing in payloads):
                    payloads.append(obj)
            except Exception:
                payloads.append(obj)

        add(data)
        for obj in list(payloads):
            for key in ("event_data", "eventData", "data", "event"):
                add(self._safe_event_attr(obj, key))
        return payloads

    def _audio_meter_items_from_event(self, data: Any) -> List[Any]:
        for payload in self._audio_meter_event_payloads(data):
            items = self._safe_event_attr(payload, "inputs", "inputMeters", "input_meters", "meters")
            if isinstance(items, (list, tuple)):
                return list(items)
            if isinstance(items, dict):
                return list(items.values())
        # Very defensive fallback: some wrappers could pass the input list directly.
        if isinstance(data, (list, tuple)):
            return list(data)
        return []

    def _audio_meter_event_shape(self, data: Any, items: List[Any]) -> str:
        try:
            root_type = type(data).__name__
            payload_types = ",".join(type(p).__name__ for p in self._audio_meter_event_payloads(data)[:4])
            first = items[0] if items else None
            if isinstance(first, dict):
                keys = ",".join(str(k) for k in list(first.keys())[:8])
            elif first is not None:
                keys = ",".join(str(k) for k in list(vars(first).keys())[:8]) if hasattr(first, "__dict__") else type(first).__name__
            else:
                keys = "none"
            return f"root={root_type} payloads={payload_types or 'none'} items={len(items)} first_keys={keys}"
        except Exception as e:
            return f"shape unavailable: {e}"

    def _flatten_numbers(self, value: Any) -> List[float]:
        out: List[float] = []
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(float(value))
        elif isinstance(value, (list, tuple)):
            for item in value:
                out.extend(self._flatten_numbers(item))
        elif isinstance(value, dict):
            for item in value.values():
                out.extend(self._flatten_numbers(item))
        elif value is not None and hasattr(value, "__dict__"):
            try:
                for item in vars(value).values():
                    out.extend(self._flatten_numbers(item))
            except Exception:
                pass
        return out

    def _extract_level_db(self, item: Any) -> Optional[float]:
        # Prefer OBS-provided dB values when present. OBS WebSocket commonly uses
        # inputLevelsDb/input_levels_db, but wrappers vary in case and naming.
        db_fields = [
            self._safe_event_attr(item,
                                  "input_levels_db", "inputLevelsDb", "input_levels_dbfs", "inputLevelsDbfs",
                                  "levels_db", "levelsDb", "level_db", "levelDb",
                                  "peak_db", "peakDb", "magnitude_db", "magnitudeDb"),
        ]
        for field in db_fields:
            nums = self._flatten_numbers(field)
            if nums:
                # Clamp extreme values so a bad packet cannot wreck the HUD scale.
                return max(-120.0, min(24.0, max(nums)))

        # Fall back to linear/multiplier values and convert to dB.
        mul_fields = [
            self._safe_event_attr(item,
                                  "input_levels_mul", "inputLevelsMul", "input_levels_magnitude", "inputLevelsMagnitude",
                                  "levels_mul", "levelsMul", "level_mul", "levelMul",
                                  "peak_mul", "peakMul", "magnitude", "magnitudes"),
        ]
        for field in mul_fields:
            raw_nums = [n for n in self._flatten_numbers(field) if n is not None]
            if raw_nums:
                positive = [n for n in raw_nums if n > 0]
                if positive:
                    return max(-120.0, min(24.0, 20.0 * math.log10(max(positive))))
                # A multiplier array of all zeros is still a valid OBS meter packet:
                # it means digital silence, not a parser failure. Report the meter floor
                # so the HUD can stay quiet without logging a false warning.
                return -120.0
        return None

    def _audio_level_for_input(self, input_name: str) -> Optional[float]:
        if not input_name:
            return None
        # Exact match first.
        if input_name in self._audio_levels_db:
            return float(self._audio_levels_db.get(input_name, -60.0))
        # Then case-insensitive and normalized matches. This covers harmless name
        # differences such as whitespace/case returned by the event wrapper.
        wanted_fold = str(input_name).strip().lower()
        wanted_norm = self._sync_name_norm(input_name)
        for k, v in list(self._audio_levels_db.items()):
            try:
                if str(k).strip().lower() == wanted_fold or self._sync_name_norm(str(k)) == wanted_norm:
                    return float(v)
            except Exception:
                continue
        return None

    def _current_audio_meter_db(self) -> float:
        # If OBS meter events stop, do not leave the HUD meter frozen at the
        # last value forever. That can falsely imply the audio path is still alive.
        try:
            stale_after = max(1.0, float(getattr(self.cfg, "AUDIO_METER_STALE_AFTER_SECONDS", 5.0) or 5.0))
            last_ts = float(getattr(self, "_audio_meter_last_update_ts", 0.0) or 0.0)
            if last_ts > 0.0 and (time.time() - last_ts) > stale_after:
                return -60.0
        except Exception:
            pass

        targets = self._audio_inputs()
        vals = []
        for name in targets:
            v = self._audio_level_for_input(name)
            if v is not None:
                vals.append(float(v))
        if vals:
            return float(max(vals))
        if self._audio_levels_db:
            return float(max(self._audio_levels_db.values()))
        return -60.0

    def _start_audio_events_if_possible(self):
        if self._audio_event_started:
            return
        if EventClient is None or Subs is None:
            if not self._audio_warned_missing_event_client:
                self._post("OBS audio meter events unavailable: obsws-python EventClient / Subs not present")
                self._audio_warned_missing_event_client = True
            return
        try:
            subs = int(Subs.INPUTVOLUMEMETERS)
            self._audio_event_client = EventClient(
                host=self.cfg.OBS_HOST,
                port=self.cfg.OBS_PORT,
                password=self.cfg.OBS_PASSWORD or None,
                timeout=5,
                subs=subs,
            )

            def on_input_volume_meters(data):
                try:
                    items = self._audio_meter_items_from_event(data)
                    if not self._audio_meter_seen_event:
                        self._audio_meter_seen_event = True
                        self._post("OBS audio meter event received: " + self._audio_meter_event_shape(data, items))

                    levels: Dict[str, float] = {}
                    for item in items:
                        name = self._safe_event_attr(item, "input_name", "inputName", "source_name", "sourceName", "name")
                        if not name:
                            continue
                        db = self._extract_level_db(item)
                        if db is None:
                            continue
                        levels[str(name).strip()] = float(db)

                    if levels:
                        self._audio_levels_db.update(levels)
                        self.audio_master_meter_db = self._current_audio_meter_db()
                        self._audio_meter_last_update_ts = time.time()
                        self._audio_meter_mark_dirty()
                        if not self._audio_meter_logged_success:
                            targets = ", ".join(self._audio_inputs()) or "none"
                            got = ", ".join(sorted(levels.keys())[:12])
                            self._post(f"OBS audio meter levels active: targets={targets}; event inputs={got}; HUD meter={self.audio_master_meter_db:.1f} dB")
                            self._audio_meter_logged_success = True
                    else:
                        now = time.time()
                        if now - float(getattr(self, "_audio_meter_last_warn_at", 0.0) or 0.0) > 30.0:
                            self._audio_meter_last_warn_at = now
                            self._post("OBS audio meter event received but no usable levels parsed: " + self._audio_meter_event_shape(data, items))
                except Exception as e:
                    now = time.time()
                    if now - float(getattr(self, "_audio_meter_last_error_at", 0.0) or 0.0) > 30.0:
                        self._audio_meter_last_error_at = now
                        self._post(f"OBS audio meter parse error: {e}")

            self._audio_event_client.callback.register(on_input_volume_meters)
            self._audio_event_started = True
            self._post("OBS audio meter events connected")
        except Exception as e:
            if not self._audio_warned_missing_event_client:
                self._post(f"OBS audio meter event start failed: {e}")
                self._audio_warned_missing_event_client = True

    def _stop_audio_events_if_possible(self):
        try:
            if self._audio_event_client is not None:
                self._audio_event_client.disconnect()
        except Exception:
            pass
        self._audio_event_client = None
        self._audio_event_started = False


    async def _drain_cmds(self):
        """Runs on worker thread; executes any queued commands."""
        if self._cmd_queue is None:
            return
        while True:
            try:
                cmd = self._cmd_queue.get_nowait()
            except Exception:
                break

            try:
                ctype = cmd.get("type")
                source = cmd.get("source", "WEB")
                if ctype == "action":
                    action = cmd.get("action")
                    if action == "start":
                        self._start_stream_flow(source)
                    elif action == "stop":
                        self._request_stop(source)
                    elif action == "rec":
                        self._toggle_record(source)
                    elif action == "scene_west":
                        self._handle_manual_scene_cut("west", source)
                    elif action == "scene_east":
                        self._handle_manual_scene_cut("east", source)
                elif ctype == "preset":
                    self._handle_preset(int(cmd.get("preset", 0)), source)
                elif ctype == "audio":
                    self._set_audio_master_db(float(cmd.get("value_db", 0.0)), source)
                elif ctype == "direct_cam_view":
                    cam_key = str(cmd.get("camera", "") or "")
                    view_num = int(cmd.get("view", 0) or 0)
                    self._post(f"{source}: director command received for {cam_key} view {view_num}")
                    self._handle_direct_camera_view(cam_key, view_num, source)
            except Exception as e:
                self._post(f"CMD error: {e}")

    def _clamped_preset_delay(self, preset_num: int) -> int:
        """Return per-preset delay for MIDI/automation, clamped to 0..30 seconds."""
        try:
            raw = int((self.cfg.PRESET_DELAYS_SECONDS or {}).get(preset_num, 0))
        except Exception:
            raw = 0
        return max(0, min(raw, 30))

    def _handle_preset(self, preset_num: int, source: str, audit_id: Optional[str] = None):
        """Handle a requested service view.

        Stream Agent III sd interprets preset numbers as logical service views. The routing
        layer then chooses the best camera and scene for that view.
        """
        if not (1 <= preset_num <= 10):
            return
        if self._stop_pending and getattr(self.cfg, "IGNORE_VIEW_NOTES_DURING_STOP_COUNTDOWN", True):
            self._trace_or_audit(
                f"IGNORE -> stop countdown active; view={preset_num} label={self._label_for_view(preset_num)}",
                audit_id,
            )
            self._post(f"{source}: ignored {self._label_for_view(preset_num)} during stop countdown")
            return
        self._route_view_request(preset_num, source, audit_id=audit_id)

    def _start_stream_flow(self, source: str):
        now = time.time()
        # A pending retry is a continuation of the original Start request, not a second
        # operator click. Without this bypass, a Start pressed while OBS is offline can
        # be swallowed if OBS reconnects inside START_DEBOUNCE_SECONDS.
        pending_retry = bool(getattr(self, "_pending_stream_start", False) and getattr(self.obs, "connected", False))
        if (not pending_retry) and (now - self._last_start_request_ts) < self.cfg.START_DEBOUNCE_SECONDS:
            self._post(f"{source}: start ignored (debounce)")
            return
        self._last_start_request_ts = now
        if pending_retry:
            self._pending_stream_start = False
            self._pending_start_reason = ""
            self._pending_start_not_before = 0.0


        # Mark that the operator/automation has requested streaming at least once this run.
        self._ever_requested_stream = True
        # Operator/automation intent: we WANT to be live (used by auto-recovery).
        self._desired_streaming = True
        # After sending a start request, OBS can take a moment to report streaming=true.
        # Suppress auto-recover retries during this grace window to avoid double-starting.
        grace_s = float(getattr(self.cfg, "AUTO_RECOVER_START_GRACE_SECONDS", 15))
        self._start_grace_until = time.time() + max(0.0, grace_s)
        self._startup_settle_until = self._start_grace_until
        self._unexpected_stop_candidate_since = 0.0
        # Manual start clears any prior stop intent and any recovery pause.
        self._stop_intent = False
        self._stop_intent_set_at = 0.0
        self._recover_hold_until = 0.0
        self._recovering = False
        self._recover_attempts = 0
        self._recover_next_at = 0.0
        self._recover_reason = ""

        if not self.obs.connected:
            self._pending_stream_start = True
            self._pending_start_reason = source
            return

        # -----------------------------
        # Preflight: OBS profile safety (optional)
        # -----------------------------
        if getattr(self.cfg, "OBS_PROFILE_CHECK_ENABLED", False):
            expected_raw = str(getattr(self.cfg, "OBS_EXPECTED_PROFILE_NAME", "") or "")
            expected = expected_raw.strip()
            if expected:
                current, perr = self.obs.get_current_profile_name()
                current_clean = str(current or "").strip()
                if perr:
                    self._post(f"{source}: WARN — could not read OBS profile ({perr})")
                elif current_clean == expected and str(current) != expected:
                    self._post(f"{source}: WARN — OBS profile whitespace differs; current='{current}' expected='{expected}' — treating as match")
                elif current_clean != expected:
                    action = (getattr(self.cfg, "OBS_PROFILE_MISMATCH_ACTION", "block") or "block").lower()
                    msg = f"OBS profile mismatch: current='{current}' expected='{expected}'"

                    if action == "warn":
                        self._post(f"{source}: WARN — {msg}")
                    elif action == "switch":
                        # Do not attempt to switch profiles while OBS is streaming or recording.
                        streaming, recording, serr = self.obs.get_status()
                        if (not serr) and (streaming or recording):
                            self._note_critical(msg + " (cannot auto-switch while streaming/recording)")
                            self._post(f"{source}: ERROR — {msg} (cannot auto-switch while streaming/recording)")
                            self._desired_streaming = False
                            return

                        ok_sw, sw_err = self.obs.set_current_profile_name(expected)
                        if ok_sw:
                            grace = float(getattr(self.cfg, "OBS_PROFILE_SWITCH_GRACE_SECONDS", 2.0))
                            self._post(f"{source}: OBS profile switched to '{expected}' — starting after {grace:.1f}s")
                            self._pending_stream_start = True
                            self._pending_start_reason = source
                            self._pending_start_not_before = time.time() + max(0.5, grace)
                            # Reset debounce so the queued retry isn't ignored.
                            self._last_start_request_ts = 0.0
                            return

                        self._note_critical(msg + f" (switch failed: {sw_err})")
                        self._post(f"{source}: ERROR — {msg} (switch failed: {sw_err})")
                        self._desired_streaming = False
                        return

                    else:
                        # "block" (default)
                        self._note_critical(msg)
                        self._post(f"{source}: ERROR — {msg} (start blocked)")
                        self._desired_streaming = False
                        return

        ok, msg = self.obs.start_stream()
        if ok:
            self._post(f"{source}: {msg}")
            if "already streaming" in str(msg).lower():
                self._trace_camera(f"START -> redundant start suppressed intro ({source})")
                return
            if getattr(self.cfg, "INTRO_SEQUENCE_ENABLED", False):
                self._maybe_start_intro_sequence(source)
            else:
                end_scene = (getattr(self.cfg, "INTRO_END_SCENE_NAME", "") or getattr(self.cfg, "OBS_SCENE_WEST", "West View")).strip()
                if end_scene:
                    self._switch_program_scene(end_scene, source)
                    self._trace_camera(f"START -> intro bypass active, post-start scene '{end_scene}' ({source})")
        else:
            self._pending_stream_start = True
            self._pending_start_reason = source
            self._post(f"{source}: start failed ({msg})")
    # ----------------------------
    # Intro Video Sequence (Option C)
    # ----------------------------
    def _maybe_start_intro_sequence(self, source: str) -> None:
        """Kick off the intro-monitor thread after a successful StartStream."""
        try:
            if not getattr(self.cfg, "INTRO_SEQUENCE_ENABLED", False):
                return
            intro_name = (getattr(self.cfg, "OBS_INTRO_INPUT_NAME", "") or "").strip()
            if not intro_name:
                self._post(f"{source}: INTRO enabled but OBS_INTRO_INPUT_NAME is blank — skipping intro sequence")
                return
            # Prevent overlap if start is hit twice.
            if getattr(self, "_intro_thread", None) and getattr(self._intro_thread, "is_alive", lambda: False)():
                self._post(f"{source}: INTRO already running — new intro suppressed")
                return

            self._intro_cancel.clear()
            self._intro_thread = threading.Thread(
                target=self._run_intro_sequence_thread,
                args=(intro_name, source),
                daemon=True,
                name="IntroSequence"
            )
            self._intro_thread.start()
        except Exception as e:
            self._post(f"{source}: INTRO thread start error: {e}")

    def _intro_set_cut_transition_if_configured(self, source: str) -> None:
        """Use a hard Cut for the intro handoff without forcing all later service routing to Cut."""
        if not (getattr(self.cfg, "INTRO_FORCE_CUT_TRANSITION", True) or getattr(self.cfg, "FORCE_CUT_TRANSITION", False)):
            return
        try:
            ok_cut, cut_msg = self.obs.set_current_scene_transition_name(getattr(self.cfg, "OBS_CUT_TRANSITION_NAME", "Cut"))
            if not ok_cut:
                self._post(f"{source}: INTRO transition WARN ({cut_msg})")
        except Exception as e:
            self._post(f"{source}: INTRO transition WARN ({e})")

    def _wait_intro_sleep(self, seconds: float) -> bool:
        """Sleep in small chunks; return False if the intro was cancelled."""
        end_at = time.time() + max(0.0, float(seconds or 0.0))
        while time.time() < end_at:
            if self._intro_cancel.is_set():
                return False
            time.sleep(min(0.05, max(0.01, end_at - time.time())))
        return not self._intro_cancel.is_set()

    def _ensure_intro_scene_live(self, preferred_scene: str, source: str) -> Tuple[bool, str]:
        """Switch to the intro scene and verify Program really reports that scene before media restart."""
        preferred_scene = (preferred_scene or "").strip()
        if not preferred_scene:
            return True, "no intro scene configured"

        try:
            retries = max(1, int(getattr(self.cfg, "INTRO_SCENE_SWITCH_RETRIES", 8) or 8))
        except Exception:
            retries = 8
        try:
            retry_s = max(0.05, float(getattr(self.cfg, "INTRO_SCENE_SWITCH_RETRY_SECONDS", 0.35) or 0.35))
        except Exception:
            retry_s = 0.35

        last_msg = ""
        for attempt in range(1, retries + 1):
            if self._intro_cancel.is_set():
                return False, "cancelled"

            cur_scene, cur_err = self.obs._get_current_program_scene_name()
            if cur_scene == preferred_scene:
                self._post(f"{source}: INTRO scene '{preferred_scene}' confirmed live")
                return True, "confirmed"
            if cur_err:
                last_msg = f"current scene read failed: {cur_err}"
                self._post(f"{source}: INTRO current-scene WARN ({cur_err})")

            self._intro_set_cut_transition_if_configured(source)
            ok_intro_scene, intro_scene_msg = self.obs.set_current_program_scene_name(preferred_scene)
            if not ok_intro_scene:
                last_msg = intro_scene_msg or "scene switch request failed"
                self._post(f"{source}: INTRO scene switch attempt {attempt}/{retries} WARN ({last_msg})")
            else:
                self._post(f"{source}: INTRO scene switch attempt {attempt}/{retries} sent to '{preferred_scene}'")

            if not self._wait_intro_sleep(retry_s):
                return False, "cancelled"

            verify_scene, verify_err = self.obs._get_current_program_scene_name()
            if verify_scene == preferred_scene:
                self._post(f"{source}: INTRO scene '{preferred_scene}' confirmed live")
                return True, "confirmed"

            extra = f"verify='{verify_scene}'" if verify_scene else "verify unavailable"
            if verify_err:
                extra += f"; verifyWarn={verify_err}"
            last_msg = f"requested '{preferred_scene}' but {extra}"
            self._post(f"{source}: INTRO scene verify attempt {attempt}/{retries} not live ({last_msg})")

        return False, last_msg or f"could not confirm '{preferred_scene}' live"

    def _run_intro_sequence_thread(self, intro_name: str, source: str) -> None:
        """Runs in background: switch to the intro scene, restart intro playback, then cut to the end scene."""
        try:
            if self._intro_cancel.is_set():
                self._post(f"{source}: INTRO cancelled before start")
                return
            poll_s = float(getattr(self.cfg, "INTRO_POLL_SECONDS", 0.5))
            max_s = int(getattr(self.cfg, "INTRO_MAX_SECONDS", 300))
            restart_grace_s = float(getattr(self.cfg, "INTRO_RESTART_GRACE_SECONDS", 1.0))
            ended_guard_s = float(getattr(self.cfg, "INTRO_ENDED_GUARD_SECONDS", 2.0))
            preferred_scene = (getattr(self.cfg, "OBS_INTRO_SCENE_NAME", "") or getattr(self.cfg, "OBS_SCENE_INTRO", "")).strip()
            disable_on_end = bool(getattr(self.cfg, "INTRO_DISABLE_ON_END", True))

            if preferred_scene:
                scene_ok, scene_msg = self._ensure_intro_scene_live(preferred_scene, source)
                if not scene_ok:
                    msg = f"INTRO scene '{preferred_scene}' could not be confirmed live ({scene_msg})"
                    self._note_critical(msg)
                    self._post(f"{source}: ERROR — {msg}; Intro_Video restart skipped")
                    if bool(getattr(self.cfg, "INTRO_ABORT_IF_SCENE_NOT_LIVE", True)):
                        end_scene = (getattr(self.cfg, "INTRO_END_SCENE_NAME", "") or getattr(self.cfg, "OBS_SCENE_WEST", "West View")).strip()
                        if end_scene:
                            self._intro_set_cut_transition_if_configured(source)
                            ok_skip, skip_msg = self.obs.set_current_program_scene_name(end_scene)
                            if ok_skip:
                                self._post(f"{source}: INTRO skipped — cut to '{end_scene}'")
                            else:
                                self._post(f"{source}: INTRO skipped but scene switch failed for '{end_scene}' ({skip_msg})")
                        return
                    self._post(f"{source}: INTRO continuing despite scene warning because INTRO_ABORT_IF_SCENE_NOT_LIVE=False")

                try:
                    sid, sid_err = self.obs._get_scene_item_id_for_source(preferred_scene, intro_name)
                    if sid is None:
                        self._post(f"{source}: INTRO source placement WARN — '{intro_name}' not found directly in scene '{preferred_scene}' ({sid_err or 'no scene item id returned'})")
                    else:
                        self._post(f"{source}: INTRO source placement OK — '{intro_name}' is in '{preferred_scene}'")
                except Exception as e:
                    self._post(f"{source}: INTRO source placement WARN ({e})")

            ok_restart, rmsg = self.obs.trigger_media_restart(intro_name)
            if ok_restart:
                self._post(f"{source}: INTRO '{intro_name}' restart OK")
            else:
                self._post(f"{source}: INTRO '{intro_name}' restart WARN ({rmsg})")

            if self._intro_cancel.is_set():
                self._post(f"{source}: INTRO cancelled after restart")
                return

            if restart_grace_s > 0:
                time.sleep(max(0.0, restart_grace_s))
            if self._intro_cancel.is_set():
                self._post(f"{source}: INTRO cancelled during restart grace")
                return

            t0 = time.time()
            last_state: Optional[str] = None
            last_err_bucket: Optional[int] = None
            seen_live_state = False
            ignored_ended_once = False
            live_states = {
                "OBS_MEDIA_STATE_OPENING",
                "OBS_MEDIA_STATE_BUFFERING",
                "OBS_MEDIA_STATE_PLAYING",
                "OBS_MEDIA_STATE_PAUSED",
                "OPENING",
                "BUFFERING",
                "PLAYING",
                "PAUSED",
            }

            while True:
                if self._intro_cancel.is_set():
                    self._post(f"{source}: INTRO cancelled")
                    return
                elapsed = time.time() - t0
                if elapsed >= max_s:
                    self._post(f"{source}: INTRO timeout after {max_s}s — switching to post-intro scene")
                    break

                state, err = self.obs.get_media_state(intro_name)
                if err:
                    bucket = int(elapsed // 5)
                    if bucket != last_err_bucket:
                        self._post(f"{source}: INTRO status WARN ({err})")
                        last_err_bucket = bucket
                if state and state != last_state:
                    self._post(f"{source}: INTRO state = {state}")
                    last_state = state

                if state in live_states:
                    seen_live_state = True

                if state in ("OBS_MEDIA_STATE_ENDED", "ENDED"):
                    if (not seen_live_state) and elapsed < max(ended_guard_s, poll_s):
                        if not ignored_ended_once:
                            self._post(f"{source}: INTRO stale ENDED ignored during restart guard")
                            ignored_ended_once = True
                        time.sleep(max(0.1, poll_s))
                        continue
                    break

                time.sleep(max(0.1, poll_s))

            end_scene = (getattr(self.cfg, "INTRO_END_SCENE_NAME", "") or getattr(self.cfg, "OBS_SCENE_WEST", "West View")).strip()
            post_hold_s = max(0.0, float(getattr(self.cfg, "INTRO_POST_HOLD_SECONDS", 0.25)))

            if disable_on_end:
                ok_dis, dmsg = self.obs.disable_source_in_scene_auto(intro_name, preferred_scene=preferred_scene)
                if ok_dis:
                    self._post(f"{source}: INTRO done — {dmsg}")
                else:
                    self._post(f"{source}: INTRO done but could not disable '{intro_name}' ({dmsg})")
                return

            if self._intro_cancel.is_set():
                self._post(f"{source}: INTRO cancelled before post-intro scene")
                return

            if post_hold_s > 0.0:
                self._post(f"{source}: INTRO ended — holding {post_hold_s:.3f}s before post-intro cut")
                hold_until = time.time() + post_hold_s
                while time.time() < hold_until:
                    if self._intro_cancel.is_set():
                        self._post(f"{source}: INTRO cancelled during post-intro hold")
                        return
                    time.sleep(min(0.05, max(0.01, hold_until - time.time())))

            self._intro_set_cut_transition_if_configured(source)
            ok_sc, smsg = self.obs.set_current_program_scene_name(end_scene)
            if ok_sc:
                self._post(f"{source}: INTRO done — cut to '{end_scene}'")
            else:
                self._post(f"{source}: INTRO done but scene switch failed for '{end_scene}' ({smsg})")

        except Exception as e:
            self._post(f"{source}: INTRO error: {e}")


    def _request_stop(self, source: str):
        # Operator/automation intent: we do NOT want to be live.
        self._cancel_intro_sequence(source, log=True)
        # A stop request should also clear any pending delayed scene cut so an older
        # routed view cannot fire during the stop countdown or after stop completes.
        self._cancel_pending_scene_switch(source)
        self._desired_streaming = False
        self._startup_settle_until = 0.0
        self._unexpected_stop_candidate_since = 0.0
        # Mark stop intent so the next transition STREAM ON -> OFF is not treated as "unexpected".
        self._stop_intent = True
        self._stop_intent_set_at = time.time()

        # Cancel any in-progress auto-recovery attempts.
        self._recovering = False
        self._recover_attempts = 0
        self._recover_next_at = 0.0
        self._recover_reason = ""

        self._last_stop_was_midi = (source == "MIDI")

        self._stop_pending = True
        self._stop_at = time.time() + self.cfg.STOP_DELAY_SECONDS
        self._post(f"{source}: stop in {self.cfg.STOP_DELAY_SECONDS}s")

    def _stop_tick(self):
        if not self._stop_pending:
            return
        rem = int(self._stop_at - time.time())
        if rem > 0:
            return
        self._stop_pending = False
        if self.obs.connected:
            ok, msg = self.obs.stop_stream()
            self._post(f"STOP: {msg}" if ok else f"STOP failed ({msg})")
            try:
                intro_name = (getattr(self.cfg, "OBS_INTRO_INPUT_NAME", "") or "").strip()
                if intro_name:
                    ok_intro_stop, intro_stop_msg = self.obs.trigger_media_stop(intro_name)
                    if ok_intro_stop:
                        self._post(f"STOP: intro media '{intro_name}' reset")
                    else:
                        self._post(f"STOP: intro media reset WARN ({intro_stop_msg})")
            except Exception as e:
                self._post(f"STOP: intro media reset WARN ({e})")
        self.stream_ended_at = time.time()  # Trigger "STREAM ENDED" banner
        # v7.14: Optional service-end master sequence (triggered ONLY by MIDI stop)
        if (self._last_stop_was_midi and
                getattr(self.cfg, 'MIDI_STOP_TRIGGERS_FULL_SEQUENCE', False) and
                getattr(self.cfg, 'SERVICE_END_SEQUENCE_ENABLED', False) and
                not self._service_end_running):
            self._service_end_running = True
            try:
                asyncio.create_task(self.run_service_end_sequence())
            except Exception as e:
                self._post(f"SERVICE-END: failed to start task: {e}")
                self._service_end_running = False

        self._intro_cancel.clear()
        self._last_stop_was_midi = False


    async def run_service_end_sequence(self):
        """
        v7.14 service-end master sequence.
        Triggered only when:
          - SERVICE_END_SEQUENCE_ENABLED is True
          - MIDI_STOP_TRIGGERS_FULL_SEQUENCE is True
          - last stop request source was MIDI
        """
        try:
            self._post("SERVICE-END: Sequence started")

            # Flush any open logs before copying.
            try:
                if self._run_log_fp:
                    self._run_log_fp.flush()
            except Exception:
                pass
            try:
                if self._session_log_fp:
                    self._session_log_fp.flush()
            except Exception:
                pass

            # Wait for OBS to fully stop (stream + record), then cooldown.
            deadline = time.time() + 300  # 5 minutes max wait
            last_report = 0.0
            while time.time() < deadline:
                streaming, recording, err = self.obs.get_status()
                if not streaming and not recording:
                    break
                if time.time() - last_report > 10:
                    self._post(f"SERVICE-END: Waiting for OBS stop... stream={'ON' if streaming else 'off'} rec={'ON' if recording else 'off'}")
                    if err:
                        self._post(f"SERVICE-END: OBS status note: {err}")
                    last_report = time.time()
                await asyncio.sleep(1.0)

            wait_s = int(getattr(self.cfg, "SERVICE_END_POST_STOP_WAIT_SECONDS", 0) or 0)
            if wait_s > 0:
                self._post(f"SERVICE-END: Cooldown {wait_s}s")
                await asyncio.sleep(wait_s)

            # Create destination folder on USB/external drive.
            usb_root = getattr(self.cfg, "SERVICE_END_USB_ROOT", "") or ""
            dest_dir = ""
            if usb_root and os.path.isdir(usb_root):
                now_dt = now_in_cfg_tz(self.cfg)
                stamp = now_dt.strftime("%Y-%m-%d_%H%M%S")
                dest_dir = os.path.join(usb_root, f"service_end_{stamp}")
                try:
                    os.makedirs(dest_dir, exist_ok=True)
                    self._post(f"SERVICE-END: Dest folder: {dest_dir}")
                except Exception as e:
                    self._post(f"SERVICE-END: Could not create dest folder: {e}")
                    dest_dir = ""
            else:
                if usb_root:
                    self._post(f"SERVICE-END: USB root missing/invalid: {usb_root}")
                else:
                    self._post("SERVICE-END: USB root not set; skipping copy steps")

            # Copy logs (current + optionally previous).
            def _copy_file(src_path: str):
                try:
                    if not dest_dir:
                        return
                    if src_path and os.path.isfile(src_path):
                        shutil.copy2(src_path, os.path.join(dest_dir, os.path.basename(src_path)))
                        self._post(f"SERVICE-END: Copied {os.path.basename(src_path)}")
                except Exception as e:
                    self._post(f"SERVICE-END: Copy failed for {src_path}: {e}")

            if dest_dir:
                base_dir = self._log_base_dir()
                prefix = getattr(self.cfg, "LOG_RUN_FILE_PREFIX", "stream_agent")
                want_prev = bool(getattr(self.cfg, "SERVICE_END_COPY_PREVIOUS_LOGS", True))

                run_logs = sorted(glob.glob(os.path.join(base_dir, f"{prefix}_run_*.log")),
                                  key=lambda p: os.path.getmtime(p), reverse=True)
                sess_logs = sorted(glob.glob(os.path.join(base_dir, f"{prefix}_session_*.log")),
                                   key=lambda p: os.path.getmtime(p), reverse=True)

                for p in run_logs[: (2 if want_prev else 1)]:
                    _copy_file(p)
                for p in sess_logs[: (2 if want_prev else 1)]:
                    _copy_file(p)

            # Copy today's MP4 (most recent only).
            if dest_dir and getattr(self.cfg, "SERVICE_END_COPY_TODAYS_MP4", True):
                rec_root = getattr(self.cfg, "OBS_RECORDING_PATH", "") or ""
                if rec_root and os.path.isdir(rec_root):
                    tz = get_tz(self.cfg)
                    today = now_in_cfg_tz(self.cfg).date()

                    def file_date_matches(p: str) -> bool:
                        try:
                            ts = os.path.getmtime(p)
                            if tz is not None:
                                d = dt.datetime.fromtimestamp(ts, dt.timezone.utc).astimezone(tz).date()
                            else:
                                d = dt.datetime.fromtimestamp(ts).date()
                            return d == today
                        except Exception:
                            return False

                    mp4s = [p for p in glob.glob(os.path.join(rec_root, "*.mp4")) if file_date_matches(p)]
                    if mp4s:
                        mp4 = max(mp4s, key=lambda p: os.path.getmtime(p))
                        try:
                            shutil.copy2(mp4, os.path.join(dest_dir, os.path.basename(mp4)))
                            self._post(f"SERVICE-END: Copied recording {os.path.basename(mp4)}")
                        except Exception as e:
                            self._post(f"SERVICE-END: Recording copy failed: {e}")
                    else:
                        self._post("SERVICE-END: No MP4 recording found for today")
                else:
                    if rec_root:
                        self._post(f"SERVICE-END: OBS_RECORDING_PATH missing/invalid: {rec_root}")
                    else:
                        self._post("SERVICE-END: OBS_RECORDING_PATH not set; skipping recording copy")

            # HOME_TEST_MODE safety: skip closing apps + shutdown.
            if self.cfg.HOME_TEST_MODE:
                self._post("SERVICE-END: HOME_TEST_MODE — skipping app closes and shutdown")
                return

            # Close apps (optional), using psutil if available.
            def close_process(name: str):
                if not name:
                    return
                if psutil is None:
                    self._post(f"SERVICE-END: psutil missing — cannot close {name}")
                    return
                target = _safe_lower(name)
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        pn = proc.info.get('name') or ""
                        if _safe_lower(pn) == target:
                            proc.terminate()
                            try:
                                proc.wait(timeout=8)
                                self._post(f"SERVICE-END: Terminated {name}")
                            except Exception:
                                proc.kill()
                                self._post(f"SERVICE-END: Force-killed {name}")
                            return
                    except Exception:
                        continue
                self._post(f"SERVICE-END: Process not found: {name}")

            if getattr(self.cfg, "SERVICE_END_CLOSE_PROCLAIM", True):
                close_process(getattr(self.cfg, "PROCLAIM_PROCESS_NAME", "Proclaim.exe"))
            if getattr(self.cfg, "SERVICE_END_CLOSE_MASTER_FADER", True):
                close_process(getattr(self.cfg, "MASTER_FADER_PROCESS_NAME", "MasterFader.exe"))
            if getattr(self.cfg, "SERVICE_END_CLOSE_OBS", True):
                close_process(getattr(self.cfg, "SERVICE_END_OBS_PROCESS_NAME", "obs64.exe"))

            # Optional Windows shutdown.
            if getattr(self.cfg, "SERVICE_END_WINDOWS_SHUTDOWN", False):
                delay = int(getattr(self.cfg, "SERVICE_END_SHUTDOWN_DELAY_SECONDS", 60) or 60)

                # UI-thread safe popup
                self._ui_action(lambda: messagebox.showinfo(
                    "Service Ended",
                    f"Service-end tasks complete.\nShutting down in {delay} seconds — run 'shutdown /a' to abort."
                ))

                try:
                    subprocess.call(["shutdown", "/s", "/t", str(delay)])
                    self._post(f"SERVICE-END: Shutdown initiated ({delay}s abort window)")
                except Exception as e:
                    self._post(f"SERVICE-END: Shutdown failed: {e}")
            else:
                self._post("SERVICE-END: Shutdown not enabled (SERVICE_END_WINDOWS_SHUTDOWN=False)")

        except Exception as e:
            self._post(f"SERVICE-END: Sequence error: {e}")
        finally:
            self._service_end_running = False

    def _toggle_record(self, source: str):
        if not self.obs.connected:
            self._post(f"{source}: OBS not connected")
            return
        ok, msg = self.obs.toggle_record()
        self._post(f"{source}: {msg}")

    def _timer_target_today(self) -> Optional[dt.datetime]:
        if not self.cfg.USE_TIMER_START:
            return None
        now = now_in_cfg_tz(self.cfg)
        if now.weekday() != self.cfg.TIMER_WEEKDAY:
            return None
        try:
            hh, mm = parse_hhmm(self.cfg.TIMER_START_HHMM)
        except Exception:
            return None
        return now.replace(hour=hh, minute=mm, second=0, microsecond=0)

    def _timer_state_path(self) -> str:
        base = self.cfg.TIMER_STATE_FILE
        if os.path.isabs(base):
            return base
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), base)

    def _load_timer_state(self):
        if not self.cfg.TIMER_PERSIST_STATE:
            return
        try:
            with open(self._timer_state_path(), "r") as f:
                data = json.load(f)
            date_s = data.get("date")
            status = data.get("status")
            hhmm = data.get("hhmm", self.cfg.TIMER_START_HHMM)
            if date_s and status in ("fired", "missed"):
                today = now_in_cfg_tz(self.cfg).date()
                if date_s == today.isoformat():
                    self._timer_done_today_date = today
                    self._timer_done_status = status
                    self._timer_done_time_hhmm = hhmm
        except Exception:
            pass

    def _save_timer_state(self, status: str, hhmm: str):
        if not self.cfg.TIMER_PERSIST_STATE:
            return
        try:
            today = now_in_cfg_tz(self.cfg).date()
            with open(self._timer_state_path(), "w") as f:
                json.dump({"date": today.isoformat(), "status": status, "hhmm": hhmm}, f)
        except Exception:
            pass

    def _timer_tick(self):
        if not self.cfg.USE_TIMER_START:
            self._set_ui_state(timer_text="Timer: disabled")
            return

        try:
            parse_hhmm(self.cfg.TIMER_START_HHMM)
        except Exception as e:
            self._set_ui_state(timer_text=f"Timer: invalid time '{self.cfg.TIMER_START_HHMM}' — {e}")
            return

        now_dt = now_in_cfg_tz(self.cfg)
        if now_dt.weekday() != self.cfg.TIMER_WEEKDAY:
            self._set_ui_state(timer_text=f"Next auto-start: Sunday {self.cfg.TIMER_START_HHMM}")
            return

        target = self._timer_target_today()
        if target is None:
            self._set_ui_state(timer_text=f"Timer active on Sundays at {self.cfg.TIMER_START_HHMM}")
            return

        today = now_dt.date()
        if self._timer_done_today_date == today:
            self._set_ui_state(timer_text=f"Timer: {'fired' if self._timer_done_status == 'fired' else 'missed'} today")
            return

        delta = int((target - now_dt).total_seconds())
        if delta > 0:
            self._set_ui_state(timer_text=f"Auto-start in T-{fmt_hms(delta)}")
            return

        past = int(-delta)
        if past > self.cfg.TIMER_FIRE_GRACE_MINUTES * 60:
            self._timer_done_today_date = today
            self._timer_done_status = "missed"
            self._save_timer_state("missed", self.cfg.TIMER_START_HHMM)
            self._set_ui_state(timer_text="Timer: missed today — manual start needed")
            return

        self._timer_done_today_date = today
        self._timer_done_status = "fired"
        self._save_timer_state("fired", self.cfg.TIMER_START_HHMM)

        streaming, _, _ = self.obs.get_status()
        if streaming:
            self._set_ui_state(timer_text=f"Timer fired ({self.cfg.TIMER_START_HHMM})")
            return

        self._set_ui_state(timer_text="Timer: starting stream now")
        self._start_stream_flow("TIMER")


    # -----------------------------
    # Web HUD (HTTP + WebSocket)
    # -----------------------------

    # -----------------------------
    # Preflight Check / GO-NO-GO Report
    # -----------------------------
    def _health_make_check(self, group: str, key: str, label: str, status: str, detail: str = "", required: bool = False) -> dict:
        status = str(status or "info").lower().strip()
        if status not in ("pass", "warn", "fail", "info"):
            status = "info"
        return {
            "group": str(group or "General"),
            "key": str(key or label or "check"),
            "label": str(label or key or "Check"),
            "status": status,
            "detail": str(detail or ""),
            "required": bool(required),
        }

    def _axis_probe_camera(self, camera_key: str, timeout_s: float = 1.5) -> Tuple[bool, str]:
        """Read-only Axis reachability check. Does not move the camera."""
        camera_key = str(camera_key or "").strip().lower()
        if camera_key not in ("west", "east"):
            return False, f"unknown camera '{camera_key}'"
        try:
            base = self._axis_base_url_for_camera(camera_key).rstrip("/")
            url = base + "/axis-cgi/param.cgi?action=list&group=Properties.System.SerialNumber"
            username = str(getattr(self.cfg, "AXIS_USERNAME", "") or "")
            password = str(getattr(self.cfg, "AXIS_PASSWORD", "") or "")
            timeout_s = max(0.5, float(timeout_s or 1.5))
            last_err = None

            if username or password:
                try:
                    pw_mgr = urlrequest.HTTPPasswordMgrWithDefaultRealm()
                    pw_mgr.add_password(None, base + "/", username, password)
                    opener = urlrequest.build_opener(
                        urlrequest.HTTPDigestAuthHandler(pw_mgr),
                        urlrequest.HTTPBasicAuthHandler(pw_mgr),
                    )
                    with opener.open(url, timeout=timeout_s) as resp:
                        code = int(getattr(resp, "status", None) or getattr(resp, "code", None) or 0)
                        if 200 <= code < 300:
                            return True, f"HTTP {code}"
                        return False, f"HTTP {code}"
                except Exception as e:
                    last_err = e

            try:
                req = urlrequest.Request(url)
                if username or password:
                    token = base64.b64encode((f"{username}:{password}").encode("utf-8")).decode("ascii")
                    req.add_header("Authorization", "Basic " + token)
                with urlrequest.urlopen(req, timeout=timeout_s) as resp:
                    code = int(getattr(resp, "status", None) or getattr(resp, "code", None) or 0)
                    if 200 <= code < 300:
                        return True, f"HTTP {code}"
                    return False, f"HTTP {code}"
            except Exception as e:
                if last_err is not None:
                    return False, f"{last_err}; fallback auth error: {e}"
                return False, str(e)
        except Exception as e:
            return False, str(e)

    def _health_log_folder_writable(self) -> Tuple[bool, str]:
        if not bool(getattr(self.cfg, "LOG_TO_FILE_ENABLED", False)):
            return True, "file logging disabled"
        try:
            base_dir = self._log_base_dir()
            os.makedirs(base_dir, exist_ok=True)
            test_path = os.path.join(base_dir, f".stream_agent_health_{os.getpid()}_{int(time.time())}.tmp")
            with open(test_path, "w", encoding="utf-8") as f:
                f.write("ok\n")
            try:
                os.remove(test_path)
            except Exception:
                pass
            return True, base_dir
        except Exception as e:
            return False, str(e)

    def _health_timer_sanity(self) -> Tuple[bool, str, bool]:
        """Return (ok, detail, warning_only)."""
        if not bool(getattr(self.cfg, "USE_TIMER_START", False)):
            return True, "timer auto-start disabled", False
        try:
            hh, mm = parse_hhmm(str(getattr(self.cfg, "TIMER_START_HHMM", "") or ""))
            weekday = int(getattr(self.cfg, "TIMER_WEEKDAY", 6) or 0)
            if not (0 <= weekday <= 6):
                return False, f"TIMER_WEEKDAY={weekday} is outside 0..6", False
            tz = get_tz(self.cfg)
            if tz is None:
                mode = str(getattr(self.cfg, "TZ_FALLBACK_MODE", "local") or "local")
                return True, f"{weekday=} {hh:02d}:{mm:02d}; timezone fallback mode '{mode}' in use", True
            return True, f"weekday={weekday} at {hh:02d}:{mm:02d} {getattr(self.cfg, 'TIMEZONE', '')}", False
        except Exception as e:
            return False, str(e), False

    def _health_report_snapshot(self, probe_cameras: bool = True) -> dict:
        """Build a read-only confidence report for preflight and live operation."""
        checks: List[dict] = []
        now_ts = time.time()
        streaming = False
        recording = False
        obs_err = ""
        obs_connected = bool(getattr(self.obs, "connected", False))

        def add(group: str, key: str, label: str, status: str, detail: str = "", required: bool = False):
            checks.append(self._health_make_check(group, key, label, status, detail, required))

        add("App", "web", "SA III Web HUD", "pass", "web server answered this request", True)
        add("App", "cmd_queue", "Command queue", "pass" if self._cmd_queue is not None else "warn",
            "worker command queue is available" if self._cmd_queue is not None else "command queue not initialized yet", False)

        # OBS connection and status
        if not obs_connected:
            add("OBS", "obs_connected", "OBS WebSocket connected", "fail", getattr(self.obs, "last_error", "") or "OBS offline", True)
        else:
            add("OBS", "obs_connected", "OBS WebSocket connected", "pass", f"{getattr(self.cfg, 'OBS_HOST', '')}:{getattr(self.cfg, 'OBS_PORT', '')}", True)
            try:
                streaming, recording, obs_err = self.obs.get_status()
                if obs_err:
                    add("OBS", "obs_status", "OBS stream/record status readable", "warn", obs_err, False)
                else:
                    add("OBS", "obs_status", "OBS stream/record status readable", "pass",
                        f"stream={'ON' if streaming else 'off'}, record={'ON' if recording else 'off'}", False)
            except Exception as e:
                obs_err = str(e)
                add("OBS", "obs_status", "OBS stream/record status readable", "warn", obs_err, False)

        desired = bool(getattr(self, "_desired_streaming", False))
        if streaming:
            add("Streaming", "stream_active", "OBS stream active", "pass", "OBS reports streaming ON", True)
        elif desired:
            add("Streaming", "stream_active", "OBS stream active", "fail", "SA III expects live streaming, but OBS reports stream off", True)
        else:
            add("Streaming", "stream_active", "OBS stream active", "info", "not currently streaming", False)

        if bool(getattr(self, "_recovering", False)):
            add("Streaming", "recovery", "Auto-recovery state", "warn", f"recovering: {getattr(self, '_recover_reason', '')}", False)
        elif float(getattr(self, "_recover_hold_until", 0.0) or 0.0) > now_ts:
            remaining = int(float(getattr(self, "_recover_hold_until", 0.0) or 0.0) - now_ts)
            add("Streaming", "recovery", "Auto-recovery state", "warn", f"recovery paused for {remaining}s", False)
        else:
            add("Streaming", "recovery", "Auto-recovery state", "pass", "not recovering", False)

        # OBS profile, scenes, and inputs
        scenes: List[str] = []
        inputs: List[str] = []
        if obs_connected:
            try:
                current_profile, perr = self.obs.get_current_profile_name()
                expected = str(getattr(self.cfg, "OBS_EXPECTED_PROFILE_NAME", "NHLC") or "NHLC")
                if perr:
                    add("OBS", "profile", "OBS profile", "warn", perr, bool(getattr(self.cfg, "OBS_PROFILE_CHECK_ENABLED", True)))
                elif current_profile == expected:
                    add("OBS", "profile", "OBS profile", "pass", f"active profile '{current_profile}'", True)
                elif current_profile.strip() == expected.strip():
                    add("OBS", "profile", "OBS profile", "warn",
                        f"active '{current_profile}' matches expected '{expected}' only after trimming whitespace", False)
                else:
                    add("OBS", "profile", "OBS profile", "fail", f"active '{current_profile}' expected '{expected}'", True)
            except Exception as e:
                add("OBS", "profile", "OBS profile", "warn", str(e), bool(getattr(self.cfg, "OBS_PROFILE_CHECK_ENABLED", True)))

            try:
                cur_scene, cur_err = self.obs._get_current_program_scene_name()
                add("OBS", "current_scene", "Current OBS scene readable",
                    "pass" if cur_scene and not cur_err else "warn",
                    cur_scene or cur_err or "unavailable", False)
            except Exception as e:
                add("OBS", "current_scene", "Current OBS scene readable", "warn", str(e), False)

            try:
                scenes, serr = self.obs._get_scene_names()
                required_scenes = [
                    str(getattr(self.cfg, "OBS_SCENE_INTRO", "Introduction") or "Introduction"),
                    str(getattr(self.cfg, "OBS_SCENE_WEST", "West View") or "West View"),
                    str(getattr(self.cfg, "OBS_SCENE_EAST", "East View") or "East View"),
                ]
                missing = [s for s in required_scenes if s and s not in scenes]
                if serr:
                    add("OBS", "required_scenes", "Required OBS scenes", "warn", serr, True)
                elif missing:
                    add("OBS", "required_scenes", "Required OBS scenes", "fail", "missing: " + ", ".join(missing), True)
                else:
                    add("OBS", "required_scenes", "Required OBS scenes", "pass", ", ".join(required_scenes), True)
            except Exception as e:
                add("OBS", "required_scenes", "Required OBS scenes", "fail", str(e), True)

            try:
                inputs, ierr = self.obs.get_input_names()
                required_inputs = [
                    str(getattr(self.cfg, "OBS_INTRO_INPUT_NAME", "Intro_Video") or "Intro_Video"),
                    str(getattr(self.cfg, "OBS_VIDEO_INPUT_WEST", "West_axis") or "West_axis"),
                    str(getattr(self.cfg, "OBS_VIDEO_INPUT_EAST", "East_axis") or "East_axis"),
                ]
                missing = [s for s in required_inputs if s and s not in inputs]
                if ierr:
                    add("OBS", "required_inputs", "Required OBS inputs", "warn", ierr, True)
                elif missing:
                    add("OBS", "required_inputs", "Required OBS inputs", "fail", "missing: " + ", ".join(missing), True)
                else:
                    add("OBS", "required_inputs", "Required OBS inputs", "pass", ", ".join(required_inputs), True)

                # Proclaim overlay source is very helpful, but not always required for every test.
                if "slides" in inputs:
                    add("OBS", "slides_source", "Proclaim overlay source", "pass", "OBS input 'slides' found", False)
                else:
                    add("OBS", "slides_source", "Proclaim overlay source", "warn", "OBS input 'slides' not found", False)
            except Exception as e:
                add("OBS", "required_inputs", "Required OBS inputs", "fail", str(e), True)

        # Audio path
        mode = self._audio_mode()
        if mode == "axis_embedded":
            add("Audio", "audio_mode", "Audio mode", "pass", "axis_embedded", True)
        else:
            add("Audio", "audio_mode", "Audio mode", "fail", f"{mode}; normal church mode should be axis_embedded", True)

        targets = self._audio_inputs()
        if not targets:
            add("Audio", "audio_targets", "Audio targets configured", "fail", "no audio targets configured", True)
        elif inputs:
            missing_audio = [t for t in targets if t not in inputs]
            if missing_audio:
                add("Audio", "audio_targets", "Audio targets found in OBS", "fail", "missing: " + ", ".join(missing_audio), True)
            else:
                add("Audio", "audio_targets", "Audio targets found in OBS", "pass", ", ".join(targets), True)
        else:
            add("Audio", "audio_targets", "Audio targets found in OBS", "warn", ", ".join(targets) + " (OBS input list unavailable)", True)

        last_meter = float(getattr(self, "_audio_meter_last_update_ts", 0.0) or 0.0)
        meter_age = (now_ts - last_meter) if last_meter > 0 else None
        stale_after = float(getattr(self.cfg, "AUDIO_METER_STALE_AFTER_SECONDS", 5.0) or 5.0)
        if meter_age is None:
            add("Audio", "audio_meter", "OBS audio meter events", "warn", "no meter event received yet", False)
        elif meter_age <= max(stale_after, 10.0):
            add("Audio", "audio_meter", "OBS audio meter events", "pass",
                f"fresh {meter_age:.1f}s ago; HUD meter {float(getattr(self, 'audio_master_meter_db', -60.0)):.1f} dBFS", False)
        else:
            add("Audio", "audio_meter", "OBS audio meter events", "warn", f"stale {meter_age:.1f}s old", False)

        # Cameras
        for cam_key, label, enabled_key in (
            ("west", "West Axis camera", "WEST_CAMERA_ENABLED"),
            ("east", "East Axis camera", "EAST_CAMERA_ENABLED"),
        ):
            if not bool(getattr(self.cfg, enabled_key, True)):
                add("Cameras", f"{cam_key}_camera", label, "warn", "disabled in config", False)
                continue
            if probe_cameras:
                ok, detail = self._axis_probe_camera(cam_key, timeout_s=1.5)
                add("Cameras", f"{cam_key}_camera", label, "pass" if ok else "fail", detail, True)
            else:
                add("Cameras", f"{cam_key}_camera", label, "info", "not probed in this report", False)

        # Director preview freshness
        if self._director_preview_enabled():
            for cam_key, label in (("west", "West preview"), ("east", "East preview")):
                st = self._director_preview_public_state(cam_key)
                if bool(st.get("ok")):
                    add("Director", f"{cam_key}_preview", label, "pass", f"fresh, age {int(st.get('age_ms', 0))} ms", False)
                else:
                    err = str(st.get("error", "") or "")
                    add("Director", f"{cam_key}_preview", label, "warn",
                        f"stale/unavailable, age {int(st.get('age_ms', 0))} ms" + (f"; {err}" if err else ""), False)
        else:
            add("Director", "previews", "Director previews", "info", "disabled in config", False)

        # MIDI
        if self.midi.is_connected():
            add("MIDI", "midi_connected", "Proclaim MIDI connection", "pass", self.midi.connected_name or "connected", False)
        else:
            add("MIDI", "midi_connected", "Proclaim MIDI connection", "warn", self.midi.last_error or "not connected", False)

        # Logging
        ok_log, log_detail = self._health_log_folder_writable()
        add("Logging", "log_folder", "Log folder writable", "pass" if ok_log else "fail", log_detail, True)

        # Timer
        ok_timer, timer_detail, timer_warn_only = self._health_timer_sanity()
        add("Timer", "timer_sanity", "Timer sanity", "pass" if ok_timer and not timer_warn_only else ("warn" if ok_timer else "fail"),
            timer_detail, not timer_warn_only)

        # Recent critical fault
        crit_msg = str(getattr(self, "_last_critical_msg", "") or "")
        crit_ts = str(getattr(self, "_last_critical_ts", "") or "")
        if crit_msg:
            add("Faults", "recent_critical", "Recent critical fault", "warn", f"{crit_ts} {crit_msg}".strip(), False)
        else:
            add("Faults", "recent_critical", "Recent critical fault", "pass", "none recorded this run", False)

        fail_count = sum(1 for c in checks if c.get("status") == "fail")
        warn_count = sum(1 for c in checks if c.get("status") == "warn")
        if fail_count:
            overall = "NO-GO"
            title = "NO-GO — Do not start yet" if not streaming else "NO-GO — Streaming needs attention"
        elif warn_count:
            overall = "CAUTION"
            title = "CAUTION — Usable, but check these items"
        else:
            overall = "GO"
            title = "GO — Ready to stream" if not streaming else "GO — Streaming healthy"

        summary = f"{fail_count} fail, {warn_count} caution, {len(checks) - fail_count - warn_count} ok/info"

        return {
            "type": "health_report",
            "app": APP_DISPLAY,
            "build_id": BUILD_ID,
            "generated_ts": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "overall": overall,
            "title": title,
            "summary": summary,
            "streaming": bool(streaming),
            "recording": bool(recording),
            "desired_streaming": bool(desired),
            "checks": checks,
        }

    def _web_payload(self) -> dict:
        # Single snapshot for WebSocket clients.
        # Browser JS expects:
        #   msg.type == "state"
        #   msg.state (banner/lines/rec_on)
        #   msg.logs (array of lines)
        #   msg.preset_labels (map)
        with self._ui_lock:
            state = dict(self._ui_state)
            logs = list(self._log_buf)[-int(self.cfg.WEB_HUD_LOG_LINES):]
            camera_trace = list(getattr(self, "_camera_trace_buf", []))
            last_scene_action = str(getattr(self, "_last_scene_action", "") or "")
            ver = self._state_version
        return {
            "type": "state",
            "ver": ver,
            "state": {
                "banner_text": state.get("banner_text", ""),
                "app_version": APP_DISPLAY,
                "banner_style": state.get("banner_style", "Banner.TLabel"),
                "obs_line": state.get("obs_line", ""),
                "midi_line": state.get("midi_line", ""),
                "cam_line": state.get("cam_line", ""),
                "timer_text": state.get("timer_text", ""),
                "audio": {
                    "enabled": bool(getattr(self.cfg, "WEB_HUD_AUDIO_MASTER_ENABLED", True)),
                    "mode": self._audio_mode(),
                    "label": self._audio_mode_label(),
                    "description": self._audio_mode_description(),
                    "targets": self._audio_inputs(),
                    "master_db": float(self.audio_master_db),
                    "meter_db": float(self.audio_master_meter_db),
                    "levels_db": dict(getattr(self, "_audio_levels_db", {}) or {}),
                    "meter_age_s": max(0.0, time.time() - float(getattr(self, "_audio_meter_last_update_ts", 0.0) or 0.0)) if float(getattr(self, "_audio_meter_last_update_ts", 0.0) or 0.0) > 0 else None,
                    "min_db": float(getattr(self.cfg, "AUDIO_MASTER_MIN_DB", -40.0)),
                    "max_db": float(getattr(self.cfg, "AUDIO_MASTER_MAX_DB", 6.0)),
                },
                "sync": self._sync_offsets_snapshot(),
                "health": {
                    "level": state.get("health_level", "READY"),
                    "title": state.get("health_title", "READY"),
                    "detail": state.get("health_detail", ""),
                    "last_ts": state.get("health_last_ts", ""),
                    "last_msg": state.get("health_last_msg", ""),
                },
                "rec_on": bool(state.get("rec_on", False)),
                "camera_trace": {
                    "enabled": bool(getattr(self.cfg, "HUD_CAMERA_TRACE_ENABLED", False)),
                    "last_scene_action": last_scene_action,
                    "lines": camera_trace,
                },
                "director": {
                    "program_camera": self._actual_program_camera_key(),
                    "west": self._director_camera_state("west"),
                    "east": self._director_camera_state("east"),
                    "preview": {
                        "enabled": self._director_preview_enabled(),
                        "backend": self._director_preview_backend(),
                        "width": int(getattr(self.cfg, "DIRECTOR_PREVIEW_WIDTH", 640) or 640),
                        "height": int(getattr(self.cfg, "DIRECTOR_PREVIEW_HEIGHT", 360) or 360),
                        "refresh_ms": int(getattr(self.cfg, "DIRECTOR_PREVIEW_REFRESH_MS", 500) or 500),
                        "stale_after_ms": int(getattr(self.cfg, "DIRECTOR_PREVIEW_STALE_AFTER_MS", 3000) or 3000),
                        "west": self._director_preview_public_state("west"),
                        "east": self._director_preview_public_state("east"),
                    },
                },
            },
            "logs": logs,
            "preset_labels": {int(k): v for k, v in self.cfg.PRESET_LABELS.items()},
        }

    def _web_html(self) -> str:
        # Single-file HTML + external JS (avoids inline-script parsing issues)
        # JS served from /app.js?v=14
        return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Stream Agent HUD</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; background:#0b1118; color:#e9eef5; }
  .wrap { max-width:520px; margin:0 auto; padding:14px; }
  .card { background:#121a24; border:1px solid #1d2a3a; border-radius:16px; padding:14px; margin:10px 0; box-shadow: 0 8px 24px rgba(0,0,0,.35); }
  .appVer { font-size:12px; opacity:.75; text-align:center; letter-spacing:.4px; margin-bottom:6px; }
  .title { font-size:20px; font-weight:700; text-align:center; letter-spacing:.4px; }

  /* Status banner: 4pt-ish black outline + state colors (matches PC HUD) */
  .statusBanner { border:5px solid #000; border-radius:14px; padding:10px 12px; margin-top:6px; }
  .sb-live { background:#2E7D32; }
  .sb-ready { background:#4CAF50; }
  .sb-warn { background:#FFC107; }
  .sb-stop { background:#FF9800; }
  .sb-ended { background:#2196F3; }
  .sb-error { background:#F44336; }

  /* White-text stroke (approx 3pt): use stroke + shadow fallback */
  .sb-live .title, .sb-ready .title, .sb-ended .title, .sb-error .title {
    color:#fff;
    -webkit-text-stroke: 3px #000;
    paint-order: stroke fill;
    text-shadow:
      -2px -2px 0 #000, 0 -2px 0 #000, 2px -2px 0 #000,
      -2px  0   0 #000,               2px  0   0 #000,
      -2px  2px 0 #000, 0  2px 0 #000, 2px  2px 0 #000;
  }
  .sb-warn .title, .sb-stop .title { color:#000; -webkit-text-stroke: 0; text-shadow:none; }

  /* Make cards and buttons borders bolder (3pt solid black) */
  .card { border:3px solid #000; }
  .btn { border:3px solid #000; }
  .pbtn { border:3px solid #000; }

  /* Outlined button text (white) */
  .btn { color:#fff; -webkit-text-stroke: 2px #000; paint-order: stroke fill;
    text-shadow:
      -1px -1px 0 #000, 0 -1px 0 #000, 1px -1px 0 #000,
      -1px  0   0 #000,              1px  0   0 #000,
      -1px  1px 0 #000, 0  1px 0 #000, 1px  1px 0 #000;
  }

  .conn { margin-top:6px; font-size:12px; opacity:.8; text-align:center; white-space:pre-wrap; }
  .row { display:flex; gap:10px; }
  .btn { flex:1; padding:14px 10px; border-radius:14px; border:0; font-size:16px; font-weight:700; cursor:pointer;  -webkit-touch-callout:none; -webkit-user-select:none; user-select:none; touch-action:manipulation; -webkit-tap-highlight-color: transparent;}
  .btn:active { transform: translateY(1px); }
  .bStart { background:#2fb14d; }
  .bStop  { background:#e14b45; }
  .bRec   { background:#f0a018; }
  .bRec.on { background:#ff3b3b; box-shadow: 0 0 0 2px rgba(255,59,59,.35) inset; }
    .bView { background:#1565C0; }
  .bView2 { background:#37474F; }
  /* Make anchor buttons behave like buttons */
  a.btn { text-decoration:none; display:flex; align-items:center; justify-content:center; }
.sectionTitle { font-size:13px; font-weight:700; opacity:.85; margin-bottom:8px; }
  .grid { display:grid; grid-template-columns: 1fr 1fr; gap:10px; }
  .pbtn { padding:12px 10px; border-radius:12px; border:1px solid #24354a; background:#0e1620; color:#e9eef5; font-size:14px; font-weight:650; cursor:pointer;  -webkit-touch-callout:none; -webkit-user-select:none; user-select:none; touch-action:manipulation; -webkit-tap-highlight-color: transparent;}
  .pbtn:active { transform: translateY(1px); }
  pre { margin:0; white-space:pre-wrap; word-break:break-word; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size:12px; line-height:1.25; }
  #logBox{ display:block; max-height:360px; overflow-y:auto; padding-right:6px; }
  .hint { font-size:12px; opacity:.75; text-align:center; padding:10px; }

  /* Sticky health indicator (operational status) */
  .healthBox { border:1px solid #26384f; border-radius:12px; padding:10px 12px; margin:10px 0 10px 0; }
  .healthTitle { font-size:16px; font-weight:800; letter-spacing:.3px; }
  .healthDetail { margin-top:4px; font-size:12px; opacity:.92; white-space:pre-wrap; }
  .healthLast { margin-top:6px; font-size:12px; opacity:.85; white-space:pre-wrap; }
  .h-ready { background: rgba(120, 140, 160, .10); border-color: rgba(120, 140, 160, .25); }
  .h-live { background: rgba(25, 190, 95, .14); border-color: rgba(25, 190, 95, .35); }
  .h-starting { background: rgba(240, 180, 20, .12); border-color: rgba(240, 180, 20, .35); }
  .h-recovering { background: rgba(240, 180, 20, .12); border-color: rgba(240, 180, 20, .35); }
  .h-degraded { background: rgba(240, 180, 20, .12); border-color: rgba(240, 180, 20, .35); }
  .h-error { background: rgba(230, 60, 60, .13); border-color: rgba(230, 60, 60, .35); }
  .h-recovered { background: rgba(100, 160, 255, .12); border-color: rgba(100, 160, 255, .35); }
  .twoBtnGrid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; }
  .sceneBtn { background:#28415c; color:#fff; }
  .sceneBtn:disabled { opacity:.55; }
  .audioWrap { display:flex; flex-direction:column; gap:8px; }
  .audioTop { display:flex; justify-content:space-between; align-items:center; gap:10px; font-size:13px; }
  .meterOuter { height:16px; background:#0c141d; border:1px solid #28415c; border-radius:999px; overflow:hidden; }
  .meterInner { height:100%; width:0%; background:linear-gradient(90deg,#2ecc71,#f1c40f,#e74c3c); }
  .audioSlider { width:100%; }
  .subtle { font-size:12px; opacity:.8; }

  .toolCard { background:linear-gradient(135deg,#101a25,#0b1118); }
  .toolGrid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }
  @media (max-width:560px){ .toolGrid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
  .toolBtn { min-height:72px; padding:12px; border-radius:16px; border:3px solid #000; text-decoration:none; color:#fff; display:flex; align-items:center; gap:10px; background:linear-gradient(135deg,#182637,#101822); box-shadow:0 8px 20px rgba(0,0,0,.28); -webkit-touch-callout:none; -webkit-user-select:none; user-select:none; touch-action:manipulation; -webkit-tap-highlight-color: transparent; }
  .toolBtn:active { transform:translateY(1px); }
  .toolBtn b { display:block; font-size:15px; letter-spacing:.2px; line-height:1.1; }
  .toolBtn small { display:block; margin-top:4px; font-size:11px; opacity:.78; line-height:1.15; }
  .toolIcon { font-size:25px; width:32px; text-align:center; filter:drop-shadow(0 2px 2px rgba(0,0,0,.45)); }
  .toolDirector { background:linear-gradient(135deg,#173e5f,#0e1b2a); }
  .toolConfig, .toolAudioCfg { background:linear-gradient(135deg,#455A64,#16212c); }
  .toolHealth { background:linear-gradient(135deg,#2e7d32,#12351d); }
  .toolLive { background:linear-gradient(135deg,#b71c1c,#471313); }
  .toolEmbed { background:linear-gradient(135deg,#1565C0,#10243d); }
  .toolSync { background:linear-gradient(135deg,#7B1FA2,#251436); }
  .toolManual { background:linear-gradient(135deg,#00695C,#0f2a25); }
  .toolBtn.hiddenTool { display:none !important; }
  .toolBtn.featuredTool { outline:2px solid rgba(255,255,255,.16); }

</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="appVer" id="appVer">__APP_VER__</div>
    <div class="statusBanner sb-ready" id="statusBanner"><div class="title" id="statusTitle">CONNECTING…</div></div>
    <div class="conn" id="connLine">Loading JavaScript…</div>
  </div>

  <div class="card">
    <div class="row">
      <button class="btn bStart" id="btnStart">Start</button>
      <button class="btn bStop" id="btnStop">Stop</button>
      <button class="btn bRec" id="btnRec">REC</button>
    </div>
  </div>

  <div class="card toolCard">
    <div class="sectionTitle">Live Tools</div>
    <div class="toolGrid" id="liveToolGrid">
      <a class="toolBtn toolDirector" id="btnDirector" href="__DIRECTOR__">
        <span class="toolIcon">🎛️</span><span><b>Director</b><small>Preview + camera fallback</small></span>
      </a>
      <a class="toolBtn toolConfig" id="btnCfg" href="__CONFIG__">
        <span class="toolIcon">⚙️</span><span><b>Config</b><small>Timer, service, cameras</small></span>
      </a>
      <a class="toolBtn toolHealth" id="btnPreflight" href="__PREFLIGHT__">
        <span class="toolIcon">📋</span><span><b>Push for report</b><small>Preflight check</small></span>
      </a>
      <a class="toolBtn toolManual" id="btnManual" href="__MANUAL__">
        <span class="toolIcon">📖</span><span><b>Manual</b><small>Setup + operating guide</small></span>
      </a>
      <a class="toolBtn toolLive" id="btnViewYT" href="__YTLIVE__" target="_blank" rel="noopener">
        <span class="toolIcon">▶️</span><span><b>View Live</b><small>YouTube direct</small></span>
      </a>
      <a class="toolBtn toolEmbed" id="btnViewEmbed" href="__VIEWER__">
        <span class="toolIcon">📺</span><span><b>Embedded</b><small>In-HUD viewer</small></span>
      </a>
      <a class="toolBtn toolSync hiddenTool" id="btnSync" href="__SYNC__">
        <span class="toolIcon">🎚️</span><span><b>Sync</b><small>ASIO audio offset</small></span>
      </a>
      <a class="toolBtn toolAudioCfg" id="btnAudioCfg" href="__CONFIG_AUDIO__">
        <span class="toolIcon">🔊</span><span><b>Audio Config</b><small id="audioCfgSub">Mode + fader setup</small></span>
      </a>
    </div>
    <div class="hint" id="syncHudHint" style="margin-top:10px; opacity:.84;">Sync appears only when shared ASIO audio is active. Timer settings live under Config → Timer.</div>
  </div>

  <div class="card">
    <div class="sectionTitle">Requested Views</div>
    <div class="grid" id="presetGrid"></div>
    <div class="hint" style="margin-top:8px; opacity:.82;">These buttons request service views. Stream Agent III sd now prefers any ready shot first, otherwise it prepares the off-air camera.</div>
  </div>

  <div class="card">
    <div class="sectionTitle">Manual Camera Cut</div>
    <div class="twoBtnGrid">
      <button class="btn sceneBtn" id="btnSceneWest">West</button>
      <button class="btn sceneBtn" id="btnSceneEast">East</button>
    </div>
    <div class="hint" style="margin-top:8px; opacity:.82;">Manual camera cuts cancel any pending automatic cut.</div>
  </div>

  <div class="card">
    <div class="sectionTitle">Audio Master</div>
    <div class="audioWrap">
      <div class="audioTop">
        <div id="audioLabel">Master: 0.0 dB</div>
        <div id="audioMeterLabel" class="subtle">Meter: -60.0 dBFS</div>
      </div>
      <div class="meterOuter"><div id="audioMeterFill" class="meterInner"></div></div>
      <input class="audioSlider" id="audioSlider" type="range" min="-40" max="6" step="0.5" value="0"/>
      <div class="subtle" id="audioHelp">One Web HUD fader controls the configured live audio target.</div>
    </div>
  </div>

  <div class="card">
    <div class="sectionTitle">Log (last 30)</div>
    <div id="healthBox" class="healthBox h-ready">
      <div id="healthTitle" class="healthTitle">READY</div>
      <div id="healthDetail" class="healthDetail"></div>
      <div id="healthLast" class="healthLast"></div>
    </div>
    <div id="traceWrap" style="display:none; margin:10px 0 12px 0;">
      <div class="sectionTitle">Camera Trace</div>
      <div id="lastSceneAction" class="subtle" style="white-space:pre-wrap; margin-bottom:6px;"></div>
      <pre id="cameraTraceBox"></pre>
    </div>
    <pre id="logBox"></pre>
  </div>

  <div class="hint"><noscript>This page needs JavaScript enabled.</noscript></div>
</div>

<script src="/app.js?v=17"></script>
</body>
</html>
""".replace("__APP_VER__", APP_DISPLAY).replace("__YTLIVE__", getattr(self.cfg, "YOUTUBE_LIVE_URL", "https://www.youtube.com/@NewHopeLutheranChurchRegina/live")).replace("__VIEWER__", "/viewer" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else "")).replace("__DIRECTOR__", "/director" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else "")).replace("__PREFLIGHT__", "/preflight" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else "")).replace("__SYNC__", "/sync" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else "")).replace("__CONFIG__", "/config" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else "")).replace("__MANUAL__", "/manual" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else "")).replace("__CONFIG_AUDIO__", "/config" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else "") + "#audio").replace("__SYNC__", "/sync" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else ""))

    def _web_manual_html(self) -> str:
        """Built-in operator reference manual for Stream Agent III sd."""
        token_qs = f"?token={self.cfg.WEB_HUD_TOKEN}" if self.cfg.WEB_HUD_TOKEN else ""
        back_url = f"/{token_qs}"
        config_url = f"/config{token_qs}"
        director_url = f"/director{token_qs}"
        viewer_url = f"/viewer{token_qs}"
        return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Stream Agent III sd Manual</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif; background:#071019; color:#edf4fb; line-height:1.45; }
  .wrap { max-width:960px; margin:0 auto; padding:16px; }
  .hero { background:linear-gradient(135deg,#0d3b4d,#092033 60%,#061019); border:3px solid #000; border-radius:22px; padding:20px; box-shadow:0 14px 32px rgba(0,0,0,.42); }
  h1 { margin:0 0 6px 0; font-size:30px; letter-spacing:.2px; }
  h2 { margin:26px 0 10px; padding-top:8px; border-top:1px solid rgba(255,255,255,.12); font-size:22px; }
  h3 { margin:18px 0 8px; font-size:17px; }
  p { margin:8px 0; }
  .sub { opacity:.84; font-size:14px; }
  .toolbar { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin:14px 0 0; }
  @media (max-width:720px){ .toolbar { grid-template-columns:repeat(2,minmax(0,1fr)); } }
  a.btn { color:white; text-decoration:none; text-align:center; font-weight:900; padding:12px 10px; border-radius:15px; border:3px solid #000; background:linear-gradient(135deg,#1565C0,#10243d); box-shadow:0 8px 20px rgba(0,0,0,.28); }
  a.btn.green { background:linear-gradient(135deg,#2e7d32,#123b1c); }
  a.btn.gray { background:linear-gradient(135deg,#455A64,#18242d); }
  a.btn.red { background:linear-gradient(135deg,#b71c1c,#471313); }
  .card { background:#111b26; border:2px solid rgba(0,0,0,.95); border-radius:18px; padding:16px; margin:14px 0; box-shadow:0 8px 24px rgba(0,0,0,.28); }
  .toc { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px 16px; }
  @media (max-width:720px){ .toc { grid-template-columns:1fr; } }
  .toc a { color:#9fd3ff; text-decoration:none; }
  .pill { display:inline-block; padding:2px 7px; border-radius:999px; background:#203247; border:1px solid #365572; font-size:12px; font-weight:800; }
  .warn { border-left:5px solid #FFC107; padding:10px 12px; background:rgba(255,193,7,.1); border-radius:10px; }
  .ok { border-left:5px solid #4CAF50; padding:10px 12px; background:rgba(76,175,80,.10); border-radius:10px; }
  .bad { border-left:5px solid #f44336; padding:10px 12px; background:rgba(244,67,54,.10); border-radius:10px; }
  code, pre { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  code { background:#071019; padding:2px 5px; border-radius:5px; }
  pre { background:#071019; border:1px solid #26384f; border-radius:12px; padding:12px; overflow:auto; }
  table { width:100%; border-collapse:collapse; margin:10px 0; }
  th,td { border-bottom:1px solid #26384f; padding:8px; text-align:left; vertical-align:top; }
  th { color:#cde8ff; }
  .footer { opacity:.72; text-align:center; padding:22px 0; font-size:12px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>📖 Stream Agent III sd User Manual</h1>
    <div class="sub">A practical operating and setup guide for the Axis-only church streaming automation app.</div>
    <div class="toolbar">
      <a class="btn green" href="__BACK__">⬅ Web HUD</a>
      <a class="btn" href="__DIRECTOR__">🎛️ Director</a>
      <a class="btn gray" href="__CONFIG__">⚙️ Config</a>
      <a class="btn red" href="__VIEWER__">📺 Viewer</a>
    </div>
  </div>

  <div class="card">
    <h2>Table of contents</h2>
    <div class="toc">
      <a href="#purpose">1. Purpose and design</a>
      <a href="#system">2. System overview</a>
      <a href="#documentation">3. GitHub documentation</a>
      <a href="#setup">4. Required setup</a>
      <a href="#operation">5. Normal service operation</a>
      <a href="#director">6. Director page</a>
      <a href="#audio">7. Current audio handling</a>
      <a href="#config">8. Config pages</a>
      <a href="#troubleshooting">9. Troubleshooting</a>
      <a href="#sunday">10. Sunday checklist</a>
      <a href="#reference">11. Quick reference</a>
    </div>
  </div>

  <div class="card" id="purpose">
    <h2>1. Purpose and design</h2>
    <p><b>Stream Agent III sd</b> helps New Hope Lutheran Church run a live-streamed service with a smaller volunteer team. It coordinates OBS streaming, Axis PTZ camera directing, Proclaim slide/MIDI sequencing, the browser Web HUD, the Director fallback page, and basic audio-level control.</p>
    <p>The practical purpose is to let <b>one trained operator</b> handle streaming, audio mixer control, slide sequencing, camera changes, stream timing, and fallback actions that would otherwise commonly require a three-person team: sound operator, slide operator, and camera/stream operator.</p>
    <p>This matters because the church does not have enough permanently trained and committed volunteers to staff roughly <b>60 services per year</b> with a full three-person AV team. The app reduces the number of live decisions and turns common service events into repeatable, recoverable actions.</p>
    <div class="ok"><b>Core idea:</b> Proclaim sends a view cue. The app chooses the best Axis camera, moves the off-air camera when possible, waits for blind delay when configured, then cuts OBS to the prepared scene.</div>
  </div>

  <div class="card" id="system">
    <h2>2. System overview</h2>
    <table>
      <tr><th>Part</th><th>Role</th></tr>
      <tr><td>OBS</td><td>Encodes the YouTube stream and contains scenes such as <code>Introduction</code>, <code>West View</code>, and <code>East View</code>.</td></tr>
      <tr><td>Axis cameras</td><td>Two network PTZ cameras with saved presets such as Pulpit, Panorama, Choir, Piano, and Podium. Their RTSP Media Sources carry camera video and, in normal operation, embedded audio from the mixer feed.</td></tr>
      <tr><td>Mackie DL32SE and Master Fader</td><td>The DL32SE produces the live mix. Master Fader provides mixer control from the PC or tablet; Stream Agent does not replace the mixer, but it gives the operator quick stream-side audio level confidence and fader control inside OBS.</td></tr>
      <tr><td>Proclaim</td><td>Sends MIDI notes that request current views, next views, start stream, stop stream, or record toggle. This ties slide sequencing and camera timing together.</td></tr>
      <tr><td>Web HUD</td><td>Browser control panel for start/stop, Director, Config, audio fader, YouTube viewer, logs, and help.</td></tr>
      <tr><td>Director</td><td>Operator fallback page with camera previews, view buttons, manual camera cuts, and vertical audio faders.</td></tr>
      <tr><td>Config overrides</td><td>Saved Web HUD changes are stored in <code>config_overrides.json</code> instead of rewriting the Python app.</td></tr>
    </table>
  </div>

  <div class="card" id="documentation">
    <h2>3. GitHub documentation</h2>
    <p>The project documentation for both the Stream Agent app and the wider church AV tech system is kept in the ChurchStreamGuard GitHub repository.</p>
    <table>
      <tr><th>Resource</th><th>Location</th></tr>
      <tr><td>Main repository</td><td><a href="https://github.com/macilentiores/ChurchStreamGuard" target="_blank" rel="noopener">https://github.com/macilentiores/ChurchStreamGuard</a></td></tr>
      <tr><td>Documentation folder</td><td><a href="https://github.com/macilentiores/ChurchStreamGuard/tree/main/documentation" target="_blank" rel="noopener">https://github.com/macilentiores/ChurchStreamGuard/tree/main/documentation</a></td></tr>
    </table>
    <p>Use the documentation folder for the user manual, network diagrams, Stream Agent notes, Wake-on-LAN notes if retained historically, YouTube/OBS notes, and broader AV Tech system rebuild information.</p>
    <div class="warn"><b>Access note:</b> if a GitHub link does not open, the repository may still be private or the viewer may need to be signed in with access permission. Do not publish passwords, YouTube stream keys, OAuth secrets, or other credentials in the repository.</div>
  </div>

  <div class="card" id="setup">
    <h2>4. Required setup</h2>
    <h3>Python and app files</h3>
    <p>Run the app on the streaming PC. Keep the Python app file in a known folder along with any saved <code>config_overrides.json</code> file. Use the latest tested app file for Sunday operation.</p>
    <h3>OBS setup</h3>
    <ul>
      <li>OBS WebSocket must be enabled on port <code>4455</code>.</li>
      <li>Scene names must match the app config exactly: <code>Introduction</code>, <code>West View</code>, and <code>East View</code>.</li>
      <li>Camera source/input names must match config, normally <code>West_axis</code> and <code>East_axis</code>.</li>
      <li>For normal operation, the <code>West_axis</code> and <code>East_axis</code> Media Sources carry both camera video and the embedded audio coming from the Axis camera audio path.</li>
      <li>If using the older/fallback shared ASIO method, create one OBS audio source named <code>ASIO_audio</code> and add it to scenes using <b>Add Existing</b>.</li>
    </ul>
    <h3>Axis camera setup</h3>
    <ul>
      <li>Both Axis cameras must be reachable on the church camera LAN.</li>
      <li>Saved preset names must match the app exactly. Axis preset names are case-sensitive; <code>Podium</code> and <code>podium</code> are different.</li>
      <li>Keep preset names simple and identical on both cameras where possible.</li>
      <li>For the current church sound path, confirm the mixer feed is reaching the Axis camera audio input path used by OBS.</li>
    </ul>
    <h3>MIDI setup</h3>
    <p>The app looks for a MIDI input port containing the configured text, normally <code>proclaim</code>. Proclaim can then send view notes and control notes.</p>
    <pre>Channel 1 = current requested view
Channel 2 = next/prepared view
Notes 70-79 = view 1-10
Note 60 = Start Stream
Note 61 = Stop Stream
Note 62 = Record Toggle</pre>
  </div>

  <div class="card" id="operation">
    <h2>5. Normal service operation</h2>
    <h3>Before service</h3>
    <ol>
      <li>Start OBS and confirm the correct OBS profile is active.</li>
      <li>Start Proclaim and make sure the MIDI port is available.</li>
      <li>Start Stream Agent III sd.</li>
      <li>Open the Web HUD from the tablet or streaming PC.</li>
      <li>Open Director and confirm previews, status, and audio fader/meter movement.</li>
    </ol>
    <h3>Starting the stream</h3>
    <p>Use the Web HUD Start button, the timer, or a Proclaim MIDI start note. If the intro sequence is enabled, the app cuts to the Introduction scene, restarts the intro video, waits for it to end, then cuts to the post-intro scene.</p>
    <h3>During service</h3>
    <p>Normal view changes should come from Proclaim MIDI cues. Manual Web HUD view buttons can also request service views. The Director page is the fallback for manual camera operation.</p>
    <h3>Stopping the stream</h3>
    <p>Use the Stop button or Proclaim stop cue. The app uses a stop countdown to reduce accidental immediate stream shutdown.</p>
  </div>

  <div class="card" id="director">
    <h2>6. Director page</h2>
    <p>The Director page is the main operator fallback screen. It shows West and East camera previews, direct camera view controls, manual scene cuts, camera status, blind delay countdown, and vertical audio faders.</p>
    <p><b>Service view buttons</b> request a logical church view. The app chooses the camera and scene. <b>Direct camera view buttons</b> steer a specific camera and are useful for fallback or setup.</p>
    <div class="warn"><b>Blind delay countdown:</b> when a delayed cut is pending, the Director page shows a short countdown such as <code>Blind wait: 10.0s</code>. This means the app is intentionally waiting so viewers do not see PTZ motion.</div>
  </div>

  <div class="card" id="audio">
    <h2>7. Current audio handling</h2>
    <h3>Normal church method: Axis embedded audio</h3>
    <p>The current method is:</p>
    <pre>Mackie DL32SE live mix
  -> Axis camera audio input path
  -> embedded audio inside Axis RTSP stream
  -> OBS Media Sources: West_axis / East_axis
  -> OBS stream output
  -> YouTube Live</pre>
    <p>In <code>axis_embedded</code> mode, the OBS camera Media Sources carry the stream audio. The Web HUD master fader and Director vertical faders control the configured OBS audio targets, normally <code>West_axis</code> and <code>East_axis</code>.</p>
    <p>The Sync page is disabled in this mode because there is no separate <code>ASIO_audio</code> source to delay or advance. Audio and video travel together through the Axis/RTSP Media Sources.</p>
    <div class="ok"><b>Normal Sunday setting:</b> <code>AUDIO_MODE = "axis_embedded"</code>. This is the current production method unless deliberately changed for fallback testing.</div>
    <h3>Older / fallback method: shared ASIO audio</h3>
    <p>In <code>asio_shared</code> mode, one shared OBS audio input named <code>ASIO_audio</code> carries the live mix from a USB/ASIO audio interface. That source should be added to scenes using <b>Add Existing</b> so it remains one common OBS input.</p>
    <p>The Sync page applies only to this fallback ASIO method. It adjusts the OBS sync offset for the shared <code>ASIO_audio</code> source.</p>
    <div class="warn"><b>Do not mix methods accidentally:</b> during normal Axis embedded operation, avoid leaving an old <code>ASIO_audio</code> source active in live scenes unless it is intentionally muted or removed, or the stream may have duplicate/echoing audio.</div>
  </div>

  <div class="card" id="config">
    <h2>8. Config pages</h2>
    <p>Config is organized into categories such as Service, Timer, Cameras, Preset Delays, Audio / Sync, Director Preview, OBS / Scenes, MIDI / Proclaim, Web HUD, Logs / End, and Advanced.</p>
    <ul>
      <li><b>Apply</b> saves staged changes for the current category only.</li>
      <li><b>Restore Defaults</b> restores only the current category.</li>
      <li><b>Reset field</b> restores one field.</li>
      <li>If a value is changed back to its loaded value, it becomes unstaged automatically.</li>
      <li>Sensitive fields such as passwords and tokens are read-only in the Web HUD.</li>
    </ul>
    <p>Saved changes go into <code>config_overrides.json</code>. This is safer than rewriting the Python file.</p>
  </div>

  <div class="card" id="troubleshooting">
    <h2>9. Troubleshooting</h2>
    <table>
      <tr><th>Symptom</th><th>Likely checks</th></tr>
      <tr><td>OBS offline</td><td>Start OBS, check WebSocket port/password, confirm OBS is not blocked by firewall.</td></tr>
      <tr><td>MIDI not connected</td><td>Start Proclaim, confirm MIDI output/loopMIDI port, check <code>MIDI_INPUT_PORT_SUBSTRING</code>.</td></tr>
      <tr><td>Camera does not move</td><td>Check camera IP, Axis credentials, network, preset name spelling, and camera enable flags.</td></tr>
      <tr><td>Wrong shot after cue</td><td>Check Proclaim note/channel, preset delay, camera position assumptions, and Director camera trace.</td></tr>
      <tr><td>Sync button missing</td><td>Expected in <code>axis_embedded</code> mode. Switch <code>AUDIO_MODE</code> to <code>asio_shared</code> only if using the older ASIO_audio fallback path.</td></tr>
      <tr><td>Audio fader works but no stream audio</td><td>Check that DL32SE audio is reaching the Axis camera audio input path, that Axis audio is enabled, and that <code>West_axis</code>/<code>East_axis</code> audio is not muted in OBS.</td></tr>
      <tr><td>Echo or doubled audio</td><td>Check for an old <code>ASIO_audio</code> source still active while Axis embedded audio is also active.</td></tr>
      <tr><td>Blind delay feels stuck</td><td>Look for the Director countdown. If it is counting down, the app is intentionally waiting.</td></tr>
      <tr><td>YouTube viewer does not play audio</td><td>Use Open in YouTube. Browser autoplay and iframe rules can limit embedded playback.</td></tr>
    </table>
  </div>

  <div class="card" id="sunday">
    <h2>10. Sunday checklist</h2>
    <ol>
      <li>Power/network gear is stable.</li>
      <li>OBS is open with the correct profile and scenes.</li>
      <li>Stream Agent says OBS connected.</li>
      <li>Proclaim is open and MIDI is connected.</li>
      <li>Director previews show West and East.</li>
      <li><code>AUDIO_MODE</code> is <code>axis_embedded</code> for normal operation.</li>
      <li>Audio fader/meter responds after the mixer and Axis audio path are active.</li>
      <li>Timer is set correctly if automatic start is used.</li>
      <li>Optional: open YouTube viewer for confidence monitoring.</li>
    </ol>
  </div>

  <div class="card" id="reference">
    <h2>11. Quick reference</h2>
    <pre>Web HUD:      http://streaming-pc-ip:8765/
Director:     /director
Config:       /config
Manual:       /manual
Health check: /health

Documentation: https://github.com/macilentiores/ChurchStreamGuard/tree/main/documentation

Normal scenes: Introduction, West View, East View
Normal video/audio inputs: West_axis, East_axis
Normal audio mode: axis_embedded
Normal audio path: DL32SE -> Axis audio input -> Axis RTSP embedded audio -> OBS -> YouTube
Fallback ASIO source: ASIO_audio
Axis preset warning: names are exact and case-sensitive</pre>
  </div>

  <div class="footer">Stream Agent III sd manual built into the Web HUD. Main documentation is in GitHub: https://github.com/macilentiores/ChurchStreamGuard/tree/main/documentation</div>
</div>
</body>
</html>""".replace("__BACK__", back_url).replace("__CONFIG__", config_url).replace("__DIRECTOR__", director_url).replace("__VIEWER__", viewer_url)

    def _web_viewer_html(self) -> str:
        # Simple "viewer screen" for phones/tablets with a big BACK button.
        # Embedding can be picky on mobile; we provide a direct YouTube fallback link too.
        token_qs = f"?token={self.cfg.WEB_HUD_TOKEN}" if self.cfg.WEB_HUD_TOKEN else ""
        back_url = f"/{token_qs}"
        # Prefer the official "live_stream?channel=" embed format.
        ch = (getattr(self.cfg, "YOUTUBE_CHANNEL_ID", "") or "").strip()
        live_url = (getattr(self.cfg, "YOUTUBE_LIVE_URL", "") or "").strip()
        embed_src = f"https://www.youtube.com/embed/live_stream?channel={ch}" if ch else live_url
        # Note: autoplay with sound may be blocked by mobile browsers; user tap is normal.
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Live Viewer</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; background:#0b1118; color:#e9eef5; }}
  .wrap {{ max-width:820px; margin:0 auto; padding:14px; }}
  .card {{ background:#121a24; border:3px solid #000; border-radius:16px; padding:14px; margin:10px 0; box-shadow: 0 8px 24px rgba(0,0,0,.35); }}
  .title {{ font-size:18px; font-weight:800; text-align:center; }}
  .btn {{ border:3px solid #000; border-radius:14px; padding:12px 14px; font-weight:900; cursor:pointer; user-select:none; }}
  a.btn {{ text-decoration:none; display:flex; align-items:center; justify-content:center; }}
  .bBack {{ background:#4CAF50; color:#fff; -webkit-text-stroke: 1px #000; text-shadow:-1px -1px 0 #000,0 -1px 0 #000,1px -1px 0 #000,-1px 0 0 #000,1px 0 0 #000,-1px 1px 0 #000,0 1px 0 #000,1px 1px 0 #000; }}
  .bYT {{ background:#1565C0; color:#fff; -webkit-text-stroke: 1px #000; text-shadow:-1px -1px 0 #000,0 -1px 0 #000,1px -1px 0 #000,-1px 0 0 #000,1px 0 0 #000,-1px 1px 0 #000,0 1px 0 #000,1px 1px 0 #000; }}
  .videoWrap {{ position:relative; width:100%; padding-top:56.25%; border:3px solid #000; border-radius:16px; overflow:hidden; background:#000; }}
  iframe {{ position:absolute; top:0; left:0; width:100%; height:100%; border:0; }}
  .hint {{ font-size:12px; opacity:.8; text-align:center; margin-top:10px; }}
  .row {{ display:flex; gap:10px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="row">
      <a class="btn bBack" href="{back_url}">⬅ Back to Web HUD</a>
      <a class="btn bYT" href="{live_url}" target="_blank" rel="noopener">Open in YouTube</a>
    </div>
    <div class="hint">If the embedded player won’t load or won’t play audio, use “Open in YouTube”.</div>
  </div>

  <div class="card">
    <div class="title">Live Stream Viewer</div>
    <div class="videoWrap">
      <iframe
        src="{embed_src}"
        title="YouTube Live"
        allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
        allowfullscreen></iframe>
    </div>
  </div>
</div>
</body>
</html>"""

    def _web_sync_html(self) -> str:
        token_qs = f"?token={self.cfg.WEB_HUD_TOKEN}" if self.cfg.WEB_HUD_TOKEN else ""
        back_url = "/" + token_qs
        live_url = (getattr(self.cfg, "YOUTUBE_LIVE_URL", "") or "").strip()
        ch = (getattr(self.cfg, "YOUTUBE_CHANNEL_ID", "") or "").strip()
        embed_src = f"https://www.youtube.com/embed/live_stream?channel={ch}&playsinline=1" if ch else live_url
        sync_js_url = "/sync.js" + token_qs
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Live Sync</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; background:#0b1118; color:#e9eef5; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:14px; }}
  .card {{ background:#121a24; border:3px solid #000; border-radius:18px; padding:14px; margin:10px 0; box-shadow: 0 8px 24px rgba(0,0,0,.35); }}
  .title {{ font-size:22px; font-weight:900; text-align:center; letter-spacing:.3px; }}
  .sub {{ font-size:12px; opacity:.82; text-align:center; white-space:pre-wrap; margin-top:6px; }}
  .row {{ display:flex; gap:10px; flex-wrap:wrap; }}
  .btn {{ flex:1; min-width:160px; padding:12px 12px; border-radius:14px; border:3px solid #000; font-size:15px; font-weight:800; cursor:pointer; color:#fff; text-decoration:none; display:flex; align-items:center; justify-content:center; user-select:none; touch-action:manipulation; -webkit-tap-highlight-color: transparent; }}
  .btn:active {{ transform: translateY(1px); }}
  .bBack {{ background:#455A64; }}
  .bYT {{ background:#1565C0; }}
  .bZoom {{ background:#546E7A; }}
  .bUnlock {{ background:#FF9800; }}
  .pill {{ display:inline-flex; align-items:center; justify-content:center; min-width:120px; padding:8px 12px; border-radius:999px; border:2px solid #000; font-size:12px; font-weight:900; }}
  .pill.locked {{ background:#6d4c41; color:#fff; }}
  .pill.unlocked {{ background:#2E7D32; color:#fff; }}
  .playerWrap {{ position:relative; width:100%; aspect-ratio:16/9; border:4px solid #000; border-radius:18px; overflow:hidden; background:#000; max-height:min(62vh, 720px); }}
  body.zoomed .playerWrap {{ max-height:min(78vh, 920px); }}
  iframe {{ position:absolute; inset:0; width:100%; height:100%; border:0; }}
  .controlsCard {{ position:sticky; bottom:0; z-index:20; }}
  .syncGrid {{ display:grid; grid-template-columns:minmax(280px, 520px); justify-content:center; gap:12px; }}
  .syncBox {{ border:2px solid #24354a; border-radius:16px; padding:14px; background:#0e1620; width:100%; box-sizing:border-box; margin:0 auto; }}
  .syncHead {{ font-size:16px; font-weight:900; text-align:center; }}
  .syncVal {{ font-size:30px; font-weight:900; text-align:center; margin:8px 0; }}
  .syncMeta {{ font-size:12px; opacity:.82; text-align:center; min-height:18px; }}
  .stepRow {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:8px; margin-top:10px; }}
  .stepBtn {{ padding:12px 8px; border-radius:12px; border:3px solid #000; background:#203244; color:#fff; font-size:16px; font-weight:900; cursor:pointer; user-select:none; touch-action:manipulation; -webkit-tap-highlight-color: transparent; }}
  .stepBtn:active {{ transform: translateY(1px); }}
  .stepBtn:disabled {{ opacity:.45; }}
  .exactRow {{ display:grid; grid-template-columns:1fr auto; gap:8px; margin-top:10px; }}
  input[type=number] {{ background:#0b1118; color:#e9eef5; border:2px solid #24354a; border-radius:12px; padding:12px; font-size:18px; width:100%; box-sizing:border-box; }}
  .applyBtn {{ padding:12px 14px; border-radius:12px; border:3px solid #000; background:#2E7D32; color:#fff; font-weight:900; cursor:pointer; }}
  .copyRow {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; margin-top:12px; }}
  .hint {{ font-size:12px; opacity:.78; text-align:center; margin-top:8px; }}
  .status {{ font-size:12px; opacity:.88; text-align:center; margin-top:8px; min-height:18px; white-space:pre-wrap; }}
  @media (max-width: 900px) {{
    .wrap {{ max-width:760px; padding:10px; }}
    .card {{ padding:12px; }}
    .playerWrap {{ max-height:min(42vh, 520px); }}
    body.zoomed .playerWrap {{ max-height:min(62vh, 820px); }}
    .controlsCard {{ max-width:560px; margin-left:auto; margin-right:auto; }}
    .syncGrid {{ grid-template-columns:minmax(260px, 100%); }}
  }}
  @media (orientation: portrait) {{
    .wrap {{ max-width:760px; padding:10px; }}
    .playerWrap {{ max-height:min(40vh, 500px); }}
    body.zoomed .playerWrap {{ max-height:min(58vh, 760px); }}
    .controlsCard {{ max-width:560px; margin-left:auto; margin-right:auto; }}
    .syncGrid {{ grid-template-columns:minmax(260px, 100%); }}
  }}
  @media (orientation: portrait) and (max-width: 700px) {{
    .row {{ gap:8px; }}
    .btn {{ min-width:0; flex:1 1 calc(50% - 8px); }}
    .controlsCard .row .title {{ width:100%; text-align:center !important; }}
    .controlsCard .row {{ justify-content:center; }}
    .syncBox {{ padding:12px; }}
    .syncVal {{ font-size:28px; }}
    .exactRow {{ grid-template-columns:1fr; }}
    .applyBtn {{ width:100%; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="title">Live Sync</div>
    <div class="sub" id="syncConn">Connecting…</div>
    <div class="row" style="margin-top:10px;">
      <a class="btn bBack" href="{back_url}">⬅ Back to main HUD</a>
      <a class="btn bYT" href="{live_url}" target="_blank" rel="noopener">Open in YouTube</a>
      <button class="btn bZoom" id="btnZoom" type="button">Enlarge View</button>
      <button class="btn bUnlock" id="btnUnlock" type="button">Unlock Sync</button>
    </div>
    <div class="hint">Use the embedded live player for lip-sync work. Pinch zoom is allowed on this page, and Enlarge View makes more room for faces while keeping the controls on-screen.</div>
  </div>

  <div class="card">
    <div class="playerWrap">
      <iframe src="{embed_src}" title="YouTube Live Sync Viewer" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
    </div>
  </div>

  <div class="card controlsCard">
    <div class="row" style="justify-content:space-between; align-items:center; margin-bottom:10px;">
      <div class="title" style="font-size:18px; text-align:left;">Audio Sync Offset</div>
      <div id="lockPill" class="pill locked">LOCKED</div>
    </div>
    <div class="syncGrid">
      <div class="syncBox">
        <div class="syncHead" id="sharedSyncHead">Shared ASIO Audio</div>
        <div class="syncVal" id="sharedSyncVal">0 ms</div>
        <div class="syncMeta" id="sharedSyncMeta">Input: —</div>
        <div class="syncMeta" id="syncModeHint"></div>
        <div class="stepRow">
          <button class="stepBtn" data-delta="-100">-100</button>
          <button class="stepBtn" data-delta="-20">-20</button>
          <button class="stepBtn" data-delta="20">+20</button>
          <button class="stepBtn" data-delta="100">+100</button>
        </div>
        <div class="exactRow">
          <input type="number" id="sharedSyncInput" inputmode="numeric" />
          <button class="applyBtn" id="sharedApply" type="button">Apply</button>
        </div>
      </div>
    </div>
    <div class="hint">In shared-ASIO mode this page adjusts ASIO_audio. In Axis embedded-audio mode it becomes a status page because camera audio and video are already carried together in RTSP.</div>
    <div class="status" id="syncStatus">Waiting for state…</div>
  </div>
</div>
<script src="{sync_js_url}"></script>
</body>
</html>"""

    def _web_director_html(self) -> str:
        token_qs = f"?token={self.cfg.WEB_HUD_TOKEN}" if self.cfg.WEB_HUD_TOKEN else ""
        back_url = "/" + token_qs
        director_js_url = "/director.js" + token_qs
        preview_backend = self._director_preview_backend()
        if preview_backend in ("obs_mjpeg_stream", "axis_mjpeg"):
            west_src = "/cam/mjpg/west.mjpg" + ("?token=" + self.cfg.WEB_HUD_TOKEN if self.cfg.WEB_HUD_TOKEN else "")
            east_src = "/cam/mjpg/east.mjpg" + ("?token=" + self.cfg.WEB_HUD_TOKEN if self.cfg.WEB_HUD_TOKEN else "")
        else:
            west_src = "/cam/snapshot/west.jpg" + ("?token=" + self.cfg.WEB_HUD_TOKEN if self.cfg.WEB_HUD_TOKEN else "")
            east_src = "/cam/snapshot/east.jpg" + ("?token=" + self.cfg.WEB_HUD_TOKEN if self.cfg.WEB_HUD_TOKEN else "")
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Director Console</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; background:#07111b; color:#e9eef5; -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility; }}
  .shell {{ max-width:1280px; margin:0 auto; padding:12px; }}
  .toolbar, .panel {{ background:#121a24; border:3px solid #000; border-radius:18px; box-shadow:0 8px 24px rgba(0,0,0,.35); }}
  .toolbar {{ padding:12px; margin-bottom:10px; }}
  .barTitle {{ font-size:clamp(18px, 2.1vw, 22px); font-weight:900; text-align:center; letter-spacing:.4px; }}
  .barSub {{ margin-top:6px; text-align:center; font-size:12px; opacity:.82; white-space:pre-wrap; min-height:78px; display:flex; align-items:center; justify-content:center; }}
  .toolRow {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }}
  .btn {{ border:3px solid #000; border-radius:14px; padding:10px 12px; font-size:clamp(15px, 1.8vw, 17px); line-height:1.18; font-weight:800; letter-spacing:.1px; cursor:pointer; user-select:none; text-decoration:none; display:flex; align-items:center; justify-content:center; color:#fff; text-shadow:none; -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility; }}
  .btnBack {{ background:#455a64; flex:1 1 240px; min-width:200px; }}
  .btnMain {{ background:#1565c0; flex:1 1 180px; min-width:160px; }}
  .btnCutWest {{ background:#0d47a1; }}
  .btnCutEast {{ background:#1b5e20; }}
  .grid2 {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; align-items:start; }}
  .panel {{ padding:10px; min-width:0; }}
  .panel.live {{ box-shadow:0 0 0 4px rgba(46, 204, 113, .85), 0 8px 24px rgba(0,0,0,.35); }}
  .panel.standby {{ box-shadow:0 0 0 2px rgba(79, 98, 120, .65), 0 8px 24px rgba(0,0,0,.35); }}
  .pHead {{ display:flex; align-items:center; justify-content:flex-start; gap:14px; margin-bottom:8px; min-height:32px; }}
  .pTitle {{ font-size:clamp(16px, 1.8vw, 20px); font-weight:900; min-width:118px; }}
  .pill {{ border-radius:999px; padding:5px 10px; min-width:76px; text-align:center; font-size:11px; font-weight:900; border:2px solid #000; }}
  .pill.live {{ background:#2e7d32; color:#fff; }}
  .pill.standby {{ background:#546e7a; color:#fff; }}
  .previewAudioLayout {{ display:grid; grid-template-columns:minmax(0, 1fr) 62px; gap:8px; align-items:stretch; }}
  .videoWrap {{ position:relative; background:#000; border:4px solid #000; border-radius:16px; overflow:hidden; aspect-ratio:16/9; max-height:min(34vh, 340px); min-height:190px; box-shadow:0 0 0 2px rgba(79, 98, 120, .65); transition:box-shadow .12s ease; }}
  .videoWrap.live {{ box-shadow:0 0 0 4px rgba(46, 204, 113, .85); }}
  .videoWrap.standby {{ box-shadow:0 0 0 2px rgba(79, 98, 120, .65); }}
  .videoWrap img {{ width:100%; height:100%; object-fit:cover; display:block; background:#000; image-rendering:auto; }}
  .dirAudioRail {{ min-height:190px; border:4px solid #000; border-radius:16px; background:linear-gradient(180deg,#132235,#0b131d); display:flex; flex-direction:column; align-items:center; justify-content:space-between; padding:8px 5px; box-sizing:border-box; }}
  .dirAudioLabel {{ font-size:11px; font-weight:900; opacity:.92; text-align:center; line-height:1.05; white-space:pre-line; }}
  .dirAudioDb {{ font-size:11px; font-weight:900; opacity:.94; text-align:center; min-height:28px; display:flex; align-items:center; justify-content:center; }}
  .dirVSlider {{ flex:1; min-height:92px; height:100%; width:44px; margin:6px 0; writing-mode:vertical-lr; direction:rtl; accent-color:#64b5f6; cursor:pointer; }}
  .dirVSlider::-webkit-slider-thumb {{ cursor:pointer; }}
  .dirVSlider:disabled {{ opacity:.55; cursor:not-allowed; }}
  .dirMeter {{ width:14px; height:46px; border:2px solid #000; border-radius:999px; background:#05080c; overflow:hidden; display:flex; align-items:flex-end; }}
  .dirMeterFill {{ width:100%; height:0%; background:linear-gradient(0deg,#2ecc71,#f1c40f,#e74c3c); transition:height .12s linear; }}
  .camMeta {{ display:flex; justify-content:space-between; gap:10px; font-size:12px; opacity:.9; margin-top:8px; min-height:18px; }}
  .readyNow {{ color:#7ee787; font-weight:900; }}
  .readyHold {{ color:#ffd166; font-weight:900; }}
  .readyMove {{ color:#8ecbff; font-weight:800; }}
  .camStatus {{ margin-top:4px; font-size:12px; opacity:.82; min-height:18px; }}
  .cutRow {{ display:grid; grid-template-columns:1fr; gap:8px; margin-top:8px; }}
  .presetGrid {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:7px; margin-top:10px; }}
  .pbtn {{ border:2px solid #000; border-radius:12px; background:#203244; color:#fff; font-size:clamp(14px, 1.65vw, 16px); line-height:1.16; font-weight:800; letter-spacing:.08px; min-height:44px; padding:7px 8px; cursor:pointer; text-shadow:none; -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility; }}
  .pbtn:active, .btn:active {{ transform:translateY(1px); }}
  .pbtn:hover {{ filter:brightness(1.06); }}
  .stamp {{ margin-top:6px; font-size:11px; opacity:.65; text-align:right; min-height:14px; }}
  .legend {{ font-size:12px; opacity:.78; text-align:center; margin-top:8px; }}
  .offline {{ position:absolute; inset:auto 10px 10px 10px; background:rgba(180,30,30,.85); border:2px solid #000; border-radius:12px; padding:8px 10px; font-weight:900; display:none; }}
  @media (max-width: 1180px) {{
    .shell {{ max-width:980px; padding:10px; }}
    .grid2 {{ gap:8px; }}
    .videoWrap {{ max-height:min(28vh, 250px); min-height:165px; }}
    .dirAudioRail {{ min-height:165px; }}
    .previewAudioLayout {{ grid-template-columns:minmax(0, 1fr) 58px; }}
    .btn {{ padding:9px 10px; }}
    .pbtn {{ min-height:40px; }}
  }}
  @media (max-width: 900px) {{
    .grid2 {{ grid-template-columns:1fr; }}
    .videoWrap {{ max-height:min(30vh, 240px); min-height:165px; }}
    .dirAudioRail {{ min-height:165px; }}
  }}
</style>
</head>
<body>
<div class="shell">
  <div class="toolbar">
    <div class="barTitle">Director Console</div>
    <div class="barSub" id="dirConn">Connecting…</div>
    <div class="toolRow">
      <a class="btn btnBack" href="{back_url}">⬅ Back to main HUD</a>
      <a class="btn btnMain" href="/preflight{token_qs}">📋 Preflight Check</a>
      <button class="btn btnCutWest" id="btnCutWestTop">Cut West</button>
      <button class="btn btnCutEast" id="btnCutEastTop">Cut East</button>
    </div>
    <div class="legend">Function-first layout inspired by the Camera Director II tablet concept: dual previews plus a preset grid for live correction work.</div>
  </div>

  <div class="grid2">
    <div class="panel standby" id="panelWest">
      <div class="pHead">
        <div class="pTitle">West camera</div>
        <div class="pill standby" id="pillWest">STANDBY</div>
      </div>
      <div class="previewAudioLayout">
        <div class="videoWrap standby" id="videoWest">
          <img id="imgWest" alt="West camera preview" src="{west_src}"/>
          <div class="offline" id="offWest">Preview reconnecting…</div>
        </div>
        <div class="dirAudioRail" id="dirAudioRailWest" title="Master audio fader — same control as the main HUD fader">
          <div class="dirAudioLabel" id="dirAudioLabelWest">Master</div>
          <input class="dirVSlider" id="dirAudioWest" type="range" min="-40" max="6" step="0.5" value="0" orient="vertical" aria-label="Master audio level beside West preview"/>
          <div class="dirAudioDb" id="dirAudioDbWest">0.0 dB</div>
          <div class="dirMeter"><div class="dirMeterFill" id="dirMeterWest"></div></div>
        </div>
      </div>
      <div class="camMeta">
        <div id="westView">View: —</div>
        <div id="westReady">Ready: —</div>
      </div>
      <div class="camStatus" id="westStatus">Waiting for state…</div>
      <div class="cutRow"><button class="btn btnCutWest" id="btnCutWest">Cut to West</button></div>
      <div class="presetGrid" id="gridWest"></div>
      <div class="stamp" id="stampWest"></div>
    </div>

    <div class="panel standby" id="panelEast">
      <div class="pHead">
        <div class="pTitle">East camera</div>
        <div class="pill standby" id="pillEast">STANDBY</div>
      </div>
      <div class="previewAudioLayout">
        <div class="videoWrap standby" id="videoEast">
          <img id="imgEast" alt="East camera preview" src="{east_src}"/>
          <div class="offline" id="offEast">Preview reconnecting…</div>
        </div>
        <div class="dirAudioRail" id="dirAudioRailEast" title="Master audio fader — same control as the main HUD fader">
          <div class="dirAudioLabel" id="dirAudioLabelEast">Master</div>
          <input class="dirVSlider" id="dirAudioEast" type="range" min="-40" max="6" step="0.5" value="0" orient="vertical" aria-label="Master audio level beside East preview"/>
          <div class="dirAudioDb" id="dirAudioDbEast">0.0 dB</div>
          <div class="dirMeter"><div class="dirMeterFill" id="dirMeterEast"></div></div>
        </div>
      </div>
      <div class="camMeta">
        <div id="eastView">View: —</div>
        <div id="eastReady">Ready: —</div>
      </div>
      <div class="camStatus" id="eastStatus">Waiting for state…</div>
      <div class="cutRow"><button class="btn btnCutEast" id="btnCutEast">Cut to East</button></div>
      <div class="presetGrid" id="gridEast"></div>
      <div class="stamp" id="stampEast"></div>
    </div>
  </div>
</div>
<script src="{director_js_url}"></script>
</body>
</html>"""


    def _web_preflight_html(self) -> str:
        token_qs = f"?token={self.cfg.WEB_HUD_TOKEN}" if self.cfg.WEB_HUD_TOKEN else ""
        back_url = "/" + token_qs
        preflight_js_url = "/preflight.js" + token_qs
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Preflight Check</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif; background:#07111b; color:#e9eef5; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:14px; }}
  .card {{ background:#121a24; border:3px solid #000; border-radius:18px; padding:14px; margin:10px 0; box-shadow:0 8px 24px rgba(0,0,0,.35); }}
  .top {{ text-align:center; }}
  .appVer {{ opacity:.72; font-size:12px; margin-bottom:6px; }}
  .overall {{ border:5px solid #000; border-radius:18px; padding:18px; font-size:clamp(24px,4vw,38px); font-weight:1000; letter-spacing:.5px; text-align:center; }}
  .go {{ background:#2e7d32; color:#fff; }}
  .caution {{ background:#ffc107; color:#000; }}
  .nogo {{ background:#f44336; color:#fff; }}
  .overall.go, .overall.nogo {{ -webkit-text-stroke:2px #000; paint-order:stroke fill; text-shadow:0 2px 0 #000; }}
  .summary {{ margin-top:8px; opacity:.88; text-align:center; white-space:pre-wrap; }}
  .row {{ display:grid; grid-template-columns:150px 1fr 92px; gap:10px; align-items:start; border-top:1px solid #26364a; padding:9px 0; }}
  .row:first-child {{ border-top:0; }}
  .group {{ color:#9fb3c8; font-weight:800; }}
  .label {{ font-weight:850; }}
  .detail {{ opacity:.84; margin-top:2px; font-size:13px; overflow-wrap:anywhere; }}
  .pill {{ border:3px solid #000; border-radius:999px; padding:5px 8px; text-align:center; font-weight:950; font-size:12px; }}
  .pass {{ background:#2e7d32; color:#fff; }}
  .warn {{ background:#ffc107; color:#000; }}
  .fail {{ background:#f44336; color:#fff; }}
  .info {{ background:#455a64; color:#fff; }}
  .btn {{ display:inline-flex; align-items:center; justify-content:center; text-decoration:none; color:#fff; background:#455a64; border:3px solid #000; border-radius:14px; padding:11px 14px; font-weight:900; margin:4px; }}
  .btnBlue {{ background:#1565c0; }}
  .hint {{ opacity:.78; font-size:13px; line-height:1.35; }}
  @media (max-width:700px) {{
    .row {{ grid-template-columns:1fr; gap:4px; }}
    .pill {{ width:max-content; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card top">
    <div class="appVer">{APP_DISPLAY}</div>
    <div id="overall" class="overall caution">Running preflight check…</div>
    <div id="summary" class="summary">Running the GO / NO-GO report now…</div>
    <div style="margin-top:12px;">
      <a class="btn" href="{back_url}">⬅ Main HUD</a>
      <a class="btn btnBlue" href="/director{token_qs}">🎛️ Director</a>
      <a class="btn" href="/health{token_qs}">Heartbeat</a>
    </div>
  </div>
  <div class="card">
    <div class="hint">
      This page runs when opened from the main HUD. It is a read-only confidence check: it does not start/stop the stream, move cameras, or change OBS settings.
      <b>GO</b> means the important items look ready. <b>CAUTION</b> means the stream may still work but something deserves attention.
      <b>NO-GO</b> means an essential item needs correction before trusting the system.
    </div>
  </div>
  <div class="card" id="checks">Waiting for checks…</div>
</div>
<script src="{preflight_js_url}"></script>
</body>
</html>"""

    def _web_preflight_js(self) -> str:
        return r"""(function(){
  'use strict';
  var overallEl = document.getElementById('overall');
  var summaryEl = document.getElementById('summary');
  var checksEl = document.getElementById('checks');

  function esc(s){
    return (s == null ? '' : String(s)).replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  function clsForOverall(v){
    v = String(v || '').toUpperCase();
    if (v === 'GO') return 'overall go';
    if (v === 'NO-GO') return 'overall nogo';
    return 'overall caution';
  }

  function render(data){
    if (!data) return;
    if (overallEl) {
      overallEl.className = clsForOverall(data.overall);
      overallEl.textContent = data.title || data.overall || 'Health Report';
    }
    if (summaryEl) {
      summaryEl.textContent = (data.generated_ts || '') + '\n' + (data.summary || '');
    }
    if (!checksEl) return;
    var rows = [];
    var checks = data.checks || [];
    for (var i=0; i<checks.length; i++){
      var c = checks[i] || {};
      var st = String(c.status || 'info').toLowerCase();
      rows.push(
        '<div class="row">' +
          '<div class="group">' + esc(c.group || '') + '</div>' +
          '<div><div class="label">' + esc(c.label || c.key || '') + '</div>' +
          '<div class="detail">' + esc(c.detail || '') + '</div></div>' +
          '<div class="pill ' + esc(st) + '">' + esc(st.toUpperCase()) + '</div>' +
        '</div>'
      );
    }
    checksEl.innerHTML = rows.join('') || 'No checks returned.';
  }

  function load(){
    fetch('/api/health_report' + window.location.search, {cache:'no-store'})
      .then(function(r){ return r.json(); })
      .then(render)
      .catch(function(e){
        if (overallEl) { overallEl.className = 'overall nogo'; overallEl.textContent = 'NO-GO — Health report unavailable'; }
        if (summaryEl) summaryEl.textContent = String(e);
      });
  }

  load();
  setInterval(load, 5000);
})();"""

    # ----------------------------
    # WEB HUD — Config Editor pages (v8.0 prep)
    # ----------------------------
    def _web_config_html(self) -> str:
        # Separate page for general configuration (editable fields + read-only critical constants)
        return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Stream Agent Config</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; background:#0b1118; color:#e9eef5; }
  .wrap { max-width:700px; margin:0 auto; padding:14px; }
  .card { background:#121a24; border:3px solid #000; border-radius:16px; padding:14px; margin:10px 0; box-shadow: 0 8px 24px rgba(0,0,0,.35); }
  .appVer { font-size:12px; opacity:.75; text-align:center; letter-spacing:.4px; margin-bottom:6px; }
  .title { font-size:20px; font-weight:800; text-align:center; letter-spacing:.4px; }
  .sub { font-size:12px; opacity:.85; text-align:center; margin-top:4px; white-space:pre-wrap; }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  .btn { padding:12px 10px; border-radius:14px; border:3px solid #000; background:#0e1620; color:#fff; font-weight:800; cursor:pointer;
    -webkit-text-stroke: 2px #000; paint-order: stroke fill;
    text-shadow: -1px -1px 0 #000, 0 -1px 0 #000, 1px -1px 0 #000, -1px 0 0 #000, 1px 0 0 #000, -1px 1px 0 #000, 0 1px 0 #000, 1px 1px 0 #000;
    -webkit-touch-callout:none; -webkit-user-select:none; user-select:none; touch-action:manipulation; -webkit-tap-highlight-color: transparent;
  }
  .btn:active { transform: translateY(1px); }
  .bBack { background:#37474F; }
  .bApply { background:#2E7D32; }
  .bUnlock { background:#FF9800; }
  .bRestore { background:#F44336; }
  .bExport { background:#1565C0; }
  .bImport { background:#455A64; }

  .secTitle { font-size:14px; font-weight:900; opacity:.9; margin:0 0 10px; letter-spacing:.2px; }
  .item { padding:10px; border-radius:14px; border:1px solid #24354a; background:#0e1620; margin:8px 0; }
  .item.changed { border:2px solid #FFC107; }
  .item.readonly { opacity:.78; }
  .line { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; font-size:12px; opacity:.95; word-break:break-word; }
  .ctrl { margin-top:8px; display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .pill { font-size:11px; padding:4px 8px; border-radius:999px; border:1px solid #24354a; opacity:.85; }
  .pill.locked { border-color:#F44336; }
  .pill.unlocked { border-color:#FF9800; }
  .pill.live { border-color:#2E7D32; }

  .switch { position:relative; display:inline-block; width:56px; height:30px; }
  .switch input { opacity:0; width:0; height:0; }
  .slider { position:absolute; cursor:pointer; inset:0; background:#37474F; transition:.2s; border-radius:999px; border:2px solid #000; }
  .slider:before { position:absolute; content:""; height:22px; width:22px; left:3px; top:3px; background:#fff; transition:.2s; border-radius:50%; }
  input:checked + .slider { background:#2E7D32; }
  input:checked + .slider:before { transform:translateX(26px); }

  select, input[type="text"] { background:#0b1118; color:#e9eef5; border:2px solid #24354a; border-radius:10px; padding:10px; font-size:14px; }
  input[type="text"]{ width: min(520px, 100%); }

  .stepper { display:flex; gap:6px; align-items:center; }
  .stepBtn { padding:10px 12px; border-radius:12px; border:3px solid #000; background:#263238; color:#fff; font-weight:900; cursor:pointer; min-width:44px; text-align:center; }
  .stepVal { min-width:70px; text-align:center; padding:10px 10px; border-radius:12px; border:2px solid #24354a; background:#0b1118; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }

  .tinyBtn { padding:8px 10px; border-radius:12px; border:3px solid #000; background:#455A64; color:#fff; font-weight:900; cursor:pointer; }
  .tinyBtn:active { transform: translateY(1px); }

  .note { font-size:12px; opacity:.8; white-space:pre-wrap; margin-top:8px; }
  .footer { font-size:12px; opacity:.7; text-align:center; padding:10px 0 18px; }
  .hidden { display:none; }
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="appVer" id="appVer">__APP_VER__</div>
    <div class="title">Config</div>
    <div class="sub" id="statusSub">Loading...</div>
    <div class="row" style="justify-content:center; margin-top:10px;">
      <a class="btn bBack" id="btnBack" href="__BACK__">Back</a>
      <button class="btn bUnlock" id="btnUnlock">Unlock (2 min)</button>
      <button class="btn bApply" id="btnApply">Apply</button>
      <button class="btn bRestore" id="btnRestore">Restore Defaults</button>
    </div>
    <div class="row" style="justify-content:center; margin-top:10px;">
      <a class="btn bExport" id="btnExport" href="__EXPORT__" target="_blank" rel="noopener">Export</a>
      <label class="btn bImport" for="fileImport" style="display:inline-flex; align-items:center; justify-content:center;">Import</label>
      <input id="fileImport" class="hidden" type="file" accept="application/json"/>
    </div>
    <div class="note">Rules:
- While LIVE, editing is locked unless you temporarily Unlock.
- Critical constants (camera identity, tokens, host/port) are read-only here.</div>
  </div>

  <div id="cfgRoot"></div>

  <div class="footer">Stream Agent Config Editor</div>
</div>

<script src="/config.js?v=7"></script>
</body>
</html>
""".replace("__APP_VER__", APP_DISPLAY)\
   .replace("__BACK__", "/" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else ""))\
   .replace("__EXPORT__", "/api/config/export" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else ""))

    def _web_config_timer_html(self) -> str:
        # Separate page for TIMER AUTO-START adjustments + dedicated restore button.
        return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Stream Agent Timer Config</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; background:#0b1118; color:#e9eef5; }
  .wrap { max-width:700px; margin:0 auto; padding:14px; }
  .card { background:#121a24; border:3px solid #000; border-radius:16px; padding:14px; margin:10px 0; box-shadow: 0 8px 24px rgba(0,0,0,.35); }
  .appVer { font-size:12px; opacity:.75; text-align:center; letter-spacing:.4px; margin-bottom:6px; }
  .title { font-size:20px; font-weight:800; text-align:center; letter-spacing:.4px; }
  .sub { font-size:12px; opacity:.85; text-align:center; margin-top:4px; white-space:pre-wrap; }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; justify-content:center; }
  .btn { padding:12px 10px; border-radius:14px; border:3px solid #000; background:#0e1620; color:#fff; font-weight:800; cursor:pointer;
    -webkit-text-stroke: 2px #000; paint-order: stroke fill;
    text-shadow: -1px -1px 0 #000, 0 -1px 0 #000, 1px -1px 0 #000, -1px 0 0 #000, 1px 0 0 #000, -1px 1px 0 #000, 0 1px 0 #000, 1px 1px 0 #000;
    -webkit-touch-callout:none; -webkit-user-select:none; user-select:none; touch-action:manipulation; -webkit-tap-highlight-color: transparent;
  }
  .btn:active { transform: translateY(1px); }
  .bBack { background:#37474F; }
  .bApply { background:#2E7D32; }
  .bUnlock { background:#FF9800; }
  .bRestore { background:#F44336; }
  .secTitle { font-size:14px; font-weight:900; opacity:.9; margin:0 0 10px; letter-spacing:.2px; }
  .item { padding:10px; border-radius:14px; border:1px solid #24354a; background:#0e1620; margin:8px 0; }
  .item.changed { border:2px solid #FFC107; }
  .item.readonly { opacity:.78; }
  .line { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; font-size:12px; opacity:.95; word-break:break-word; }
  .ctrl { margin-top:8px; display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .switch { position:relative; display:inline-block; width:56px; height:30px; }
  .switch input { opacity:0; width:0; height:0; }
  .slider { position:absolute; cursor:pointer; inset:0; background:#37474F; transition:.2s; border-radius:999px; border:2px solid #000; }
  .slider:before { position:absolute; content:""; height:22px; width:22px; left:3px; top:3px; background:#fff; transition:.2s; border-radius:50%; }
  input:checked + .slider { background:#2E7D32; }
  input:checked + .slider:before { transform:translateX(26px); }
  select, input[type="text"] { background:#0b1118; color:#e9eef5; border:2px solid #24354a; border-radius:10px; padding:10px; font-size:14px; }
  input[type="text"]{ width: min(520px, 100%); }
  .stepper { display:flex; gap:6px; align-items:center; }
  .stepBtn { padding:10px 12px; border-radius:12px; border:3px solid #000; background:#263238; color:#fff; font-weight:900; cursor:pointer; min-width:44px; text-align:center; }
  .stepVal { min-width:70px; text-align:center; padding:10px 10px; border-radius:12px; border:2px solid #24354a; background:#0b1118; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }
  .note { font-size:12px; opacity:.8; white-space:pre-wrap; margin-top:8px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="appVer" id="appVer">__APP_VER__</div>
    <div class="title">Timer</div>
    <div class="sub" id="statusSub">Loading...</div>
    <div class="row" style="margin-top:10px;">
      <a class="btn bBack" id="btnBack" href="__BACK__">Back</a>
      <button class="btn bUnlock" id="btnUnlock">Unlock (2 min)</button>
      <button class="btn bApply" id="btnApply">Apply</button>
      <button class="btn bRestore" id="btnRestoreTimer">Restore TIMER Defaults</button>
    </div>
    <div class="note">Restore TIMER Defaults resets only the TIMER AUTO-START fields to their shipped Sunday defaults, without affecting other tuned settings.</div>
  </div>

  <div id="cfgRoot"></div>
</div>

<script src="/config.js?v=7"></script>
</body>
</html>
""".replace("__APP_VER__", APP_DISPLAY)\
   .replace("__BACK__", "/" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else ""))

    def _web_config_js(self) -> str:
        # Shared JS for /config and /config_timer
        return r"""(function(){
  'use strict';
  function $(id){ return document.getElementById(id); }

  function injectPrettyCss(){
    if (document.getElementById('cfgPrettyCss')) return;
    var st = document.createElement('style');
    st.id = 'cfgPrettyCss';
    st.textContent = `
      .wrap { max-width: 980px !important; }
      .cfgShell { margin-top: 10px; }
      .cfgMetaBar { display:flex; gap:8px; justify-content:center; flex-wrap:wrap; margin:8px 0 0; }
      .cfgSearchCard { background:linear-gradient(135deg,#111b27,#0f1722); border:3px solid #000; border-radius:18px; padding:12px; margin:10px 0; box-shadow:0 8px 24px rgba(0,0,0,.30); }
      .cfgSearchRow { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
      .cfgSearch { flex:1 1 260px; min-width:220px; background:#07111b !important; border:2px solid #31506d !important; border-radius:14px !important; color:#fff !important; padding:13px 14px !important; font-size:16px !important; }
      .cfgMini { font-size:12px; opacity:.78; margin-top:6px; line-height:1.35; }
      .cfgCatGrid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin:10px 0; }
      @media (max-width:720px){ .cfgCatGrid{grid-template-columns:repeat(2,minmax(0,1fr));} }
      @media (max-width:430px){ .cfgCatGrid{grid-template-columns:1fr;} }
      .cfgCatBtn { display:flex; gap:10px; align-items:center; text-align:left; background:#101a25; color:#e9eef5; border:3px solid #000; border-radius:18px; padding:12px; cursor:pointer; min-height:72px; box-shadow:0 8px 20px rgba(0,0,0,.22); }
      .cfgCatBtn:active { transform:translateY(1px); }
      .cfgCatBtn.active { background:linear-gradient(135deg,#173e5f,#102233); outline:2px solid #2f83c6; }
      .cfgIcon { font-size:26px; width:34px; text-align:center; filter:drop-shadow(0 2px 2px rgba(0,0,0,.45)); }
      .cfgCatName { font-weight:900; font-size:15px; letter-spacing:.2px; }
      .cfgCatDesc { font-size:11px; opacity:.78; line-height:1.2; margin-top:2px; }
      .cfgCount { margin-left:auto; font-size:12px; opacity:.85; border:1px solid #36536f; border-radius:999px; padding:3px 7px; }
      .categoryHero { background:linear-gradient(135deg,#172232,#101822); border:3px solid #000; border-radius:18px; padding:14px; margin:10px 0; box-shadow:0 8px 24px rgba(0,0,0,.30); }
      .categoryHeroTop { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
      .categoryHeroMain { display:flex; align-items:center; gap:10px; flex:1 1 280px; min-width:240px; }
      .categoryHeroActions { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; flex:1 1 260px; }
      .categoryHeroActions .tinyBtn { min-width:108px; padding:10px 12px; }
      .categoryMenuIntro { text-align:center; opacity:.88; padding:14px; line-height:1.4; }
      .fieldInput { background:#07111b !important; color:#e9eef5 !important; border:2px solid #31506d !important; border-radius:12px !important; padding:10px !important; font-size:16px !important; min-width:94px; max-width:190px; }
      .fieldInput.small { max-width:88px; min-width:72px; text-align:center; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
      .editHint { font-size:11px; opacity:.72; margin-left:4px; }
      .categoryTitle { font-size:22px; font-weight:950; letter-spacing:.2px; }
      .categorySub { font-size:13px; opacity:.82; margin-top:5px; line-height:1.35; }
      .settingsGrid { display:grid; grid-template-columns:1fr; gap:10px; }
      .midiMapGrid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin-top:10px; }
      @media (max-width:720px){ .midiMapGrid{grid-template-columns:1fr;} }
      .midiMapRow { background:#0b1420; border:1px solid #28415c; border-radius:12px; padding:9px 10px; display:grid; grid-template-columns:74px 1fr; gap:4px 10px; align-items:center; font-size:12px; }
      .midiMapRow b { color:#ffffff; }
      .midiMapRow span { opacity:.9; }
      .sectionBlock { background:#121a24; border:3px solid #000; border-radius:18px; padding:14px; margin:10px 0; box-shadow:0 8px 24px rgba(0,0,0,.30); }
      .sectionHead { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:8px; }
      .secTitle { margin:0 !important; font-size:15px !important; }
      .sectionHint { font-size:11px; opacity:.72; }
      .item.setting-card { border:2px solid #24354a; background:linear-gradient(135deg,#0d1722,#0a121b); border-radius:16px; padding:12px; margin:10px 0; }
      .item.setting-card.changed, .item.setting-card.staged { border-color:#FFC107; box-shadow:0 0 0 1px rgba(255,193,7,.25) inset; }
      .settingTop { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
      .settingName { font-size:15px; font-weight:900; line-height:1.2; }
      .settingKey { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:11px; opacity:.72; margin-top:3px; word-break:break-word; }
      .settingValue { margin-top:8px; padding:8px 10px; border-radius:12px; border:1px solid #203349; background:#07111b; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:12px; opacity:.95; word-break:break-word; }
      .badgeRow { display:flex; gap:5px; flex-wrap:wrap; justify-content:flex-end; }
      .badge { font-size:10px; padding:4px 7px; border-radius:999px; border:1px solid #37516b; opacity:.9; white-space:nowrap; }
      .badgeChanged { border-color:#FFC107; color:#FFE082; }
      .badgeStaged { border-color:#FF9800; color:#FFCC80; }
      .badgeLock { border-color:#F44336; color:#FFCDD2; }
      .badgeRead { border-color:#78909C; color:#CFD8DC; }
      .badgeLive { border-color:#2E7D32; color:#A5D6A7; }
      .ctrl { border-top:1px solid rgba(120,160,200,.15); padding-top:10px; }
      .ctrl button:disabled, .ctrl input:disabled, .ctrl select:disabled, .ctrl textarea:disabled { opacity:.45; cursor:not-allowed; }
      .emptyMsg { text-align:center; opacity:.75; padding:22px; border:2px dashed #26384f; border-radius:18px; margin:12px 0; }
      .presetGrid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; width:100%; }
      @media (max-width:620px){ .presetGrid{grid-template-columns:1fr;} }
      .presetDelayRow { display:flex; align-items:center; justify-content:space-between; gap:8px; background:#08121b; border:1px solid #22354a; border-radius:14px; padding:8px; flex-wrap:wrap; }
      .presetCoach { flex:1 1 230px; min-width:200px; font-size:11.5px; line-height:1.28; opacity:.86; color:#d8e7f5; }
      .presetCoach b { color:#ffffff; }
      .fieldResetRow { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-top:10px; }
      .fieldCoachText { flex:1 1 260px; min-width:220px; font-size:11.5px; line-height:1.28; opacity:.86; color:#d8e7f5; }
      .fieldCoachText b { color:#ffffff; }
      .helpBtn { padding:7px 10px; border-radius:10px; border:2px solid #000; background:#1565C0; color:#fff; font-weight:900; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; justify-content:center; }
      .helpBtn:active { transform: translateY(1px); }
      .quickBtns { display:flex; gap:6px; flex-wrap:wrap; margin-top:8px; }
      .chipBtn { padding:7px 9px; border-radius:10px; border:2px solid #000; background:#263238; color:#fff; font-weight:800; cursor:pointer; }
    `;
    document.head.appendChild(st);
  }
  injectPrettyCss();

  // Token handling (same scheme as the main HUD)
  function getToken(){
    try {
      var u = new URL(window.location.href);
      return u.searchParams.get('token') || '';
    } catch(e){ return ''; }
  }
  var TOKEN = getToken();
  function withToken(url){
    if (!TOKEN) return url;
    return url + (url.indexOf('?')>=0 ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN);
  }

  function api(url, opts){
    opts = opts || {};
    opts.headers = opts.headers || {};
    if (opts.body && !opts.headers['Content-Type']) opts.headers['Content-Type'] = 'application/json';
    return fetch(withToken(url), opts).then(function(r){
      return r.text().then(function(t){
        var j=null;
        try { j = t ? JSON.parse(t) : null; } catch(e) {}
        if (!r.ok) {
          var msg = (j && j.error) ? j.error : (t || ('HTTP '+r.status));
          var err = new Error(msg);
          err.status = r.status;
          throw err;
        }
        return j;
      });
    });
  }

  function isTimerPage(){
    return (window.location.pathname || '').indexOf('config_timer') >= 0;
  }

  var root = $('cfgRoot');
  var statusSub = $('statusSub');
  var btnUnlock = $('btnUnlock');
  var btnApply  = $('btnApply');
  var btnRestore = $('btnRestore');
  var btnRestoreTimer = $('btnRestoreTimer');
  var fileImport = $('fileImport');
  var btnExport = $('btnExport');

  var CATS = [
    { id:'service', label:'Service', icon:'⛪', desc:'stream start/stop, intro, recovery' },
    { id:'timer', label:'Timer Auto-Start', icon:'🕘', desc:'Sunday start time, timezone, grace window' },
    { id:'cameras', label:'Cameras', icon:'🎥', desc:'Axis cameras, routing, safe fallback' },
    { id:'delays', label:'Preset Delays', icon:'⏱️', desc:'blind-delay timing by service view' },
    { id:'audio', label:'Audio / Sync', icon:'🎚️', desc:'audio mode, master fader and sync controls' },
    { id:'director', label:'Director Preview', icon:'🖥️', desc:'tablet preview quality and freshness' },
    { id:'obs', label:'OBS / Scenes', icon:'🎬', desc:'OBS profile, scenes, source names' },
    { id:'midi', label:'MIDI / Proclaim', icon:'🎹', desc:'current and next-view note channels' },
    { id:'web', label:'Web HUD', icon:'📱', desc:'HUD behavior, logs, YouTube helper' },
    { id:'logs', label:'Logs / End', icon:'🧾', desc:'run logs and service-end sequence' },
    { id:'advanced', label:'Advanced', icon:'⚙️', desc:'environment and read-only constants' }
  ];
  var CAT_BY_ID = {};
  CATS.forEach(function(c){ CAT_BY_ID[c.id] = c; });

  function hashCategory(){
    try {
      var h = String(window.location.hash || '').replace(/^#/, '').trim();
      if (h && CAT_BY_ID[h]) return h;
    } catch(e) {}
    return '';
  }

  var FIELD_HELP = {
    'INTRO_SEQUENCE_ENABLED':'Play the Introduction scene automatically after stream start.',
    'OBS_INTRO_SCENE_NAME':'OBS scene that must be live before the intro media is restarted.',
    'OBS_INTRO_INPUT_NAME':'OBS media input/source name for the intro video.',
    'INTRO_END_SCENE_NAME':'Scene to cut to after the intro finishes.',
    'INTRO_POST_HOLD_SECONDS':'Extra pause after intro ended before cutting to the live scene.',
    'STOP_DELAY_SECONDS':'Countdown duration before OBS stop is sent.',
    'AUTO_RECOVER_ENABLED':'Allow the app to try restarting the stream after an unexpected stop.',
    'USE_TIMER_START':'Enable or disable the automatic Sunday service start timer.',
    'TIMER_START_HHMM':'Local 24-hour start time for the Sunday auto-start timer.',
    'TIMER_WEEKDAY':'Python weekday number: Monday=0 through Sunday=6.',
    'TIMER_FIRE_GRACE_MINUTES':'How many minutes after the target time the timer may still fire.',
    'OBS_PROFILE_CHECK_ENABLED':'Verify the correct OBS profile before starting the stream.',
    'OBS_EXPECTED_PROFILE_NAME':'The exact church OBS profile expected for live service streaming. Church default is NHLC with no trailing spaces.',
    'OBS_SCENE_WEST':'OBS scene used for the West Axis camera.',
    'OBS_SCENE_EAST':'OBS scene used for the East Axis camera.',
    'OBS_SAFE_FALLBACK_SCENE':'Safe scene used when a requested view cannot be routed cleanly.',
    'AUDIO_MODE':'Choose shared ASIO audio or embedded Axis camera audio.',
    'WEB_HUD_AUDIO_MASTER_ENABLED':'Show/control the Web HUD master fader.',
    'CONFIG_HELP_ENABLED':'Show extra coaching notes and Help buttons in config pages.',
    'SYNC_OFFSET_WEB_ENABLED':'Enable browser controls for ASIO sync offset when applicable.',
    'DIRECTOR_PREVIEW_BACKEND':'Where the director preview image comes from.',
    'DIRECTOR_PREVIEW_REFRESH_MS':'How often the director preview updates.',
    'MIDI_INPUT_PORT_SUBSTRING':'Text used to find the Proclaim MIDI input port.',
    'MIDI_CHANNEL_1_BASED':'Channel for current/immediate view requests.',
    'MIDI_NEXT_CHANNEL_1_BASED':'Channel for next expected view preparation. With the default map, Channel 2 Note 71 means “Next Panorama” and Channel 2 Note 74 means “Next Choir”.',
    'NOTE_PRESET_FIRST':'First note in the view range. The same note range is used on Channel 1 for immediate/current views and on Channel 2 for next/prep views.',
    'NOTE_PRESET_LAST':'Last note in the view range. Defaults 70–79 map to the 10 service views.',
    'PRESET_DELAYS_SECONDS':'Delay before cutting to a prepared camera view.',
    'AXIS_VIEW_PRESET_NAMES':'Exact Axis preset names. Preset names are case-sensitive.',
    'WEST_AXIS_IP':'West Axis camera IP address.',
    'EAST_AXIS_IP':'East Axis camera IP address.',
    'LOG_RETENTION_COUNT':'How many older run logs to keep.',
    'SERVICE_END_SEQUENCE_ENABLED':'Optional end-of-service copy/close/shutdown workflow.'
  };

  var FIELD_INLINE_COACH = {
    "HOME_TEST_MODE": "Bench-test mode. It simulates camera moves and suppresses some church-only actions; keep it off for normal service use.",
    "OBS_SCENE_INTRO": "OBS scene that contains the introduction video. The app cuts here before restarting the intro media.",
    "OBS_SCENE_WEST": "OBS Program scene used when the West camera is selected live.",
    "OBS_SCENE_EAST": "OBS Program scene used when the East camera is selected live.",
    "OBS_SAFE_FALLBACK_SCENE": "Safe scene used when routing cannot confidently provide the requested shot.",
    "FORCE_CUT_TRANSITION": "Forces app-directed camera switches to use Cut. This is safest for buffered RTSP camera sources.",
    "OBS_CUT_TRANSITION_NAME": "Exact OBS transition name used when a forced cut is requested. Usually Cut.",
    "OBS_VIDEO_INPUT_WEST": "OBS source/input name for the West camera video. Director previews and checks use this name.",
    "OBS_VIDEO_INPUT_EAST": "OBS source/input name for the East camera video. Director previews and checks use this name.",
    "AXIS_USERNAME": "Axis login username for camera control. Change only if camera accounts change.",
    "AXIS_PASSWORD": "Axis camera password. It is read-only in the Web HUD for safety.",
    "AXIS_USE_HTTPS": "Use HTTPS only if the Axis cameras are configured for it. The current church setup normally uses plain HTTP on the private camera LAN.",
    "AXIS_COMMAND_TIMEOUT_SECONDS": "How long Stream Agent waits for an Axis preset command before calling it a camera-control error.",
    "WEST_AXIS_IP": "Network address for the West Axis camera. A wrong IP means West presets and previews will fail.",
    "WEST_AXIS_CAMERA_ID": "Usually 1. Change only if this Axis unit exposes multiple internal camera channels.",
    "EAST_AXIS_IP": "Network address for the East Axis camera. A wrong IP means East presets and previews will fail.",
    "EAST_AXIS_CAMERA_ID": "Usually 1. Change only if this Axis unit exposes multiple internal camera channels.",
    "AXIS_VIEW_PRESET_NAMES": "Exact preset names stored inside both Axis cameras. These are case-sensitive; Podium and podium are different.",
    "WEST_CAMERA_ENABLED": "Disables West camera routing if the camera is unavailable. Useful as a temporary fault workaround.",
    "EAST_CAMERA_ENABLED": "Disables East camera routing if the camera is unavailable. Useful as a temporary fault workaround.",
    "INITIAL_CAMERA_VIEWS": "Startup assumption for where each camera is parked. This helps routing before the app has moved the cameras itself.",
    "AUDIO_MODE": "Selects the audio design: shared ASIO input or embedded Axis camera audio. This controls whether the Sync page is useful.",
    "OBS_AUDIO_INPUT_SHARED": "OBS input name for the single shared ASIO audio source, normally ASIO_audio.",
    "WEB_HUD_AUDIO_MASTER_ENABLED": "Shows the Web HUD audio master fader and allows browser volume control of the configured audio targets.",
    "AUDIO_MASTER_MIN_DB": "Lowest dB value available on the Web HUD audio fader.",
    "AUDIO_MASTER_MAX_DB": "Highest dB value available on the Web HUD audio fader. Keep conservative to avoid accidental overload.",
    "AUDIO_MASTER_DEFAULT_DB": "Initial master fader value used by the Web HUD when the app starts.",
    "AUTO_MINIMIZE_ENABLED": "Allows the small PC HUD window to minimize itself after startup or stream start.",
    "AUTO_MINIMIZE_AFTER_SECONDS": "Delay before the PC HUD auto-minimizes.",
    "MINIMIZE_ON_STARTUP": "Minimizes the PC HUD shortly after launching the app.",
    "AUTO_RESTORE_ON_ISSUE": "Reserved safety behavior for restoring the PC HUD if an issue occurs.",
    "AUTO_BRING_TO_FRONT_ON_STREAM_START": "Optional desktop behavior to bring the PC HUD forward when streaming starts.",
    "AUTO_RECOVER_ENABLED": "Allows the app to attempt a stream restart if OBS unexpectedly stops while streaming was desired.",
    "AUTO_RECOVER_MAX_ATTEMPTS": "Maximum restart attempts before the app pauses recovery.",
    "AUTO_RECOVER_BASE_DELAY_SECONDS": "Delay before the first recovery retry. Later retries can be longer.",
    "AUTO_RECOVER_BACKOFF_MULTIPLIER": "Multiplier that increases the delay between repeated recovery attempts.",
    "AUTO_RECOVER_COOLDOWN_SECONDS": "Pause time after maximum recovery attempts are reached.",
    "AUTO_RECOVER_START_GRACE_SECONDS": "Grace time after a start request before the app judges stream-off as a failure.",
    "UNEXPECTED_STOP_DEBOUNCE_SECONDS": "How long stream-off must persist before it is treated as an unexpected stop.",
    "LOG_TO_FILE_ENABLED": "Turns run log file writing on or off.",
    "LOG_RUN_FILE_PREFIX": "Filename prefix used for Stream Agent run and session logs.",
    "LOG_SEPARATE_SESSION_FILES": "Creates an additional per-stream session log for easier Sunday troubleshooting.",
    "LOG_DIR": "Optional folder for log files. Blank means use the app folder.",
    "LOG_RETENTION_COUNT": "How many older run logs to keep before cleanup removes extras.",
    "OBS_PROFILE_CHECK_ENABLED": "Checks the active OBS profile before starting, to reduce wrong stream-key/profile mistakes.",
    "OBS_EXPECTED_PROFILE_NAME": "The exact OBS profile name expected for normal church streaming. Church default is NHLC with no trailing spaces.",
    "OBS_PROFILE_MISMATCH_ACTION": "What to do when OBS is on the wrong profile: block, warn, or switch.",
    "OBS_PROFILE_SWITCH_GRACE_SECONDS": "Wait time after switching OBS profiles before trying to start streaming.",
    "INTRO_SEQUENCE_ENABLED": "Enables automatic Introduction scene playback after a successful stream start.",
    "OBS_INTRO_INPUT_NAME": "OBS media source/input name for the intro video.",
    "OBS_INTRO_SCENE_NAME": "OBS scene that must be live before the intro video is restarted.",
    "INTRO_END_SCENE_NAME": "Scene to cut to after the intro finishes or times out.",
    "INTRO_POLL_SECONDS": "How often the app checks the intro media state.",
    "INTRO_MAX_SECONDS": "Maximum time to wait for the intro before falling through to the live scene.",
    "INTRO_RESTART_GRACE_SECONDS": "Short wait after restarting the intro media before checking its state.",
    "INTRO_ENDED_GUARD_SECONDS": "Prevents a stale ENDED state from immediately skipping a freshly restarted intro.",
    "INTRO_FORCE_CUT_TRANSITION": "Uses the Cut transition for intro scene changes, independent of normal service transitions.",
    "INTRO_SCENE_SWITCH_RETRIES": "How many times to try confirming the Introduction scene is actually live.",
    "INTRO_SCENE_SWITCH_RETRY_SECONDS": "Delay between intro scene verification attempts.",
    "INTRO_ABORT_IF_SCENE_NOT_LIVE": "If enabled, skips the intro rather than playing the intro source off-air.",
    "INTRO_POST_HOLD_SECONDS": "Small hold after intro end before cutting to the live scene.",
    "INTRO_DISABLE_ON_END": "Legacy option for stacked intro-source designs. Leave off for the current scene-based intro workflow.",
    "STOP_DELAY_SECONDS": "Countdown time before OBS Stop Stream is sent. Helps avoid accidental immediate shutdown.",
    "START_DEBOUNCE_SECONDS": "Minimum spacing between repeated start requests to avoid double-start behavior.",
    "USE_TIMER_START": "Enables the automatic Sunday service start timer.",
    "TIMER_START_HHMM": "Local 24-hour time for the timer, such as 9:55.",
    "TIMER_WEEKDAY": "Day for the timer: Monday=0 through Sunday=6.",
    "TIMEZONE": "Timezone used by the timer. Regina should normally stay America/Regina.",
    "TIMER_PERSIST_STATE": "Stores whether the timer already fired today so it does not fire repeatedly.",
    "TIMER_STATE_FILE": "Small local file used to remember today’s timer fired/missed state.",
    "TIMER_FIRE_GRACE_MINUTES": "How many minutes after the scheduled time the timer is still allowed to start the stream.",
    "TZ_FALLBACK_MODE": "Fallback time mode used only if the named timezone cannot be loaded.",
    "TZ_FALLBACK_UTC_OFFSET_HOURS": "Fixed UTC offset used only by fallback time mode.",
    "WEB_HUD_ENABLED": "Enables the browser-based HUD served by Stream Agent.",
    "WEB_HUD_LOG_LINES": "Number of recent log lines shown in the Web HUD.",
    "CONFIG_HELP_ENABLED": "Shows coaching notes and Help buttons in config pages.",
    "DIRECTOR_PREVIEW_ENABLED": "Enables preview images on the Director page.",
    "DIRECTOR_PREVIEW_BACKEND": "Chooses whether previews come from OBS screenshots or directly from Axis snapshots/MJPEG.",
    "DIRECTOR_PREVIEW_REFRESH_MS": "How often Director preview images update.",
    "DIRECTOR_PREVIEW_WIDTH": "Requested preview image width.",
    "DIRECTOR_PREVIEW_HEIGHT": "Requested preview image height.",
    "DIRECTOR_PREVIEW_JPEG_QUALITY": "JPEG quality used for Director previews. Higher looks better but uses more bandwidth.",
    "DIRECTOR_PREVIEW_STALE_AFTER_MS": "How old a preview can be before the HUD marks it stale.",
    "YOUTUBE_LIVE_URL": "Public YouTube live URL opened by the View Live button.",
    "YOUTUBE_CHANNEL_ID": "YouTube channel ID used by the embedded viewer page.",
    "SYNC_OFFSET_WEB_ENABLED": "Enables Web HUD sync controls when shared ASIO audio mode is active.",
    "SYNC_OFFSET_STEP_MS": "Small sync-adjust step size in milliseconds.",
    "SYNC_OFFSET_COARSE_STEP_MS": "Large sync-adjust step size in milliseconds.",
    "SYNC_OFFSET_UNLOCK_SECONDS": "How long sync controls stay unlocked after pressing unlock.",
    "SYNC_OFFSET_MIN_MS": "Minimum allowed ASIO sync offset.",
    "SYNC_OFFSET_MAX_MS": "Maximum allowed ASIO sync offset.",
    "ENABLE_PRESET_DELAYS": "Master switch for blind delays. Leave enabled for normal service so off-air PTZ moves are hidden before the cut.",
    "PRESET_DELAYS_SECONDS": "Delay table for each service view. These values protect viewers from seeing camera motion.",
    "MIDI_INPUT_PORT_SUBSTRING": "Text used to find the Proclaim MIDI input port.",
    "MIDI_CHANNEL_1_BASED": "MIDI channel used for immediate/current view cues.",
    "MIDI_NEXT_CHANNEL_1_BASED": "MIDI channel used for next-view preparation cues.",
    "NOTE_START_STREAM": "MIDI note that requests Start Stream.",
    "NOTE_STOP_STREAM": "MIDI note that starts the stop countdown.",
    "NOTE_REC_TOGGLE": "MIDI note that toggles OBS recording.",
    "NOTE_PRESET_FIRST": "First MIDI note in the service-view range.",
    "NOTE_PRESET_LAST": "Last MIDI note in the service-view range.",
    "SERVICE_END_SEQUENCE_ENABLED": "Master switch for optional end-of-service copy/close/shutdown automation.",
    "MIDI_STOP_TRIGGERS_FULL_SEQUENCE": "Allows the MIDI stop cue to trigger the full service-end sequence.",
    "SERVICE_END_USB_ROOT": "Destination root folder for copied service logs/recordings.",
    "SERVICE_END_POST_STOP_WAIT_SECONDS": "Cooldown after OBS stops before service-end copy/close steps run.",
    "SERVICE_END_COPY_PREVIOUS_LOGS": "Also copy a small number of previous logs for troubleshooting context.",
    "SERVICE_END_COPY_TODAYS_MP4": "Copy today’s most recent MP4 recording if a recording path is configured.",
    "OBS_RECORDING_PATH": "Folder where OBS saves MP4 recordings, used by the service-end copy step.",
    "SERVICE_END_CLOSE_PROCLAIM": "Closes Proclaim during the optional service-end sequence.",
    "SERVICE_END_CLOSE_MASTER_FADER": "Closes Master Fader during the optional service-end sequence.",
    "SERVICE_END_CLOSE_OBS": "Closes OBS during the optional service-end sequence.",
    "SERVICE_END_WINDOWS_SHUTDOWN": "If enabled, Windows shutdown is started after service-end tasks.",
    "SERVICE_END_SHUTDOWN_DELAY_SECONDS": "Abort window before Windows shutdown completes.",
    "OBS_HOST": "OBS WebSocket host. Read-only in the HUD to avoid disconnecting control mid-service.",
    "OBS_PORT": "OBS WebSocket port. Read-only in the HUD to avoid disconnecting control mid-service.",
    "OBS_PASSWORD": "OBS WebSocket password. Hidden from browser editing for safety.",
    "WEB_HUD_HOST": "Network bind address for the Web HUD. Read-only here to avoid stranding the page.",
    "WEB_HUD_PORT": "Web HUD port. Read-only here because changing it would move the page you are using.",
    "WEB_HUD_TOKEN": "Optional Web HUD access token. Read-only in the browser for safety."
};

  var FIELD_HELP_URLS = {
    "HOME_TEST_MODE": "/help/config#home-test-mode",
    "OBS_SCENE_INTRO": "/help/config#obs-scene-intro",
    "OBS_SCENE_WEST": "/help/config#obs-scene-west",
    "OBS_SCENE_EAST": "/help/config#obs-scene-east",
    "OBS_SAFE_FALLBACK_SCENE": "/help/config#obs-safe-fallback-scene",
    "FORCE_CUT_TRANSITION": "/help/config#force-cut-transition",
    "OBS_CUT_TRANSITION_NAME": "/help/config#obs-cut-transition-name",
    "OBS_VIDEO_INPUT_WEST": "/help/config#obs-video-input-west",
    "OBS_VIDEO_INPUT_EAST": "/help/config#obs-video-input-east",
    "AXIS_USERNAME": "/help/config#axis-username",
    "AXIS_PASSWORD": "/help/config#axis-password",
    "AXIS_USE_HTTPS": "/help/cameras#axis-use-https",
    "AXIS_COMMAND_TIMEOUT_SECONDS": "/help/cameras#axis-command-timeout-seconds",
    "WEST_AXIS_IP": "/help/cameras#west-axis-ip",
    "WEST_AXIS_CAMERA_ID": "/help/cameras#west-axis-camera-id",
    "EAST_AXIS_IP": "/help/cameras#east-axis-ip",
    "EAST_AXIS_CAMERA_ID": "/help/cameras#east-axis-camera-id",
    "AXIS_VIEW_PRESET_NAMES": "/help/cameras#axis-view-preset-names",
    "WEST_CAMERA_ENABLED": "/help/cameras#west-camera-enabled",
    "EAST_CAMERA_ENABLED": "/help/cameras#east-camera-enabled",
    "INITIAL_CAMERA_VIEWS": "/help/cameras#initial-camera-views",
    "AUDIO_MODE": "/help/config#audio-mode",
    "OBS_AUDIO_INPUT_SHARED": "/help/config#obs-audio-input-shared",
    "WEB_HUD_AUDIO_MASTER_ENABLED": "/help/config#web-hud-audio-master-enabled",
    "AUDIO_MASTER_MIN_DB": "/help/config#audio-master-min-db",
    "AUDIO_MASTER_MAX_DB": "/help/config#audio-master-max-db",
    "AUDIO_MASTER_DEFAULT_DB": "/help/config#audio-master-default-db",
    "AUTO_MINIMIZE_ENABLED": "/help/config#auto-minimize-enabled",
    "AUTO_MINIMIZE_AFTER_SECONDS": "/help/config#auto-minimize-after-seconds",
    "MINIMIZE_ON_STARTUP": "/help/config#minimize-on-startup",
    "AUTO_RESTORE_ON_ISSUE": "/help/config#auto-restore-on-issue",
    "AUTO_BRING_TO_FRONT_ON_STREAM_START": "/help/config#auto-bring-to-front-on-stream-start",
    "AUTO_RECOVER_ENABLED": "/help/config#auto-recover-enabled",
    "AUTO_RECOVER_MAX_ATTEMPTS": "/help/config#auto-recover-max-attempts",
    "AUTO_RECOVER_BASE_DELAY_SECONDS": "/help/config#auto-recover-base-delay-seconds",
    "AUTO_RECOVER_BACKOFF_MULTIPLIER": "/help/config#auto-recover-backoff-multiplier",
    "AUTO_RECOVER_COOLDOWN_SECONDS": "/help/config#auto-recover-cooldown-seconds",
    "AUTO_RECOVER_START_GRACE_SECONDS": "/help/config#auto-recover-start-grace-seconds",
    "UNEXPECTED_STOP_DEBOUNCE_SECONDS": "/help/config#unexpected-stop-debounce-seconds",
    "LOG_TO_FILE_ENABLED": "/help/config#log-to-file-enabled",
    "LOG_RUN_FILE_PREFIX": "/help/config#log-run-file-prefix",
    "LOG_SEPARATE_SESSION_FILES": "/help/config#log-separate-session-files",
    "LOG_DIR": "/help/config#log-dir",
    "LOG_RETENTION_COUNT": "/help/config#log-retention-count",
    "OBS_PROFILE_CHECK_ENABLED": "/help/config#obs-profile-check-enabled",
    "OBS_EXPECTED_PROFILE_NAME": "/help/config#obs-expected-profile-name",
    "OBS_PROFILE_MISMATCH_ACTION": "/help/config#obs-profile-mismatch-action",
    "OBS_PROFILE_SWITCH_GRACE_SECONDS": "/help/config#obs-profile-switch-grace-seconds",
    "INTRO_SEQUENCE_ENABLED": "/help/config#intro-sequence-enabled",
    "OBS_INTRO_INPUT_NAME": "/help/config#obs-intro-input-name",
    "OBS_INTRO_SCENE_NAME": "/help/config#obs-intro-scene-name",
    "INTRO_END_SCENE_NAME": "/help/config#intro-end-scene-name",
    "INTRO_POLL_SECONDS": "/help/config#intro-poll-seconds",
    "INTRO_MAX_SECONDS": "/help/config#intro-max-seconds",
    "INTRO_RESTART_GRACE_SECONDS": "/help/config#intro-restart-grace-seconds",
    "INTRO_ENDED_GUARD_SECONDS": "/help/config#intro-ended-guard-seconds",
    "INTRO_FORCE_CUT_TRANSITION": "/help/config#intro-force-cut-transition",
    "INTRO_SCENE_SWITCH_RETRIES": "/help/config#intro-scene-switch-retries",
    "INTRO_SCENE_SWITCH_RETRY_SECONDS": "/help/config#intro-scene-switch-retry-seconds",
    "INTRO_ABORT_IF_SCENE_NOT_LIVE": "/help/config#intro-abort-if-scene-not-live",
    "INTRO_POST_HOLD_SECONDS": "/help/config#intro-post-hold-seconds",
    "INTRO_DISABLE_ON_END": "/help/config#intro-disable-on-end",
    "STOP_DELAY_SECONDS": "/help/config#stop-delay-seconds",
    "START_DEBOUNCE_SECONDS": "/help/config#start-debounce-seconds",
    "USE_TIMER_START": "/help/config#use-timer-start",
    "TIMER_START_HHMM": "/help/config#timer-start-hhmm",
    "TIMER_WEEKDAY": "/help/config#timer-weekday",
    "TIMEZONE": "/help/config#timezone",
    "TIMER_PERSIST_STATE": "/help/config#timer-persist-state",
    "TIMER_STATE_FILE": "/help/config#timer-state-file",
    "TIMER_FIRE_GRACE_MINUTES": "/help/config#timer-fire-grace-minutes",
    "TZ_FALLBACK_MODE": "/help/config#tz-fallback-mode",
    "TZ_FALLBACK_UTC_OFFSET_HOURS": "/help/config#tz-fallback-utc-offset-hours",
    "WEB_HUD_ENABLED": "/help/config#web-hud-enabled",
    "WEB_HUD_LOG_LINES": "/help/config#web-hud-log-lines",
    "CONFIG_HELP_ENABLED": "/help/config#config-help-enabled",
    "DIRECTOR_PREVIEW_ENABLED": "/help/config#director-preview-enabled",
    "DIRECTOR_PREVIEW_BACKEND": "/help/config#director-preview-backend",
    "DIRECTOR_PREVIEW_REFRESH_MS": "/help/config#director-preview-refresh-ms",
    "DIRECTOR_PREVIEW_WIDTH": "/help/config#director-preview-width",
    "DIRECTOR_PREVIEW_HEIGHT": "/help/config#director-preview-height",
    "DIRECTOR_PREVIEW_JPEG_QUALITY": "/help/config#director-preview-jpeg-quality",
    "DIRECTOR_PREVIEW_STALE_AFTER_MS": "/help/config#director-preview-stale-after-ms",
    "YOUTUBE_LIVE_URL": "/help/config#youtube-live-url",
    "YOUTUBE_CHANNEL_ID": "/help/config#youtube-channel-id",
    "SYNC_OFFSET_WEB_ENABLED": "/help/config#sync-offset-web-enabled",
    "SYNC_OFFSET_STEP_MS": "/help/config#sync-offset-step-ms",
    "SYNC_OFFSET_COARSE_STEP_MS": "/help/config#sync-offset-coarse-step-ms",
    "SYNC_OFFSET_UNLOCK_SECONDS": "/help/config#sync-offset-unlock-seconds",
    "SYNC_OFFSET_MIN_MS": "/help/config#sync-offset-min-ms",
    "SYNC_OFFSET_MAX_MS": "/help/config#sync-offset-max-ms",
    "ENABLE_PRESET_DELAYS": "/help/preset_delays#enable-preset-delays",
    "PRESET_DELAYS_SECONDS": "/help/preset_delays",
    "MIDI_INPUT_PORT_SUBSTRING": "/help/config#midi-input-port-substring",
    "MIDI_CHANNEL_1_BASED": "/help/config#midi-channel-1-based",
    "MIDI_NEXT_CHANNEL_1_BASED": "/help/config#midi-next-channel-1-based",
    "NOTE_START_STREAM": "/help/config#note-start-stream",
    "NOTE_STOP_STREAM": "/help/config#note-stop-stream",
    "NOTE_REC_TOGGLE": "/help/config#note-rec-toggle",
    "NOTE_PRESET_FIRST": "/help/config#note-preset-first",
    "NOTE_PRESET_LAST": "/help/config#note-preset-last",
    "SERVICE_END_SEQUENCE_ENABLED": "/help/config#service-end-sequence-enabled",
    "MIDI_STOP_TRIGGERS_FULL_SEQUENCE": "/help/config#midi-stop-triggers-full-sequence",
    "SERVICE_END_USB_ROOT": "/help/config#service-end-usb-root",
    "SERVICE_END_POST_STOP_WAIT_SECONDS": "/help/config#service-end-post-stop-wait-seconds",
    "SERVICE_END_COPY_PREVIOUS_LOGS": "/help/config#service-end-copy-previous-logs",
    "SERVICE_END_COPY_TODAYS_MP4": "/help/config#service-end-copy-todays-mp4",
    "OBS_RECORDING_PATH": "/help/config#obs-recording-path",
    "SERVICE_END_CLOSE_PROCLAIM": "/help/config#service-end-close-proclaim",
    "SERVICE_END_CLOSE_MASTER_FADER": "/help/config#service-end-close-master-fader",
    "SERVICE_END_CLOSE_OBS": "/help/config#service-end-close-obs",
    "SERVICE_END_WINDOWS_SHUTDOWN": "/help/config#service-end-windows-shutdown",
    "SERVICE_END_SHUTDOWN_DELAY_SECONDS": "/help/config#service-end-shutdown-delay-seconds",
    "OBS_HOST": "/help/config#obs-host",
    "OBS_PORT": "/help/config#obs-port",
    "OBS_PASSWORD": "/help/config#obs-password",
    "WEB_HUD_HOST": "/help/config#web-hud-host",
    "WEB_HUD_PORT": "/help/config#web-hud-port",
    "WEB_HUD_TOKEN": "/help/config#web-hud-token"
};


  var PRESET_DELAY_HELP = {
    '1':'Pulpit often needs a longer blind delay so the off-air camera can finish moving before the scene cut.',
    '2':'Panorama is normally safe immediately because it is the fallback/home-style wide view.',
    '3':'Children’s Time often involves a larger PTZ move; delay keeps the camera motion hidden.',
    '4':'Altar usually benefits from a short pause so the camera settles before going live.',
    '5':'Choir often needs enough time for tilt/zoom to settle before the cut.',
    '6':'Screen is usually a prepared/static view, so little or no delay is normally needed.',
    '7':'Band is usually safe with little delay if the preset is close to the current position.',
    '8':'Piano is usually safe with little delay if the camera is already near that area.',
    '9':'Communion can involve a larger move; a delay avoids showing PTZ motion live.',
    '10':'Podium is often a prepared view; use more delay only if you see the camera still moving.'
  };

  var initialHashCat = hashCategory();
  var state = {
    snapshot: null,
    edited: {},
    dirty: false,
    lastTap: {},
    activeCat: initialHashCat || localStorage.getItem('sa_cfg_active_cat') || 'service',
    search: '',
    view: isTimerPage() ? 'category' : (initialHashCat ? 'category' : 'menu'),
    visibleKeys: []
  };
  var searchDrawTimer = null;

  function focusSearchInput(caretPos){
    try {
      var el = document.getElementById('cfgSearchInput');
      if (!el) return;
      el.focus();
      var pos = (typeof caretPos === 'number') ? caretPos : String(el.value || '').length;
      el.selectionStart = el.selectionEnd = Math.max(0, Math.min(String(el.value || '').length, pos));
    } catch(e) {}
  }

  function scheduleSearchDraw(caretPos){
    if (searchDrawTimer) clearTimeout(searchDrawTimer);
    searchDrawTimer = setTimeout(function(){
      searchDrawTimer = null;
      draw();
      setTimeout(function(){ focusSearchInput(caretPos); }, 0);
    }, 120);
  }

  function setStatus(t){ if (statusSub) statusSub.textContent = t; }
  function hasOwn(o,k){ return Object.prototype.hasOwnProperty.call(o,k); }
  function clone(v){ try { return JSON.parse(JSON.stringify(v)); } catch(e){ return v; } }

  function tapConfirm(key, btn, label1, label2, action){
    var now = Date.now();
    var lt = state.lastTap[key] || 0;
    if (now - lt < 2000) {
      state.lastTap[key] = 0;
      if (btn) btn.textContent = label1;
      action();
      return;
    }
    state.lastTap[key] = now;
    if (btn) btn.textContent = label2;
    setTimeout(function(){
      if (state.lastTap[key] === now) {
        state.lastTap[key] = 0;
        if (btn) btn.textContent = label1;
      }
    }, 2200);
  }

  function fmtBool(v){ return v ? 'True' : 'False'; }
  function roundToStep(x, step){ if (!step) return x; var inv = 1/step; return Math.round(x*inv)/inv; }
  function humanName(key){
    var acronyms = {'OBS':'OBS','HUD':'HUD','MIDI':'MIDI','ASIO':'ASIO','AXIS':'Axis','RTSP':'RTSP','USB':'USB','URL':'URL','IP':'IP','DB':'dB'};
    return String(key || '').split('_').map(function(part){
      if (acronyms[part]) return acronyms[part];
      var low = part.toLowerCase();
      if (low === 'hhmm') return 'HH:MM';
      if (low === 'cfg') return 'Config';
      return low.charAt(0).toUpperCase() + low.slice(1);
    }).join(' ');
  }
  function valueSummary(v){
    if (v === true || v === false) return fmtBool(v);
    if (v === null || v === undefined) return '';
    if (typeof v === 'string') return JSON.stringify(v);
    try { return JSON.stringify(v); } catch(e){ return String(v); }
  }
  function displayLine(item, val){
    if (item.kind === 'bool') return item.key + ': bool = ' + fmtBool(!!val);
    if (item.kind === 'int') return item.key + ': int = ' + String(parseInt(val,10) || 0);
    if (item.kind === 'float') {
      var n = parseFloat(val); if (isNaN(n)) n = 0;
      var shown = Math.abs(n - Math.round(n)) < 1e-9 ? n.toFixed(1) : String(n);
      return item.key + ': float = ' + shown;
    }
    if (item.kind === 'str' || item.kind === 'enum') return item.key + ': str = ' + JSON.stringify(val || '');
    if (item.kind === 'json') return item.key + ': json = ' + valueSummary(val);
    if (item.kind === 'preset_delays') return item.key + ': Dict = ' + valueSummary(val);
    return item.display || (item.key + ' = ' + valueSummary(val));
  }

  function categoryForSection(title){
    var t = String(title || '').toUpperCase();
    if (isTimerPage() || t.indexOf('TIMER') >= 0) return 'timer';
    if (t.indexOf('INTRO') >= 0 || t.indexOf('STREAM SAFETY') >= 0 || t.indexOf('AUTO-RECOVERY') >= 0) return 'service';
    if (t.indexOf('AXIS CAMERA') >= 0 || t.indexOf('VIEW ROUTING') >= 0) return 'cameras';
    if (t.indexOf('PRESET DELAYS') >= 0) return 'delays';
    if (t.indexOf('AUDIO') >= 0 || t.indexOf('SYNC') >= 0) return 'audio';
    if (t.indexOf('DIRECTOR') >= 0) return 'director';
    if (t.indexOf('OBS SCENE') >= 0 || t.indexOf('OBS PROFILE') >= 0) return 'obs';
    if (t.indexOf('MIDI') >= 0) return 'midi';
    if (t === 'WEB HUD' || t.indexOf('YOUTUBE') >= 0 || t.indexOf('PC HUD') >= 0) return 'web';
    if (t.indexOf('LOGGING') >= 0 || t.indexOf('SERVICE-END') >= 0) return 'logs';
    return 'advanced';
  }

  function buildCategories(snapshot){
    var cats = {};
    CATS.forEach(function(c){ cats[c.id] = { meta:c, sections:[], count:0 }; });
    (snapshot.sections || []).forEach(function(sec){
      var id = categoryForSection(sec.title);
      if (!cats[id]) id = 'advanced';
      cats[id].sections.push(sec);
      cats[id].count += (sec.items || []).length;
    });
    return cats;
  }

  function findItemByKey(snapshot, key){
    var secs = (snapshot && snapshot.sections) ? snapshot.sections : [];
    for (var i=0; i<secs.length; i++){
      var items = secs[i].items || [];
      for (var j=0; j<items.length; j++){
        if (items[j] && items[j].key === key) return items[j];
      }
    }
    return null;
  }

  function cfgValue(snapshot, key, fallback){
    var item = findItemByKey(snapshot, key);
    if (!item) return fallback;
    return hasOwn(state.edited, key) ? state.edited[key] : item.value;
  }

  function configHelpEnabled(){
    return !!cfgValue(state.snapshot, 'CONFIG_HELP_ENABLED', true);
  }

  function configHelpUrl(path){
    return withToken(path || '/help/preset_delays');
  }

  function makeMidiMapCard(snapshot){
    var labels = ((snapshot.meta || {}).preset_labels || {});
    var first = parseInt(cfgValue(snapshot, 'NOTE_PRESET_FIRST', 70), 10);
    var last = parseInt(cfgValue(snapshot, 'NOTE_PRESET_LAST', 79), 10);
    var chNow = parseInt(cfgValue(snapshot, 'MIDI_CHANNEL_1_BASED', 1), 10);
    var chNext = parseInt(cfgValue(snapshot, 'MIDI_NEXT_CHANNEL_1_BASED', 2), 10);
    if (isNaN(first)) first = 70;
    if (isNaN(last)) last = first + 9;
    if (isNaN(chNow)) chNow = 1;
    if (isNaN(chNext)) chNext = 2;

    var card = document.createElement('div');
    card.className = 'sectionBlock midiMapCard';
    var head = document.createElement('div'); head.className='sectionHead';
    var title = document.createElement('div'); title.className='secTitle'; title.textContent='MIDI View Note Map';
    var hint = document.createElement('div'); hint.className='sectionHint'; hint.textContent='same notes, different channels';
    head.appendChild(title); head.appendChild(hint); card.appendChild(head);

    var note = document.createElement('div'); note.className='note';
    note.textContent = 'Channel ' + chNow + ' recalls the immediate/current view. Channel ' + chNext + ' prepares the NEXT expected view off-air. Example: Note ' + (first + 1) + ' on Channel ' + chNext + ' = Next Panorama.';
    card.appendChild(note);

    var grid = document.createElement('div'); grid.className='midiMapGrid';
    for (var noteNum = first; noteNum <= last; noteNum++){
      var viewNum = noteNum - first + 1;
      var lbl = labels[String(viewNum)] || ('Preset ' + viewNum);
      var row = document.createElement('div'); row.className='midiMapRow';
      row.innerHTML = '<b>Note ' + noteNum + '</b><span>Ch ' + chNow + ': ' + lbl + '</span><span>Ch ' + chNext + ': Next ' + lbl + '</span>';
      grid.appendChild(row);
    }
    card.appendChild(grid);
    return card;
  }

  function itemMatches(item, sectionTitle, q){
    if (!q) return true;
    var hay = [item.key, item.display, humanName(item.key), sectionTitle, FIELD_HELP[item.key] || '', valueSummary(item.value)].join(' ').toLowerCase();
    return hay.indexOf(q.toLowerCase()) >= 0;
  }

  function makeBadge(text, cls){
    var b = document.createElement('span');
    b.className = 'badge ' + (cls || '');
    b.textContent = text;
    return b;
  }

  function currentVal(item){ return hasOwn(state.edited, item.key) ? state.edited[item.key] : item.value; }

  function stableValue(v){
    if (v === null || v === undefined) return v;
    if (Array.isArray(v)) return v.map(stableValue);
    if (typeof v === 'object') {
      var out = {};
      Object.keys(v).sort().forEach(function(k){ out[k] = stableValue(v[k]); });
      return out;
    }
    return v;
  }

  function normalizeValue(item, v){
    if (!item) return v;
    if (item.kind === 'bool') return !!v;
    if (item.kind === 'int') {
      var iv = parseInt(v, 10);
      return isNaN(iv) ? 0 : iv;
    }
    if (item.kind === 'float') {
      var fv = parseFloat(v);
      if (isNaN(fv)) fv = 0;
      return fv;
    }
    if (item.kind === 'enum' || item.kind === 'str') return String(v == null ? '' : v);
    if (item.kind === 'preset_delays') {
      var d = {};
      var obj = v || {};
      for (var k in obj) if (hasOwn(obj,k)) {
        var n = parseInt(obj[k], 10);
        if (isNaN(n)) n = 0;
        d[String(parseInt(k,10))] = Math.max(0, Math.min(600, n));
      }
      return d;
    }
    if (item.kind === 'json') {
      if (typeof v === 'string') {
        try { return JSON.parse(v); } catch(e) { return v; }
      }
      return v;
    }
    return v;
  }

  function valuesEqual(item, a, b){
    try {
      return JSON.stringify(stableValue(normalizeValue(item, a))) === JSON.stringify(stableValue(normalizeValue(item, b)));
    } catch(e) {
      return String(a) === String(b);
    }
  }

  function recomputeDirty(){
    state.dirty = Object.keys(state.edited || {}).length > 0;
    return state.dirty;
  }

  function refreshActionButtons(){
    var meta = (state.snapshot && state.snapshot.meta) ? state.snapshot.meta : {};
    var locked = !!meta.locked;
    recomputeDirty();
    if (btnUnlock) btnUnlock.style.display = (meta.streaming ? 'inline-block' : 'none');
    if (btnUnlock) btnUnlock.disabled = (!meta.streaming);
    if (btnApply) btnApply.disabled = locked || !state.dirty;
    if (btnRestore) btnRestore.disabled = (locked || !!meta.streaming);
    if (btnRestoreTimer) btnRestoreTimer.disabled = (locked || !!meta.streaming);
  }

  function refreshStatusText(){
    var meta = (state.snapshot && state.snapshot.meta) ? state.snapshot.meta : {};
    var locked = !!meta.locked;
    var status = [];
    status.push(meta.streaming ? 'LIVE: yes' : 'LIVE: no');
    status.push(locked ? 'EDIT: locked' : 'EDIT: unlocked');
    if (state.dirty) status.push('pending changes: ' + Object.keys(state.edited).length);
    if (meta.unlock_remaining_s && meta.unlock_remaining_s > 0) status.push('unlock remaining: ' + Math.ceil(meta.unlock_remaining_s) + 's');
    setStatus(status.join('   |   '));
    refreshActionButtons();
  }

  function stageValue(item, value, redraw){
    if (!item || item.readonly) return;
    if (valuesEqual(item, value, item.value)) {
      if (hasOwn(state.edited, item.key)) delete state.edited[item.key];
    } else {
      if (item.kind === 'preset_delays') state.edited[item.key] = normalizeValue(item, value);
      else if (item.kind === 'int' || item.kind === 'float' || item.kind === 'bool') state.edited[item.key] = normalizeValue(item, value);
      else state.edited[item.key] = value;
    }
    recomputeDirty();
    refreshStatusText();
    if (redraw) draw();
  }

  function collectEditableKeys(sections, q){
    var keys = [];
    (sections || []).forEach(function(sec){
      (sec.items || []).forEach(function(item){
        if (!item || item.readonly) return;
        if (!itemMatches(item, sec.title, q || '')) return;
        if (keys.indexOf(item.key) < 0) keys.push(item.key);
      });
    });
    return keys;
  }


  function makeItemEl(item, locked){
    var staged = hasOwn(state.edited, item.key);
    var valForDisplay = currentVal(item);
    var div = document.createElement('div');
    div.className = 'item setting-card' + (item.changed ? ' changed' : '') + (item.readonly ? ' readonly' : '') + (staged ? ' staged' : '');

    var top = document.createElement('div');
    top.className = 'settingTop';
    var left = document.createElement('div');
    var name = document.createElement('div');
    name.className = 'settingName';
    name.textContent = humanName(item.key);
    var key = document.createElement('div');
    key.className = 'settingKey';
    key.textContent = item.key;
    left.appendChild(name); left.appendChild(key);

    var badges = document.createElement('div');
    badges.className = 'badgeRow';
    if (staged) badges.appendChild(makeBadge('staged', 'badgeStaged'));
    if (item.changed) badges.appendChild(makeBadge('changed', 'badgeChanged'));
    if (item.readonly) badges.appendChild(makeBadge('read-only', 'badgeRead'));
    if (locked || item.locked) badges.appendChild(makeBadge('locked', 'badgeLock'));
    if (!item.readonly && !item.locked && !locked) badges.appendChild(makeBadge('editable', 'badgeLive'));
    top.appendChild(left); top.appendChild(badges);
    div.appendChild(top);

    var line = document.createElement('div');
    line.className = 'settingValue';
    line.textContent = displayLine(item, valForDisplay);
    div.appendChild(line);

    var help = FIELD_HELP[item.key] || item.note || '';
    if (help) {
      var n = document.createElement('div');
      n.className = 'note';
      n.textContent = help;
      div.appendChild(n);
    }

    if (item.readonly) return div;

    var ctrl = document.createElement('div');
    ctrl.className = 'ctrl';
    var disabled = !!locked;

    if (item.kind === 'bool'){
      var label = document.createElement('label');
      label.className = 'switch';
      var inp = document.createElement('input');
      inp.type = 'checkbox';
      inp.checked = !!valForDisplay;
      inp.disabled = disabled;
      var slider = document.createElement('span');
      slider.className = 'slider';
      label.appendChild(inp); label.appendChild(slider);
      ctrl.appendChild(label);
      var txt = document.createElement('span');
      txt.className = 'pill';
      txt.textContent = fmtBool(inp.checked);
      ctrl.appendChild(txt);
      inp.addEventListener('change', function(){
        txt.textContent = fmtBool(inp.checked);
        line.textContent = displayLine(item, !!inp.checked);
        stageValue(item, !!inp.checked, true);
      });
    }
    else if (item.kind === 'enum'){
      var sel = document.createElement('select');
      sel.disabled = disabled;
      (item.options || []).forEach(function(opt){
        var o = document.createElement('option');
        o.value = opt; o.textContent = opt;
        if (opt === valForDisplay) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener('change', function(){ stageValue(item, sel.value, true); });
      ctrl.appendChild(sel);
    }
    else if (item.kind === 'preset_delays'){
      var cur = normalizeValue(item, clone(valForDisplay || {}));
      var def = normalizeValue(item, item.default || {});
      var holder = document.createElement('div');
      holder.className = 'presetGrid';
      var keys = [];
      var keySource = Object.keys(cur).length ? cur : def;
      for (var k in keySource) if (hasOwn(keySource,k)) keys.push(k);
      keys.sort(function(a,b){ return parseInt(a,10) - parseInt(b,10); });
      function setAllFromCur(redraw){
        line.textContent = displayLine(item, cur);
        stageValue(item, cur, redraw);
      }
      keys.forEach(function(k){
        var row = document.createElement('div');
        row.className = 'presetDelayRow';
        var label = document.createElement('div');
        label.className = 'pill';
        var lbl = '';
        try {
          var pl = (state.snapshot && state.snapshot.meta && state.snapshot.meta.preset_labels) ? state.snapshot.meta.preset_labels : {};
          lbl = (pl && pl[k]) ? pl[k] : '';
        } catch(e) { lbl = ''; }
        label.textContent = k + (lbl ? (' — ' + lbl) : '');
        row.appendChild(label);
        var stepper = document.createElement('div');
        stepper.className = 'stepper';
        var down = document.createElement('button'); down.className='stepBtn'; down.textContent='▼'; down.disabled=disabled;
        var val = document.createElement('input'); val.className='fieldInput small'; val.type='number'; val.min='0'; val.max='600'; val.step='1'; val.value=''+(cur[k]!=null?cur[k]:0); val.disabled=disabled;
        var up = document.createElement('button'); up.className='stepBtn'; up.textContent='▲'; up.disabled=disabled;
        function setK(newV, redraw){
          newV = Math.max(0, Math.min(600, parseInt(newV,10) || 0));
          cur[k] = newV;
          val.value = ''+newV;
          setAllFromCur(!!redraw);
        }
        down.addEventListener('click', function(){ setK((cur[k]||0) - 1, true); });
        up.addEventListener('click', function(){ setK((cur[k]||0) + 1, true); });
        val.addEventListener('input', function(){ setK(val.value, false); });
        val.addEventListener('change', function(){ setK(val.value, true); });
        val.addEventListener('keydown', function(e){ if (e.key === 'Enter') { e.preventDefault(); setK(val.value, true); } });
        stepper.appendChild(down); stepper.appendChild(val); stepper.appendChild(up);
        var reset = document.createElement('button'); reset.className='tinyBtn'; reset.textContent='↺'; reset.disabled=disabled;
        reset.addEventListener('click', function(){ setK(def[k] != null ? def[k] : 0, true); });
        row.appendChild(stepper); row.appendChild(reset);
        if (configHelpEnabled()) {
          var coach = document.createElement('div');
          coach.className = 'presetCoach';
          coach.innerHTML = '<b>Why this delay?</b> ' + (PRESET_DELAY_HELP[k] || 'Delay gives the off-air camera time to move and settle before the cut.');
          row.appendChild(coach);
          var helpLink = document.createElement('a');
          helpLink.className = 'helpBtn';
          helpLink.href = configHelpUrl('/help/preset_delays#view-' + encodeURIComponent(k));
          helpLink.target = '_blank';
          helpLink.rel = 'noopener';
          helpLink.textContent = 'Help';
          row.appendChild(helpLink);
        }
        holder.appendChild(row);
      });
      ctrl.appendChild(holder);
      var quick = document.createElement('div'); quick.className='quickBtns';
      [0,5,10,20,30].forEach(function(n){
        var b=document.createElement('button'); b.className='chipBtn'; b.textContent='All '+n+'s'; b.disabled=disabled;
        b.addEventListener('click', function(){ keys.forEach(function(k){ cur[k]=n; }); setAllFromCur(true); });
        quick.appendChild(b);
      });
      ctrl.appendChild(quick);
    }
    else if (item.kind === 'int' || item.kind === 'float'){
      var step = (item.step != null) ? item.step : (item.kind === 'float' ? 0.5 : 1);
      var minv = (item.min != null) ? item.min : -1e9;
      var maxv = (item.max != null) ? item.max :  1e9;
      var stepper = document.createElement('div'); stepper.className='stepper';
      var down = document.createElement('button'); down.className='stepBtn'; down.textContent='▼'; down.disabled=disabled;
      var val = document.createElement('input'); val.className='fieldInput small'; val.type='number'; val.step=String(step); val.min=String(minv); val.max=String(maxv); val.value=''+valForDisplay; val.disabled=disabled;
      var up = document.createElement('button'); up.className='stepBtn'; up.textContent='▲'; up.disabled=disabled;
      var hint = document.createElement('span'); hint.className='editHint'; hint.textContent='type a value or use arrows';
      function setV(newV, redraw){
        if (item.kind === 'int') newV = parseInt(newV,10); else newV = parseFloat(newV);
        if (isNaN(newV)) newV = item.value;
        newV = Math.max(minv, Math.min(maxv, newV));
        if (item.kind === 'float') newV = roundToStep(newV, step);
        val.value = ''+newV;
        line.textContent = displayLine(item, newV);
        stageValue(item, newV, !!redraw);
      }
      down.addEventListener('click', function(){ setV((hasOwn(state.edited,item.key)?state.edited[item.key]:item.value) - step, true); });
      up.addEventListener('click', function(){ setV((hasOwn(state.edited,item.key)?state.edited[item.key]:item.value) + step, true); });
      val.addEventListener('input', function(){
        var raw = val.value;
        if (raw === '' || raw === '-' || raw === '.') { refreshActionButtons(); return; }
        var parsed = (item.kind === 'int') ? parseInt(raw,10) : parseFloat(raw);
        if (!isNaN(parsed)) { line.textContent = displayLine(item, parsed); stageValue(item, parsed, false); }
      });
      val.addEventListener('change', function(){ setV(val.value, true); });
      val.addEventListener('keydown', function(e){ if (e.key === 'Enter') { e.preventDefault(); setV(val.value, true); } });
      stepper.appendChild(down); stepper.appendChild(val); stepper.appendChild(up); stepper.appendChild(hint);
      ctrl.appendChild(stepper);
    }
    else if (item.kind === 'str'){
      var inp = document.createElement('input');
      inp.type = 'text';
      inp.className = 'fieldInput';
      inp.value = valForDisplay || '';
      inp.disabled = disabled;
      inp.addEventListener('input', function(){
        line.textContent = displayLine(item, inp.value);
        stageValue(item, inp.value, false);
      });
      inp.addEventListener('change', function(){ stageValue(item, inp.value, true); });
      inp.addEventListener('keydown', function(e){ if (e.key === 'Enter') { e.preventDefault(); stageValue(item, inp.value, true); } });
      ctrl.appendChild(inp);
    }
    else if (item.kind === 'json'){
      var ta = document.createElement('textarea');
      ta.style.width = '100%'; ta.style.minHeight = '100px'; ta.style.resize = 'vertical';
      ta.style.background = '#07111b'; ta.style.color = '#e9eef5'; ta.style.border = '2px solid #31506d';
      ta.style.borderRadius = '12px'; ta.style.padding = '10px'; ta.style.fontFamily = 'Consolas, monospace';
      ta.disabled = disabled;
      try { ta.value = (typeof valForDisplay === 'string') ? valForDisplay : JSON.stringify(valForDisplay, null, 2); }
      catch(e) { ta.value = String(valForDisplay || ''); }
      ta.addEventListener('input', function(){
        line.textContent = item.key + ': json = ' + ta.value;
        stageValue(item, ta.value, false);
      });
      ta.addEventListener('change', function(){ stageValue(item, ta.value, true); });
      ctrl.appendChild(ta);
    }

    var resetBtn = document.createElement('button');
    resetBtn.className = 'tinyBtn';
    resetBtn.textContent = 'Reset field';
    resetBtn.disabled = disabled;
    resetBtn.title = 'Stage this field back to its shipped default';
    resetBtn.addEventListener('click', function(){
      stageValue(item, clone(item.default), true);
      setStatus('Reset staged: ' + item.key + ' — tap Apply to save');
    });
    var resetRow = document.createElement('div');
    resetRow.className = 'fieldResetRow';
    resetRow.appendChild(resetBtn);
    if (configHelpEnabled()) {
      var coachText = FIELD_INLINE_COACH[item.key] || '';
      var helpUrl = FIELD_HELP_URLS[item.key] || '';
      if (coachText) {
        var coachInline = document.createElement('div');
        coachInline.className = 'fieldCoachText';
        coachInline.innerHTML = '<b>What this does:</b> ' + coachText;
        resetRow.appendChild(coachInline);
      }
      if (helpUrl) {
        var h = document.createElement('a');
        h.className = 'helpBtn';
        h.href = configHelpUrl(helpUrl);
        h.target = '_blank';
        h.rel = 'noopener';
        h.textContent = 'Help';
        resetRow.appendChild(h);
      }
    }
    ctrl.appendChild(resetRow);

    div.appendChild(ctrl);
    return div;
  }

  function makeStatusPill(text, cls){
    var p = document.createElement('span');
    p.className = 'pill ' + (cls || '');
    p.textContent = text;
    return p;
  }

  function renderSection(sec, q, locked){
    var items = (sec.items || []).filter(function(item){ return itemMatches(item, sec.title, q); });
    if (!items.length) return null;
    var card = document.createElement('div');
    card.className = 'sectionBlock';
    var head = document.createElement('div'); head.className='sectionHead';
    var st = document.createElement('div'); st.className='secTitle'; st.textContent=sec.title || '';
    var hint = document.createElement('div'); hint.className='sectionHint'; hint.textContent=items.length + ' setting' + (items.length===1?'':'s');
    head.appendChild(st); head.appendChild(hint); card.appendChild(head);
    items.forEach(function(item){ card.appendChild(makeItemEl(item, locked || !!item.locked)); });
    return card;
  }

  function draw(){
    var snapshot = state.snapshot;
    if (!snapshot || !root) return;
    root.innerHTML = '';

    var meta = snapshot.meta || {};
    var locked = !!meta.locked;
    var cats = buildCategories(snapshot);
    if (!cats[state.activeCat] || cats[state.activeCat].count === 0) state.activeCat = 'service';
    var q = String(state.search || '').trim();

    refreshStatusText();

    var shell = document.createElement('div'); shell.className='cfgShell';
    var metaBar = document.createElement('div'); metaBar.className='cfgMetaBar';
    metaBar.appendChild(makeStatusPill(meta.streaming ? 'Live stream active' : 'Not live', meta.streaming ? 'unlocked' : ''));
    metaBar.appendChild(makeStatusPill(locked ? 'Edits locked' : 'Edits available', locked ? 'locked' : 'unlocked'));
    metaBar.appendChild(makeStatusPill(state.dirty ? ('Pending: '+Object.keys(state.edited).length) : 'No pending changes', state.dirty ? 'unlocked' : ''));
    shell.appendChild(metaBar);

    var searchCard = document.createElement('div'); searchCard.className='cfgSearchCard';
    var sr = document.createElement('div'); sr.className='cfgSearchRow';
    var search = document.createElement('input'); search.id='cfgSearchInput'; search.className='cfgSearch'; search.type='text'; search.placeholder='Search settings: delay, camera, audio, timer, OBS...'; search.value=state.search;
    search.addEventListener('input', function(){
      var caret = 0;
      try { caret = search.selectionStart || String(search.value || '').length; } catch(e) { caret = String(search.value || '').length; }
      state.search = search.value;
      if (state.search) state.view='search'; else if (!isTimerPage()) state.view='menu';
      // Do not redraw synchronously on every keystroke. Replacing the input immediately
      // caused the tablet/browser to lose focus after one character. Debounce + refocus.
      scheduleSearchDraw(caret);
      refreshStatusText();
    });
    sr.appendChild(search);
    var clear = document.createElement('button'); clear.className='tinyBtn'; clear.textContent='Clear'; clear.addEventListener('click', function(){ state.search=''; if (!isTimerPage()) state.view='menu'; draw(); setTimeout(function(){ focusSearchInput(0); },0); });
    sr.appendChild(clear);
    searchCard.appendChild(sr);
    var mini = document.createElement('div'); mini.className='cfgMini';
    mini.textContent = 'Choose a category below for normal use, or search for settings such as “delay”, “intro”, “sync”, or “profile”. Typed fields are staged only when they differ from the loaded value.';
    searchCard.appendChild(mini);
    shell.appendChild(searchCard);

    var targetSections = [];
    var heroMeta = null;
    if (q) {
      state.view = 'search';
      heroMeta = { icon:'🔎', label:'Search Results', desc:'Showing settings that match “'+q+'”.' };
      (snapshot.sections || []).forEach(function(sec){ targetSections.push(sec); });
    } else if (isTimerPage()) {
      state.view = 'category';
      heroMeta = { icon:'🕘', label:'Timer Auto-Start', desc:'Sunday start time, timezone, persistence, and grace window.' };
      targetSections = snapshot.sections || [];
    } else if (state.view === 'menu') {
      var intro = document.createElement('div'); intro.className='categoryMenuIntro';
      intro.textContent = 'Configuration Menu — choose a category. Changes you make inside a category are staged until you tap Apply.';
      shell.appendChild(intro);
      var navOnly = document.createElement('div'); navOnly.className='cfgCatGrid';
      CATS.forEach(function(c){
        var info = cats[c.id];
        if (!info || info.count === 0) return;
        var b = document.createElement('button');
        b.className = 'cfgCatBtn';
        b.innerHTML = '<div class="cfgIcon">'+c.icon+'</div><div style="flex:1"><div class="cfgCatName">'+c.label+'</div><div class="cfgCatDesc">'+c.desc+'</div></div><div class="cfgCount">'+info.count+'</div>';
        b.addEventListener('click', function(){ state.activeCat = c.id; state.view='category'; localStorage.setItem('sa_cfg_active_cat', c.id); state.search=''; try { history.replaceState(null, '', withToken('/config') + '#' + c.id); } catch(e) {} draw(); });
        navOnly.appendChild(b);
      });
      shell.appendChild(navOnly);
      root.appendChild(shell);
      return;
    } else {
      var active = cats[state.activeCat] || cats.service;
      heroMeta = active.meta;
      targetSections = active.sections || [];
    }

    var visibleKeys = collectEditableKeys(targetSections, q);
    state.visibleKeys = visibleKeys;

    var hero = document.createElement('div'); hero.className='categoryHero';
    var ht = document.createElement('div'); ht.className='categoryHeroTop';
    var main = document.createElement('div'); main.className='categoryHeroMain';
    var hi = document.createElement('div'); hi.className='cfgIcon'; hi.textContent=heroMeta.icon;
    var hw = document.createElement('div');
    var htitle = document.createElement('div'); htitle.className='categoryTitle'; htitle.textContent=heroMeta.label;
    var hsub = document.createElement('div'); hsub.className='categorySub'; hsub.textContent=heroMeta.desc || '';
    hw.appendChild(htitle); hw.appendChild(hsub); main.appendChild(hi); main.appendChild(hw); ht.appendChild(main);

    var actions = document.createElement('div'); actions.className='categoryHeroActions';
    var back = document.createElement('button'); back.className='tinyBtn'; back.textContent = isTimerPage() ? 'Back to Config' : 'Back to Menu';
    back.addEventListener('click', function(){
      if (isTimerPage()) { window.location.href = withToken('/config'); return; }
      state.search=''; state.view='menu'; try { history.replaceState(null, '', withToken('/config')); } catch(e) {} draw();
    });
    var applyB = document.createElement('button'); applyB.className='tinyBtn'; applyB.textContent='Apply'; applyB.disabled = locked || !Object.keys(pickEdited(visibleKeys)).length;
    applyB.addEventListener('click', function(){ doApplyKeys(visibleKeys, heroMeta.label, applyB); });
    var restoreB = document.createElement('button'); restoreB.className='tinyBtn'; restoreB.textContent='Restore Defaults'; restoreB.disabled = locked || !!meta.streaming || !visibleKeys.length;
    restoreB.addEventListener('click', function(){ doRestoreKeys(visibleKeys, heroMeta.label, restoreB); });
    actions.appendChild(back); actions.appendChild(applyB); actions.appendChild(restoreB);
    ht.appendChild(actions); hero.appendChild(ht);
    shell.appendChild(hero);
    if (!q && !isTimerPage() && state.activeCat === 'midi') shell.appendChild(makeMidiMapCard(snapshot));

    var any = false;
    targetSections.forEach(function(sec){
      var block = renderSection(sec, q, locked);
      if (block) { shell.appendChild(block); any = true; }
    });
    if (!any) {
      var empty = document.createElement('div'); empty.className='emptyMsg';
      empty.textContent = q ? 'No matching settings found.' : 'No settings in this category.';
      shell.appendChild(empty);
    }

    root.appendChild(shell);
  }

  function render(snapshot){
    state.snapshot = snapshot;
    state.edited = {};
    state.dirty = false;
    state.visibleKeys = [];
    var hcat = hashCategory();
    if (isTimerPage()) { state.activeCat = 'service'; state.view = 'category'; }
    else if (hcat) { state.activeCat = hcat; state.view = 'category'; localStorage.setItem('sa_cfg_active_cat', hcat); }
    else { state.view = 'menu'; }
    draw();
  }

  function load(){
    var scope = isTimerPage() ? 'timer' : 'general';
    api('/api/config?scope='+encodeURIComponent(scope), { method:'GET' })
      .then(function(j){ render(j); })
      .catch(function(e){ setStatus('Error: ' + (e && e.message ? e.message : e)); });
  }

  function doUnlock(){
    tapConfirm('unlock', btnUnlock, 'Unlock (2 min)', 'Tap again to Unlock', function(){
      api('/api/config/unlock', { method:'POST', body: JSON.stringify({ minutes: 2 }) })
        .then(function(){ load(); })
        .catch(function(e){ setStatus('Unlock failed: ' + e.message); });
    });
  }

  function pickEdited(keys){
    var out = {};
    (keys || []).forEach(function(k){ if (hasOwn(state.edited, k)) out[k] = state.edited[k]; });
    return out;
  }

  function doApplyKeys(keys, label, tapBtn){
    var changes = pickEdited(keys || Object.keys(state.edited));
    if (!Object.keys(changes).length) { setStatus('No pending changes in ' + (label || 'this category') + '.'); return; }
    tapConfirm('apply_' + (label || 'all'), tapBtn || btnApply, 'Apply', 'Tap again to Apply', function(){
      api('/api/config/apply', { method:'POST', body: JSON.stringify({ changes: changes }) })
        .then(function(r){
          setStatus(r && r.message ? r.message : 'Applied.');
          load();
        })
        .catch(function(e){ setStatus('Apply failed: ' + e.message); });
    });
  }

  function doApply(){
    if (!state.dirty) { setStatus('No pending changes.'); return; }
    doApplyKeys(Object.keys(state.edited), 'all settings', btnApply);
  }

  function doRestoreKeys(keys, label, tapBtn){
    keys = keys || [];
    if (!keys.length) { setStatus('No editable settings to restore in ' + (label || 'this category') + '.'); return; }
    tapConfirm('restore_' + (label || 'category'), tapBtn || btnRestore, 'Restore Defaults', 'Tap again to Restore', function(){
      if (isTimerPage()) {
        api('/api/config/restore_timer', { method:'POST', body: '{}' })
          .then(function(r){ setStatus(r && r.message ? r.message : 'Timer restored.'); load(); })
          .catch(function(e){ setStatus('Restore failed: ' + e.message); });
      } else {
        api('/api/config/restore_fields', { method:'POST', body: JSON.stringify({ keys: keys }) })
          .then(function(r){ setStatus(r && r.message ? r.message : 'Restored.'); load(); })
          .catch(function(e){ setStatus('Restore failed: ' + e.message); });
      }
    });
  }

  function doRestoreGlobal(){
    tapConfirm('restore', btnRestore, 'Restore Defaults', 'Tap again to Restore', function(){
      api('/api/config/restore_global', { method:'POST', body: '{}' })
        .then(function(r){ setStatus(r && r.message ? r.message : 'Restored.'); load(); })
        .catch(function(e){ setStatus('Restore failed: ' + e.message); });
    });
  }

  function doRestoreTimer(){
    doRestoreKeys(state.visibleKeys && state.visibleKeys.length ? state.visibleKeys : ['USE_TIMER_START','TIMER_START_HHMM','TIMER_WEEKDAY','TIMEZONE','TIMER_PERSIST_STATE','TIMER_STATE_FILE','TIMER_FIRE_GRACE_MINUTES','TZ_FALLBACK_MODE','TZ_FALLBACK_UTC_OFFSET_HOURS'], 'Timer', btnRestoreTimer);
  }

  function doImport(file){
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function(){
      var txt = reader.result || '';
      var j = null;
      try { j = JSON.parse(txt); } catch(e){ setStatus('Import: invalid JSON'); return; }
      api('/api/config/import', { method:'POST', body: JSON.stringify(j) })
        .then(function(r){ setStatus(r && r.message ? r.message : 'Imported.'); load(); })
        .catch(function(e){ setStatus('Import failed: ' + e.message); });
    };
    reader.readAsText(file);
  }

  if (btnUnlock) btnUnlock.addEventListener('click', doUnlock);
  if (btnApply) btnApply.addEventListener('click', doApply);
  if (btnRestore) btnRestore.addEventListener('click', doRestoreGlobal);
  if (btnRestoreTimer) btnRestoreTimer.addEventListener('click', doRestoreTimer);

  if (fileImport) fileImport.addEventListener('change', function(){
    if (!fileImport.files || !fileImport.files.length) return;
    doImport(fileImport.files[0]);
    fileImport.value = '';
  });

  try { if (btnExport && TOKEN) btnExport.href = withToken(btnExport.href); } catch(e) {}

  load();
})();"""


    def _web_preset_delay_help_html(self) -> str:
        token = ("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else ""
        back = "/config" + token + "#delays"
        return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Preset Delay Help</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif; background:#0b1118; color:#e9eef5; }
  .wrap { max-width:860px; margin:0 auto; padding:16px; }
  .card { background:#121a24; border:3px solid #000; border-radius:18px; padding:16px; margin:12px 0; box-shadow:0 8px 24px rgba(0,0,0,.35); }
  .hero { background:linear-gradient(135deg,#173e5f,#101822); }
  h1 { margin:0; font-size:26px; }
  h2 { margin:0 0 8px; font-size:18px; }
  p, li { line-height:1.45; opacity:.92; }
  .btn { display:inline-flex; align-items:center; justify-content:center; padding:12px 14px; border-radius:14px; border:3px solid #000; background:#37474F; color:#fff; font-weight:900; text-decoration:none; margin-top:10px; }
  .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
  @media(max-width:720px){ .grid{grid-template-columns:1fr;} }
  .view { background:#0e1620; border:1px solid #28415c; border-radius:14px; padding:12px; }
  .key { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; background:#07111b; border:1px solid #28415c; border-radius:10px; padding:2px 7px; }
  .warn { border-color:#FF9800; }
</style>
</head>
<body>
<div class="wrap">
  <div class="card hero">
    <h1>Preset Delay Help</h1>
    <p>Preset delays are the safety pause between sending an Axis camera to a view and cutting that camera live in OBS. The goal is simple: let the off-air camera move, zoom, focus, and settle before the congregation sees it.</p>
    <a class="btn" href="__BACK__">Back to Config → Preset Delays</a>
  </div>

  <div class="card warn">
    <h2>How to choose a delay</h2>
    <ul>
      <li>Use <b>0 seconds</b> only when the camera is normally already on that shot or the move is tiny.</li>
      <li>Use <b>5–10 seconds</b> for modest pan/tilt/zoom moves.</li>
      <li>Use <b>15–20 seconds</b> for large moves such as Pulpit or Children’s Time if you see motion during cuts.</li>
      <li>Longer delays are safer, but they also make automatic view changes feel slower.</li>
    </ul>
    <p>The delay is not a camera speed setting. It is a waiting period before Stream Agent cuts to the prepared camera scene.</p>
  </div>

  <div class="card">
    <h2>Current preset delay coaching</h2>
    <div class="grid">
      <div class="view" id="view-1"><b>1 — Pulpit</b><p>Pulpit often needs a longer blind delay so the off-air camera can finish moving before the scene cut.</p></div>
      <div class="view" id="view-2"><b>2 — Panorama</b><p>Panorama is normally safe immediately because it is the fallback or home-style wide view.</p></div>
      <div class="view" id="view-3"><b>3 — Children’s Time</b><p>Children’s Time often involves a larger PTZ move; delay keeps the camera motion hidden.</p></div>
      <div class="view" id="view-4"><b>4 — Altar</b><p>Altar usually benefits from a short pause so the camera settles before going live.</p></div>
      <div class="view" id="view-5"><b>5 — Choir</b><p>Choir often needs enough time for tilt/zoom to settle before the cut.</p></div>
      <div class="view" id="view-6"><b>6 — Screen</b><p>Screen is usually a prepared/static view, so little or no delay is normally needed.</p></div>
      <div class="view" id="view-7"><b>7 — Band</b><p>Band is usually safe with little delay if the preset is close to the current position.</p></div>
      <div class="view" id="view-8"><b>8 — Piano</b><p>Piano is usually safe with little delay if the camera is already near that area.</p></div>
      <div class="view" id="view-9"><b>9 — Communion</b><p>Communion can involve a larger move; a delay avoids showing PTZ motion live.</p></div>
      <div class="view" id="view-10"><b>10 — Podium</b><p>Podium is often a prepared view; use more delay only if you see the camera still moving.</p></div>
    </div>
  </div>

  <div class="card">
    <h2>Related setting</h2>
    <p id="enable-preset-delays"><span class="key">ENABLE_PRESET_DELAYS</span> turns the blind-delay system on or off.</p>
    <p><span class="key">PRESET_DELAYS_SECONDS</span> stores the delay value for each service view.</p>
  </div>
</div>
</body>
</html>""".replace("__BACK__", back)


    def _web_camera_config_help_html(self) -> str:
        token = ("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else ""
        back = "/config" + token + "#cameras"
        return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Camera Config Help</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif; background:#0b1118; color:#e9eef5; }
  .wrap { max-width:900px; margin:0 auto; padding:16px; }
  .card { background:#121a24; border:3px solid #000; border-radius:18px; padding:16px; margin:12px 0; box-shadow:0 8px 24px rgba(0,0,0,.35); }
  .hero { background:linear-gradient(135deg,#193d4f,#101822); }
  h1 { margin:0; font-size:26px; }
  h2 { margin:0 0 8px; font-size:18px; }
  p, li { line-height:1.45; opacity:.92; }
  .btn { display:inline-flex; align-items:center; justify-content:center; padding:12px 14px; border-radius:14px; border:3px solid #000; background:#37474F; color:#fff; font-weight:900; text-decoration:none; margin-top:10px; }
  .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
  @media(max-width:720px){ .grid{grid-template-columns:1fr;} }
  .view { background:#0e1620; border:1px solid #28415c; border-radius:14px; padding:12px; }
  .key { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; background:#07111b; border:1px solid #28415c; border-radius:10px; padding:2px 7px; }
  .warn { border-color:#FF9800; }
</style>
</head>
<body>
<div class="wrap">
  <div class="card hero">
    <h1>Camera Config Help</h1>
    <p>These settings tell Stream Agent how to reach the two Axis cameras and how to interpret their stored PTZ presets. Most of these should stay stable once the church network is working.</p>
    <a class="btn" href="__BACK__">Back to Config → Cameras</a>
  </div>

  <div class="card warn">
    <h2>Most important rule</h2>
    <p>The Axis preset names must match exactly on both cameras. Capital letters, spaces, and punctuation matter. For example, <span class="key">Podium</span> and <span class="key">podium</span> are different preset names.</p>
  </div>

  <div class="card">
    <h2>Camera connection settings</h2>
    <div class="grid">
      <div class="view" id="west-axis-ip"><b>WEST_AXIS_IP</b><p>The IP address Stream Agent uses to send PTZ preset commands to the West camera. If this is wrong, West camera steering will fail.</p></div>
      <div class="view" id="east-axis-ip"><b>EAST_AXIS_IP</b><p>The IP address Stream Agent uses to send PTZ preset commands to the East camera. If this is wrong, East camera steering will fail.</p></div>
      <div class="view" id="west-axis-camera-id"><b>WEST_AXIS_CAMERA_ID</b><p>Usually <b>1</b>. Change only if an Axis device exposes more than one internal camera channel.</p></div>
      <div class="view" id="east-axis-camera-id"><b>EAST_AXIS_CAMERA_ID</b><p>Usually <b>1</b>. Change only if an Axis device exposes more than one internal camera channel.</p></div>
      <div class="view" id="axis-use-https"><b>AXIS_USE_HTTPS</b><p>Use HTTPS only if the cameras are configured for HTTPS access. On the private church AV network, HTTP is normally simpler and expected.</p></div>
      <div class="view" id="axis-command-timeout-seconds"><b>AXIS_COMMAND_TIMEOUT_SECONDS</b><p>How long Stream Agent waits for a camera command to respond before logging a camera-control warning.</p></div>
    </div>
  </div>

  <div class="card">
    <h2>Routing and preset settings</h2>
    <div class="grid">
      <div class="view" id="axis-view-preset-names"><b>AXIS_VIEW_PRESET_NAMES</b><p>The exact Axis server preset names for Pulpit, Panorama, Choir, and the other service views. Keep these names identical on both cameras.</p></div>
      <div class="view" id="west-camera-enabled"><b>WEST_CAMERA_ENABLED</b><p>Turn this off only as a temporary workaround if the West camera is unavailable or misbehaving.</p></div>
      <div class="view" id="east-camera-enabled"><b>EAST_CAMERA_ENABLED</b><p>Turn this off only as a temporary workaround if the East camera is unavailable or misbehaving.</p></div>
      <div class="view" id="initial-camera-views"><b>INITIAL_CAMERA_VIEWS</b><p>The app’s startup assumption about where each camera is parked before Stream Agent has moved it. This helps early routing decisions.</p></div>
    </div>
  </div>

  <div class="card">
    <h2>When to change these</h2>
    <ul>
      <li>Change IP addresses only after confirming the camera address on the network.</li>
      <li>Change preset names only after checking the exact Axis preset spelling.</li>
      <li>Use camera enable flags as temporary fault isolation, not as normal weekly setup.</li>
      <li>After major camera/network changes, test manual Director buttons before trusting MIDI automation.</li>
    </ul>
  </div>
</div>
</body>
</html>""".replace("__BACK__", back)



    def _web_config_field_help_html(self) -> str:
        token = ("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else ""
        back = "/config" + token

        def _esc(x):
            return (str(x or "")
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace('"', "&quot;"))

        def _slug(k):
            return str(k or "").lower().replace("_", "-")

        cards = []
        seen = set()
        section_list = list(CFG_UI_GENERAL_SECTIONS)
        # Keep Timer represented even if it appears in the general grouping.
        if not any(str(t).upper().startswith("TIMER") for t, _ in section_list):
            section_list.insert(1, ("TIMER AUTO-START", CFG_UI_TIMER_FIELDS))

        for title, keys in section_list:
            rows = []
            for key in keys:
                if key == "PRESET_LABELS" or key in seen:
                    continue
                seen.add(key)
                desc = CONFIG_FIELD_COACHING.get(key, "")
                if not desc:
                    continue
                rows.append(
                    f'<div class="view" id="{_slug(key)}"><b>{_esc(key)}</b><p>{_esc(desc)}</p></div>'
                )
            if rows:
                cards.append(
                    '<div class="card"><h2>' + _esc(title) + '</h2><div class="grid">' + "\n".join(rows) + '</div></div>'
                )

        body_cards = "\n".join(cards)
        return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Config Field Help</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif; background:#0b1118; color:#e9eef5; }
  .wrap { max-width:980px; margin:0 auto; padding:16px; }
  .card { background:#121a24; border:3px solid #000; border-radius:18px; padding:16px; margin:12px 0; box-shadow:0 8px 24px rgba(0,0,0,.35); }
  .hero { background:linear-gradient(135deg,#193d4f,#101822); }
  h1 { margin:0; font-size:26px; }
  h2 { margin:0 0 8px; font-size:18px; }
  p, li { line-height:1.45; opacity:.92; }
  .btn { display:inline-flex; align-items:center; justify-content:center; padding:12px 14px; border-radius:14px; border:3px solid #000; background:#37474F; color:#fff; font-weight:900; text-decoration:none; margin-top:10px; }
  .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
  @media(max-width:720px){ .grid{grid-template-columns:1fr;} }
  .view { background:#0e1620; border:1px solid #28415c; border-radius:14px; padding:12px; scroll-margin-top:12px; }
  .view:target { outline:3px solid #42A5F5; background:#10263a; }
  .view b { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  .key { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; background:#07111b; border:1px solid #28415c; border-radius:10px; padding:2px 7px; }
</style>
</head>
<body>
<div class="wrap">
  <div class="card hero">
    <h1>Config Field Help</h1>
    <p>This page gives short operator-focused explanations for the Stream Agent configuration fields. Use it as a quick reference when deciding whether a setting should be changed before a service.</p>
    <a class="btn" href="__BACK__">Back to Config</a>
  </div>
  <div class="card">
    <h2>General guidance</h2>
    <ul>
      <li>Change only one or two settings at a time, then apply and verify behavior.</li>
      <li>Do not change read-only network/password fields from the browser during service.</li>
      <li>When a setting affects OBS source names, scene names, camera IPs, or MIDI notes, test the related function before relying on it live.</li>
    </ul>
  </div>
  __CARDS__
</div>
</body>
</html>""".replace("__BACK__", back).replace("__CARDS__", body_cards)

    def _cfg_is_streaming(self) -> bool:
        try:
            streaming, recording, err = self.obs.get_status()
            return bool(streaming)
        except Exception:
            return False

    def _cfg_unlock_active(self) -> bool:
        return time.time() < getattr(self, "_cfg_unlock_until", 0.0)

    def _cfg_edit_locked(self) -> bool:
        # Locked only while LIVE unless unlocked
        if not self._cfg_is_streaming():
            return False
        return not self._cfg_unlock_active()

    def _cfg_unlock_for_minutes(self, minutes: float = 2.0):
        try:
            minutes = float(minutes)
        except Exception:
            minutes = 2.0
        if minutes < 0.5:
            minutes = 0.5
        if minutes > 10.0:
            minutes = 10.0
        self._cfg_unlock_until = time.time() + (minutes * 60.0)

    def _cfg_make_item(self, key: str):
        # Build a UI item dict for a given config key.
        # Structured dict/list/tuple values are shown as editable JSON blocks so they are readable
        # in the browser instead of collapsing to [object Object].
        readonly = (key in CFG_UI_READONLY_ALWAYS)
        cur = getattr(self.cfg, key, None) if hasattr(self.cfg, key) else None
        dflt = getattr(DEFAULT_CFG, key, None) if hasattr(DEFAULT_CFG, key) else None

        # Special: preset delays dict
        if key == "PRESET_DELAYS_SECONDS":
            cur = dict(cur or {})
            dflt = dict(dflt or {})
            # normalize keys to strings for JS
            cur_s = {str(int(k)): int(v) for k, v in cur.items()}
            dflt_s = {str(int(k)): int(v) for k, v in dflt.items()}
            changed = (cur_s != dflt_s)
            return {
                "key": key,
                "kind": "preset_delays",
                "value": cur_s,
                "default": dflt_s,
                "display": _cfg_make_display_line(key, cur),
                "changed": changed,
                "readonly": readonly,
                "min": 0,
                "max": 600,
                "step": 1,
                "locked": False,
            }

        kind = "str"
        options = None
        step = None
        minv = None
        maxv = None

        if key in CFG_UI_ENUM_OPTIONS:
            kind = "enum"
            options = CFG_UI_ENUM_OPTIONS[key]
        elif isinstance(cur, bool):
            kind = "bool"
        elif isinstance(cur, int) and not isinstance(cur, bool):
            kind = "int"
            # heuristics
            if "NOTE_" in key:
                minv, maxv, step = 0, 127, 1
            elif "WEEKDAY" in key:
                minv, maxv, step = 0, 6, 1
            elif "PORT" in key:
                minv, maxv, step = 1, 65535, 1
            elif "RETENTION" in key:
                minv, maxv, step = 1, 365, 1
            elif "GRACE" in key and "MINUTES" in key:
                minv, maxv, step = 0, 240, 1
            elif "SECONDS" in key:
                minv, maxv, step = 0, 3600, 1
        elif isinstance(cur, float):
            kind = "float"
            step = 0.5
            if "SECONDS" in key:
                minv, maxv = 0.0, 3600.0
            elif "MULTIPLIER" in key:
                minv, maxv = 0.5, 10.0
        elif isinstance(cur, (dict, list, tuple)):
            # JSON editor for structured config values.
            kind = "json"
        else:
            kind = "str"

        changed = (cur != dflt)

        # If LIVE and locked: mark as locked unless it's in the live-editable list and unlock is active
        locked_now = False
        if self._cfg_is_streaming():
            if key in CFG_UI_LIVE_EDITABLE_FIELDS:
                locked_now = (not self._cfg_unlock_active())
            else:
                locked_now = True  # while live, only Tier-B is even eligible

        return {
            "key": key,
            "kind": kind,
            "value": cur,
            "default": dflt,
            "display": _cfg_make_display_line(key, cur),
            "changed": changed,
            "readonly": readonly,
            "options": options,
            "step": step,
            "min": minv,
            "max": maxv,
            "locked": locked_now,
        }

    def _cfg_snapshot(self, scope: str = "general") -> dict:
        scope = (scope or "general").lower().strip()
        streaming = self._cfg_is_streaming()
        locked = self._cfg_edit_locked()
        unlock_remaining = max(0.0, getattr(self, "_cfg_unlock_until", 0.0) - time.time())

        sections = []
        if scope == "timer":
            items = [self._cfg_make_item(k) for k in CFG_UI_TIMER_FIELDS]
            sections.append({"title": "TIMER AUTO-START", "items": items})
        else:
            for title, keys in CFG_UI_GENERAL_SECTIONS:
                items = []
                for k in keys:
                    if k == "PRESET_LABELS":
                        continue
                    items.append(self._cfg_make_item(k))
                sections.append({"title": title, "items": items})

        return {
            "meta": {
                "streaming": bool(streaming),
                "locked": bool(locked),
                "unlock_remaining_s": float(unlock_remaining),
                "preset_labels": {str(int(k)): str(v) for k, v in (getattr(self.cfg, "PRESET_LABELS", {}) or {}).items()},
            },
            "sections": sections,
        }

    def _cfg_set_field(self, key: str, value):
        # Enforce read-only
        if key in CFG_UI_READONLY_ALWAYS:
            raise ValueError(f"{key} is read-only")

        if not hasattr(self.cfg, key):
            raise ValueError(f"Unknown config key: {key}")

        cur = getattr(self.cfg, key)
        if key in CFG_UI_ENUM_OPTIONS:
            if not isinstance(value, str) or value not in CFG_UI_ENUM_OPTIONS[key]:
                raise ValueError(f"Invalid value for {key}")
            setattr(self.cfg, key, value)
            return

        if key == "TIMER_START_HHMM":
            # Validate before saving so a bad Web HUD entry cannot crash the timer/banner loop.
            hh, mm = parse_hhmm(str(value))
            setattr(self.cfg, key, f"{hh}:{mm:02d}")
            return

        if key == "PRESET_DELAYS_SECONDS":
            if not isinstance(value, dict):
                raise ValueError("PRESET_DELAYS_SECONDS must be a dict")
            nd = {}
            for kk, vv in value.items():
                try:
                    nd[int(kk)] = max(0, min(600, int(vv)))
                except Exception:
                    pass
            if not nd:
                raise ValueError("No valid preset delays supplied")
            # swap whole dict (safer than mutating)
            setattr(self.cfg, key, nd)
            return

        # Structured JSON fields shown in the config page.
        if isinstance(cur, (dict, list, tuple)) and isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception as e:
                raise ValueError(f"{key} JSON parse error: {e}")

        if key == "AXIS_VIEW_PRESET_NAMES":
            if not isinstance(value, dict):
                raise ValueError("AXIS_VIEW_PRESET_NAMES must be a dict")
            nd = {}
            for kk, vv in value.items():
                try:
                    k_int = int(kk)
                except Exception:
                    continue
                name = str(vv).strip()
                if not name:
                    continue
                nd[k_int] = name
            if not nd:
                raise ValueError("No valid Axis preset names supplied")
            setattr(self.cfg, key, nd)
            return

        if key == "INITIAL_CAMERA_VIEWS":
            if not isinstance(value, dict):
                raise ValueError("INITIAL_CAMERA_VIEWS must be a dict")
            nd = {}
            for kk, vv in value.items():
                s = str(kk).strip().lower()
                if s not in ("west", "east"):
                    continue
                try:
                    nd[s] = int(vv)
                except Exception:
                    continue
            if not nd:
                raise ValueError("No valid initial camera views supplied")
            setattr(self.cfg, key, nd)
            return

        if isinstance(cur, bool):
            setattr(self.cfg, key, _cfg_bool_from_value(value))
        elif isinstance(cur, int) and not isinstance(cur, bool):
            iv = int(value)
            # clamp some known ranges
            if "NOTE_" in key:
                iv = max(0, min(127, iv))
            elif "WEEKDAY" in key:
                iv = max(0, min(6, iv))
            elif "PORT" in key:
                iv = max(1, min(65535, iv))
            elif "SECONDS" in key:
                iv = max(0, min(3600, iv))
            elif "RETENTION" in key:
                iv = max(1, min(365, iv))
            setattr(self.cfg, key, iv)
        elif isinstance(cur, float):
            fv = float(value)
            # round to 0.5 increments
            fv = round(fv * 2.0) / 2.0
            if "SECONDS" in key:
                fv = max(0.0, min(3600.0, fv))
            if "MULTIPLIER" in key:
                fv = max(0.5, min(10.0, fv))
            setattr(self.cfg, key, fv)
        elif isinstance(cur, str):
            setattr(self.cfg, key, str(value))
        else:
            raise ValueError(f"Unsupported type for {key}")

    def _cfg_update_overrides_from_current(self) -> dict:
        # Save only diffs from DEFAULT_CFG (plus preset delays diffs)
        overrides = {}
        for title, keys in CFG_UI_GENERAL_SECTIONS:
            for k in keys:
                if k in CFG_UI_READONLY_ALWAYS:
                    continue
                if k == "PRESET_LABELS":
                    continue
                if not hasattr(self.cfg, k) or not hasattr(DEFAULT_CFG, k):
                    continue
                cur = getattr(self.cfg, k)
                dfl = getattr(DEFAULT_CFG, k)
                if k == "PRESET_DELAYS_SECONDS":
                    cur_d = {str(int(kk)): int(vv) for kk, vv in (cur or {}).items()}
                    dfl_d = {str(int(kk)): int(vv) for kk, vv in (dfl or {}).items()}
                    if cur_d != dfl_d:
                        overrides[k] = cur_d
                else:
                    if cur != dfl:
                        overrides[k] = cur
        # timer fields
        for k in CFG_UI_TIMER_FIELDS:
            if k in CFG_UI_READONLY_ALWAYS:
                continue
            if not hasattr(self.cfg, k) or not hasattr(DEFAULT_CFG, k):
                continue
            cur = getattr(self.cfg, k)
            dfl = getattr(DEFAULT_CFG, k)
            if cur != dfl:
                overrides[k] = cur
        return overrides

    def _cfg_persist_overrides(self, source: str, remote_ip: str = "") -> bool:
        overrides = self._cfg_update_overrides_from_current()
        ok = _cfg_save_overrides_file(overrides)
        # record a compact changelog line
        _cfg_append_changelog({"source": source, "remote_ip": remote_ip, "event": "persist", "overrides_keys": sorted(list(overrides.keys()))})
        # keep in memory too (for export)
        self._cfg_overrides_cache = overrides
        return ok

    def _cfg_apply_changes(self, changes: dict, source: str = "WEB", remote_ip: str = "") -> str:
        if self._cfg_edit_locked():
            raise ValueError("Editing is locked while LIVE (unlock to override).")

        if not isinstance(changes, dict):
            raise ValueError("Invalid payload (changes must be an object).")

        streaming = self._cfg_is_streaming()
        applied = []
        for k, v in changes.items():
            # While LIVE: only Tier-B is allowed (even if unlocked)
            if streaming and k not in CFG_UI_LIVE_EDITABLE_FIELDS:
                continue
            self._cfg_set_field(k, v)
            applied.append(k)

        # Persist to overrides file
        self._cfg_persist_overrides(source=source, remote_ip=remote_ip)
        if applied:
            _cfg_append_changelog({"source": source, "remote_ip": remote_ip, "event": "apply", "keys": applied})
        return "Applied: " + (", ".join(applied) if applied else "(no eligible fields)")

    def _cfg_restore_global(self, source: str = "WEB", remote_ip: str = "") -> str:
        if self._cfg_is_streaming():
            raise ValueError("Restore is disabled while LIVE.")
        if self._cfg_edit_locked():
            raise ValueError("Editing is locked while LIVE (unlock to override).")
        # Reset all editable fields to DEFAULT_CFG
        for title, keys in CFG_UI_GENERAL_SECTIONS:
            for k in keys:
                if k in CFG_UI_READONLY_ALWAYS or k == "PRESET_LABELS":
                    continue
                if not hasattr(self.cfg, k) or not hasattr(DEFAULT_CFG, k):
                    continue
                if k == "PRESET_DELAYS_SECONDS":
                    setattr(self.cfg, k, dict(getattr(DEFAULT_CFG, k) or {}))
                else:
                    setattr(self.cfg, k, getattr(DEFAULT_CFG, k))
        # Timer fields also reset as part of global restore
        for k in CFG_UI_TIMER_FIELDS:
            if k in CFG_UI_READONLY_ALWAYS:
                continue
            if hasattr(self.cfg, k) and hasattr(DEFAULT_CFG, k):
                setattr(self.cfg, k, getattr(DEFAULT_CFG, k))
        # Persist (which will likely clear most overrides)
        self._cfg_persist_overrides(source=source, remote_ip=remote_ip)
        _cfg_append_changelog({"source": source, "remote_ip": remote_ip, "event": "restore_global"})
        return "Global defaults restored."

    def _cfg_restore_timer_only(self, source: str = "WEB", remote_ip: str = "") -> str:
        if self._cfg_is_streaming():
            raise ValueError("Restore is disabled while LIVE.")
        if self._cfg_edit_locked():
            raise ValueError("Editing is locked while LIVE (unlock to override).")
        # Reset only timer fields (labels are not editable anyway)
        for k in CFG_UI_TIMER_FIELDS:
            if k in CFG_UI_READONLY_ALWAYS:
                continue
            if hasattr(self.cfg, k) and hasattr(DEFAULT_CFG, k):
                setattr(self.cfg, k, getattr(DEFAULT_CFG, k))
        self._cfg_persist_overrides(source=source, remote_ip=remote_ip)
        _cfg_append_changelog({"source": source, "remote_ip": remote_ip, "event": "restore_timer"})
        return "TIMER defaults restored (timer-only)."

    def _cfg_restore_selected_fields(self, keys: list, source: str = "WEB", remote_ip: str = "") -> str:
        """Restore only the requested editable config fields to shipped defaults.

        Used by the categorized Web HUD so a category-level Restore Defaults button
        affects only the visible/menu range, not the whole app. Sensitive/read-only
        fields are ignored even if a browser sends them.
        """
        if self._cfg_is_streaming():
            raise ValueError("Restore is disabled while LIVE.")
        if self._cfg_edit_locked():
            raise ValueError("Editing is locked while LIVE (unlock to override).")
        if not isinstance(keys, (list, tuple, set)):
            raise ValueError("keys must be a list")

        restored = []
        for raw_key in keys:
            k = str(raw_key or "").strip()
            if not k or k in CFG_UI_READONLY_ALWAYS or k == "PRESET_LABELS":
                continue
            if not hasattr(self.cfg, k) or not hasattr(DEFAULT_CFG, k):
                continue
            if k == "PRESET_DELAYS_SECONDS":
                setattr(self.cfg, k, dict(getattr(DEFAULT_CFG, k) or {}))
            else:
                setattr(self.cfg, k, getattr(DEFAULT_CFG, k))
            restored.append(k)

        self._cfg_persist_overrides(source=source, remote_ip=remote_ip)
        if restored:
            _cfg_append_changelog({"source": source, "remote_ip": remote_ip, "event": "restore_selected", "keys": restored})
        return "Restored defaults: " + (", ".join(restored) if restored else "(no eligible fields)")

    def _cfg_export_payload(self) -> dict:
        overrides = getattr(self, "_cfg_overrides_cache", None)
        if not overrides:
            overrides = _cfg_load_overrides_file()
        return {"version": 1, "exported_utc": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z", "overrides": overrides or {}}

    def _cfg_import_payload(self, payload: dict, source: str = "WEB", remote_ip: str = "") -> str:
        if self._cfg_is_streaming():
            raise ValueError("Import is disabled while LIVE.")
        if self._cfg_edit_locked():
            raise ValueError("Editing is locked while LIVE (unlock to override).")
        if not isinstance(payload, dict):
            raise ValueError("Import must be a JSON object.")
        overrides = payload.get("overrides", payload)
        if not isinstance(overrides, dict):
            raise ValueError("Import JSON must contain an overrides object/dict.")
        # Apply to current cfg (respecting read-only and type casting)
        _cfg_apply_overrides(self.cfg, overrides)
        self._cfg_persist_overrides(source=source, remote_ip=remote_ip)
        _cfg_append_changelog({"source": source, "remote_ip": remote_ip, "event": "import", "keys": sorted(list(overrides.keys()))})
        return "Imported overrides."




    def _web_js(self) -> str:
        # ES5-only JS, served as /app.js (cache-busted by ?v=12)
        # IMPORTANT: Use a raw string so backslashes (e.g. "\n") survive into JS.
        return r"""(function(){
  'use strict';
  // Stream Agent HUD JS v2.1 (schema + newline fix)
  try { console.log('Stream Agent HUD JS v2.1 loaded'); } catch (e) {}

  // Touch UX: prevent long-press context menus on buttons (makes "normal click" tolerant of slower presses)
  try {
    document.addEventListener('contextmenu', function(e){
      var t = e && e.target;
      if (!t) return;
      var cls = t.classList;
      if (cls && (cls.contains('pbtn') || cls.contains('btn'))) {
        e.preventDefault();
      }
    }, { passive: false });
  } catch (e) {}

  function $(id){ return document.getElementById(id); }
  var statusTitle = $('statusTitle');
  var statusBanner = $('statusBanner');
  var connLine = $('connLine');
  var appVer = $('appVer');
  var btnStart = $('btnStart');
  var btnStop  = $('btnStop');
  var btnRec   = $('btnRec');
  var btnSceneWest = $('btnSceneWest');
  var btnSceneEast = $('btnSceneEast');
  var audioSlider = $('audioSlider');
  var audioLabel = $('audioLabel');
  var audioMeterLabel = $('audioMeterLabel');
  var audioMeterFill = $('audioMeterFill');
  var audioHelp = $('audioHelp');
  var presetGrid = $('presetGrid');
  var logBox = $('logBox');
  var traceWrap = $('traceWrap');
  var lastSceneAction = $('lastSceneAction');
  var cameraTraceBox = $('cameraTraceBox');
  var healthBox = $('healthBox');
  var healthTitle = $('healthTitle');
  var healthDetail = $('healthDetail');
  var healthLast = $('healthLast');
  var btnSync = $('btnSync');
  var btnAudioCfg = $('btnAudioCfg');
  var syncHudHint = $('syncHudHint');
  var audioCfgSub = $('audioCfgSub');


  function setConn(t){ if (connLine) connLine.textContent = t; }
  function setTitle(t){ if (statusTitle) statusTitle.textContent = t; }


  function setBannerStyle(styleName){
    if (!statusBanner) return;
    var s = (styleName || '') + '';
    var cls = 'statusBanner ';
    if (s.indexOf('Live.') === 0) cls += 'sb-live';
    else if (s.indexOf('Ready.') === 0) cls += 'sb-ready';
    else if (s.indexOf('Ended.') === 0) cls += 'sb-ended';
    else if (s.indexOf('Stopping.') === 0) cls += 'sb-stop';
    else if (s.indexOf('Countdown.') === 0) cls += 'sb-warn';
    else if (s.indexOf('Error.') === 0) cls += 'sb-error';
    else cls += 'sb-ready';
    statusBanner.className = cls;
  }

  function setVer(t){ if (appVer) appVer.textContent = t || ''; }

  function setHealth(h){
    if (!healthBox) return;
    var lvl = (h && h.level) ? (''+h.level).toLowerCase() : 'ready';
    // Normalize common values
    if (lvl === 'streaming') lvl = 'live';
    var cls = 'healthBox h-' + lvl;
    healthBox.className = cls;
    if (healthTitle) healthTitle.textContent = (h && h.title) ? h.title : (lvl ? lvl.toUpperCase() : 'READY');
    if (healthDetail) healthDetail.textContent = (h && h.detail) ? h.detail : '';
    var last = '';
    if (h && h.last_msg) {
      if (h.last_ts) last = 'Last: ' + h.last_ts + ' — ' + h.last_msg;
      else last = 'Last: ' + h.last_msg;
    }
    if (healthLast) healthLast.textContent = last;
  }

  function clamp(v, lo, hi){ return Math.max(lo, Math.min(hi, v)); }

  function setAudio(audio){
    if (!audio) return;
    var minDb = (typeof audio.min_db === 'number') ? audio.min_db : -40;
    var maxDb = (typeof audio.max_db === 'number') ? audio.max_db : 6;
    var db = (typeof audio.master_db === 'number') ? audio.master_db : 0;
    var meterDb = (typeof audio.meter_db === 'number') ? audio.meter_db : -60;

    var lbl = audio.label ? String(audio.label) : 'Master';
    if (audioLabel) audioLabel.textContent = lbl + ': ' + db.toFixed(1) + ' dB';
    if (audioMeterLabel) audioMeterLabel.textContent = 'Meter: ' + meterDb.toFixed(1) + ' dBFS';
    if (audioHelp) audioHelp.textContent = audio.description ? String(audio.description) : '';

    if (audioSlider && document.activeElement !== audioSlider) {
      audioSlider.min = String(minDb);
      audioSlider.max = String(maxDb);
      audioSlider.value = String(db);
    }

    if (audioMeterFill) {
      var pct = ((clamp(meterDb, -60, 0) + 60) / 60) * 100;
      audioMeterFill.style.width = pct.toFixed(1) + '%';
    }
  }


  function getTokenQS(){
    // If opened with ?token=XYZ, forward to /ws?token=XYZ
    try {
      var qs = window.location.search || '';
      if (qs && qs.indexOf('token=') >= 0) return qs;
    } catch (e) {}
    return '';
  }

  var ws = null;
var wsReady = false;

  function applyToolMode(st){
    var sync = st && st.sync ? st.sync : null;
    var audio = st && st.audio ? st.audio : null;
    var mode = (audio && audio.mode) ? String(audio.mode) : ((sync && sync.mode) ? String(sync.mode) : '');
    var syncEnabled = !!(sync && sync.enabled === true);

    if (btnSync) {
      if (syncEnabled) btnSync.classList.remove('hiddenTool');
      else btnSync.classList.add('hiddenTool');
    }
    if (btnAudioCfg) {
      btnAudioCfg.classList.remove('hiddenTool');
      if (!syncEnabled) btnAudioCfg.classList.add('featuredTool');
      else btnAudioCfg.classList.remove('featuredTool');
    }
    if (audioCfgSub) {
      if (syncEnabled) audioCfgSub.textContent = 'Shared ASIO active';
      else if (mode === 'axis_embedded') audioCfgSub.textContent = 'Axis audio mode';
      else audioCfgSub.textContent = 'Mode + fader setup';
    }
    if (syncHudHint) {
      if (syncEnabled) syncHudHint.textContent = 'Shared ASIO audio is active, so Live Sync is available. Timer settings live under Config → Timer.';
      else if (mode === 'axis_embedded') syncHudHint.textContent = 'Sync is hidden because AUDIO_MODE is axis_embedded. Use Audio Config to switch to shared ASIO if needed. Timer settings live under Config → Timer.';
      else syncHudHint.textContent = 'Sync appears only when shared ASIO audio is active. Timer settings live under Config → Timer.';
    }
  }

  function setEnabled(enabled){
    if (btnStart) btnStart.disabled = !enabled;
    if (btnStop)  btnStop.disabled  = !enabled;
    if (btnRec)   btnRec.disabled   = !enabled;
    if (btnSceneWest) btnSceneWest.disabled = !enabled;
    if (btnSceneEast) btnSceneEast.disabled = !enabled;
    if (audioSlider) audioSlider.disabled = !enabled;
    if (presetGrid){
      var bs = presetGrid.getElementsByTagName('button');
      for (var i=0; i<bs.length; i++) bs[i].disabled = !enabled;
    }
  }


  function wsUrl(){
    var proto = (window.location.protocol === 'https:') ? 'wss:' : 'ws:';
    return proto + '//' + window.location.host + '/ws' + getTokenQS();
  }

  function send(obj){
    try {
      if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
      else setConn('Not connected yet — wait for "Connected"');
    } catch (e) {
      setConn('Send failed: ' + e);
    }
  }

  function buildPresets(presetLabels){
    while (presetGrid.firstChild) presetGrid.removeChild(presetGrid.firstChild);
    for (var i=1; i<=10; i++){
      var label = 'Preset ' + i;
      if (presetLabels && presetLabels[i]) label = i + ': ' + presetLabels[i];

      var b = document.createElement('button');
      b.className = 'pbtn';
      b.textContent = label;
      b.disabled = !wsReady;
      (function(n){
        b.onclick = function(){ send({type:'cmd', cmd:'preset', value:n}); };
      })(i);
      presetGrid.appendChild(b);
    }
  }
  // Preset buttons are essentially static. If we rebuild the grid on every state update
  // (which can be several times per second), a "normal" click-and-release can be lost
  // because the DOM element disappears before mouseup/click fires. So we only rebuild
  // when labels actually change.
  var _presetSig = '';
  var _presetsBuilt = false;

  function _presetSignature(pl){
    if (!pl) return '';
    var keys = [];
    for (var k in pl) { if (pl.hasOwnProperty(k)) keys.push(k); }
    keys.sort(function(a,b){ return parseInt(a,10) - parseInt(b,10); });
    var parts = [];
    for (var i=0; i<keys.length; i++){
      var kk = keys[i];
      parts.push(kk + '=' + String(pl[kk]));
    }
    return parts.join('|');
  }


  function applyState(msg){
    // msg schema: {type:'state', ver, state:{...}, logs:[...], preset_labels:{...}}
    var st = (msg && msg.state) ? msg.state : null;

    setVer((st && st.app_version) ? st.app_version : '');
    setTitle((st && st.banner_text) ? st.banner_text : 'READY');
    setBannerStyle((st && st.banner_style) ? st.banner_style : 'Ready.Banner.TLabel');
    setHealth((st && st.health) ? st.health : null);


    var lines = [];
    if (st && st.obs_line)  lines.push(st.obs_line);
    if (st && st.midi_line) lines.push(st.midi_line);
    if (st && st.cam_line)  lines.push(st.cam_line);
    if (st && st.timer_text) lines.push(st.timer_text);
    if (lines.length) setConn(lines.join("\n"));

    setAudio((st && st.audio) ? st.audio : null);
    applyToolMode(st || {});

    var trace = (st && st.camera_trace) ? st.camera_trace : null;
    if (traceWrap) traceWrap.style.display = (trace && trace.enabled) ? 'block' : 'none';
    if (lastSceneAction) lastSceneAction.textContent = (trace && trace.last_scene_action) ? ('Scene: ' + trace.last_scene_action) : '';
    if (cameraTraceBox) {
      var traceLines = (trace && trace.lines) ? trace.lines : [];
      cameraTraceBox.textContent = traceLines.join("\n");
    }

    var recOn = !!(st && st.rec_on);
    if (btnRec) {
      if (recOn) btnRec.classList.add('on');
      else btnRec.classList.remove('on');
    }

    if (msg && msg.logs && logBox) {
      logBox.textContent = msg.logs.join("\n");
      try { logBox.scrollTop = logBox.scrollHeight; } catch (e) {}
    }

        // Preset labels are sent as part of state. Build the buttons once, and only rebuild
    // if labels change (prevents missed clicks).
    var pl = null;
    if (msg && msg.preset_labels) {
      pl = {};
      for (var k in msg.preset_labels) {
        if (msg.preset_labels.hasOwnProperty(k)) {
          var nk = parseInt(k, 10);
          if (!isNaN(nk)) pl[nk] = msg.preset_labels[k];
        }
      }
    }

    var sig = _presetSignature(pl || {});
    if (!_presetsBuilt || sig !== _presetSig) {
      _presetSig = sig;
      _presetsBuilt = true;
      buildPresets(pl);
    }

  }

  function connect(){
    var url = wsUrl();
    setConn('Connecting WS: ' + url);

    wsReady = false;
    setEnabled(false);

    try { ws = new WebSocket(url); }
    catch (e) { setConn('WebSocket ctor failed: ' + e); return; }

    ws.onopen = function(){
      wsReady = true;
      setEnabled(true);
      setConn('Connected');
      send({type:'hello'});
    };

    ws.onmessage = function(ev){
      try {
        var msg = JSON.parse(ev.data);
        if (msg && msg.type === 'state') applyState(msg);
      } catch (e) {
        setConn('Bad message: ' + e);
      }
    };

    ws.onerror = function(){
      setConn('WebSocket error (see DevTools Console)');
    };

    ws.onclose = function(){
      wsReady = false;
      setEnabled(false);
      setConn('Disconnected — retrying in 2s');
      setTimeout(connect, 2000);
    };
  }

  // Safety: require a quick double-tap to confirm Start/Stop/REC on touch devices
  var _confirmUntil = { start: 0, stop: 0, rec: 0 };
  var _confirmMs = 2000;

  function _setBtnText(btn, t){ try { if (btn) btn.textContent = t; } catch (e) {} }
  function _getOrig(btn, fallback){
    try {
      if (!btn) return fallback;
      if (!btn.dataset) return fallback;
      if (!btn.dataset.origText) btn.dataset.origText = (btn.textContent || fallback);
      return btn.dataset.origText || fallback;
    } catch (e) { return fallback; }
  }

  function armOrSend(actionKey, btn, verbUpper, payload){
    try {
      var now = Date.now();
      var until = _confirmUntil[actionKey] || 0;
      var orig = _getOrig(btn, verbUpper);

      if (until && now < until) {
        _confirmUntil[actionKey] = 0;
        _setBtnText(btn, orig);
        send(payload);
        return;
      }

      _confirmUntil[actionKey] = now + _confirmMs;
      _setBtnText(btn, 'Tap again to ' + verbUpper);

      setTimeout(function(){
        try {
          if (Date.now() >= (_confirmUntil[actionKey] || 0)) {
            _confirmUntil[actionKey] = 0;
            _setBtnText(btn, orig);
          }
        } catch (e) {}
      }, _confirmMs + 50);
    } catch (e) {
      // If anything goes wrong, fall back to single-tap
      send(payload);
    }
  }

  if (btnStart) btnStart.onclick = function(){ armOrSend('start', btnStart, 'START', {type:'cmd', cmd:'start'}); };
  if (btnStop)  btnStop.onclick  = function(){ armOrSend('stop',  btnStop,  'STOP',  {type:'cmd', cmd:'stop'}); };
  if (btnRec)   btnRec.onclick   = function(){ armOrSend('rec',   btnRec,   'REC',   {type:'cmd', cmd:'rec'}); };
  if (btnSceneWest)   btnSceneWest.onclick = function(){ send({type:'cmd', cmd:'scene_west'}); };
  if (btnSceneEast)   btnSceneEast.onclick = function(){ send({type:'cmd', cmd:'scene_east'}); };
  if (audioSlider) {
    var pushAudio = function(){
      var v = parseFloat(audioSlider.value || '0');
      if (!isNaN(v)) send({type:'cmd', cmd:'audio_set', value_db:v});
    };
    audioSlider.onchange = pushAudio;
    audioSlider.onmouseup = pushAudio;
    audioSlider.ontouchend = pushAudio;
  }

  setEnabled(false);
  buildPresets(null);
  connect();
})();"""

    def _web_sync_js(self) -> str:
        return r"""(function(){
  'use strict';
  function $(id){ return document.getElementById(id); }
  function getTokenQS(){ try { var qs = window.location.search || ''; if (qs && qs.indexOf('token=') >= 0) return qs; } catch (e) {} return ''; }
  function withToken(url){ var qs = getTokenQS(); if (!qs) return url; return url + (url.indexOf('?') >= 0 ? '&' : '?') + qs.replace(/^\?/, ''); }
  function wsUrl(){ var proto = (window.location.protocol === 'https:') ? 'wss:' : 'ws:'; return proto + '//' + window.location.host + '/ws' + getTokenQS(); }
  function api(url, payload){
    return fetch(withToken(url), { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload || {}) }).then(function(r){
      return r.text().then(function(t){
        var j = null;
        try { j = t ? JSON.parse(t) : null; } catch(e) {}
        if (!r.ok) throw new Error((j && (j.error || j.message)) ? (j.error || j.message) : (t || ('HTTP ' + r.status)));
        return j || {ok:true};
      });
    });
  }

  var conn = $('syncConn');
  var statusEl = $('syncStatus');
  var lockPill = $('lockPill');
  var btnUnlock = $('btnUnlock');
  var btnZoom = $('btnZoom');
  var stepButtons = Array.prototype.slice.call(document.querySelectorAll('.stepBtn'));
  var localUnlockUntilMs = 0;
  var lastSync = null;
  var ws = null;

  function setConn(t){ if (conn) conn.textContent = t || ''; }
  function setStatus(t){ if (statusEl) statusEl.textContent = t || ''; }

  function applySync(sync){
    if (!sync) return;
    lastSync = sync;
    var disabledMode = (sync.enabled === false);
    if ($('sharedSyncHead')) $('sharedSyncHead').textContent = disabledMode ? 'Axis Embedded Audio' : 'Shared ASIO Audio';
    if ($('sharedSyncVal')) $('sharedSyncVal').textContent = disabledMode ? 'Disabled' : (String(sync.shared_ms || 0) + ' ms');
    if ($('sharedSyncMeta')) {
      if (disabledMode) $('sharedSyncMeta').textContent = 'Input(s): ' + (sync.shared_input || 'West_axis / East_axis');
      else $('sharedSyncMeta').textContent = (sync.shared_error ? ('Error: ' + sync.shared_error) : ('Input: ' + (sync.shared_input || '—')));
    }
    if ($('syncModeHint')) $('syncModeHint').textContent = disabledMode ? (sync.disabled_reason || 'Sync controls disabled in this audio mode.') : '';
    if ($('sharedSyncInput') && document.activeElement !== $('sharedSyncInput')) $('sharedSyncInput').value = String(sync.shared_ms || 0);
    if (disabledMode) {
      localUnlockUntilMs = 0;
    } else {
      localUnlockUntilMs = Math.max(localUnlockUntilMs, Date.now() + Math.max(0, Math.round((sync.unlock_remaining_s || 0) * 1000)));
    }
    refreshLockUi();
  }

  function refreshLockUi(){
    var disabledMode = !!(lastSync && lastSync.enabled === false);
    if (disabledMode) {
      if (lockPill) { lockPill.className = 'pill locked'; lockPill.textContent = 'DISABLED'; }
      if (btnUnlock) { btnUnlock.textContent = 'Sync disabled'; btnUnlock.disabled = true; }
      for (var i0=0; i0<stepButtons.length; i0++) stepButtons[i0].disabled = true;
      if ($('sharedApply')) $('sharedApply').disabled = true;
      if ($('sharedSyncInput')) $('sharedSyncInput').disabled = true;
      return;
    }
    if (btnUnlock) btnUnlock.disabled = false;
    if ($('sharedSyncInput')) $('sharedSyncInput').disabled = false;
    var remMs = Math.max(0, localUnlockUntilMs - Date.now());
    var remS = Math.ceil(remMs / 1000);
    var locked = remS <= 0;
    if (lastSync && typeof lastSync.locked === 'boolean' && locked && !lastSync.locked) locked = false;
    if (locked) {
      if (lockPill) { lockPill.className = 'pill locked'; lockPill.textContent = 'LOCKED'; }
      if (btnUnlock) btnUnlock.textContent = 'Unlock Sync';
    } else {
      if (lockPill) { lockPill.className = 'pill unlocked'; lockPill.textContent = 'UNLOCKED ' + remS + 's'; }
      if (btnUnlock) btnUnlock.textContent = 'Unlocked (' + remS + 's)';
    }
    for (var i=0; i<stepButtons.length; i++) stepButtons[i].disabled = locked;
    if ($('sharedApply')) $('sharedApply').disabled = locked;
  }

  function handleState(msg){
    if (!msg || msg.type !== 'state') return;
    var st = msg.state || {};
    var lines = [];
    if (st.obs_line) lines.push(st.obs_line);
    if (st.timer_text) lines.push(st.timer_text);
    setConn(lines.length ? lines.join('\\n') : 'Connected');
    applySync(st.sync || null);
  }

  function connect(){
    var url = wsUrl();
    setConn('Connecting: ' + url);
    try { ws = new WebSocket(url); } catch (e) { setConn('WebSocket failed: ' + e); return; }
    ws.onopen = function(){ setConn('Connected'); try { ws.send(JSON.stringify({type:'hello'})); } catch(e) {} };
    ws.onmessage = function(ev){ try { var msg = JSON.parse(ev.data); handleState(msg); } catch (e) { setConn('Bad state: ' + e); } };
    ws.onerror = function(){ setConn('WebSocket error — retrying'); };
    ws.onclose = function(){ setConn('Disconnected — retrying in 2s'); setTimeout(connect, 2000); };
  }

  function postUnlock(){
    api('/api/sync/unlock', {}).then(function(j){
      if (j && j.sync) applySync(j.sync);
      setStatus((j && j.message) ? j.message : 'Sync controls unlocked');
    }).catch(function(err){ setStatus('Unlock failed: ' + err); });
  }
  function adjust(delta){
    api('/api/sync/adjust', {delta_ms: delta}).then(function(j){
      if (j && j.sync) applySync(j.sync);
      setStatus((j && j.message) ? j.message : 'Adjusted shared audio sync');
    }).catch(function(err){ setStatus('Adjust failed: ' + err); });
  }
  function applyExact(){
    var el = $('sharedSyncInput');
    if (!el) return;
    var v = parseInt(el.value || '', 10);
    if (isNaN(v)) { setStatus('Enter a numeric sync value first'); return; }
    api('/api/sync/set', {value_ms: v}).then(function(j){
      if (j && j.sync) applySync(j.sync);
      setStatus((j && j.message) ? j.message : 'Applied shared audio sync');
    }).catch(function(err){ setStatus('Apply failed: ' + err); });
  }

  if (btnUnlock) btnUnlock.onclick = postUnlock;
  if (btnZoom) btnZoom.onclick = function(){
    var on = document.body.classList.toggle('zoomed');
    btnZoom.textContent = on ? 'Normal View' : 'Enlarge View';
  };
  for (var i=0; i<stepButtons.length; i++) {
    (function(btn){
      btn.onclick = function(){
        adjust(parseInt(btn.getAttribute('data-delta') || '0', 10) || 0);
      };
    })(stepButtons[i]);
  }
  if ($('sharedApply')) $('sharedApply').onclick = applyExact;

  refreshLockUi();
  setInterval(refreshLockUi, 250);
  connect();
})();"""

    def _web_director_js(self) -> str:
        return r"""(function(){
  'use strict';
  function $(id){ return document.getElementById(id); }
  function getTokenQS(){ try { var qs = window.location.search || ''; if (qs && qs.indexOf('token=') >= 0) return qs; } catch (e) {} return ''; }
  function withToken(url){
    var qs = getTokenQS();
    if (!qs) return url;
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + qs.replace(/^\?/, '');
  }
  function wsUrl(){ var proto = (window.location.protocol === 'https:') ? 'wss:' : 'ws:'; return proto + '//' + window.location.host + '/ws' + getTokenQS(); }
  function apiSteer(payload){
    return fetch(withToken('/api/director/steer'), {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload || {})
    }).then(function(r){
      return r.text().then(function(t){
        var j = null;
        try { j = t ? JSON.parse(t) : null; } catch(e) {}
        if (!r.ok) throw new Error((j && j.error) ? j.error : (t || ('HTTP ' + r.status)));
        return j || {ok:true};
      });
    });
  }

  var conn = $('dirConn');
  var ws = null;
  var presetLabels = null;
  var _dirButtonsBuilt = false;
  var _dirPresetSig = '';

  function setConn(t){ if (conn) conn.textContent = t; }

  var _dirAudioRange = {min:-40, max:6, db:0, meter:-60};
  var _dirAudioSendTimer = null;
  var _dirAudioLastSend = 0;

  function clamp(v, lo, hi){ return Math.max(lo, Math.min(hi, v)); }

  function dirAudioElems(){
    return {
      sliders: [$('dirAudioWest'), $('dirAudioEast')],
      dbs: [$('dirAudioDbWest'), $('dirAudioDbEast')],
      meters: [$('dirMeterWest'), $('dirMeterEast')],
      labels: [$('dirAudioLabelWest'), $('dirAudioLabelEast')]
    };
  }

  function sendDirectorAudio(valueDb, immediate){
    var v = parseFloat(valueDb);
    if (isNaN(v)) return;
    v = clamp(v, _dirAudioRange.min, _dirAudioRange.max);
    var now = Date.now();
    var doSend = function(){
      try {
        if (ws && ws.readyState === 1) {
          ws.send(JSON.stringify({type:'cmd', cmd:'audio_set', value_db:v}));
          _dirAudioLastSend = Date.now();
        } else {
          setConn('Audio fader waiting for WebSocket connection…');
        }
      } catch (e) {
        setConn('Audio fader send failed: ' + e);
      }
    };
    if (immediate || (now - _dirAudioLastSend) > 220) {
      if (_dirAudioSendTimer) { clearTimeout(_dirAudioSendTimer); _dirAudioSendTimer = null; }
      doSend();
    } else {
      if (_dirAudioSendTimer) clearTimeout(_dirAudioSendTimer);
      _dirAudioSendTimer = setTimeout(doSend, 220);
    }
  }

  function paintDirectorAudio(valueDb, meterDb, fromUser){
    var v = parseFloat(valueDb);
    if (isNaN(v)) v = _dirAudioRange.db;
    v = clamp(v, _dirAudioRange.min, _dirAudioRange.max);
    _dirAudioRange.db = v;
    var meter = (typeof meterDb === 'number') ? meterDb : _dirAudioRange.meter;
    _dirAudioRange.meter = meter;
    var pct = ((clamp(meter, -60, 0) + 60) / 60) * 100;
    var e = dirAudioElems();
    for (var i=0; i<e.sliders.length; i++){
      var sl = e.sliders[i];
      if (sl) {
        sl.min = String(_dirAudioRange.min);
        sl.max = String(_dirAudioRange.max);
        if (fromUser || document.activeElement !== sl) sl.value = String(v);
      }
      if (e.dbs[i]) e.dbs[i].textContent = v.toFixed(1) + ' dB';
      if (e.meters[i]) e.meters[i].style.height = pct.toFixed(1) + '%';
    }
  }

  function setDirectorAudio(audio){
    if (!audio) return;
    _dirAudioRange.min = (typeof audio.min_db === 'number') ? audio.min_db : -40;
    _dirAudioRange.max = (typeof audio.max_db === 'number') ? audio.max_db : 6;
    var db = (typeof audio.master_db === 'number') ? audio.master_db : 0;
    var meter = (typeof audio.meter_db === 'number') ? audio.meter_db : -60;
    var label = audio.mode === 'asio_shared' ? 'ASIO' : 'Axis';
    var e = dirAudioElems();
    for (var i=0; i<e.labels.length; i++){ if (e.labels[i]) e.labels[i].textContent = label + '\nMaster'; }
    paintDirectorAudio(db, meter, false);
  }

  function bindDirectorAudioFader(id){
    var sl = $(id);
    if (!sl) return;
    var onMove = function(immediate){
      var v = parseFloat(sl.value || '0');
      if (isNaN(v)) return;
      paintDirectorAudio(v, _dirAudioRange.meter, true);
      sendDirectorAudio(v, !!immediate);
    };
    sl.oninput = function(){ onMove(false); };
    sl.onchange = function(){ onMove(true); };
    sl.onmouseup = function(){ onMove(true); };
    sl.ontouchend = function(){ onMove(true); };
    sl.onkeyup = function(ev){ if (ev && (ev.key === 'Enter' || ev.key === ' ')) onMove(true); };
  }

  function _dirPresetSignature(obj){
    var keys = [];
    if (obj) {
      for (var k in obj) { if (obj.hasOwnProperty(k)) keys.push(parseInt(k, 10)); }
    }
    keys.sort(function(a,b){ return a-b; });
    var out = [];
    for (var i=0; i<keys.length; i++){
      var kk = keys[i];
      out.push(String(kk) + '=' + String(obj[kk]));
    }
    return out.join('|');
  }

  function makeButtons(cam, hostId){
    var host = $(hostId);
    if (!host) return;
    while (host.firstChild) host.removeChild(host.firstChild);
    for (var i=1; i<=10; i++){
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'pbtn';
      b.dataset.camera = cam;
      b.dataset.view = String(i);
      var label = (presetLabels && presetLabels[i]) ? presetLabels[i] : ('View ' + i);
      b.textContent = i + ': ' + label;
      b.title = label;
      (function(cameraKey, n){
        b.onclick = function(ev){
          try { if (ev && ev.preventDefault) ev.preventDefault(); } catch (e) {}
          setConn('Sending ' + cameraKey + ' view ' + n + '…');
          apiSteer({action:'direct_cam_view', camera:cameraKey, view:n}).then(function(){
            setConn('Queued ' + cameraKey + ' view ' + n);
          }).catch(function(err){
            setConn('Director command failed: ' + err);
          });
        };
      })(cam, i);
      host.appendChild(b);
    }
  }

  function rebuildButtonsIfNeeded(){
    var sig = _dirPresetSignature(presetLabels || {});
    if (!_dirButtonsBuilt || sig !== _dirPresetSig) {
      _dirPresetSig = sig;
      _dirButtonsBuilt = true;
      makeButtons('west', 'gridWest');
      makeButtons('east', 'gridEast');
    }
  }

  function setActive(panelId, pillId, live){
    var panel = $(panelId), pill = $(pillId);
    var upper = String(panelId || '').replace('panel', '');
    var video = upper ? $('video' + upper) : null;
    if (panel) panel.className = 'panel ' + (live ? 'live' : 'standby');
    if (video) video.className = 'videoWrap ' + (live ? 'live' : 'standby');
    if (pill) { pill.className = 'pill ' + (live ? 'live' : 'standby'); pill.textContent = live ? 'LIVE' : 'STANDBY'; }
  }

  function fmtReady(camState){
    if (!camState) return 'Ready: —';
    if (camState.blind_delay_active) {
      var br = Number(camState.blind_delay_remaining_s || 0);
      if (br > 0.05) return 'Blind wait: ' + br.toFixed(1) + 's';
      return 'Ready: now';
    }
    var s = camState.ready_in_s;
    if (typeof s !== 'number') return 'Ready: —';
    if (s <= 0.05) return 'Ready: now';
    return 'Ready in: ' + s.toFixed(1) + 's';
  }

  function readyClass(camState){
    if (!camState) return '';
    if (camState.blind_delay_active && Number(camState.blind_delay_remaining_s || 0) > 0.05) return 'readyHold';
    if (typeof camState.ready_in_s === 'number' && camState.ready_in_s > 0.05) return 'readyMove';
    return 'readyNow';
  }

  function applyCamera(camKey, camState, isLive){
    var upper = camKey.charAt(0).toUpperCase() + camKey.slice(1);
    setActive('panel' + upper, 'pill' + upper, isLive);
    if (!camState) return;
    var viewEl = $(camKey + 'View');
    var readyEl = $(camKey + 'Ready');
    var statusEl = $(camKey + 'Status');
    if (viewEl) viewEl.textContent = 'View: ' + (camState.current_label || '—');
    if (readyEl) { readyEl.textContent = fmtReady(camState); readyEl.className = readyClass(camState); }
    if (statusEl) statusEl.textContent = camState.status || '';
  }

  function SnapshotPreview(camKey){
    this.camKey = camKey;
    var upper = camKey.charAt(0).toUpperCase() + camKey.slice(1);
    this.img = $('img' + upper);
    this.off = $('off' + upper);
    this.stamp = $('stamp' + upper);
    this.lastSeq = 0;
  }
  SnapshotPreview.prototype.urlForSeq = function(seq, preview){
    var backend = (preview && preview.backend) ? String(preview.backend) : 'obs_screenshot';
    var mjpeg = (backend === 'obs_mjpeg_stream' || backend === 'axis_mjpeg');
    var url = mjpeg ? ('/cam/mjpg/' + this.camKey + '.mjpg') : ('/cam/snapshot/' + this.camKey + '.jpg');
    url = withToken(url);
    if (mjpeg) return url;
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'seq=' + encodeURIComponent(String(seq || 0));
  };
  SnapshotPreview.prototype.setOffline = function(flag, msg){
    if (this.off) {
      this.off.style.display = flag ? 'block' : 'none';
      if (msg) this.off.textContent = msg;
    }
  };
  SnapshotPreview.prototype.setStamp = function(text){ if (this.stamp) this.stamp.textContent = text || ''; };
  SnapshotPreview.prototype.noteLoaded = function(seq, updatedTs, preview){
    this.lastSeq = seq || 0;
    var backend = (preview && preview.backend) ? preview.backend : 'preview';
    var stamp = '';
    try { if (updatedTs) stamp = new Date(updatedTs * 1000).toLocaleTimeString(); } catch(e) {}
    var label = 'Preview ';
    if (backend === 'obs_screenshot') label = 'OBS preview ';
    else if (backend === 'obs_mjpeg_stream') label = 'OBS MJPEG ';
    else if (backend === 'axis_mjpeg') label = 'Axis MJPEG ';
    this.setStamp(label + (stamp || ('seq ' + String(seq || 0))));
  };
  SnapshotPreview.prototype.maybeApply = function(info, preview){
    if (!this.img || !info) return;
    var seq = parseInt(info.seq || 0, 10) || 0;
    var ok = !!info.ok;
    var stale = !!info.stale;
    if (!preview || preview.enabled === false) {
      this.setOffline(true, 'Preview disabled');
      this.setStamp('Preview disabled');
      return;
    }
    var backend = (preview && preview.backend) ? String(preview.backend) : 'obs_screenshot';
    var mjpeg = (backend === 'obs_mjpeg_stream' || backend === 'axis_mjpeg');
    if (!ok) {
      this.setOffline(true, info.error || 'Preview reconnecting…');
      this.setStamp(info.error ? ('Preview error: ' + info.error) : 'Preview waiting…');
      if (mjpeg) {
        var offlineUrl = this.urlForSeq(seq, preview);
        if (this.img.src !== offlineUrl) this.img.src = offlineUrl;
      } else if (seq > 0 && seq !== this.lastSeq) {
        this.img.src = this.urlForSeq(seq, preview);
        this.noteLoaded(seq, info.updated_ts || 0, preview);
      }
      return;
    }
    this.setOffline(!!stale, stale ? 'Preview stale — waiting for refresh…' : '');
    if (mjpeg) {
      var liveUrl = this.urlForSeq(seq, preview);
      if (this.img.src !== liveUrl) this.img.src = liveUrl;
      if (seq > 0 && seq !== this.lastSeq) this.noteLoaded(seq, info.updated_ts || 0, preview);
      return;
    }
    if (seq > 0 && seq !== this.lastSeq) {
      this.img.src = this.urlForSeq(seq, preview);
      this.noteLoaded(seq, info.updated_ts || 0, preview);
    }
  };

  var westPreview = new SnapshotPreview('west');
  var eastPreview = new SnapshotPreview('east');

  function applyState(msg){
    if (!msg) return;
    if (msg.preset_labels) {
      presetLabels = {};
      for (var k in msg.preset_labels) { if (msg.preset_labels.hasOwnProperty(k)) presetLabels[parseInt(k,10)] = msg.preset_labels[k]; }
    }
    rebuildButtonsIfNeeded();
    var st = msg.state || {};
    setDirectorAudio(st.audio || null);
    var d = st.director || {};
    var liveCam = d.program_camera || '';
    applyCamera('west', d.west || null, liveCam === 'west');
    applyCamera('east', d.east || null, liveCam === 'east');
    var preview = d.preview || {};
    westPreview.maybeApply(preview.west || null, preview);
    eastPreview.maybeApply(preview.east || null, preview);
    var lines = [];
    if (st.obs_line) lines.push(st.obs_line);
    if (st.midi_line) lines.push(st.midi_line);
    if (st.cam_line) lines.push(st.cam_line);
    if (st.timer_text) lines.push(st.timer_text);
    if (preview && preview.backend) lines.push('Director preview: ' + preview.backend + ' @ ' + String(preview.width || 0) + 'x' + String(preview.height || 0));
    setConn(lines.length ? lines.join('\n') : 'Connected');
  }

  function connect(){
    var url = wsUrl();
    setConn('Connecting: ' + url);
    try { ws = new WebSocket(url); } catch (e) { setConn('WebSocket failed: ' + e); return; }
    ws.onopen = function(){ setConn('Connected'); try { ws.send(JSON.stringify({type:'hello'})); } catch(e) {} };
    ws.onmessage = function(ev){ try { var msg = JSON.parse(ev.data); if (msg && msg.type === 'state') applyState(msg); } catch (e) { setConn('Bad state: ' + e); } };
    ws.onerror = function(){ setConn('WebSocket error — retrying'); };
    ws.onclose = function(){ setConn('Disconnected — retrying in 2s'); setTimeout(connect, 2000); };
  }

  var btnCutWest = $('btnCutWest');
  var btnCutEast = $('btnCutEast');
  var btnCutWestTop = $('btnCutWestTop');
  var btnCutEastTop = $('btnCutEastTop');
  function cutWest(){
    setConn('Sending scene_west…');
    apiSteer({action:'scene_west'}).then(function(){ setConn('Queued scene_west'); }).catch(function(err){ setConn('Director command failed: ' + err); });
  }
  function cutEast(){
    setConn('Sending scene_east…');
    apiSteer({action:'scene_east'}).then(function(){ setConn('Queued scene_east'); }).catch(function(err){ setConn('Director command failed: ' + err); });
  }
  if (btnCutWest) btnCutWest.onclick = cutWest;
  if (btnCutEast) btnCutEast.onclick = cutEast;
  if (btnCutWestTop) btnCutWestTop.onclick = cutWest;
  if (btnCutEastTop) btnCutEastTop.onclick = cutEast;

  bindDirectorAudioFader('dirAudioWest');
  bindDirectorAudioFader('dirAudioEast');
  rebuildButtonsIfNeeded();
  connect();
})();"""

    async def _start_web_server(self):
        if not self.cfg.WEB_HUD_ENABLED:
            self._post("WEB: HUD disabled by config (WEB_HUD_ENABLED = False)")
            return

        self._post(f"WEB: starting HUD on {self.cfg.WEB_HUD_HOST}:{int(self.cfg.WEB_HUD_PORT)}")
        try:
            from aiohttp import web, WSMsgType
        except Exception as e:
            self._post(f"WEB: aiohttp import failed: {e} — install with: py -m pip install aiohttp")
            return

        async def index(request):
            # optional token check (only if configured)
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")
            return web.Response(text=self._web_html(), content_type="text/html", charset="utf-8")


        async def viewer(request):
            # optional token check (only if configured)
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")
            return web.Response(text=self._web_viewer_html(), content_type="text/html", charset="utf-8")

        async def manual_page(request):
            # optional token check (only if configured)
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")
            return web.Response(text=self._web_manual_html(), content_type="text/html", charset="utf-8")

        async def sync_page(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")
            return web.Response(text=self._web_sync_html(), content_type="text/html", charset="utf-8")

        async def director(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")
            return web.Response(text=self._web_director_html(), content_type="text/html", charset="utf-8")

        async def preflight(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")
            return web.Response(text=self._web_preflight_html(), content_type="text/html", charset="utf-8")

        async def api_health_report(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")
            try:
                report = await asyncio.to_thread(self._health_report_snapshot, True)
                return web.json_response(report)
            except Exception as e:
                return web.json_response({"overall": "NO-GO", "title": "NO-GO — Health report error", "summary": str(e), "checks": []}, status=500)

        async def health(request):
            return web.Response(text="ok", content_type="text/plain", charset="utf-8")

        async def ws_handler(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")

            ws = web.WebSocketResponse(heartbeat=20)
            await ws.prepare(request)

            self._ws_clients.add(ws)
            # Send an immediate snapshot
            await ws.send_str(json.dumps(self._web_payload()))

            try:
                async for msg in ws:
                    if msg.type == WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                        except Exception:
                            continue
                        if data.get("type") == "cmd":
                            try:
                                cmd = data.get("cmd")
                                if cmd in ("start", "stop", "rec", "scene_west", "scene_east"):
                                    await self._cmd_queue.put({"type": "action", "action": cmd, "source": "WEB"})
                                elif cmd == "preset":
                                    val = int(data.get("value", 0))
                                    await self._cmd_queue.put({"type": "preset", "preset": val, "source": "WEB"})
                                elif cmd == "audio_set":
                                    val = float(data.get("value_db", 0.0))
                                    await self._cmd_queue.put({"type": "audio", "value_db": val, "source": "WEB"})
                                elif cmd == "direct_cam_view":
                                    cam = str(data.get("camera", "") or "").strip().lower()
                                    view = int(data.get("view", 0) or 0)
                                    await self._cmd_queue.put({"type": "direct_cam_view", "camera": cam, "view": view, "source": "WEB"})
                            except Exception as e:
                                self._post(f"WEB: ignored bad websocket command ({e})")
                    elif msg.type == WSMsgType.ERROR:
                        break
            finally:
                self._ws_clients.discard(ws)
                try:
                    await ws.close()
                except Exception:
                    pass

            return ws

        async def app_js(request):
            return web.Response(text=self._web_js(), content_type="application/javascript", charset="utf-8")

        async def director_js(request):
            return web.Response(text=self._web_director_js(), content_type="application/javascript", charset="utf-8")

        async def sync_js(request):
            return web.Response(text=self._web_sync_js(), content_type="application/javascript", charset="utf-8")

        async def preflight_js(request):
            return web.Response(text=self._web_preflight_js(), content_type="application/javascript", charset="utf-8")

        async def cam_snapshot(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")
            cam_key = str(request.match_info.get("cam", "") or "").strip().lower()
            if cam_key not in ("west", "east"):
                return web.Response(status=404, text="Unknown camera")

            backend = self._director_preview_backend()
            if backend == "axis_mjpeg":
                try:
                    width = max(8, min(4096, int(getattr(self.cfg, "DIRECTOR_PREVIEW_WIDTH", 640) or 640)))
                    height = max(8, min(4096, int(getattr(self.cfg, "DIRECTOR_PREVIEW_HEIGHT", 360) or 360)))
                    data, ctype = await asyncio.to_thread(self._fetch_axis_snapshot_bytes, cam_key, width, height)
                    return web.Response(body=data, content_type=(ctype or "image/jpeg"), headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"})
                except Exception as e:
                    return web.Response(status=502, text=f"Preview fetch failed: {e}")

            cache = (getattr(self, "_director_preview_cache", {}) or {}).get(cam_key, {}) or {}
            data = cache.get("data", b"") or b""
            ctype = str(cache.get("content_type", "image/jpeg") or "image/jpeg")
            if not data:
                try:
                    self._refresh_director_previews()
                    cache = (getattr(self, "_director_preview_cache", {}) or {}).get(cam_key, {}) or {}
                    data = cache.get("data", b"") or b""
                    ctype = str(cache.get("content_type", "image/jpeg") or "image/jpeg")
                except Exception:
                    pass
            if not data:
                err = str(cache.get("error", "Preview unavailable") or "Preview unavailable")
                return web.Response(status=503, text=err)
            return web.Response(body=data, content_type=ctype, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"})

        async def cam_mjpg(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")
            cam_key = str(request.match_info.get("cam", "") or "").strip().lower()
            if cam_key not in ("west", "east"):
                return web.Response(status=404, text="Unknown camera")

            backend = self._director_preview_backend()
            if backend not in ("axis_mjpeg", "obs_mjpeg_stream"):
                target = f"/cam/snapshot/{cam_key}.jpg"
                if self.cfg.WEB_HUD_TOKEN:
                    target += ("?token=" + self.cfg.WEB_HUD_TOKEN)
                raise web.HTTPFound(target)

            if backend == "obs_mjpeg_stream":
                headers = {
                    "Content-Type": "multipart/x-mixed-replace; boundary=frame",
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "Connection": "close",
                }
                resp = web.StreamResponse(status=200, headers=headers)
                await resp.prepare(request)
                last_seq = -1
                empty_jpeg = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")
                poll_s = max(0.04, min(0.5, float(getattr(self.cfg, "DIRECTOR_PREVIEW_REFRESH_MS", 500) or 500) / 2000.0))
                try:
                    while self.running:
                        cache = (getattr(self, "_director_preview_cache", {}) or {}).get(cam_key, {}) or {}
                        seq = int(cache.get("seq", 0) or 0)
                        data = cache.get("data", b"") or b""
                        ctype = str(cache.get("content_type", "image/jpeg") or "image/jpeg")
                        if not data:
                            try:
                                self._refresh_director_previews()
                                cache = (getattr(self, "_director_preview_cache", {}) or {}).get(cam_key, {}) or {}
                                seq = int(cache.get("seq", 0) or 0)
                                data = cache.get("data", b"") or b""
                                ctype = str(cache.get("content_type", "image/jpeg") or "image/jpeg")
                            except Exception:
                                pass
                        if seq != last_seq:
                            jpeg_bytes = data if data else empty_jpeg
                            part = (
                                b"--frame\r\n"
                                b"Content-Type: " + ctype.encode("ascii", "ignore") + b"\r\n\r\n" +
                                jpeg_bytes + b"\r\n"
                            )
                            await resp.write(part)
                            last_seq = seq
                        await asyncio.sleep(poll_s)
                except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                    pass
                finally:
                    try:
                        await resp.write_eof()
                    except Exception:
                        pass
                return resp

            upstream = None
            try:
                width = max(8, min(4096, int(getattr(self.cfg, "DIRECTOR_PREVIEW_WIDTH", 640) or 640)))
                height = max(8, min(4096, int(getattr(self.cfg, "DIRECTOR_PREVIEW_HEIGHT", 360) or 360)))
                upstream, ctype = await asyncio.to_thread(self._open_axis_mjpeg_stream, cam_key, width, height, 0)
                headers = {
                    "Content-Type": (ctype or "multipart/x-mixed-replace"),
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "Connection": "close",
                }
                resp = web.StreamResponse(status=200, headers=headers)
                await resp.prepare(request)
                try:
                    while True:
                        chunk = await asyncio.to_thread(upstream.read, 65536)
                        if not chunk:
                            break
                        await resp.write(chunk)
                except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                    pass
                finally:
                    try:
                        await resp.write_eof()
                    except Exception:
                        pass
                return resp
            except Exception as e:
                return web.Response(status=502, text=f"MJPEG proxy failed: {e}")
            finally:
                if upstream is not None:
                    try:
                        await asyncio.to_thread(upstream.close)
                    except Exception:
                        pass

        async def favicon(request):
            # avoid noisy 404s
            return web.Response(status=204, text="")


        async def config_page(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")
            return web.Response(text=self._web_config_html(), content_type="text/html", charset="utf-8")

        async def preset_delay_help_page(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")
            if not bool(getattr(self.cfg, "CONFIG_HELP_ENABLED", True)):
                return web.Response(status=404, text="Config help is disabled")
            return web.Response(text=self._web_preset_delay_help_html(), content_type="text/html", charset="utf-8")

        async def camera_config_help_page(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")
            if not bool(getattr(self.cfg, "CONFIG_HELP_ENABLED", True)):
                return web.Response(status=404, text="Config help is disabled")
            return web.Response(text=self._web_camera_config_help_html(), content_type="text/html", charset="utf-8")

        async def config_timer_page(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")
            return web.Response(text=self._web_config_timer_html(), content_type="text/html", charset="utf-8")

        async def config_js(request):
            return web.Response(text=self._web_config_js(), content_type="application/javascript", charset="utf-8")

        def _remote_ip(request):
            try:
                # Try X-Forwarded-For first (if ever proxied), else peername
                xff = request.headers.get("X-Forwarded-For", "")
                if xff:
                    return xff.split(",")[0].strip()
                peer = request.transport.get_extra_info("peername")
                if peer and isinstance(peer, (list, tuple)) and peer:
                    return str(peer[0])
            except Exception:
                pass
            return ""

        async def api_get_config(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.json_response({"error": "Forbidden"}, status=403)
            scope = request.query.get("scope", "general")
            return web.json_response(self._cfg_snapshot(scope))


        async def config_field_help_page(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")
            if not bool(getattr(self.cfg, "CONFIG_HELP_ENABLED", True)):
                return web.Response(status=404, text="Config help is disabled")
            return web.Response(text=self._web_config_field_help_html(), content_type="text/html", charset="utf-8")

        async def api_unlock(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.json_response({"error": "Forbidden"}, status=403)
            data = {}
            try:
                data = await request.json()
            except Exception:
                data = {}
            minutes = data.get("minutes", 2)
            self._cfg_unlock_for_minutes(minutes)
            _cfg_append_changelog({"source": "WEB", "remote_ip": _remote_ip(request), "event": "unlock", "minutes": minutes})
            return web.json_response({"ok": True, "unlock_remaining_s": max(0.0, self._cfg_unlock_until - time.time())})

        async def api_apply_config(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.json_response({"error": "Forbidden"}, status=403)
            try:
                data = await request.json()
            except Exception:
                return web.json_response({"error": "Invalid JSON"}, status=400)

            changes = data.get("changes", {})
            try:
                msg = self._cfg_apply_changes(changes, source="WEB", remote_ip=_remote_ip(request))
                return web.json_response({"ok": True, "message": msg})
            except Exception as e:
                return web.json_response({"error": str(e)}, status=400)

        async def api_restore_global(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.json_response({"error": "Forbidden"}, status=403)
            try:
                msg = self._cfg_restore_global(source="WEB", remote_ip=_remote_ip(request))
                return web.json_response({"ok": True, "message": msg})
            except Exception as e:
                return web.json_response({"error": str(e)}, status=400)

        async def api_restore_timer(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.json_response({"error": "Forbidden"}, status=403)
            try:
                msg = self._cfg_restore_timer_only(source="WEB", remote_ip=_remote_ip(request))
                return web.json_response({"ok": True, "message": msg})
            except Exception as e:
                return web.json_response({"error": str(e)}, status=400)

        async def api_restore_fields(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.json_response({"error": "Forbidden"}, status=403)
            try:
                data = await request.json()
            except Exception:
                return web.json_response({"error": "Invalid JSON"}, status=400)
            try:
                keys = data.get("keys", []) if isinstance(data, dict) else []
                msg = self._cfg_restore_selected_fields(keys, source="WEB", remote_ip=_remote_ip(request))
                return web.json_response({"ok": True, "message": msg})
            except Exception as e:
                return web.json_response({"error": str(e)}, status=400)

        async def api_export(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.json_response({"error": "Forbidden"}, status=403)
            payload = self._cfg_export_payload()
            return web.Response(
                text=json.dumps(payload, indent=2, sort_keys=True),
                content_type="application/json",
                charset="utf-8",
                headers={"Content-Disposition": "attachment; filename=config_overrides_export.json"},
            )

        async def api_import(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.json_response({"error": "Forbidden"}, status=403)
            try:
                data = await request.json()
            except Exception:
                return web.json_response({"error": "Invalid JSON"}, status=400)
            try:
                msg = self._cfg_import_payload(data, source="WEB", remote_ip=_remote_ip(request))
                return web.json_response({"ok": True, "message": msg})
            except Exception as e:
                return web.json_response({"error": str(e)}, status=400)

        async def _sync_err_status(msg: str) -> int:
            txt = str(msg or "").lower()
            if "locked" in txt:
                return 403
            return 400

        async def api_sync_unlock(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.json_response({"error": "Forbidden"}, status=403)
            ok, msg = self._sync_unlock("WEB SYNC")
            status = 200 if ok else 400
            return web.json_response({"ok": ok, "message": msg, "sync": self._sync_offsets_snapshot()}, status=status)

        async def api_sync_adjust(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.json_response({"error": "Forbidden"}, status=403)
            try:
                data = await request.json()
            except Exception:
                return web.json_response({"error": "Invalid JSON"}, status=400)
            try:
                delta_ms = int((data or {}).get("delta_ms", 0) or 0)
            except Exception:
                return web.json_response({"error": "Invalid delta"}, status=400)
            ok, msg = self._sync_adjust_offset(delta_ms, "WEB SYNC")
            return web.json_response({"ok": ok, "message": msg, "sync": self._sync_offsets_snapshot()}, status=(200 if ok else (403 if "locked" in str(msg).lower() else 400)))

        async def api_sync_set(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.json_response({"error": "Forbidden"}, status=403)
            try:
                data = await request.json()
            except Exception:
                return web.json_response({"error": "Invalid JSON"}, status=400)
            try:
                value_ms = int((data or {}).get("value_ms", 0) or 0)
            except Exception:
                return web.json_response({"error": "Invalid value"}, status=400)
            ok, msg = self._sync_set_offset(value_ms, "WEB SYNC")
            return web.json_response({"ok": ok, "message": msg, "sync": self._sync_offsets_snapshot()}, status=(200 if ok else (403 if "locked" in str(msg).lower() else 400)))

        async def api_sync_copy(request):
            return web.json_response({"error": "Copy is not used with the shared ASIO audio source."}, status=400)

        async def api_director_steer(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.json_response({"error": "Forbidden"}, status=403)
            try:
                data = await request.json()
            except Exception:
                return web.json_response({"error": "Invalid JSON"}, status=400)

            action = str((data or {}).get("action", "") or "").strip().lower()
            source = "WEB"

            try:
                if action in ("scene_west", "scene_east"):
                    self._enqueue_cmd({"type": "action", "action": action, "source": source})
                    self._post(f"WEB DIRECTOR: queued {action}")
                    return web.json_response({"ok": True, "message": action})

                if action == "direct_cam_view":
                    camera = str((data or {}).get("camera", "") or "").strip().lower()
                    view = int((data or {}).get("view", 0) or 0)
                    if camera not in ("west", "east"):
                        return web.json_response({"error": f"Invalid camera '{camera}'"}, status=400)
                    if not (1 <= view <= 10):
                        return web.json_response({"error": f"Invalid view '{view}'"}, status=400)
                    self._enqueue_cmd({"type": "direct_cam_view", "camera": camera, "view": view, "source": source})
                    self._post(f"WEB DIRECTOR: queued direct_cam_view camera={camera} view={view}")
                    return web.json_response({"ok": True, "message": f"{camera}:{view}"})

                return web.json_response({"error": "Unknown action"}, status=400)
            except Exception as e:
                return web.json_response({"error": str(e)}, status=400)


        app = web.Application()
        app.add_routes([
            web.get("/", index),
            web.get("/viewer", viewer),
            web.get("/manual", manual_page),
            web.get("/sync", sync_page),
            web.get("/director", director),
            web.get("/preflight", preflight),
            web.get("/health", health),
            web.get("/api/health_report", api_health_report),
            web.get("/config", config_page),
            web.get("/help/preset_delays", preset_delay_help_page),
            web.get("/help/cameras", camera_config_help_page),
            web.get("/help/config", config_field_help_page),
            web.get("/config_timer", config_timer_page),
            web.get("/ws", ws_handler),
            web.get("/app.js", app_js),
            web.get("/director.js", director_js),
            web.get("/sync.js", sync_js),
            web.get("/preflight.js", preflight_js),
            web.get(r"/cam/snapshot/{cam}.jpg", cam_snapshot),
            web.get(r"/cam/mjpg/{cam}.mjpg", cam_mjpg),
            web.get("/config.js", config_js),
            web.get("/api/config", api_get_config),
            web.post("/api/config/unlock", api_unlock),
            web.post("/api/config/apply", api_apply_config),
            web.post("/api/config/restore_global", api_restore_global),
            web.post("/api/config/restore_timer", api_restore_timer),
            web.post("/api/config/restore_fields", api_restore_fields),
            web.get("/api/config/export", api_export),
            web.post("/api/config/import", api_import),
            web.post("/api/sync/unlock", api_sync_unlock),
            web.post("/api/sync/adjust", api_sync_adjust),
            web.post("/api/sync/set", api_sync_set),
            web.post("/api/sync/copy", api_sync_copy),
            web.post("/api/director/steer", api_director_steer),
            web.get("/favicon.ico", favicon),
        ])

        try:
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, host=self.cfg.WEB_HUD_HOST, port=int(self.cfg.WEB_HUD_PORT))
            await site.start()
        except Exception as e:
            self._post(f"WEB: failed to start HUD on {self.cfg.WEB_HUD_HOST}:{int(self.cfg.WEB_HUD_PORT)} -> {e}")
            try:
                await runner.cleanup()
            except Exception:
                pass
            return

        self._web_runner = runner
        self._web_site = site
        self._post(f"WEB: HUD at http://{self._local_ip_hint()}:{int(self.cfg.WEB_HUD_PORT)}")
        self._post(f"WEB: health URL http://127.0.0.1:{int(self.cfg.WEB_HUD_PORT)}/health")
        self._post(f"WEB: token enabled = {bool(self.cfg.WEB_HUD_TOKEN)}")
        self._post(f"WEB: local test URL http://127.0.0.1:{int(self.cfg.WEB_HUD_PORT)}")

    async def _stop_web_server(self):
        try:
            # Close clients
            for ws in list(self._ws_clients):
                try:
                    await ws.close()
                except Exception:
                    pass
            self._ws_clients.clear()
            if self._web_runner:
                await self._web_runner.cleanup()
        except Exception:
            pass
        finally:
            self._web_runner = None
            self._web_site = None

    def _local_ip_hint(self) -> str:
        # Best-effort: pick a non-loopback address
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    async def _broadcast_web_state_if_dirty(self):
        if not self.cfg.WEB_HUD_ENABLED or not self._ws_clients:
            return

        dirty = False
        with self._ui_lock:
            if self._web_dirty:
                dirty = True
                self._web_dirty = False

        if not dirty:
            return

        payload = json.dumps(self._web_payload())
        dead = []
        for ws in list(self._ws_clients):
            try:
                await ws.send_str(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._ws_clients.discard(ws)

    async def loop(self):
        self._async_loop = asyncio.get_running_loop()
        self._cmd_queue = asyncio.Queue()
        await self._start_web_server()

        startup_grace = 20.0
        while self.running:
            await self._drain_cmds()
            if not self.obs.connected and self.cfg.AUTO_RECONNECT_OBS:
                if self.obs.connect():
                    self._post("OBS connected")
                    self._obs_connected_at = time.time()
                    self._obs_profile_checked_on_connect = False
                    self._obs_profile_check_last_attempt = 0.0
                    self._audio_mode_logged = False
                    self._audio_mode_status_next_at = 0.0
                    # Rebuild OBS event subscriptions after every reconnect. Without
                    # this, a stale audio-meter EventClient can survive an OBS restart
                    # and leave the Web HUD meter flat even though OBS audio is fine.
                    self._stop_audio_events_if_possible()
                    self._start_audio_events_if_possible()
                    # Pull the current locked volume from the configured audio target if available.
                    for _audio_target in self._audio_inputs():
                        vdb, _ = self.obs.get_input_volume_db(_audio_target)
                        if vdb is not None:
                            self.audio_master_db = float(vdb)
                            break
                    self._sync_offsets_next_poll = 0.0
                    self._sync_offsets_poll_tick(force=True)
            # Verify audio targets after OBS has finished warming up. A first attempt can
            # legitimately hit OBS WebSocket code 207 / NotReady during OBS startup.
            if self.obs.connected and not getattr(self, "_audio_mode_logged", False):
                now_audio_check = time.time()
                if now_audio_check >= float(getattr(self, "_audio_mode_status_next_at", 0.0) or 0.0):
                    if self._log_audio_mode_status():
                        self._audio_mode_logged = True
                    else:
                        self._audio_mode_status_next_at = now_audio_check + 2.0

             # Early OBS profile mismatch check (warn/switch ASAP after connect; retry while OBS warms up)
            if self.obs.connected and getattr(self.cfg, "OBS_PROFILE_CHECK_ENABLED", False) and not getattr(self, "_obs_profile_checked_on_connect", False):
                now = time.time()
                if now - getattr(self, "_obs_profile_check_last_attempt", 0.0) >= 5.0:
                    self._obs_profile_check_last_attempt = now
                    expected_raw = str(getattr(self.cfg, "OBS_EXPECTED_PROFILE_NAME", "") or "")
                    expected = expected_raw.strip()
                    if expected:
                        current, perr = self.obs.get_current_profile_name()
                        current_clean = str(current or "").strip()
                        if perr:
                            # OBS may still be initializing; keep retrying quietly.
                            pass
                        else:
                            if current_clean == expected:
                                if str(current) != expected:
                                    self._post(f"OBS: WARN — profile whitespace differs; current='{current}' expected='{expected}' — treating as match")
                                self._obs_profile_checked_on_connect = True
                            else:
                                action = (getattr(self.cfg, "OBS_PROFILE_MISMATCH_ACTION", "block") or "block").lower()
                                msg = f"OBS profile mismatch: current='{current}' expected='{expected}'"

                                if action == "warn":
                                    self._post(f"OBS: WARN — {msg}")
                                    self._obs_profile_checked_on_connect = True
                                elif action == "switch":
                                    # Do not attempt to switch profiles while OBS is streaming or recording.
                                    streaming, recording, serr = self.obs.get_status()
                                    if (not serr) and (streaming or recording):
                                        self._post(f"OBS: WARN — {msg} (cannot auto-switch while streaming/recording)")
                                        self._obs_profile_checked_on_connect = True
                                    else:
                                        ok, sw_err = self.obs.set_current_profile_name(expected)
                                        if ok:
                                            self._post(f"OBS: switched profile to '{expected}'")
                                            self._obs_profile_checked_on_connect = True
                                        else:
                                            # If OBS rejects the switch with a stable error like code 600,
                                            # stop retrying so the console does not get spammed forever.
                                            last_err = getattr(self, "_obs_profile_switch_last_err", "")
                                            last_log = getattr(self, "_obs_profile_switch_last_log", 0.0)
                                            if str(sw_err) != str(last_err) or (now - float(last_log)) > 30.0:
                                                self._obs_profile_switch_last_err = str(sw_err)
                                                self._obs_profile_switch_last_log = now
                                                self._post(f"OBS: WARN — {msg} (auto-switch failed: {sw_err})")
                                            if "code 600" in str(sw_err):
                                                self._post("OBS: profile auto-switch disabled for this run after code 600; continuing in warn-only mode")
                                                self._obs_profile_checked_on_connect = True
                                            # otherwise leave _obs_profile_checked_on_connect False so we retry
                                else:
                                    # 'block' means we'll block later on start; warn now.
                                    self._post(f"OBS: WARN — {msg} (start will be blocked)")
                                    self._obs_profile_checked_on_connect = True

            if not self.midi.is_connected():
                self.midi.connect()

            for msg in self.midi.pending():
                try:
                    audit_id = None
                    msg_channel = (getattr(msg, "channel", -1) + 1) if hasattr(msg, "channel") else -1
                    if getattr(msg, "type", "") == "note_on" and (getattr(msg, "velocity", 0) or 0) > 0 and msg_channel in (self.cfg.MIDI_CHANNEL_1_BASED, getattr(self.cfg, "MIDI_NEXT_CHANNEL_1_BASED", 2)):
                        audit_id = self._next_midi_audit_id()
                        self._audit_midi_rx(msg, audit_id)
                    if self.midi.is_note_on_channel(msg, self.cfg.NOTE_START_STREAM, self.cfg.MIDI_CHANNEL_1_BASED):
                        self._audit_midi_dec(audit_id, f"start stream note={self.cfg.NOTE_START_STREAM} ch={self.cfg.MIDI_CHANNEL_1_BASED}")
                        self._start_stream_flow("MIDI")
                    elif self.midi.is_note_on_channel(msg, self.cfg.NOTE_STOP_STREAM, self.cfg.MIDI_CHANNEL_1_BASED):
                        self._audit_midi_dec(audit_id, f"stop stream note={self.cfg.NOTE_STOP_STREAM} ch={self.cfg.MIDI_CHANNEL_1_BASED}")
                        self._request_stop("MIDI")
                    elif self.midi.is_note_on_channel(msg, self.cfg.NOTE_REC_TOGGLE, self.cfg.MIDI_CHANNEL_1_BASED):
                        self._audit_midi_dec(audit_id, f"record toggle note={self.cfg.NOTE_REC_TOGGLE} ch={self.cfg.MIDI_CHANNEL_1_BASED}")
                        self._toggle_record("MIDI")
                    else:
                        pn_now = self.midi.is_note_in_range_channel(msg, self.cfg.NOTE_PRESET_FIRST, self.cfg.NOTE_PRESET_LAST, self.cfg.MIDI_CHANNEL_1_BASED)
                        if pn_now is not None:
                            preset = pn_now - self.cfg.NOTE_PRESET_FIRST + 1
                            self._audit_midi_dec(audit_id, f"current view note={pn_now} ch={self.cfg.MIDI_CHANNEL_1_BASED} -> view={preset} label={self._label_for_view(preset)}")
                            self._handle_preset(preset, "MIDI", audit_id=audit_id)
                        else:
                            pn_next = self.midi.is_note_in_range_channel(msg, self.cfg.NOTE_PRESET_FIRST, self.cfg.NOTE_PRESET_LAST, getattr(self.cfg, "MIDI_NEXT_CHANNEL_1_BASED", 2))
                            if pn_next is not None:
                                preset = pn_next - self.cfg.NOTE_PRESET_FIRST + 1
                                self._audit_midi_dec(audit_id, f"next view note={pn_next} ch={getattr(self.cfg, 'MIDI_NEXT_CHANNEL_1_BASED', 2)} -> view={preset} label={self._label_for_view(preset)}")
                                self._prepare_next_view_request(preset, "MIDI", audit_id=audit_id)
                except Exception as e:
                    self._post(f"MIDI error: {e}")

            if self._pending_stream_start and self.obs.connected:
                if time.time() >= getattr(self, "_pending_start_not_before", 0.0):
                    reason = self._pending_start_reason or "PENDING"
                    self._pending_stream_start = False
                    self._pending_start_reason = ""
                    self._pending_start_not_before = 0.0
                    # A pending retry is not a new operator double-click. Clear the debounce
                    # timestamp so a Start pressed while OBS was offline, or a quick OBS
                    # NotReady retry, cannot be swallowed by START_DEBOUNCE_SECONDS.
                    self._last_start_request_ts = 0.0
                    self._start_stream_flow(reason)

            self._axis_move_ready_tick()
            self._scene_switch_tick()
            self._stop_tick()
            self._timer_tick()
            self._refresh_director_previews()
            self.audio_master_meter_db = self._current_audio_meter_db()
            self._sync_offsets_poll_tick()

            streaming, recording, err = self.obs.get_status()

            elapsed = time.time() - self.start_time

            if self.midi.is_connected():
                midi_line = f"MIDI: connected ({self.midi.connected_name})"
            else:
                reason = self.midi.last_error or "no matching port"
                midi_line = f"MIDI: waiting ({reason})"

            if elapsed < startup_grace:
                obs_line = "OBS: connecting..."
                cam_src_line = ""
            else:
                scene_name, _scene_err = self.obs._get_current_program_scene_name()
                obs_line = f"OBS: {'STREAM ON' if streaming else 'stream off'} / {'REC ON' if recording else 'rec off'}"
                if scene_name:
                    obs_line += f" | scene: {scene_name}"
                if err:
                    obs_line = f"OBS: offline ({err})"
                cam_src_line = ""

            west_view = self.camera_positions.get("west")
            east_view = self.camera_positions.get("east")
            west_rem = self._camera_remaining_ready_seconds("west")
            east_rem = self._camera_remaining_ready_seconds("east")
            west_state = f"West={self._label_for_view(west_view)}" if west_view is not None else "West=?"
            east_state = f"East={self._label_for_view(east_view)}" if east_view is not None else "East=?"
            if west_rem > 0:
                west_state += f" ({west_rem:.1f}s)"
            if east_rem > 0:
                east_state += f" ({east_rem:.1f}s)"
            cam_prefix = "ROUTING: HOME TEST" if self.cfg.HOME_TEST_MODE else "ROUTING"
            cam_line = f"{cam_prefix}: {west_state} | {east_state}"

            # Coalesced UI update (applied on UI thread)
            if elapsed < startup_grace:
                banner_text, banner_style = "INITIALIZING — Launch OBS/Proclaim as needed", "Ready.Banner.TLabel"
            else:
                banner_text, banner_style = self._update_banner(streaming, recording, err if err else "")

            self._set_ui_state(
                obs_line=obs_line,
                midi_line=midi_line,
                cam_line=cam_line,
                rec_on=recording,
                banner_text=banner_text,
                banner_style=banner_style,
            )


            # --- Stream transition + auto-minimize + issue detection/recovery ---
            prev_streaming = self._was_streaming

            # Expire stale stop-intent (safety)
            if self._stop_intent and (time.time() - self._stop_intent_set_at) > 120:
                self._stop_intent = False


            # OBS error changes (sticky)
            if err and err != self._last_obs_err:
                self._last_obs_err = err
                self._note_critical(f"OBS error: {err}")
                self._post(f"ERROR: OBS status: {err}")
            elif (not err) and self._last_obs_err:
                self._post("OBS error cleared")
                self._last_obs_err = ""

            # Stream started
            if streaming and not prev_streaming:
                self.stream_stable_since = time.time()
                self.minimized_this_stream = False
                self.stream_ended_at = None
                self._desired_streaming = True

                had_recovery = (self._recover_attempts > 0)
                # Clear recovery state now that streaming is back
                self._recovering = False
                self._recover_attempts = 0
                self._recover_next_at = 0.0
                self._recover_reason = ""
                self._recover_hold_until = 0.0
                self._recovered_until = time.time() + 15.0 if had_recovery else 0.0

                self._open_session_log("stream_started")
                self._post("Stream started — enjoy the service!")

                # Optional "bring to front" behavior (disabled by default)
                if self.minimized and self.cfg.AUTO_BRING_TO_FRONT_ON_STREAM_START:
                    self._ui_action(lambda: (self.root.deiconify(), self.root.lift()))
                    self.minimized = False

            # Stream stopped (transition ON -> OFF)
            if (not streaming) and prev_streaming:
                self.stream_stable_since = None
                self.minimized_this_stream = False

                if self._stop_intent:
                    self._post("Stream stopped (requested)")
                    self._stop_intent = False
                    self._clear_unexpected_stop_candidate()
                    # stream_ended_at is set in _stop_tick for requested stops
                    self._close_session_log("requested_stop")
                else:
                    self._begin_unexpected_stop_candidate()

            if streaming:
                self._clear_unexpected_stop_candidate()

            if not streaming:
                self.stream_stable_since = None

            # Confirm an unexpected stop only after it survives the startup settle window
            # and the configured OFF debounce period.
            self._confirm_unexpected_stop_if_needed(streaming)

            # Auto-minimize (stay minimized; never auto-restore unless explicitly enabled)
            if (self.cfg.AUTO_MINIMIZE_ENABLED and streaming and self.stream_stable_since and
                not self.minimized_this_stream and not self.minimized and
                (time.time() - self.stream_stable_since) >= self.cfg.AUTO_MINIMIZE_AFTER_SECONDS):
                self._ui_action(lambda: self.root.iconify())
                self.minimized = True
                self.minimized_this_stream = True
                self._post("Stable — minimizing HUD")

            # Optional auto-restore on issues (disabled by default)
            if self.cfg.AUTO_RESTORE_ON_ISSUE:
                if self.minimized and ((prev_streaming and (not streaming) and (not self._stop_intent)) or err):
                    def _restore():
                        self.root.deiconify()
                        self.root.lift()
                        self.root.attributes('-topmost', True)
                        self.root.after(8000, lambda: self.root.attributes('-topmost', False))
                    self._ui_action(_restore)
                    self.minimized = False
                    self._post("Issue detected — restoring HUD")

            # Auto-recovery tick (only when desired live but not streaming)
            self._recovery_tick(streaming)

            # Web HUD health snapshot
            now = time.time()
            health_level = "READY"
            health_title = "READY"
            health_detail = ""

            if streaming:
                if now < self._recovered_until:
                    health_level = "RECOVERED"
                    health_title = "RECOVERED"
                    health_detail = "Stream recovered (auto-restart succeeded)."
                else:
                    health_level = "LIVE"
                    health_title = "LIVE"
                    health_detail = "Streaming is active."
            else:
                if self._desired_streaming and self._ever_requested_stream:
                    if now < (getattr(self, "_start_grace_until", 0.0) or 0.0):
                        health_level = "STARTING"
                        health_title = "STARTING"
                        health_detail = "Starting stream — waiting for OBS to go live."
                    elif now < self._recover_hold_until:
                        rem = int(self._recover_hold_until - now)
                        health_level = "ERROR"
                        health_title = "ERROR"
                        health_detail = f"Repair paused. Auto-restart paused for {fmt_hms(rem)} (press Start to retry)."
                    elif self._recovering:
                        max_attempts = int(getattr(self.cfg, "AUTO_RECOVER_MAX_ATTEMPTS", 3))
                        next_in = max(0, int(self._recover_next_at - now))
                        health_level = "RECOVERING"
                        health_title = "RECOVERING"
                        health_detail = f"Repairing OBS. Auto-restart attempt {min(self._recover_attempts+1, max_attempts)}/{max_attempts} in {fmt_hms(next_in)}."
                    elif not self.obs.connected:
                        health_level = "RECOVERING"
                        health_title = "RECOVERING"
                        health_detail = "Repairing OBS. OBS offline — reconnecting."
                    else:
                        health_level = "ERROR"
                        health_title = "ERROR"
                        health_detail = "Not live. Press Start, or check OBS."
                else:
                    health_level = "READY"
                    health_title = "READY"
                    health_detail = "Not streaming."

            # Top banner override when we WANT to be live but the stream isn't live yet
            if (not streaming) and self._desired_streaming and self._ever_requested_stream:
                grace_until = (getattr(self, "_start_grace_until", 0.0) or 0.0)
                if now < grace_until:
                    # OBS can take a moment to flip streaming=true after we send StartStream or switch profiles.
                    banner_text = "🟡 STARTING — preparing OBS"
                    banner_style = "Countdown.Banner.TLabel"
                else:
                    banner_text = "🔧 REPAIRING OBS — restarting stream" if self._recovering else "🔴 STREAM DOWN"
                    banner_style = "Countdown.Banner.TLabel" if self._recovering else "Error.Banner.TLabel"


            # Push health state to UI + Web HUD
            self._set_ui_state(
                health_level=health_level,
                health_title=health_title,
                health_detail=health_detail,
                health_last_ts=self._last_critical_ts,
                health_last_msg=self._last_critical_msg,
                banner_text=banner_text,
                banner_style=banner_style,
            )

            self._was_streaming = streaming

            await self._broadcast_web_state_if_dirty()
            await asyncio.sleep(0.25)

        self._stop_audio_events_if_possible()
        await self._stop_web_server()

    def _runner(self):
        try:
            self._post(f"BUILD: {BUILD_ID}")
            asyncio.run(self.loop())
        except Exception as e:
            msg = str(e)
            if "8765" in msg and ("10048" in msg or "address already in use" in msg.lower() or "only one usage" in msg.lower()):
                self._post("Loop crashed: Web HUD port 8765 is already in use. Close any other Stream Agent copy, then try again.")
            else:
                self._post(f"Loop crashed: {e}")

    def _on_close(self):
        # 1. Signal stop
        self.running = False

        # 2. Wait for worker thread to finish smoothly (avoids race condition)
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)

        # 3. Close logs
        try:
            self._close_session_log("app_close")
        except Exception:
            pass
        try:
            if self._run_log_fp:
                ts = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                self._run_log_fp.write(f"=== Stream Agent run ended {ts} ===\n")
                self._run_log_fp.flush()
                self._run_log_fp.close()
        except Exception:
            pass
        finally:
            self._run_log_fp = None

        # 4. Destroy UI
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App(CFG).run()
