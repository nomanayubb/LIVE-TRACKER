#!/usr/bin/env python3
"""
Always-on-top live overlay showing what the tracker currently sees.
Runs independently - reads the tracker's output file every 100ms and
displays it directly on screen. No chat interaction needed at all.
"""
import tkinter as tk
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent / ".live_screen_state.txt"

root = tk.Tk()
root.title("Tracker Overlay")
root.attributes("-topmost", True)
root.overrideredirect(True)  # no title bar, borderless
root.geometry("500x290+20+20")  # small, top-left corner
root.configure(bg="#111111")

label = tk.Label(
    root, text="Starting...", justify="left", anchor="nw",
    bg="#111111", fg="#00ff88", font=("Consolas", 10),
    padx=10, pady=8
)
label.pack(fill="both", expand=True)

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

        display = (
            f"LIVE (iter {iteration})  pixel loop: {loop_ms}ms (avg {avg_loop_ms}ms)\n"
            f"Foreground : {fg}\n"
            f"  process  : {fg_proc}\n"
            f"Tracking   : {target}  [{target_state}, alive={target_alive}]\n"
            f"Matching target: {match}   OCR text age: {ocr_age}ms\n"
            f"Mouse: {mouse}   Idle: {idle}s   Res: {resolution}\n"
            f"Brightness: {brightness}  Color: {dom_color}\n"
            f"Change: {change_pct}%  bbox={bbox}  Blobs: {blobs}\n"
            f"New text: {new_text[:50] if new_text != '?' else '-'}"
        )
        label.config(text=display)
    except Exception as e:
        label.config(text=f"Waiting for tracker...\n{e}")
    # 100ms is fine - measured at ~0.2 CPU-seconds/minute. The tracker's torch
    # thread pool was the real CPU contention, not this overlay.
    root.after(100, refresh)

refresh()
root.mainloop()
