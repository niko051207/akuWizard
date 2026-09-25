"""
Webcam hand input shared by the game and the spell recorder.

Everything that turns a camera image into a clean stroke lives here, so spells
are recorded with exactly the same pipeline they are later cast with.
"""
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

# Pinch thresholds are ratios of hand size (wrist -> middle knuckle), so they
# work the same whether you stand close to the camera or far away.
PINCH_ON = 0.35          # fingers closer than this -> pinch starts
PINCH_OFF = 0.55         # fingers farther than this -> pinch releases
                         # (the gap between the two stops on/off flicker)
CONFIRM_FRAMES = 2       # camera frames a new pinch state must hold before it counts
LOST_GRACE_FRAMES = 6    # camera frames the hand may vanish mid-stroke before the cast is cancelled
RELEASE_TRIM = 3         # points dropped from the end of a stroke (opening fingers drag the cursor)
MAX_JUMP_FRAC = 0.2      # ignore single-frame jumps bigger than this fraction of the arena height
REANCHOR_AFTER = 3       # ...unless the hand really did move: accept after this many in a row


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def cam_rect(win_w, win_h, cam_w, cam_h):
    """Largest rect with the camera's aspect ratio that fits the window (letterbox).
    Drawing inside this rect keeps gestures from being stretched when the window
    is not the same shape as the camera."""
    s = min(win_w / cam_w, win_h / cam_h)
    w, h = max(1, int(cam_w * s)), max(1, int(cam_h * s))
    return pygame.Rect((win_w - w) // 2, (win_h - h) // 2, w, h)


def to_screen(lm_point, rect):
    return rect.x + lm_point.x * rect.w, rect.y + lm_point.y * rect.h


def pinch_point(lm, rect):
    """Midpoint of thumb tip (4) and index tip (8) in screen pixels.
    This is also where the on-screen cursor is drawn, so what you see is what draws."""
    x = (lm[4].x + lm[8].x) / 2
    y = (lm[4].y + lm[8].y) / 2
    return rect.x + x * rect.w, rect.y + y * rect.h


def pinch_ratio(lm, aspect):
    """Thumb-index distance divided by hand size. `aspect` = frame width / height,
    so x and y distances are measured in the same units."""
    def dist(a, b):
        return math.hypot((a.x - b.x) * aspect, a.y - b.y)
    hand = dist(lm[0], lm[9]) or 1e-6
    return dist(lm[4], lm[8]) / hand


# ---------------------------------------------------------------------------
# Pinch state machine
# ---------------------------------------------------------------------------
class PinchState:
    """Turns per-frame landmarks into clean events: 'start', 'release', 'cancel'.

    - Two thresholds (PINCH_ON / PINCH_OFF) plus CONFIRM_FRAMES stop flicker.
    - A missing hand is NOT a release: after LOST_GRACE_FRAMES it becomes a
      'cancel', so a half-drawn spell never fires by accident.
    """

    def __init__(self):
        self.pinching = False
        self.streak = 0
        self.missing = 0
        self.need_release = False   # after a cancel, fingers must open before a new stroke

    def reset(self):
        self.__init__()

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
            if ratio > PINCH_OFF:
                self.need_release = False
            return None
        want = ratio < (PINCH_OFF if self.pinching else PINCH_ON)
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
    """One Euro filter: smooths hard when the hand moves slowly (kills jitter),
    follows closely when it moves fast (no lag on quick strokes)."""

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
    """Collects smoothed points for one gesture."""

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
        """Add a raw point; returns the smoothed point if it was kept, else None."""
        if not self.active:
            return None
        x, y = self._fx(raw_x, dt), self._fy(raw_y, dt)
        p = (int(x), int(y))
        if self.points:
            lx, ly = self.points[-1]
            if math.hypot(p[0] - lx, p[1] - ly) > MAX_JUMP_FRAC * arena_h:
                self._rejects += 1
                if self._rejects < REANCHOR_AFTER:
                    return None      # probably a tracking glitch
                # it kept happening, so the hand really moved: continue from here
        self._rejects = 0
        self.points.append(p)
        return p

    def finish(self):
        """End the stroke and return its points, minus the release hook."""
        pts = self.points[:-RELEASE_TRIM] if len(self.points) > RELEASE_TRIM * 3 else list(self.points)
        self.cancel()
        return pts


# ---------------------------------------------------------------------------
# Finding cameras
# ---------------------------------------------------------------------------
MAX_CAMERAS = 6   # indices 0..5 are checked


def open_camera(index):
    """Open a webcam by index. On Windows DirectShow opens much faster than the
    default backend and its device order matches the names from camera_names()."""
    if sys.platform == "win32":
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if cap.isOpened():
            return cap
        cap.release()
    return cv2.VideoCapture(index)


def camera_names():
    """Best-effort {index: friendly name}. Falls back to "Camera N" in the picker."""
    names = {}
    if sys.platform == "win32":
        try:  # optional: pip install pygrabber
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


def list_cameras(max_index=MAX_CAMERAS):
    """Return [(index, name)] for every camera that actually delivers a frame.
    Takes a second or two, so call it from a background thread."""
    names = camera_names()
    found = []
    for i in range(max_index):
        cap = open_camera(i)
        try:
            ok = cap.isOpened() and cap.read()[0]
        finally:
            cap.release()
        if ok:
            found.append((i, names.get(i) or f"Camera {i + 1}"))
    return found


# ---------------------------------------------------------------------------
# Camera + hand detection on a background thread
# ---------------------------------------------------------------------------
class HandTracker:
    """Reads the webcam and runs MediaPipe on its own thread.

    The game calls latest() every frame and never waits for the camera, so it
    renders at a steady 60 fps even with a 30 fps webcam.
    latest() -> (frame_id, rgb_frame, landmarks_or_None, capture_time) or None.
    """

    def __init__(self, camera_index=0, width=640, height=480):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._latest = None
        self.error = None
        self.ready = threading.Event()   # set on first frame OR on error
        self.camera_index = camera_index
        self._thread = threading.Thread(
            target=self._run, args=(camera_index, width, height), daemon=True
        )
        self._thread.start()

    def latest(self):
        with self._lock:
            return self._latest

    def stop(self, wait=True):
        """Stop the camera thread. wait=False returns immediately (the camera is
        released a moment later); use wait=True before reopening the same camera."""
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
                    ts = max(ts, last_ts + 1)          # MediaPipe needs strictly increasing timestamps
                    last_ts = ts
                    result = landmarker.detect_for_video(
                        mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts
                    )
                    lm = result.hand_landmarks[0] if result.hand_landmarks else None
                    frame_id += 1
                    with self._lock:
                        self._latest = (frame_id, rgb, lm, time.monotonic())
                    self.ready.set()
        except Exception as e:  # surfaced to the player on the loading screen
            if "hand_landmarker" in str(e) or "model" in str(e).lower():
                self.error = f"Couldn't load {MODEL_FILE}. Put it next to main.py. ({e})"
            else:
                self.error = str(e)
            self.ready.set()
        finally:
            if cap is not None:
                cap.release()