import glob
import math
import sys
import threading
import time

import cv2
import mediapipe as mp
import pygame

from paths import resource_path

MODEL_FILE = "hand_landmarker.task"

# Pinch tuning
PINCH_ON = 0.35
PINCH_OFF = 0.55
CONFIRM_FRAMES = 2
LOST_GRACE_FRAMES = 6
RELEASE_TRIM = 3
MAX_JUMP_FRAC = 0.2
REANCHOR_AFTER = 3


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def cam_rect(win_w, win_h, cam_w, cam_h):
    s = min(win_w / cam_w, win_h / cam_h)
    w, h = max(1, int(cam_w * s)), max(1, int(cam_h * s))
    return pygame.Rect((win_w - w) // 2, (win_h - h) // 2, w, h)


def to_screen(lm_point, rect):
    return rect.x + lm_point.x * rect.w, rect.y + lm_point.y * rect.h


def pinch_point(lm, rect):
    x = (lm[4].x + lm[8].x) / 2
    y = (lm[4].y + lm[8].y) / 2
    return rect.x + x * rect.w, rect.y + y * rect.h


def pinch_ratio(lm, aspect):
    def dist(a, b):
        return math.hypot((a.x - b.x) * aspect, a.y - b.y)
    hand = dist(lm[0], lm[9]) or 1e-6
    return dist(lm[4], lm[8]) / hand


# ---------------------------------------------------------------------------
# Pinch state machine
# ---------------------------------------------------------------------------
class PinchState:
    def __init__(self, sensitivity=1.0):
        self.sensitivity = sensitivity
        self.on = PINCH_ON * sensitivity
        self.off = PINCH_OFF * sensitivity
        self.pinching = False
        self.streak = 0
        self.missing = 0
        self.need_release = False

    def reset(self):
        self.__init__(self.sensitivity)

    def update(self, lm, aspect):
        if lm is None:
            self.streak = 0
            self.missing += 1
            if self.pinching and self.missing > LOST_GRACE_FRAMES:
                self.pinching = False
                self.need_release = True
                return "cancel"
            return None

        self.missing = 0
        ratio = pinch_ratio(lm, aspect)
        if self.need_release:
            if ratio > self.off:
                self.need_release = False
            return None
        want = ratio < (self.off if self.pinching else self.on)
        if want != self.pinching:
            self.streak += 1
            if self.streak >= CONFIRM_FRAMES:
                self.pinching, self.streak = want, 0
                return "start" if want else "release"
        else:
            self.streak = 0
        return None


# ---------------------------------------------------------------------------
# Smoothing
# ---------------------------------------------------------------------------
class OneEuro:
    def __init__(self, min_cutoff=1.2, beta=0.02, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = None
        self.dx = 0.0

    def reset(self):
        self.x, self.dx = None, 0.0

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, dt):
        dt = max(dt, 1e-3)
        if self.x is None:
            self.x, self.dx = x, 0.0
            return x
        dx = (x - self.x) / dt
        self.dx += self._alpha(self.d_cutoff, dt) * (dx - self.dx)
        cutoff = self.min_cutoff + self.beta * abs(self.dx)
        self.x += self._alpha(cutoff, dt) * (x - self.x)
        return self.x


class StrokeBuilder:
    def __init__(self):
        self.points = []
        self.active = False
        self._fx, self._fy = OneEuro(), OneEuro()
        self._rejects = 0

    def begin(self):
        self.points = []
        self.active = True
        self._fx.reset()
        self._fy.reset()
        self._rejects = 0

    def cancel(self):
        self.points = []
        self.active = False

    def add(self, raw_x, raw_y, dt, arena_h):
        if not self.active:
            return None
        x, y = self._fx(raw_x, dt), self._fy(raw_y, dt)
        p = (int(x), int(y))
        if self.points:
            lx, ly = self.points[-1]
            if math.hypot(p[0] - lx, p[1] - ly) > MAX_JUMP_FRAC * arena_h:
                self._rejects += 1
                if self._rejects < REANCHOR_AFTER:
                    return None
        self._rejects = 0
        self.points.append(p)
        return p

    def finish(self):
        pts = self.points[:-RELEASE_TRIM] if len(self.points) > RELEASE_TRIM * 3 else list(self.points)
        self.cancel()
        return pts


# ---------------------------------------------------------------------------
# Finding cameras
# ---------------------------------------------------------------------------
MAX_CAMERAS = 6


def open_camera(index):
    if sys.platform == "win32":
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if cap.isOpened():
            return cap
        cap.release()
    return cv2.VideoCapture(index)


def camera_names():
    names = {}
    if sys.platform == "win32":
        try:
            from pygrabber.dshow_graph import FilterGraph
            for i, name in enumerate(FilterGraph().get_input_devices()):
                names[i] = name
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        for path in glob.glob("/sys/class/video4linux/video*/name"):
            try:
                idx = int(path.split("video4linux/video")[1].split("/")[0])
                with open(path) as f:
                    names[idx] = f.read().strip()
            except (ValueError, OSError):
                pass
    return names


def _gives_picture(cap, timeout=2.0):
    # IR cameras (e.g. ThinkPad "Integrated IR Camera") open fine but only send
    # black frames (a few hot pixels aside); a real camera may also start with
    # a few black frames while warming up.
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        ok, frame = cap.read()
        if ok and frame is not None and frame.mean() >= 0.5:
            return True
    return False


def list_cameras(max_index=MAX_CAMERAS):
    names = camera_names()
    found = []
    for i in range(max_index):
        cap = open_camera(i)
        try:
            ok = cap.isOpened() and _gives_picture(cap)
        finally:
            cap.release()
        if ok:
            found.append((i, names.get(i) or f"Camera {i + 1}"))
    return found


# ---------------------------------------------------------------------------
# Camera + hand detection on a background thread
# ---------------------------------------------------------------------------
class HandTracker:
    def __init__(self, camera_index=0, width=640, height=480):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._latest = None
        self.error = None
        self.ready = threading.Event()
        self.camera_index = camera_index
        self._thread = threading.Thread(
            target=self._run, args=(camera_index, width, height), daemon=True
        )
        self._thread.start()

    def latest(self):
        with self._lock:
            return self._latest

    def stop(self, wait=True):
        self._stop.set()
        if wait:
            self._thread.join(timeout=2.0)

    def _run(self, index, width, height):
        cap = None
        try:
            model = resource_path(MODEL_FILE)
            options = mp.tasks.vision.HandLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=model),
                running_mode=mp.tasks.vision.RunningMode.VIDEO,
                num_hands=1,
                min_hand_detection_confidence=0.4,
                min_tracking_confidence=0.5,
                min_hand_presence_confidence=0.4,
            )
            cap = open_camera(index)
            if not cap.isOpened():
                raise RuntimeError(
                    f"Couldn't open camera {index + 1}. Close any other app using it, "
                    "or pick another camera from CAMERA in the main menu.")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

            with mp.tasks.vision.HandLandmarker.create_from_options(options) as landmarker:
                t0 = time.monotonic()
                last_ts = -1
                frame_id = 0
                last_ok = time.monotonic()
                while not self._stop.is_set():
                    ok, frame = cap.read()
                    if not ok:
                        if time.monotonic() - last_ok > 3.0:
                            raise RuntimeError(
                                f"Camera {index + 1} stopped sending frames. "
                                "Check the cable, or pick another camera from CAMERA in the main menu.")
                        time.sleep(0.01)
                        continue
                    last_ok = time.monotonic()
                    frame = cv2.flip(frame, 1)
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    ts = int((time.monotonic() - t0) * 1000)
                    ts = max(ts, last_ts + 1)
                    last_ts = ts
                    result = landmarker.detect_for_video(
                        mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts
                    )
                    lm = result.hand_landmarks[0] if result.hand_landmarks else None
                    frame_id += 1
                    with self._lock:
                        self._latest = (frame_id, rgb, lm, time.monotonic())
                    self.ready.set()
        except Exception as e:
            if "hand_landmarker" in str(e) or "model" in str(e).lower():
                self.error = f"Couldn't load {MODEL_FILE}. Put it next to main.py. ({e})"
            else:
                self.error = str(e)
            self.ready.set()
        finally:
            if cap is not None:
                cap.release()