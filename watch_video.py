"""Watch rat tracking on a video file.

Usage: python watch_video.py [video]   (default test.mp4)
Keys: space = pause/resume, q or Esc = quit.
"""
import sys

import cv2

from rat_track import Tracker, draw

path = (sys.argv[1].strip().strip('"') if len(sys.argv) > 1 else "") or "test.mp4"
cap = cv2.VideoCapture(path)
if not cap.isOpened():
    sys.exit(f"Cannot open {path}")
fps = cap.get(cv2.CAP_PROP_FPS) or 30
tracker = Tracker()
i = 0
while True:
    ok, frame = cap.read()
    if not ok:
        break
    r = tracker.update(frame, i / fps)
    if r:
        draw(frame, r)
    cv2.putText(frame, f"{i / fps:5.1f}s  {r['behavior'] if r else 'no rat'}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
    cv2.imshow("Rat tracking (space = pause, q = quit)", frame)
    i += 1
    key = cv2.waitKey(int(1000 / fps)) & 0xFF
    if key == ord(" "):
        key = cv2.waitKey(0) & 0xFF
    if key in (ord("q"), 27):
        break
cap.release()
cv2.destroyAllWindows()
