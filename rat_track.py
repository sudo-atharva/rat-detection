"""Rat position + behavior from camera frames, plain OpenCV (no trained model).

Rat = biggest blob that differs from an empty-arena background shot, or, with no
background set, the biggest bright blob (white lab rat on dark floor).
"""
from collections import deque
from statistics import median

import cv2
import numpy as np

# ponytail: hand-tuned pixel thresholds, retune per camera/arena; DeepLabCut/SLEAP if you need limb-level pose
VIEW = "side"        # "top": rearing = blob area shrinks; "side": rearing = blob gets taller
DIFF_THRESH = 30     # grey-level change vs background that counts as rat
BRIGHT_THRESH = 150  # grey level counted as rat when no background is set
MOTION_THRESH = 25   # frame-to-frame grey change that counts as motion
MIN_AREA = 500       # px, smaller blobs are noise
MAX_AREA_FRAC = 0.5  # blob over this fraction of frame = lighting change / blank frame, not a rat
MOVE_FRAC_S = 0.05   # centroid speed above this fraction of frame width per second = moving
TAIL_FRAC = 0.015    # opening kernel as fraction of frame width; strips the thin tail so it can't jerk the centroid
REAR_RATIO = 1.3     # size vs typical: side = height above 1.3x, top = area below 1/1.3x
GROOM_FRAC = 0.05    # fraction of body pixels changing while in place = grooming
SPEED_SMOOTH = 0.3   # EMA weight of newest speed sample

COLORS = {"moving": (0, 200, 255), "rearing": (255, 0, 255), "grooming": (255, 200, 0), "still": (0, 0, 255)}


def classify(speed, size_ratio, motion_frac):
    """speed in frame widths/s; size_ratio = current / typical size (area top view, height side view)."""
    if size_ratio and (size_ratio > REAR_RATIO if VIEW == "side" else size_ratio < 1 / REAR_RATIO):
        return "rearing"
    if speed >= MOVE_FRAC_S:
        return "moving"
    if motion_frac >= GROOM_FRAC:
        return "grooming"
    return "still"


def _gray(frame):
    return cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (5, 5), 0)


class Tracker:
    def __init__(self):
        self.bg = None
        self.prev_gray = None
        self.prev = None  # (x, y, t)
        self.speed = 0.0
        self.sizes = deque(maxlen=300)

    def set_background(self, frame):
        self.bg = _gray(frame)
        self.prev = None
        self.sizes.clear()

    def update(self, frame, t):
        """Returns dict(x, y, speed, behavior, contour) or None if no rat."""
        gray = _gray(frame)
        prev_gray, self.prev_gray = self.prev_gray, gray
        if self.bg is None:
            _, mask = cv2.threshold(gray, BRIGHT_THRESH, 255, cv2.THRESH_BINARY)
        else:
            _, mask = cv2.threshold(cv2.absdiff(gray, self.bg), DIFF_THRESH, 255, cv2.THRESH_BINARY)
        k = max(3, int(gray.shape[1] * TAIL_FRAC))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(contours, key=cv2.contourArea, default=None)
        if c is None or not MIN_AREA <= cv2.contourArea(c) <= MAX_AREA_FRAC * gray.size:
            return None

        area = cv2.contourArea(c)
        m = cv2.moments(c)
        x, y = m["m10"] / m["m00"], m["m01"] / m["m00"]
        if self.prev and t > self.prev[2]:
            inst = np.hypot(x - self.prev[0], y - self.prev[1]) / (t - self.prev[2]) / gray.shape[1]
            self.speed += SPEED_SMOOTH * (inst - self.speed)
        self.prev = (x, y, t)

        motion_frac = 0.0
        if prev_gray is not None:
            body = np.zeros_like(gray)
            cv2.drawContours(body, [c], -1, 255, -1)
            moved = (cv2.absdiff(gray, prev_gray) > MOTION_THRESH) & (body > 0)
            motion_frac = moved.sum() / area

        size = cv2.boundingRect(c)[3] if VIEW == "side" else area
        ratio = size / median(self.sizes) if len(self.sizes) >= 30 else None
        self.sizes.append(size)
        return {"x": x, "y": y, "speed": self.speed, "contour": c,
                "behavior": classify(self.speed, ratio, motion_frac)}


def draw(frame, r):
    color = COLORS[r["behavior"]]
    cv2.drawContours(frame, [r["contour"]], -1, color, 2)
    cv2.circle(frame, (int(r["x"]), int(r["y"])), 4, color, -1)
    cv2.putText(frame, r["behavior"], (int(r["x"]) + 8, int(r["y"]) - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
