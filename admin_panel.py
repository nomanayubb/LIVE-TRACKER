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

# ---------------- Settings / reference window ----------------
# Surfaces the same mode table and JSON field reference that live in
# README.md, but inside the app itself - so a user watching this panel does
# not need to go find and open a text file to know what a field means or
# which mode to pick for a task.
JSON_FIELD_REFERENCE = """
COMPLETE .live_screen_state.json FIELD REFERENCE
(every key dual_tracker.py actually writes - see README.md for the same
table kept in sync)

  timestamp, frame_ts        Unix time of this report / of the last pixel capture
  iteration, total_iterations  Fast-loop tick counter
  loop_ms, avg_loop_ms        This tick's duration / rolling average (headline speed)
  status                      "Running" or "PAUSED"
  mouse_x, mouse_y            Live cursor position
  idle_seconds                Seconds since last input
  screen_resolution, monitor, monitor_count   Display geometry
  actual_foreground, foreground_process,      The genuinely focused window right
  foreground_pid, foreground_changed          now, regardless of tracker target
  target_window, target_window_obj,           What the tracker is pointed at,
  target_alive, target_state                  and its window state
  watched_windows              Secondary windows from a comma-separated config
  window_list, available_windows, new_windows All visible window titles / new ones
  clipboard                    Current clipboard text (first 200 chars)
  brightness, dominant_color   Pixel-grid summary stats
  pixel_grid, grid_cols, grid_rows   RGB colour grid (16x9 normal / 48x27 precision)
  precision_mode               Whether the richer grid + exact frame are active
  exact_frame_png              Path to the sharp JPEG, only when precision_mode=true
  frame_change_pct, frame_change_bbox   How much of the screen changed, and where
  selection_blob_count/_center Orange-outline (Blender-style selection) detector
  text_data, ocr_text, ocr_boxes, ocr_ts, ocr_age_ms,   OCR output, freshness, and
  ocr_pass_count, ocr_passes, new_text_tokens           newly-appeared text
  vision, vision_ts, vision_age_ms, vision_pass_count   Full vision.py structural
                                                          scan and its freshness
  last_click, clicks_detected, claude_clicks, user_clicks,   Click attribution -
  by, app_title, app_process, app_pid, app_layer,            who clicked, where,
  claude_delay_s                                             in which app
  claude_active, claude_action, claude_window, claude_private  What claude_activity.py
                                                                 says Claude is doing
  denied_window_blocks, paused_ticks   Admin-panel enforcement counters
  started_at                   When this tracker process started

If a field isn't listed here, it doesn't exist - check dual_tracker.py's
source for the literal string before assuming it's just undocumented.
"""

def open_settings():
    win = tk.Toplevel(root)
    win.title("Settings / Reference")
    win.geometry("820x640")
    win.configure(bg=BG)
    # Without this, the Toplevel can register with Windows (correct title,
    # geometry) but never actually get raised above whatever else is
    # foreground - verified: a screenshot of its exact screen region showed
    # a completely different app's content because this window opened behind
    # it. lift()+focus_force() ensures it's genuinely on top and interactive.
    win.lift()
    win.focus_force()
    win.attributes("-topmost", True)
    win.after(4000, lambda: win.attributes("-topmost", False))

    nb = ttk.Style()
    nb.theme_use("default")
    nb.configure("TNotebook", background=BG, borderwidth=0)
    nb.configure("TNotebook.Tab", background="#30363d", foreground=FG, padding=[12, 6])
    nb.map("TNotebook.Tab", background=[("selected", ACCENT)], foreground=[("selected", "#0d1117")])

    tabs = ttk.Notebook(win)
    tabs.pack(fill="both", expand=True, padx=10, pady=10)

    # --- Modes tab ---
    modes_tab = tk.Frame(tabs, bg=BG)
    tabs.add(modes_tab, text="Modes")
    if _modes:
        for name, p in _modes.PROFILES.items():
            box = tk.LabelFrame(modes_tab, text=f" {name} ", bg=BG, fg=ACCENT,
                                font=("Segoe UI", 10, "bold"), bd=1, relief="solid")
            box.pack(fill="x", padx=8, pady=4)
            on = [k.upper() for k, v in p["switches"].items() if v] or ["-"]
            off = [k for k, v in p["switches"].items() if not v] or ["-"]
            text = (f"{p['summary']}\n"
                    f"ON: {', '.join(on)}   OFF: {', '.join(off)}\n"
                    f"speed: {p['performance']}")
            tk.Label(box, text=text, justify="left", anchor="w", bg=BG, fg=FG,
                    font=("Consolas", 9), padx=8, pady=4, wraplength=760).pack(fill="x")

    # --- Field reference tab ---
    fields_tab = tk.Frame(tabs, bg=BG)
    tabs.add(fields_tab, text="JSON Fields")
    txt = tk.Text(fields_tab, bg="#0d1117", fg=FG, font=("Consolas", 9),
                  wrap="none", padx=10, pady=10, relief="flat")
    txt.insert("1.0", JSON_FIELD_REFERENCE.strip())
    txt.config(state="disabled")
    txt.pack(fill="both", expand=True)

    # --- Files tab ---
    files_tab = tk.Frame(tabs, bg=BG)
    tabs.add(files_tab, text="Files & Control")
    files_text = (
        "LIVE OUTPUT\n"
        "  .live_screen_state.txt / .json   rewritten every fast-loop tick\n"
        "  .live_frame.jpg                  exact-pixel frame (precision mode only)\n"
        "  tracker_snapshots/                rotating buffer, one saved every ~3s\n\n"
        "HISTORY (append-only logs, never overwritten)\n"
        "  .tracker_history.log             every foreground-window switch\n"
        "  .tracker_change_history.jsonl    every on-screen text change\n"
        "  .tracker_click_history.jsonl     every click, with attribution\n\n"
        "LIVE CONTROL (create/write these while the tracker runs)\n"
        "  .tracker_paused                  create to instantly halt all capture\n"
        "  .tracker_precision               create to enable precision mode\n"
        "  .tracker_window_config.txt       write window title(s), comma-separated\n"
        "  .tracker_denied_windows.txt      one blocked window-title substring per line\n"
        "  .tracker_refresh                 create to force one re-activation\n"
    )
    tk.Label(files_tab, text=files_text, justify="left", anchor="nw", bg=BG, fg=FG,
             font=("Consolas", 9), padx=10, pady=10).pack(fill="both", expand=True)

settings_btn = tk.Button(header, text="Settings", command=open_settings,
                         width=10, font=("Segoe UI", 10, "bold"), bg="#30363d",
                         fg=FG, relief="flat", cursor="hand2")
settings_btn.pack(side="right", padx=(0, 10))

# ---------------- task mode selector ----------------
try:
    import modes as _modes
except Exception:
    _modes = None

if _modes:
    mode_frame = tk.LabelFrame(root, text=" Task mode (one click sets every switch correctly) ",
                               bg=BG, fg=ACCENT, font=("Segoe UI", 10, "bold"),
                               bd=1, relief="solid")
    mode_frame.pack(fill="x", padx=14, pady=6)
    btn_row = tk.Frame(mode_frame, bg=BG)
    btn_row.pack(fill="x", padx=8, pady=(8, 2))

    mode_buttons = {}

    def _apply_mode(name):
        try:
            _modes.apply(name)
        except Exception:
            pass

    for _name in _modes.PROFILES:
        b = tk.Button(btn_row, text=_name.replace("_", " "),
                      command=lambda n=_name: _apply_mode(n),
                      font=("Segoe UI", 9), bg="#30363d", fg=FG,
                      relief="flat", cursor="hand2", padx=6)
        b.pack(side="left", padx=3)
        mode_buttons[_name] = b

    mode_desc = tk.Label(mode_frame, text="", justify="left", anchor="w", bg=BG,
                         fg="#8b949e", font=("Consolas", 9), padx=10, pady=6,
                         wraplength=720)
    mode_desc.pack(fill="x")

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

    if _modes:
        cur = _modes.current()
        active = cur.get("matching_profiles")
        for nm, btn in mode_buttons.items():
            if nm == active:
                btn.config(bg=ACCENT, fg="#0d1117", font=("Segoe UI", 9, "bold"))
            else:
                btn.config(bg="#30363d", fg=FG, font=("Segoe UI", 9))
        if active:
            p = _modes.PROFILES[active]
            mode_desc.config(text=f"{p['summary']}\nspeed: {p['performance']}")
        else:
            mode_desc.config(text="Custom switch combination (not a named profile)")

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
