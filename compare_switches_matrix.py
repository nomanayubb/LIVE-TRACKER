#!/usr/bin/env python3
"""
The TRUE combinatorial space: paused x precision x fullscreen = 8 states.

The 6 named "modes" (normal, ui_automation, text_reading, evidence,
motion_capture, privacy) are just convenient presets over paused+precision -
they are mutually exclusive by design (you can't be "text_reading" AND
"evidence" at once, since both just set precision=True). fullscreen is the
one genuinely independent switch that layers on top of any of them. This
script tests all 8 raw combinations directly, not named presets.
"""
import json
import time
from pathlib import Path

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
STATE_JSON = BASE_DIR / ".live_screen_state.json"
PAUSE_SWITCH = BASE_DIR / ".tracker_paused"
PRECISION_SWITCH = BASE_DIR / ".tracker_precision"
FULLSCREEN_SWITCH = BASE_DIR / ".tracker_fullscreen"
OUT = BASE_DIR / "switch_matrix.jpg"

PANEL_W, PANEL_H = 480, 270
HEADER_H = 94   # +16 to fit the added named-mode line without crowding


def _set(path, on):
    if on:
        path.write_text("on", encoding="utf-8")
    elif path.exists():
        path.unlink()


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


def panel(title, img, lines, ok=True):
    p = np.full((PANEL_H + HEADER_H, PANEL_W, 3), 18, dtype=np.uint8)
    if img is not None:
        p[:PANEL_H] = cv2.resize(img, (PANEL_W, PANEL_H), interpolation=cv2.INTER_NEAREST)
    else:
        cv2.putText(p, "no data (paused)", (PANEL_W // 2 - 90, PANEL_H // 2),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (90, 90, 90), 2)
    colour = (80, 220, 120) if ok else (90, 90, 230)
    cv2.rectangle(p, (0, 0), (PANEL_W - 1, PANEL_H - 1), colour, 2)
    cv2.rectangle(p, (0, PANEL_H), (PANEL_W, PANEL_H + HEADER_H), (28, 28, 28), -1)
    cv2.putText(p, title, (8, PANEL_H + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 2)
    for i, line in enumerate(lines[:3]):
        cv2.putText(p, line[:56], (8, PANEL_H + 42 + i * 16),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 200, 200), 1)
    return p


# Which named modes correspond to which raw (paused, precision) combination.
# Several names share IDENTICAL switches - they only differ in which other
# module you use (vision.py vs motion.py) or intent, not in these two flags.
# fullscreen is independent of all of them, so it is not part of this map.
NAMED_MODES = {
    (False, False): "normal / ui_automation / motion_capture",
    (False, True): "text_reading / evidence",
    (True, False): "privacy",
    (True, True): "privacy",   # paused overrides precision regardless
}


def main():
    panels = []
    for paused in (False, True):
        for precision in (False, True):
            for fullscreen in (False, True):
                mode_names = NAMED_MODES[(paused, precision)]
                title = f"P={int(paused)} Pr={int(precision)} FS={int(fullscreen)}"
                print(f"testing: {title}", flush=True)
                _set(PAUSE_SWITCH, paused)
                _set(PRECISION_SWITCH, precision)
                _set(FULLSCREEN_SWITCH, fullscreen)
                time.sleep(6)
                st = _read_state()
                if paused or st.get("status") == "PAUSED":
                    panels.append(panel(title, None, [mode_names, "ALL CAPTURE HALTED"], False))
                    continue
                cols, rows = st.get("grid_cols", "?"), st.get("grid_rows", "?")
                res = st.get("screen_resolution", "?")
                loop = st.get("avg_loop_ms", "?")
                lines = [mode_names, f"grid {cols}x{rows}  res={res}", f"loop={loop}ms"]
                panels.append(panel(title, grid_to_image(st), lines, True))

    cols = 4
    rows = (len(panels) + cols - 1) // cols
    ph, pw = panels[0].shape[:2]
    sheet = np.full((rows * ph, cols * pw, 3), 12, dtype=np.uint8)
    for i, p in enumerate(panels):
        r, c = divmod(i, cols)
        sheet[r*ph:(r+1)*ph, c*pw:(c+1)*pw] = p

    cv2.imwrite(str(OUT), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    _set(PAUSE_SWITCH, False)
    _set(PRECISION_SWITCH, False)
    _set(FULLSCREEN_SWITCH, False)
    print(f"\nsaved -> {OUT}")
    print("reset all switches off")


if __name__ == "__main__":
    main()
