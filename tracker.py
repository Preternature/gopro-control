"""
Person Tracker — lock-on face tracking with gimbal-aware re-initialization.

State machine:
  SEARCHING  →  Haar finds the first face
  TRACKING   →  CSRT tracker follows it frame-to-frame
  HOLDING    →  CSRT lost; hold last box, strict nearby re-acquire

Gimbal communication:
  When a pan command fires, we know the camera will physically move and the
  stream will show that movement ~stream_delay seconds later.  We schedule a
  tracker re-initialization at that future time, placing the CSRT window at
  the predicted new face position so the tracker doesn't get left behind.
"""

import collections
import cv2
import numpy as np
import os
import threading
import time
import queue
from typing import Optional


def _make_cv_tracker():
    """CSRT preferred (accurate), fall back to KCF."""
    for maker in [
        lambda: cv2.TrackerCSRT_create(),
        lambda: cv2.legacy.TrackerCSRT_create(),
        lambda: cv2.TrackerKCF_create(),
        lambda: cv2.legacy.TrackerKCF_create(),
    ]:
        try:
            return maker()
        except AttributeError:
            continue
    return None


class PersonTracker:

    def __init__(self, arduino):
        self.arduino = arduino
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.active = False
        self.cam_id: Optional[int] = None
        self._connection = None
        self._flip = False

        yunet_path = os.path.join(os.path.dirname(__file__), 'face_detection_yunet_2023mar.onnx')
        if os.path.exists(yunet_path):
            self._yunet = cv2.FaceDetectorYN.create(
                yunet_path, "", (320, 320), score_threshold=0.60, nms_threshold=0.3)
            print("[tracker] YuNet face detector loaded")
        else:
            self._yunet = None
            cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
            self._face_cascade = cv2.CascadeClassifier(cascade_path)
            print("[tracker] WARNING: YuNet not found, using Haar cascade")

        # State machine
        self._state = 'searching'
        self._cv_tracker = None
        self._hold_start = 0.0
        self.hold_timeout = 1.5

        # Public state
        self.detected = False
        self.bbox = None
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.frame_count = 0

        # Smoothed bbox (center + size) for stable display
        self._smooth_cx: Optional[float] = None
        self._smooth_cy: Optional[float] = None
        self._smooth_bw: Optional[float] = None
        self._smooth_bh: Optional[float] = None
        self.bbox_smooth_alpha = 0.15

        # Last frame size (updated each frame, used by gimbal shift math)
        self._last_fw: Optional[int] = None
        self._last_fh: Optional[int] = None

        # Pending gimbal-shift predictions: (delta_cx, delta_cy, apply_at)
        # Scheduled when a command fires; applied after stream_delay seconds
        self._pending_shifts: collections.deque = collections.deque()

        # Gimbal state — targets set by _adjust_gimbal, physical positions ramped by _run_gimbal_ramp
        self._base_angle = 90.0        # target pan angle (degrees)
        self._cam_us = 1900.0          # target tilt (μs)
        self._phys_angle = 90.0        # current physical servo position
        self._phys_us = 1500.0
        self._last_gimbal_cmd = 0.0
        self._ramp_thread: Optional[threading.Thread] = None

        # Gimbal movement limits (degrees for pan, μs for tilt)
        self.pan_min  = 45.0
        self.pan_max  = 144.0
        self.tilt_min = 1300.0
        self.tilt_max = 2350.0

        # Tuning
        self.dead_zone = 0.18
        self.poll_interval = 2.5       # seconds between target re-evaluations
        self.pan_max_rate = 4.0        # degrees / second max target correction
        self.tilt_max_rate = 80.0      # μs / second max target correction
        self.pan_gain = 0.4
        self.tilt_gain = 0.4
        # Ramp: how fast the servo physically moves toward the target
        self.ramp_pan_rate = 2.5       # degrees / second servo ramp speed
        self.ramp_tilt_rate = 60.0     # μs / second servo ramp speed
        self.pan_sign = -1
        self.tilt_sign = 1
        # Target: box bottom sits this far above frame center (negative = above).
        # -0.30 ≈ upper-third framing — face in upper portion of viewport.
        self.tilt_target_y = -0.15
        self.max_jump_fraction = 0.40
        # Video stream lag in seconds — shift prediction fires this long after command
        self.stream_delay = 1.2
        # Estimated horizontal field of view (degrees) for pixel displacement math
        self.fov_h_degrees = 100.0

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, cam_id: int, connection, flip: bool = False) -> bool:
        if not connection.stream_active:
            print("[tracker] Cannot start — stream not active")
            return False
        if self.active:
            self.stop()
        self.cam_id = cam_id
        self._connection = connection
        self._flip = flip
        self._base_angle = float(self.arduino.base_angle)
        self._cam_us = float(self.arduino.cam_us)
        self._phys_angle = self._base_angle
        self._phys_us = self._cam_us
        self.frame_count = 0
        self._reset_smooth()
        self._pending_shifts.clear()
        self._state = 'searching'
        self._cv_tracker = None
        self._stop_event.clear()
        self.active = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="person-tracker")
        self._ramp_thread = threading.Thread(target=self._run_gimbal_ramp, daemon=True, name="gimbal-ramp")
        self._thread.start()
        self._ramp_thread.start()
        print(f"[tracker] Started on cam{cam_id} flip={flip} — searching for face...")
        return True

    def stop(self):
        print("[tracker] Stopping")
        self.active = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        if self._ramp_thread:
            self._ramp_thread.join(timeout=2)
            self._ramp_thread = None
        self.detected = False
        self.bbox = None
        self._state = 'searching'
        self._cv_tracker = None
        self._pending_shifts.clear()
        self.cam_id = None
        self._connection = None

    def set_limits(self, pan_min: float, pan_max: float, tilt_min: float, tilt_max: float):
        self.pan_min  = float(pan_min)
        self.pan_max  = float(pan_max)
        self.tilt_min = float(tilt_min)
        self.tilt_max = float(tilt_max)
        # Clamp current targets to new limits immediately
        self._base_angle = max(self.pan_min,  min(self.pan_max,  self._base_angle))
        self._cam_us     = max(self.tilt_min, min(self.tilt_max, self._cam_us))

    def get_status(self) -> dict:
        display_bbox = None
        if self._smooth_cx is not None and self._smooth_bw is not None:
            display_bbox = [
                int(self._smooth_cx - self._smooth_bw / 2),
                int(self._smooth_cy - self._smooth_bh / 2),
                int(self._smooth_bw),
                int(self._smooth_bh),
            ]
        return {
            "active": self.active,
            "cam_id": self.cam_id,
            "detected": self.detected,
            "state": self._state,
            "bbox": display_bbox,
            "offset_x": round(self.offset_x, 3),
            "offset_y": round(self.offset_y, 3),
            "frame_count": self.frame_count,
            "base_angle": round(self._base_angle, 1),
            "cam_us": round(self._cam_us),
            "flip": self._flip,
        }

    # ── Background thread ─────────────────────────────────────────────────────

    def _run(self):
        conn = self._connection
        q = conn.subscribe_frames()
        print("[tracker] Waiting for frames...")
        try:
            while not self._stop_event.is_set():
                try:
                    frame_bytes = q.get(timeout=1.0)
                except queue.Empty:
                    print(f"[tracker] No frames (count={self.frame_count})")
                    continue

                arr = np.frombuffer(frame_bytes, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is None:
                    continue

                if self._flip:
                    frame = cv2.flip(frame, -1)

                self.frame_count += 1
                self._process_frame(frame)
        finally:
            conn.unsubscribe_frames(q)
            self.active = False
            print(f"[tracker] Exited after {self.frame_count} frames")

    # ── State machine ─────────────────────────────────────────────────────────

    def _process_frame(self, frame):
        fh, fw = frame.shape[:2]
        self._last_fw = fw
        self._last_fh = fh

        # Apply scheduled gimbal shift predictions whose time has come
        now = time.time()
        while self._pending_shifts and self._pending_shifts[0][2] <= now:
            delta_cx, delta_cy, _ = self._pending_shifts.popleft()
            self._apply_tracker_shift(frame, delta_cx, delta_cy)

        if self._state == 'searching':
            self._do_search(frame, strict=False)
        elif self._state == 'tracking':
            self._do_track(frame)
        elif self._state == 'holding':
            self._do_hold(frame)

    def _do_search(self, frame, strict=False):
        h, w = frame.shape[:2]
        scale = min(1.0, 640 / w)
        small = cv2.resize(frame, (int(w * scale), int(h * scale)))
        sh, sw = small.shape[:2]

        if self._yunet is not None:
            self._yunet.setInputSize((sw, sh))
            _, faces = self._yunet.detect(small)
            if faces is None or len(faces) == 0:
                return
            areas = [f[2] * f[3] for f in faces]
            f = faces[int(np.argmax(areas))]
            inv = 1.0 / scale
            fx, fy, bw, bh = int(f[0]*inv), int(f[1]*inv), int(f[2]*inv), int(f[3]*inv)
        else:
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            faces = self._face_cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
            if len(faces) == 0:
                return
            areas = [fw2 * fh2 for (_, _, fw2, fh2) in faces]
            fx, fy, bw, bh = faces[int(np.argmax(areas))]
            inv = 1.0 / scale
            fx, fy, bw, bh = int(fx*inv), int(fy*inv), int(bw*inv), int(bh*inv)

        if strict and self._smooth_cx is not None:
            max_jump = w * self.max_jump_fraction
            new_cx, new_cy = fx + bw / 2, fy + bh / 2
            if (abs(new_cx - self._smooth_cx) > max_jump or
                    abs(new_cy - self._smooth_cy) > max_jump):
                return

        self._lock_onto(frame, fx, fy, bw, bh)

    def _do_track(self, frame):
        ok, raw_bbox = self._cv_tracker.update(frame)
        if not ok:
            print("[tracker] CSRT lost face — holding")
            self._state = 'holding'
            self._hold_start = time.time()
            return

        x, y, w, h = (int(v) for v in raw_bbox)
        self.bbox = (x, y, w, h)
        self.detected = True
        self._update_smooth(frame, x, y, w, h)

    def _do_hold(self, frame):
        self._do_search(frame, strict=True)
        if self._state == 'holding' and time.time() - self._hold_start > self.hold_timeout:
            print("[tracker] Hold timeout — searching freely")
            self._state = 'searching'
            self._reset_smooth()

    def _lock_onto(self, frame, fx, fy, bw, bh):
        cv_tracker = _make_cv_tracker()
        if cv_tracker is None:
            self.bbox = (fx, fy, bw, bh)
            self.detected = True
            self._update_smooth(frame, fx, fy, bw, bh)
            return

        cv_tracker.init(frame, (fx, fy, bw, bh))
        self._cv_tracker = cv_tracker
        self._state = 'tracking'
        self.bbox = (fx, fy, bw, bh)
        self.detected = True
        print(f"[tracker] Locked on — bbox=({fx},{fy},{bw}x{bh})")
        self._update_smooth(frame, fx, fy, bw, bh)

    # ── Smooth center + gimbal ────────────────────────────────────────────────

    def _reset_smooth(self):
        self._smooth_cx = None
        self._smooth_cy = None
        self._smooth_bw = None
        self._smooth_bh = None

    def _update_smooth(self, frame, x, y, w, h):
        fh, fw = frame.shape[:2]
        cx, cy = x + w / 2, y + h / 2
        a = self.bbox_smooth_alpha

        if self._smooth_cx is None:
            self._smooth_cx, self._smooth_cy = cx, cy
            self._smooth_bw, self._smooth_bh = float(w), float(h)
        else:
            self._smooth_cx = a * cx + (1 - a) * self._smooth_cx
            self._smooth_cy = a * cy + (1 - a) * self._smooth_cy
            self._smooth_bw = a * w  + (1 - a) * self._smooth_bw
            self._smooth_bh = a * h  + (1 - a) * self._smooth_bh

        self.offset_x = (self._smooth_cx - fw / 2) / (fw / 2)
        self.offset_y = (self._smooth_cy - fh / 2) / (fh / 2)
        self._adjust_gimbal()

    def _apply_tracker_shift(self, frame, delta_cx: float, delta_cy: float = 0.0):
        """
        Called stream_delay seconds after a pan command.
        Shifts the tracked center to where the face should now appear and
        re-initializes the CSRT window there so it doesn't lose the face.
        """
        if self._smooth_cx is None or self._smooth_bw is None:
            return

        fh, fw = frame.shape[:2]
        self._smooth_cx += delta_cx
        self._smooth_cy += delta_cy
        hw = self._smooth_bw / 2
        hh = self._smooth_bh / 2
        self._smooth_cx = max(hw, min(fw - hw, self._smooth_cx))
        self._smooth_cy = max(hh, min(fh - hh, self._smooth_cy))

        if self._state == 'tracking' and self._cv_tracker is not None:
            x = int(self._smooth_cx - self._smooth_bw / 2)
            y = int(self._smooth_cy - self._smooth_bh / 2)
            w, h = int(self._smooth_bw), int(self._smooth_bh)
            x = max(0, min(x, fw - w - 1))
            y = max(0, min(y, fh - h - 1))
            if w > 0 and h > 0:
                new_tracker = _make_cv_tracker()
                if new_tracker:
                    new_tracker.init(frame, (x, y, w, h))
                    self._cv_tracker = new_tracker
                    print(f"[tracker] Re-centered CSRT after gimbal move ({delta_cx:+.0f}px)")

    def _adjust_gimbal(self):
        now = time.time()
        elapsed = now - self._last_gimbal_cmd
        # Tilt target: bottom edge of box sits on the horizontal midline.
        # box_bottom_offset = how far the box's bottom edge is from frame center (normalized).
        # Positive → bottom is below center → tilt up to raise it.
        if self._smooth_bh and self._last_fh:
            # box_bottom_offset = normalized dist of box bottom from frame center
            box_bottom = self.offset_y + (self._smooth_bh / 2) / (self._last_fh / 2)
            tilt_error = box_bottom - self.tilt_target_y
        else:
            tilt_error = self.offset_y - self.tilt_target_y

        if elapsed < self.poll_interval:
            return

        pan_budget  = self.pan_max_rate  * elapsed
        tilt_budget = self.tilt_max_rate * elapsed
        moved = False

        # Pan: update target angle to bring face toward horizontal center
        if abs(self.offset_x) > self.dead_zone:
            desired = self.offset_x * 20.0 * self.pan_sign
            step    = max(-pan_budget, min(pan_budget, desired * self.pan_gain))
            new_angle = max(self.pan_min, min(self.pan_max, self._base_angle + step))
            if abs(new_angle - self._base_angle) >= 0.5:
                direction = "RIGHT" if step > 0 else "LEFT"
                print(f"[tracker] Pan {direction}: {self._base_angle:.1f}° → {new_angle:.1f}° (offset={self.offset_x:+.2f})")
                self._base_angle = new_angle  # ramp thread will glide to this

                # Schedule tracker re-position after stream catches up
                if self._last_fw:
                    delta_cx = -step * (self._last_fw / self.fov_h_degrees)
                    self._pending_shifts.append((delta_cx, 0.0, now + self.stream_delay))

                moved = True

        # Tilt: update target μs so box bottom sits at tilt_target_y
        if abs(tilt_error) > self.dead_zone:
            desired = tilt_error * 500.0 * self.tilt_sign
            step    = max(-tilt_budget, min(tilt_budget, desired * self.tilt_gain))
            new_us  = max(self.tilt_min, min(self.tilt_max, self._cam_us + step))
            if abs(new_us - self._cam_us) >= 5.0:
                direction = "DOWN" if step > 0 else "UP"
                print(f"[tracker] Tilt {direction}: {self._cam_us:.0f}μs → {new_us:.0f}μs (tilt_err={tilt_error:+.2f})")
                self._cam_us = new_us  # ramp thread will glide to this

                # Schedule predicted cy shift after stream delay
                if self._last_fh:
                    # Tilt step (μs) → estimated pixel shift in cy
                    # Rough: full servo range 2200μs ≈ vertical FOV pixels
                    fov_v = self.fov_h_degrees * (self._last_fh / (self._last_fw or self._last_fh))
                    delta_cy = -step * (self._last_fh / (2200 / (fov_v / 90)))
                    self._pending_shifts.append((0.0, delta_cy, now + self.stream_delay))

                moved = True

        if moved:
            self._last_gimbal_cmd = now

    def _run_gimbal_ramp(self):
        """
        Dedicated thread: smoothly moves physical servos toward the targets
        (_base_angle, _cam_us) set by _adjust_gimbal. Runs at 20 Hz so motion
        is fluid rather than a single discrete jump.
        """
        tick = 0.05  # 20 Hz
        while not self._stop_event.is_set():
            max_da = self.ramp_pan_rate  * tick
            max_du = self.ramp_tilt_rate * tick

            da = self._base_angle - self._phys_angle
            du = self._cam_us     - self._phys_us

            if abs(da) >= 0.2:
                self._phys_angle += max(-max_da, min(max_da, da))
                self.arduino.gimbal_base_angle(int(round(self._phys_angle)))

            if abs(du) >= 2.0:
                self._phys_us += max(-max_du, min(max_du, du))
                self.arduino.gimbal_cam_us(int(round(self._phys_us)))

            time.sleep(tick)
