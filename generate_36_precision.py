#!/usr/bin/env python3
"""
Precision-only subset of generate_72.py: 6 single-mode + 30 mode-pair
labeled images, all with precision ON = 36 panels. Reuses the same
capture/panel logic so results are directly comparable to matrix_72.jpg.
"""
import time
from pathlib import Path

import cv2
import numpy as np

import modes
from generate_72 import MODE_LIST, PRECISION_SWITCH, _set, capture_panel

BASE_DIR = Path(__file__).resolve().parent
OUT = BASE_DIR / "matrix_36_precision.jpg"


def main():
    panels = []
    idx = 0

    _set(PRECISION_SWITCH, True)
    for mode_name in MODE_LIST:
        modes.apply(mode_name)
        _set(PRECISION_SWITCH, True)
        time.sleep(3)
        idx += 1
        panels.append(capture_panel(idx, mode_name, "SINGLE", True))
        print(f"[{idx}/36] single {mode_name} precision=1", flush=True)

    for mode_a in MODE_LIST:
        modes.apply(mode_a)
        _set(PRECISION_SWITCH, True)
        time.sleep(3)
        others = [m for m in MODE_LIST if m != mode_a]
        for mode_b in others:
            idx += 1
            panels.append(capture_panel(idx, f"{mode_a}+{mode_b}", "COMBO", True))
            print(f"[{idx}/36] pair {mode_a}+{mode_b} precision=1", flush=True)

    cols_n = 6
    rows_n = (len(panels) + cols_n - 1) // cols_n
    ph, pw = panels[0].shape[:2]
    sheet = np.full((rows_n * ph, cols_n * pw, 3), 12, dtype=np.uint8)
    for i, p in enumerate(panels):
        r, c = divmod(i, cols_n)
        sheet[r*ph:(r+1)*ph, c*pw:(c+1)*pw] = p

    cv2.imwrite(str(OUT), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    modes.apply("normal")
    _set(PRECISION_SWITCH, False)
    print(f"\nsaved {len(panels)} panels -> {OUT}")


if __name__ == "__main__":
    main()
