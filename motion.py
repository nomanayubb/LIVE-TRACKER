#!/usr/bin/env python3
"""
MOTION - high-FPS region capture with a frame ring buffer, for motion-blur
and any other work that needs real consecutive frames rather than structural
summaries.

Different performance profile from the UI tracker, so it lives separately:
the tracker samples cheaply and infrequently because UI state changes slowly;
this captures a fixed region as fast as the OS allows and keeps real pixels in
memory.

Measured capture ceiling on this machine (mss, no DXGI available offline):
    full screen 1920x1080 -> ~34ms  (~29 fps)
    any smaller region    -> ~18ms  (~52 fps)
The ~18ms floor is mss's fixed per-grab overhead, NOT proportional to pixel
count - so shrinking a region below ~960x540 buys nothing. Capture the region
you actually need and no more; going full-screen is what costs you.

Blur modes:
    accumulate  - weighted blend of the last N frames. Fast, and physically
                  what a real camera shutter does (light integrated over time).
    directional - optical flow estimates per-frame motion, then blurs along
                  that vector. Slower, but correct when the whole scene pans
                  (e.g. a spinning wheel) rather than just ghosting.

Usage:
    import motion
    cap = motion.RegionCapture(left=100, top=100, width=1280, height=720)
    cap.start()
    ...
    frame = cap.latest()                      # newest raw frame
    blurred = cap.motion_blur(frames=5)       # blur over last 5 frames
    cap.stop()

CLI:
    python motion.py bench                    # capture-rate benchmark
    python motion.py demo 1280 720 5          # capture + blur, save samples
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
OUT_DIR = BASE_DIR / "motion_out"
OUT_DIR.mkdir(exist_ok=True)


class RegionCapture:
    """Captures one screen region continuously on its own thread, keeping the
    most recent frames in memory. Nothing is written to disk unless you ask -
    disk I/O at 50fps would dominate the cost."""

    def __init__(self, left=0, top=0, width=1280, height=720, buffer_size=16):
        self.monitor = {"left": int(left), "top": int(top),
                        "width": int(width), "height": int(height)}
        self.frames = deque(maxlen=buffer_size)   # (timestamp, frame BGR)
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.captured = 0
        self.fps = 0.0

    # ------------------------------------------------------------ capture

    def _loop(self):
        intervals = deque(maxlen=30)
        last = time.perf_counter()
        with mss.mss() as sct:
            while not self._stop.is_set():
                raw = sct.grab(self.monitor)
                frame = np.asarray(raw)[:, :, :3]   # BGRA -> BGR view
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
        time.sleep(0.2)   # let the buffer fill a little
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def latest(self):
        with self.lock:
            return self.frames[-1][1].copy() if self.frames else None

    def recent(self, n):
        with self.lock:
            return [f.copy() for _, f in list(self.frames)[-n:]]

    # ------------------------------------------------------------ blur

    def motion_blur(self, frames=5, decay=0.65):
        """Weighted blend of the last N frames - newest weighted highest.

        This is what a physical shutter does: it integrates incoming light over
        the exposure window, so fast-moving objects smear while static ones
        stay sharp. Blending real consecutive frames therefore produces blur
        that matches the actual motion, instead of a uniform filter applied to
        one still image (which smears moving and static parts identically)."""
        buf = self.recent(frames)
        if not buf:
            return None
        if len(buf) == 1:
            return buf[0]
        weights = np.array([decay ** (len(buf) - 1 - i) for i in range(len(buf))], dtype=np.float32)
        weights /= weights.sum()
        acc = np.zeros_like(buf[-1], dtype=np.float32)
        for w, f in zip(weights, buf):
            if f.shape != acc.shape:
                continue
            acc += w * f.astype(np.float32)
        return np.clip(acc, 0, 255).astype(np.uint8)

    def directional_blur(self, frames=3, strength=1.0, max_kernel=31):
        """Estimate dominant motion with optical flow, then blur ALONG that
        direction. Use when the whole scene moves coherently (a spinning wheel,
        a pan) - accumulation alone ghosts in that case, this smears correctly."""
        buf = self.recent(frames)
        if len(buf) < 2:
            return buf[0] if buf else None
        g1 = cv2.cvtColor(buf[-2], cv2.COLOR_BGR2GRAY)
        g2 = cv2.cvtColor(buf[-1], cv2.COLOR_BGR2GRAY)
        small1 = cv2.resize(g1, (0, 0), fx=0.25, fy=0.25)
        small2 = cv2.resize(g2, (0, 0), fx=0.25, fy=0.25)
        flow = cv2.calcOpticalFlowFarneback(small1, small2, None, 0.5, 2, 15, 2, 5, 1.2, 0)
        dx = float(np.mean(flow[..., 0])) * 4.0 * strength
        dy = float(np.mean(flow[..., 1])) * 4.0 * strength
        length = int(min(max(abs(dx), abs(dy)) * 2, max_kernel))
        if length < 3:
            return buf[-1]
        if length % 2 == 0:
            length += 1
        kernel = np.zeros((length, length), dtype=np.float32)
        angle = np.arctan2(dy, dx)
        cx = cy = length // 2
        for i in range(length):
            off = i - cx
            x = int(round(cx + off * np.cos(angle)))
            y = int(round(cy + off * np.sin(angle)))
            if 0 <= x < length and 0 <= y < length:
                kernel[y, x] = 1
        s = kernel.sum()
        if s == 0:
            return buf[-1]
        kernel /= s
        return cv2.filter2D(buf[-1], -1, kernel)

    def motion_mask(self, threshold=18):
        """Which pixels are actually moving - so blur can be applied only to
        them, keeping static UI (scoreboards, overlays, chrome) sharp."""
        buf = self.recent(2)
        if len(buf) < 2:
            return None
        d = cv2.absdiff(cv2.cvtColor(buf[-2], cv2.COLOR_BGR2GRAY),
                        cv2.cvtColor(buf[-1], cv2.COLOR_BGR2GRAY))
        mask = (d > threshold).astype(np.uint8) * 255
        return cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=1)

    def selective_blur(self, frames=5, threshold=18):
        """Blur ONLY the moving parts, leave everything static crisp. For a
        casino feed this keeps text/UI readable while the wheel or cards blur."""
        blurred = self.motion_blur(frames)
        mask = self.motion_mask(threshold)
        sharp = self.latest()
        if blurred is None or mask is None or sharp is None:
            return blurred if blurred is not None else sharp
        m3 = cv2.cvtColor(cv2.GaussianBlur(mask, (21, 21), 0), cv2.COLOR_GRAY2BGR) / 255.0
        return np.clip(sharp * (1 - m3) + blurred * m3, 0, 255).astype(np.uint8)

    def record_video(self, path=None, duration=10, fps=None, blur=None,
                     blur_frames=5, progress=True):
        """Record the region straight to a video file, live.

        Frames are written as they are captured, so nothing is reconstructed
        and nothing is lost to compression of a summary - this is the real
        pixels at full region resolution.

        blur: None | 'accumulate' | 'directional' | 'selective' - applies the
        blur per frame as it records, so the output video is already processed
        (no second pass needed).
        """
        if not self._thread or not self._thread.is_alive():
            self.start()
        path = Path(path) if path else (OUT_DIR / f"capture_{int(time.time())}.mp4")
        # If fps isn't given, use the rate actually being achieved so playback
        # runs at true speed rather than fast/slow motion.
        time.sleep(0.5)
        target_fps = fps or max(5.0, self.fps or 20.0)
        w, h = self.monitor["width"], self.monitor["height"]
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                                 target_fps, (w, h))
        if not writer.isOpened():
            raise RuntimeError(f"Could not open video writer for {path}")

        blur_fn = {"accumulate": lambda: self.motion_blur(blur_frames),
                   "directional": lambda: self.directional_blur(),
                   "selective": lambda: self.selective_blur(blur_frames)}.get(blur)

        end = time.time() + duration
        written, last_ts = 0, None
        interval = 1.0 / target_fps
        while time.time() < end:
            with self.lock:
                item = self.frames[-1] if self.frames else None
            if item and item[0] != last_ts:
                last_ts = item[0]
                frame = blur_fn() if blur_fn else item[1]
                if frame is not None and frame.shape[:2] == (h, w):
                    writer.write(frame)
                    written += 1
            time.sleep(interval * 0.4)
        writer.release()
        if progress:
            actual_fps = written / max(duration, 0.001)
            print(f"wrote {written} frames ({actual_fps:.1f} fps) -> {path}")
        return {"path": str(path), "frames": written, "fps": target_fps,
                "size": f"{w}x{h}", "blur": blur}

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


def demo(w=1280, h=720, blur_frames=5):
    print(f"capturing {w}x{h} for 3s...")
    cap = RegionCapture(0, 0, w, h).start()
    time.sleep(3)
    s = cap.stats()
    print("stats:", s)

    t0 = time.perf_counter()
    sharp = cap.latest()
    acc = cap.motion_blur(frames=blur_frames)
    t_acc = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    directional = cap.directional_blur()
    t_dir = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    selective = cap.selective_blur(frames=blur_frames)
    t_sel = (time.perf_counter() - t0) * 1000
    cap.stop()

    for label, img in [("sharp", sharp), ("blur_accumulate", acc),
                       ("blur_directional", directional), ("blur_selective", selective)]:
        if img is not None:
            cv2.imwrite(str(OUT_DIR / f"{label}.jpg"), img, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    print(f"accumulate blur : {t_acc:6.1f} ms")
    print(f"directional blur: {t_dir:6.1f} ms")
    print(f"selective blur  : {t_sel:6.1f} ms")
    print(f"saved to {OUT_DIR}")


def video(seconds=10, w=1280, h=720, blur=None):
    cap = RegionCapture(0, 0, w, h).start()
    try:
        info = cap.record_video(duration=seconds, blur=blur)
        print("stats:", cap.stats())
        return info
    finally:
        cap.stop()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "bench"
    if cmd == "bench":
        bench()
    elif cmd == "video":
        # python motion.py video 10 1280 720 selective
        video(int(sys.argv[2]) if len(sys.argv) > 2 else 10,
              int(sys.argv[3]) if len(sys.argv) > 3 else 1280,
              int(sys.argv[4]) if len(sys.argv) > 4 else 720,
              sys.argv[5] if len(sys.argv) > 5 else None)
    else:
        demo(int(sys.argv[2]) if len(sys.argv) > 2 else 1280,
             int(sys.argv[3]) if len(sys.argv) > 3 else 720,
             int(sys.argv[4]) if len(sys.argv) > 4 else 5)
