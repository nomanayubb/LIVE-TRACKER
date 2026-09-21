#!/usr/bin/env python3
"""
Shared module for any automation script to declare what it's doing, so the
tracker/overlay can show "Claude: idle" vs "Claude: clicking in Blender >
Add menu" live, and so real mouse clicks can be attributed to Claude vs
the user (see dual_tracker.py's click-attribution logic, which reads
CLICK_LOG and compares timestamps/positions against detected clicks).

Usage in any automation script:
    import claude_activity as ca
    ca.set_activity(True, window="Blender", action="Clicking Add menu")
    ca.log_click(319, 69)
    pyautogui.click(319, 69)
    ...
    ca.set_activity(False)  # mark idle when done
"""
import json
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ACTIVITY_FILE = BASE_DIR / ".claude_activity.json"
CLICK_LOG = BASE_DIR / ".claude_click_log.txt"
CLICK_LOG_MAX_AGE = 300  # seconds - entries older than this are pruned on write

def set_activity(active, window=None, action=None, private=False):
    """
    active  : True while Claude is actively driving an automation, False when idle/done.
    window  : the window title Claude is operating in (e.g. "Blender").
    action  : short human-readable description of the current step.
    private : True if this is a background/hidden window not visible in the
              user's taskbar (e.g. WS_EX_TOOLWINDOW); False if it's an
              ordinary window the user would also see in their taskbar.
    """
    data = {
        "active": active,
        "window": window,
        "action": action,
        "private": private,
        "timestamp": time.time(),
    }
    try:
        ACTIVITY_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass

def log_click(x, y, button="left"):
    """Call this immediately before/after every pyautogui click Claude performs,
    so the tracker can attribute real detected clicks to Claude vs the user."""
    now = time.time()
    entries = []
    try:
        if CLICK_LOG.exists():
            for line in CLICK_LOG.read_text(encoding="utf-8").splitlines():
                parts = line.split(",")
                if len(parts) >= 3 and now - float(parts[0]) < CLICK_LOG_MAX_AGE:
                    entries.append(line)
    except Exception:
        pass
    entries.append(f"{now},{x},{y},{button}")
    try:
        CLICK_LOG.write_text("\n".join(entries) + "\n", encoding="utf-8")
    except Exception:
        pass

def get_activity():
    """Read current Claude activity status (used by dual_tracker.py)."""
    try:
        if ACTIVITY_FILE.exists():
            return json.loads(ACTIVITY_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"active": False, "window": None, "action": None, "private": False, "timestamp": 0}

def get_recent_clicks(max_age=1.0):
    """Returns list of (timestamp, x, y, button) for Claude-logged clicks
    within the last max_age seconds - used to attribute a just-detected
    real click to Claude vs the user."""
    now = time.time()
    out = []
    try:
        if CLICK_LOG.exists():
            for line in CLICK_LOG.read_text(encoding="utf-8").splitlines():
                parts = line.split(",")
                if len(parts) >= 3:
                    ts = float(parts[0])
                    if now - ts < max_age:
                        out.append((ts, float(parts[1]), float(parts[2]), parts[3] if len(parts) > 3 else "left"))
    except Exception:
        pass
    return out
