#!/usr/bin/env python3
"""
Complete combinatorial matrix: 6 named modes x 2 precision states = 12
unique captures. fullscreen is left at its default (already always full
screen unless a window target is configured, which this script never
does) so it is not varied here - only precision, which is the switch that
actually changes image sharpness (via exact_frame_png).
"""
import json
import time
from pathlib import Path

import cv2
import numpy as np

import modes

BASE_DIR = Path(__file__).resolve().parent
STATE_JSON = BASE_DIR / ".live_screen_state.json"
PRECISION_SWITCH = BASE_DIR / ".tracker_precision"
OUT = BASE_DIR / "full_matrix.jpg"

PANEL_W, PANEL_H = 380, 214
HEADER_H = 90


def _set(path, on):
    if on:
        path.write_text("on", encoding="utf-8")
    elif path.exists():
        path.unlink()


def _read_state(retries=10):
    for _ in range(retries):
        try:
            return json.loads(STATE_JSON.read_text(encoding="utf-8"))
        except Exception:
            time.sleep(0.25)
    return {}


def state_to_image(state):
    # Precision mode writes a genuinely sharp screenshot to disk (exact_frame_png).
    # Use that when available instead of the always-lossy pixel_grid, so
    # precision panels actually look sharp instead of just "less blocky".
    frame_path = state.get("exact_frame_png")
    if frame_path and Path(frame_path).exists():
        img = cv2.imread(frame_path)
        if img is not None:
            return cv2.resize(img, (PANEL_W, PANEL_H), interpolation=cv2.INTER_AREA), True

    grid = state.get("pixel_grid")
    if not grid:
        return None, False
    arr = np.array(grid, dtype=np.uint8)
    return cv2.resize(arr[:, :, ::-1], (PANEL_W, PANEL_H), interpolation=cv2.INTER_CUBIC), False


def panel(title, img, lines, ok=True, sharp=False):
    p = np.full((PANEL_H + HEADER_H, PANEL_W, 3), 18, dtype=np.uint8)
    if img is not None:
        p[:PANEL_H] = cv2.resize(img, (PANEL_W, PANEL_H), interpolation=cv2.INTER_NEAREST)
    else:
        cv2.putText(p, "HALTED", (PANEL_W // 2 - 45, PANEL_H // 2),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (90, 90, 90), 2)
    colour = (80, 220, 120) if ok else (90, 90, 230)
    cv2.rectangle(p, (0, 0), (PANEL_W - 1, PANEL_H - 1), colour, 2)
    if sharp:
        cv2.putText(p, "SHARP (exact_frame_png)", (6, 16),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)
    cv2.rectangle(p, (0, PANEL_H), (PANEL_W, PANEL_H + HEADER_H), (28, 28, 28), -1)
    cv2.putText(p, title, (6, PANEL_H + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.42, colour, 1)
    for i, line in enumerate(lines[:4]):
        cv2.putText(p, line[:44], (6, PANEL_H + 35 + i * 14),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.32, (200, 200, 200), 1)
    return p


def main():
    mode_list = ["normal", "ui_automation", "text_reading", "evidence", "motion_capture", "privacy"]
    precision_states = [False, True]
    panels = []
    n = 0

    for mode_name in mode_list:
        for precision in precision_states:
            n += 1
            title = f"[{n}/12] {mode_name}"
            print(f"{title}  precision={precision}", flush=True)
            modes.apply(mode_name)
            _set(PRECISION_SWITCH, precision)
            time.sleep(5)
            st = _read_state()

            if st.get("status") == "PAUSED":
                panels.append(panel(title, None,
                                    [f"Pr={int(precision)}",
                                     "ALL CAPTURE HALTED (privacy)"], False))
                continue

            cols, rows = st.get("grid_cols", "?"), st.get("grid_rows", "?")
            res = st.get("screen_resolution", "?")
            loop = st.get("avg_loop_ms", "?")
            actual_precision = st.get("precision_mode", "?")
            lines = [
                f"Pr={int(precision)}",
                f"grid {cols}x{rows}  res={res}",
                f"actual: precision={actual_precision}",
                f"loop={loop}ms",
            ]
            img, sharp = state_to_image(st)
            panels.append(panel(title, img, lines, True, sharp))

    cols_n = 4
    rows_n = (len(panels) + cols_n - 1) // cols_n
    ph, pw = panels[0].shape[:2]
    sheet = np.full((rows_n * ph, cols_n * pw, 3), 12, dtype=np.uint8)
    for i, p in enumerate(panels):
        r, c = divmod(i, cols_n)
        sheet[r*ph:(r+1)*ph, c*pw:(c+1)*pw] = p

    cv2.imwrite(str(OUT), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    modes.apply("normal")
    _set(PRECISION_SWITCH, False)
    print(f"\nsaved {len(panels)} panels -> {OUT}")
    print("reset to normal, precision off")


if __name__ == "__main__":
    main()
