#!/usr/bin/env python3
"""
Combinatorial mode comparison: every task mode x the fullscreen switch,
so the effect of each is visible independently AND combined - not just
each mode in isolation like compare_modes.py does.

6 modes x 2 fullscreen states = 12 panels in one sheet.
"""
import json
import time
from pathlib import Path

import cv2
import numpy as np

import modes

BASE_DIR = Path(__file__).resolve().parent
STATE_JSON = BASE_DIR / ".live_screen_state.json"
FULLSCREEN_SWITCH = BASE_DIR / ".tracker_fullscreen"
OUT = BASE_DIR / "mode_matrix.jpg"

PANEL_W, PANEL_H = 480, 270
HEADER_H = 78


def _read_state(retries=12):
    for _ in range(retries):
        try:
            return json.loads(STATE_JSON.read_text(encoding="utf-8"))
        except Exception:
            time.sleep(0.25)
    return {}


def grid_to_image(state):
    grid = state.get("pixel_grid")
    if not grid:
        return None
    arr = np.array(grid, dtype=np.uint8)
    return cv2.resize(arr[:, :, ::-1], (PANEL_W, PANEL_H), interpolation=cv2.INTER_CUBIC)


def set_fullscreen(on):
    if on:
        FULLSCREEN_SWITCH.write_text("on", encoding="utf-8")
    elif FULLSCREEN_SWITCH.exists():
        FULLSCREEN_SWITCH.unlink()


def panel(title, img, lines, ok=True):
    p = np.full((PANEL_H + HEADER_H, PANEL_W, 3), 18, dtype=np.uint8)
    if img is not None:
        p[:PANEL_H] = cv2.resize(img, (PANEL_W, PANEL_H), interpolation=cv2.INTER_NEAREST)
    else:
        cv2.putText(p, "no data", (PANEL_W // 2 - 50, PANEL_H // 2),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (90, 90, 90), 2)
    colour = (80, 220, 120) if ok else (90, 90, 230)
    cv2.rectangle(p, (0, 0), (PANEL_W - 1, PANEL_H - 1), colour, 2)
    cv2.rectangle(p, (0, PANEL_H), (PANEL_W, PANEL_H + HEADER_H), (28, 28, 28), -1)
    cv2.putText(p, title, (8, PANEL_H + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 2)
    for i, line in enumerate(lines[:2]):
        cv2.putText(p, line[:56], (8, PANEL_H + 42 + i * 16),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 200, 200), 1)
    return p


def main():
    task_modes = ["normal", "ui_automation", "text_reading", "evidence", "motion_capture", "privacy"]
    panels = []

    for mode_name in task_modes:
        for fs in (False, True):
            print(f"testing: {mode_name} x fullscreen={fs}", flush=True)
            modes.apply(mode_name)
            set_fullscreen(fs)
            time.sleep(6)
            st = _read_state()
            if st.get("status") == "PAUSED":
                panels.append(panel(f"{mode_name} + fs={fs}", None,
                                    ["ALL CAPTURE HALTED", "(privacy mode)"], False))
                continue
            cols, rows = st.get("grid_cols", "?"), st.get("grid_rows", "?")
            res = st.get("screen_resolution", "?")
            fsm = st.get("fullscreen_mode", False)
            loop = st.get("avg_loop_ms", "?")
            lines = [f"grid {cols}x{rows}  res={res}", f"fullscreen_mode={fsm}  loop={loop}ms"]
            panels.append(panel(f"{mode_name} + fs={fs}", grid_to_image(st), lines, True))

    cols = 4
    rows = (len(panels) + cols - 1) // cols
    ph, pw = panels[0].shape[:2]
    sheet = np.full((rows * ph, cols * pw, 3), 12, dtype=np.uint8)
    for i, p in enumerate(panels):
        r, c = divmod(i, cols)
        sheet[r*ph:(r+1)*ph, c*pw:(c+1)*pw] = p

    cv2.imwrite(str(OUT), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    modes.apply("normal")
    set_fullscreen(False)
    print(f"\nsaved -> {OUT}")
    print("reset to normal, fullscreen off")


if __name__ == "__main__":
    main()
