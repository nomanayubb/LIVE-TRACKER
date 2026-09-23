#!/usr/bin/env python3
"""
FRAME_CAPTURE - buffer real .live_frame.jpg captures during a task, then
build ONE contact-sheet image covering the whole sequence.

Different from replay.py: replay.py reconstructs from the compressed 1600x
pixel grid (fast, but blurry, not text/detail-legible). This buffers the
REAL sharp JPEG the tracker already writes every ~100-130ms in precision
mode, so the sheet actually shows what was on screen, not a colour summary.

Usage:
    python frame_capture.py record 8          # buffer real frames for 8s
    python frame_capture.py sheet <session>    # build one contact sheet from a session
    python frame_capture.py record_sheet 8     # do both, print the sheet path
"""
import shutil
import sys
import time
from pathlib import Path

import cv2

BASE_DIR = Path(__file__).resolve().parent
FRAME_FILE = BASE_DIR / ".live_frame.jpg"
SESSIONS_DIR = BASE_DIR / "capture_sessions"
SESSIONS_DIR.mkdir(exist_ok=True)


def record(seconds=8):
    """Copy every new .live_frame.jpg write into a fresh session folder for
    `seconds`, detected by mtime (not size - two frames can be byte-identical
    in size while genuinely different writes, so size comparison misses them)."""
    session_dir = SESSIONS_DIR / time.strftime("%Y%m%d_%H%M%S")
    session_dir.mkdir(exist_ok=True)
    if not FRAME_FILE.exists():
        print("No .live_frame.jpg yet - is dual_tracker.py running in precision mode?")
        return None
    start = time.time()
    end = start + seconds
    last_mtime = FRAME_FILE.stat().st_mtime
    n = 0
    while time.time() < end:
        try:
            mtime = FRAME_FILE.stat().st_mtime
            if mtime != last_mtime:
                last_mtime = mtime
                n += 1
                elapsed_ms = round((time.time() - start) * 1000)
                shutil.copy2(FRAME_FILE, session_dir / f"frame_{n:04d}_{elapsed_ms}ms.jpg")
        except Exception:
            pass
        time.sleep(0.005)
    print(f"recorded {n} frames over {seconds}s -> {session_dir}")
    return str(session_dir)


def sheet(session, cols=8, cell_w=280, cell_h=158):
    """One contact-sheet image from a recorded session's real frames, each
    cell labelled with its elapsed-ms timestamp - readable in a single Read."""
    session_dir = Path(session)
    if not session_dir.is_absolute():
        session_dir = SESSIONS_DIR / session
    frames = sorted(session_dir.glob("frame_*.jpg"))
    if not frames:
        print(f"No frames found in {session_dir}")
        return None
    imgs = []
    for fp in frames:
        img = cv2.imread(str(fp))
        if img is None:
            continue
        img = cv2.resize(img, (cell_w, cell_h), interpolation=cv2.INTER_AREA)
        label = fp.stem.split("_", 2)[-1]  # "123ms"
        cv2.rectangle(img, (0, 0), (cell_w, 20), (0, 0, 0), -1)
        cv2.putText(img, label, (4, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 130), 1)
        imgs.append(img)
    import numpy as np
    rows = (len(imgs) + cols - 1) // cols
    sheet_img = np.zeros((rows * cell_h, cols * cell_w, 3), dtype="uint8")
    for i, img in enumerate(imgs):
        r, c = divmod(i, cols)
        sheet_img[r*cell_h:(r+1)*cell_h, c*cell_w:(c+1)*cell_w] = img
    out_path = session_dir / "contact_sheet.jpg"
    cv2.imwrite(str(out_path), sheet_img, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    print(f"contact sheet of {len(imgs)} frames -> {out_path}")
    return str(out_path)


def record_sheet(seconds=8, cols=8):
    session_dir = record(seconds)
    if session_dir:
        return sheet(session_dir, cols=cols)
    return None


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "record_sheet"
    if cmd == "record":
        record(float(sys.argv[2]) if len(sys.argv) > 2 else 8)
    elif cmd == "sheet":
        sheet(sys.argv[2])
    elif cmd == "record_sheet":
        record_sheet(float(sys.argv[2]) if len(sys.argv) > 2 else 8)
    else:
        print(__doc__)
