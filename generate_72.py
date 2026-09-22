#!/usr/bin/env python3
"""
72 labeled images:
  6 single-mode captures, precision off               =  6
  6 single-mode captures, precision on                = 12
  30 mode-pair labels (mode A applied, paired with     = 42
     each of the other 5 mode names), precision off
  30 mode-pair labels, precision on                    = 72

A "pair" here means: the underlying switches are whatever mode A sets
(modes are mutually exclusive presets - you cannot literally run two at
once), but the image is labeled "A+B" for the requested pairing
reference. Precision-on panels use the real exact_frame_png screenshot
(sharp) instead of the lossy pixel_grid.
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
OUT = BASE_DIR / "matrix_72.jpg"

PANEL_W, PANEL_H = 300, 168
HEADER_H = 78

MODE_LIST = ["normal", "ui_automation", "text_reading", "evidence", "motion_capture", "privacy"]


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
            time.sleep(0.2)
    return {}


def state_to_image(state):
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


def panel(idx, title, group, img, precision, loop_ms, clock, ok=True, sharp=False):
    p = np.full((PANEL_H + HEADER_H, PANEL_W, 3), 18, dtype=np.uint8)
    if img is not None:
        p[:PANEL_H] = cv2.resize(img, (PANEL_W, PANEL_H), interpolation=cv2.INTER_NEAREST)
    else:
        cv2.putText(p, "HALTED", (PANEL_W // 2 - 40, PANEL_H // 2),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (90, 90, 90), 2)
    colour = (80, 220, 120) if ok else (90, 90, 230)
    cv2.rectangle(p, (0, 0), (PANEL_W - 1, PANEL_H - 1), colour, 2)
    if sharp:
        cv2.putText(p, "SHARP", (6, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 0), 1)
    grp_colour = (120, 180, 255) if group == "SINGLE" else (255, 160, 80)
    cv2.putText(p, group, (PANEL_W - 78, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.36, grp_colour, 1)
    cv2.rectangle(p, (0, PANEL_H), (PANEL_W, PANEL_H + HEADER_H), (28, 28, 28), -1)
    cv2.putText(p, f"#{idx} {title[:26]}", (5, PANEL_H + 15),
               cv2.FONT_HERSHEY_SIMPLEX, 0.34, colour, 1)
    cv2.putText(p, f"precision={int(precision)}", (5, PANEL_H + 32),
               cv2.FONT_HERSHEY_SIMPLEX, 0.32, (200, 200, 200), 1)
    cv2.putText(p, f"loop={loop_ms}ms  {clock}", (5, PANEL_H + 49),
               cv2.FONT_HERSHEY_SIMPLEX, 0.32, (200, 200, 200), 1)
    return p


def capture_panel(idx, title, group, precision):
    st = _read_state()
    loop_ms = st.get("avg_loop_ms", "?")
    ts = st.get("timestamp")
    clock = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else time.strftime("%H:%M:%S")
    if st.get("status") == "PAUSED":
        return panel(idx, title, group, None, precision, loop_ms, clock, ok=False)
    img, sharp = state_to_image(st)
    return panel(idx, title, group, img, precision, loop_ms, clock, ok=True, sharp=sharp)


def main():
    panels = []
    idx = 0

    # Phase 1+2: 6 single-mode images, precision off then on = 12
    for precision in (False, True):
        _set(PRECISION_SWITCH, precision)
        for mode_name in MODE_LIST:
            modes.apply(mode_name)
            _set(PRECISION_SWITCH, precision)
            time.sleep(3)
            idx += 1
            panels.append(capture_panel(idx, mode_name, "SINGLE", precision))
            print(f"[{idx}/72] single {mode_name} precision={int(precision)}", flush=True)

    # Phase 3+4: 30 pair labels x 2 precision states = 60 (total 72)
    for precision in (False, True):
        for mode_a in MODE_LIST:
            modes.apply(mode_a)
            _set(PRECISION_SWITCH, precision)
            time.sleep(3)
            others = [m for m in MODE_LIST if m != mode_a]
            for mode_b in others:
                idx += 1
                panels.append(capture_panel(idx, f"{mode_a}+{mode_b}", "COMBO", precision))
                print(f"[{idx}/72] pair {mode_a}+{mode_b} precision={int(precision)}", flush=True)

    cols_n = 9
    rows_n = (len(panels) + cols_n - 1) // cols_n
    ph, pw = panels[0].shape[:2]
    sheet = np.full((rows_n * ph, cols_n * pw, 3), 12, dtype=np.uint8)
    for i, p in enumerate(panels):
        r, c = divmod(i, cols_n)
        sheet[r*ph:(r+1)*ph, c*pw:(c+1)*pw] = p

    cv2.imwrite(str(OUT), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    modes.apply("normal")
    _set(PRECISION_SWITCH, False)
    print(f"\nsaved {len(panels)} panels -> {OUT}")


if __name__ == "__main__":
    main()
