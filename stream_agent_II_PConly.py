"""
stream_agent.py

Stream Agent v7.7 - Per-Preset Delays (Feature-Gated) + v7.6 Stability

Changes:
- NEW: Optional per-preset delays for MIDI/automation (HUD presets remain immediate)
  - Enable with ENABLE_PRESET_DELAYS; set PRESET_DELAYS_SECONDS per preset (0-30s)
- Banner: "STREAM ENDED" for 60s after stop, then "READY"
- Minimize: Stays minimized after normal stop; only restores on real issues while streaming
- Marquee removed (static live banner)
- MIDI channel fixed
- All previous features intact
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import socket
import threading
import queue
import time
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

import tkinter as tk
from tkinter import ttk
from tkinter import scrolledtext


@dataclass
class Config:
    HOME_TEST_MODE: bool = True

    AUTO_MINIMIZE_ENABLED: bool = True
    AUTO_MINIMIZE_AFTER_SECONDS: int = 80

    OBS_HOST: str = "127.0.0.1"
    OBS_PORT: int = 4455
    OBS_PASSWORD: str = ""

    OBS_CAMERA_INPUT_NAME: str = ""
    OBS_CAMERA_NDI_SENDER_NAME: str = "NDI_HX (NDI-E477DA4C5898)"
    OBS_CAMERA_SCENE_NAME: str = ""
    CAMERA_SOURCE_CHECK_SECONDS: int = 5
    CAMERA_SOURCE_WARN_AFTER_SECONDS: int = 25
    AUTO_RECONNECT_OBS: bool = True

    STOP_DELAY_SECONDS: int = 30
    START_DEBOUNCE_SECONDS: float = 5.0

    CAMERA_IP: str = "192.168.88.20"
    CAMERA_VISCA_PORT: int = 1259
    VISCA_USE_OVERIP_HEADER: bool = False
    VISCA_ADDR: int = 0x81
    CAMERA_BOOT_SECONDS: int = 20
    CAMERA_AUTO_WAKE_ON_PRESET: bool = True
    PRESET_NUMBER_BASE: int = 1

    # Feature gate: delayed preset recall for MIDI/automation only (HUD remains immediate).
    ENABLE_PRESET_DELAYS: bool = True

    # Per-preset delay (seconds) for MIDI/automation preset recalls only.
    # Values are clamped to 0..30 at runtime; missing keys default to 0.
    PRESET_DELAYS_SECONDS: Dict[int, int] = field(default_factory=lambda: {
        1: 0,   # lectern
        2: 0,   # Panorama
        3: 20,   # Children's Time (suggested starting point: 15-30)
        4: 15,   # Altar
        5: 10,   # Choir (suggested starting point: 10-20)
        6: 0,   # Screen
        7: 0,   # Band
        8: 12,   # Piano
        9: 0,   # (Unassigned)
        10: 0,  # (Unassigned)
    })


    MIDI_INPUT_PORT_SUBSTRING: str = "proclaim"
    MIDI_CHANNEL_1_BASED: int = 1

    NOTE_START_STREAM: int = 60
    NOTE_STOP_STREAM: int = 61
    NOTE_REC_TOGGLE: int = 62
    NOTE_PRESET_FIRST: int = 70
    NOTE_PRESET_LAST: int = 79

    USE_TIMER_START: bool = True
    TIMER_START_HHMM: str = "9:50"
    TIMER_WEEKDAY: int = 6
    TIMEZONE: str = "America/Regina"
    TIMER_PERSIST_STATE: bool = True
    TIMER_STATE_FILE: str = "csg_timer_state.json"
    TIMER_FIRE_GRACE_MINUTES: int = 15

    TZ_FALLBACK_MODE: str = "local"
    TZ_FALLBACK_UTC_OFFSET_HOURS: int = -6

    PRESET_LABELS: Dict[int, str] = field(default_factory=lambda: {
        1: "lectern",
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

    def camera_source_status(self, cfg) -> dict:
        if not self.connected or not self.client:
            return {"ok": None, "visible": None, "input": None, "detail": "OBS offline"}

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
                r2, e2 = self._safe_call("get_input_settings", inputName=nm)
                if e2 or r2 is None:
                    continue
                settings = self._get(r2, "inputSettings", {}) or {}
                if self._contains_text(settings, target):
                    cam_input = nm
                    break

        if cam_input is None:
            return {"ok": False, "visible": None, "input": None, "detail": "Camera input not found"}

        visible = None
        scene_name = getattr(cfg, "OBS_CAMERA_SCENE_NAME", "") or ""
        if not scene_name:
            r4, e4 = self._safe_call("get_current_program_scene")
            if not e4 and r4 is not None:
                scene_name = self._get(r4, "currentProgramSceneName") or ""

        if scene_name:
            r5, e5 = self._safe_call("get_scene_item_list", sceneName=scene_name)
            if not e5 and r5 is not None:
                items = self._get(r5, "sceneItems", []) or []
                for it in items:
                    src = self._get(it, "sourceName") or self._get(it, "inputName")
                    if src == cam_input:
                        visible = bool(self._get(it, "sceneItemEnabled", True))
                        break
                if visible is None:
                    visible = False

        detail = f"Found '{cam_input}'" + (f" (scene='{scene_name}', visible={visible})" if scene_name else "")
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
        if getattr(msg, "channel", -1) + 1 != self.cfg.MIDI_CHANNEL_1_BASED:
            return False
        if getattr(msg, "note", None) != note:
            return False
        return getattr(msg, "type", "") in ("note_on", "note_off")

    def is_note_in_range(self, msg, lo: int, hi: int) -> Optional[int]:
        if getattr(msg, "channel", -1) + 1 != self.cfg.MIDI_CHANNEL_1_BASED:
            return None
        n = getattr(msg, "note", None)
        if lo <= n <= hi and getattr(msg, "type", "") in ("note_on", "note_off"):
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

        self._stop_pending: bool = False
        self._stop_at: float = 0.0

        self._timer_done_today_date: Optional[dt.date] = None
        self._timer_done_status: Optional[str] = None
        self._timer_done_time_hhmm: Optional[str] = None
        self._last_start_request_ts: float = 0.0
        self._load_timer_state()

        self.minimized: bool = False
        self.minimized_this_stream: bool = False
        self.stream_stable_since: Optional[float] = None
        self.stream_ended_at: Optional[float] = None  # For "STREAM ENDED" display

        self._build_ui()
        self._ui_pump()  # start UI pump on main thread
        self._post("Started — initializing connections...")

        self.thread = threading.Thread(target=self._runner, daemon=True)
        self.thread.start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use('clam')

        style.configure("Banner.TLabel", font=("Segoe UI", 18, "bold"), padding=14, anchor="center")
        style.configure("Live.Banner.TLabel", background="#D32F2F", foreground="white")
        style.configure("Stopping.Banner.TLabel", background="#FF9800", foreground="black")
        style.configure("Countdown.Banner.TLabel", background="#FFC107", foreground="black")
        style.configure("Ready.Banner.TLabel", background="#4CAF50", foreground="white")
        style.configure("Ended.Banner.TLabel", background="#2196F3", foreground="white")  # Blue for ended
        style.configure("Error.Banner.TLabel", background="#F44336", foreground="white")

        style.configure("Green.TButton", background="#4CAF50", foreground="white", font=("Segoe UI", 11, "bold"))
        style.configure("Red.TButton", background="#F44336", foreground="white", font=("Segoe UI", 11, "bold"))
        style.configure("RecOff.TButton", background="#FF9800", foreground="white", font=("Segoe UI", 11, "bold"))
        style.configure("RecOn.TButton", background="#D32F2F", foreground="white", font=("Segoe UI", 11, "bold"))

        style.map("Green.TButton", background=[('active', '#388E3C')])
        style.map("Red.TButton", background=[('active', '#C62828')])
        style.map("RecOff.TButton", background=[('active', '#EF6C00')])
        style.map("RecOn.TButton", background=[('active', '#B71C1C')])

        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        self.banner_var = tk.StringVar(value="INITIALIZING — Launch OBS/Proclaim as needed")
        self.banner = ttk.Label(main, textvariable=self.banner_var, style="Ready.Banner.TLabel")
        self.banner.pack(fill="x", pady=(0, 12))

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

        controls = ttk.Frame(main)
        controls.pack(fill="x", pady=(12, 0))
        ttk.Button(controls, text="Start Stream", style="Green.TButton",
                   command=lambda: self._ui_fire("start")).grid(row=0, column=0, padx=8, pady=4)
        ttk.Button(controls, text="Stop Stream", style="Red.TButton",
                   command=lambda: self._ui_fire("stop")).grid(row=0, column=1, padx=8, pady=4)
        self.rec_btn = ttk.Button(controls, text="REC Toggle", style="RecOff.TButton",
                                  command=lambda: self._ui_fire("rec"))
        self.rec_btn.grid(row=0, column=2, padx=8, pady=4)

        presets_frame = ttk.LabelFrame(main)
        presets_frame.pack(fill="x", pady=(15, 0))

        presets_label = ttk.Label(presets_frame, text="Camera Presets", font=("Segoe UI", 12, "bold"), anchor="center")
        presets_label.grid(row=0, column=0, columnspan=2, pady=(4, 8))

        for i in range(1, 11):
            label = self.cfg.PRESET_LABELS.get(i, f"Preset {i}")
            ttk.Button(presets_frame, text=f"{i}: {label}", width=24,
                       command=lambda p=i: self._ui_preset(p)).grid(
                row=((i-1)//2) + 1, column=(i-1)%2, padx=10, pady=4, sticky="ew")
        presets_frame.columnconfigure(0, weight=1)
        presets_frame.columnconfigure(1, weight=1)

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
                    self.rec_btn.configure(style="RecOn.TButton" if state["rec_on"] else "RecOff.TButton")
                if "banner_text" in state:
                    self.banner_var.set(state["banner_text"])
                if "banner_style" in state:
                    self.banner.configure(style=state["banner_style"])
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

    def _post(self, msg: str):
        ts = dt.datetime.now().strftime("%H:%M:%S")
        full = f"[{ts}] {msg}\n"

        def _append():
            self.log_text.config(state="normal")
            self.log_text.insert("end", full)
            self.log_text.see("end")
            self.log_text.config(state="disabled")

        self._ui_action(_append)

    def _camera_source_status_line(self) -> str:
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
                self._post("WARN: camera feed not in OBS")

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
            return "🔴 LIVE — NOW STREAMING", "Live.Banner.TLabel"

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

    def _ui_fire(self, action: str):
        if action == "start":
            self._start_stream_flow("HUD")
        elif action == "stop":
            self._request_stop("HUD")
        elif action == "rec":
            self._toggle_record("HUD")

    def _ui_preset(self, preset_num: int):
        self._handle_preset(preset_num, "HUD")

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

        ok, msg = self.obs.start_stream()
        if ok:
            self._post(f"{source}: stream start sent")
        else:
            self._pending_stream_start = True
            self._pending_start_reason = source
            self._post(f"{source}: start failed ({msg})")

    def _request_stop(self, source: str):
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

    async def loop(self):
        startup_grace = 20.0
        while self.running:
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

            if streaming:
                if self.stream_stable_since is None:
                    self.stream_stable_since = time.time()
                    self.minimized_this_stream = False
                    self._post("Stream started — enjoy the service!")
                if self.minimized:
                    self._ui_action(lambda: (self.root.deiconify(), self.root.lift()))
                    self.minimized = False
            else:
                if self.stream_stable_since is not None:
                    self._post("Stream stopped")
                    self.stream_ended_at = time.time()  # For banner
                    self.minimized_this_stream = False
                self.stream_stable_since = None

            if (self.cfg.AUTO_MINIMIZE_ENABLED and streaming and self.stream_stable_since and
                not self.minimized_this_stream and not self.minimized and
                (time.time() - self.stream_stable_since) >= self.cfg.AUTO_MINIMIZE_AFTER_SECONDS):
                self._ui_action(lambda: self.root.iconify())
                self.minimized = True
                self.minimized_this_stream = True
                self._post("Stable — minimizing HUD")

            # Restore only on real issues while streaming
            cam_issue = (streaming and not self.cfg.HOME_TEST_MODE and self.cam_state == "AWAKE" and
                         self._cam_src_last_result and (not self._cam_src_last_result.get("ok") or
                                                        self._cam_src_last_result.get("visible") is False))
            unexpected_stop = (self._was_streaming and (not streaming) and (not self._stop_pending))
            if self.minimized and (unexpected_stop or err or cam_issue):
                def _restore():
                    self.root.deiconify()
                    self.root.lift()
                    self.root.attributes('-topmost', True)
                    self.root.after(8000, lambda: self.root.attributes('-topmost', False))
                self._ui_action(_restore)
                self.minimized = False
                self._post("Issue detected — restoring HUD")

            self._was_streaming = streaming

            await asyncio.sleep(0.25)

    def _runner(self):
        try:
            asyncio.run(self.loop())
        except Exception as e:
            self._post(f"Loop crashed: {e}")

    def _on_close(self):
        self.running = False
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App(CFG).run()