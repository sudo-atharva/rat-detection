import csv
import tempfile
from pathlib import Path

import cv2
import numpy as np

from postprocess import process

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    vw = cv2.VideoWriter(str(d / "video.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 30, (320, 240))
    for i in range(30):
        f = np.zeros((240, 320, 3), np.uint8)
        f[100:140, 50 + i:130 + i] = 255  # "rat"
        vw.write(f)
    vw.release()
    with open(d / "frames.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame", "elapsed_s"])
        w.writerows([i, i / 10] for i in range(30))  # 10 fps real, though video header says 30
    with open(d / "data.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["elapsed_s", "temp_c", "setpoint_c", "heater", "accel_peak_g", "jump", "jump_count",
                    "tap", "tap_count", "rat_x", "rat_y", "speed_width_per_s", "behavior"])
        for i in range(30):
            jump, tap = int(i == 20), int(i == 10)
            w.writerow([i / 10, 37.0, 37, 1, 1.2 if jump else 0.4 if tap else 0.05, jump, int(i >= 20),
                        tap, int(i >= 10), "", "", "", ""])

    s = process(d, 0.3, 0.8)
    assert s["taps"] == 1 and s["jumps"] == 1, s
    out = cv2.VideoCapture(str(d / "video_processed.mp4"))
    assert out.get(cv2.CAP_PROP_FRAME_COUNT) == 30
    assert abs(out.get(cv2.CAP_PROP_FPS) - 10) < 0.5  # speed fixed from frame timestamps
    out.release()
    assert len((d / "tracking.csv").read_text().splitlines()) == 31
    assert "taps: 1" in (d / "summary.txt").read_text()
print("ok")
