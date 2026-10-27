import numpy as np

import rat_track
from rat_track import Tracker, classify

rat_track.VIEW = "top"
assert classify(0.1, 1.0, 0) == "moving"
assert classify(0, 0.5, 0) == "rearing"
assert classify(0, 1.0, 0.2) == "grooming"
assert classify(0, 1.0, 0) == "still"
assert classify(0, None, 0) == "still"  # no typical size yet
rat_track.VIEW = "side"
assert classify(0, 1.5, 0) == "rearing"
assert classify(0, 0.5, 0) == "still"
rat_track.VIEW = "top"


def frame(x, y, w=80, h=40):
    f = np.zeros((480, 640, 3), np.uint8)
    f[y:y + h, x:x + w] = 255
    return f


tr = Tracker()  # no background: bright-blob mode
assert tr.update(np.full((480, 640, 3), 255, np.uint8), 0) is None  # blank white frame isn't a rat
t = 0
for _ in range(40):
    r = tr.update(frame(100, 100), t)
    t += 0.1
assert r["behavior"] == "still", r["behavior"]
assert abs(r["x"] - 140) < 2 and abs(r["y"] - 120) < 2
for i in range(10):
    r = tr.update(frame(100 + 20 * i, 100), t)
    t += 0.1
assert r["behavior"] == "moving", r["behavior"]
r = tr.update(frame(300, 100, 40, 40), t)
assert r["behavior"] == "rearing", r["behavior"]
assert tr.update(np.zeros((480, 640, 3), np.uint8), t + 0.1) is None  # rat gone

tr.set_background(np.full((480, 640, 3), 255, np.uint8))  # background mode: dark rat on white
f = np.full((480, 640, 3), 255, np.uint8)
f[100:140, 100:180] = 0
assert abs(tr.update(f, 0)["x"] - 140) < 2
print("ok")
