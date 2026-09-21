#!/usr/bin/env python3
"""
DUAL-LOOP TRACKER - fast pixel-level thread (~30-50ms, true near-real-time)
running in parallel with a slower OCR thread (~150-300ms, EasyOCR is
inherently CPU-heavy and cannot go faster than that on this machine).
The fast loop never waits on OCR - it merges whatever the latest completed
OCR result is and labels its own staleness (OCR_AGE_MS) so you always know
how fresh the text data is relative to the pixel data.

Adds a genuine pixel-grid matrix (real per-cell average color across the
whole frame, not just one brightness scalar) and a change bounding box
(WHERE on screen changed between frames, not just a percentage).
"""
import time
import json
import ctypes
import sys
import threading
from pathlib import Path
from collections import deque

import mss
import cv2
import numpy as np
import pygetwindow as gw

BASE_DIR = Path(__file__).resolve().parent  # everything lives inside this project folder, not scattered in home dir
OUTPUT_FILE = BASE_DIR / ".live_screen_state.txt"
JSON_FILE = BASE_DIR / ".live_screen_state.json"
HISTORY_LOG = BASE_DIR / ".tracker_history.log"
WINDOW_CONFIG = BASE_DIR / ".tracker_window_config.txt"
REFRESH_SIGNAL = BASE_DIR / ".tracker_refresh"
SNAPSHOT_DIR = BASE_DIR / "tracker_snapshots"
SNAPSHOT_DIR.mkdir(exist_ok=True)
MAX_SNAPSHOTS = 20
GRID_COLS, GRID_ROWS = 16, 9  # pixel-grid matrix resolution

user32 = ctypes.windll.user32

# ---------------- shared state between the two threads ----------------
lock = threading.Lock()
shared = {
    "frame": None,            # latest captured frame (numpy array, BGRA)
    "frame_ts": 0.0,
    "monitor": None,
    "target_window_obj": None,
    "ocr_text": "",
    "ocr_boxes": [],
    "ocr_ts": 0.0,
    "new_text_tokens": [],
    "stop": False,
}

# ---------------- ctypes helpers (no pywin32/psutil needed) ----------------

def get_cursor_pos():
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y

def get_idle_seconds():
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    user32.GetLastInputInfo(ctypes.byref(lii))
    millis_since_boot = ctypes.windll.kernel32.GetTickCount()
    return round((millis_since_boot - lii.dwTime) / 1000.0, 1)

def get_process_name_for_hwnd(hwnd):
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    kernel32 = ctypes.windll.kernel32
    h_process = kernel32.OpenProcess(0x1000, False, pid.value)
    if not h_process:
        return "Unknown", pid.value
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = ctypes.c_ulong(260)
        ok = kernel32.QueryFullProcessImageNameW(h_process, 0, buf, ctypes.byref(size))
        return (Path(buf.value).name if ok else "Unknown"), pid.value
    finally:
        kernel32.CloseHandle(h_process)

_clipboard_root = None
def get_clipboard_text():
    global _clipboard_root
    try:
        import tkinter as tk
        if _clipboard_root is None:
            _clipboard_root = tk.Tk()
            _clipboard_root.withdraw()
        try:
            return _clipboard_root.clipboard_get()[:200]
        except Exception:
            return ""
    except Exception:
        return ""

def get_all_windows():
    try:
        return [w for w in gw.getAllWindows() if w.title.strip()]
    except Exception:
        return []

def force_activate(window):
    try:
        window.minimize(); window.restore()
        return True
    except Exception:
        pass
    try:
        window.activate(); return True
    except Exception:
        return False

# ---------------- pixel-level vision (fast, no OCR) ----------------

def pixel_grid_matrix(img):
    """Real per-cell average color across the whole frame - a genuine
    compact numeric picture, not just one brightness scalar."""
    small = cv2.resize(img[:, :, :3], (GRID_COLS, GRID_ROWS), interpolation=cv2.INTER_AREA)
    # BGR -> RGB, flatten to list of [r,g,b] ints per cell, row-major
    grid = small[:, :, ::-1].astype(int).tolist()
    return grid

def dominant_color(img):
    small = cv2.resize(img[:, :, :3], (40, 30), interpolation=cv2.INTER_AREA)
    avg = small.reshape(-1, 3).mean(axis=0).astype(int)
    return f"rgb({avg[2]},{avg[1]},{avg[0]})"

def detect_selection_outline(img):
    try:
        hsv = cv2.cvtColor(img[:, :, :3].astype(np.uint8), cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([5, 150, 150]), np.array([25, 255, 255]))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = [c for c in contours if cv2.contourArea(c) > 15]
        if not contours:
            return 0, None
        largest = max(contours, key=cv2.contourArea)
        M = cv2.moments(largest)
        if M["m00"] == 0:
            return len(contours), None
        return len(contours), (int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"]))
    except Exception:
        return 0, None

def change_region(prev_gray, curr_gray):
    """Returns (change_pct, bbox) where bbox=(x,y,w,h) of the changed
    region, or None if nothing changed - tells you WHERE, not just how much."""
    if prev_gray is None or prev_gray.shape != curr_gray.shape:
        return 0.0, None
    diff = cv2.absdiff(prev_gray, curr_gray)
    mask = (diff > 25).astype(np.uint8)
    changed = int(np.count_nonzero(mask))
    pct = round(100.0 * changed / mask.size, 2)
    if changed == 0:
        return pct, None
    ys, xs = np.nonzero(mask)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max() - xs.min()), int(ys.max() - ys.min()))
    return pct, bbox

def init_easyocr():
    try:
        import easyocr
        print("Loading EasyOCR (background OCR thread)...")
        try:
            return easyocr.Reader(['en'], gpu=True)
        except Exception:
            return easyocr.Reader(['en'], gpu=False)
    except Exception as e:
        print(f"EasyOCR unavailable: {e}")
        return None

# ---------------- OCR thread (slow, ~150-300ms per pass, never blocks fast loop) ----------------

def ocr_worker():
    reader = init_easyocr()
    last_tokens = set()
    while not shared["stop"]:
        with lock:
            frame = shared["frame"]
            frame_ts = shared["frame_ts"]
        if frame is None or reader is None:
            time.sleep(0.05)
            continue
        try:
            h, w = frame.shape[:2]
            scale = 1080 / h if h > 1080 else 1.0
            proc = cv2.resize(frame, (int(w * scale), 1080), interpolation=cv2.INTER_LINEAR) if scale != 1.0 else frame
            results = reader.readtext(proc, detail=1)
            boxes, texts = [], []
            for bbox, text, conf in results:
                xs = [p[0] / scale for p in bbox]; ys = [p[1] / scale for p in bbox]
                boxes.append({"text": text, "conf": round(float(conf), 2),
                              "x": int(min(xs)), "y": int(min(ys)),
                              "w": int(max(xs) - min(xs)), "h": int(max(ys) - min(ys))})
                texts.append(text)
            text_joined = " | ".join(texts)[:800]
            tokens = set(t.strip().lower() for t in texts if t.strip())
            new_tokens = list(tokens - last_tokens)
            last_tokens = tokens
            with lock:
                shared["ocr_text"] = text_joined
                shared["ocr_boxes"] = boxes
                shared["ocr_ts"] = frame_ts
                shared["new_text_tokens"] = new_tokens
        except Exception as e:
            with lock:
                shared["ocr_text"] = f"OCR error: {e}"

# ---------------- fast pixel-capture thread (~30-50ms target) ----------------

def fast_worker(target_window_name):
    current_target = target_window_name
    last_activated = None
    prev_gray = None
    prev_fg_title = None
    prev_window_titles = set()
    frame_times = deque(maxlen=60)
    iteration = 0
    last_snapshot_time = 0

    with mss.mss() as sct:
        monitor_count = len(sct.monitors) - 1
        while not shared["stop"]:
            loop_start = time.time()
            try:
                iteration += 1
                try:
                    if WINDOW_CONFIG.exists():
                        new_target = WINDOW_CONFIG.read_text(encoding='utf-8').strip()
                        if new_target and new_target != current_target:
                            current_target = new_target
                except Exception:
                    pass

                all_windows = get_all_windows()
                window_titles = {w.title[:40] for w in all_windows}
                new_windows = list(window_titles - prev_window_titles)
                prev_window_titles = window_titles
                window_list = sorted(window_titles)

                if not current_target and all_windows:
                    current_target = all_windows[0].title

                try:
                    fg = gw.getActiveWindow()
                    fg_title = fg.title if fg else "Unknown"
                    fg_hwnd = fg._hWnd if fg else None
                except Exception:
                    fg_title, fg_hwnd = "Unknown", None

                fg_process, fg_pid = ("Unknown", 0)
                if fg_hwnd:
                    fg_process, fg_pid = get_process_name_for_hwnd(fg_hwnd)

                fg_changed = fg_title != prev_fg_title
                if fg_changed:
                    ts = time.strftime("%H:%M:%S")
                    try:
                        with open(HISTORY_LOG, "a", encoding="utf-8") as hf:
                            hf.write(f"[{ts}] FOREGROUND -> {fg_title} ({fg_process})\n")
                    except Exception:
                        pass
                prev_fg_title = fg_title

                target_window = None
                target_alive = False
                win_state = "normal"
                if current_target:
                    matching = [w for w in all_windows if current_target in w.title]
                    if matching:
                        target_window = matching[0]
                        target_alive = True
                        refresh_requested = REFRESH_SIGNAL.exists()
                        if current_target != last_activated or refresh_requested:
                            force_activate(target_window)
                            last_activated = current_target
                            if refresh_requested:
                                try: REFRESH_SIGNAL.unlink()
                                except Exception: pass
                            time.sleep(0.3)
                            refreshed = [w for w in get_all_windows() if current_target in w.title]
                            if refreshed:
                                target_window = refreshed[0]
                        if target_window.isMinimized: win_state = "minimized"
                        elif target_window.isMaximized: win_state = "maximized"

                monitor = sct.monitors[1]
                if target_window:
                    monitor = {'top': max(0, target_window.top), 'left': max(0, target_window.left),
                               'width': max(100, target_window.width), 'height': max(100, target_window.height)}

                sct_img = sct.grab(monitor)
                img = np.array(sct_img)
                gray = cv2.cvtColor(img[:, :, :3], cv2.COLOR_RGB2GRAY)
                b = int(np.mean(gray))
                change_pct, change_bbox = change_region(prev_gray, gray)
                prev_gray = gray
                dom_color = dominant_color(img)
                grid = pixel_grid_matrix(img)
                blob_count, blob_center = detect_selection_outline(img)
                mouse_x, mouse_y = get_cursor_pos()
                idle_s = get_idle_seconds()
                clipboard = get_clipboard_text()

                now = time.time()
                with lock:
                    shared["frame"] = img.copy()
                    shared["frame_ts"] = now
                    ocr_text = shared["ocr_text"]
                    ocr_boxes = shared["ocr_boxes"]
                    ocr_ts = shared["ocr_ts"]
                    new_text_tokens = shared["new_text_tokens"]

                if now - last_snapshot_time > 3:
                    try:
                        from PIL import Image
                        Image.fromarray(img[:, :, :3][:, :, ::-1]).save(SNAPSHOT_DIR / f"snap_{int(now)}.png")
                        last_snapshot_time = now
                        existing = sorted(SNAPSHOT_DIR.glob("snap_*.png"))
                        while len(existing) > MAX_SNAPSHOTS:
                            existing.pop(0).unlink(missing_ok=True)
                    except Exception:
                        pass

                loop_ms = (time.time() - loop_start) * 1000
                frame_times.append(loop_ms)
                avg_ms = round(sum(frame_times) / len(frame_times), 1)
                ocr_age_ms = round((now - ocr_ts) * 1000, 0) if ocr_ts else -1

                data = {
                    "timestamp": now, "iteration": iteration, "loop_ms": round(loop_ms, 1), "avg_loop_ms": avg_ms,
                    "target_window": current_target, "target_alive": target_alive, "target_state": win_state,
                    "actual_foreground": fg_title, "foreground_process": fg_process, "foreground_pid": fg_pid,
                    "foreground_changed": fg_changed, "available_windows": len(all_windows),
                    "window_list": window_list, "new_windows": new_windows,
                    "mouse_x": mouse_x, "mouse_y": mouse_y, "idle_seconds": idle_s,
                    "monitor_count": monitor_count, "screen_resolution": f"{monitor['width']}x{monitor['height']}",
                    "brightness": b, "dominant_color": dom_color,
                    "frame_change_pct": change_pct, "frame_change_bbox": change_bbox,
                    "selection_blob_count": blob_count, "selection_blob_center": blob_center,
                    "pixel_grid": grid, "grid_cols": GRID_COLS, "grid_rows": GRID_ROWS,
                    "text_data": ocr_text, "ocr_boxes": ocr_boxes, "ocr_age_ms": ocr_age_ms,
                    "new_text_tokens": new_text_tokens, "clipboard": clipboard, "status": "Running"
                }
                with open(JSON_FILE, "w", encoding="utf-8") as jf:
                    json.dump(data, jf, ensure_ascii=False)

                txt = f"""TIMESTAMP: {now}
ITERATION: {iteration}
LOOP_MS: {round(loop_ms,1)}  AVG_LOOP_MS: {avg_ms}
TARGET_WINDOW: {current_target}
TARGET_ALIVE: {target_alive}
TARGET_STATE: {win_state}
ACTUAL_FOREGROUND: {fg_title}
FOREGROUND_PROCESS: {fg_process} (PID {fg_pid})
FOREGROUND_CHANGED: {fg_changed}
AVAILABLE_WINDOWS: {len(all_windows)}
NEW_WINDOWS: {new_windows}
WINDOW_LIST: {window_list}
MOUSE: ({mouse_x}, {mouse_y})
IDLE_SECONDS: {idle_s}
MONITOR_COUNT: {monitor_count}
SCREEN_RESOLUTION: {data['screen_resolution']}
BRIGHTNESS: {b}
DOMINANT_COLOR: {dom_color}
FRAME_CHANGE_PCT: {change_pct}
FRAME_CHANGE_BBOX: {change_bbox}
SELECTION_BLOBS: {blob_count} center={blob_center}
PIXEL_GRID_{GRID_COLS}x{GRID_ROWS}: see JSON file for full matrix
OCR_AGE_MS: {ocr_age_ms}
NEW_TEXT: {new_text_tokens[:10]}
CLIPBOARD: {clipboard[:80]}
TEXT_DATA: {ocr_text}
STATUS: Running
"""
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    f.write(txt)

                if iteration % 40 == 0:
                    print(f"[{iteration}] fg={fg_title[:20]} loop_ms={loop_ms:.1f} avg={avg_ms} ocr_age={ocr_age_ms}ms")

            except Exception as e:
                print(f"Fast loop error: {e}")
                time.sleep(0.05)

if __name__ == "__main__":
    print("=" * 60)
    print("DUAL-LOOP TRACKER: fast pixel thread + parallel OCR thread")
    print("=" * 60)
    target = sys.argv[1] if len(sys.argv) > 1 else None
    ocr_thread = threading.Thread(target=ocr_worker, daemon=True)
    ocr_thread.start()
    try:
        fast_worker(target)
    except KeyboardInterrupt:
        shared["stop"] = True
        print("Stopped by user")
