"""
stream_agent_II_webhud_doubletap_v7_14.py

Stream Agent II — PC HUD + Web HUD (tablet/phone)

Version: v7.14 — Service-End Master Sequence (optional) + v7.13 fixes

Key behaviour:
- PC HUD is allowed to auto-minimize, but will NOT pop up during service by default.
- Web HUD is the primary monitoring surface (live status + sticky last error + scrolling log).

Changes in v7.13:
- FIX: Camera-check now uses `get_source_active` (OBS v5+) to reliably detect if the camera 
       is showing, even if nested inside Groups or other Scenes.
- FIX: Shutdown race condition resolved by joining the worker thread before destroying UI.
- NEW: Automatic log cleanup (keeps last 30 logs, deletes older ones on startup).

New in v7.14 (disabled by default):
- Service-End Master Sequence: Optional automated post-service cleanup triggered by MIDI stop note.
  - Waits for OBS (Open Broadcaster Software) stream/recording to fully stop + configurable cooldown.
  - Copies latest run + session logs (and optionally previous logs) to a dated folder on USB (Universal Serial Bus) drive.
  - Copies the most recent MP4 (current-day only) from the OBS recordings folder.
  - Optionally closes Proclaim, Master Fader, and OBS via psutil (if installed).
  - Optionally initiates Windows shutdown with a configurable abort window.
- UI-thread safety for any shutdown popup (Tkinter messagebox shown via UI thread).

"""

# -----------------------------

from __future__ import annotations

# -----------------------------
# App identity / version
# -----------------------------
APP_NAME = "Stream Agent II"
APP_VERSION = "v7.14 Standard fix11d + Config UI beta"
APP_DISPLAY = f"{APP_NAME} {APP_VERSION}"


import asyncio
import datetime as dt
import json
import os
import socket
import threading
import queue
import time
import glob
import shutil
import subprocess
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

try:
    import mido
except Exception:
    mido = None

try:
    from obsws_python import ReqClient
except Exception:
    ReqClient = None

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
    """Configuration for Stream Agent II."""

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

    # ----------------------------
    # LOGGING
    # ----------------------------
    LOG_TO_FILE_ENABLED: bool = True
    LOG_RUN_FILE_PREFIX: str = "stream_agent"
    LOG_SEPARATE_SESSION_FILES: bool = True
    LOG_DIR: str = ""
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
    OBS_EXPECTED_PROFILE_NAME: str = "NHLC live"
    # Actions: "block" (warn & stop), "warn" (warn & continue), "switch" (auto-switch then start)
    OBS_PROFILE_MISMATCH_ACTION: str = "switch"
    # How long to wait after switching profiles before attempting StartStream
    OBS_PROFILE_SWITCH_GRACE_SECONDS: float = 2.0

    OBS_CAMERA_INPUT_NAME: str = "Fomako ndi av"
    OBS_CAMERA_NDI_SENDER_NAME: str = "NDI_HX (NDI-E477D4AC5898)"
    OBS_CAMERA_SCENE_NAME: str = ""  # Ignored in v7.13 (using global source-active check)
    CAMERA_SOURCE_CHECK_SECONDS: int = 5
    CAMERA_SOURCE_WARN_AFTER_SECONDS: int = 25
    AUTO_RECONNECT_OBS: bool = True
    CAMERA_SOURCE_CHECK_ENABLED: bool = True
    CAMERA_SOURCE_CHECK_IN_HOME_TEST: bool = False

    # ----------------------------
    # STREAM SAFETY
    # ----------------------------
    STOP_DELAY_SECONDS: int = 30
    START_DEBOUNCE_SECONDS: float = 5.0

    # ----------------------------
    # WEB HUD
    # ----------------------------
    WEB_HUD_ENABLED: bool = True
    WEB_HUD_HOST: str = "0.0.0.0"
    WEB_HUD_PORT: int = 8765
    WEB_HUD_TOKEN: str = ""
    WEB_HUD_LOG_LINES: int = 30


    # ----------------------------
    # WEB HUD — YouTube "View Live" helper
    # ----------------------------
    YOUTUBE_LIVE_URL: str = "https://www.youtube.com/@NewHopeLutheranChurchRegina/live"
    # Used for the embedded viewer mode (/viewer). Found from your channel URL:
    # https://www.youtube.com/channel/UCNg9iyVIF5ks6hO1P-VIqKQ
    YOUTUBE_CHANNEL_ID: str = "UCNg9iyVIF5ks6hO1P-VIqKQ"

    # ----------------------------
    # CAMERA CONTROL
    # ----------------------------
    CAMERA_IP: str = "192.168.88.20"
    CAMERA_VISCA_PORT: int = 1259
    VISCA_USE_OVERIP_HEADER: bool = False
    VISCA_ADDR: int = 0x81
    CAMERA_BOOT_SECONDS: int = 20
    CAMERA_AUTO_WAKE_ON_PRESET: bool = True
    PRESET_NUMBER_BASE: int = 1

    # ----------------------------
    # PRESET DELAYS
    # ----------------------------
    ENABLE_PRESET_DELAYS: bool = False
    PRESET_DELAYS_SECONDS: Dict[int, int] = field(default_factory=lambda: {
        1: 10,  #pulpit
        2: 0,   #panorama
        3: 20,  #Children's Time
        4: 12,  #alter
        5: 20,  #choir
        6: 0,   #screen
        7: 0,   #band
        8: 0,   #piano
        9: 30,  #communion
        10: 0,  #track
    })

    # ----------------------------
    # MIDI INPUT
    # ----------------------------
    MIDI_INPUT_PORT_SUBSTRING: str = "proclaim"
    MIDI_CHANNEL_1_BASED: int = 1
    NOTE_START_STREAM: int = 60
    NOTE_STOP_STREAM: int = 61
    NOTE_REC_TOGGLE: int = 62
    NOTE_PRESET_FIRST: int = 70
    NOTE_PRESET_LAST: int = 79

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
        10: "(Unassigned)",
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
    # Camera identity / VISCA addressing (edit in VS Code)
    "CAMERA_IP", "CAMERA_VISCA_PORT", "VISCA_USE_OVERIP_HEADER", "VISCA_ADDR",
    # Credentials / tokens (edit locally)
    "OBS_PASSWORD", "WEB_HUD_TOKEN",
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
    "CAMERA_SOURCE_CHECK_SECONDS",
    "CAMERA_SOURCE_WARN_AFTER_SECONDS",
    "OBS_PROFILE_SWITCH_GRACE_SECONDS",
    "AUTO_RECOVER_ENABLED",
    "AUTO_RECOVER_MAX_ATTEMPTS",
    "AUTO_RECOVER_BASE_DELAY_SECONDS",
    "AUTO_RECOVER_BACKOFF_MULTIPLIER",
    "AUTO_RECOVER_COOLDOWN_SECONDS",
    "AUTO_RECOVER_START_GRACE_SECONDS",
    "WEB_HUD_LOG_LINES",
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
    "OBS_PROFILE_MISMATCH_ACTION": ["block", "warn", "switch"],
    "TZ_FALLBACK_MODE": ["local", "fixed_offset"],
}

# UI grouping (order) for general config page
CFG_UI_GENERAL_SECTIONS = [
    ("MODE / ENVIRONMENT", ["HOME_TEST_MODE"]),
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
    ("OBS CAMERA SOURCE CHECK", [
        "OBS_CAMERA_INPUT_NAME",
        "OBS_CAMERA_NDI_SENDER_NAME",
        "CAMERA_SOURCE_CHECK_ENABLED",
        "CAMERA_SOURCE_CHECK_IN_HOME_TEST",
        "CAMERA_SOURCE_CHECK_SECONDS",
        "CAMERA_SOURCE_WARN_AFTER_SECONDS",
        "AUTO_RECONNECT_OBS",
    ]),
    ("STREAM SAFETY", [
        "STOP_DELAY_SECONDS",
        "START_DEBOUNCE_SECONDS",
    ]),
    ("WEB HUD", [
        "WEB_HUD_ENABLED",
        "WEB_HUD_LOG_LINES",
    ]),
    ("WEB HUD — YouTube viewer", [
        "YOUTUBE_LIVE_URL",
        "YOUTUBE_CHANNEL_ID",
    ]),
    ("CAMERA CONTROL (non-identity)", [
        "CAMERA_BOOT_SECONDS",
        "CAMERA_AUTO_WAKE_ON_PRESET",
        "PRESET_NUMBER_BASE",
    ]),
    ("PRESET DELAYS", [
        "ENABLE_PRESET_DELAYS",
        "PRESET_DELAYS_SECONDS",
    ]),
    ("MIDI INPUT", [
        "MIDI_INPUT_PORT_SUBSTRING",
        "MIDI_CHANNEL_1_BASED",
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
        "CAMERA_IP",
        "CAMERA_VISCA_PORT",
        "VISCA_USE_OVERIP_HEADER",
        "VISCA_ADDR",
        "OBS_HOST",
        "OBS_PORT",
        "OBS_PASSWORD",
        "WEB_HUD_HOST",
        "WEB_HUD_PORT",
        "WEB_HUD_TOKEN",
    ]),
]

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
                setattr(cfg, k, bool(v))
            elif isinstance(cur, int) and not isinstance(cur, bool):
                setattr(cfg, k, int(v))
            elif isinstance(cur, float):
                setattr(cfg, k, float(v))
            elif isinstance(cur, str):
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
    hh, mm = hhmm.strip().split(":")
    return int(hh), int(mm)


def fmt_hms(seconds: int) -> str:
    if seconds < 0:
        seconds = 0
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class ViscaCamera:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addr = (cfg.CAMERA_IP, cfg.CAMERA_VISCA_PORT)

    def _wrap(self, payload: bytes) -> bytes:
        if not self.cfg.VISCA_USE_OVERIP_HEADER:
            return payload
        return payload

    def send(self, payload: bytes):
        packet = self._wrap(payload)
        self.sock.sendto(packet, self.addr)

    def power_on(self):
        self.send(bytes([self.cfg.VISCA_ADDR, 0x01, 0x04, 0x00, 0x02, 0xFF]))

    def power_off(self):
        self.send(bytes([self.cfg.VISCA_ADDR, 0x01, 0x04, 0x00, 0x03, 0xFF]))

    def recall_preset(self, preset_num_1_based: int):
        pp = (preset_num_1_based - 1) + self.cfg.PRESET_NUMBER_BASE
        pp = max(0, min(pp, 127))
        self.send(bytes([self.cfg.VISCA_ADDR, 0x01, 0x04, 0x3F, 0x02, pp, 0xFF]))


class ObsController:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client: Optional[ReqClient] = None
        self.last_error: str = ""
        self.connected: bool = False

    def connect(self) -> bool:
        if ReqClient is None:
            self.last_error = "obsws-python not installed"
            return False
        try:
            self.client = ReqClient(host=self.cfg.OBS_HOST, port=self.cfg.OBS_PORT,
                                    password=self.cfg.OBS_PASSWORD or None, timeout=5)
            self.client.get_version()
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
            self.connected = False
            self.last_error = str(e)
            return False, False, self.last_error

    def start_stream(self) -> Tuple[bool, str]:
        if not self._ok():
            return False, "OBS not connected"
        try:
            self.client.start_stream()
            return True, "start stream sent"
        except Exception as e:
            self.connected = False
            self.last_error = str(e)
            return False, self.last_error

    def stop_stream(self) -> Tuple[bool, str]:
        if not self._ok():
            return False, "OBS not connected"
        try:
            self.client.stop_stream()
            return True, "stop stream sent"
        except Exception as e:
            self.connected = False
            self.last_error = str(e)
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
        """Switch OBS to the specified profile. Returns (ok, err)."""
        name = (name or "").strip()
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
    def camera_source_status(self, cfg) -> dict:
        """Checks if the camera source is actively showing on the output.
        
        IMPROVED in v7.13: Uses `get_source_active` which reliably detects if a source is
        showing, even if it is nested inside Groups or other Scenes.
        """
        if not self.connected or not self.client:
            return {"ok": None, "visible": None, "input": None, "detail": "OBS offline"}

        # Step 1: Identify the source name
        # We need the exact source name to query active status.
        resp, err = self._safe_call("get_input_list")
        if err:
            return {"ok": None, "visible": None, "input": None, "detail": f"get_input_list failed: {err}"}

        inputs = self._get(resp, "inputs", []) or []
        names = [self._get(it, "inputName") or self._get(it, "sourceName") or self._get(it, "name") for it in inputs]

        cam_input = None
        if getattr(cfg, "OBS_CAMERA_INPUT_NAME", "") and cfg.OBS_CAMERA_INPUT_NAME in names:
            cam_input = cfg.OBS_CAMERA_INPUT_NAME
        elif getattr(cfg, "OBS_CAMERA_NDI_SENDER_NAME", ""):
            target = cfg.OBS_CAMERA_NDI_SENDER_NAME.lower()
            for nm in names:
                # We have to check settings to find the NDI sender
                r2, e2 = self._safe_call("get_input_settings", inputName=nm)
                if e2 or r2 is None:
                    continue
                settings = self._get(r2, "inputSettings", {}) or {}
                if self._contains_text(settings, target):
                    cam_input = nm
                    break

        if cam_input is None:
            return {"ok": False, "visible": None, "input": None, "detail": "Camera input not found"}

        # Step 2: Prefer a *scene-item* visibility check in the current PROGRAM scene.
        # This matches what you actually see on-stream and avoids occasional false negatives
        # from get_source_active with some NDI sources.
        visible = None
        detail_extra = ""

        # 2a) Get current program scene name
        r_scene, e_scene = self._safe_call("get_current_program_scene")
        prog_scene = None
        if not e_scene and r_scene is not None:
            prog_scene = (self._get(r_scene, "currentProgramSceneName")
                          or self._get(r_scene, "current_program_scene_name")
                          or self._get(r_scene, "sceneName")
                          or self._get(r_scene, "scene_name"))

        # 2b) Look for scene item whose sourceName matches cam_input
        if prog_scene:
            r_items, e_items = self._safe_call("get_scene_item_list", sceneName=prog_scene)
            # obsws-python versions differ on kwarg naming; try snake_case if needed
            if e_items and "unexpected keyword argument" in str(e_items):
                r_items, e_items = self._safe_call("get_scene_item_list", scene_name=prog_scene)
            if e_items and "unexpected keyword argument" in str(e_items):
                r_items, e_items = self._safe_call("get_scene_item_list", scene=prog_scene)

            if not e_items and r_items is not None:
                items = (self._get(r_items, "sceneItems")
                         or self._get(r_items, "scene_items")
                         or [])
                for it in items or []:
                    src = (self._get(it, "sourceName")
                           or self._get(it, "source_name")
                           or self._get(it, "inputName")
                           or self._get(it, "input_name"))
                    if src == cam_input:
                        enabled = (self._get(it, "sceneItemEnabled")
                                   if self._get(it, "sceneItemEnabled") is not None
                                   else self._get(it, "scene_item_enabled"))
                        # Some wrappers may use "enabled"
                        if enabled is None:
                            enabled = self._get(it, "enabled")
                        visible = bool(enabled) if enabled is not None else True
                        detail_extra = f" (programScene='{prog_scene}', itemEnabled={visible})"
                        break

        # 2c) Fallback: get_source_active (older logic)
        if detail_extra == "":
            r_active, e_active = self._safe_call("get_source_active", sourceName=cam_input)
            if e_active and "unexpected keyword argument" in str(e_active):
                r_active, e_active = self._safe_call("get_source_active", source_name=cam_input)
            if e_active and "unexpected keyword argument" in str(e_active):
                r_active, e_active = self._safe_call("get_source_active", source=cam_input)

            if not e_active and r_active is not None:
                if self._get(r_active, "videoShowing") is None and self._get(r_active, "video_showing") is None:
                    visible = None
                    video_active = bool(self._get(r_active, "videoActive", False) or self._get(r_active, "video_active", False))
                    detail_extra = f" (showing=UNKNOWN, active={video_active})"
                else:
                    visible = bool(self._get(r_active, "videoShowing", False) or self._get(r_active, "video_showing", False))
                    video_active = bool(self._get(r_active, "videoActive", False) or self._get(r_active, "video_active", False))
                    detail_extra = f" (showing={visible}, active={video_active})"
            else:
                visible = None
                detail_extra = " (status unknown)"

        detail = f"Found '{cam_input}'" + detail_extra

        # If we found the input, but visible is False, it's "FOUND (hidden)"
        return {"ok": True, "visible": visible, "input": cam_input, "detail": detail}


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

    def is_note_on(self, msg, note: int) -> bool:
        """Return True only for a real NOTE_ON (velocity > 0) on our configured MIDI channel."""
        if getattr(msg, "channel", -1) + 1 != self.cfg.MIDI_CHANNEL_1_BASED:
            return False
        if getattr(msg, "note", None) != note:
            return False
        if getattr(msg, "type", "") != "note_on":
            return False
        # In MIDI, NOTE_ON with velocity 0 is often used as NOTE_OFF; ignore it.
        vel = getattr(msg, "velocity", 0) or 0
        return vel > 0

    def is_note_in_range(self, msg, lo: int, hi: int) -> Optional[int]:
        """Return the note number for a real NOTE_ON (velocity > 0) within [lo, hi] on our channel."""
        if getattr(msg, "channel", -1) + 1 != self.cfg.MIDI_CHANNEL_1_BASED:
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


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.start_time = time.time()
        self.root = tk.Tk()
        self.root.title("Stream Agent")
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
        self._web_dirty = False
        self._state_version = 0
        self._ws_clients = set()
        self._web_runner = None
        self._web_site = None
        self._async_loop = None

        # Commands from UI/web are funneled to the worker loop thread
        self._cmd_queue = None  # created inside async loop

        self.running = True
        self.obs = ObsController(cfg)
        self.midi = MidiListener(cfg)
        self.cam = ViscaCamera(cfg)

        self.cam_state = "SLEEP"
        self.cam_ready_at: float = 0.0
        self._cam_src_last_check: float = 0.0
        self._cam_src_last_result: Optional[dict] = None
        self._cam_src_warned: bool = False
        self._cam_awake_since: Optional[float] = None
        self._queued_preset: Optional[int] = None
        # Delayed preset scheduling (MIDI/automation only; HUD is immediate)
        self._pending_preset: Optional[int] = None
        self._pending_preset_due: float = 0.0
        self._pending_preset_delay_s: int = 0
        self._pending_preset_source: str = ""
        self._pending_stream_start: bool = False
        self._pending_start_reason: str = ""
        self._pending_start_not_before: float = 0.0

        self._stop_pending: bool = False
        self._stop_at: float = 0.0
        self._last_stop_was_midi: bool = False
        self._service_end_running: bool = False

        self._timer_done_today_date: Optional[dt.date] = None
        self._timer_done_status: Optional[str] = None
        self._timer_done_time_hhmm: Optional[str] = None
        self._last_start_request_ts: float = 0.0
        self._start_grace_until: float = 0.0  # suppress auto-recover retries right after a start request
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
        self._cam_issue_prev: bool = False

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


    def _camera_source_status_line(self) -> str:
        # In HOME_TEST_MODE (no camera), the OBS camera-source monitor is disabled by default
        # to avoid noisy "camera missing" warnings. Enable it by setting:
        #   CAMERA_SOURCE_CHECK_IN_HOME_TEST = True
        if (not getattr(self.cfg, "CAMERA_SOURCE_CHECK_ENABLED", True) or
            (self.cfg.HOME_TEST_MODE and not getattr(self.cfg, "CAMERA_SOURCE_CHECK_IN_HOME_TEST", False))):
            self._cam_src_last_result = {"ok": None, "visible": None, "input": None, "detail": "Camera source check disabled"}
            return ""

        now = time.time()
        if (now - self._cam_src_last_check) < self.cfg.CAMERA_SOURCE_CHECK_SECONDS and self._cam_src_last_result:
            return self._format_cam_src_line(self._cam_src_last_result)
        self._cam_src_last_check = now
        if not self.obs.connected:
            return "SRC: (OBS?)"
        try:
            res = self.obs.camera_source_status(self.cfg)
        except Exception:
            res = {"detail": "check error"}
        self._cam_src_last_result = res

        if (self.cam_state == "AWAKE" and self._cam_awake_since and not self._cam_src_warned and
            (now - self._cam_awake_since) >= self.cfg.CAMERA_SOURCE_WARN_AFTER_SECONDS):
            if not res.get("ok") or res.get("visible") is False:
                self._cam_src_warned = True
                detail = ""
                try:
                    if res.get("detail"):
                        detail = f" | {res.get('detail')}"
                except Exception:
                    pass
                self._post("WARN: camera feed not in OBS" + detail)

        return self._format_cam_src_line(res)

    def _format_cam_src_line(self, res: dict) -> str:
        ok = res.get("ok")
        visible = res.get("visible")
        if ok is None:
            return "SRC: (OBS?)"
        if ok is False:
            return "SRC: MISSING"
        if visible is True:
            return "SRC: OK"
        if visible is False:
            return "SRC: FOUND (hidden)"
        return "SRC: FOUND"

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
                elif ctype == "preset":
                    self._handle_preset(int(cmd.get("preset", 0)), source)
            except Exception as e:
                self._post(f"CMD error: {e}")

    def _camera_wake(self, source: str):
        if self.cam_state in ("WAKING", "AWAKE"):
            return
        self.cam_state = "WAKING"
        self.cam_ready_at = time.time() + self.cfg.CAMERA_BOOT_SECONDS
        if self.cfg.HOME_TEST_MODE:
            self._post(f"{source}: camera wake simulated")
        else:
            try:
                self.cam.power_on()
                self._post(f"{source}: camera power ON sent")
            except Exception as e:
                self._post(f"{source}: camera power error: {e}")

    def _camera_sleep(self, source: str):
        self.cam_state = "SLEEP"
        self._cam_awake_since = None
        self._cam_src_warned = False
        self.cam_ready_at = 0.0
        self._queued_preset = None
        self._cancel_pending_preset(source)
        if self.cfg.HOME_TEST_MODE:
            self._post(f"{source}: camera sleep simulated")
        else:
            try:
                self.cam.power_off()
                self._post(f"{source}: camera power OFF sent")
            except Exception as e:
                self._post(f"{source}: camera power error: {e}")

    def _camera_ready_tick(self):
        if self.cam_state == "WAKING" and time.time() >= self.cam_ready_at:
            self.cam_state = "AWAKE"
            self._cam_awake_since = time.time()
            self._cam_src_warned = False
            self._post("CAM: awake/ready")
            if self._queued_preset is not None:
                p = self._queued_preset
                self._queued_preset = None
                self._send_preset(p, "QUEUE")
            if self._pending_stream_start:
                # If a preflight action (e.g., OBS profile switch) scheduled a delayed start, honor it.
                if time.time() < getattr(self, "_pending_start_not_before", 0.0):
                    return
                reason = self._pending_start_reason or "PENDING"
                self._pending_stream_start = False
                self._pending_start_reason = ""
                self._start_stream_flow(reason)

    def _send_preset(self, preset_num: int, source: str):
        label = self.cfg.PRESET_LABELS.get(preset_num, f"Preset {preset_num}")
        if self.cfg.HOME_TEST_MODE:
            self._post(f"{source}: preset {preset_num} ({label}) simulated")
            return
        try:
            self.cam.recall_preset(preset_num)
            self._post(f"{source}: preset {preset_num} ({label}) sent")
        except Exception as e:
            self._post(f"{source}: preset error: {e}")

    def _clamped_preset_delay(self, preset_num: int) -> int:
        """Return per-preset delay for MIDI/automation, clamped to 0..30 seconds."""
        try:
            raw = int((self.cfg.PRESET_DELAYS_SECONDS or {}).get(preset_num, 0))
        except Exception:
            raw = 0
        return max(0, min(raw, 30))

    def _cancel_pending_preset(self, reason: str = ""):
        if self._pending_preset is None:
            return
        p = self._pending_preset
        d = self._pending_preset_delay_s
        self._pending_preset = None
        self._pending_preset_due = 0.0
        self._pending_preset_delay_s = 0
        self._pending_preset_source = ""
        if reason:
            self._post(f"{reason}: cancelled pending preset {p} (delay {d}s)")

    def _schedule_preset(self, preset_num: int, source: str, delay_s: int):
        # Replace any previously scheduled preset
        if self._pending_preset is not None and self._pending_preset != preset_num:
            self._cancel_pending_preset(f"{source}")
        self._pending_preset = preset_num
        self._pending_preset_delay_s = delay_s
        self._pending_preset_due = time.time() + delay_s
        self._pending_preset_source = source
        label = self.cfg.PRESET_LABELS.get(preset_num, f"Preset {preset_num}")
        self._post(f"{source}: preset {preset_num} ({label}) scheduled in {delay_s}s")

        # If camera is asleep and auto-wake is enabled, wake now so we're ready when delay elapses.
        if self.cam_state == "SLEEP" and self.cfg.CAMERA_AUTO_WAKE_ON_PRESET:
            self._camera_wake(f"{source}: wake for delayed preset")

    def _preset_delay_tick(self):
        """Fire a delayed preset when due and the camera is ready."""
        if self._pending_preset is None:
            return
        if time.time() < self._pending_preset_due:
            return
        # Only execute when camera is awake (or in home test mode where presets are simulated anyway).
        if not self.cfg.HOME_TEST_MODE and self.cam_state != "AWAKE":
            return
        p = self._pending_preset
        src = self._pending_preset_source or "DELAY"
        delay_s = self._pending_preset_delay_s
        # Clear first to avoid re-entrancy surprises
        self._pending_preset = None
        self._pending_preset_due = 0.0
        self._pending_preset_delay_s = 0
        self._pending_preset_source = ""
        self._send_preset(p, f"{src}: delayed({delay_s}s)")

    def _handle_preset(self, preset_num: int, source: str):
        if not (1 <= preset_num <= 10):
            return

        # HUD presets are ALWAYS immediate (operator judgment). Also cancel any pending delayed preset.
        if source == "HUD":
            self._cancel_pending_preset("HUD")
            if self.cam_state == "SLEEP" and self.cfg.CAMERA_AUTO_WAKE_ON_PRESET:
                self._queued_preset = preset_num
                self._camera_wake(f"{source}: wake for preset")
                return
            if self.cam_state == "WAKING":
                self._queued_preset = preset_num
                self._post(f"{source}: queued preset {preset_num}")
                return
            self._send_preset(preset_num, source)
            return

        # MIDI/automation presets: optional per-preset delay (feature gated)
        if self.cfg.ENABLE_PRESET_DELAYS:
            delay_s = self._clamped_preset_delay(preset_num)
            if delay_s > 0:
                self._schedule_preset(preset_num, source, delay_s)
                return

        # Default behavior (no delay)
        if self.cam_state == "SLEEP" and self.cfg.CAMERA_AUTO_WAKE_ON_PRESET:
            self._queued_preset = preset_num
            self._camera_wake(f"{source}: wake for preset")
            return
        if self.cam_state == "WAKING":
            self._queued_preset = preset_num
            self._post(f"{source}: queued preset {preset_num}")
            return
        self._send_preset(preset_num, source)

    def _start_stream_flow(self, source: str):
        now = time.time()
        if (now - self._last_start_request_ts) < self.cfg.START_DEBOUNCE_SECONDS:
            self._post(f"{source}: start ignored (debounce)")
            return
        self._last_start_request_ts = now


        # Mark that the operator/automation has requested streaming at least once this run.
        self._ever_requested_stream = True
        # Operator/automation intent: we WANT to be live (used by auto-recovery).
        self._desired_streaming = True
        # After sending a start request, OBS can take a moment to report streaming=true.
        # Suppress auto-recover retries during this grace window to avoid double-starting.
        grace_s = float(getattr(self.cfg, "AUTO_RECOVER_START_GRACE_SECONDS", 15))
        self._start_grace_until = time.time() + max(0.0, grace_s)
        # Manual start clears any prior stop intent and any recovery pause.
        self._stop_intent = False
        self._stop_intent_set_at = 0.0
        self._recover_hold_until = 0.0
        self._recovering = False
        self._recover_attempts = 0
        self._recover_next_at = 0.0
        self._recover_reason = ""

        if not self.cfg.HOME_TEST_MODE:
            if self.cam_state == "SLEEP":
                self._pending_stream_start = True
                self._pending_start_reason = source
                self._camera_wake(f"{source}: wake for start")
                return
            if self.cam_state == "WAKING":
                self._pending_stream_start = True
                self._pending_start_reason = source
                return

        if not self.obs.connected:
            self._pending_stream_start = True
            self._pending_start_reason = source
            return

        # -----------------------------
        # Preflight: OBS profile safety (optional)
        # -----------------------------
        if getattr(self.cfg, "OBS_PROFILE_CHECK_ENABLED", False):
            expected = (getattr(self.cfg, "OBS_EXPECTED_PROFILE_NAME", "") or "").strip()
            if expected:
                current, perr = self.obs.get_current_profile_name()
                if perr:
                    self._post(f"{source}: WARN — could not read OBS profile ({perr})")
                elif current != expected:
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
            self._post(f"{source}: stream start sent")
        else:
            self._pending_stream_start = True
            self._pending_start_reason = source
            self._post(f"{source}: start failed ({msg})")

    def _request_stop(self, source: str):
        # Operator/automation intent: we do NOT want to be live.
        self._desired_streaming = False
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
        self.stream_ended_at = time.time()  # Trigger "STREAM ENDED" banner
        if not self.cfg.HOME_TEST_MODE:
            self._camera_sleep("STOP")

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
        hh, mm = parse_hhmm(self.cfg.TIMER_START_HHMM)
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
                "health": {
                    "level": state.get("health_level", "READY"),
                    "title": state.get("health_title", "READY"),
                    "detail": state.get("health_detail", ""),
                    "last_ts": state.get("health_last_ts", ""),
                    "last_msg": state.get("health_last_msg", ""),
                },
                "rec_on": bool(state.get("rec_on", False)),
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
  .h-recovering { background: rgba(240, 180, 20, .12); border-color: rgba(240, 180, 20, .35); }
  .h-degraded { background: rgba(240, 180, 20, .12); border-color: rgba(240, 180, 20, .35); }
  .h-error { background: rgba(230, 60, 60, .13); border-color: rgba(230, 60, 60, .35); }
  .h-recovered { background: rgba(100, 160, 255, .12); border-color: rgba(100, 160, 255, .35); }

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

  <div class="card">
    <div class="sectionTitle">Monitor</div>
    <div class="row">
      <a class="btn bView" id="btnViewYT" href="__YTLIVE__" target="_blank" rel="noopener">View Live (YouTube)</a>
      <a class="btn bView2" id="btnViewEmbed" href="__VIEWER__">View Live (Embedded)</a>
    </div>
    <div class="row" style="margin-top:10px;">
      <a class="btn bView2" id="btnCfg" href="__CONFIG__">Config</a>
      <a class="btn bView2" id="btnCfgTimer" href="__CONFIG_TIMER__">Timer</a>
    </div>
    <div class="hint" style="margin-top:8px; opacity:.8;">Tip: If embedded playback is picky, use the YouTube button.</div>
  </div>

  <div class="card">
    <div class="sectionTitle">Camera Presets</div>
    <div class="grid" id="presetGrid"></div>
  </div>

  <div class="card">
    <div class="sectionTitle">Log (last 30)</div>
    <div id="healthBox" class="healthBox h-ready">
      <div id="healthTitle" class="healthTitle">READY</div>
      <div id="healthDetail" class="healthDetail"></div>
      <div id="healthLast" class="healthLast"></div>
    </div>
    <pre id="logBox"></pre>
  </div>

  <div class="hint"><noscript>This page needs JavaScript enabled.</noscript></div>
</div>

<script src="/app.js?v=14"></script>
</body>
</html>
""".replace("__APP_VER__", APP_DISPLAY).replace("__YTLIVE__", getattr(self.cfg, "YOUTUBE_LIVE_URL", "https://www.youtube.com/@NewHopeLutheranChurchRegina/live")).replace("__VIEWER__", "/viewer" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else "")).replace("__CONFIG__", "/config" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else "")).replace("__CONFIG_TIMER__", "/config_timer" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else ""))
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
      <a class="btn bBack" id="btnTimer" href="__TIMER__">Timer</a>
    </div>
    <div class="note">Rules:
- While LIVE, editing is locked unless you temporarily Unlock.
- Critical constants (camera/VISCA identity, tokens, host/port) are read-only here.</div>
  </div>

  <div id="cfgRoot"></div>

  <div class="footer">Stream Agent Config Editor</div>
</div>

<script src="/config.js?v=1"></script>
</body>
</html>
""".replace("__APP_VER__", APP_DISPLAY)\
   .replace("__BACK__", "/" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else ""))\
   .replace("__TIMER__", "/config_timer" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else ""))\
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

<script src="/config.js?v=1"></script>
</body>
</html>
""".replace("__APP_VER__", APP_DISPLAY)\
   .replace("__BACK__", "/" + (("?token=" + self.cfg.WEB_HUD_TOKEN) if self.cfg.WEB_HUD_TOKEN else ""))

    def _web_config_js(self) -> str:
        # Shared JS for /config and /config_timer
        return r"""(function(){
  'use strict';
  function $(id){ return document.getElementById(id); }

  // Token handling (same scheme as the main HUD)
  function getToken(){
    try {
      var u = new URL(window.location.href);
      var t = u.searchParams.get('token') || '';
      return t;
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

  var state = {
    snapshot: null,
    edited: {}, // key -> value (or dict for PRESET_DELAYS_SECONDS)
    dirty: false,
    lastTap: {}
  };

  function setStatus(t){ if (statusSub) statusSub.textContent = t; }

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

  function roundToStep(x, step){
    if (!step) return x;
    var inv = 1/step;
    return Math.round(x*inv)/inv;
  }

  function makeItemEl(item, locked){
    var div = document.createElement('div');
    div.className = 'item' + (item.changed ? ' changed' : '') + (item.readonly ? ' readonly' : '');
    var line = document.createElement('div');
    line.className = 'line';
    line.textContent = item.display;
    div.appendChild(line);

    if (item.note) {
      var n = document.createElement('div');
      n.className = 'note';
      n.textContent = item.note;
      div.appendChild(n);
    }

    if (item.readonly) return div;

    var ctrl = document.createElement('div');
    ctrl.className = 'ctrl';

    var disabled = !!locked;

    // bool toggle
    if (item.kind === 'bool'){
      var label = document.createElement('label');
      label.className = 'switch';
      var inp = document.createElement('input');
      inp.type = 'checkbox';
      inp.checked = !!item.value;
      inp.disabled = disabled;
      var slider = document.createElement('span');
      slider.className = 'slider';
      label.appendChild(inp);
      label.appendChild(slider);
      ctrl.appendChild(label);

      var txt = document.createElement('span');
      txt.className = 'pill';
      txt.textContent = fmtBool(inp.checked);
      ctrl.appendChild(txt);

      inp.addEventListener('change', function(){
        txt.textContent = fmtBool(inp.checked);
        try { line.textContent = item.key + ': bool = ' + fmtBool(inp.checked); } catch(e) {}
        state.edited[item.key] = !!inp.checked;
        state.dirty = true;
      });
    }
    // enum dropdown
    else if (item.kind === 'enum'){
      var sel = document.createElement('select');
      sel.disabled = disabled;
      (item.options || []).forEach(function(opt){
        var o = document.createElement('option');
        o.value = opt;
        o.textContent = opt;
        if (opt === item.value) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener('change', function(){
        try { line.textContent = item.key + ': str = ' + JSON.stringify(sel.value); } catch(e) {}
        state.edited[item.key] = sel.value;
        state.dirty = true;
      });
      ctrl.appendChild(sel);
    }
    // preset delays dict
    else if (item.kind === 'preset_delays'){
      var cur = item.value || {};
      var def = item.default || {};
      var holder = document.createElement('div');
      holder.style.width = '100%';

      var keys = [];
      for (var k in cur) if (cur.hasOwnProperty(k)) keys.push(k);
      keys.sort(function(a,b){ return parseInt(a,10) - parseInt(b,10); });

      keys.forEach(function(k){
        var row = document.createElement('div');
        row.className = 'row';
        row.style.justifyContent = 'space-between';
        row.style.marginTop = '8px';

        var label = document.createElement('div');
        label.className = 'pill';
        label.textContent = 'Preset ' + k;
        row.appendChild(label);

        var stepper = document.createElement('div');
        stepper.className = 'stepper';

        var down = document.createElement('button');
        down.className = 'stepBtn';
        down.textContent = '▼';
        down.disabled = disabled;

        var val = document.createElement('div');
        val.className = 'stepVal';
        val.textContent = '' + (cur[k] != null ? cur[k] : 0);

        var up = document.createElement('button');
        up.className = 'stepBtn';
        up.textContent = '▲';
        up.disabled = disabled;

        function setK(newV){
          newV = Math.max(0, Math.min(600, parseInt(newV,10) || 0));
          cur[k] = newV;
          val.textContent = '' + newV;
          state.edited[item.key] = cur;
          state.dirty = true;
        }

        down.addEventListener('click', function(){ setK((cur[k]||0) - 1); });
        up.addEventListener('click', function(){ setK((cur[k]||0) + 1); });

        stepper.appendChild(down);
        stepper.appendChild(val);
        stepper.appendChild(up);

        // reset button per preset
        var reset = document.createElement('button');
        reset.className = 'tinyBtn';
        reset.textContent = '↺';
        reset.disabled = disabled;
        reset.addEventListener('click', function(){
          setK(def[k] != null ? def[k] : 0);
        });

        row.appendChild(stepper);
        row.appendChild(reset);

        holder.appendChild(row);
      });

      ctrl.appendChild(holder);
    }
    // numeric stepper
    else if (item.kind === 'int' || item.kind === 'float'){
      var step = (item.step != null) ? item.step : (item.kind === 'float' ? 0.5 : 1);
      var minv = (item.min != null) ? item.min : -1e9;
      var maxv = (item.max != null) ? item.max :  1e9;

      var stepper = document.createElement('div');
      stepper.className = 'stepper';

      var down = document.createElement('button');
      down.className = 'stepBtn';
      down.textContent = '▼';
      down.disabled = disabled;

      var val = document.createElement('div');
      val.className = 'stepVal';
      val.textContent = '' + item.value;

      var up = document.createElement('button');
      up.className = 'stepBtn';
      up.textContent = '▲';
      up.disabled = disabled;

      function setV(newV){
        if (item.kind === 'int') newV = parseInt(newV,10);
        else newV = parseFloat(newV);
        if (isNaN(newV)) newV = item.value;
        newV = Math.max(minv, Math.min(maxv, newV));
        if (item.kind === 'float') newV = roundToStep(newV, step);
        val.textContent = '' + newV;
        try {
          var tname = (item.kind === 'int') ? 'int' : 'float';
          var shown = (item.kind === 'float' && Math.abs(newV - Math.round(newV)) < 1e-9) ? (newV.toFixed(1)) : ('' + newV);
          line.textContent = item.key + ': ' + tname + ' = ' + shown;
        } catch(e) {}
        state.edited[item.key] = newV;
        state.dirty = true;
      }
      down.addEventListener('click', function(){ setV((state.edited[item.key]!=null?state.edited[item.key]:item.value) - step); });
      up.addEventListener('click', function(){ setV((state.edited[item.key]!=null?state.edited[item.key]:item.value) + step); });

      stepper.appendChild(down);
      stepper.appendChild(val);
      stepper.appendChild(up);
      ctrl.appendChild(stepper);
    }
    // text input
    else if (item.kind === 'str'){
      var inp = document.createElement('input');
      inp.type = 'text';
      inp.value = item.value || '';
      inp.disabled = disabled;
      inp.addEventListener('input', function(){
        try { line.textContent = item.key + ': str = ' + JSON.stringify(inp.value); } catch(e) {}
        state.edited[item.key] = inp.value;
        state.dirty = true;
      });
      ctrl.appendChild(inp);
    }

    // per-setting reset
    var resetBtn = document.createElement('button');
    resetBtn.className = 'tinyBtn';
    resetBtn.textContent = '↺';
    resetBtn.disabled = disabled;
    resetBtn.title = 'Reset to default';
    resetBtn.addEventListener('click', function(){
      state.edited[item.key] = item.default;
      state.dirty = true;
      // simplest: reload after apply; we don't live-update line text here
      setStatus('Reset staged: ' + item.key);
    });
    ctrl.appendChild(resetBtn);

    div.appendChild(ctrl);
    return div;
  }

  function render(snapshot){
    state.snapshot = snapshot;
    state.edited = {};
    state.dirty = false;

    if (!root) return;
    root.innerHTML = '';

    var meta = snapshot && snapshot.meta ? snapshot.meta : {};
    var locked = !!meta.locked;

    var status = [];
    status.push(meta.streaming ? 'LIVE: yes' : 'LIVE: no');
    status.push(locked ? 'EDIT: locked' : 'EDIT: unlocked');
    if (meta.unlock_remaining_s && meta.unlock_remaining_s > 0) {
      status.push('unlock remaining: ' + Math.ceil(meta.unlock_remaining_s) + 's');
    }
    setStatus(status.join('   |   '));

    // enable/disable buttons
    if (btnUnlock) btnUnlock.style.display = (meta.streaming ? 'inline-block' : 'none');
    if (btnUnlock) btnUnlock.disabled = (!meta.streaming);
    if (btnApply) btnApply.disabled = locked;
    if (btnRestore) btnRestore.disabled = (locked || !!meta.streaming);
    if (btnRestoreTimer) btnRestoreTimer.disabled = (locked || !!meta.streaming);

    // sections
    (snapshot.sections || []).forEach(function(sec){
      var card = document.createElement('div');
      card.className = 'card';

      var st = document.createElement('div');
      st.className = 'secTitle';
      st.textContent = sec.title || '';
      card.appendChild(st);

      (sec.items || []).forEach(function(item){
        // On timer page: only show timer scope
        card.appendChild(makeItemEl(item, locked || !!item.locked));
      });

      root.appendChild(card);
    });
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

  function doApply(){
    if (!state.dirty) { setStatus('No pending changes.'); return; }
    tapConfirm('apply', btnApply, 'Apply', 'Tap again to Apply', function(){
      var payload = { changes: state.edited };
      api('/api/config/apply', { method:'POST', body: JSON.stringify(payload) })
        .then(function(r){
          setStatus(r && r.message ? r.message : 'Applied.');
          load();
        })
        .catch(function(e){ setStatus('Apply failed: ' + e.message); });
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
    tapConfirm('restoreTimer', btnRestoreTimer, 'Restore TIMER Defaults', 'Tap again to Restore', function(){
      api('/api/config/restore_timer', { method:'POST', body: '{}' })
        .then(function(r){ setStatus(r && r.message ? r.message : 'Timer restored.'); load(); })
        .catch(function(e){ setStatus('Restore failed: ' + e.message); });
    });
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

  // Export link should include token
  try {
    if (btnExport && TOKEN) btnExport.href = withToken(btnExport.href);
  } catch(e) {}

  load();
})();"""

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
        # Build a UI item dict for a given config key
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

        if isinstance(cur, bool):
            setattr(self.cfg, key, bool(value))
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
  var presetGrid = $('presetGrid');
  var logBox = $('logBox');
  var healthBox = $('healthBox');
  var healthTitle = $('healthTitle');
  var healthDetail = $('healthDetail');
  var healthLast = $('healthLast');


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

  function setEnabled(enabled){
    if (btnStart) btnStart.disabled = !enabled;
    if (btnStop)  btnStop.disabled  = !enabled;
    if (btnRec)   btnRec.disabled   = !enabled;
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

  setEnabled(false);
  buildPresets(null);
  connect();
})();"""

    async def _start_web_server(self):
        if not self.cfg.WEB_HUD_ENABLED:
            return

        try:
            from aiohttp import web, WSMsgType
        except Exception:
            self._post("WEB: aiohttp not installed (pip install aiohttp) — web HUD disabled")
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
                            cmd = data.get("cmd")
                            if cmd in ("start", "stop", "rec"):
                                await self._cmd_queue.put({"type": "action", "action": cmd, "source": "WEB"})
                            elif cmd == "preset":
                                val = int(data.get("value", 0))
                                await self._cmd_queue.put({"type": "preset", "preset": val, "source": "WEB"})
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

        async def favicon(request):
            # avoid noisy 404s
            return web.Response(status=204, text="")


        async def config_page(request):
            if self.cfg.WEB_HUD_TOKEN:
                tok = request.query.get("token", "")
                if tok != self.cfg.WEB_HUD_TOKEN:
                    return web.Response(status=403, text="Forbidden")
            return web.Response(text=self._web_config_html(), content_type="text/html", charset="utf-8")

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


        app = web.Application()
        app.add_routes([
            web.get("/", index),
            web.get("/viewer", viewer),
            web.get("/config", config_page),
            web.get("/config_timer", config_timer_page),
            web.get("/ws", ws_handler),
            web.get("/app.js", app_js),
            web.get("/config.js", config_js),
            web.get("/api/config", api_get_config),
            web.post("/api/config/unlock", api_unlock),
            web.post("/api/config/apply", api_apply_config),
            web.post("/api/config/restore_global", api_restore_global),
            web.post("/api/config/restore_timer", api_restore_timer),
            web.get("/api/config/export", api_export),
            web.post("/api/config/import", api_import),
            web.get("/favicon.ico", favicon),
        ])

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=self.cfg.WEB_HUD_HOST, port=int(self.cfg.WEB_HUD_PORT))
        await site.start()

        self._web_runner = runner
        self._web_site = site
        self._post(f"WEB: HUD at http://{self._local_ip_hint()}:{int(self.cfg.WEB_HUD_PORT)}")

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
                self.obs.connect()

            if not self.midi.is_connected():
                self.midi.connect()

            for msg in self.midi.pending():
                try:
                    if self.midi.is_note_on(msg, self.cfg.NOTE_START_STREAM):
                        self._start_stream_flow("MIDI")
                    elif self.midi.is_note_on(msg, self.cfg.NOTE_STOP_STREAM):
                        self._request_stop("MIDI")
                    elif self.midi.is_note_on(msg, self.cfg.NOTE_REC_TOGGLE):
                        self._toggle_record("MIDI")
                    else:
                        pn = self.midi.is_note_in_range(msg, self.cfg.NOTE_PRESET_FIRST, self.cfg.NOTE_PRESET_LAST)
                        if pn is not None:
                            preset = pn - self.cfg.NOTE_PRESET_FIRST + 1
                            self._handle_preset(preset, "MIDI")
                except Exception as e:
                    self._post(f"MIDI error: {e}")

            if self._pending_stream_start and self.obs.connected:
                if time.time() >= getattr(self, "_pending_start_not_before", 0.0):
                    if self.cfg.HOME_TEST_MODE or self.cam_state == "AWAKE":
                        reason = self._pending_start_reason or "PENDING"
                        self._pending_stream_start = False
                        self._pending_start_reason = ""
                        self._start_stream_flow(reason)

            self._camera_ready_tick()
            self._preset_delay_tick()
            self._stop_tick()
            self._timer_tick()

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
                obs_line = f"OBS: {'STREAM ON' if streaming else 'stream off'} / {'REC ON' if recording else 'rec off'}"
                if err:
                    obs_line = f"OBS: offline ({err})"
                cam_src_line = self._camera_source_status_line()

            cam_line = f"CAM: {self.cam_state}" + (f" | {cam_src_line}" if cam_src_line else "")

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

                        # Camera issue (meaningful only when streaming and OBS can *see* the configured camera source)
            cam_issue = False
            cam_check_enabled = (getattr(self.cfg, "CAMERA_SOURCE_CHECK_ENABLED", True) and
                                 (not self.cfg.HOME_TEST_MODE or getattr(self.cfg, "CAMERA_SOURCE_CHECK_IN_HOME_TEST", False)))

            if cam_check_enabled:
                try:
                    grace_s = float(getattr(self.cfg, "CAMERA_SOURCE_WARN_AFTER_SECONDS", 25))
                except Exception:
                    grace_s = 25.0

                # IMPORTANT: use stream_stable_since ONLY.
                # Using app start_time here causes a false-positive right at stream start if the app has been running > grace_s.
                since = self.stream_stable_since
                if streaming and since and (time.time() - since) >= grace_s:
                    res = self._cam_src_last_result
                    if isinstance(res, dict):
                        ok = res.get("ok")
                        vis = res.get("visible")
                        # Treat explicit missing/hidden states as a camera issue.
                        if ok is False or vis is False:
                            cam_issue = True

            if cam_issue and not self._cam_issue_prev:
                self._note_critical("Camera issue detected (OBS source hidden/offline)")
                detail = ""
                try:
                    res = self._cam_src_last_result
                    if isinstance(res, dict) and res.get("detail"):
                        detail = f" | {res.get('detail')}"
                except Exception:
                    pass
                self._post("ERROR: Camera issue detected (OBS source hidden/offline)" + detail)
            elif (not cam_issue) and self._cam_issue_prev:
                self._post("Camera issue cleared")
            self._cam_issue_prev = cam_issue

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
                    # stream_ended_at is set in _stop_tick for requested stops
                    self._close_session_log("requested_stop")
                else:
                    self._note_critical("Stream stopped unexpectedly")
                    self._post("ERROR: Stream stopped unexpectedly")
                    self._close_session_log("unexpected_stop")
                    # If we still want to be live, arm recovery
                    if self.cfg.AUTO_RECOVER_ENABLED:
                        self._arm_recovery("Unexpected stream stop")

            if not streaming:
                self.stream_stable_since = None

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
                if self.minimized and ((prev_streaming and (not streaming) and (not self._stop_intent)) or err or cam_issue):
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
                if cam_issue:
                    health_level = "DEGRADED"
                    health_title = "DEGRADED"
                    health_detail = "Camera issue detected in OBS (check source visibility)."
                elif now < self._recovered_until:
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

        await self._stop_web_server()

    def _runner(self):
        try:
            asyncio.run(self.loop())
        except Exception as e:
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
