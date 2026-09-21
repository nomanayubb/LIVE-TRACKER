#!/usr/bin/env python3
"""
COMPARE MODES - renders what each capture mode actually produces, side by side,
so the difference is visible rather than described.

For each mode it applies the profile, lets the tracker settle, then renders a
panel showing: the grid data rebuilt as an image (what the REPORT contains),
the measured loop speed, grid resolution, and whether an exact-pixel frame is
available. The point is to make the trade-off concrete - normal mode's grid is
genuinely unreadable, precision's is better but still not text-legible, and
only the exact frame is truly sharp.

    python compare_modes.py
"""
import json
import time
from pathlib import Path

import cv2
import numpy as np

import modes

BASE_DIR = Path(__file__).resolve().parent
STATE_JSON = BASE_DIR / ".live_screen_state.json"
FRAME_FILE = BASE_DIR / ".live_frame.jpg"
OUT = BASE_DIR / "mode_comparison.jpg"

PANEL_W, PANEL_H = 640, 360
HEADER_H = 96


def _read_state(retries=12):
    """The tracker rewrites this file constantly, so a read can land mid-write
    and yield truncated JSON. Retry briefly rather than reporting '?' values."""
    for _ in range(retries):
        try:
            return json.loads(STATE_JSON.read_text(encoding="utf-8"))
        except Exception:
            time.sleep(0.25)
    return {}


def _panel(title, body_img, lines, ok=True):
    """One labelled panel: image on top, stats underneath."""
    panel = np.full((PANEL_H + HEADER_H, PANEL_W, 3), 18, dtype=np.uint8)
    if body_img is not None:
        img = cv2.resize(body_img, (PANEL_W, PANEL_H), interpolation=cv2.INTER_NEAREST)
        panel[:PANEL_H] = img
    else:
        cv2.putText(panel, "no image", (PANEL_W // 2 - 60, PANEL_H // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (90, 90, 90), 2)

    colour = (80, 220, 120) if ok else (90, 90, 230)
    cv2.rectangle(panel, (0, 0), (PANEL_W - 1, PANEL_H - 1), colour, 2)
    cv2.rectangle(panel, (0, PANEL_H), (PANEL_W, PANEL_H + HEADER_H), (28, 28, 28), -1)
    cv2.putText(panel, title, (10, PANEL_H + 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)
    for i, line in enumerate(lines[:3]):
        cv2.putText(panel, line[:64], (10, PANEL_H + 48 + i * 17),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1)
    return panel


def grid_to_image(state):
    grid = state.get("pixel_grid")
    if not grid:
        return None
    arr = np.array(grid, dtype=np.uint8)
    return cv2.resize(arr[:, :, ::-1], (PANEL_W, PANEL_H), interpolation=cv2.INTER_CUBIC)


SKIP_TITLES = ("Tracker", "Windows Input Experience", "Program Manager",
               "Windows Shell Experience", "Settings")

def _pick_visible_target():
    """Find the window that is genuinely on screen right now - not just
    non-minimised, but actually FOREGROUND. Comparing modes against a
    minimised or invisible system window yields black panels and proves
    nothing."""
    try:
        import pygetwindow as gw
        fg = gw.getActiveWindow()
        if fg and fg.title.strip() and not any(s in fg.title for s in SKIP_TITLES):
            return fg.title[:30]
        candidates = [w for w in gw.getAllWindows()
                      if w.title.strip() and not w.isMinimized
                      and w.width > 700 and w.height > 400
                      and not any(s in w.title for s in SKIP_TITLES)]
        if candidates:
            return max(candidates, key=lambda w: w.width * w.height).title[:30]
    except Exception:
        pass
    return None


def capture_mode(name, settle=8):
    """Apply a mode, let it settle, return (image, stat lines)."""
    modes.apply(name)
    time.sleep(settle)
    st = _read_state()

    if st.get("status") == "PAUSED":
        return None, ["ALL CAPTURE HALTED", "no data reaches Claude at all", "resume is instant"], False

    cols, rows = st.get("grid_cols", "?"), st.get("grid_rows", "?")
    loop = st.get("avg_loop_ms", st.get("loop_ms", "?"))
    cells = (cols * rows) if isinstance(cols, int) and isinstance(rows, int) else "?"
    exact = "yes (.live_frame.jpg)" if st.get("precision_mode") else "no"
    lines = [
        f"grid {cols}x{rows} = {cells} cells   loop {loop}ms",
        f"exact-pixel frame: {exact}",
        f"OCR text age {st.get('ocr_age_ms','?')}ms | vision {st.get('vision_age_ms','?')}ms",
    ]
    return grid_to_image(st), lines, True


def main():
    # Point the tracker at a window that's actually visible - comparing modes
    # against a minimised window just yields black panels and proves nothing.
    target = _pick_visible_target()
    if target:
        (BASE_DIR / ".tracker_window_config.txt").write_text(target, encoding="utf-8")
        print(f"Comparing against visible window: {target!r}")
        time.sleep(3)

    print("Rendering what each mode produces (this takes ~40s, modes need to settle)...\n")
    panels = []

    for name in ["normal", "ui_automation", "text_reading", "evidence", "motion_capture", "privacy"]:
        print(f"  capturing mode: {name} ...")
        img, lines, ok = capture_mode(name)
        panels.append(_panel(f"{name}", img, lines, ok))

    # The exact-pixel frame, for contrast - this is the only truly sharp one
    modes.apply("text_reading")
    time.sleep(5)
    exact = cv2.imread(str(FRAME_FILE)) if FRAME_FILE.exists() else None
    panels.append(_panel("EXACT FRAME (precision)", exact, [
        "real pixels, fully sharp and readable",
        "written automatically by the tracker",
        "this is what to use when text must be legible",
    ]))

    cols = 3
    rows = (len(panels) + cols - 1) // cols
    ph, pw = panels[0].shape[:2]
    sheet = np.full((rows * ph, cols * pw, 3), 12, dtype=np.uint8)
    for i, p in enumerate(panels):
        r, c = divmod(i, cols)
        sheet[r*ph:(r+1)*ph, c*pw:(c+1)*pw] = p

    cv2.imwrite(str(OUT), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    modes.apply("normal")
    print(f"\nsaved -> {OUT}")
    print("switched back to 'normal' mode")


if __name__ == "__main__":
    main()
