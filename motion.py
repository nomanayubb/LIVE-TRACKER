#!/usr/bin/env python3
"""
MOTION - high frame-rate region capture with an in-memory frame buffer.

Purpose: give any downstream pipeline a stream of real, consecutive frames
from a screen region, as fast as the OS allows, with no screenshot step and
no disk round-trip. It hands you raw numpy frames - what you do with them
(processing, analysis, your own pipeline) is entirely up to you.

Separate from dual_tracker.py because it's a different performance profile:
the tracker samples cheaply and infrequently because UI state changes slowly;
this grabs one fixed region continuously and keeps real pixels in memory.

Measured capture ceiling on this machine (mss; DXGI/dxcam unavailable offline):
    1920x1080 full screen -> ~22 fps
    1280x720              -> ~47 fps
    960x540               -> ~56 fps
The ~18ms per-grab cost is FIXED overhead, not proportional to pixel count -
so full-screen is what hurts (34ms), and shrinking below ~960x540 buys
nothing. Capture only the region you actually need.

Usage:
    import motion
    cap = motion.RegionCapture(left=100, top=100, width=1280, height=720).start()
    frame = cap.latest()          # newest frame, BGR numpy array
    frames = cap.recent(5)        # last 5 consecutive frames
    moving = cap.motion_mask()    # which pixels changed between last two frames
    cap.stop()

CLI:
    python motion.py bench        # capture-rate benchmark
"""
import ctypes
ctypes.windll.user32.SetProcessDPIAware()

import sys
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import mss
import numpy as np

BASE_DIR = Path(__file__).resolve().parent


class RegionCapture:
    """Captures one screen region continuously on its own thread, keeping the
    most recent frames in memory. Nothing is written to disk - disk I/O at
    50fps would dominate the cost and defeat the point."""

    def __init__(self, left=0, top=0, width=1280, height=720, buffer_size=16):
        self.monitor = {"left": int(left), "top": int(top),
                        "width": int(width), "height": int(height)}
        self.frames = deque(maxlen=buffer_size)   # (timestamp, BGR frame)
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.captured = 0
        self.fps = 0.0

    def _loop(self):
        intervals = deque(maxlen=30)
        last = time.perf_counter()
        with mss.mss() as sct:
            while not self._stop.is_set():
                raw = sct.grab(self.monitor)
                frame = np.asarray(raw)[:, :, :3]
                now = time.perf_counter()
                with self.lock:
                    self.frames.append((now, frame.copy()))
                    self.captured += 1
                intervals.append(now - last)
                last = now
                if intervals:
                    self.fps = round(1.0 / max(sum(intervals) / len(intervals), 1e-6), 1)

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        time.sleep(0.2)          # let the buffer fill a little
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def latest(self):
        """Newest captured frame as a BGR numpy array, or None."""
        with self.lock:
            return self.frames[-1][1].copy() if self.frames else None

    def recent(self, n):
        """The last n consecutive frames, oldest first."""
        with self.lock:
            return [f.copy() for _, f in list(self.frames)[-n:]]

    def recent_with_times(self, n):
        """Same as recent() but each item is (timestamp, frame) - use when the
        exact interval between frames matters."""
        with self.lock:
            return [(t, f.copy()) for t, f in list(self.frames)[-n:]]

    def motion_mask(self, threshold=18):
        """Which pixels actually changed between the last two frames. Pure
        detection - tells a downstream pipeline where the movement is."""
        buf = self.recent(2)
        if len(buf) < 2:
            return None
        d = cv2.absdiff(cv2.cvtColor(buf[-2], cv2.COLOR_BGR2GRAY),
                        cv2.cvtColor(buf[-1], cv2.COLOR_BGR2GRAY))
        mask = (d > threshold).astype(np.uint8) * 255
        return cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=1)

    def motion_amount(self, threshold=18):
        """How much of the region is moving, as a percentage."""
        mask = self.motion_mask(threshold)
        if mask is None:
            return 0.0
        return round(100.0 * np.count_nonzero(mask) / mask.size, 2)

    def stats(self):
        with self.lock:
            n = len(self.frames)
            span = (self.frames[-1][0] - self.frames[0][0]) if n > 1 else 0
        return {"captured": self.captured, "buffered": n,
                "fps": self.fps, "buffer_span_s": round(span, 2),
                "region": self.monitor}


def bench():
    print("capture-rate benchmark (5s per region)\n")
    for name, (w, h) in [("full 1920x1080", (1920, 1080)),
                         ("video 1280x720", (1280, 720)),
                         ("half 960x540", (960, 540))]:
        cap = RegionCapture(0, 0, w, h).start()
        time.sleep(5)
        s = cap.stats()
        cap.stop()
        print(f"{name:16s} {s['fps']:6.1f} fps   ({s['captured']} frames in 5s)")


if __name__ == "__main__":
    bench()
