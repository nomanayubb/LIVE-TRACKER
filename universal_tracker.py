#!/usr/bin/env python3
"""
UNIVERSAL WINDOW TRACKER
Tracks ANY application/window, switches between them, shows real-time data
"""

import time
import mss
import cv2
import numpy as np
import sys
from pathlib import Path
import pygetwindow as gw
import pyautogui

def capture_window_direct(hwnd, width, height):
    """Capture a window's actual rendered content via PrintWindow, even if it's
    covered by other windows or not focused. No focus-stealing required.
    Falls back to None on failure (caller should fall back to mss screen-region capture)."""
    import ctypes
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    hwndDC = mfcDC = saveDC = saveBitMap = None
    try:
        hwndDC = user32.GetWindowDC(hwnd)
        if not hwndDC:
            return None
        mfcDC = gdi32.CreateCompatibleDC(hwndDC)
        saveBitMap = gdi32.CreateCompatibleBitmap(hwndDC, width, height)
        saveDC = gdi32.CreateCompatibleDC(hwndDC)
        gdi32.SelectObject(saveDC, saveBitMap)

        PW_RENDERFULLCONTENT = 2
        result = user32.PrintWindow(hwnd, saveDC, PW_RENDERFULLCONTENT)
        if not result:
            return None

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32),
            ]
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = width
        bmi.biHeight = -height  # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB

        buf_size = width * height * 4
        buf = (ctypes.c_ubyte * buf_size)()
        got = gdi32.GetDIBits(saveDC, saveBitMap, 0, height, buf, ctypes.byref(bmi), 0)
        if not got:
            return None

        arr = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 4)
        return arr[:, :, :3][:, :, ::-1]  # BGRA -> RGB
    except Exception:
        return None
    finally:
        try:
            if saveBitMap: gdi32.DeleteObject(saveBitMap)
            if saveDC: gdi32.DeleteDC(saveDC)
            if mfcDC: gdi32.DeleteDC(mfcDC)
            if hwndDC: user32.ReleaseDC(hwnd, hwndDC)
        except Exception:
            pass

def force_activate(window):
    """Reliably bring a window to foreground, bypassing Windows focus-steal prevention.
    SetForegroundWindow (used by .activate()) fails silently when the caller isn't the
    foreground process; minimize+restore is a state change Windows honors regardless."""
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

OUTPUT_FILE = Path.home() / ".live_screen_state.txt"
WINDOW_CONFIG = Path.home() / ".tracker_window_config.txt"
REFRESH_SIGNAL = Path.home() / ".tracker_refresh"

def init_easyocr():
    try:
        import easyocr
        print("EasyOCR loaded")
        try:
            reader = easyocr.Reader(['en'], gpu=True)
            return reader
        except:
            return easyocr.Reader(['en'], gpu=False)
    except:
        print("EasyOCR not available")
        return None

def get_all_windows():
    try:
        windows = [w for w in gw.getAllWindows() if w.title.strip()]
        return windows
    except:
        return []

def extract_window_data(img, reader):
    try:
        h, w = img.shape[:2]
        if h > 1080:
            ratio = 1080 / h
            new_w = int(w * ratio)
            img = cv2.resize(img, (new_w, 1080), interpolation=cv2.INTER_LINEAR)

        results = reader.readtext(img, detail=0) if reader else []
        text_data = " | ".join(results)

        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        brightness = int(np.mean(gray))

        return {
            "text": text_data[:500],
            "brightness": brightness,
            "success": True
        }
    except:
        return {
            "text": "Error extracting data",
            "brightness": 0,
            "success": False
        }

def track_universal(reader, target_window_name=None):
    print("Starting UNIVERSAL TRACKER...")
    print("Writing to:", OUTPUT_FILE)

    iteration = 0
    current_target = target_window_name
    last_activated = None  # only steal focus when target actually changes

    try:
        with mss.mss() as sct:
            while True:
                try:
                    iteration += 1

                    # Allow live switching by writing a window name into WINDOW_CONFIG
                    try:
                        if WINDOW_CONFIG.exists():
                            new_target = WINDOW_CONFIG.read_text(encoding='utf-8').strip()
                            if new_target and new_target != current_target:
                                current_target = new_target
                                print(f"Switched target to: {current_target}")
                    except:
                        pass

                    all_windows = get_all_windows()
                    window_list = [w.title[:40] for w in all_windows]

                    # The window ACTUALLY in foreground right now (ground truth for focus),
                    # separate from current_target which is just what we're trying to capture.
                    try:
                        actual_fg = gw.getActiveWindow()
                        actual_fg_title = actual_fg.title if actual_fg else "Unknown"
                    except Exception:
                        actual_fg_title = "Unknown"

                    if not current_target and all_windows:
                        current_target = all_windows[0].title

                    target_window = None
                    if current_target:
                        matching = [w for w in all_windows if current_target in w.title]
                        if matching:
                            target_window = matching[0]
                            # Re-activate only on target change OR when a REFRESH signal file
                            # is dropped (on-demand verification) - never continuously, since
                            # that steals focus from whatever the user is actively doing.
                            refresh_requested = REFRESH_SIGNAL.exists()
                            if current_target != last_activated or refresh_requested:
                                force_activate(target_window)
                                last_activated = current_target
                                if refresh_requested:
                                    try:
                                        REFRESH_SIGNAL.unlink()
                                    except Exception:
                                        pass
                                time.sleep(0.3)  # let the window settle after activation

                    # PrintWindow (GPU-covered-window capture) doesn't work for OpenGL apps
                    # like Blender - falls back to screen-region capture, accurate only right
                    # after activation above (hence the REFRESH mechanism for on-demand checks).
                    monitor = sct.monitors[1]
                    if target_window:
                        monitor = {
                            'top': max(0, target_window.top),
                            'left': max(0, target_window.left),
                            'width': max(100, target_window.width),
                            'height': max(100, target_window.height)
                        }
                    sct_img = sct.grab(monitor)
                    img = np.array(sct_img)
                    data = extract_window_data(img, reader)

                    timestamp = time.time()
                    output = f"""TIMESTAMP: {timestamp}
ITERATION: {iteration}
TARGET_WINDOW: {current_target if current_target else 'None'}
ACTUAL_FOREGROUND: {actual_fg_title}
AVAILABLE_WINDOWS: {len(all_windows)}
WINDOW_LIST: {window_list}
BRIGHTNESS: {data['brightness']}
TEXT_DATA: {data['text']}
STATUS: Running
"""

                    with open(OUTPUT_FILE, "w", encoding='utf-8') as f:
                        f.write(output)

                    elapsed = time.time() - (time.time())
                    time.sleep(0.1)

                    if iteration % 10 == 0:
                        print(f"[{iteration}] Windows: {len(all_windows)} | Current: {current_target} | Brightness: {data['brightness']}")

                except KeyboardInterrupt:
                    print("Tracker stopped by user")
                    break
                except Exception as e:
                    print(f"Error: {e}")
                    time.sleep(0.1)

    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    print("=" * 60)
    print("UNIVERSAL WINDOW TRACKER - ADVANCED")
    print("=" * 60)
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if target:
        print(f"Targeting window: {target}")
    reader = init_easyocr()
    track_universal(reader, target_window_name=target)
