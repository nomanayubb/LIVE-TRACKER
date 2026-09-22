#!/usr/bin/env python3
"""
150 labeled images, targeting the Claude window specifically.

24 real distinct settings combinations exist (6 modes x 4 precision/fullscreen
states). To reach exactly 150: 6 sampled moments per combination = 144,
plus 6 extra samples of the first combination to reach 150 exactly.

Each image is individually labeled with the mode name(s) and exact
precision/fullscreen state active when it was captured - answering
"how much function or mode enabled" per image, not just per group.
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
FULLSCREEN_SWITCH = BASE_DIR / ".tracker_fullscreen"
WINDOW_CONFIG = BASE_DIR / ".tracker_window_config.txt"
OUT_DIR = BASE_DIR / "images_150"
OUT_DIR.mkdir(exist_ok=True)

PANEL_W, PANEL_H = 320, 180
HEADER_H = 70

NAMED_MODES = {
    (False, False): "normal+ui_automation+motion_capture",
    (False, True): "text_reading+evidence",
    (True, False): "privacy",
    (True, True): "privacy",
}


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


def grid_to_image(state):
    grid = state.get("pixel_grid")
    if not grid:
        return None
    arr = np.array(grid, dtype=np.uint8)
    return cv2.resize(arr[:, :, ::-1], (PANEL_W, PANEL_H), interpolation=cv2.INTER_CUBIC)


def make_panel(idx, mode_name, precision, fullscreen, sample_n, state):
    img = grid_to_image(state)
    p = np.full((PANEL_H + HEADER_H, PANEL_W, 3), 18, dtype=np.uint8)
    if img is not None:
        p[:PANEL_H] = cv2.resize(img, (PANEL_W, PANEL_H), interpolation=cv2.INTER_NEAREST)
        ok = True
    else:
        cv2.putText(p, "HALTED", (PANEL_W // 2 - 40, PANEL_H // 2),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (90, 90, 90), 2)
        ok = False
    colour = (80, 220, 120) if ok else (90, 90, 230)
    cv2.rectangle(p, (0, 0), (PANEL_W - 1, PANEL_H - 1), colour, 2)
    cv2.rectangle(p, (0, PANEL_H), (PANEL_W, PANEL_H + HEADER_H), (28, 28, 28), -1)
    cv2.putText(p, f"#{idx} {mode_name[:22]}", (5, PANEL_H + 15),
               cv2.FONT_HERSHEY_SIMPLEX, 0.35, colour, 1)
    cv2.putText(p, f"precision={int(precision)} fullscreen={int(fullscreen)} sample={sample_n}",
               (5, PANEL_H + 32), cv2.FONT_HERSHEY_SIMPLEX, 0.32, (200, 200, 200), 1)
    loop = state.get("avg_loop_ms", "?")
    cv2.putText(p, f"loop={loop}ms  target=Claude", (5, PANEL_H + 49),
               cv2.FONT_HERSHEY_SIMPLEX, 0.32, (200, 200, 200), 1)
    return p


def main():
    WINDOW_CONFIG.write_text("Claude", encoding="utf-8")
    print("target set to Claude window", flush=True)

    mode_list = ["normal", "ui_automation", "text_reading", "evidence", "motion_capture", "privacy"]
    configs = [(False, False), (True, False), (False, True), (True, True)]

    panels = []
    idx = 0
    for combo_i, (mode_name_key, (precision, fullscreen)) in enumerate(
            [(m, c) for m in mode_list for c in configs]):
        modes.apply(mode_name_key)
        _set(PRECISION_SWITCH, precision)
        _set(FULLSCREEN_SWITCH, fullscreen)
        time.sleep(4)  # settle once per unique combination

        # The actual switches applied are (mode's own "paused", the precision
        # we just forced) - looking THIS up in NAMED_MODES gives the real
        # paired name (e.g. "text_reading+evidence"), not just whichever
        # single mode we happened to apply() to get there.
        actual_paused = modes.PROFILES[mode_name_key]["switches"]["paused"]
        pair_name = NAMED_MODES.get((actual_paused, precision), mode_name_key)

        n_samples = 12 if combo_i == 0 else 6  # pad the first combo to reach exactly 150
        for s in range(1, n_samples + 1):
            idx += 1
            st = _read_state()
            panels.append(make_panel(idx, pair_name, precision, fullscreen, s, st))
            print(f"[{idx}/150] {pair_name} pr={int(precision)} fs={int(fullscreen)} sample={s}", flush=True)
            time.sleep(0.4)

    cols_n = 10
    rows_n = (len(panels) + cols_n - 1) // cols_n
    ph, pw = panels[0].shape[:2]
    sheet = np.full((rows_n * ph, cols_n * pw, 3), 12, dtype=np.uint8)
    for i, p in enumerate(panels):
        r, c = divmod(i, cols_n)
        sheet[r*ph:(r+1)*ph, c*pw:(c+1)*pw] = p

    out_path = BASE_DIR / "matrix_150.jpg"
    cv2.imwrite(str(out_path), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    modes.apply("normal")
    _set(PRECISION_SWITCH, False)
    _set(FULLSCREEN_SWITCH, False)
    WINDOW_CONFIG.unlink(missing_ok=True)
    print(f"\nsaved {len(panels)} images -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
