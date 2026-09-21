#!/usr/bin/env python3
"""
FULL TRACKER - comprehensive real-time screen awareness.
Builds on universal_tracker.py with 20 additional signals: mouse position,
process identity, window state/geometry, multi-monitor info, OCR with
bounding boxes, frame-change detection, selection-outline (color blob)
detection, idle time, process-alive checks, rolling history log, real
achieved frame rate, periodic screenshot buffer, JSON output, new-text
diffing, clipboard, resolution/DPI, new-window detection, dominant color,
and region-of-interest capture. No focus-stealing beyond one-time
activation on target change or explicit refresh request (same policy as
universal_tracker.py, proven necessary to avoid disrupting the user).
"""
import time
import json
import ctypes
import sys
import ast
from pathlib import Path
from collections import deque

import mss
import cv2
import numpy as np
import pygetwindow as gw
import pyautogui

OUTPUT_FILE = Path.home() / ".live_screen_state.txt"
JSON_FILE = Path.home() / ".live_screen_state.json"
HISTORY_LOG = Path.home() / ".tracker_history.log"
WINDOW_CONFIG = Path.home() / ".tracker_window_config.txt"
REFRESH_SIGNAL = Path.home() / ".tracker_refresh"
SNAPSHOT_DIR = Path.home() / "tracker_snapshots"
SNAPSHOT_DIR.mkdir(exist_ok=True)
MAX_SNAPSHOTS = 20

user32 = ctypes.windll.user32

# ---------- lightweight ctypes helpers (no pywin32/psutil needed) ----------

def get_cursor_pos():
    pt = ctypes.wintypes.POINT() if hasattr(ctypes, "wintypes") else None
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
    idle_ms = millis_since_boot - lii.dwTime
    return round(idle_ms / 1000.0, 1)

def get_process_name_for_hwnd(hwnd):
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h_process:
        return "Unknown", pid.value
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = ctypes.c_ulong(260)
        psapi = ctypes.windll.kernel32
        ok = psapi.QueryFullProcessImageNameW(h_process, 0, buf, ctypes.byref(size))
        name = Path(buf.value).name if ok else "Unknown"
        return name, pid.value
    finally:
        kernel32.CloseHandle(h_process)

_clipboard_root = None

def get_clipboard_text():
    """Reuses one hidden Tk root for the whole run - creating a new Tk()
    instance every frame was the dominant cost in early testing (~9s/frame)."""
    global _clipboard_root
    try:
        import tkinter as tk
        if _clipboard_root is None:
            _clipboard_root = tk.Tk()
            _clipboard_root.withdraw()
        try:
            text = _clipboard_root.clipboard_get()
        except Exception:
            text = ""
        return text[:200]
    except Exception:
        return ""

# ---------- vision helpers ----------

def get_all_windows():
    try:
        return [w for w in gw.getAllWindows() if w.title.strip()]
    except Exception:
        return []

def brightness_of(img):
    gray = cv2.cvtColor(img[:, :, :3], cv2.COLOR_RGB2GRAY)
    return int(np.mean(gray))

def dominant_color(img):
    small = cv2.resize(img[:, :, :3], (40, 30), interpolation=cv2.INTER_AREA)
    avg = small.reshape(-1, 3).mean(axis=0).astype(int)
    return f"rgb({avg[2]},{avg[1]},{avg[0]})"  # BGR->RGB order for mss frames

def detect_selection_outline(img):
    """Detect orange/highlight-colored regions (Blender's selection color,
    and similar highlight colors in other apps). Returns count of blobs
    and approximate center of the largest one."""
    try:
        bgr = img[:, :, :3].astype(np.uint8)
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        lower = np.array([5, 150, 150])
        upper = np.array([25, 255, 255])
        mask = cv2.inRange(hsv, lower, upper)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = [c for c in contours if cv2.contourArea(c) > 15]
        if not contours:
            return 0, None
        largest = max(contours, key=cv2.contourArea)
        M = cv2.moments(largest)
        if M["m00"] == 0:
            return len(contours), None
        cx, cy = int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"])
        return len(contours), (cx, cy)
    except Exception:
        return 0, None

def frame_change_pct(prev_gray, curr_gray):
    if prev_gray is None or prev_gray.shape != curr_gray.shape:
        return 0.0
    diff = cv2.absdiff(prev_gray, curr_gray)
    changed = np.count_nonzero(diff > 25)
    return round(100.0 * changed / diff.size, 2)

def extract_ocr(img, reader):
    """OCR with bounding boxes. Returns (plain_text, [{text,x,y,w,h}, ...])"""
    try:
        h, w = img.shape[:2]
        scale = 1.0
        proc = img
        if h > 1080:
            scale = 1080 / h
            proc = cv2.resize(img, (int(w * scale), 1080), interpolation=cv2.INTER_LINEAR)
        results = reader.readtext(proc, detail=1) if reader else []
        boxes = []
        texts = []
        for bbox, text, conf in results:
            xs = [p[0] / scale for p in bbox]
            ys = [p[1] / scale for p in bbox]
            boxes.append({
                "text": text, "conf": round(float(conf), 2),
                "x": int(min(xs)), "y": int(min(ys)),
                "w": int(max(xs) - min(xs)), "h": int(max(ys) - min(ys))
            })
            texts.append(text)
        return " | ".join(texts)[:800], boxes
    except Exception as e:
        return f"OCR error: {e}", []

def force_activate(window):
    try:
        window.minimize()
        window.restore()
        return True
    except Exception:
        pass
    try:
        window.activate()
        return True
    except Exception:
        return False

def init_easyocr():
    try:
        import easyocr
        print("Loading EasyOCR...")
        try:
            return easyocr.Reader(['en'], gpu=True)
        except Exception:
            return easyocr.Reader(['en'], gpu=False)
    except Exception as e:
        print(f"EasyOCR unavailable: {e}")
        return None

# ---------- main loop ----------

def run(target_window_name=None):
    print("=" * 60)
    print("FULL TRACKER - 20-signal real-time awareness")
    print("=" * 60)
    reader = init_easyocr()

    current_target = target_window_name
    last_activated = None
    prev_gray = None
    prev_fg_title = None
    prev_window_titles = set()
    last_ocr_boxes = []
    last_ocr_text_tokens = set()
    frame_times = deque(maxlen=30)
    iteration = 0
    last_snapshot_time = 0

    with mss.mss() as sct:
        monitor_count = len(sct.monitors) - 1  # index 0 is "all monitors" combined

        while True:
            loop_start = time.time()
            try:
                iteration += 1

                # live target switching via config file
                try:
                    if WINDOW_CONFIG.exists():
                        new_target = WINDOW_CONFIG.read_text(encoding='utf-8').strip()
                        if new_target and new_target != current_target:
                            current_target = new_target
                            print(f"Switched target to: {current_target}")
                except Exception:
                    pass

                all_windows = get_all_windows()
                window_titles = {w.title[:40] for w in all_windows}
                window_list = sorted(window_titles)
                new_windows = list(window_titles - prev_window_titles)
                prev_window_titles = window_titles

                if not current_target and all_windows:
                    current_target = all_windows[0].title

                # ground truth: what's really in foreground
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
                                try:
                                    REFRESH_SIGNAL.unlink()
                                except Exception:
                                    pass
                            time.sleep(0.3)
                            target_window = [w for w in get_all_windows() if current_target in w.title][0]

                monitor = sct.monitors[1]
                if target_window:
                    monitor = {
                        'top': max(0, target_window.top), 'left': max(0, target_window.left),
                        'width': max(100, target_window.width), 'height': max(100, target_window.height)
                    }

                sct_img = sct.grab(monitor)
                img = np.array(sct_img)
                gray = cv2.cvtColor(img[:, :, :3], cv2.COLOR_RGB2GRAY)

                b = int(np.mean(gray))
                change_pct = frame_change_pct(prev_gray, gray)
                prev_gray = gray
                dom_color = dominant_color(img)
                blob_count, blob_center = detect_selection_outline(img)

                # OCR (heaviest step) - always run so TEXT_DATA / boxes stay current
                text, boxes = extract_ocr(img, reader)
                text_tokens = set(t.strip().lower() for t in text.split("|") if t.strip())
                new_text_tokens = list(text_tokens - last_ocr_text_tokens)
                last_ocr_text_tokens = text_tokens
                last_ocr_boxes = boxes

                mouse_x, mouse_y = get_cursor_pos()
                idle_s = get_idle_seconds()
                clipboard = get_clipboard_text()

                win_state = "normal"
                if target_window:
                    if target_window.isMinimized:
                        win_state = "minimized"
                    elif target_window.isMaximized:
                        win_state = "maximized"

                now = time.time()
                if now - last_snapshot_time > 3:
                    try:
                        from PIL import Image
                        snap_path = SNAPSHOT_DIR / f"snap_{int(now)}.png"
                        Image.fromarray(img[:, :, :3][:, :, ::-1]).save(snap_path)
                        last_snapshot_time = now
                        existing = sorted(SNAPSHOT_DIR.glob("snap_*.png"))
                        while len(existing) > MAX_SNAPSHOTS:
                            existing.pop(0).unlink(missing_ok=True)
                    except Exception:
                        pass

                loop_time = time.time() - loop_start
                frame_times.append(loop_time)
                avg_ms = round(1000 * sum(frame_times) / len(frame_times), 1)

                data = {
                    "timestamp": now, "iteration": iteration,
                    "target_window": current_target, "target_alive": target_alive,
                    "target_state": win_state,
                    "actual_foreground": fg_title, "foreground_process": fg_process,
                    "foreground_pid": fg_pid, "foreground_changed": fg_changed,
                    "available_windows": len(all_windows), "window_list": window_list,
                    "new_windows": new_windows,
                    "mouse_x": mouse_x, "mouse_y": mouse_y,
                    "idle_seconds": idle_s,
                    "monitor_count": monitor_count,
                    "screen_resolution": f"{monitor['width']}x{monitor['height']}",
                    "brightness": b, "dominant_color": dom_color,
                    "frame_change_pct": change_pct,
                    "selection_blob_count": blob_count, "selection_blob_center": blob_center,
                    "text_data": text, "ocr_boxes": boxes,
                    "new_text_tokens": new_text_tokens,
                    "clipboard": clipboard,
                    "avg_frame_ms": avg_ms,
                    "status": "Running"
                }

                with open(JSON_FILE, "w", encoding="utf-8") as jf:
                    json.dump(data, jf, ensure_ascii=False)

                txt = f"""TIMESTAMP: {now}
ITERATION: {iteration}
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
SELECTION_BLOBS: {blob_count} center={blob_center}
NEW_TEXT: {new_text_tokens[:10]}
CLIPBOARD: {clipboard[:80]}
AVG_FRAME_MS: {avg_ms}
TEXT_DATA: {text}
STATUS: Running
"""
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    f.write(txt)

                if iteration % 20 == 0:
                    print(f"[{iteration}] fg={fg_title[:20]} target={current_target} "
                          f"b={b} change%={change_pct} avg_ms={avg_ms}")

            except KeyboardInterrupt:
                print("Stopped by user")
                break
            except Exception as e:
                print(f"Loop error: {e}")
                time.sleep(0.1)

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    run(target)
