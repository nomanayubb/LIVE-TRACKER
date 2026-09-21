#!/usr/bin/env python3
"""
REPLAY - turn recorded history into visual frames, fast, with no screenshots.

Two separate things live here, and the distinction matters:

  * The FRAME LOG (.tracker_frame_log.jsonl) stores the pixel grid + metadata
    for each moment, ~4KB per frame. Rebuilding images from it is fast because
    it is just numbers - but it is a 1600x compression of the real screen, so
    reconstructions show layout, colour and where text sits, NOT readable text.

  * The exact-pixel JPEGs in tracker_snapshots/ are the real thing. When you
    need to actually read something, use those.

Claude does not need either of these to know what is on screen - it reads OCR
text and vision structure directly from the tracker. These outputs exist so a
HUMAN can see what was happening, and scrub back through it.

Usage:
    python replay.py record 60        # log 60 seconds of frames
    python replay.py build 50         # rebuild the last 50 logged frames as images
    python replay.py sheet 50         # one contact-sheet image of the last 50
    python replay.py timeline         # text timeline of what changed when
"""
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
STATE_JSON = BASE_DIR / ".live_screen_state.json"
FRAME_LOG = BASE_DIR / ".tracker_frame_log.jsonl"
CHANGE_HISTORY = BASE_DIR / ".tracker_change_history.jsonl"
WINDOW_HISTORY = BASE_DIR / ".tracker_history.log"
OUT_DIR = BASE_DIR / "replay_out"
OUT_DIR.mkdir(exist_ok=True)


def record(seconds=60, interval=0.25):
    """Append the live grid + metadata to the frame log. ~4KB per frame, so a
    minute at 4fps is roughly 1MB - cheap enough to leave running."""
    end = time.time() + seconds
    n = 0
    last_sig = None
    while time.time() < end:
        try:
            d = json.loads(STATE_JSON.read_text(encoding="utf-8"))
            grid = d.get("pixel_grid")
            if grid:
                sig = hash(str(grid))
                if sig != last_sig:          # only log actual changes
                    last_sig = sig
                    entry = {
                        "t": d.get("timestamp"),
                        "clock": time.strftime("%H:%M:%S"),
                        "window": d.get("target_window"),
                        "foreground": d.get("actual_foreground"),
                        "process": d.get("foreground_process"),
                        "cols": d.get("grid_cols"), "rows": d.get("grid_rows"),
                        "grid": grid,
                        "text": (d.get("text_data") or "")[:400],
                        "mouse": [d.get("mouse_x"), d.get("mouse_y")],
                        "click": d.get("last_click"),
                    }
                    with open(FRAME_LOG, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    n += 1
        except Exception:
            pass
        time.sleep(interval)
    print(f"recorded {n} changed frames to {FRAME_LOG.name}")


def _load_frames(count):
    if not FRAME_LOG.exists():
        return []
    lines = FRAME_LOG.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines[-count:]:
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _to_image(entry, width=960, height=540):
    grid = np.array(entry["grid"], dtype=np.uint8)
    img = cv2.resize(grid[:, :, ::-1], (width, height), interpolation=cv2.INTER_CUBIC)
    label = f"{entry.get('clock','')}  {str(entry.get('foreground',''))[:40]}"
    cv2.rectangle(img, (0, 0), (width, 26), (0, 0, 0), -1)
    cv2.putText(img, label, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 130), 1)
    return img


def build(count=50):
    """Rebuild the last N logged frames as individual images."""
    frames = _load_frames(count)
    if not frames:
        print("No frames logged yet - run: python replay.py record 60")
        return
    t0 = time.time()
    for i, entry in enumerate(frames):
        cv2.imwrite(str(OUT_DIR / f"frame_{i:03d}.jpg"), _to_image(entry),
                    [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    dt = time.time() - t0
    print(f"built {len(frames)} images in {dt:.2f}s "
          f"({len(frames)/max(dt,0.001):.0f} images/sec) -> {OUT_DIR}")


def sheet(count=50, cols=10, cell_w=320, cell_h=180):
    """One contact-sheet image showing many moments at once - the fastest way
    for a human to see 'what happened over the last while' in a single glance."""
    frames = _load_frames(count)
    if not frames:
        print("No frames logged yet - run: python replay.py record 60")
        return
    t0 = time.time()
    rows = (len(frames) + cols - 1) // cols
    sheet_img = np.zeros((rows * cell_h, cols * cell_w, 3), dtype=np.uint8)
    for i, entry in enumerate(frames):
        cell = _to_image(entry, cell_w, cell_h)
        r, c = divmod(i, cols)
        sheet_img[r*cell_h:(r+1)*cell_h, c*cell_w:(c+1)*cell_w] = cell
    path = OUT_DIR / "contact_sheet.jpg"
    cv2.imwrite(str(path), sheet_img, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    print(f"contact sheet of {len(frames)} frames in {time.time()-t0:.2f}s -> {path}")


def timeline(limit=40):
    """Text timeline of what actually changed - usually more informative than
    the images, since it names the text that appeared and disappeared."""
    print("=== window switches ===")
    if WINDOW_HISTORY.exists():
        for line in WINDOW_HISTORY.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
            print("  " + line.strip())
    print("\n=== on-screen text changes ===")
    if CHANGE_HISTORY.exists():
        for line in CHANGE_HISTORY.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
            try:
                e = json.loads(line)
                app = ", ".join(e.get("appeared", [])[:6])
                gone = ", ".join(e.get("disappeared", [])[:6])
                print(f"  [{e.get('clock')}] {e.get('window','')}")
                if app:
                    print(f"      + {app}")
                if gone:
                    print(f"      - {gone}")
            except Exception:
                pass


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "timeline"
    arg = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if cmd == "record":
        record(arg or 60)
    elif cmd == "build":
        build(arg or 50)
    elif cmd == "sheet":
        sheet(arg or 50)
    else:
        timeline(arg or 40)
