"""Overlay a run's MPU6050 data onto its video, plus offline rat tracking.

Usage: python postprocess.py runs/<folder> [tap_g jump_g]
Reads video.mp4, frames.csv (per-frame timestamps) and data.csv from the run folder,
writes video_processed.mp4, tracking.csv and summary.txt next to them.
"""
import csv
import sys
from pathlib import Path

import cv2
import numpy as np

from rat_track import Tracker, draw

GRAPH_S = 5     # seconds of accel history in the graph
FLASH_S = 0.4   # how long TAP / JUMP text stays on screen after an event
EVENT_COLORS = {"tap": (0, 255, 255), "jump": (0, 0, 255)}


def draw_overlay(frame, text, samples, events, now, tap_g, jump_g):
    """Status text + scrolling accel graph + TAP/JUMP flash.

    samples: [(t, peak_g)], events: [(t, 'tap'|'jump')], all on the same clock as now.
    """
    h, w = frame.shape[:2]
    s = max(0.5, w / 1280)
    cv2.putText(frame, text, (10, int(35 * s)), cv2.FONT_HERSHEY_SIMPLEX, 0.9 * s, (0, 255, 0), 2)

    gw, gh = w * 2 // 5, h // 5
    x0, y0 = 10, h - gh - 10
    frame[y0:y0 + gh, x0:x0 + gw] //= 3  # darken graph area
    top = max(jump_g * 1.5, 0.5)

    def px(t):
        return int(x0 + gw - (now - t) / GRAPH_S * gw)

    def py(g):
        return int(y0 + gh - min(g, top) / top * gh)

    for g, col in ((tap_g, EVENT_COLORS["tap"]), (jump_g, EVENT_COLORS["jump"])):
        cv2.line(frame, (x0, py(g)), (x0 + gw, py(g)), col, 2)
    for t, kind in events:
        if now - GRAPH_S <= t <= now:
            cv2.line(frame, (px(t), y0), (px(t), y0 + gh), EVENT_COLORS[kind], 2)
    pts = np.array([(px(t), py(g)) for t, g in samples if now - GRAPH_S <= t <= now], np.int32)
    if len(pts) > 1:
        cv2.polylines(frame, [pts], False, (255, 255, 255), 2)
    cv2.putText(frame, f"accel g, last {GRAPH_S}s", (x0 + 5, y0 + int(20 * s)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5 * s, (255, 255, 255), 1)

    recent = [k for t, k in events if 0 <= now - t <= FLASH_S]
    if recent:
        kind = "jump" if "jump" in recent else "tap"
        cv2.putText(frame, kind.upper() + "!", (w // 2 - int(80 * s), int(90 * s)),
                    cv2.FONT_HERSHEY_SIMPLEX, 2 * s, EVENT_COLORS[kind], 4)


def _read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def process(folder, tap_g=0.3, jump_g=0.8, progress=None):
    """Returns summary dict. progress(i, n) is called per frame."""
    folder = Path(folder)
    rows = _read(folder / "data.csv")
    t = np.array([float(r["elapsed_s"]) for r in rows])
    peak = np.array([float(r["accel_peak_g"]) for r in rows])
    events = [(float(r["elapsed_s"]), "jump" if r["jump"] == "1" else "tap")
              for r in rows if r["jump"] == "1" or r["tap"] == "1"]
    frame_t = [float(r["elapsed_s"]) for r in _read(folder / "frames.csv")]
    # real fps from timestamps, so output plays at true speed whatever the camera claimed
    fps = (len(frame_t) - 1) / (frame_t[-1] - frame_t[0]) if len(frame_t) > 1 and frame_t[-1] > frame_t[0] else 30

    tracker = Tracker()
    if (folder / "background.png").exists():
        tracker.set_background(cv2.imread(str(folder / "background.png")))
    cap = cv2.VideoCapture(str(folder / "video.mp4"))
    out = None
    behavior_s = {}
    with open(folder / "tracking.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["elapsed_s", "rat_x", "rat_y", "speed_width_per_s", "behavior"])
        for i, ft in enumerate(frame_t):
            ok, frame = cap.read()
            if not ok:
                break
            if out is None:
                fh, fw = frame.shape[:2]
                out = cv2.VideoWriter(str(folder / "video_processed.mp4"), cv2.VideoWriter_fourcc(*"mp4v"),
                                      fps, (fw, fh))
            r = tracker.update(frame, ft)
            if r:
                draw(frame, r)
                w.writerow([f"{ft:.3f}", f"{r['x']:.0f}", f"{r['y']:.0f}", f"{r['speed']:.3f}", r["behavior"]])
                if i:
                    behavior_s[r["behavior"]] = behavior_s.get(r["behavior"], 0) + ft - frame_t[i - 1]

            k = np.searchsorted(t, ft, "right") - 1
            if k >= 0:
                row = rows[k]
                text = (f"{ft:6.1f}s  {float(row['temp_c']):.2f}C  heater {'ON' if row['heater'] == '1' else 'off'}"
                        f"  taps {row['tap_count']}  jumps {row['jump_count']}")
            else:
                text = f"{ft:6.1f}s"
            lo = np.searchsorted(t, ft - GRAPH_S)
            draw_overlay(frame, text, list(zip(t[lo:k + 1], peak[lo:k + 1])), events, ft, tap_g, jump_g)
            out.write(frame)
            if progress:
                progress(i + 1, len(frame_t))
    cap.release()
    if out:
        out.release()

    summary = {"taps": sum(k == "tap" for _, k in events), "jumps": sum(k == "jump" for _, k in events),
               **{f"{b}_s": round(v, 1) for b, v in sorted(behavior_s.items())}}
    (folder / "summary.txt").write_text("".join(f"{k}: {v}\n" for k, v in summary.items()))
    return summary


if __name__ == "__main__":
    if len(sys.argv) not in (2, 4):
        sys.exit(__doc__)
    g = [float(a) for a in sys.argv[2:]] or [0.3, 0.8]
    print(process(sys.argv[1], *g, progress=lambda i, n: print(f"\r{i}/{n}", end="")))
