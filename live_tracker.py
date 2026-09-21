#!/usr/bin/env python3
"""
Live Screen Tracker for Blender
Captures screen, extracts text/UI state, writes to shared buffer every 100ms
"""

import time
import mss
import cv2
import numpy as np
import os
import sys
from pathlib import Path
import pygetwindow as gw
import pyautogui
import subprocess

# Fix for Pillow ANTIALIAS compatibility issue
try:
    from PIL import Image
    if not hasattr(Image, 'ANTIALIAS'):
        Image.ANTIALIAS = Image.LANCZOS
except:
    pass

# Create output directory
OUTPUT_FILE = Path.home() / ".live_screen_state.txt"

def init_easyocr():
    """Initialize EasyOCR reader with GPU support if available"""
    try:
        import easyocr
        print("EasyOCR imported. Initializing with GPU...")
        try:
            reader = easyocr.Reader(['en'], gpu=True)
            print("GPU enabled for EasyOCR")
            return reader
        except:
            print("GPU not available, using CPU")
            reader = easyocr.Reader(['en'], gpu=False)
            return reader
    except ImportError:
        print("EasyOCR not installed. Install with: pip install easyocr")
        sys.exit(1)

def get_all_windows():
    """Get list of all open windows"""
    try:
        windows = gw.getAllWindows()
        return [w for w in windows if w.title.strip()]
    except:
        return []

def find_and_switch_to_window(window_name):
    """Find window by name and switch to it"""
    try:
        windows = gw.getWindowsWithTitle(window_name)
        if windows:
            target = windows[0]
            target.activate()
            time.sleep(0.1)
            # Maximize to ensure it's visible
            if not target.isMaximized:
                target.maximize()
            return True
    except:
        pass
    return False

def extract_blender_data(img, reader):
    """Extract Blender-specific UI data from screen"""
    try:
        # Resize image for faster processing using OpenCV only
        h, w = img.shape[:2]
        if h > 1080:
            ratio = 1080 / h
            new_w = int(w * ratio)
            img = cv2.resize(img, (new_w, 1080), interpolation=cv2.INTER_LINEAR)

        # Extract text
        results = reader.readtext(img, detail=0)
        text_data = " | ".join(results)

        # Analyze image for UI indicators
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        # Detect bright areas (UI elements)
        _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
        bright_pixels = np.count_nonzero(thresh)

        ui_active = "Yes" if bright_pixels > (img.shape[0] * img.shape[1] * 0.1) else "No"

        return {
            "text": text_data[:500],  # Limit to 500 chars
            "ui_active": ui_active,
            "brightness": int(np.mean(gray))
        }
    except Exception as e:
        return {
            "text": f"Error: {str(e)}",
            "ui_active": "Unknown",
            "brightness": 0
        }

def stream_to_buffer(reader):
    """Capture screen and write state every 100ms"""
    print(f"Starting live tracker...")
    print(f"Writing to: {OUTPUT_FILE}")

    import pygetwindow as gw

    try:
        with mss.mss() as sct:
            iteration = 0

            while True:
                try:
                    # Actively switch to Blender window
                    if not find_and_switch_to_window("Blender"):
                        # If no Blender found, list available windows
                        if iteration % 50 == 0:  # Log every 5 seconds
                            available = get_all_windows()
                            print(f"Available windows: {[w.title[:30] for w in available[:5]]}")

                    start_time = time.time()
                    iteration += 1

                    # Capture ACTIVE WINDOW (any application, general purpose)
                    monitor = sct.monitors[1]  # Fallback to primary
                    try:
                        active_window = gw.getActiveWindow()
                        if active_window:
                            monitor = {
                                'top': max(0, active_window.top),
                                'left': max(0, active_window.left),
                                'width': max(100, active_window.width),
                                'height': max(100, active_window.height)
                            }
                    except:
                        pass  # Fall back to primary monitor

                    # Capture screen
                    sct_img = sct.grab(monitor)
                    img = np.array(sct_img)

                    # Extract Blender data
                    data = extract_blender_data(img, reader)

                    # Write to file
                    timestamp = time.time()
                    output = f"""TIMESTAMP: {timestamp}
ITERATION: {iteration}
UI_ACTIVE: {data['ui_active']}
BRIGHTNESS: {data['brightness']}
TEXT_DATA: {data['text']}
STATUS: Running
"""

                    with open(OUTPUT_FILE, "w") as f:
                        f.write(output)

                    # Enforce 100ms interval
                    elapsed = time.time() - start_time
                    sleep_time = max(0, 0.1 - elapsed)
                    time.sleep(sleep_time)

                    if iteration % 10 == 0:
                        print(f"  [{iteration}] {data['ui_active']} | Brightness: {data['brightness']}")

                except KeyboardInterrupt:
                    print("\nStopped by user")
                    break
                except Exception as e:
                    print(f"Error in loop: {e}")
                    time.sleep(0.1)

    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    print("=" * 60)
    print("LIVE TRACKER - AUTO-FOCUS ENABLED")
    print("=" * 60)

    reader = init_easyocr()
    stream_to_buffer(reader)
