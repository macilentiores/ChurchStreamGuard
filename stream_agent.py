"""
church_stream_guard_UPDATED_v6b.py

OBS (Open Broadcaster Software) + Proclaim MIDI (Musical Instrument Digital Interface) guard app.

Primary goals:
- Timed auto-start (Sunday HH:MM in America/Regina) + MIDI backup start
- Stop stream with delay + camera power OFF
- Record toggle
- Camera PTZ preset recall via VISCA-over-UDP (Video System Control Architecture)
- Small HUD with clear status + timer countdown + manual override buttons

MIDI mapping (default):
- 60: Start stream (backup to timer)
- 61: Stop stream (requests stop; actual stop after STOP_DELAY_SECONDS)
- 62: Record toggle
- 70..79: Presets 1..10 (labels configurable)

Notes:
- Accepts Proclaim velocity=0 (many systems treat note_on velocity 0 as note_off);
  we treat BOTH note_on and note_off as triggers for reliability.
- MIDI port matching uses a substring; set it to "proclaim to script" to match
  "proclaim to script 0" on Windows.

Requires:
- obsws-python
- mido
- python-rtmidi
- tzdata (recommended on Windows for ZoneInfo)
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

try:
    from zoneinfo import ZoneInfo  # py3.9+
except Exception:
    ZoneInfo = None

try:
    import mido
except Exception:
    mido = None

try:
    from obsws_python import ReqClient  # OBS WebSocket v5
except Exception:
    ReqClient = None

import tkinter as tk
from tkinter import ttk


# =========================
# CONFIG (edit these only)
# =========================

@dataclass
class Config:
    # ---- Modes ----
    HOME_TEST_MODE: bool = True

    # ---- OBS WebSocket ----
    OBS_HOST: str = "127.0.0.1"
    OBS_PORT: int = 4455
    OBS_PASSWORD: str = ""  # blank if OBS websocket auth is OFF

    # ---- Optional OBS camera-source monitoring (NDI) ----
    # Goal: warn you if the FoMaKo camera feed is missing inside OBS, so you know *why* a stream would fail.
    # Configure EITHER:
    #  - OBS_CAMERA_INPUT_NAME  (the OBS source/input name, as seen in the OBS "Sources" list)
    # OR
    #  - OBS_CAMERA_NDI_SENDER_NAME (the NDI sender name selected inside the NDI source properties)
    #
    # If OBS_CAMERA_INPUT_NAME is blank, the app will try to *find* the correct OBS input by scanning all
    # inputs' settings for the NDI sender string.
    OBS_CAMERA_INPUT_NAME: str = ""  # e.g. "fomako ndi"
    OBS_CAMERA_NDI_SENDER_NAME: str = "NDI_HX (NDI-E477DA4C5898)"  # from the NDI source properties

    # Optional: name of the scene where you expect the camera to be visible.
    # Leave blank to check the current Program scene.
    OBS_CAMERA_SCENE_NAME: str = ""

    # How often to poll OBS for camera-source presence
    CAMERA_SOURCE_CHECK_SECONDS: int = 5

    # After camera wakes, if the source is still missing for this long, post a warning in the HUD
    CAMERA_SOURCE_WARN_AFTER_SECONDS: int = 25

    AUTO_RECONNECT_OBS: bool = True

    # ---- Stream behavior ----
    STOP_DELAY_SECONDS: int = 30  # stop requested -> wait N seconds -> stop stream + camera off
    START_DEBOUNCE_SECONDS: float = 5.0  # ignore repeated start requests within this window

    # ---- Camera (FoMaKo) VISCA over UDP ----
    CAMERA_IP: str = "192.168.88.20"
    CAMERA_VISCA_PORT: int = 1259
    VISCA_USE_OVERIP_HEADER: bool = False
    VISCA_ADDR: int = 0x81  # common for IP cams
    CAMERA_BOOT_SECONDS: int = 20  # wait after camera power on before "ready"
    CAMERA_AUTO_WAKE_ON_PRESET: bool = True

    # Many IP cams map "Preset 1" -> pp=0 in VISCA.
    PRESET_NUMBER_BASE: int = 0

    # ---- MIDI ----
    # IMPORTANT: set to match your enumerated port, e.g. "proclaim to script"
    # so it matches "proclaim to script 0"
    MIDI_INPUT_PORT_SUBSTRING: str = "proclaim to script"
    MIDI_CHANNEL_1_BASED: int = 1

    NOTE_START_STREAM: int = 60
    NOTE_STOP_STREAM: int = 61
    NOTE_REC_TOGGLE: int = 62

    NOTE_PRESET_FIRST: int = 70
    NOTE_PRESET_LAST: int = 79

    # ---- Optional timer start (primary start) ----
    USE_TIMER_START: bool = True
    TIMER_START_HHMM: str = "9:45"  # local Regina time
    TIMER_WEEKDAY: int = 6  # Monday=0 ... Sunday=6
    TIMEZONE: str = "America/Regina"
    TIMER_PERSIST_STATE: bool = True
    TIMER_STATE_FILE: str = "csg_timer_state.json"
    TIMER_FIRE_GRACE_MINUTES: int = 15

    # Timezone fallback if ZoneInfo fails
    TZ_FALLBACK_MODE: str = "local"  # "local" or "fixed_offset"
    TZ_FALLBACK_UTC_OFFSET_HOURS: int = -6  # Regina CST year-round

    # ---- Labels for presets 1..10 ----
    PRESET_LABELS: Dict[int, str] = field(default_factory=lambda: {
        1: "Pulpit",
        2: "Panorama",
        3: "Children's Time",
        4: "Altar",
        5: "Choir",
        6: "Screen",
        7: "Band",
        8: "Piano",
        9: "(Unassigned)",
        10: "(Unassigned)",
    })


CFG = Config()


# =========================
# Utilities
# =========================

def _safe_lower(s: str) -> str:
    return (s or "").lower().strip()


def get_tz(cfg: Config):
    """Return tzinfo or None."""
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(cfg.TIMEZONE)
    except Exception:
        return None


def now_in_cfg_tz(cfg: Config) -> dt.datetime:
    """Return timezone-aware or naive datetime depending on availability."""
    tz = get_tz(cfg)
    if tz is not None:
        return dt.datetime.now(tz)

    # Fallbacks
    if cfg.TZ_FALLBACK_MODE == "fixed_offset":
        off = dt.timezone(dt.timedelta(hours=cfg.TZ_FALLBACK_UTC_OFFSET_HOURS))
        return dt.datetime.now(off)
    # "local"
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


# =========================
# VISCA Camera
# =========================

class ViscaCamera:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addr = (cfg.CAMERA_IP, cfg.CAMERA_VISCA_PORT)

    def _wrap(self, payload: bytes) -> bytes:
        # If you ever need VISCA-over-IP wrapper, implement here.
        # Default False for your FoMaKo.
        if not self.cfg.VISCA_USE_OVERIP_HEADER:
            return payload
        # Minimal "over IP" wrapper placeholder; many cams don't need it.
        # Keeping as pass-through for safety unless explicitly enabled.
        return payload

    def send(self, payload: bytes):
        packet = self._wrap(payload)
        self.sock.sendto(packet, self.addr)

    def power_on(self):
        # VISCA: 8x 01 04 00 02 FF
        self.send(bytes([self.cfg.VISCA_ADDR, 0x01, 0x04, 0x00, 0x02, 0xFF]))

    def power_off(self):
        # VISCA: 8x 01 04 00 03 FF
        self.send(bytes([self.cfg.VISCA_ADDR, 0x01, 0x04, 0x00, 0x03, 0xFF]))

    def recall_preset(self, preset_num_1_based: int):
        # VISCA preset recall: 8x 01 04 3F 02 pp FF (pp=0..127)
        pp = (preset_num_1_based - 1) + self.cfg.PRESET_NUMBER_BASE
        if pp < 0:
            pp = 0
        if pp > 127:
            pp = 127
        self.send(bytes([self.cfg.VISCA_ADDR, 0x01, 0x04, 0x3F, 0x02, pp, 0xFF]))


# =========================
# OBS Control
# =========================

class ObsController:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client: Optional[ReqClient] = None
        self.last_error: str = ""
        self.connected: bool = False

    def connect(self) -> bool:
        if ReqClient is None:
            self.last_error = "obsws-python not installed"
            self.connected = False
            return False
        try:
            self.client = ReqClient(
                host=self.cfg.OBS_HOST,
                port=self.cfg.OBS_PORT,
                password=self.cfg.OBS_PASSWORD or None,
                timeout=2,
            )
            # light touch test call
            _ = self.client.get_version()
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
        """Return (streaming, recording, errtext)."""
        if not self._ok():
            return False, False, (self.last_error or "OBS offline")
        try:
            out = self.client.get_stream_status()
            streaming = bool(getattr(out, "output_active", False))
            # OBS uses separate API for record status
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
            return True, "OBS: start stream sent"
        except Exception as e:
            self.connected = False
            self.last_error = str(e)
            return False, self.last_error

    def stop_stream(self) -> Tuple[bool, str]:
        if not self._ok():
            return False, "OBS not connected"
        try:
            self.client.stop_stream()
            return True, "OBS: stop stream sent"
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
                return True, "OBS: stop record sent"
            self.client.start_record()
            return True, "OBS: start record sent"
        except Exception as e:
            self.connected = False
            self.last_error = str(e)
            return False, self.last_error



    # -----------------------------
    # Camera source monitoring (NDI)
    # -----------------------------
    def _safe_call(self, method_name: str, **kwargs):
        """Call an obsws-python ReqClient method safely. Returns (resp, error_str_or_empty)."""
        if not self.connected or not self.client:
            return None, "OBS not connected"
        fn = getattr(self.client, method_name, None)
        if fn is None:
            return None, f"OBS client missing method: {method_name}"
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
        """Recursively search dict/list/str for a substring."""
        if not needle:
            return False
        try:
            if isinstance(container, str):
                return needle in container
            if isinstance(container, dict):
                return any(ObsController._contains_text(v, needle) for v in container.values())
            if isinstance(container, (list, tuple)):
                return any(ObsController._contains_text(v, needle) for v in container)
        except Exception:
            return False
        return False

    def camera_source_status(self, cfg) -> dict:
        """Return info about whether the camera source exists in OBS and whether it is visible."""
        if not self.connected or not self.client:
            return {"ok": None, "visible": None, "input": None, "detail": "OBS offline"}

        # 1) list inputs
        resp, err = self._safe_call("get_input_list")
        if err:
            return {"ok": None, "visible": None, "input": None, "detail": f"get_input_list failed: {err}"}

        inputs = self._get(resp, "inputs", []) or []
        names = []
        for it in inputs:
            nm = self._get(it, "inputName") or self._get(it, "sourceName") or self._get(it, "name")
            if nm:
                names.append(nm)

        cam_input = None
        mismatch_note = ""

        # Prefer explicit OBS source name
        if getattr(cfg, "OBS_CAMERA_INPUT_NAME", ""):
            if cfg.OBS_CAMERA_INPUT_NAME in names:
                cam_input = cfg.OBS_CAMERA_INPUT_NAME
            else:
                mismatch_note = f"Input '{cfg.OBS_CAMERA_INPUT_NAME}' not found."

        # Otherwise try to discover by scanning settings for the NDI sender string
        if cam_input is None and getattr(cfg, "OBS_CAMERA_NDI_SENDER_NAME", ""):
            target = cfg.OBS_CAMERA_NDI_SENDER_NAME
            for nm in names:
                r2, e2 = self._safe_call("get_input_settings", inputName=nm)
                if e2 or r2 is None:
                    continue
                settings = self._get(r2, "inputSettings", {}) or {}
                if self._contains_text(settings, target):
                    cam_input = nm
                    break

        if cam_input is None:
            detail = mismatch_note or "Camera input not found (configure OBS_CAMERA_INPUT_NAME or OBS_CAMERA_NDI_SENDER_NAME)."
            return {"ok": False, "visible": None, "input": None, "detail": detail}

        # 2) (Optional) verify sender string is still selected (helps if multiple NDI sources exist)
        if getattr(cfg, "OBS_CAMERA_NDI_SENDER_NAME", ""):
            r3, e3 = self._safe_call("get_input_settings", inputName=cam_input)
            if not e3 and r3 is not None:
                settings = self._get(r3, "inputSettings", {}) or {}
                if not self._contains_text(settings, cfg.OBS_CAMERA_NDI_SENDER_NAME):
                    mismatch_note = f"NDI sender mismatch (expected '{cfg.OBS_CAMERA_NDI_SENDER_NAME}')."

        # 3) is it in the program scene?
        scene_name = getattr(cfg, "OBS_CAMERA_SCENE_NAME", "") or ""
        if not scene_name:
            r4, e4 = self._safe_call("get_current_program_scene")
            if not e4 and r4 is not None:
                scene_name = self._get(r4, "currentProgramSceneName") or self._get(r4, "sceneName") or ""

        visible = None
        if scene_name:
            r5, e5 = self._safe_call("get_scene_item_list", sceneName=scene_name)
            if not e5 and r5 is not None:
                items = self._get(r5, "sceneItems", []) or []
                found = False
                for it in items:
                    src = self._get(it, "sourceName") or self._get(it, "inputName") or self._get(it, "sceneItemName")
                    if src == cam_input:
                        visible = bool(self._get(it, "sceneItemEnabled", True))
                        found = True
                        break
                if not found:
                    visible = False

        detail = f"Found input '{cam_input}'"
        if scene_name and visible is not None:
            detail += f" (scene='{scene_name}', visible={visible})"
        if mismatch_note:
            detail += f" | {mismatch_note}"
        return {"ok": True, "visible": visible, "input": cam_input, "detail": detail}

# =========================
# MIDI Listener
# =========================

class MidiListener:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.inport = None
        self.connected_name: str = ""
        self.last_error: str = ""

    def connect(self) -> bool:
        if mido is None:
            self.last_error = "mido not installed"
            return False
        try:
            names = mido.get_input_names()
            wanted = _safe_lower(self.cfg.MIDI_INPUT_PORT_SUBSTRING)
            match = None
            for n in names:
                if wanted in _safe_lower(n):
                    match = n
                    break
            if match is None:
                self.last_error = f"No MIDI in port matching '{self.cfg.MIDI_INPUT_PORT_SUBSTRING}' (found {len(names)})"
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
        except Exception as e:
            self.last_error = str(e)
            self.inport = None
            self.connected_name = ""
            return []

    @staticmethod
    def _msg_note(msg) -> Optional[int]:
        try:
            return int(getattr(msg, "note", None))
        except Exception:
            return None

    @staticmethod
    def _msg_ch(msg) -> Optional[int]:
        try:
            # mido uses 0-based channels
            return int(getattr(msg, "channel", None))
        except Exception:
            return None

    def _channel_ok(self, msg) -> bool:
        ch0 = self._msg_ch(msg)
        if ch0 is None:
            return False
        return (ch0 + 1) == int(self.cfg.MIDI_CHANNEL_1_BASED)

    def is_note_on(self, msg, note: int) -> bool:
        """
        Treat BOTH note_on and note_off as triggers to handle velocity=0 behavior.
        """
        if not self._channel_ok(msg):
            return False
        n = self._msg_note(msg)
        if n is None or n != int(note):
            return False
        t = getattr(msg, "type", "")
        return t in ("note_on", "note_off")

    def is_note_in_range(self, msg, lo: int, hi: int) -> Optional[int]:
        if not self._channel_ok(msg):
            return None
        n = self._msg_note(msg)
        if n is None:
            return None
        if int(lo) <= n <= int(hi):
            t = getattr(msg, "type", "")
            if t in ("note_on", "note_off"):
                return n
        return None


# =========================
# App
# =========================

class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.root = tk.Tk()
        self.root.title("Church Stream Guard")

        # state
        self.running = True
        self.obs = ObsController(cfg)
        self.midi = MidiListener(cfg)
        self.cam = ViscaCamera(cfg)

        self.cam_state = "SLEEP"   # SLEEP | WAKING | AWAKE
        self.cam_ready_at: float = 0.0


        # OBS camera-source monitoring (NDI)
        self._cam_src_last_check: float = 0.0
        self._cam_src_last_result: Optional[dict] = None
        self._cam_src_warned: bool = False
        self._cam_awake_since: Optional[float] = None
        self._queued_preset: Optional[int] = None
        self._pending_stream_start: bool = False  # requested start but waiting for cam/obs
        self._pending_start_reason: str = ""

        self._stop_pending: bool = False
        self._stop_at: float = 0.0

        self._timer_done_today_date: Optional[dt.date] = None
        self._timer_done_status: Optional[str] = None  # "fired" or "missed"
        self._timer_done_time_hhmm: Optional[str] = None
        self._last_start_request_ts: float = 0.0
        self._load_timer_state()

        # UI variables
        self.var_mode = tk.StringVar()
        self.var_timer = tk.StringVar()
        self.var_obs = tk.StringVar()
        self.var_midi = tk.StringVar()
        self.var_cam = tk.StringVar()
        self.var_last = tk.StringVar()

        # Build UI
        self._build_ui()
        self._update_mode_text()
        self._post("Started. Waiting for OBS/MIDI...")

        # background loop thread
        self.thread = threading.Thread(target=self._runner, daemon=True)
        self.thread.start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        frm = ttk.Frame(self.root, padding=8)
        frm.grid(row=0, column=0, sticky="nsew")

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        ttk.Label(frm, textvariable=self.var_mode, font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(frm, textvariable=self.var_timer).grid(row=1, column=0, sticky="w", pady=(2, 6))

        ttk.Separator(frm).grid(row=2, column=0, sticky="ew", pady=4)

        ttk.Label(frm, textvariable=self.var_obs).grid(row=3, column=0, sticky="w")
        ttk.Label(frm, textvariable=self.var_midi).grid(row=4, column=0, sticky="w")
        ttk.Label(frm, textvariable=self.var_cam).grid(row=5, column=0, sticky="w")

        ttk.Label(frm, text="Last:", font=("Segoe UI", 9, "bold")).grid(row=6, column=0, sticky="w", pady=(8, 0))
        ttk.Label(frm, textvariable=self.var_last, wraplength=420).grid(row=7, column=0, sticky="w")

        btns = ttk.Frame(frm)
        btns.grid(row=8, column=0, sticky="w", pady=(10, 0))

        ttk.Button(btns, text="Start Stream", command=lambda: self._ui_fire("start")).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(btns, text="Stop Stream", command=lambda: self._ui_fire("stop")).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(btns, text="REC Toggle", command=lambda: self._ui_fire("rec")).grid(row=0, column=2, padx=(0, 6))

        # Preset test buttons (1..8 for compactness)
        presets = ttk.Frame(frm)
        presets.grid(row=9, column=0, sticky="w", pady=(10, 0))

        for i in range(1, 9):
            label = self.cfg.PRESET_LABELS.get(i, f"Preset {i}")
            ttk.Button(
                presets,
                text=f"{i}:{label}",
                command=lambda p=i: self._ui_preset(p),
                width=14
            ).grid(row=(i-1)//4, column=(i-1) % 4, padx=2, pady=2)

    def _update_mode_text(self):
        if self.cfg.HOME_TEST_MODE:
            self.var_mode.set("HOME TEST MODE")
        else:
            self.var_mode.set("CHURCH MODE")

    def _post(self, msg: str):
        # thread-safe UI update
        def _do():
            self.var_last.set(msg)
        self.root.after(0, _do)

    def _set_timer_line(self, msg: str):
        def _do():
            self.var_timer.set(msg)
        self.root.after(0, _do)

    def _set_status_lines(self, obs_line: str, midi_line: str, cam_line: str):
        def _do():
            self.var_obs.set(obs_line)
            self.var_midi.set(midi_line)
            self.var_cam.set(cam_line)
        self.root.after(0, _do)

    def _camera_source_status_line(self) -> str:
        """Returns a short status like 'SRC: OK' / 'SRC: MISSING' / 'SRC: (OBS offline)'."""
        # If OBS monitoring isn't configured, don't clutter the HUD.
        if not getattr(self.cfg, "OBS_CAMERA_INPUT_NAME", "") and not getattr(self.cfg, "OBS_CAMERA_NDI_SENDER_NAME", ""):
            return ""

        # Rate-limit the polling
        now = time.time()
        interval = max(1, int(getattr(self.cfg, "CAMERA_SOURCE_CHECK_SECONDS", 5)))
        if (now - self._cam_src_last_check) < interval and self._cam_src_last_result is not None:
            return self._format_cam_src_line(self._cam_src_last_result)

        self._cam_src_last_check = now

        # If OBS isn't connected (yet), return a helpful hint
        if not self.obs.connected:
            self._cam_src_last_result = {"ok": None, "visible": None, "input": None, "detail": "OBS offline"}
            return self._format_cam_src_line(self._cam_src_last_result)

        # Not all versions of obsws-python support every call; fail softly.
        if not hasattr(self.obs, "camera_source_status"):
            self._cam_src_last_result = {"ok": None, "visible": None, "input": None, "detail": "OBS camera check not supported"}
            return self._format_cam_src_line(self._cam_src_last_result)

        try:
            res = self.obs.camera_source_status(self.cfg)
        except Exception as e:
            res = {"ok": None, "visible": None, "input": None, "detail": f"camera check error: {e}"}

        self._cam_src_last_result = res

        # One-time warning after camera is expected to be ready
        warn_after = int(getattr(self.cfg, "CAMERA_SOURCE_WARN_AFTER_SECONDS", 25))
        if (
            self.cam_state == "AWAKE"
            and self._cam_awake_since is not None
            and not self._cam_src_warned
            and warn_after > 0
            and (now - self._cam_awake_since) >= warn_after
        ):
            ok = res.get("ok")
            visible = res.get("visible")
            if ok is False or visible is False:
                self._cam_src_warned = True
                self._post("WARN: camera feed not detected in OBS yet (check NDI source / network).")

        return self._format_cam_src_line(res)

    def _format_cam_src_line(self, res: dict) -> str:
        ok = res.get("ok")
        visible = res.get("visible")
        if ok is None:
            return "SRC: (OBS?)"
        if ok is False:
            return "SRC: MISSING"
        # ok == True
        if visible is True:
            return "SRC: OK"
        if visible is False:
            return "SRC: FOUND (not in scene)"
        return "SRC: FOUND"

    def _ui_fire(self, action: str):
        # Manual override always allowed
        if action == "start":
            self._start_stream_flow("HUD")
        elif action == "stop":
            self._request_stop("HUD")
        elif action == "rec":
            self._toggle_record("HUD")

    def _ui_preset(self, preset_num: int):
        self._handle_preset(preset_num, source="HUD")

    # -------- Camera logic --------

    def _camera_wake(self, source: str):
        if self.cam_state in ("WAKING", "AWAKE"):
            return
        self.cam_state = "WAKING"
        self.cam_ready_at = time.time() + float(self.cfg.CAMERA_BOOT_SECONDS)
        if self.cfg.HOME_TEST_MODE:
            self._post(f"{source}: camera wake (simulated) - ready in {self.cfg.CAMERA_BOOT_SECONDS}s")
        else:
            try:
                self.cam.power_on()
                self._post(f"{source}: camera power ON sent - waiting {self.cfg.CAMERA_BOOT_SECONDS}s")
            except Exception as e:
                self._post(f"{source}: camera power ON error: {e}")

    def _camera_sleep(self, source: str):
        self.cam_state = "SLEEP"
        self._cam_awake_since = None
        self._cam_src_warned = False
        self.cam_ready_at = 0.0
        self._queued_preset = None
        if self.cfg.HOME_TEST_MODE:
            self._post(f"{source}: camera sleep (simulated)")
        else:
            try:
                self.cam.power_off()
                self._post(f"{source}: camera power OFF sent")
            except Exception as e:
                self._post(f"{source}: camera power OFF error: {e}")

    def _camera_ready_tick(self):
        if self.cam_state == "WAKING" and time.time() >= self.cam_ready_at:
            self.cam_state = "AWAKE"
            self._cam_awake_since = time.time()
            self._cam_src_warned = False
            self._post("CAM: awake/ready")
            # apply queued preset if any
            if self._queued_preset is not None:
                p = self._queued_preset
                self._queued_preset = None
                self._send_preset(p, source="QUEUE")
            # if stream start pending, attempt now
            if self._pending_stream_start:
                reason = self._pending_start_reason or "PENDING"
                self._pending_stream_start = False
                self._pending_start_reason = ""
                self._start_stream_flow(reason)

    def _send_preset(self, preset_num: int, source: str):
        label = self.cfg.PRESET_LABELS.get(preset_num, f"Preset {preset_num}")
        if self.cfg.HOME_TEST_MODE:
            self._post(f"{source}: preset {preset_num} ({label}) (simulated)")
            return
        try:
            self.cam.recall_preset(preset_num)
            self._post(f"{source}: preset {preset_num} ({label}) sent")
        except Exception as e:
            self._post(f"{source}: preset error: {e}")

    def _handle_preset(self, preset_num: int, source: str):
        # ensure 1..10
        if preset_num < 1 or preset_num > 10:
            self._post(f"{source}: preset {preset_num} ignored (out of range)")
            return

        if self.cam_state == "SLEEP" and self.cfg.CAMERA_AUTO_WAKE_ON_PRESET:
            self._queued_preset = preset_num
            self._camera_wake(source=f"{source}: auto-wake for preset")
            self._post(f"{source}: queued preset {preset_num} while waking")
            return

        if self.cam_state == "WAKING":
            self._queued_preset = preset_num
            self._post(f"{source}: queued preset {preset_num} (camera waking)")
            return

        # AWAKE or no auto-wake
        self._send_preset(preset_num, source=source)

    # -------- OBS logic --------

    def _start_stream_flow(self, source: str):
        # debounce starts
        now = time.time()
        if (now - self._last_start_request_ts) < float(self.cfg.START_DEBOUNCE_SECONDS):
            self._post(f"{source}: start ignored (debounce)")
            return
        self._last_start_request_ts = now

        # Ensure camera is ready (church mode)
        if not self.cfg.HOME_TEST_MODE:
            if self.cam_state == "SLEEP":
                self._pending_stream_start = True
                self._pending_start_reason = source
                self._camera_wake(source=f"{source}: wake before start")
                self._post(f"{source}: start pending (waiting for camera)")
                return
            if self.cam_state == "WAKING":
                self._pending_stream_start = True
                self._pending_start_reason = source
                self._post(f"{source}: start pending (camera waking)")
                return

        # Ensure OBS connected; if not, queue start until OBS reconnects
        if not self.obs.connected:
            self._pending_stream_start = True
            self._pending_start_reason = source
            self._post(f"{source}: start pending (waiting for OBS connection)")
            return

        ok, msg = self.obs.start_stream()
        if ok:
            self._post(f"{source}: stream start requested")
        else:
            # queue again if it was a transient disconnect
            self._pending_stream_start = True
            self._pending_start_reason = source
            self._post(f"{source}: OBS start failed; pending retry ({msg})")

    def _request_stop(self, source: str):
        self._stop_pending = True
        self._stop_at = time.time() + int(self.cfg.STOP_DELAY_SECONDS)
        self._post(f"{source}: stop requested; stopping in {self.cfg.STOP_DELAY_SECONDS}s")

    def _stop_tick(self):
        if not self._stop_pending:
            return
        remaining = int(self._stop_at - time.time())
        if remaining > 0:
            self._set_timer_line(f"Stop pending: T-{fmt_hms(remaining)}")
            return

        # time to stop now
        self._stop_pending = False
        self._set_timer_line("")  # clear stop countdown line (timer line will refresh next tick)
        if self.obs.connected:
            ok, msg = self.obs.stop_stream()
            self._post(f"STOP: {msg}" if ok else f"STOP: OBS stop failed ({msg})")
        else:
            self._post("STOP: OBS not connected; cannot stop stream")

        # camera off at end (church mode only)
        if not self.cfg.HOME_TEST_MODE:
            self._camera_sleep(source="STOP")

    def _toggle_record(self, source: str):
        if not self.obs.connected:
            self._post(f"{source}: OBS not connected; cannot toggle record")
            return
        ok, msg = self.obs.toggle_record()
        self._post(f"{source}: {msg}" if ok else f"{source}: REC failed ({msg})")

    # -------- Timer logic --------

    def _timer_target_today(self) -> Optional[dt.datetime]:
        if not self.cfg.USE_TIMER_START:
            return None
        now = now_in_cfg_tz(self.cfg)
        if now.weekday() != int(self.cfg.TIMER_WEEKDAY):
            return None
        hh, mm = parse_hhmm(self.cfg.TIMER_START_HHMM)
        # keep tz awareness if present
        if now.tzinfo is not None:
            return now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return dt.datetime(now.year, now.month, now.day, hh, mm, 0)


    def _timer_state_path(self) -> str:
        # Store next to the script by default (portable with the repo).
        base = self.cfg.TIMER_STATE_FILE
        if os.path.isabs(base):
            return base
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), base)

    def _load_timer_state(self) -> None:
        if not self.cfg.TIMER_PERSIST_STATE:
            return
        path = self._timer_state_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            date_s = data.get("date")
            status = data.get("status")
            hhmm = data.get("hhmm", self.cfg.TIMER_START_HHMM)
            if not date_s or status not in ("fired", "missed"):
                return
            today = now_in_cfg_tz(self.cfg).date()
            if date_s == today.isoformat():
                self._timer_done_today_date = today
                self._timer_done_status = status
                self._timer_done_time_hhmm = hhmm
        except FileNotFoundError:
            return
        except Exception:
            return

    def _save_timer_state(self, status: str, hhmm: str) -> None:
        if not self.cfg.TIMER_PERSIST_STATE:
            return
        try:
            today = now_in_cfg_tz(self.cfg).date()
            data = {"date": today.isoformat(), "status": status, "hhmm": hhmm}
            with open(self._timer_state_path(), "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            return

    def _timer_tick(self):
        if not self.cfg.USE_TIMER_START:
            self._set_timer_line("Timer: disabled")
            return

        now = now_in_cfg_tz(self.cfg)

        # show countdown (even on non-Sunday show next schedule message)
        if now.weekday() != int(self.cfg.TIMER_WEEKDAY):
            self._set_timer_line(f"Timer: next Sun {self.cfg.TIMER_START_HHMM} ({self.cfg.TIMEZONE})")
            return

        target = self._timer_target_today()
        if target is None:
            self._set_timer_line(f"Timer: Sun {self.cfg.TIMER_START_HHMM} ({self.cfg.TIMEZONE})")
            return

        # Fire once per date
        today = now.date()
        # If we already handled today's timer (even from a previous run), don't auto-start again.
        if self._timer_done_today_date == today:
            if self._timer_done_status == "missed":
                self._set_timer_line(f"Timer: missed today ({self.cfg.TIMER_START_HHMM}) — manual start")
            else:
                self._set_timer_line(f"Timer: fired today ({self.cfg.TIMER_START_HHMM})")
            return

        # countdown
        delta = int((target - now).total_seconds())
        if delta > 0:
            self._set_timer_line(f"Timer: T-{fmt_hms(delta)} to auto-start")
            return

        # time reached (or passed)
        past_seconds = int(-delta)
        grace_seconds = max(0, int(self.cfg.TIMER_FIRE_GRACE_MINUTES)) * 60
        if past_seconds > grace_seconds:
            # Too late — don't surprise-start the stream hours later.
            self._timer_done_today_date = today
            self._timer_done_status = "missed"
            self._timer_done_time_hhmm = self.cfg.TIMER_START_HHMM
            self._save_timer_state("missed", self.cfg.TIMER_START_HHMM)
            self._set_timer_line(f"Timer: missed today ({self.cfg.TIMER_START_HHMM}) — manual start")
            return

        # Within grace window: fire once
        self._timer_done_today_date = today
        self._timer_done_status = "fired"
        self._timer_done_time_hhmm = self.cfg.TIMER_START_HHMM
        self._save_timer_state("fired", self.cfg.TIMER_START_HHMM)

        # If OBS is already streaming, don't re-trigger.
        streaming, _recording, _err = self.obs.get_status()
        if streaming:
            self._set_timer_line(f"Timer: fired today ({self.cfg.TIMER_START_HHMM})")
            return

        self._set_timer_line("Timer: start time reached")
        self._start_stream_flow("TIMER")

    # -------- Main loop --------

    async def loop(self):
        while self.running:
            # OBS connect/reconnect
            if not self.obs.connected and self.cfg.AUTO_RECONNECT_OBS:
                self.obs.connect()

            # MIDI connect/reconnect
            if not self.midi.is_connected():
                self.midi.connect()

            # process MIDI messages
            for msg in self.midi.pending():
                try:
                    # Start/Stop/REC
                    if self.midi.is_note_on(msg, self.cfg.NOTE_START_STREAM):
                        self._post("MIDI: NOTE 60 (start)")
                        self._start_stream_flow("MIDI")
                        continue

                    if self.midi.is_note_on(msg, self.cfg.NOTE_STOP_STREAM):
                        self._post("MIDI: NOTE 61 (stop)")
                        self._request_stop("MIDI")
                        continue

                    if self.midi.is_note_on(msg, self.cfg.NOTE_REC_TOGGLE):
                        self._post("MIDI: NOTE 62 (rec toggle)")
                        self._toggle_record("MIDI")
                        continue

                    pn = self.midi.is_note_in_range(msg, self.cfg.NOTE_PRESET_FIRST, self.cfg.NOTE_PRESET_LAST)
                    if pn is not None:
                        preset_num = (pn - self.cfg.NOTE_PRESET_FIRST) + 1
                        label = self.cfg.PRESET_LABELS.get(preset_num, f"Preset {preset_num}")
                        self._post(f"MIDI: NOTE {pn} -> preset {preset_num} ({label})")
                        self._handle_preset(preset_num, source="MIDI")
                except Exception as e:
                    self._post(f"MIDI handler error: {e}")

            # if start is pending waiting for OBS, try again once connected
            if self._pending_stream_start and self.obs.connected:
                # also need camera ready in church mode
                if self.cfg.HOME_TEST_MODE or self.cam_state == "AWAKE":
                    reason = self._pending_start_reason or "PENDING"
                    self._pending_stream_start = False
                    self._pending_start_reason = ""
                    self._start_stream_flow(reason)

            # camera readiness tick
            self._camera_ready_tick()

            # stop pending tick
            self._stop_tick()

            # timer tick
            self._timer_tick()

            # update status lines
            streaming, recording, err = self.obs.get_status()
            if err:
                obs_line = f"OBS: offline ({err})"
            else:
                obs_line = f"OBS: {'STREAM ON' if streaming else 'stream off'} / {'REC ON' if recording else 'rec off'}"

            if self.midi.is_connected():
                midi_line = f"MIDI: connected ({self.midi.connected_name})"
            else:
                midi_line = f"MIDI: waiting ({self.midi.last_error})"

            cam_src = self._camera_source_status_line()
            cam_line = f"CAM: {self.cam_state}" + (f" | {cam_src}" if cam_src else "")
            self._set_status_lines(obs_line, midi_line, cam_line)

            await asyncio.sleep(0.25)

    def _runner(self):
        try:
            asyncio.run(self.loop())
        except Exception as e:
            # show in HUD
            self._post(f"Background loop crashed: {e}")

    def _on_close(self):
        self.running = False
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App(CFG).run()
