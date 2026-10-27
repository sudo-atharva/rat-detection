"""Rat heat-bed experiment GUI.

Nano streams "<tempC>,<heater>,<accelPeak>" lines; we send "S<setpoint>" back
every second (also serves as heartbeat - Nano cuts heater if PC goes silent).
Camera is shown live and recorded during a run, with a CSV of sensor data and
a per-frame timestamp CSV (frames.csv) so post-processing can sync the two.

live_tracking=False (rat_record.py): no tracking during the run, just video +
accel graph; tracking and overlay are done by postprocess.py after the run.
"""
import csv
import queue
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
import serial
import sv_ttk
from serial.tools import list_ports

from postprocess import GRAPH_S, draw_overlay, process
from rat_track import Tracker, draw

try:
    import winsound

    def beep(freq=2000, ms=200):
        threading.Thread(target=winsound.Beep, args=(freq, ms), daemon=True).start()
except ImportError:  # non-Windows
    def beep(freq=2000, ms=200):
        print("\a", end="", flush=True)

RUNS_DIR = Path(__file__).parent / "runs"
JUMP_REFRACTORY_S = 0.5  # one jump can spike accel for several samples
# ponytail: a jump's take-off push may also count as a tap; raise tap threshold if taps look inflated
TAP_REFRACTORY_S = 0.2
MOVE_BEEP_GAP_S = 1.0  # min seconds between "rat started moving" beeps, so walking doesn't beep nonstop
VIDEO_W = 760  # display width; height follows the camera's aspect ratio
NO_PORT = "None (video only)"
# USB vendor IDs of Arduino boards and the USB-serial chips clones use
ARDUINO_VIDS = {0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4}
GREEN, RED, GREY = "#4caf50", "#ef5350", "#9e9e9e"


def parse_line(line):
    """'36.50,1,0.123' -> (36.5, 1, 0.123), or None if garbled."""
    try:
        t, h, a = line.strip().split(",")
        return float(t), int(h), float(a)
    except ValueError:
        return None


def is_event(peak_g, threshold_g, now, last, refractory_s=JUMP_REFRACTORY_S):
    return peak_g >= threshold_g and now - last >= refractory_s


def move_started(moving, was_moving, now, last_beep):
    return moving and not was_moving and now - last_beep >= MOVE_BEEP_GAP_S


def find_ports():
    """['COM5 - USB-SERIAL CH340', ...], Arduino-looking ports first."""
    ports = sorted(list_ports.comports(), key=lambda p: (p.vid not in ARDUINO_VIDS, p.device))
    return [f"{p.device} - {p.description}" for p in ports]


def find_cameras(max_index=5):
    """['0 - 1280x720', ...] for each camera index that delivers a frame."""
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened() and cap.read()[0]:
            found.append(f"{i} - {int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}")
        cap.release()
    return found


class App:
    def __init__(self, root, live_tracking=True):
        self.root = root
        self.live_tracking = live_tracking
        root.title("Rat Heat Bed - live tracking" if live_tracking else "Rat Heat Bed - record, process after")
        sv_ttk.set_theme("dark")
        self.ser = None
        self.cap = None
        self.lines = queue.Queue()
        self.run = None  # dict while a run is active
        self.last_send = 0
        self.last_jump = 0
        self.last_tap = 0
        self.latest = (float("nan"), 0, 0.0)
        self.accel_hist = deque(maxlen=GRAPH_S * 20)  # (monotonic t, peak g), Nano sends 10/s
        self.events = deque(maxlen=50)  # (monotonic t, 'tap'|'jump')
        self.tracker = Tracker()
        self.track = None  # latest tracker result
        self.raw = None  # latest camera frame without overlays
        self.bg_frame = None  # empty-cage shot, saved with each run for post-processing
        self.last_frame_t = 0
        self.frame_dt = 0  # min seconds between reads; set for video files
        self.proc = None  # post-processing state while it runs
        self.last_port_scan = 0
        self.was_moving = False
        self.last_move_beep = 0
        self.move_beep = tk.BooleanVar(value=True)

        side = ttk.Frame(root, padding=12)
        side.pack(side="left", fill="y")
        main = ttk.Frame(root, padding=(0, 12, 12, 12))
        main.pack(side="left", fill="both", expand=True)

        conn = ttk.LabelFrame(side, text="Connection", padding=10)
        conn.pack(fill="x")
        ttk.Label(conn, text="Nano port").grid(row=0, column=0, sticky="w")
        self.port = ttk.Combobox(conn, width=28, state="readonly")
        self.port.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        ttk.Button(conn, text="⟳", width=3, command=self.scan_ports).grid(row=1, column=1, padx=(4, 0), pady=(2, 8))
        ttk.Label(conn, text="Camera index or video file").grid(row=2, column=0, sticky="w")
        self.cam = ttk.Combobox(conn, width=28)
        self.cam.grid(row=3, column=0, sticky="ew", pady=2)
        ttk.Button(conn, text="⟳", width=3, command=self.scan_cameras).grid(row=3, column=1, padx=(4, 0))
        ttk.Button(conn, text="Browse video…", command=self.browse).grid(row=4, column=0, sticky="w", pady=(2, 8))
        self.connect_btn = ttk.Button(conn, text="Connect", style="Accent.TButton", command=self.connect)
        self.connect_btn.grid(row=5, column=0, columnspan=2, sticky="ew")

        cfg = ttk.LabelFrame(side, text="Run settings", padding=10)
        cfg.pack(fill="x", pady=12)
        self.setpoint = self._entry(cfg, 0, "Bed setpoint (°C)", "37")
        self.duration = self._entry(cfg, 1, "Duration (s)", "300")
        self.tap_threshold = self._entry(cfg, 2, "Tap threshold (g)", "0.3")
        self.threshold = self._entry(cfg, 3, "Jump threshold (g)", "0.8")
        self.interval = self._entry(cfg, 4, "Beep every (s), 0 = off", "60")
        ttk.Checkbutton(cfg, text="Beep when rat moves", variable=self.move_beep,
                        style="Switch.TCheckbutton").grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.start_btn = ttk.Button(cfg, text="▶  Start run", style="Accent.TButton", command=self.toggle_run,
                                    state="disabled")
        self.start_btn.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 4))
        self.bg_btn = ttk.Button(cfg, text="Set background (empty cage)", command=self.set_background,
                                 state="disabled")
        self.bg_btn.grid(row=7, column=0, columnspan=2, sticky="ew")

        live = ttk.LabelFrame(main, text="Live", padding=10)
        live.pack(fill="x", pady=(0, 10))
        self.cards = {}
        names = ["Temp", "Heater", "Accel", "Rat", "Elapsed", "Time left", "Taps", "Jumps"]
        for i, name in enumerate(names):
            box = ttk.Frame(live, padding=(0, 4, 16, 4))
            box.grid(row=i // 4, column=i % 4, sticky="w")
            ttk.Label(box, text=name, foreground=GREY).pack(anchor="w")
            self.cards[name] = ttk.Label(box, text="–", font=("Segoe UI", 18, "bold"))
            self.cards[name].pack(anchor="w")

        self.video = ttk.Label(main, text="No camera connected", anchor="center", width=60)
        self.video.pack(fill="both", expand=True)
        self.status = ttk.Label(main, text="Pick a port and camera, then Connect.", foreground=GREY, padding=(0, 8, 0, 0))
        self.status.pack(fill="x")

        root.protocol("WM_DELETE_WINDOW", self.close)
        self.scan_ports()
        self.scan_cameras()
        self.tick()

    def _entry(self, parent, row, label, default):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        e = ttk.Entry(parent, width=8)
        e.insert(0, default)
        e.grid(row=row, column=1, sticky="e", pady=3, padx=(8, 0))
        return e

    def _float(self, entry, fallback=0.0):
        try:
            return float(entry.get())
        except ValueError:
            return fallback

    def scan_ports(self):
        ports = find_ports()
        if list(self.port["values"]) == ports + [NO_PORT]:
            return
        self.port["values"] = ports + [NO_PORT]
        if self.port.get() not in self.port["values"] or self.port.get() == NO_PORT:
            self.port.set(ports[0] if ports else NO_PORT)  # auto-pick Arduino when it gets plugged in

    def scan_cameras(self):
        self.status["text"] = "Looking for cameras…"

        def work():
            cams = find_cameras()
            self.root.after(0, lambda: self._cameras_found(cams))
        threading.Thread(target=work, daemon=True).start()

    def _cameras_found(self, cams):
        self.cam["values"] = cams
        if not self.cam.get() and cams:
            self.cam.set(cams[0])
        self.status["text"] = f"{len(cams)} camera(s) found." if cams else "No camera found - plug one in and press ⟳, or Browse a video."

    def browse(self):
        path = filedialog.askopenfilename(filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv"), ("All files", "*.*")])
        if path:
            self.cam.set(path)

    def connect(self):
        port = self.port.get()
        if port and port != NO_PORT:
            try:
                self.ser = serial.Serial(port.split(" - ")[0], 115200, timeout=1)
            except serial.SerialException as e:
                messagebox.showerror("Serial", str(e))
                return
        src = self.cam.get().strip()
        index = src.split(" - ")[0]
        if index.isdigit():
            self.cap = cv2.VideoCapture(int(index), cv2.CAP_DSHOW)
        else:  # video file: pace reads to its fps so it plays at real speed
            self.cap = cv2.VideoCapture(src)
            self.frame_dt = 1 / (self.cap.get(cv2.CAP_PROP_FPS) or 30)
        if not self.cap.isOpened():
            messagebox.showerror("Camera", f"Cannot open {src or 'camera'}")
            self.cap = None
            if self.ser:
                self.ser.close()
                self.ser = None
            return
        if self.ser:
            threading.Thread(target=self.read_serial, daemon=True).start()
        self.connect_btn.configure(text="Connected", state="disabled")
        for w in (self.port, self.cam):
            w["state"] = "disabled"
        self.start_btn["state"] = "normal"
        self.bg_btn["state"] = "normal"
        self.status["text"] = f"Connected: {port.split(' - ')[0] if self.ser else 'no Nano'}, camera {src}"

    def set_background(self):
        if self.raw is not None:
            self.bg_frame = self.raw.copy()
            self.tracker.set_background(self.raw)
            self.status["text"] = "Background saved."

    def read_serial(self):
        while self.ser and self.ser.is_open:
            try:
                line = self.ser.readline().decode(errors="ignore")
            except serial.SerialException:
                break
            if parsed := parse_line(line):
                self.lines.put(parsed)

    def toggle_run(self):
        if self.run:
            self.stop_run("stopped")
            return
        folder = RUNS_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
        folder.mkdir(parents=True)
        if self.bg_frame is not None:
            cv2.imwrite(str(folder / "background.png"), self.bg_frame)
        f = open(folder / "data.csv", "w", newline="")
        w = csv.writer(f)
        w.writerow(["elapsed_s", "temp_c", "setpoint_c", "heater", "accel_peak_g", "jump", "jump_count",
                    "tap", "tap_count", "rat_x", "rat_y", "speed_width_per_s", "behavior"])
        ff = open(folder / "frames.csv", "w", newline="")
        fw = csv.writer(ff)
        fw.writerow(["frame", "elapsed_s"])
        self.run = {"folder": folder, "file": f, "csv": w, "frames_file": ff, "frames": fw, "n": 0,
                    "t0": time.monotonic(), "dur": self._float(self.duration, 300), "jumps": 0, "taps": 0,
                    "video": None, "behavior_s": {}, "interval": self._float(self.interval, 0)}
        self.run["next_beep"] = self.run["interval"]
        self.start_btn["text"] = "■  Stop run"
        self.status["text"] = f"Recording to {folder}"
        beep(1000, 150)

    def stop_run(self, reason):
        r, self.run = self.run, None
        r["file"].close()
        r["frames_file"].close()
        if r["video"]:
            r["video"].release()
        self.start_btn["text"] = "▶  Start run"
        self.status["text"] = f"Saved to {r['folder']}"
        if reason == "time up":
            beep(1500, 1000)
        msg = f"{r['jumps']} jumps, {r['taps']} taps"
        msg += "".join(f"\n{b}: {s:.0f} s" for b, s in sorted(r["behavior_s"].items()))
        if not self.live_tracking and r["video"]:
            self.proc = {"done": 0, "total": 1, "result": None, "folder": r["folder"]}
            threading.Thread(target=self._process, daemon=True,
                             args=(r["folder"], self._float(self.tap_threshold, 99),
                                   self._float(self.threshold, 99))).start()
            msg += "\nProcessing video in background - you'll get a popup when done."
        messagebox.showinfo("Run " + reason, f"{msg}\nSaved to {r['folder']}")

    def _process(self, folder, tap_g, jump_g):
        p = self.proc
        try:
            p["result"] = process(folder, tap_g, jump_g, lambda i, n: p.update(done=i, total=n))
        except Exception as e:  # shown in the popup instead of dying silently in the thread
            p["result"] = e

    def tick(self):
        now = time.monotonic()
        if not self.ser and not self.cap and now - self.last_port_scan >= 2:
            self.scan_ports()  # notice the Nano being plugged in
            self.last_port_scan = now
        if self.ser and now - self.last_send >= 1:
            left = int(max(0, self.run["dur"] - (now - self.run["t0"]))) if self.run else -1
            self.ser.write(f"S{self._float(self.setpoint):.2f}\nT{left}\n".encode())
            self.last_send = now

        jump_g, tap_g = self._float(self.threshold, 99), self._float(self.tap_threshold, 99)
        while not self.lines.empty():
            temp, heater, peak = self.latest = self.lines.get()
            jump = is_event(peak, jump_g, now, self.last_jump)
            tap = not jump and peak < jump_g and is_event(peak, tap_g, now, self.last_tap, TAP_REFRACTORY_S)
            self.accel_hist.append((now, peak))
            if jump:
                self.last_jump = now
                self.events.append((now, "jump"))
                beep()
            if tap:
                self.last_tap = now
                self.events.append((now, "tap"))
                beep(3000, 60)
            if self.run:
                self.run["jumps"] += jump
                self.run["taps"] += tap
                self.run["csv"].writerow([f"{now - self.run['t0']:.2f}", temp, self._float(self.setpoint),
                                          heater, peak, int(jump), self.run["jumps"], int(tap), self.run["taps"],
                                          *self._track_cols()])

        temp, heater, peak = self.latest
        c = self.cards
        c["Temp"]["text"] = "–" if temp != temp else f"{temp:.2f} °C"
        c["Heater"].configure(text="ON" if heater else "off", foreground=RED if heater else GREY)
        c["Accel"]["text"] = f"{peak:.2f} g"
        if self.run:
            elapsed = now - self.run["t0"]
            c["Elapsed"]["text"] = f"{int(elapsed)} s"
            c["Time left"]["text"] = f"{int(max(0, self.run['dur'] - elapsed))} s"
            c["Taps"]["text"], c["Jumps"]["text"] = self.run["taps"], self.run["jumps"]
            if self.run["interval"] > 0 and elapsed >= self.run["next_beep"]:
                beep(800, 400)
                self.run["next_beep"] += self.run["interval"]
        else:
            c["Elapsed"]["text"] = c["Time left"]["text"] = "–"
        c["Rat"].configure(text=self.track["behavior"] if self.track else "not found",
                           foreground=GREEN if self.track else GREY)
        if self.proc:
            if self.proc["result"] is None:
                self.status["text"] = f"Processing video… {self.proc['done'] / self.proc['total']:.0%}"
            else:
                self._processed()

        if self.cap and now - self.last_frame_t >= self.frame_dt:
            ok, frame = self.cap.read()
            if not ok and self.frame_dt:  # video file ended: loop it
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            if ok:
                self.raw = frame.copy()
                self.track = self.tracker.update(frame, now)
                moving = bool(self.track) and self.track["behavior"] == "moving"
                if self.move_beep.get() and move_started(moving, self.was_moving, now, self.last_move_beep):
                    beep(1200, 80)
                    self.last_move_beep = now
                self.was_moving = moving
                if self.track:
                    if self.live_tracking:  # record mode keeps the recorded video clean
                        draw(frame, self.track)
                    if self.run:
                        b = self.track["behavior"]
                        self.run["behavior_s"][b] = self.run["behavior_s"].get(b, 0) + now - self.last_frame_t
                self.last_frame_t = now
                if self.run:
                    if self.live_tracking:
                        stamp = f"{now - self.run['t0']:7.1f}s  {temp:.2f}C  jumps {self.run['jumps']}"
                        cv2.putText(frame, stamp, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                    if not self.run["video"]:
                        h, w = frame.shape[:2]
                        # ponytail: header fps is the camera's claim; postprocess fixes speed from frames.csv
                        fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
                        self.run["video"] = cv2.VideoWriter(str(self.run["folder"] / "video.mp4"),
                                                            cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
                    self.run["video"].write(frame)
                    self.run["frames"].writerow([self.run["n"], f"{now - self.run['t0']:.3f}"])
                    self.run["n"] += 1
                if not self.live_tracking:  # display only; recorded video stays clean
                    label = f"{now - self.run['t0']:6.1f}s  REC" if self.run else "not recording"
                    draw_overlay(frame, f"{label}  {temp:.2f}C", list(self.accel_hist), list(self.events),
                                 now, tap_g, jump_g)
                h, w = frame.shape[:2]
                ppm = cv2.imencode(".ppm", cv2.resize(frame, (VIDEO_W, VIDEO_W * h // w)))[1].tobytes()
                self.video.image = tk.PhotoImage(data=ppm, format="PPM")
                self.video.configure(image=self.video.image, text="")

        if self.run and now - self.run["t0"] >= self.run["dur"]:
            self.stop_run("time up")

        self.root.after(15, self.tick)

    def _processed(self):
        p, self.proc = self.proc, None
        if isinstance(p["result"], Exception):
            self.status["text"] = "Processing failed."
            messagebox.showerror("Processing failed", f"{p['result']}\nRetry: python postprocess.py {p['folder']}")
            return
        self.status["text"] = f"Processed: {p['folder'] / 'video_processed.mp4'}"
        beep(1500, 300)
        lines = "".join(f"\n{k}: {v}" for k, v in p["result"].items())
        messagebox.showinfo("Processed", f"video_processed.mp4 ready{lines}\n{p['folder']}")

    def _track_cols(self):
        t = self.track
        return [f"{t['x']:.0f}", f"{t['y']:.0f}", f"{t['speed']:.3f}", t["behavior"]] if t else ["", "", "", ""]

    def close(self):
        if self.proc and not messagebox.askyesno("Still processing", "Video is still processing. Quit anyway?"):
            return
        if self.run:
            self.stop_run("stopped")
        if self.ser:
            self.ser.write(b"S0\n")
            ser, self.ser = self.ser, None
            ser.close()
        if self.cap:
            self.cap.release()
        self.root.destroy()


def main(live_tracking=True):
    try:  # sharp text on high-DPI Windows screens
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)  # silence camera-probe warnings
    App(tk.Tk(), live_tracking).root.mainloop()


if __name__ == "__main__":
    main()
