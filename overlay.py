#!/usr/bin/env python3
"""
Always-on-top live overlay showing what the tracker currently sees.
Runs independently - reads the tracker's output file every 100ms and
displays it directly on screen. No chat interaction needed at all.
"""
import ctypes
ctypes.windll.user32.SetProcessDPIAware()  # same fix as admin_panel.py - without
# it, dragging this window can desync between where it visually is and where
# Windows delivers the drag's mouse input.

import json
import tkinter as tk
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / ".live_screen_state.txt"
STATE_JSON = BASE_DIR / ".live_screen_state.json"
CLICK_HISTORY = BASE_DIR / ".tracker_click_history.jsonl"

root = tk.Tk()
root.title("Tracker Overlay")
root.attributes("-topmost", True)
root.overrideredirect(True)  # no title bar, borderless
# Heightened from 290 to fit the added click-attribution + recent-clicks
# block (up to 6 history lines) without truncating.
root.geometry("560x440+20+20")  # top-left corner
root.configure(bg="#111111")

label = tk.Label(
    root, text="Starting...", justify="left", anchor="nw",
    bg="#111111", fg="#00ff88", font=("Consolas", 10),
    padx=10, pady=8
)
label.pack(fill="both", expand=True)

# A real, visible close button - double-click/Escape exist too, but a user
# asked specifically "does it have a cross button" and it didn't; those two
# alternatives aren't a substitute for an actual clickable X.
close_btn = tk.Label(root, text="✕", bg="#111111", fg="#ff5555",
                     font=("Consolas", 12, "bold"), cursor="hand2")
close_btn.place(relx=1.0, x=-6, y=4, anchor="ne")
close_btn.bind("<Button-1>", lambda e: root.destroy())
close_btn.bind("<Enter>", lambda e: close_btn.config(fg="#ffffff"))
close_btn.bind("<Leave>", lambda e: close_btn.config(fg="#ff5555"))

# Let the user drag the overlay by clicking anywhere on it
def start_move(event):
    root._drag_x = event.x
    root._drag_y = event.y

def do_move(event):
    x = root.winfo_pointerx() - root._drag_x
    y = root.winfo_pointery() - root._drag_y
    root.geometry(f"+{x}+{y}")

label.bind("<Button-1>", start_move)
label.bind("<B1-Motion>", do_move)

# No title bar (overrideredirect) means no X button and Windows gives no
# other built-in way to close this - confirmed there was no close binding at
# all before this, so the only option was killing the process from a
# terminal. Double-click is the common convention for borderless overlay
# windows; Escape also closes it if the overlay has focus.
label.bind("<Double-Button-1>", lambda e: root.destroy())
root.bind("<Escape>", lambda e: root.destroy())

def parse_field(text, name):
    for line in text.splitlines():
        if line.startswith(name + ":"):
            return line[len(name) + 1:].strip()
    return "?"

def refresh():
    try:
        content = STATE_FILE.read_text(encoding="utf-8", errors="ignore")
        fg = parse_field(content, "ACTUAL_FOREGROUND")
        fg_proc = parse_field(content, "FOREGROUND_PROCESS")
        target = parse_field(content, "TARGET_WINDOW")
        target_state = parse_field(content, "TARGET_STATE")
        target_alive = parse_field(content, "TARGET_ALIVE")
        brightness = parse_field(content, "BRIGHTNESS")
        dom_color = parse_field(content, "DOMINANT_COLOR")
        iteration = parse_field(content, "ITERATION")
        mouse = parse_field(content, "MOUSE")
        idle = parse_field(content, "IDLE_SECONDS")
        change_pct = parse_field(content, "FRAME_CHANGE_PCT")
        blobs = parse_field(content, "SELECTION_BLOBS")
        new_text = parse_field(content, "NEW_TEXT")
        resolution = parse_field(content, "SCREEN_RESOLUTION")
        loop_ms = parse_field(content, "LOOP_MS")
        avg_loop_ms = parse_field(content, "AVG_LOOP_MS")
        ocr_age = parse_field(content, "OCR_AGE_MS")
        bbox = parse_field(content, "FRAME_CHANGE_BBOX")
        match = "YES" if fg == target else "no"

        # Click attribution: read the structured JSON rather than parsing the
        # .txt file's Python-dict-repr string for LAST_CLICK, which is fragile.
        # This was previously computed by the tracker but never shown on the
        # overlay at all - confirmed by reading the old version of this file.
        last_click_line = "Last click : -"
        try:
            state = json.loads(STATE_JSON.read_text(encoding="utf-8"))
            lc = state.get("last_click")
            if lc:
                who = "YOU" if lc["by"] == "user" else "CLAUDE"
                last_click_line = (f"Last click : {who} -> {lc['app_title'][:28]} "
                                   f"({lc['x']},{lc['y']}) [{lc['app_layer']}]")
        except Exception:
            pass

        # Recent clicks across multiple windows, newest first - so attribution
        # is visible per-window, not just the single latest click.
        recent_lines = []
        try:
            lines = CLICK_HISTORY.read_text(encoding="utf-8", errors="ignore").splitlines()
            for line in reversed(lines[-15:]):
                e = json.loads(line)
                who = "you" if e["by"] == "user" else "CLAUDE"
                recent_lines.append(f"  {e.get('clock','')} {who:6s} -> {e['app_title'][:22]}")
        except Exception:
            pass
        recent_block = "\n".join(recent_lines) if recent_lines else "  (none yet)"

        display = (
            f"LIVE (iter {iteration})  pixel loop: {loop_ms}ms (avg {avg_loop_ms}ms)\n"
            f"Foreground : {fg}\n"
            f"  process  : {fg_proc}\n"
            f"Tracking   : {target}  [{target_state}, alive={target_alive}]\n"
            f"Matching target: {match}   OCR text age: {ocr_age}ms\n"
            f"Mouse: {mouse}   Idle: {idle}s   Res: {resolution}\n"
            f"Brightness: {brightness}  Color: {dom_color}\n"
            f"Change: {change_pct}%  bbox={bbox}  Blobs: {blobs}\n"
            f"New text: {new_text[:50] if new_text != '?' else '-'}\n"
            f"{last_click_line}\n"
            f"Recent clicks (who -> which window):\n{recent_block}"
        )
        label.config(text=display)
    except Exception as e:
        label.config(text=f"Waiting for tracker...\n{e}")
    # 100ms is fine - measured at ~0.2 CPU-seconds/minute. The tracker's torch
    # thread pool was the real CPU contention, not this overlay.
    root.after(100, refresh)

refresh()
root.mainloop()
