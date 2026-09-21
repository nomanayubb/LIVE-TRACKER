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

**Four cadences, each running at its natural cost** — this split is the whole reason it's fast, and was arrived at by profiling, not guessing:

| Loop | Cadence | What it does | Why this cadence |
|---|---|---|---|
| Input/window | **~7.5ms** | mouse position, real clicks + attribution, foreground window, process+PID, idle time, window list | all sub-millisecond `ctypes`/`pygetwindow` calls — no screen capture needed |
| Pixel | ~50ms | brightness, 16×9 RGB pixel-grid matrix, change % + bounding box, selection-outline detection | `mss.grab` measured at **33ms** and full-res `cvtColor` at **12ms** — unavoidable, so it gets its own cadence instead of blocking everything |
| Vision | ~250ms | full structural scan via `vision.py` (rectangles, text regions, lines, corners, layout) | measured 40-90ms per scan; running it flat-out starved the other loops (fast loop degraded 15ms→175ms, OCR fell 23s behind) |
| OCR | ~150-300ms+ | EasyOCR text reading with bounding boxes | hard CPU floor on this machine (no CUDA/MPS) |

Every output carries its own freshness label (`OCR_AGE_MS`, `VISION_AGE_MS`) so you always know how stale each portion is relative to the millisecond-fresh input data. Nothing ever blocks the fast loop.

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

### 3. `vision.py` — structural screen understanding WITHOUT OCR

OCR reads *text* and costs 150-300ms. But most of a screen is *structure* — buttons, panels, icons, borders, highlights — which OpenCV detects geometrically far cheaper. This module answers "where are the clickable things, what shape are they, what changed, what's the layout?" rather than "what does that text say?".

Provides: `edge_profile`, `detect_rectangles` (buttons/panels), `detect_text_regions` (finds WHERE text is without reading it — use it to pick which small region deserves a real OCR pass), `detect_lines`, `detect_circles`, `detect_corners`, `color_regions`, `find_color` (any colour, any app's highlight), `dominant_palette`, `match_template` (find a known icon anywhere on screen — the key to app-agnostic automation), `perceptual_hash` + `hamming_distance` (cheap "did anything actually change?"), `motion_vectors` (optical flow — direction/speed of scrolling/dragging), `region_change_grid` (which grid cells changed), `detect_blinking` (carets, spinners), `layout_analysis` (panel dividers), and `scan_all` which runs the whole set efficiently.

Run `python vision.py` to re-run the built-in benchmark on your current screen.

**Measured costs (1920×1080, avg of 5):** individual detectors 15-32ms each; `scan_all(fast)` ~90ms; `scan_all(fast, scale=0.25)` ~42ms; `scan_all(heavy)` ~177ms. Downscaling is the main speed lever — `scan_all` deliberately resizes **once** and shares that frame with every detector, because benchmarking showed each detector resizing independently cost more than it saved.

### 4. `claude_activity.py` — activity declaration + click attribution

Any automation script imports this to declare what it's doing, so the tracker/admin panel can show "Claude: clicking in Blender > Add menu" instead of just "something happened":
```python
import claude_activity as ca
ca.set_activity(True, window="Blender", action="Clicking Add menu")
ca.log_click(319, 69)      # log before clicking, so the click can be attributed
pyautogui.click(319, 69)
ca.set_activity(False)
```
The tracker detects every real mouse click (via `GetAsyncKeyState` polling), then reports **who did it and where it landed**: `by` (claude/user), `app_title`, `app_process`, `app_pid`, and `app_layer` (foreground vs background window).

### 5. `admin_panel.py` — the user's control surface

A GUI dashboard (run `python admin_panel.py`) showing live: whether Claude is currently operating and in which window/action, what access it currently has, full capture statistics (frames, OCR scans, clicks broken down by Claude vs you), and per-window access checkboxes.

**STOP is a real kill switch** — it creates `.tracker_paused`, and the tracker blanks out *all* capture and reporting within ~200ms (writes only a "PAUSED" marker, captures nothing, shares no frames). The process stays alive so START resumes instantly. While paused, Claude receives no screen data whatsoever.

Per-window blocking writes to `.tracker_denied_windows.txt`; a denied window is skipped entirely rather than falling back to a full-screen grab (which would leak its contents anyway).

### 6. Change history — a replayable record, not just a snapshot

`.tracker_change_history.jsonl` is an append-only log. Every time on-screen text changes, one JSON line records the timestamp, window label, and exactly which text **appeared** and **disappeared**:
```json
{"t": 1789953875.29, "clock": "06:24:35", "window": "Blender",
 "appeared": ["render", "options"], "disappeared": ["scene", "viewlayer"]}
```
OCR re-runs whenever the frame is not bit-identical to the last one scanned (perceptual hash, 32×32, hamming distance 0) — so a single character appearing is enough to trigger a fresh read; nothing on screen is missed. The tradeoff is that a constantly-changing screen means OCR runs continuously and uses more CPU.

`.tracker_history.log` separately logs every foreground-window switch.

### 7. Precision mode (opt-in, off by default)

Normal tracking stays fast. When you need maximum capture detail, toggle **PRECISION** in the admin panel (or `touch .tracker_precision`) — it switches live, no restart:

| | Normal (default) | Precision |
|---|---|---|
| Pixel grid | 16×9 = 144 cells | **48×27 = 1296 cells** of real averaged RGB |
| Exact-pixel frame | not written | `.live_frame.jpg` rewritten on every visual change, linked from the JSON as `exact_frame_png` |
| Measured loop | ~3.0ms | ~3.4ms |

**On "data that can rebuild the exact screenshot":** a 1920×1080 frame is ~6.2 MB of raw pixels. Encoding all of them into JSON would be *larger and slower* than just writing an image file, so precision mode writes the real image to disk and links it from the report — you get exact original pixels without a separate screenshot step, and without a 6 MB JSON per frame. (An early attempt that PNG-encoded every frame took the loop from 90ms to 188ms; JPEG at q92, written only when the frame actually changes, made it essentially free.)

### 8. Performance notes (all measured, not assumed)

| Configuration | Fast loop |
|---|---|
| Tracker alone, static screen | **~4-9ms** |
| Tracker + overlay | ~5ms (overlay costs ~0.2 CPU-seconds/min — negligible) |
| Full stack, constantly-changing screen | ~90ms (OCR firing continuously by design) |

Things that turned out to matter, in order:
1. **`torch.set_num_threads(2)`** — by default PyTorch spread EasyOCR across every core, consuming ~6 cores' worth (297 CPU-seconds in 50s wall time) and starving everything else. This one line took total CPU from 297 → 58 CPU-seconds.
2. **Splitting the loop by cost type** — `mss.grab` is ~33ms and full-res `cvtColor` ~12ms, while *every* non-pixel signal (mouse, clicks, window focus, process, idle) costs <1ms combined. Pixel work runs on its own cadence so input signals stay millisecond-fresh.
3. **Skipping OCR on bit-identical frames** — on a static screen this eliminates essentially all OCR cost.
4. **Masking the tracker's own overlay/admin windows out of the frame** before OCR/vision — otherwise it recursively reads its own status text back into `TEXT_DATA`.

Things that turned out **not** to matter (measured, so don't re-optimize them): JSON serialization, file writes, window enumeration, clipboard reads, and the GUI processes' refresh rates — all sub-millisecond.

### 9. Verified click-automation pattern (see `click_cat.py` for the reference implementation)

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
- **DPI-awareness coordinate mismatch (important — call `ctypes.windll.user32.SetProcessDPIAware()` at the very top of ANY script that uses `pyautogui` to click, before importing pyautogui).** Without it, pyautogui's coordinate space silently disagrees with `ctypes`/`mss`'s physical-pixel space by the display's DPI scale factor — e.g. asking to click `(500,500)` actually lands at `(450,461)`. This was discovered while building click-attribution and likely explains some of the click-accuracy problems during the earlier Blender rigging automation attempts. `dual_tracker.py` already has this fix; any Blender-automation script must add it too.
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
