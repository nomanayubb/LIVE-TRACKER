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
import queue
from pathlib import Path
from collections import deque

ctypes.windll.user32.SetProcessDPIAware()  # must run before any window/coord queries -
# without this, pyautogui's coordinate space can silently disagree with ctypes/mss's
# physical-pixel space by the display's DPI scale factor (e.g. asking to click (500,500)
# lands at (450,461) instead). Any script that clicks via pyautogui needs this too.

import mss
import cv2
import numpy as np
import pygetwindow as gw

import claude_activity as ca

BASE_DIR = Path(__file__).resolve().parent  # everything lives inside this project folder, not scattered in home dir
OUTPUT_FILE = BASE_DIR / ".live_screen_state.txt"
JSON_FILE = BASE_DIR / ".live_screen_state.json"
HISTORY_LOG = BASE_DIR / ".tracker_history.log"
WINDOW_CONFIG = BASE_DIR / ".tracker_window_config.txt"
REFRESH_SIGNAL = BASE_DIR / ".tracker_refresh"
SNAPSHOT_DIR = BASE_DIR / "tracker_snapshots"
SNAPSHOT_DIR.mkdir(exist_ok=True)
PROFILE = False   # set False to silence the per-section timing breakdown
PAUSE_SWITCH = BASE_DIR / ".tracker_paused"          # create this file to instantly pause all capture/reporting
DENIED_WINDOWS_FILE = BASE_DIR / ".tracker_denied_windows.txt"  # one window-title substring per line = never reported
STATS_FILE = BASE_DIR / ".tracker_stats.json"
CLICK_HISTORY = BASE_DIR / ".tracker_click_history.jsonl"   # every click, persisted
CHANGE_HISTORY = BASE_DIR / ".tracker_change_history.jsonl"  # append-only log of what
# text appeared/disappeared on screen over time - a replayable record, not just a snapshot
MAX_SNAPSHOTS = 60
FRAME_FILE = BASE_DIR / ".live_frame.jpg"
PRECISION_SWITCH = BASE_DIR / ".tracker_precision"   # create this file to enable precision mode
FULLSCREEN_SWITCH = BASE_DIR / ".tracker_fullscreen"  # create this file to force true monitor capture

def fullscreen_on():
    """True monitor capture, independent of any window. Without this, even
    'no target specified' still auto-picks whichever window happens to be
    first in Windows' enumeration order (see the fast_worker loop) - it is
    NOT the same as capturing the physical screen regardless of windows.
    Confirmed directly: with no target given, the tracker locked onto
    whatever window Windows listed first (it happened to be a maximized one,
    so the region looked full-screen, but would shrink the moment that
    window changed size or a different window became first in the list)."""
    try:
        return FULLSCREEN_SWITCH.exists()
    except Exception:
        return False

# Two capture profiles. NORMAL is the default and stays fast (~5-9ms loop).
# PRECISION is opt-in for when maximum detail matters, and deliberately costs
# more - measured: the 48x27 grid plus per-change full-frame encoding took the
# loop from ~90ms to ~188ms. It is never on unless explicitly switched on.
#
# Hard limit worth knowing: a true 1920x1080 frame is ~6.2MB of raw pixels.
# Encoding that into JSON every frame would be both larger and slower than
# just writing the image, so exact pixels live in FRAME_FILE (linked from the
# JSON as "exact_frame_png") rather than being inlined as numbers.
NORMAL_GRID = (16, 9)      # 144 cells  - structural summary, cheap
PRECISION_GRID = (48, 27)  # 1296 cells - genuine low-res image as data

def precision_on():
    try:
        return PRECISION_SWITCH.exists()
    except Exception:
        return False

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
    "ocr_pass_count": 0,
    "vision": {},            # structural scan results (rectangles, text regions, layout...)
    "vision_ts": 0.0,
    "vision_pass_count": 0,
    "stop": False,
}

# ---------------- ctypes helpers (no pywin32/psutil needed) ----------------

def get_cursor_pos():
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y

VK_LBUTTON, VK_RBUTTON = 0x01, 0x02

def mouse_buttons_down():
    """Polls real button state via GetAsyncKeyState - works for any click,
    real or synthetic (pyautogui), since both set the same OS-level key state."""
    left = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
    right = bool(user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000)
    return left, right

# ---------------- fast-poll click detection (own thread, pure polling) ----------------
#
# The original design polled GetAsyncKeyState once per main-loop tick (~90ms
# while tracking a busy window) - too slow to reliably catch a synthetic
# click's down->up cycle, which can complete in a few ms. A WH_MOUSE_LL hook
# would close that gap completely but INTERCEPTS system-wide mouse input,
# which is too dangerous (a bug in the hook callback froze the cursor for the
# whole system during testing - see git history).
#
# GetAsyncKeyState is different in kind, not just degree: it is a pure QUERY.
# It reads state; it never intercepts, blocks, or modifies the input pipeline,
# so a bug here can at worst miss a click - it cannot freeze anything or
# affect any other application. Running the SAME safe API on its own thread,
# polled every 2ms instead of once per ~90ms tick, closes most of the gap
# with none of the hook's risk. Verified in isolation before integrating
# here: 4 of 5 rapid synthetic clicks (50ms apart) were caught, versus 0 of 2
# with the old once-per-tick approach.
click_queue = queue.Queue()

def click_poll_worker(interval=0.002):
    prev_down = False
    while not shared["stop"]:
        down = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
        if down and not prev_down:
            x, y = get_cursor_pos()
            click_queue.put((time.time(), x, y, "left"))
        prev_down = down
        time.sleep(interval)

def window_at_point(x, y):
    """Which window/app is actually under this screen coordinate, and is it
    the foreground one or a background window? Answers 'the click landed in
    WHICH app' rather than just 'a click happened somewhere'."""
    try:
        hwnd = user32.WindowFromPoint(ctypes.wintypes.POINT(x, y)) if hasattr(ctypes, "wintypes") else None
    except Exception:
        hwnd = None
    if not hwnd:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        try:
            hwnd = user32.WindowFromPoint(POINT(x, y))
        except Exception:
            return {"title": "Unknown", "process": "Unknown", "pid": 0, "layer": "unknown"}
    try:
        # walk up to the top-level owner window so we get "Blender", not an inner child control
        GA_ROOT = 2
        root_hwnd = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        length = user32.GetWindowTextLengthW(root_hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(root_hwnd, buf, length + 1)
        proc, pid = get_process_name_for_hwnd(root_hwnd)
        # Many windows genuinely have no Win32 title text - confirmed common
        # for modern File Explorer frames and various background/system
        # windows (GetWindowText returns "" for them; their visible name, if
        # any, comes from a different UI layer entirely). "Untitled" told the
        # user nothing useful there. The process name at least says WHAT
        # clicked, even when Windows has no title string to offer for it.
        title = buf.value or f"({proc})"
        fg_hwnd = user32.GetForegroundWindow()
        layer = "foreground" if root_hwnd == fg_hwnd else "background"
        return {"title": title, "process": proc, "pid": pid, "layer": layer}
    except Exception:
        return {"title": "Unknown", "process": "Unknown", "pid": 0, "layer": "unknown"}

def attribute_click(x, y):
    """A real click was just detected at (x,y) - check if Claude logged a
    click near this position/time to attribute it, else it's the user's.
    Tolerance is wide (80px) because even with SetProcessDPIAware() there's
    a residual DPI-virtualization rounding gap between processes on this
    display - not worth chasing pixel-perfect alignment for attribution."""
    for ts, cx, cy, button in ca.get_recent_clicks(max_age=1.5):
        if abs(cx - x) < 80 and abs(cy - y) < 80:
            return "claude", round(time.time() - ts, 2)
    return "user", None

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

# The tracker's own UI windows. They get drawn on top of whatever is being
# tracked, so without this the tracker captures and OCRs its own status text
# recursively - pure wasted CPU, and it pollutes TEXT_DATA with its own output.
OWN_UI_TITLES = ("Tracker Overlay", "Tracker Admin Panel")

def is_own_ui(title):
    return any(t.lower() in (title or "").lower() for t in OWN_UI_TITLES)

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

def pixel_grid_matrix(img, cols=None, rows=None):
    """Real per-cell average color across the whole frame - a genuine
    compact numeric picture, not just one brightness scalar."""
    cols = cols or NORMAL_GRID[0]
    rows = rows or NORMAL_GRID[1]
    small = cv2.resize(img[:, :, :3], (cols, rows), interpolation=cv2.INTER_AREA)
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
        # Cap torch's thread pool BEFORE EasyOCR loads. By default PyTorch spawns
        # worker threads across every core; measured, that made the tracker consume
        # ~6 cores' worth of CPU (297 CPU-seconds in 50s wall time), starving the
        # fast pixel loop (7.5ms -> 251ms) and pushing OCR 15-23s behind. OCR is a
        # background concern here - it does not need the whole machine.
        try:
            import torch
            torch.set_num_threads(2)
        except Exception:
            pass
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

def vision_worker():
    """Third parallel loop: structural screen understanding WITHOUT OCR.
    Benchmarked at ~40-90ms per full scan, so it gets its own thread rather
    than blocking the ~15ms pixel loop - same reasoning as the OCR thread.
    Finds rectangles (buttons/panels), text regions (where text is, without
    reading it), lines, corners, layout dividers, and change grids."""
    import vision
    heavy_counter = 0
    # Rate limit: running this flat-out starved the fast loop (measured: fast
    # loop degraded 15ms -> 175ms and OCR fell 17s behind, because CPU-bound
    # OpenCV work competes for cores/GIL). Structural layout doesn't change
    # 20x/second, so ~4 scans/sec is plenty and leaves the other loops room.
    TARGET_INTERVAL = 0.25
    while not shared["stop"]:
        cycle_start = time.time()
        if PAUSE_SWITCH.exists():
            time.sleep(0.3)
            continue
        with lock:
            frame = shared["frame"]
            frame_ts = shared["frame_ts"]
        if frame is None:
            time.sleep(0.05)
            continue
        try:
            heavy_counter += 1
            prev = getattr(vision_worker, "_prev_gray", None)
            gray = cv2.cvtColor(frame[:, :, :3].astype(np.uint8), cv2.COLOR_BGR2GRAY)
            result = vision.scan_all(frame, prev_gray=prev,
                                     heavy=(heavy_counter % 10 == 0))  # deep pass occasionally
            vision_worker._prev_gray = gray
            with lock:
                shared["vision"] = result
                shared["vision_ts"] = frame_ts
                shared["vision_pass_count"] += 1
        except Exception as e:
            with lock:
                shared["vision"] = {"error": str(e)}
        # yield the CPU back to the fast/OCR loops for the rest of the interval
        time.sleep(max(0.02, TARGET_INTERVAL - (time.time() - cycle_start)))

def ocr_worker():
    reader = init_easyocr()
    last_tokens = set()
    last_phash = None
    OCR_MIN_INTERVAL = 0.4   # text doesn't change 10x/second; running back-to-back
                             # was pushing OCR 16-23s behind the live frame
    while not shared["stop"]:
        cycle_start = time.time()
        if PAUSE_SWITCH.exists():
            time.sleep(0.3)
            continue
        with lock:
            frame = shared["frame"]
            frame_ts = shared["frame_ts"]
        if frame is None or reader is None:
            time.sleep(0.05)
            continue
        try:
            # Skip the whole expensive OCR pass if the screen is visually unchanged
            # since the last one - on a static screen this saves ~100% of the cost.
            try:
                import vision as _v
                # Skip ONLY when the frame is bit-identical. Any single change on the
                # tracked window - one character, one pixel of new text - re-runs OCR,
                # so nothing that appears on screen is ever missed.
                phash = _v.perceptual_hash(frame, size=32)   # finer hash = more sensitive
                if last_phash is not None and _v.hamming_distance(phash, last_phash) == 0:
                    time.sleep(0.05)
                    continue
                last_phash = phash
            except Exception:
                pass

            h, w = frame.shape[:2]
            # Cap OCR input height at 720px - UI text stays legible and the pass is
            # far cheaper than at native 1080p+.
            scale = 720 / h if h > 720 else 1.0
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
            gone_tokens = list(last_tokens - tokens)

            # Append every text change to a timestamped history, so there's a
            # replayable record of what appeared/disappeared on screen over time,
            # not just a snapshot of the current state.
            if last_tokens and (new_tokens or gone_tokens):
                try:
                    entry = {
                        "t": round(time.time(), 2),
                        "clock": time.strftime("%H:%M:%S"),
                        "window": shared.get("ocr_window_label", ""),
                        "appeared": sorted(new_tokens)[:25],
                        "disappeared": sorted(gone_tokens)[:25],
                    }
                    with open(CHANGE_HISTORY, "a", encoding="utf-8") as hf:
                        hf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except Exception:
                    pass
            last_tokens = tokens
            with lock:
                shared["ocr_text"] = text_joined
                shared["ocr_boxes"] = boxes
                shared["ocr_ts"] = frame_ts
                shared["new_text_tokens"] = new_tokens
                shared["ocr_pass_count"] += 1
        except Exception as e:
            with lock:
                shared["ocr_text"] = f"OCR error: {e}"
        # hold to the minimum interval so OCR can't monopolise the CPU
        time.sleep(max(0.05, OCR_MIN_INTERVAL - (time.time() - cycle_start)))

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
    last_click_info = None
    pending_explorer_clicks = []   # explorer.exe clicks awaiting a foreground change to link to
    # Pixel capture (mss.grab ~33ms + cvtColor ~12ms) runs on this slower cadence;
    # the input/window signals below it run every tick at sub-millisecond cost.
    PIXEL_INTERVAL = 0.05
    last_pixel_time = 0.0
    cached_pixels = None
    last_frame_sig = None
    session_start = time.time()
    stats = {"started_at": session_start, "total_iterations": 0, "ocr_passes": 0,
              "clicks_detected": 0, "claude_clicks": 0, "user_clicks": 0,
              "denied_window_blocks": 0, "paused_ticks": 0}
    was_paused = False

    with mss.mss() as sct:
        monitor_count = len(sct.monitors) - 1
        while not shared["stop"]:
            loop_start = time.time()
            try:
                # PAUSE SWITCH: if this file exists, blank out everything immediately -
                # process stays alive (so removing the file resumes instantly), but no
                # real screen content, mouse position, or text is captured or reported.
                if PAUSE_SWITCH.exists():
                    was_paused = True
                    stats["paused_ticks"] += 1
                    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                        f.write(f"STATUS: PAUSED BY USER\nTIMESTAMP: {time.time()}\n"
                                f"All capture and reporting halted. Delete {PAUSE_SWITCH.name} to resume.\n")
                    with open(JSON_FILE, "w", encoding="utf-8") as jf:
                        json.dump({"status": "PAUSED", "timestamp": time.time()}, jf)
                    with lock:
                        shared["frame"] = None
                    if iteration % 20 == 0:
                        with open(STATS_FILE, "w", encoding="utf-8") as sf:
                            json.dump(stats, sf)
                    time.sleep(0.2)
                    continue
                if was_paused:
                    print("Resumed from pause.")
                    was_paused = False

                iteration += 1
                now_input = time.time()
                precision = precision_on()
                g_cols, g_rows = PRECISION_GRID if precision else NORMAL_GRID
                stats["total_iterations"] = iteration
                # WINDOW_CONFIG may name several windows, comma separated. The FIRST
                # is the primary target and gets the full treatment (pixel capture,
                # OCR, vision scan). The rest are watched cheaply - existence, state,
                # geometry, whether they're foreground - all sub-millisecond calls.
                # Doing full capture on every window would multiply the cost linearly;
                # this keeps multi-window watching essentially free.
                watch_list = []
                try:
                    if WINDOW_CONFIG.exists():
                        raw_cfg = WINDOW_CONFIG.read_text(encoding='utf-8').strip()
                        if raw_cfg:
                            parts = [p.strip() for p in raw_cfg.split(",") if p.strip()]
                            if parts:
                                if parts[0] != current_target:
                                    current_target = parts[0]
                                watch_list = parts[1:]
                except Exception:
                    pass

                denied_windows = []
                try:
                    if DENIED_WINDOWS_FILE.exists():
                        denied_windows = [l.strip() for l in DENIED_WINDOWS_FILE.read_text(encoding='utf-8').splitlines() if l.strip()]
                except Exception:
                    pass

                _t0 = time.perf_counter()
                all_windows = get_all_windows()
                t_windows = time.perf_counter() - _t0
                window_titles = {w.title[:40] for w in all_windows}
                new_windows = list(window_titles - prev_window_titles)
                prev_window_titles = window_titles
                window_list = sorted(window_titles)

                if not current_target and all_windows:
                    current_target = all_windows[0].title

                _t0 = time.perf_counter()
                try:
                    fg = gw.getActiveWindow()
                    fg_title = fg.title if fg else "Unknown"
                    fg_hwnd = fg._hWnd if fg else None
                except Exception:
                    fg_title, fg_hwnd = "Unknown", None

                fg_process, fg_pid = ("Unknown", 0)
                if fg_hwnd:
                    fg_process, fg_pid = get_process_name_for_hwnd(fg_hwnd)
                t_fg = time.perf_counter() - _t0

                fg_changed = fg_title != prev_fg_title
                if fg_changed:
                    ts = time.strftime("%H:%M:%S")
                    try:
                        with open(HISTORY_LOG, "a", encoding="utf-8") as hf:
                            hf.write(f"[{ts}] FOREGROUND -> {fg_title} ({fg_process})\n")
                    except Exception:
                        pass
                prev_fg_title = fg_title

                # Cheap per-window status for every secondary watched window.
                watched = []
                for wname in watch_list:
                    match = next((w for w in all_windows if wname.lower() in w.title.lower()), None)
                    if match is None:
                        watched.append({"name": wname, "alive": False})
                        continue
                    try:
                        st = ("minimized" if match.isMinimized else
                              "maximized" if match.isMaximized else "normal")
                        watched.append({
                            "name": wname, "alive": True, "title": match.title[:50],
                            "state": st, "foreground": match.title == fg_title,
                            "x": match.left, "y": match.top,
                            "w": match.width, "h": match.height,
                        })
                    except Exception:
                        watched.append({"name": wname, "alive": True, "state": "unknown"})

                target_window = None
                target_alive = False
                win_state = "normal"
                access_denied = any(d.lower() in (current_target or "").lower() for d in denied_windows)
                if access_denied:
                    stats["denied_window_blocks"] += 1
                if current_target and not access_denied:
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

                if access_denied:
                    # Explicit denial - do NOT fall back to full-screen capture (that would
                    # leak the denied window's content anyway if it's on screen). Skip
                    # capture entirely and report the denial plainly.
                    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                        f.write(f"STATUS: ACCESS DENIED BY USER\nTARGET_WINDOW: {current_target}\n"
                                f"TIMESTAMP: {time.time()}\nRemove its entry from {DENIED_WINDOWS_FILE.name} to restore access.\n")
                    with open(JSON_FILE, "w", encoding="utf-8") as jf:
                        json.dump({"status": "ACCESS_DENIED", "target_window": current_target, "timestamp": time.time()}, jf)
                    time.sleep(0.1)
                    continue

                monitor = sct.monitors[1]
                force_fullscreen = fullscreen_on()
                if target_window and not force_fullscreen:
                    monitor = {'top': max(0, target_window.top), 'left': max(0, target_window.left),
                               'width': max(100, target_window.width), 'height': max(100, target_window.height)}
                # force_fullscreen leaves monitor as sct.monitors[1] - the true
                # physical screen - regardless of any window, auto-picked or not.

                # Pixel capture is the expensive part - measured mss.grab at ~33ms and
                # full-res cvtColor at ~12ms, vs <1ms for every non-pixel signal
                # (mouse/window/process/clicks). So pixel work runs on its own slower
                # cadence while the input/window signals stay truly millisecond-fresh.
                _t0 = time.perf_counter()
                do_pixels = (now_input - last_pixel_time) >= PIXEL_INTERVAL
                if do_pixels:
                    last_pixel_time = now_input
                    sct_img = sct.grab(monitor)
                    img = np.array(sct_img)
                    # grayscale on a downscaled copy - 12ms full-res vs ~1ms small,
                    # and nothing downstream here needs full-res grey
                    small_bgr = cv2.resize(img[:, :, :3], (0, 0), fx=0.4, fy=0.4,
                                           interpolation=cv2.INTER_AREA)
                    gray = cv2.cvtColor(small_bgr, cv2.COLOR_RGB2GRAY)
                    b = int(np.mean(gray))
                    change_pct, change_bbox = change_region(prev_gray, gray)
                    if change_bbox:  # scale bbox back to true screen coordinates
                        change_bbox = tuple(int(v / 0.4) for v in change_bbox)
                    prev_gray = gray
                    # Black out our own overlay/admin windows where they overlap the
                    # captured region, so OCR and vision don't recursively read the
                    # tracker's own status text back into TEXT_DATA.
                    for ow in all_windows:
                        if not is_own_ui(ow.title):
                            continue
                        try:
                            ox = ow.left - monitor['left']
                            oy = ow.top - monitor['top']
                            x0, y0 = max(0, ox), max(0, oy)
                            x1 = min(img.shape[1], ox + ow.width)
                            y1 = min(img.shape[0], oy + ow.height)
                            if x1 > x0 and y1 > y0:
                                img[y0:y1, x0:x1] = 0
                        except Exception:
                            pass

                    cached_pixels = (img, b, change_pct, change_bbox)
                elif cached_pixels is not None:
                    img, b, change_pct, change_bbox = cached_pixels
                else:
                    time.sleep(0.005)
                    continue
                dom_color = dominant_color(img)
                grid = pixel_grid_matrix(img, g_cols, g_rows)
                blob_count, blob_center = detect_selection_outline(img)
                mouse_x, mouse_y = get_cursor_pos()
                idle_s = get_idle_seconds()
                _t0 = time.perf_counter()
                clipboard = get_clipboard_text()
                t_clip = time.perf_counter() - _t0
                claude_activity = ca.get_activity()

                # Drain EVERY click queued by click_poll_worker (a dedicated
                # thread polling GetAsyncKeyState every 2ms - see that
                # function's docstring for why this replaced once-per-tick
                # polling here). Processing all queued items, not just one,
                # means a burst of rapid clicks across several windows between
                # main-loop ticks is never coalesced into a single event.
                # Each click uses ITS OWN recorded (x,y), not the current
                # mouse position, since the cursor may have moved on by the
                # time a backlog is processed.
                while True:
                    try:
                        click_ts, cx, cy, button = click_queue.get_nowait()
                    except queue.Empty:
                        break
                    source, claude_delay = attribute_click(cx, cy)
                    target_app = window_at_point(cx, cy)
                    last_click_info = {
                        "x": cx, "y": cy,
                        "by": source,                      # "claude" or "user"
                        "claude_delay_s": claude_delay,
                        "app_title": target_app["title"],  # WHICH app was clicked
                        "app_process": target_app["process"],
                        "app_pid": target_app["pid"],
                        "app_layer": target_app["layer"],  # foreground or background window
                        "at": click_ts,
                    }
                    stats["clicks_detected"] += 1
                    stats[f"{source}_clicks"] += 1
                    # Persist every click - the live state file is overwritten
                    # constantly, so without this the event is gone within ms.
                    try:
                        with open(CLICK_HISTORY, "a", encoding="utf-8") as cf:
                            cf.write(json.dumps({**last_click_info,
                                                 "clock": time.strftime("%H:%M:%S", time.localtime(click_ts))},
                                                ensure_ascii=False) + "\n")
                    except Exception:
                        pass
                    per_app = stats.setdefault("clicks_per_app", {})
                    key = f"{target_app['title'][:30]} [{source}]"
                    per_app[key] = per_app.get(key, 0) + 1

                    # Both the taskbar and the desktop are owned by
                    # explorer.exe, so a click launching/switching to an app
                    # via either one is correctly attributed to explorer.exe
                    # at click time - but that tells you nothing about WHICH
                    # app the icon represented. Track it and link it to
                    # whatever becomes foreground shortly after.
                    if target_app["process"].lower() == "explorer.exe":
                        pending_explorer_clicks.append({**last_click_info, "click_ts": click_ts})

                # Resolve any pending explorer.exe click whose target app has
                # now become foreground - write a follow-up record linking
                # them, and drop entries that waited too long (that click
                # likely hit empty desktop/taskbar space, not an app icon).
                if pending_explorer_clicks and fg_changed and fg_title and fg_process.lower() != "explorer.exe":
                    still_pending = []
                    for pc in pending_explorer_clicks:
                        age = time.time() - pc["click_ts"]
                        if age > 3.0:
                            continue  # too late to be this click's result - drop it
                        if age < 0.05:
                            still_pending.append(pc)  # too soon, foreground may not have settled
                            continue
                        try:
                            with open(CLICK_HISTORY, "a", encoding="utf-8") as cf:
                                cf.write(json.dumps({
                                    "type": "taskbar_or_desktop_launch",
                                    "icon_click_x": pc["x"], "icon_click_y": pc["y"],
                                    "by": pc["by"], "switched_to_app": fg_title,
                                    "switched_to_process": fg_process,
                                    "delay_s": round(age, 2),
                                    "at": time.time(),
                                    "clock": time.strftime("%H:%M:%S"),
                                }, ensure_ascii=False) + "\n")
                        except Exception:
                            pass
                    pending_explorer_clicks[:] = still_pending
                elif pending_explorer_clicks:
                    pending_explorer_clicks[:] = [
                        pc for pc in pending_explorer_clicks if time.time() - pc["click_ts"] <= 3.0
                    ]

                now = time.time()
                with lock:
                    shared["frame"] = img.copy()
                    shared["frame_ts"] = now
                    shared["ocr_window_label"] = current_target or fg_title
                    ocr_text = shared["ocr_text"]
                    ocr_boxes = shared["ocr_boxes"]
                    ocr_ts = shared["ocr_ts"]
                    new_text_tokens = shared["new_text_tokens"]
                    vision_result = shared["vision"]
                    vision_ts = shared["vision_ts"]

                # Keep an exact-pixel image of the current frame on disk, so the JSON
                # report and the true original pixels are both available without a
                # separate screenshot step. Written only when the frame actually
                # changed, and as JPEG: PNG-encoding 1920x1080 every tick measured at
                # ~100ms+ (loop went 90 -> 188ms), JPEG q92 is a fraction of that.
                if do_pixels and precision:
                    try:
                        cur_sig = int(gray.sum())
                        if cur_sig != last_frame_sig:
                            last_frame_sig = cur_sig
                            cv2.imwrite(str(FRAME_FILE), img[:, :, :3],
                                        [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                    except Exception:
                        pass

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

                if PROFILE and iteration % 100 == 0:
                    print(f"[prof] windows={t_windows*1000:.1f} fg={t_fg*1000:.1f} "
                          f"clip={t_clip*1000:.1f} pixels={t_pixels*1000:.1f} "
                          f"write={t_write*1000:.1f} loop={loop_ms:.1f}")

                if iteration % 20 == 0:
                    with lock:
                        stats["ocr_passes"] = shared["ocr_pass_count"]
                    stats["uptime_seconds"] = round(time.time() - session_start, 1)
                    try:
                        with open(STATS_FILE, "w", encoding="utf-8") as sf:
                            json.dump(stats, sf)
                    except Exception:
                        pass

                t_pixels = time.perf_counter() - _t0
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
                    "pixel_grid": grid, "grid_cols": g_cols, "grid_rows": g_rows, "precision_mode": precision,
                    "fullscreen_mode": force_fullscreen,
                    "text_data": ocr_text, "ocr_boxes": ocr_boxes, "ocr_age_ms": ocr_age_ms,
                    "new_text_tokens": new_text_tokens, "clipboard": clipboard,
                    "claude_active": claude_activity["active"], "claude_window": claude_activity["window"],
                    "claude_action": claude_activity["action"], "claude_private": claude_activity["private"],
                    "last_click": last_click_info,
                    "exact_frame_png": str(FRAME_FILE) if precision else None,
                    "watched_windows": watched,
                    "vision": vision_result,
                    "vision_age_ms": round((now - vision_ts) * 1000, 0) if vision_ts else -1,
                    "status": "Running"
                }
                _t0 = time.perf_counter()
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
PIXEL_GRID_{g_cols}x{g_rows}: {g_cols*g_rows} cells of real averaged RGB (full matrix in .json)
OCR_AGE_MS: {ocr_age_ms}
NEW_TEXT: {new_text_tokens[:10]}
CLIPBOARD: {clipboard[:80]}
CLAUDE_ACTIVE: {claude_activity['active']}
CLAUDE_WINDOW: {claude_activity['window']}
CLAUDE_ACTION: {claude_activity['action']}
CLAUDE_PRIVATE: {claude_activity['private']}
LAST_CLICK: {last_click_info}
VISION_AGE_MS: {round((now - vision_ts) * 1000, 0) if vision_ts else -1}
VISION_RECTANGLES: {len(vision_result.get('rectangles', []))} clickable rects found
VISION_TEXT_REGIONS: {len(vision_result.get('text_regions', []))} text areas located (not read)
VISION_LINES: {len(vision_result.get('lines', []))}  CORNERS: {len(vision_result.get('corners', []))}
VISION_LAYOUT: {vision_result.get('layout', {})}
VISION_EDGE_DENSITY: {vision_result.get('edges', {}).get('total_edge_density_pct', '-')}%
VISION_CHANGED_CELLS: {len(vision_result.get('region_change', {}).get('changed_cells', []))}
VISION_PHASH: {vision_result.get('phash', '-')[:32]}
WATCHED_WINDOWS: {len(watched)} secondary {'| ' + ' | '.join(f"{w['name']}:{'alive/' + w.get('state','?') if w.get('alive') else 'CLOSED'}" for w in watched) if watched else ''}
PRECISION_MODE: {'ON' if precision else 'off (default - create .tracker_precision to enable)'}
EXACT_FRAME: {FRAME_FILE if precision else '- (precision mode off)'}
TEXT_DATA: {ocr_text}
STATUS: Running
"""
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    f.write(txt)
                t_write = time.perf_counter() - _t0

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
    vision_thread = threading.Thread(target=vision_worker, daemon=True)
    vision_thread.start()
    click_thread = threading.Thread(target=click_poll_worker, daemon=True)
    click_thread.start()
    print("Threads: fast pixel loop (~15ms) | vision scan (~40-90ms) | OCR (~150-300ms) | click poll (2ms, pure query)")
    try:
        fast_worker(target)
    except KeyboardInterrupt:
        shared["stop"] = True
        print("Stopped by user")
