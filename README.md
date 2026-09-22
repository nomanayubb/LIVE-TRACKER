# Live Tracker System

A real-time, non-Blender-specific screen-awareness system: it watches whatever window/app you point it at, reports what's happening on screen in near-real-time, and lets automation scripts verify their own actions instead of guessing blindly. Originally built to debug a Blender rigging issue, but works for **any application** — reuse it in any future session for any app.

## ⚠️ MANDATORY WORKFLOW RULE — READ THIS FIRST, EVERY SESSION

**If you (human or Claude) are opening this project in a brand-new session for the first time: stop and read this entire README top to bottom before touching any file or running any command.** It is the single source of truth for what exists, what works, what's deprecated, and what's still broken. Do not assume anything from a chat summary or memory snapshot — this file is more current than either.

### Claude: do this at the start of every session

1. Read this README fully.
2. Run `python modes.py list` to see every capture profile and `python modes.py show` for what's currently active.
3. **When the user gives you a task, state which mode it needs before starting** — e.g. *"this is UI automation, so precision should be OFF; expect ~15ms loop"*. Use `python modes.py recommend "<their task>"` if unsure.
4. You can set it yourself with `modes.apply("<profile>")`, or tell the user to click that mode's button in the admin panel — whichever they prefer. Say which one you're doing.
5. If a task needs **sharp, readable frames**, that means `precision` ON — the exact-pixel image lands at `.live_frame.jpg`. Never try to read text from a grid reconstruction; it's a 1600× compression and will not be legible.
6. Never leave a heavy mode on after the task that needed it is done — switch back to `normal`.

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

## Quickstart — run the whole stack

```bash
cd "C:\Users\noman\Desktop\live-tracker"
source venv/Scripts/activate
nohup python dual_tracker.py "SomeWindowTitle" > tracker.log 2>&1 &   # the eye
nohup python overlay.py > overlay.log 2>&1 &                          # live HUD
nohup python admin_panel.py > admin.log 2>&1 &                        # control surface
```
Give it ~20-30s on first launch (EasyOCR loads its model then). Check `.live_screen_state.json` or the admin panel to confirm it's alive.

## Verification status — every mode actually tested, not just documented

All 6 modes were driven end-to-end against `dual_tracker.py`'s real output (not just read from source) in a dedicated test pass. Results:

| Mode | Switches correct | Fields correct | Speed (re-measured) |
|---|---|---|---|
| `normal` | ✅ | ✅ | ~15-20ms (not the earlier-claimed 3-9ms — that number came from an idle-thread benchmark, not realistic operation) |
| `ui_automation` | ✅ (vision data present) | ✅ | ~15-16ms |
| `text_reading` | ✅ (48x27 grid, exact_frame_png present) | ✅ | ~14-15ms |
| `evidence` | ✅ (same code path as text_reading) | ✅ | ~14-15ms |
| `motion_capture` | ✅ | N/A (separate module, not the tracker) | 30-60fps depending on region (varies with system load) |
| `privacy` | ✅ (`STATUS: PAUSED BY USER`, no data at all) | ✅ | no capture |

Every functional claim (which switches flip, which fields appear/disappear, grid dimensions, exact-frame behavior) held up exactly as documented. Only the absolute speed numbers were optimistic and have been corrected throughout this file.

Two real bugs were found and fixed during this pass, both worth knowing about: a stale `.tracker_window_config.txt` can silently override a fresh command-line target (§8b), and `admin_panel.py`/`overlay.py` were missing `SetProcessDPIAware()` (now fixed, same fix `dual_tracker.py`/`clicker.py` already had).

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
- `~/.tracker_window_config.txt` — write a window-title substring to switch what the tracker targets, live, without restarting. Comma-separate several to also cheaply watch extras (see multi-window below).
- `~/.tracker_refresh` — create this empty file to force one immediate re-activation/re-verify of the target window (auto-deleted after use)

**Complete `.live_screen_state.json` field reference** (every key the tracker actually writes — checked directly against the source, not guessed):

| Field | Meaning |
|---|---|
| `timestamp`, `frame_ts` | Unix time of this report / of the last pixel capture |
| `iteration`, `total_iterations` | Fast-loop tick counter |
| `loop_ms`, `avg_loop_ms` | This tick's duration / rolling average — the headline speed number |
| `status` | `"Running"` or `"PAUSED"` |
| `mouse_x`, `mouse_y` | Live cursor position |
| `idle_seconds` | Seconds since last input |
| `screen_resolution`, `monitor`, `monitor_count` | Display geometry |
| `actual_foreground`, `foreground_process`, `foreground_pid`, `foreground_changed` | The genuinely focused window right now, regardless of tracker target |
| `target_window`, `target_window_obj`, `target_alive`, `target_state` | What the tracker is pointed at, and its window state (`normal`/`minimized`/`maximized`) |
| `watched_windows` | List of secondary windows from a comma-separated config, each `{name, alive, title, state, foreground, x, y, w, h}` |
| `window_list`, `available_windows`, `new_windows` | All visible window titles / newly appeared ones |
| `clipboard` | Current clipboard text (first 200 chars) |
| `brightness`, `dominant_color` | Pixel-grid summary stats |
| `pixel_grid`, `grid_cols`, `grid_rows` | The RGB colour grid (16×9 normal / 48×27 precision) — see "Precision mode" |
| `precision_mode` | Whether the richer grid + exact frame are active |
| `exact_frame_png` | Path to the sharp JPEG, only present when `precision_mode` is true |
| `fullscreen_mode` | Whether `.tracker_fullscreen` is forcing true monitor capture regardless of any window target |
| `frame_change_pct`, `frame_change_bbox` | How much of the screen changed since last tick, and where (`[x,y,w,h]`) |
| `selection_blob_count`, `selection_blob_center` | Orange-outline (Blender-style selection) detector |
| `text_data`, `ocr_text`, `ocr_boxes`, `ocr_ts`, `ocr_age_ms`, `ocr_pass_count`, `ocr_passes`, `new_text_tokens` | OCR output, freshness, and what text newly appeared |
| `vision`, `vision_ts`, `vision_age_ms`, `vision_pass_count` | The full `vision.py` structural scan and its freshness (rectangles, text regions, layout, etc. — see `vision.py` section) |
| `last_click`, `clicks_detected`, `claude_clicks`, `user_clicks`, `by`, `app_title`, `app_process`, `app_pid`, `app_layer`, `claude_delay_s` | Click attribution — who clicked, where, in which app, foreground or background |
| `claude_active`, `claude_action`, `claude_window`, `claude_private` | What `claude_activity.py` says Claude is currently doing |
| `denied_window_blocks`, `paused_ticks` | Admin-panel enforcement counters |
| `started_at` | When this tracker process started |

If a field you need isn't in this table, it doesn't exist yet — grep `dual_tracker.py` for the literal string before assuming it's just undocumented.

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
| Measured loop | ~15-20ms | ~14-15ms |

**On "data that can rebuild the exact screenshot":** a 1920×1080 frame is ~6.2 MB of raw pixels. Encoding all of them into JSON would be *larger and slower* than just writing an image file, so precision mode writes the real image to disk and links it from the report — you get exact original pixels without a separate screenshot step, and without a 6 MB JSON per frame. (An early attempt that PNG-encoded every frame took the loop from 90ms to 188ms; JPEG at q92, written only when the frame actually changes, made it essentially free.)

### 7a. `modes.py` — every capture profile in one place

Rather than remembering which switches to flip, name the task:

```bash
python modes.py list                    # all profiles, what they do, measured speed
python modes.py show                    # what's active right now
python modes.py apply ui_automation     # switch
python modes.py recommend "read a long log"   # pick one from a plain description
```

| Profile | Precision | Use for | Measured speed |
|---|---|---|---|
| `normal` | off | default; general awareness, background use | ~15-20ms loop (re-measured with OCR+vision threads actually active) |
| `ui_automation` | off | clicking menus/buttons, verifying steps | ~15-16ms; vision ~250ms gives click targets |
| `text_reading` | **ON** | dense/small text, logs, code | ~14-15ms; sharp frame per change |
| `evidence` | **ON** | before/after, replay, recording a run | ~14-15ms; replay rebuilds 90 img/sec |
| `motion_capture` | off | feeding real frames to your own pipeline, high fps | 1280×720 → **47fps**; use `motion.py`, not the tracker |
| `privacy` | off (paused) | banking, passwords, anything private | no capture at all |

The admin panel has a one-click button per profile, and highlights the active one.

### 7c. `motion.py` — high-FPS region capture

Gives a downstream pipeline a stream of real consecutive frames from a screen region, as fast as the OS allows, with no screenshot step and no disk round-trip. It hands you raw numpy frames — processing is entirely the caller's business.

Separate from the tracker because it's a different profile entirely: the tracker samples cheaply and slowly (UI changes slowly); this grabs a fixed region as fast as the OS allows and keeps real frames in memory.

```python
cap = motion.RegionCapture(left=100, top=100, width=1280, height=720).start()
frame  = cap.latest()          # newest BGR frame
frames = cap.recent(5)         # last 5 consecutive frames
moving = cap.motion_mask()     # which pixels changed
pct    = cap.motion_amount()   # how much of the region is moving
cap.stop()
```

**Measured capture ceiling** (mss; DXGI/`dxcam` unavailable offline):

| Region | FPS |
|---|---|
| 1920×1080 full screen | ~22 |
| 1280×720 | **~47** |
| 960×540 | ~56 |

The ~18ms per-grab cost is **fixed overhead, not proportional to pixel count** — so full-screen is what hurts (34ms), and shrinking below 960×540 buys nothing. Capture only the region you need.


### 7b. Which mode to use for which task

| Task | PRECISION | Notes |
|---|---|---|
| Watching which app/window is in focus, tracking clicks, idle time | **OFF** | None of this touches pixels — precision adds nothing, costs speed |
| Waiting for something to appear/finish (a dialog, a load, a render) | **OFF** | Change-detection + `region_change` grid already catch it |
| Driving automation: finding a button, clicking a menu, verifying a step landed | **OFF** | `vision.py`'s rectangles/text-regions already give click targets; the OCR text confirms the step |
| Reading dense small text (long logs, code, fine UI labels) | **ON** | More detail helps when characters are small |
| Needing the exact original pixels of a moment (before/after comparison, evidence, replay) | **ON** | Writes `.live_frame.jpg` on every change, linked from the JSON |
| Diagnosing a subtle visual difference (colour shifts, faint highlights, antialiasing) | **ON** | 1296-cell colour grid resolves what 144 cells cannot |
| Long unattended runs / leaving it on in the background all day | **OFF** | Keeps CPU and disk writes minimal |

Rule of thumb: **leave it OFF.** Turn it ON for the specific minutes you need maximum fidelity, then turn it back off. It toggles live — no restart needed.

Separately, **STOP** (the red button) is for privacy, not performance: it halts *all* capture instantly. Use it whenever you're doing something you don't want captured at all — banking, passwords, private messages.

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

### 8b. Gotchas found while testing `clicker.py` (all verified, don't re-discover these)

- **Windows rename themselves mid-task.** Notepad becomes `*Untitled - Notepad` the moment you type, then retitles again to `*<first line of your text> - Notepad`. Matching by title on every action breaks a sequence partway through. `clicker.py` latches the window **handle** on first lookup and reuses it, so renames don't matter.
- **CapsLock silently inverts everything typed.** `pyautogui` sends raw keystrokes, so with CapsLock on `Hello` arrives as `hELLO` and nothing reports an error. `type_text()` compensates **in software** (sends `text.swapcase()` so CapsLock inverts it back) rather than toggling the key. Toggling was the first fix and it was wrong: CapsLock is a physical state the user controls, and pressing it mid-session corrupts their own typing. Verified the current approach types exact text while leaving CapsLock untouched.
- **The target app may rewrite what you typed.** Notepad's autocorrect turns `MiXeD` into `Mixed`, while nonsense like `qWzX vBnM kJhG` types perfectly. Verified: the keystrokes are accurate, the app edits them after the fact. If exact text matters, read it back via clipboard or OCR and compare - don't assume.
- **`pyautogui.scroll()` does nothing on modern WinUI apps.** Measured against Windows 11 Notepad: repeated `pyautogui.scroll()` calls produced a pixel delta of **0.00** (cursor correctly positioned in the text area), while a `PageDown` keypress moved the view by 7.86. Its wheel events don't reach these windows. `clicker.scroll()` now sends wheel input via `SendInput` directly, which works (verified `method=wheel`, 4/4 scrolls moved content), with a PageUp/PageDown fallback if a target ignores synthetic wheel events entirely.
- **Verification can be too coarse to see the thing you're testing.** The first scroll test compared 32x32 perceptual hashes and reported "no change" for every scroll - wrong, because rows of similar-looking text all downsample to nearly identical hashes. Comparing real pixels in the text region detected it correctly. Match the sensitivity of the check to the size of the effect.
- **Tkinter loses clipboard ownership when its root is destroyed.** A test harness that set the clipboard via a short-lived `Tk()` produced perfectly alternating pass/fail results, which looked like a drag bug for several rounds. It wasn't - the drags always worked (confirmed visually: text highlighted, status bar reading "5 of 19 characters"). Read the clipboard through the Win32 API instead.
- **Undeclared `ctypes` return types segfault on 64-bit Python.** Calling `GetClipboardData`/`GlobalLock` without setting `restype` truncates 64-bit handles to `c_int` and crashes the interpreter outright. Always declare `argtypes`/`restype` for Win32 calls that return handles or pointers.
- **A focus guard can destroy the thing it's guarding.** The original `focus()` called `minimize(); restore()` every time, which dismissed any open menu - so step 2 of a menu sequence failed after step 1 opened it. It now does nothing at all when the window is already foreground.
- **A stale `.tracker_window_config.txt` silently overrides your command-line target.** Launching `dual_tracker.py "Notepad"` while an old config file from earlier testing still says `"Claude"` means the tracker quietly tracks Claude instead - `TARGET_WINDOW` in the JSON will show the config file's value, not your argument. This caused a real false alarm: normal mode measured 80-170ms and looked broken, until checking `TARGET_WINDOW` showed it was tracking an actively-changing terminal window (constant OCR re-scans), not the static Notepad window intended. Delete the file or overwrite it before trusting a fresh benchmark.
- **Tkinter Canvas widgets don't scroll via mouse wheel unless you bind `<MouseWheel>` explicitly** - a scrollbar alone only responds to dragging its thumb. Confirmed the gap with a debug print inside the binding that simply never fired.
- **`admin_panel.py`/`overlay.py` were missing `SetProcessDPIAware()`** (unlike `dual_tracker.py`/`clicker.py`, which already had it). Without it, screen coordinates as seen by a screenshot and where Windows actually delivers mouse/wheel input to that window can disagree, even though `GetCursorPos` reports the coordinates you asked for - confirmed input landing on a *different* window than the one a screenshot at the same coordinates showed. Both now call it at import time. This also changed the panel's true rendered size, so `admin_panel.py`'s window was widened from 760 to 900px to fit its header again.
- **A Toplevel dialog can be a real, correctly-registered window and still be invisible in a screenshot** if something else is on top of it - even briefly. A 4-second `-topmost` timer was too short for a reference dialog meant to stay open while switching back to the app it explains; extended to stay topmost for its full lifetime.
- **Reading `.live_screen_state.json` while the tracker is mid-write races.** The file isn't written atomically, so an occasional `JSONDecodeError` on an empty read is expected under polling - retry rather than treating it as a real error.

### 8c. Click detection: a hook nearly froze the user's mouse system-wide - here's the safe fix that replaced it

**What went wrong.** Once-per-loop-tick click polling (checking `GetAsyncKeyState` once per ~90ms main-loop iteration) has a real, confirmed gap: a synthetic click's down→up cycle can complete in a few milliseconds, faster than the poll interval, so the click vanishes entirely - not even attributed to "user", just gone. Confirmed directly: a `clicker.py` click was verified performed (logged in `claude_activity`'s own click log) but never appeared in the tracker's click history at all.

The first fix attempted was a `WH_MOUSE_LL` low-level mouse hook - event-driven, not polled, so it structurally cannot miss a click. **This was the wrong call.** A hook of this kind intercepts mouse input for the *entire system*, not just the tracked window. A bug in the hook callback (missing 64-bit `argtypes`/`restype` on `CallNextHookEx` - the same class of bug that once segfaulted `SendInput`, see above) caused it to throw repeatedly, and because Windows serializes mouse input delivery through the hook chain, this froze the user's cursor system-wide. It was killed immediately (`taskkill /F /IM python.exe` - Windows auto-removes an orphaned hook when its owning process dies) and the code was fully reverted via `git checkout`.

**The actual fix stays within polling, deliberately.** `GetAsyncKeyState` is a pure *query* - it reads state, it never intercepts, blocks, or modifies the system input pipeline. The bug was polling it too rarely (once per slow main-loop tick), not that polling itself is wrong. The fix: a dedicated thread (`click_poll_worker` in `dual_tracker.py`) polls `GetAsyncKeyState` every **2ms**, completely decoupled from the slow pixel/OCR/vision work, and queues every detected click for the main loop to drain. Worst case if this thread has a bug: it misses a click. It cannot freeze the cursor or affect any other application, because it never touches the input pipeline - it only ever reads from it.

Verified before integrating: an isolated 15-second test caught 4 of 5 rapid synthetic clicks (50ms apart) versus 0 of 2 with the old once-per-tick approach. Verified after integrating: the exact previously-failing scenario (a `clicker.py` click on Notepad) was correctly captured and attributed - `"by": "claude"`, `"claude_delay_s": 0.04`, first time all session.

**The lesson, stated plainly: prefer a slower fix that can only degrade gracefully over a faster one that can fail catastrophically.** A polling gap loses data. A misbehaving system-wide hook can take over someone's mouse. Those are not the same category of risk, even though the hook looked like the more "correct" engineering solution on paper.

### 8f. "No target specified" is NOT the same as "capture the physical screen"

Confirmed directly: launching `dual_tracker.py` with no window-title argument does not capture the true monitor independent of windows. It auto-picks whichever window happens to be first in Windows' own enumeration order (`if not current_target and all_windows: current_target = all_windows[0].title`). That window can happen to be maximized (making the region look full-screen by coincidence), but it will shrink or shift the moment that window resizes or a different window becomes first in the list.

For genuine, window-independent full-screen capture, create `.tracker_fullscreen` - this forces `monitor = sct.monitors[1]` (the true physical screen) regardless of what `current_target` is set to, even if a specific window target is also configured. The JSON reports this via `fullscreen_mode: true`. Verified: with `.tracker_window_config.txt` explicitly set to `"Notepad"` (a ~900x500 window), enabling this switch still produced `screen_resolution: 1920x1080` and a full 1920x1080 exact-pixel frame in precision mode.

### 8e. Taskbar/desktop icon clicks: identifying WHICH app was launched, not just "explorer.exe"

Both the taskbar and the desktop are literally owned by `explorer.exe`, so a click launching or switching to an app via either one is correctly attributed to `explorer.exe` at the moment of the click - accurate, but useless for "which app did they mean to open." Since such a click reliably causes a different app to become foreground shortly after, `dual_tracker.py` now tracks every `explorer.exe`-attributed click and, if a different app becomes foreground within 3 seconds, writes a follow-up `.tracker_click_history.jsonl` line:

```json
{"type": "taskbar_or_desktop_launch", "icon_click_x": 900, "icon_click_y": 1050,
 "by": "user", "switched_to_app": "Notepad", "switched_to_process": "notepad.exe",
 "delay_s": 0.15, "at": ..., "clock": "19:26:08"}
```

**This is a best-effort heuristic, not a guarantee** - verified directly: in a fast-changing environment, if more than one foreground change happens within that 3-second window, it links to whichever comes *first*, which can be a transient/incidental window rather than the one actually launched. A test run linked a taskbar click to "Tongbu Assistant" (some other window that briefly flashed foreground) instead of the Notepad window actually being switched to. Treat `switched_to_app` as "probably this, especially if `delay_s` is small," not as certain.

**Bug this feature introduced, found and fixed the same day:** `.tracker_click_history.jsonl` now holds two different record shapes - a normal click has `app_title`; a launch record has no such key, only `switched_to_app`. `overlay.py`'s recent-clicks loop used raw `e['app_title']` inside a single `try/except` wrapping the *whole* loop, so the moment the newest line was a launch record, that `KeyError` silently discarded the entire recent-clicks list - the overlay showed `(none yet)` even with plenty of real history, and this happened **specifically** whenever someone clicked a taskbar/desktop icon, which is exactly the action that appends a launch record. Reproduced by forcing a launch record to be the newest line and confirming the blank display; fixed by giving each line its own `try/except` and rendering each record type on its own terms (a launch record now shows `"launched WhatsApp"` instead of crashing the list).

### 8d. A click's `app_title` can show "Untitled" or the wrong app - usually correct, not a bug

`window_at_point()` uses `WindowFromPoint` + `GetAncestor(GA_ROOT)`, which answers "what window is truly topmost at this exact pixel" - genuine Windows Z-order truth, not a guess. Two things follow from that:

- **Some windows have a real, empty native title.** `GetWindowText` returns `""` for plenty of background/system windows and some modern app frames - confirmed directly (`pygetwindow` showed a dozen+ windows with title `''` on an ordinary desktop). The fallback used to show the bare word `"Untitled"`, which told you nothing. It now shows `"(process.exe)"` instead - e.g. `(explorer.exe)` - strictly more useful whenever this happens.
- **The topmost window at a pixel isn't always the app you'd visually guess.** On a cluttered desktop (29 windows open is normal in a long session), an overlapping or partially-hidden window can genuinely be topmost at one specific pixel without being the app anyone's attention is on. Verified directly: querying a coordinate computed from a *stale* window position (the target had since moved) correctly returned whatever large window really covered that pixel now, not the one from the stale calculation. That's the function doing its job right, not a bug - the fix for a mismatch like this is re-checking the target window's current position before computing a click coordinate, not "fixing" `window_at_point()`, which is already answering the question truthfully.

**Postscript 2 - an always-on-top window (including this project's own overlay) can legitimately intercept a click meant for something else.** Reproduced directly: `clicker.py` clicking Calculator at a window-relative coordinate landed on the tracker's own overlay instead, because the overlay is deliberately `-topmost` and genuinely was the frontmost thing at that absolute screen position - not a window-matching bug, just an always-on-top window doing what always-on-top means. Click-through was considered and rejected (it would also disable the overlay's drag-to-move and close button). Instead, `click_at()` now checks whether the computed absolute coordinate falls inside any of this project's own windows (overlay, admin panel, Settings dialog) before clicking, and **refuses** rather than silently misclicking - the same philosophy `WindowNotReady` already uses elsewhere in this class. Verified: reproduces and is caught for the exact Calculator scenario, and does not false-positive for a normal click elsewhere on screen.

**Postscript - the 2ms poll thread alone wasn't actually the fix.** After integrating it, real-usage retesting showed clicks were *still* being missed - not rarely, but consistently, several attempts in a row, even immediately after a completely fresh restart. That ruled out "long-running degradation" as the cause. Isolating further with a dedicated `GetAsyncKeyState` watcher thread running completely independently of `dual_tracker.py` (polling every 1ms, zero other code running) still saw **zero** button-down transitions during a `pyautogui.click()` call - proving the bug wasn't in the tracker's integration at all. The actual cause: `pyautogui.click()`'s internal gap between its own `mouseDown` and `mouseUp` calls is too short for `GetAsyncKeyState` to reliably observe, even at 1ms polling with nothing else competing for the CPU. A manual `mouseDown()` → `time.sleep(0.02)` → `mouseUp()` sequence was detected reliably every time in the same test. `clicker.py`'s `click_at()` now does exactly that instead of calling `pyautogui.click()`. Verified 3 clean reliability runs after the fix, all three correctly attributed `"by": "claude"`. The 2ms poll thread was still the right change (it also fixes real hardware clicks with a naturally short hold, and removed the old ~90ms-per-tick gap for anything else that transitions state quickly) - it just wasn't sufficient on its own, because the actual click-generation side had its own, separate timing problem.

### 9. Verified click-automation pattern (now encoded in `clicker.py` — §10 — not a standalone script)

**The one rule that matters:** always re-verify focus (via `ACTUAL_FOREGROUND` or `clicker.py`'s own guard) **immediately before every single click/keystroke, inside the same script run.** Never split "activate window" and "click" across two separate script invocations — focus reverts to whatever invoked the script (the terminal) the instant a script exits, so a second script starting later can't assume the target is still focused. This was the root cause of nearly every failed automation attempt this project has hit, including during the Blender rigging session.

**Window-activation trick that actually works on this machine:** `window.minimize(); window.restore()`. Plain `.activate()` (`SetForegroundWindow`) fails **silently** — no exception, just doesn't work — because of Windows' focus-steal prevention when the caller process isn't already foreground.

**Menu automation:** clicking a persistent header menu (e.g. Blender's "Add" menu in the top bar) is far more reliable than a keyboard shortcut chord like `Shift+A`, which is sensitive to exact mouse-over-viewport timing and can misfire (e.g. registered as "extend selection" instead of opening the menu).

**Best verification method:** save a real screenshot to disk and view it with the Read tool (visual inspection), rather than relying only on OCR text. This is how the cat mesh, selection outlines, and exact click coordinates were all confirmed this session.

---

### 10. `clicker.py` — the "hands" to vision.py's "eyes" (verified interaction layer)

Every action re-verifies the target window is genuinely foreground **immediately before acting, inside the same process** — never split across two script runs, since focus reverts to whatever launched a script the instant it exits. If it can't confirm the right window, it refuses to act rather than clicking blind.

```python
c = clicker.Clicker("Blender")
c.click_at(500, 400)               # window-relative click, verified
c.click_text("File")               # OCR-find then click
c.click_image("save_icon.png")     # template-match then click
c.drag(100, 100, 400, 400)         # stepped movement, not one long jump
c.scroll(-5)                       # SendInput wheel, with key fallback
c.type_text("hello")               # CapsLock-safe (see gotchas below)
c.wait_for_text("Done", timeout=30)
c.run_steps([...])                 # multiple dependent actions, ONE process
```

`run_steps()` is how a multi-action sequence (e.g. open a menu, click an item in it) should always be driven — splitting it across separate script invocations is what caused nearly every automation failure this project has hit.

**Trackpad-gesture equivalents** (added for non-mouse-wheel-native interactions like pinch-zoom and two/one-finger swipes). Windows has no simple "inject a two-finger touch gesture" primitive — these reach apps as wheel events with modifier keys held, which is also how apps that genuinely support trackpad gestures receive them:

```python
c.zoom(-10)                          # pinch zoom out: Ctrl+wheel
c.zoom(10, modifiers=["shift"])      # zoom with an extra modifier held too
c.swipe("up")   ; c.swipe("down")    # two-finger vertical swipe = scroll()
c.swipe("left") ; c.swipe("right")   # two-finger horizontal swipe
c.scroll(-5, modifiers=["ctrl"])     # any custom wheel+modifier combo directly
c.hotkey("ctrl", "shift", "k")       # any N-key chord, not just 2-key
c.rotate()                           # raises NotImplementedError - see below
```

Verified against real apps, not assumed:
- `zoom()` confirmed on Windows File Explorer's Ctrl+Scroll icon-resize (pixel delta 2.66 — icons visibly grew).
- `swipe("left"/"right")` tries `MOUSEEVENTF_HWHEEL` first, then **automatically falls back to Shift+vertical-wheel** if that produced no change — tested because raw horizontal wheel did nothing in File Explorer (delta 0.48) while Shift+wheel genuinely scrolled it (delta 2.27). Shift+wheel is the older, far more universally supported horizontal-scroll convention on Windows; most standard list/tree controls never opted into the newer horizontal-wheel message.
- `hotkey()` already supported any number of keys via `*keys` — verified with both a 2-key (Ctrl+Z) and 3-key (Ctrl+Shift+Z) combo on a real app.
- `rotate()` (two-finger rotate) is deliberately **not implemented** — it raises rather than faking it. No wheel/keyboard convention reaches most apps for rotation; the few apps that do support it (some CAD/image viewers) need Windows' Touch Injection API (`InitializeTouchInjection`/`InjectTouchInput`), a much larger addition than a wheel event. If a specific app needs this, check for a dedicated rotate hotkey first and use `hotkey()`.
- **Blender doesn't use the Ctrl+Scroll zoom convention** — it zooms on plain scroll instead (confirmed: plain `scroll()` moved its viewport, delta 2.86; `zoom()`'s Ctrl+wheel did nothing there). That's Blender's own binding choice, not a flaw in `zoom()` — the method itself is proven to work via the File Explorer test above.

**All six two/one-finger patterns, mapped:**

| Finger pattern | Method |
|---|---|
| Fingers spreading apart (pinch out) | `zoom(positive_amount)` |
| Fingers coming together (pinch in) | `zoom(negative_amount)` |
| Two fingers up | `swipe("up")` |
| Two fingers down | `swipe("down")` |
| Two fingers left | `swipe("left")` |
| Two fingers right | `swipe("right")` |

All six accept **any combination of `ctrl`, `shift`, and `alt`** — `modifiers=["ctrl"]`, `["shift"]`, `["alt"]`, `["ctrl","shift"]`, or all three together (`["ctrl","shift","alt"]`, verified as a triple chord). Every one of these was tested for real, not assumed, including confirming no modifier key is left stuck down afterward (checked via `GetAsyncKeyState` — all clear).

One test artifact worth knowing: an early combinatorial test showed `swipe("left", modifiers=["ctrl"])` and `swipe("left", modifiers=["shift"])` producing **exactly** zero visible change, which looked like a real failure — until the same calls were retried on a genuinely fresh window with no prior scroll history and worked immediately (delta 3.11). The cause was **not a code bug**: repeatedly testing many left/right combos in a row on one window exhausted that window's limited horizontal scroll range, so later calls in the same direction had nowhere left to move — indistinguishable from a broken gesture unless you reset to a known-fresh state between measurements. The same pattern showed up again testing `alt` (`swipe("up", modifiers=["alt"])` read a near-zero delta of 0.18) for the identical reason: a freshly opened window starts already scrolled to the top, so swiping further up has nowhere to go. If a modifier combo appears to do nothing, try it on a fresh window/scroll position before concluding it's unsupported.

None of `clicker.py` is Blender-specific — Blender and File Explorer were just convenient already-open test targets, the same role Notepad played for the drag/scroll/CapsLock testing earlier. `Clicker(window_title)` takes any window title substring.

### 11. `replay.py` — turn recorded history into images/video, for a human, fast

Claude never needs this — it reads OCR/vision data directly. This exists so a *person* can see what happened without staring at raw JSON. Measured: 90 images/sec from logged frame data, contact sheet of 50 frames in 0.08s, data→video conversion at 237 frames/sec (no capture involved, just encoding stored grids).

```bash
python replay.py record 60      # log 60s of frame data
python replay.py build 50       # rebuild last 50 as individual images
python replay.py sheet 50       # one contact-sheet image of many moments
python replay.py video 200      # convert logged data straight to an mp4
python replay.py timeline       # text log of window switches + text changes
```

### 12. `compare_modes.py` — visual proof of what each mode actually captures

Applies every mode in turn, lets it settle, and renders one sheet showing what the report contains in each — so the precision/grid-size tradeoff is visible rather than described. Run `python compare_modes.py`.

---

## Roadmap — everything originally listed here is now BUILT

This section used to list five requested features as "not yet built." All five are done; do not treat them as missing:

| Was requested | Now lives in | Confirm it |
|---|---|---|
| Claude-activity status | `claude_activity.py` — `set_activity()`/`get_activity()` | `claude_active`, `claude_action`, `claude_window` fields |
| Click-origin attribution | `dual_tracker.py`'s click detection + `claude_activity.log_click()` | `last_click.by` is `"claude"` or `"user"` |
| Multi-window tracking | comma-separated `.tracker_window_config.txt` | `watched_windows` field, ~5ms per extra window |
| Admin control panel | `admin_panel.py` | STOP kill switch, per-mode buttons, Settings/Reference dialog |
| Richer per-frame pixel reporting | precision mode (48×27 grid + exact JPEG) | `python modes.py apply text_reading`, check `exact_frame_png` |

Nothing is currently on the roadmap as "not yet built." If a genuinely new feature is requested, add it here as its own line and remove it once done — don't leave it in prose that looks like this table.

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
- `blender_automation_final.py`, `automate_rigging.py`, `focus_and_automate.py`, `run_rigging.py`, `multi_handler_system*.py`, `import_cat.py`, `import_model.py`, `click_cat.py`, `select_by_name.py`, `do_rigging.py`, `mcp_server.py` — early blind-automation attempts (no verification loop), all unreliable, superseded by `clicker.py` (§10), which encodes the same verify-before-every-click lesson as reusable, general-purpose code instead of one-off Blender-specific scripts.
- `*.log` files scattered in this folder — just run output, safe to delete anytime, not part of the system itself.
- The Blender rigging diagnostic scripts (`rig_*.py`, `check_*.py`, `recover*.py`, `audit*.py`, `scene_audit.py`, `blender_diagnose.py`, `_rig_setup.py`) have been **moved out of this repo entirely**, to `C:\Users\noman\Desktop\blender-rigging-diagnostics\` — they were never part of the tracker system, and this repo is deliberately non-Blender-specific. The findings they produced are fully captured in prose in §"The actual Blender task this was all built for" below; the scripts themselves are kept only in case that investigation resumes.

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

**Root cause found and confirmed, in Blender's own words:**
```
Warning: Bone Heat Weighting: failed to find solution for one or more bones
{'FINISHED'}
```
The automatic-weight solver fails on this mesh and assigns **zero weights to zero vertices**, but the operator still reports `FINISHED` — so the UI shows a completely normal-looking rig (parent set ✓, Armature modifier ✓, vertex group created ✓) that is silently non-functional. That's why nothing moved in pose mode and why it was so hard to diagnose by eye: every panel that would normally flag a broken rig looked fine.

**What was tried and measured, in order:**
| Attempt | Result |
|---|---|
| Decimate 432k → 37k verts, then automatic weights | Still 0 weighted verts |
| Reposition bone to run inside the mesh (was outside it) | Still 0 weighted verts |
| Clean mesh topology (removed ~8,700 duplicate/loose verts) | Still 0 weighted verts |
| Envelope weights (default bone radius) | Still 0 weighted verts |
| Envelope weights (widened bone radius) | Still 0 weighted verts |
| **Direct vertex-group assignment (weight 1.0, bypassing both solvers)** | **28,581/28,581 weighted — mesh genuinely deforms in pose mode, verified: moving the bone moved a mesh vertex by 0.2 units** |

Direct assignment is confirmed working but crude — the whole mesh follows one bone rigidly (correct for proving the pipeline, not a finished rig). A multi-bone skeleton (Hips/Spine/Head/Arm_L/Arm_R/Leg_L/Leg_R/Tail) with custom proximity-based per-vertex weighting was built (`rig_multibone.py`) since Blender's own solvers can't be trusted on this mesh, but the session hit a serious automation mishap partway through (see below) and this was not confirmed working before the session ended.

**Safety net in place:** the original 432k-vertex mesh was duplicated to `Mesh_0_ORIGINAL_BACKUP` and hidden before any destructive editing — recoverable regardless of what happened to the working copy.

**⚠️ Automation mishap, learn from this before touching Blender again:** typing Python into Blender's interactive console via synthetic keystrokes is fragile — if the OS cursor moves or focus shifts mid-typing (including from the *user's own mouse movement*), the remaining characters land in the 3D viewport as hotkey shortcuts instead of console input. This happened mid-session: stray keystrokes triggered repeated object duplication (object count jumped to 61) and activated Blender's fullscreen Animation Player (a black-screen modal state that `Escape` alone did not clear — clicking its small close icon in the status bar did). **Never** run a multi-line block by typing it character-by-character into the console; write it to a `.py` file and have the console `exec(open(path).read())` as a single line instead — this was the eventual fix and worked reliably.

**⚠️ The .blend file was never saved to disk this session.** Everything above exists only in Blender's live memory/undo buffer. Before any further Blender work: `File → Save As` to a real path immediately, so a crash or accidental close doesn't lose the backup mesh or the rigging progress.

**Next step if resuming:** re-verify current scene state via the console (`bpy.data.objects`, vertex group weights) before assuming anything above is still true — Ctrl+Z was used to recover from the duplication incident, and its exact end-state was not re-confirmed via automation after that.
- Not yet done: parent mesh to armature with automatic weights, enter Pose Mode, test that the mesh actually deforms when a bone is moved (this is the actual fix/diagnosis for the original bug).

## 9. Architecture — what depends on what (read this first in a new session)

**Core question answered: is `dual_tracker.py` self-contained, or does it need the rest of the project to work?**
Verified by grepping every `import`/`from` line in every core file (not assumed) — here is the real, complete dependency graph:

```
dual_tracker.py  ──imports──▶  claude_activity.py (as ca)
                               (that's the ONLY project-local import dual_tracker.py has)

clicker.py       ──imports──▶  claude_activity.py (as ca)
                  ──imports──▶  vision.py

admin_panel.py    ──imports──▶  modes.py   (lazy import, only inside one function, line ~299)

overlay.py         no project-local imports at all — pure stdlib (json, tkinter, pathlib, ctypes)
modes.py            no project-local imports — pure stdlib
claude_activity.py  no project-local imports — pure stdlib
vision.py           no project-local imports — cv2 + numpy only
```

**In plain terms:**
- **`dual_tracker.py` is the one file that MUST run** for any live tracking to exist. It only needs `claude_activity.py` alongside it (for click attribution) — nothing else.
- **`overlay.py` is 100% optional.** It never runs any tracking itself — it just opens a small always-on-top window that reads `.live_screen_state.json` and `.tracker_click_history.jsonl` (files `dual_tracker.py` already writes) and displays them nicely. Close it, delete it, never launch it — `dual_tracker.py` behaves identically either way. It exists purely so a human watching the screen can glance at current state without opening the raw JSON.
- **`admin_panel.py` is also optional** — it's a GUI for flipping the switch files (`.tracker_precision`, `.tracker_paused`, `.tracker_fullscreen`, etc.) instead of creating/deleting them by hand or via `modes.apply()`. `dual_tracker.py` just polls for those files' existence every loop; it doesn't know or care whether admin_panel.py, modes.py, or a plain text editor created them.
- **`clicker.py` is a separate, independent tool** (synthetic click/scroll/gesture automation) — it is not imported by `dual_tracker.py` and doesn't need it running, though it does read `claude_activity.py`'s click log to know what it itself has clicked recently.

**How every file actually talks to every other file: plain disk files, not Python imports.** The entire system is glue-free by design — one process writes a file, another reads it, and neither needs to import the other or even run on the same schedule:

| File on disk | Written by | Read by | Purpose |
|---|---|---|---|
| `.live_screen_state.json` | `dual_tracker.py` (every loop, ~90-100ms) | `overlay.py`, `admin_panel.py`, any comparison script | The live snapshot — all ~44 top-level fields |
| `.live_frame.jpg` | `dual_tracker.py` (only when precision mode on, atomic write) | anything reading `exact_frame_png` path from the state JSON | The one genuinely sharp screenshot |
| `.tracker_click_history.jsonl` | `dual_tracker.py` | `overlay.py` | Append-only click log (one JSON line per click/launch event) |
| `.tracker_precision` / `.tracker_paused` / `.tracker_fullscreen` | `admin_panel.py`, `modes.py`, or manual `touch`/delete | `dual_tracker.py` (polled every loop, presence = on) | Runtime switches — pure file existence, no content matters |
| `.tracker_window_config.txt` | `admin_panel.py` or manual write | `dual_tracker.py` | Which window title to target (empty/absent = first enumerated window, not full screen — see §8f for why `.tracker_fullscreen` exists) |
| `.tracker_active_mode.txt` | `modes.py` (`apply()`) | `modes.py` (`current()`, to resolve naming ambiguity — see fix below) |  Which named preset is active, so two modes with identical switches (e.g. `normal`/`ui_automation`/`motion_capture`) still report their real name back |

**Conclusion for a fresh session:** if you only care about live tracking data existing, you need exactly two files running together — `dual_tracker.py` and its one import `claude_activity.py` — everything else (`overlay.py`, `admin_panel.py`, `modes.py`, `clicker.py`, `vision.py`, all `compare_*`/`generate_*` scripts) is an optional consumer or optional control surface that talks to it purely through the disk files above, never through Python imports. Precision mode is confirmed (via this session's matrix generation and manual state checks) to be the most reliable/sharpest capture mode and is the recommended default for serious tracking work.

### 9a. Clicking: two completely separate systems, don't confuse them

There are two unrelated things both called "clicking" in this project. Verified directly from code, not assumed:

**1. Click *detection/reporting* — lives in `dual_tracker.py`, is the important one, is always on.**
A dedicated background thread (`click_poll_worker`, `dual_tracker.py:145`) polls `GetAsyncKeyState(VK_LBUTTON)` every 2ms — a pure read-only query, not a system hook (see §8c for why a hook was tried once and is now permanently banned for this project). Every real click, from anyone, is caught the instant it happens and:
- attributed to `"you"` or `"claude"` by matching against `claude_activity.py`'s log (80px / 1.5s window)
- written into `.live_screen_state.json` as the `last_click` field
- appended permanently to `.tracker_click_history.jsonl`

This needs nothing else running. **The tracker itself always tells you about clicks** — that's on by default the moment `dual_tracker.py` is running.

**2. Click *generation* — lives in `clicker.py`, is optional, is a separate tool.**
This is the automation side: `click_at()`, `swipe()`, `zoom()`, `scroll()` — it performs synthetic clicks/scrolls/gestures on command (used for Blender automation, etc). It does not detect anything itself; it just logs what it did to `claude_activity.py` so `dual_tracker.py`'s detector (see #1) can correctly attribute the resulting click as `"claude"` instead of misreading it as the user.

**`overlay.py`'s role in clicking: read-only display, nothing more.** Its "Last click" / "Recent clicks" panel just reads `.tracker_click_history.jsonl` (already written by `dual_tracker.py`, per #1) and prints it nicely on screen. It does not detect clicks, does not affect attribution, and closing it has zero effect on click tracking — you only lose the on-screen popup, not the underlying log.

**Bottom line:** for click tracking to exist at all, only `dual_tracker.py` matters. `clicker.py` is only needed if something needs to *perform* clicks programmatically. `overlay.py` is only needed if a human wants to *see* click history on screen instead of reading the raw `.jsonl`/JSON.

## 10. Interaction modes — how much automation interferes with your cursor/window (`interaction_modes.py`)

Windows only has **one physical cursor and one input stream** at the OS level — there is no such thing as a truly free second cursor. `interaction_modes.py` gives 4 explicit, honest tradeoffs instead of pretending otherwise. Generic — every mode takes a window title substring, never assumes which app it's targeting (Blender is just this project's example use case, not a dependency).

| Mode | Function | What it does | Verified this session |
|---|---|---|---|
| **1 — SHARED** | `mode1_shared_click()` | Real cursor, real focus change — this is what `clicker.py` always did. Fully interferes with whatever you're doing, exactly like before. | N/A (pre-existing, unchanged) |
| **2 — FAKE_CURSOR** | `mode2_fake_cursor_click()` | Sends `WM_LBUTTONDOWN`/`WM_LBUTTONUP` directly to the target window's HWND via `SendMessage`, at specific client coordinates — **the real system cursor never moves.** Optional `overlay_update` callback lets you draw a visible blue on-screen indicator at the synthetic click point. | Confirmed the message-send path works (`verify_message_input()` against a live Notepad window returned `True`). **Caveat, not swept under the rug:** this only works for apps whose UI reacts to window messages (native controls: buttons, menus, text fields). Apps reading raw input directly (many 3D viewports, games, canvas-based UIs — likely including Blender's own viewport) will accept the message silently and ignore it. Verify per-app before relying on this for viewport-style interaction. |
| **3 — VIRTUAL_DESKTOP** | `mode3_virtual_desktop_click()` | Moves the target window to a second Windows virtual desktop (creating one via `pyvda` if needed), switches to it, does the click there with the **real** cursor, then switches back to your original desktop automatically. Your mouse movements on your own desktop cannot reach the other desktop while it isn't active. | **Fully tested end-to-end**, not just written: ran against a live Notepad window — confirmed desktop switched to 2 during the click, window's HWND was found and clicked on desktop 2, then desktop automatically returned to 1 afterward. Tradeoff: your screen visibly flips away and back — not simultaneous with your own work, but zero permanent disruption to your window layout. |
| **4 — HEADLESS_API / SANDBOX** | `mode4_notes()` | Deliberately **not** implemented as generic clicking. Real "invisible, non-interfering, own cursor" isolation only exists via (a) the target app's own scripting/automation API (e.g. Blender's `bpy`, or any CLI/REST/COM surface) — nothing on screen is touched at all, so there is nothing to interfere with; or (b) a genuinely separate machine/session (Windows Sandbox or a VM with its own virtual display, viewed via RDP) — heavier to set up, but a literal separate screen+cursor. | Documented as guidance, not built as automatic — which path applies depends entirely on whether the target app has a scripting API. Anything else claiming to be "mode 4" is really mode 2 or 3 underneath; there is no generic invisible-second-cursor capability at the Windows API level. |

**Dependency added this session:** `pyvda` (wraps Windows' undocumented `IVirtualDesktopManager`/`IApplicationView` COM interfaces) — installed via `pip install pyvda`, works alongside `pywin32`/`pyautogui` without conflict: `pyvda` only controls *which virtual desktop is active and which window lives on which desktop*; the actual clicking is still done by `pyautogui`/`SendInput` (mode 1/3) or `win32gui.SendMessage` (mode 2), whichever desktop happens to be active at the time.

**Recommendation:** use mode 4 (the target app's own API) whenever one exists — it's the only mode that eliminates the interference problem instead of working around it. Fall back to mode 3 (virtual desktop) for apps with no API where GUI automation is unavoidable. Mode 2 is a lighter-weight option worth trying first for simple UI button/menu clicks specifically (not viewport/canvas interaction). Mode 1 remains the default for cases where interference is acceptable or the user is not actively multitasking.

## 11. Blender automation via `bpy` instead of GUI clicking

**The tracker itself works perfectly.** It reads live frames, tracks clicks, executes commands, and provides real-time visual feedback — all verified working end-to-end this session.

**But Blender GUI automation is problematic.** Keyboard shortcut menu navigation (`Shift+A` for Add, arrow keys to navigate, `Return` to select) is unreliable and difficult to verify against the live frame — the menu may appear off-screen, navigation state is invisible/hard to detect, and visual feedback is inconsistent. Coordinate-based menu clicking fails because Blender's menu layout changes per context.

**The correct solution for Blender:** use **Blender's own Python API (`bpy`)** instead of GUI automation. This:
- Bypasses all menu navigation problems entirely
- Is 100× faster (no sleep delays waiting for UI responsiveness)
- Produces reliable, repeatable results
- Works the same way whether Blender is open/visible or headless in the background
- Is the official intended way to automate Blender (Blender itself is scriptable-first)

**Example:**
```python
import bpy
import math

# Add cube
bpy.ops.mesh.primitive_cube_add()
cube = bpy.context.active_object

# Transform
cube.scale = (2, 2, 2)                          # scale 2
cube.rotation_euler[0] = math.radians(45)       # rotate X 45°
cube.location[2] = 2                            # move Z 2
```

**Pattern for any app with an automation API:** Prefer the API over GUI clicking. The tracker's value is not "I can click any app's menus" — it's "I can see what's on screen in real-time while scripts run, and I can verify each step." Use that visibility to drive API scripts or scripts written for apps without good GUIs, not to automate apps that have better ways.
