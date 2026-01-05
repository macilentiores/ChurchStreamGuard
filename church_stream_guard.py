"""
church_stream_guard.py

OBS (Open Broadcaster Software) + Proclaim MIDI (Musical Instrument Digital Interface) guard app
for:
- timed start / MIDI backup start
- stop stream + delay + camera power off
- record on/off
- camera PTZ preset recall via VISCA-over-UDP (Video System Control Architecture)

New in this version:
- MIDI notes 70–79 recall presets 1–10
- Preset labels (pulpit/panorama/etc) shown in HUD + optional HUD buttons for quick testing
"""

from __future__ import annotations

import asyncio
import datetime as dt
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

try:
    import mido  # MIDI
except Exception:
    mido = None

try:
    # pip install obsws-python
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
    HOME_TEST_MODE: bool = False
    # If True, the app will NOT require Proclaim "On Air" to accept MIDI cues
    # (useful at home; keep False at church if you want a safety gate)
    REQUIRE_PROCLAIM_ONAIR: bool = False

    # ---- OBS WebSocket ----
    OBS_HOST: str = "127.0.0.1"
    OBS_PORT: int = 4455
    OBS_PASSWORD: str = ""  # leave blank if auth is OFF in OBS WebSocket

    # ---- YouTube/OBS behavior ----
    STOP_DELAY_SECONDS: int = 30  # after STOP cue, wait then actually stop stream
    AUTO_RECONNECT_OBS: bool = True

    # ---- Camera (FoMaKo) VISCA over UDP ----
    CAMERA_IP: str = "192.168.88.20"
    CAMERA_VISCA_PORT: int = 1259

    # If your camera requires a VISCA-over-IP header wrapper, set True.
    # FoMaKo + your CameraDirectorII usage strongly suggests "raw VISCA payload" works, so default False.
    VISCA_USE_OVERIP_HEADER: bool = False

    # VISCA address byte:
    # VISCA uses 8x where x is camera address (1..7) for serial; many IP cams accept x=1 (0x81).
    VISCA_ADDR: int = 0x81

    # Preset numbering base:
    # PTZOptics command list defines pp as memory number 0..127. Many cams map "Preset 1" => pp=0.
    # If your camera maps "Preset 1" => pp=1, set this to 1.
    PRESET_NUMBER_BASE: int = 0

    # When a preset MIDI note arrives and the camera is "asleep", should we auto-wake first?
    CAMERA_AUTO_WAKE_ON_PRESET: bool = True

    CAMERA_BOOT_SECONDS: int = 20  # wait after camera power-on before starting stream

    # ---- MIDI ----
    MIDI_INPUT_PORT_SUBSTRING: str = "loopMIDI"  # partial match for port name
    MIDI_CHANNEL_1_BASED: int = 1  # Proclaim commonly uses channel 1

    # MIDI notes:
    NOTE_START_STREAM: int = 60
    NOTE_STOP_STREAM: int = 61
    NOTE_REC_TOGGLE: int = 62

    # Presets: notes 70..79 => presets 1..10
    NOTE_PRESET_FIRST: int = 70
    NOTE_PRESET_LAST: int = 79

    # ---- Optional timer start (primary start) ----
    USE_TIMER_START: bool = True
    # Local time (America/Regina) to start stream on Sundays. Example: "09:45"
    TIMER_START_HHMM: str = "09:45"
    TIMER_WEEKDAY: int = 6  # Monday=0 ... Sunday=6

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
# VISCA controller
# =========================

class ViscaController:
    def __init__(self, ip: str, port: int, addr: int, use_header: bool):
        self.ip = ip
        self.port = port
        self.addr = addr
        self.use_header = use_header
        self._seq = 1  # for over-IP header (if used)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(0.25)

    def _wrap_overip(self, payload: bytes) -> bytes:
        # Common VISCA-over-IP wrapper: 01 00 00 <len> <seq32> + payload
        ln = len(payload)
        hdr = bytes([0x01, 0x00, (ln >> 8) & 0xFF, ln & 0xFF]) + int(self._seq).to_bytes(4, "big")
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        return hdr + payload

    def send(self, payload: bytes) -> None:
        pkt = self._wrap_overip(payload) if self.use_header else payload
        self._sock.sendto(pkt, (self.ip, self.port))

    def cam_power_on(self) -> None:
        # 8x 01 04 00 02 FF  :contentReference[oaicite:2]{index=2}
        self.send(bytes([self.addr, 0x01, 0x04, 0x00, 0x02, 0xFF]))

    def cam_power_off(self) -> None:
        # 8x 01 04 00 03 FF  :contentReference[oaicite:3]{index=3}
        self.send(bytes([self.addr, 0x01, 0x04, 0x00, 0x03, 0xFF]))

    def recall_preset(self, preset_1_based: int, preset_number_base: int) -> Tuple[int, int]:
        """
        Returns (pp, preset_1_based) where pp is the VISCA memory number actually sent.
        Recall: 8x 01 04 3F 02 pp FF  :contentReference[oaicite:4]{index=4}
        """
        if not (1 <= preset_1_based <= 10):
            raise ValueError("preset must be 1..10")

        pp = (preset_1_based - 1 + preset_number_base)  # base=0 means preset1->0
        if not (0 <= pp <= 127):
            raise ValueError("computed pp out of range 0..127")

        self.send(bytes([self.addr, 0x01, 0x04, 0x3F, 0x02, pp & 0x7F, 0xFF]))
        return pp, preset_1_based


# =========================
# OBS controller
# =========================

class ObsController:
    def __init__(self, host: str, port: int, password: str):
        self.host = host
        self.port = port
        self.password = password
        self.client: Optional[ReqClient] = None

    def connect(self) -> bool:
        if ReqClient is None:
            return False
        try:
            self.client = ReqClient(host=self.host, port=self.port, password=self.password)
            # lightweight call to confirm
            _ = self.client.get_version()
            return True
        except Exception:
            self.client = None
            return False

    def is_connected(self) -> bool:
        return self.client is not None

    def start_stream(self) -> None:
        if not self.client:
            raise RuntimeError("OBS not connected")
        self.client.start_stream()

    def stop_stream(self) -> None:
        if not self.client:
            raise RuntimeError("OBS not connected")
        self.client.stop_stream()

    def start_record(self) -> None:
        if not self.client:
            raise RuntimeError("OBS not connected")
        self.client.start_record()

    def stop_record(self) -> None:
        if not self.client:
            raise RuntimeError("OBS not connected")
        self.client.stop_record()

    def toggle_record(self) -> str:
        if not self.client:
            raise RuntimeError("OBS not connected")
        status = self.client.get_record_status()
        if status.output_active:
            self.client.stop_record()
            return "REC stop"
        else:
            self.client.start_record()
            return "REC start"

    def get_status_text(self) -> str:
        if not self.client:
            return "OBS offline"
        try:
            s = self.client.get_stream_status()
            r = self.client.get_record_status()
            parts = []
            parts.append("STREAM ON" if s.output_active else "stream off")
            parts.append("REC ON" if r.output_active else "rec off")
            return " | ".join(parts)
        except Exception:
            return "OBS error"


# =========================
# MIDI listener
# =========================

class MidiListener:
    def __init__(self, port_substring: str, channel_1_based: int):
        self.port_substring = port_substring.lower()
        self.channel_0_based = max(0, channel_1_based - 1)
        self.inport = None

    def available_ports(self):
        if mido is None:
            return []
        try:
            return list(mido.get_input_names())
        except Exception:
            return []

    def connect(self) -> bool:
        if mido is None:
            return False
        ports = self.available_ports()
        match = None
        for p in ports:
            if self.port_substring in p.lower():
                match = p
                break
        if not match:
            return False
        try:
            self.inport = mido.open_input(match)
            return True
        except Exception:
            self.inport = None
            return False

    def poll(self):
        if not self.inport:
            return []
        msgs = []
        try:
            for msg in self.inport.iter_pending():
                msgs.append(msg)
        except Exception:
            pass
        return msgs

    def is_note_on(self, msg, note: int) -> bool:
        try:
            return (msg.type == "note_on"
                    and msg.channel == self.channel_0_based
                    and msg.note == note
                    and msg.velocity > 0)
        except Exception:
            return False


# =========================
# App state + HUD
# =========================

class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.obs = ObsController(cfg.OBS_HOST, cfg.OBS_PORT, cfg.OBS_PASSWORD)
        self.midi = MidiListener(cfg.MIDI_INPUT_PORT_SUBSTRING, cfg.MIDI_CHANNEL_1_BASED)
        self.visca = ViscaController(cfg.CAMERA_IP, cfg.CAMERA_VISCA_PORT, cfg.VISCA_ADDR, cfg.VISCA_USE_OVERIP_HEADER)

        self.running = True

        self.camera_state = "SLEEP"  # SLEEP | WAKING | AWAKE
        self.camera_wake_started_at: Optional[float] = None

        self.last_post = ""
        self.last_midi = ""
        self.last_cam = ""
        self.last_obs = ""

        self.stop_pending_until: Optional[float] = None

        # Used to de-bounce looping “start” notes from Proclaim preservice loop
        self.stream_start_armed = True
        self.last_start_attempt_at: float = 0.0

        # Timer-based start
        self.timer_fired_for_date: Optional[dt.date] = None

        # Tkinter HUD
        self.root = tk.Tk()
        self.root.title("Church Stream Guard")
        self.root.resizable(False, False)

        self._build_ui()

    def _build_ui(self):
        frm = ttk.Frame(self.root, padding=10)
        frm.grid(row=0, column=0)

        self.var_mode = tk.StringVar(value="HOME TEST" if self.cfg.HOME_TEST_MODE else "CHURCH MODE")
        self.var_status = tk.StringVar(value="starting…")
        self.var_obs = tk.StringVar(value="OBS: ?")
        self.var_midi = tk.StringVar(value="MIDI: ?")
        self.var_cam = tk.StringVar(value="CAM: ?")
        self.var_last = tk.StringVar(value="")

        ttk.Label(frm, textvariable=self.var_mode, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w")

        ttk.Label(frm, textvariable=self.var_status).grid(row=1, column=0, sticky="w", pady=(4, 6))

        ttk.Label(frm, textvariable=self.var_obs).grid(row=2, column=0, sticky="w")
        ttk.Label(frm, textvariable=self.var_midi).grid(row=3, column=0, sticky="w")
        ttk.Label(frm, textvariable=self.var_cam).grid(row=4, column=0, sticky="w")

        ttk.Separator(frm).grid(row=5, column=0, sticky="ew", pady=8)

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, sticky="ew")

        self.btn_start = ttk.Button(btns, text="Start Stream", command=lambda: self._ui_fire("start_stream"))
        self.btn_stop = ttk.Button(btns, text="Stop Stream", command=lambda: self._ui_fire("stop_stream"))
        self.btn_rec = ttk.Button(btns, text="REC Toggle", command=lambda: self._ui_fire("rec_toggle"))

        self.btn_start.grid(row=0, column=0, padx=4)
        self.btn_stop.grid(row=0, column=1, padx=4)
        self.btn_rec.grid(row=0, column=2, padx=4)

        # Preset buttons (for confidence testing without MIDI)
        presets = ttk.LabelFrame(frm, text="Camera Presets (test)")
        presets.grid(row=7, column=0, sticky="ew", pady=(8, 0))

        # show the 8 you named as buttons, and keep 9/10 out to reduce clutter
        row = 0
        col = 0
        for p in range(1, 9):
            label = self.cfg.PRESET_LABELS.get(p, f"Preset {p}")
            b = ttk.Button(presets, text=f"{p}: {label}", width=18,
                           command=lambda pp=p: self._fire_preset(pp, source="HUD"))
            b.grid(row=row, column=col, padx=4, pady=4, sticky="w")
            col += 1
            if col >= 2:
                col = 0
                row += 1

        ttk.Separator(frm).grid(row=8, column=0, sticky="ew", pady=8)
        ttk.Label(frm, text="Last:").grid(row=9, column=0, sticky="w")
        ttk.Label(frm, textvariable=self.var_last, wraplength=360).grid(row=10, column=0, sticky="w")

        # Ensure close exits cleanly
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        self.running = False
        try:
            self.root.destroy()
        except Exception:
            pass

    def post(self, msg: str):
        self.last_post = msg
        self.var_last.set(msg)

    def _ui_fire(self, action: str):
        # Manual fallback is ALWAYS allowed
        if action == "start_stream":
            self.post("HUD: Start Stream pressed")
            self._start_stream_flow(source="HUD")
        elif action == "stop_stream":
            self.post("HUD: Stop Stream pressed")
            self._stop_stream_flow(source="HUD")
        elif action == "rec_toggle":
            self.post("HUD: REC Toggle pressed")
            self._rec_toggle_flow(source="HUD")

    def _set_cam_state(self, state: str):
        self.camera_state = state

    def _wake_camera(self, source: str):
        if self.cfg.HOME_TEST_MODE:
            self._set_cam_state("AWAKE")
            self.last_cam = f"{source}: camera wake (simulated)"
            self.post(self.last_cam)
            return

        if self.camera_state in ("WAKING", "AWAKE"):
            return

        try:
            self.visca.cam_power_on()
            self._set_cam_state("WAKING")
            self.camera_wake_started_at = time.time()
            self.last_cam = f"{source}: camera power ON (VISCA) → wait {self.cfg.CAMERA_BOOT_SECONDS}s"
            self.post(self.last_cam)
        except Exception as e:
            self.last_cam = f"{source}: camera power ON failed: {e}"
            self.post(self.last_cam)

    def _sleep_camera(self, source: str):
        if self.cfg.HOME_TEST_MODE:
            self._set_cam_state("SLEEP")
            self.last_cam = f"{source}: camera sleep (simulated)"
            self.post(self.last_cam)
            return

        try:
            self.visca.cam_power_off()
            self._set_cam_state("SLEEP")
            self.camera_wake_started_at = None
            self.last_cam = f"{source}: camera power OFF (VISCA)"
            self.post(self.last_cam)
        except Exception as e:
            self.last_cam = f"{source}: camera power OFF failed: {e}"
            self.post(self.last_cam)

    def _camera_is_ready(self) -> bool:
        if self.cfg.HOME_TEST_MODE:
            return True
        if self.camera_state == "AWAKE":
            return True
        if self.camera_state == "WAKING" and self.camera_wake_started_at is not None:
            if (time.time() - self.camera_wake_started_at) >= self.cfg.CAMERA_BOOT_SECONDS:
                self._set_cam_state("AWAKE")
                return True
        return False

    def _fire_preset(self, preset_1_based: int, source: str):
        label = self.cfg.PRESET_LABELS.get(preset_1_based, f"Preset {preset_1_based}")

        if self.camera_state == "SLEEP" and self.cfg.CAMERA_AUTO_WAKE_ON_PRESET:
            self._wake_camera(source=f"{source}/preset {preset_1_based}")
            # don’t block; preset will send once ready in loop
            self.last_cam = f"{source}: queued preset {preset_1_based} ({label}) until camera ready"
            self.post(self.last_cam)
            # store a one-shot “pending preset”
            self._pending_preset = preset_1_based
            return

        if not self._camera_is_ready():
            self.last_cam = f"{source}: camera not ready; ignoring preset {preset_1_based} ({label})"
            self.post(self.last_cam)
            return

        if self.cfg.HOME_TEST_MODE:
            self.last_cam = f"{source}: preset {preset_1_based} ({label}) (simulated)"
            self.post(self.last_cam)
            return

        try:
            pp, p = self.visca.recall_preset(preset_1_based, self.cfg.PRESET_NUMBER_BASE)
            self.last_cam = f"{source}: recall preset {p} ({label}) → VISCA pp={pp}"
            self.post(self.last_cam)
        except Exception as e:
            self.last_cam = f"{source}: preset {preset_1_based} failed: {e}"
            self.post(self.last_cam)

    def _start_stream_flow(self, source: str):
        # Debounce: avoid Proclaim loop spam
        now = time.time()
        if (now - self.last_start_attempt_at) < 5.0:
            self.post(f"{source}: start ignored (debounce)")
            return
        self.last_start_attempt_at = now

        # Ensure camera wake begins first
        if self.camera_state == "SLEEP":
            self._wake_camera(source=f"{source}/start")

        # If camera isn’t ready yet, we’ll start once ready (main loop handles it)
        if not self._camera_is_ready():
            self.post(f"{source}: waiting for camera readiness before starting stream…")
            self._pending_stream_start = True
            return

        # OBS start
        if not self.obs.is_connected():
            self.post(f"{source}: OBS not connected; cannot start stream")
            return

        try:
            self.obs.start_stream()
            self.last_obs = f"{source}: OBS start_stream() sent"
            self.post(self.last_obs)
        except Exception as e:
            self.last_obs = f"{source}: OBS start_stream failed: {e}"
            self.post(self.last_obs)

    def _stop_stream_flow(self, source: str):
        # Set a pending stop with delay
        self.stop_pending_until = time.time() + float(self.cfg.STOP_DELAY_SECONDS)
        self.post(f"{source}: STOP requested → will stop in {self.cfg.STOP_DELAY_SECONDS}s")

    def _do_stop_now(self, source: str):
        if not self.obs.is_connected():
            self.post(f"{source}: OBS not connected; cannot stop stream")
            return
        try:
            self.obs.stop_stream()
            self.last_obs = f"{source}: OBS stop_stream() sent"
            self.post(self.last_obs)
        except Exception as e:
            self.last_obs = f"{source}: OBS stop_stream failed: {e}"
            self.post(self.last_obs)

        # Camera should power off after stream stop (your “must not crash all week” requirement)
        self._sleep_camera(source=f"{source}/after stop")

    def _rec_toggle_flow(self, source: str):
        if not self.obs.is_connected():
            self.post(f"{source}: OBS not connected; cannot toggle REC")
            return
        try:
            r = self.obs.toggle_record()
            self.post(f"{source}: {r}")
        except Exception as e:
            self.post(f"{source}: REC toggle failed: {e}")

    def _timer_should_fire(self) -> bool:
        if not self.cfg.USE_TIMER_START:
            return False

        now = dt.datetime.now()
        if now.weekday() != self.cfg.TIMER_WEEKDAY:
            return False

        try:
            hh, mm = self.cfg.TIMER_START_HHMM.split(":")
            target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except Exception:
            return False

        # fire if time has passed and not already fired today
        if now >= target:
            if self.timer_fired_for_date == now.date():
                return False
            self.timer_fired_for_date = now.date()
            return True
        return False

    def _update_ui(self):
        self.var_obs.set(f"OBS: {self.obs.get_status_text()}")
        ports = self.midi.available_ports()
        midi_ok = "connected" if self.midi.inport else "waiting"
        self.var_midi.set(f"MIDI: {midi_ok} ({len(ports)} ports seen)")
        self.var_cam.set(f"CAM: {self.camera_state}")
        self.var_status.set(self.last_post)

    async def loop(self):
        self._pending_stream_start = False
        self._pending_preset = None

        # Try connect OBS and MIDI at start
        if self.cfg.AUTO_RECONNECT_OBS:
            self.obs.connect()
        self.midi.connect()

        while self.running:
            # Keep OBS connected
            if self.cfg.AUTO_RECONNECT_OBS and not self.obs.is_connected():
                self.obs.connect()

            # Keep MIDI connected
            if not self.midi.inport:
                self.midi.connect()

            # Camera readiness transitions
            _ = self._camera_is_ready()

            # If a preset was queued while waking, fire it once ready
            if self._pending_preset is not None and self._camera_is_ready():
                p = self._pending_preset
                self._pending_preset = None
                self._fire_preset(p, source="queued")

            # Timer-based start (primary)
            if self._timer_should_fire():
                self.post("TIMER: start time reached → start stream flow")
                self._start_stream_flow(source="TIMER")

            # MIDI polling
            for msg in self.midi.poll():
                self.last_midi = str(msg)

                # Only respond to channel + NOTE_ON
                if self.midi.is_note_on(msg, self.cfg.NOTE_START_STREAM):
                    self.post(f"MIDI: NOTE {self.cfg.NOTE_START_STREAM} → start stream")
                    self._start_stream_flow(source="MIDI")

                elif self.midi.is_note_on(msg, self.cfg.NOTE_STOP_STREAM):
                    self.post(f"MIDI: NOTE {self.cfg.NOTE_STOP_STREAM} → stop stream")
                    self._stop_stream_flow(source="MIDI")

                elif self.midi.is_note_on(msg, self.cfg.NOTE_REC_TOGGLE):
                    self.post(f"MIDI: NOTE {self.cfg.NOTE_REC_TOGGLE} → REC toggle")
                    self._rec_toggle_flow(source="MIDI")

                else:
                    # Preset range 70..79 => presets 1..10
                    try:
                        n = msg.note
                        if (msg.type == "note_on" and msg.velocity > 0
                                and msg.channel == (self.cfg.MIDI_CHANNEL_1_BASED - 1)
                                and self.cfg.NOTE_PRESET_FIRST <= n <= self.cfg.NOTE_PRESET_LAST):
                            preset_1_based = (n - self.cfg.NOTE_PRESET_FIRST) + 1
                            label = self.cfg.PRESET_LABELS.get(preset_1_based, f"Preset {preset_1_based}")
                            self.post(f"MIDI: NOTE {n} → preset {preset_1_based} ({label})")
                            self._fire_preset(preset_1_based, source="MIDI")
                    except Exception:
                        pass

            # Handle delayed stop
            if self.stop_pending_until is not None:
                remaining = self.stop_pending_until - time.time()
                if remaining <= 0:
                    self.stop_pending_until = None
                    self._do_stop_now(source="STOP-DELAY")
                else:
                    # light status update
                    self.var_status.set(f"Stop pending… {int(remaining)}s")

            # Update UI
            self._update_ui()

            await asyncio.sleep(0.1)

    def run(self):
        # Run asyncio loop in background thread so Tkinter stays responsive
        def runner():
            asyncio.run(self.loop())

        t = threading.Thread(target=runner, daemon=True)
        t.start()

        self.root.mainloop()
        self.running = False


if __name__ == "__main__":
    App(CFG).run()
