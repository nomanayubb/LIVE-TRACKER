# Live Tracker System

A real-time, non-Blender-specific screen-awareness system: it watches whatever window/app you point it at, reports what's happening on screen in near-real-time, and lets automation scripts verify their own actions instead of guessing blindly. Originally built to debug a Blender rigging issue, but works for **any application** — reuse it in any future session for any app.

## ⚠️ MANDATORY WORKFLOW RULE — READ THIS FIRST, EVERY SESSION

**If you (human or Claude) are opening this project in a brand-new session for the first time: stop and read this entire README top to bottom before touching any file or running any command.** It is the single source of truth for what exists, what works, what's deprecated, and what's still broken. Do not assume anything from a chat summary or memory snapshot — this file is more current than either.

**Any time you add, change, or remove anything here, update this README in the same session, before moving on to the next task — never defer it "for later."** Specifically:

| If you change/add...                          | You MUST update...                                              |
|------------------------------------------------|-------------------------------------------------------------------|
| Any field in `dual_tracker.py`'s output         | The "Outputs" section below AND the field list                    |
| A new script/feature file                       | The "Current architecture" section (add it) and/or "Roadmap" (remove it once built) |
| Anything that makes an existing script obsolete | Move the old file into "Deprecated files" with a one-line reason  |
| A new environment gotcha/limitation discovered  | The "Known environment constraints" section                       |
| Progress on the actual Blender rigging task     | The final "Blender task" section at the bottom                    |
| Anything at all                                 | Commit the change to the local git repo in this folder (see Git section) with a clear message |

If a script mentioned here no longer matches reality, fix the mismatch immediately (update the doc or the code, whichever is correct) — a stale README is worse than no README.

---

## Current architecture (use these — see "Deprecated files" below for what NOT to use)

### 1. `dual_tracker.py` — the tracker to run (supersedes `live_tracker.py`, `universal_tracker.py`, `full_tracker.py`)

Two threads running in parallel:
- **Fast pixel loop** (~15-20ms/frame): mouse position, idle time, real foreground window (`ACTUAL_FOREGROUND`), process name+PID, window list, brightness, dominant color, a genuine **16×9 pixel-grid matrix** (real per-cell RGB color, not just one brightness number), frame-change % **and bounding box** (where on screen changed), orange-selection-outline detection (Blender-style), clipboard content.
- **Slow OCR thread** (~150-300ms, EasyOCR on CPU — this is a hard floor, cannot go faster on this machine): reads on-screen text with bounding boxes. Runs independently, never blocks the fast loop. The merged output always includes `OCR_AGE_MS` so you know exactly how stale the text portion is relative to the pixel portion.

**Run it:**
```bash
cd "C:\Users\noman\Desktop\live-tracker"
source venv/Scripts/activate
nohup python dual_tracker.py "Blender" > tracker.log 2>&1 &
```
Replace `"Blender"` with any window title substring to track a different app. Omit it to auto-track the first available window.

**Outputs:**
- `~/.live_screen_state.txt` — human-readable snapshot, rewritten every fast-loop tick
- `~/.live_screen_state.json` — same data, full structure incl. `pixel_grid` and `ocr_boxes`
- `~/.tracker_history.log` — append-only, timestamped log of every foreground-window change
- `~/tracker_snapshots/` — rotating buffer of the last 20 real PNG screenshots (one saved every ~3s)

**Live control files** (create/write these while the tracker is running):
- `~/.tracker_window_config.txt` — write a window-title substring to switch what the tracker targets, live, without restarting
- `~/.tracker_refresh` — create this empty file to force one immediate re-activation/re-verify of the target window (auto-deleted after use)

### 2. `overlay.py` — the live on-screen HUD

Always-on-top, borderless, draggable window (top-left corner by default) showing the tracker's live stats **directly on your screen, independent of any chat session**. This is the actual fix for "I want to see this live": a chat assistant can only react when invoked, so it can never be truly real-time — the overlay is. Updates every 100ms by reading the tracker's output file itself.

**Run it:**
```bash
cd "C:\Users\noman\Desktop\live-tracker"
source venv/Scripts/activate
nohup python overlay.py > overlay.log 2>&1 &
```
Click-and-drag anywhere on the overlay to reposition it.

### 3. Verified click-automation pattern (see `click_cat.py` for the reference implementation)

**The one rule that matters:** always re-verify focus (via brightness check or `ACTUAL_FOREGROUND`) **immediately before every single click/keystroke, inside the same script run.** Never split "activate window" and "click" across two separate script invocations — focus reverts to whatever invoked the script (the terminal) the instant a script exits, so a second script starting later can't assume the target is still focused. This was the root cause of nearly every failed automation attempt this session.

**Window-activation trick that actually works on this machine:** `window.minimize(); window.restore()`. Plain `.activate()` (`SetForegroundWindow`) fails **silently** — no exception, just doesn't work — because of Windows' focus-steal prevention when the caller process isn't already foreground.

**Menu automation:** clicking a persistent header menu (e.g. Blender's "Add" menu in the top bar) is far more reliable than a keyboard shortcut chord like `Shift+A`, which is sensitive to exact mouse-over-viewport timing and can misfire (e.g. registered as "extend selection" instead of opening the menu).

**Best verification method:** save a real screenshot to disk and view it with the Read tool (visual inspection), rather than relying only on OCR text. This is how the cat mesh, selection outlines, and exact click coordinates were all confirmed this session.

---

## Roadmap / requested features (not yet built — build these next, and move them to "Current architecture" above once done)

1. **Claude-activity status** — a shared status file (e.g. `~/.claude_activity.json`) that any automation script writes to when it starts/stops an action: which window, which specific action, timestamp, whether it's a private/background window or one visible in the user's taskbar. The overlay should display "Claude: idle" vs "Claude: clicking in Blender > Add menu" live.
2. **Click-origin attribution** — since the OS can't natively distinguish a synthetic (pyautogui) click from a real hardware click, approximate it: every automation script logs each click it performs (`~/.claude_click_log.txt`, timestamped x/y). The overlay/tracker compares a detected click against this log — if a real click coincides with a very recent logged entry, attribute it to Claude; otherwise attribute it to the user.
3. **Multi-window / multi-tab tracking without interference** — extend `dual_tracker.py` to watch several target windows at once (a list instead of a single `current_target`), reporting each one's state independently, so automation can work in one window while genuinely not disturbing others.
4. **Admin control panel** — a small settings UI (likely `overlay.py` extended, or a separate `admin_panel.py`) letting the user toggle individual features on/off at runtime (OCR on/off, screenshot buffer on/off, which signals to compute, which window(s) to track) without editing code.
5. **Richer per-frame pixel reporting** — expand beyond the 16×9 grid on request (configurable resolution), and/or full-resolution raw frame export on demand for a specific instant.

---

## Known environment constraints (don't relitigate these — they're settled)

- **No internet access** in this environment. `pywin32` and `psutil` could not be installed via pip. All "process name", "idle time", "cursor position" features are built with raw `ctypes` calls to `user32`/`kernel32` directly instead (see the top of `dual_tracker.py`).
- **EasyOCR on this CPU has no CUDA/MPS** — a single OCR pass takes ~150-300ms. This is a hard floor; the dual-loop architecture works around it rather than fighting it.
- **`killall python` does not work** in this Git Bash environment. Use `taskkill //F //IM python.exe` instead (note the double-slash for Git Bash).
- **Blender's window repeatedly gets minimized** between sessions/turns (pygetwindow reports it parked at `-25600,-25600`). Always check `TARGET_STATE`/`isMinimized` before trusting a capture, and restore with `.minimize(); .restore()` if needed.
- **Do not use chained-`sleep` polling loops** (e.g. `for i in ...; do sleep 1; done`) — they get auto-rejected by the harness as a disguised long-blocking command. Use single on-demand checks instead, or the proper background/Monitor tooling if the harness offers it.

---

## Deprecated files — do not use, kept only for history

- `live_tracker.py` — first working version, single-threaded, OCR blocks the whole loop. Superseded by `dual_tracker.py`.
- `universal_tracker.py` — added window-switching and `ACTUAL_FOREGROUND`, still single-threaded/OCR-blocking. Superseded by `dual_tracker.py`.
- `full_tracker.py` — added the 20 extra signals but still single-threaded (had a serious perf bug: recreated a Tk() window every frame just for clipboard access, ~9.4s/frame). Superseded by `dual_tracker.py`, which fixed the clipboard bug AND split into fast/slow threads.
- `blender_automation_final.py`, `automate_rigging.py`, `focus_and_automate.py`, `run_rigging.py`, `multi_handler_system*.py`, `import_cat.py`, `import_model.py`, `mcp_server.py` — early blind-automation attempts (no verification loop), all unreliable, superseded by the verify-before-every-click pattern in `click_cat.py`/`select_by_name.py`/`do_rigging.py`.
- `*.log` files scattered in this folder — just run output, safe to delete anytime, not part of the system itself.

## Git

This folder has its **own dedicated git repo** (deliberately NOT part of the larger `C:\Users\noman` home-directory repo, to avoid pulling unrelated personal files into version control).

Remote: https://github.com/nomanayubb/LIVE-TRACKER

After making any change (per the table above), commit and push:
```bash
cd "C:\Users\noman\Desktop\live-tracker"
git add -A
git commit -m "describe what changed and why"
git push
```
`venv/`, `__pycache__/`, and `*.log` files are gitignored — they're machine-specific/disposable, not part of the system itself.

## The actual Blender task this was all built for

Original bug report: mesh doesn't move in pose mode after applying automated weights.

Progress so far:
- Cat model (glTF import, object name `Mesh_0`) confirmed loaded in Blender 3.5.1, alongside default Cube/Camera/Light — confirmed visually via screenshot.
- Cat mesh selected successfully via verified click automation.
- Armature-add step (`Add` menu → `Armature`) attempted multiple times, **not yet confirmed successful** — focus kept reverting to Claude Code mid-sequence before earlier fixes were in place. Retry this using `dual_tracker.py`'s `ACTUAL_FOREGROUND` field to gate every click.
- Not yet done: parent mesh to armature with automatic weights, enter Pose Mode, test that the mesh actually deforms when a bone is moved (this is the actual fix/diagnosis for the original bug).
