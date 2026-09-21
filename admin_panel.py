#!/usr/bin/env python3
"""
ADMIN CONTROL PANEL - the user-facing control surface for the tracker.

Shows: whether Claude is currently operating, what window/action, what access
it currently has, live statistics on exactly what data has been captured, and
gives hard START/STOP control plus per-window access toggles.

STOP is a real kill switch: it creates the pause file, and the tracker blanks
out ALL capture and reporting within ~200ms (process stays alive so START
resumes instantly). While paused, Claude receives no screen data at all.

Run:  python admin_panel.py
"""
import json
import tkinter as tk
from tkinter import ttk
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / ".live_screen_state.txt"
JSON_FILE = BASE_DIR / ".live_screen_state.json"
STATS_FILE = BASE_DIR / ".tracker_stats.json"
PAUSE_SWITCH = BASE_DIR / ".tracker_paused"
DENIED_WINDOWS_FILE = BASE_DIR / ".tracker_denied_windows.txt"
WINDOW_CONFIG = BASE_DIR / ".tracker_window_config.txt"

BG = "#0d1117"
FG = "#c9d1d9"
ACCENT = "#58a6ff"
OK = "#3fb950"
WARN = "#f85149"

root = tk.Tk()
root.title("Tracker Admin Panel")
root.geometry("760x680")
root.configure(bg=BG)

def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}

def read_denied():
    try:
        return [l.strip() for l in DENIED_WINDOWS_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        return []

def write_denied(entries):
    try:
        DENIED_WINDOWS_FILE.write_text("\n".join(entries) + ("\n" if entries else ""), encoding="utf-8")
    except Exception:
        pass

# ---------------- header: master start/stop ----------------
header = tk.Frame(root, bg=BG)
header.pack(fill="x", padx=14, pady=(14, 6))

status_label = tk.Label(header, text="●  checking...", font=("Segoe UI", 15, "bold"),
                        bg=BG, fg=FG, anchor="w")
status_label.pack(side="left")

def toggle_pause():
    if PAUSE_SWITCH.exists():
        try:
            PAUSE_SWITCH.unlink()
        except Exception:
            pass
    else:
        try:
            PAUSE_SWITCH.write_text("paused by admin panel", encoding="utf-8")
        except Exception:
            pass

toggle_btn = tk.Button(header, text="STOP", command=toggle_pause, width=14,
                       font=("Segoe UI", 12, "bold"), bg=WARN, fg="white",
                       relief="flat", cursor="hand2")
toggle_btn.pack(side="right")

PRECISION_SWITCH = BASE_DIR / ".tracker_precision"

def toggle_precision():
    """Precision mode: much richer per-frame detail (48x27 real-colour pixel
    grid + exact frame image saved on every change) at a real speed cost
    (measured ~90ms -> ~188ms loop). Off by default so normal tracking stays
    fast; turn it on only when maximum capture detail actually matters."""
    if PRECISION_SWITCH.exists():
        try:
            PRECISION_SWITCH.unlink()
        except Exception:
            pass
    else:
        try:
            PRECISION_SWITCH.write_text("precision mode on", encoding="utf-8")
        except Exception:
            pass

precision_btn = tk.Button(header, text="PRECISION: off", command=toggle_precision,
                          width=18, font=("Segoe UI", 10, "bold"), bg="#30363d",
                          fg=FG, relief="flat", cursor="hand2")
precision_btn.pack(side="right", padx=(0, 10))

# ---------------- Claude operating status ----------------
claude_frame = tk.LabelFrame(root, text=" Claude activity ", bg=BG, fg=ACCENT,
                             font=("Segoe UI", 10, "bold"), bd=1, relief="solid")
claude_frame.pack(fill="x", padx=14, pady=6)
claude_label = tk.Label(claude_frame, text="", justify="left", anchor="w",
                        bg=BG, fg=FG, font=("Consolas", 10), padx=10, pady=8)
claude_label.pack(fill="x")

# ---------------- what it currently sees / has access to ----------------
access_frame = tk.LabelFrame(root, text=" Current access ", bg=BG, fg=ACCENT,
                             font=("Segoe UI", 10, "bold"), bd=1, relief="solid")
access_frame.pack(fill="x", padx=14, pady=6)
access_label = tk.Label(access_frame, text="", justify="left", anchor="w",
                        bg=BG, fg=FG, font=("Consolas", 10), padx=10, pady=8)
access_label.pack(fill="x")

# ---------------- statistics ----------------
stats_frame = tk.LabelFrame(root, text=" Data captured (statistics) ", bg=BG, fg=ACCENT,
                            font=("Segoe UI", 10, "bold"), bd=1, relief="solid")
stats_frame.pack(fill="x", padx=14, pady=6)
stats_label = tk.Label(stats_frame, text="", justify="left", anchor="w",
                       bg=BG, fg=FG, font=("Consolas", 10), padx=10, pady=8)
stats_label.pack(fill="x")

# ---------------- per-window access control ----------------
win_frame = tk.LabelFrame(root, text=" Per-window access (uncheck to block Claude from that window) ",
                          bg=BG, fg=ACCENT, font=("Segoe UI", 10, "bold"), bd=1, relief="solid")
win_frame.pack(fill="both", expand=True, padx=14, pady=6)

canvas = tk.Canvas(win_frame, bg=BG, highlightthickness=0, height=200)
scrollbar = ttk.Scrollbar(win_frame, orient="vertical", command=canvas.yview)
win_inner = tk.Frame(canvas, bg=BG)
win_inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
canvas.create_window((0, 0), window=win_inner, anchor="nw")
canvas.configure(yscrollcommand=scrollbar.set)
canvas.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)
scrollbar.pack(side="right", fill="y")

window_vars = {}   # title -> BooleanVar(allowed)
rendered_windows = set()

def on_window_toggle(title, var):
    denied = read_denied()
    if var.get():                       # allowed -> remove from denylist
        denied = [d for d in denied if d != title]
    else:                               # blocked -> add to denylist
        if title not in denied:
            denied.append(title)
    write_denied(denied)

def rebuild_window_list(titles):
    global rendered_windows
    if set(titles) == rendered_windows:
        return                          # avoid rebuilding every tick (keeps checkboxes usable)
    rendered_windows = set(titles)
    for child in win_inner.winfo_children():
        child.destroy()
    window_vars.clear()
    denied = read_denied()
    for t in sorted(titles):
        allowed = not any(d.lower() in t.lower() for d in denied)
        var = tk.BooleanVar(value=allowed)
        window_vars[t] = var
        cb = tk.Checkbutton(win_inner, text=t[:70], variable=var, bg=BG, fg=FG,
                            selectcolor=BG, activebackground=BG, activeforeground=ACCENT,
                            font=("Consolas", 9), anchor="w",
                            command=lambda tt=t, vv=var: on_window_toggle(tt, vv))
        cb.pack(fill="x", anchor="w")

# ---------------- refresh loop ----------------
def refresh():
    paused = PAUSE_SWITCH.exists()
    data = read_json(JSON_FILE, {})
    stats = read_json(STATS_FILE, {})

    if paused:
        status_label.config(text="●  STOPPED — Claude receives no data", fg=WARN)
        toggle_btn.config(text="START", bg=OK)
    else:
        status_label.config(text="●  RUNNING — tracker active", fg=OK)
        toggle_btn.config(text="STOP", bg=WARN)

    claude_active = data.get("claude_active", False)
    claude_label.config(text=(
        f"Claude operating now : {'YES' if claude_active else 'no (idle)'}\n"
        f"Window it's acting in: {data.get('claude_window') or '-'}\n"
        f"Current action       : {data.get('claude_action') or '-'}\n"
        f"Private/hidden window: {data.get('claude_private', False)}\n"
        f"Last click detected  : {data.get('last_click') or '-'}"
    ))

    if paused:
        access_label.config(text="No access — all capture halted by admin STOP.")
    else:
        access_label.config(text=(
            f"Tracking window   : {data.get('target_window') or '-'}  "
            f"[{data.get('target_state','-')}, alive={data.get('target_alive','-')}]\n"
            f"Foreground now    : {data.get('actual_foreground','-')}  "
            f"({data.get('foreground_process','-')}, PID {data.get('foreground_pid','-')})\n"
            f"Windows visible   : {data.get('available_windows','-')}   "
            f"Blocked by you: {len(read_denied())}\n"
            f"Pixel loop        : {data.get('loop_ms','-')}ms   "
            f"OCR text age: {data.get('ocr_age_ms','-')}ms"
        ))

    if PRECISION_SWITCH.exists():
        precision_btn.config(text="PRECISION: ON", bg=ACCENT, fg="#0d1117")
    else:
        precision_btn.config(text="PRECISION: off", bg="#30363d", fg=FG)

    stats_label.config(text=(
        f"Capture detail        : {data.get('grid_cols','-')}x{data.get('grid_rows','-')} "
        f"pixel grid  ({'PRECISION' if data.get('precision_mode') else 'normal'} mode)\n"
        f"Uptime                : {stats.get('uptime_seconds','-')}s\n"
        f"Frames captured       : {stats.get('total_iterations','-')}\n"
        f"OCR text scans done   : {stats.get('ocr_passes','-')}\n"
        f"Clicks detected total : {stats.get('clicks_detected','-')}  "
        f"(by Claude: {stats.get('claude_clicks','-')}, by you: {stats.get('user_clicks','-')})\n"
        f"Blocked-window hits   : {stats.get('denied_window_blocks','-')}\n"
        f"Ticks spent paused    : {stats.get('paused_ticks','-')}"
    ))

    titles = data.get("window_list", [])
    if titles:
        rebuild_window_list(titles)

    # 200ms: measured, this panel uses ~0.2 CPU-seconds per minute (essentially
    # nothing) - the tracker's own torch threads were the real CPU hog, not this.
    root.after(200, refresh)

refresh()
root.mainloop()
