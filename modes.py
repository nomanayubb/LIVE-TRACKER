#!/usr/bin/env python3
"""
MODES - one central place defining every capture profile.

The point: instead of remembering which of half a dozen switches to flip for a
given job, name the task and apply its profile. Every number in here was
measured on this machine (see PERFORMANCE in each profile), not estimated.

    python modes.py list                 # every profile and what it does
    python modes.py show                 # what is currently active
    python modes.py apply ui_automation  # switch profile
    python modes.py recommend "read a long log file"

From Python:
    import modes
    modes.apply("motion_capture")
    print(modes.current())
"""
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ACTIVE_FILE = BASE_DIR / ".tracker_active_mode.txt"

# Control files the tracker watches. Presence = enabled.
PAUSE_SWITCH = BASE_DIR / ".tracker_paused"
PRECISION_SWITCH = BASE_DIR / ".tracker_precision"

PROFILES = {
    # ------------------------------------------------------------------ default
    "normal": {
        "summary": "Default all-purpose tracking. Fast, low cost, always safe to leave on.",
        "use_for": [
            "general awareness of what window/app is active",
            "tracking clicks and who made them",
            "leaving running in the background all day",
        ],
        "switches": {"paused": False, "precision": False},
        "performance": "~3-9ms input loop, 16x9 colour grid, OCR only when the screen changes",
        "cost": "low - safe to leave running indefinitely",
    },

    # ------------------------------------------------------------------ automation
    "ui_automation": {
        "summary": "Driving another app: finding buttons, clicking menus, verifying each step landed.",
        "use_for": [
            "clicking through menus in any application",
            "filling forms, pressing buttons, navigating UI",
            "verifying an automation step actually worked",
        ],
        "switches": {"paused": False, "precision": False},
        "performance": "~3-9ms input loop; vision scan ~250ms gives button/text-region positions; OCR confirms each step",
        "cost": "low",
        "notes": "Precision OFF on purpose - vision.py already locates click targets, "
                 "and OCR text confirms the result. Extra pixel detail adds nothing here "
                 "and only slows the loop. Use clicker.py, and keep dependent steps inside "
                 "ONE run_steps call (focus reverts the moment a script exits).",
    },

    # ------------------------------------------------------------------ reading
    "text_reading": {
        "summary": "Reading dense or small text accurately - logs, code, fine UI labels.",
        "use_for": [
            "reading a long log or console output",
            "small fonts, dense tables, fine print",
            "when OCR keeps mis-reading characters",
        ],
        "switches": {"paused": False, "precision": True},
        "performance": "~3.4ms loop, 48x27 colour grid, exact-pixel frame written on every change",
        "cost": "moderate - writes an image on each visual change",
        "notes": "The exact frame (.live_frame.jpg) is the sharp, fully readable one. "
                 "The grid is a 1600x compression and will NOT be legible - do not try to "
                 "read text from a reconstructed grid image.",
    },

    # ------------------------------------------------------------------ evidence
    "evidence": {
        "summary": "Capturing exactly what was on screen at a moment, for replay or comparison.",
        "use_for": [
            "before/after comparison of a change",
            "recording what happened during an automated run",
            "diagnosing a subtle visual difference (colour shifts, faint highlights)",
        ],
        "switches": {"paused": False, "precision": True},
        "performance": "~3.4ms loop; exact frames + rotating snapshot buffer; replay.py rebuilds 90 images/sec",
        "cost": "moderate - disk writes on every visual change",
        "notes": "Pair with replay.py: 'record' to log frames, then 'sheet' for a "
                 "contact sheet of many moments in one image (measured 0.08s for 50).",
    },

    # ------------------------------------------------------------------ motion
    "motion_capture": {
        "summary": "High frame-rate capture of a screen region, feeding real consecutive frames to a downstream pipeline.",
        "use_for": [
            "feeding a live video region into your own processing pipeline",
            "anything needing many real consecutive frames per second",
            "measuring how much of a region is moving",
        ],
        "switches": {"paused": False, "precision": False},
        "performance": "MEASURED capture ceiling: 1920x1080 -> ~22fps | 1280x720 -> ~47fps | 960x540 -> ~56fps",
        "cost": "high CPU while running - use a region, not full screen",
        "notes": "Use motion.py's RegionCapture directly, NOT the main tracker - it samples "
                 "too slowly for this. It hands you raw numpy frames; processing is left to "
                 "the caller. Capture ONLY the region you need: mss has a fixed ~18ms "
                 "per-grab overhead, so full-screen costs 34ms (22fps) while any smaller "
                 "region costs ~18ms (~50fps). Shrinking below 960x540 buys nothing. "
                 "Turn the main tracker's precision OFF so it doesn't compete for CPU.",
    },

    # ------------------------------------------------------------------ privacy
    "privacy": {
        "summary": "Everything halted. Nothing is captured, read, or recorded at all.",
        "use_for": [
            "banking, passwords, payment details",
            "private messages or personal documents",
            "any moment you simply don't want captured",
        ],
        "switches": {"paused": True, "precision": False},
        "performance": "no capture whatsoever; the process stays alive so resuming is instant",
        "cost": "none",
        "notes": "This is a real kill switch, not a filter - within ~200ms the tracker "
                 "writes only a PAUSED marker, shares no frames, and runs no OCR or vision. "
                 "Claude receives no screen data while this is active.",
    },
}

# Plain-language hints -> profile. Used by `recommend`.
KEYWORDS = {
    "ui_automation": ["click", "button", "menu", "automate", "automation", "form",
                       "navigate", "press", "fill", "select", "drag"],
    "text_reading":  ["read", "text", "log", "small font", "tiny", "code", "label",
                       "console", "output", "ocr"],
    "evidence":      ["record", "evidence", "replay", "before", "after", "compare",
                       "history", "proof", "diagnose", "screenshot"],
    "motion_capture":["motion", "blur", "video", "fps", "frame", "stream", "casino",
                       "live", "fast", "smooth", "animation"],
    "privacy":       ["private", "password", "bank", "secret", "stop", "sensitive",
                       "personal", "hide"],
}


def _set(path: Path, on: bool):
    try:
        if on:
            path.write_text("on", encoding="utf-8")
        elif path.exists():
            path.unlink()
    except Exception:
        pass


def apply(name):
    """Apply a profile by flipping the control files the tracker watches."""
    if name not in PROFILES:
        raise KeyError(f"Unknown profile {name!r}. Known: {', '.join(PROFILES)}")
    sw = PROFILES[name]["switches"]
    _set(PAUSE_SWITCH, sw["paused"])
    _set(PRECISION_SWITCH, sw["precision"])
    try:
        ACTIVE_FILE.write_text(name, encoding="utf-8")
    except Exception:
        pass
    return current()


def current():
    """What's actually on right now, read from the live switches (not just the
    recorded name - the switches are the truth, they can be flipped directly)."""
    paused = PAUSE_SWITCH.exists()
    precision = PRECISION_SWITCH.exists()
    recorded = ""
    try:
        recorded = ACTIVE_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    match = next((n for n, p in PROFILES.items()
                  if p["switches"]["paused"] == paused
                  and p["switches"]["precision"] == precision), None)
    return {"paused": paused, "precision": precision,
            "recorded_profile": recorded, "matching_profiles": match}


def recommend(task_text):
    """Given a plain description of a task, pick the profile and explain why."""
    t = task_text.lower()
    scores = {name: sum(1 for k in kws if k in t) for name, kws in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        best = "normal"
    p = PROFILES[best]
    return {
        "task": task_text,
        "profile": best,
        "turn_on": [k for k, v in p["switches"].items() if v] or ["nothing extra"],
        "turn_off": [k for k, v in p["switches"].items() if not v],
        "performance": p["performance"],
        "why": p["summary"],
        "notes": p.get("notes", ""),
    }


def _print_profile(name, p, active=False):
    mark = " [ACTIVE]" if active else ""
    on = [k.upper() for k, v in p["switches"].items() if v] or ["-"]
    off = [k for k, v in p["switches"].items() if not v] or ["-"]
    print(f"\n=== {name}{mark} ===")
    print(f"  {p['summary']}")
    print(f"  ON : {', '.join(on)}")
    print(f"  OFF: {', '.join(off)}")
    print(f"  speed: {p['performance']}")
    print(f"  cost : {p['cost']}")
    for u in p["use_for"]:
        print(f"    - {u}")
    if p.get("notes"):
        print(f"  note: {p['notes']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "list":
        cur = current()
        for name, p in PROFILES.items():
            _print_profile(name, p, active=(name == cur["matching_profiles"]))
    elif cmd == "apply":
        print(json.dumps(apply(sys.argv[2]), indent=2))
    elif cmd == "recommend":
        print(json.dumps(recommend(" ".join(sys.argv[2:])), indent=2))
    else:
        c = current()
        print(json.dumps(c, indent=2))
        if c["matching_profiles"]:
            _print_profile(c["matching_profiles"], PROFILES[c["matching_profiles"]], True)
