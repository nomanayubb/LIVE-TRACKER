#!/usr/bin/env python3
"""
CLICKER - verified interaction layer. The "hands" to vision.py's "eyes".

Every action here is gated on actually being in the right window first. That
gate exists because of a specific, repeated failure: focus silently reverts to
whatever process launched a script the moment that script exits, so a click
issued by a later script lands in the WRONG application. Blind clicking
produced wrong-window clicks over and over until this was enforced.

Two rules this module exists to enforce:
  1. Re-verify the target window is genuinely foreground immediately before
     every single action, in the same process. Never split "focus" and "click"
     across two script runs.
  2. Declare what you're doing via claude_activity so the admin panel can show
     it and clicks can be attributed to Claude rather than the user.

Usage:
    import clicker
    c = clicker.Clicker("Blender")          # target window title substring
    c.click_at(500, 400)                    # verified click
    c.click_text("File")                    # OCR-find then click
    c.click_image("save_button.png")        # template-match then click
    c.drag(100, 100, 400, 400)
    c.scroll(-5)
    c.type_text("hello")
    c.wait_for_text("Done", timeout=30)     # poll until it appears
"""
import ctypes
ctypes.windll.user32.SetProcessDPIAware()   # MUST precede pyautogui import - without
# it pyautogui's coordinates disagree with ctypes/mss by the display scale factor
# (asking to click (500,500) landed at (450,461) before this was added)

import time
from pathlib import Path

import cv2
import mss
import numpy as np
import pyautogui
import pygetwindow as gw

import claude_activity as ca
import vision

pyautogui.FAILSAFE = True    # slam mouse to a screen corner to abort everything
pyautogui.PAUSE = 0.05

BASE_DIR = Path(__file__).resolve().parent
SHOT_DIR = BASE_DIR / "clicker_shots"
SHOT_DIR.mkdir(exist_ok=True)


class WindowNotReady(RuntimeError):
    """Raised instead of clicking when the target window can't be confirmed
    foreground - refusing to act beats clicking into the wrong application."""


class Clicker:
    def __init__(self, window_title, verify=True, shots=True):
        self.window_title = window_title
        self.verify = verify          # False only for deliberate whole-desktop actions
        self.shots = shots            # save a before/after image per action
        self._ocr = None
        self._hwnd = None             # latched once found - see _find_window

    # ---------------------------------------------------------------- window

    def _find_window(self):
        """Resolve the target window, preferring a latched handle.

        Apps rename their own windows mid-task - Notepad becomes
        '*Untitled - Notepad' the moment you type, browsers retitle on
        navigation, editors append the document name. Matching by title on
        every action therefore breaks partway through a sequence. Once we've
        found the window we latch its handle, which stays valid regardless of
        what the app calls itself afterwards."""
        if self._hwnd is not None:
            for w in gw.getAllWindows():
                if getattr(w, "_hWnd", None) == self._hwnd:
                    return w
            self._hwnd = None          # window closed - fall back to title search

        wins = [w for w in gw.getWindowsWithTitle(self.window_title) if w.title.strip()]
        if not wins:
            # Retry loosely: the title may have gained a prefix/suffix such as '*'
            needle = self.window_title.lower().lstrip("*").strip()
            wins = [w for w in gw.getAllWindows()
                    if w.title.strip() and needle in w.title.lower()]
        if not wins:
            raise WindowNotReady(f"No window matching '{self.window_title}'")
        self._hwnd = getattr(wins[0], "_hWnd", None)
        return wins[0]

    def focus(self, tries=4):
        """Bring the target genuinely to the foreground and confirm it.
        minimize()+restore() is used because plain .activate()
        (SetForegroundWindow) fails SILENTLY - no exception, just no effect -
        when the calling process isn't already foreground."""
        # Critical: if the window is ALREADY foreground, do nothing at all.
        # activate()/minimize()/restore() dismiss open menus and popups - that
        # bug silently broke multi-step sequences (click "Add", then the guard
        # for the next step closed the very menu it had just opened).
        if self._is_foreground():
            return self._find_window()

        for attempt in range(tries):
            win = self._find_window()
            if win.isMinimized:
                win.restore()
                time.sleep(0.4)
            try:
                win.activate()
            except Exception:
                pass
            time.sleep(0.15)
            if self._is_foreground():
                return self._find_window()
            try:
                win.minimize(); win.restore()
                time.sleep(0.4)
            except Exception:
                pass
            if self._is_foreground():
                return self._find_window()
        raise WindowNotReady(
            f"Could not bring '{self.window_title}' to foreground after {tries} tries "
            f"(currently foreground: '{self._foreground_title()}')")

    def _foreground_title(self):
        try:
            fg = gw.getActiveWindow()
            return fg.title if fg else ""
        except Exception:
            return ""

    def _is_foreground(self):
        """Compare window handles, not titles - the target may have renamed
        itself since we latched onto it (see _find_window)."""
        try:
            fg = gw.getActiveWindow()
            if fg is None:
                return False
            if self._hwnd is not None and getattr(fg, "_hWnd", None) == self._hwnd:
                return True
            needle = self.window_title.lower().lstrip("*").strip()
            return needle in (fg.title or "").lower()
        except Exception:
            return False

    def _guard(self, action_desc):
        """The gate every action passes through: confirm the right window is
        foreground, and declare the action so it's visible + attributable."""
        if self.verify:
            win = self.focus()
        else:
            win = self._find_window()
        ca.set_activity(True, window=self.window_title, action=action_desc)
        return win

    def _done(self):
        ca.set_activity(False)

    # ---------------------------------------------------------------- capture

    def grab(self):
        """Screenshot of the target window's region (BGRA numpy array)."""
        win = self._find_window()
        with mss.mss() as sct:
            mon = {"top": max(0, win.top), "left": max(0, win.left),
                   "width": max(10, win.width), "height": max(10, win.height)}
            return np.array(sct.grab(mon)), mon

    def _shot(self, label):
        if not self.shots:
            return None
        try:
            img, _ = self.grab()
            path = SHOT_DIR / f"{int(time.time()*1000)}_{label}.jpg"
            cv2.imwrite(str(path), img[:, :, :3], [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            return str(path)
        except Exception:
            return None

    # ---------------------------------------------------------------- clicking

    def click_at(self, x, y, button="left", clicks=1, relative=True, desc=None):
        """Click at a coordinate. relative=True treats (x,y) as offsets inside
        the target window, which is what you almost always want - absolute
        screen coordinates break the moment the window moves."""
        win = self._guard(desc or f"click ({x},{y})")
        try:
            sx, sy = (win.left + x, win.top + y) if relative else (x, y)
            before = self._shot("before")
            ca.log_click(sx, sy, button)
            pyautogui.click(sx, sy, clicks=clicks, button=button)
            time.sleep(0.15)
            after = self._shot("after")
            return {"ok": True, "x": sx, "y": sy, "button": button,
                    "clicks": clicks, "before": before, "after": after}
        finally:
            self._done()

    def double_click(self, x, y, **kw):
        return self.click_at(x, y, clicks=2, desc=f"double-click ({x},{y})", **kw)

    def right_click(self, x, y, **kw):
        return self.click_at(x, y, button="right", desc=f"right-click ({x},{y})", **kw)

    def middle_click(self, x, y, **kw):
        return self.click_at(x, y, button="middle", desc=f"middle-click ({x},{y})", **kw)

    def hover(self, x, y, relative=True, settle=0.4):
        """Move without clicking - reveals tooltips, opens hover menus."""
        win = self._guard(f"hover ({x},{y})")
        try:
            sx, sy = (win.left + x, win.top + y) if relative else (x, y)
            pyautogui.moveTo(sx, sy)
            time.sleep(settle)
            return {"ok": True, "x": sx, "y": sy, "after": self._shot("hover")}
        finally:
            self._done()

    def drag(self, x1, y1, x2, y2, relative=True, duration=0.4, button="left"):
        """Press, move, release - for sliders, canvas drags, drag-and-drop,
        selection rectangles."""
        win = self._guard(f"drag ({x1},{y1})->({x2},{y2})")
        try:
            if relative:
                x1, y1 = win.left + x1, win.top + y1
                x2, y2 = win.left + x2, win.top + y2
            before = self._shot("before")
            ca.log_click(x1, y1, button)
            pyautogui.moveTo(x1, y1)
            pyautogui.mouseDown(button=button)
            pyautogui.moveTo(x2, y2, duration=duration)
            pyautogui.mouseUp(button=button)
            time.sleep(0.2)
            return {"ok": True, "from": [x1, y1], "to": [x2, y2],
                    "before": before, "after": self._shot("after")}
        finally:
            self._done()

    def scroll(self, amount, x=None, y=None, relative=True):
        """Scroll wheel. Positive = up/away, negative = down/toward. If x/y are
        given the pointer moves there first, which matters in apps where scroll
        applies to whatever is under the cursor."""
        win = self._guard(f"scroll {amount}")
        try:
            if x is not None and y is not None:
                sx, sy = (win.left + x, win.top + y) if relative else (x, y)
                pyautogui.moveTo(sx, sy)
            pyautogui.scroll(amount)
            time.sleep(0.2)
            return {"ok": True, "amount": amount, "after": self._shot("scroll")}
        finally:
            self._done()

    # ---------------------------------------------------------------- keyboard

    VK_CAPITAL = 0x14

    def _capslock_on(self):
        return bool(ctypes.windll.user32.GetKeyState(self.VK_CAPITAL) & 1)

    def type_text(self, text, interval=0.02, fix_capslock=True):
        """Type text into the focused window.

        CapsLock matters here: pyautogui sends raw keystrokes, so with CapsLock
        on every letter arrives inverted - typing 'Hello' produces 'hELLO' and
        the caller never finds out. That silently corrupts anything typed, so
        by default we turn CapsLock off first and restore it afterwards.

        Separately, be aware the TARGET APP may rewrite what you type. Windows
        Notepad has autocorrect on by default: typing 'MiXeD' lands as 'Mixed'
        while nonsense like 'qWzX' is left alone (verified - our keystrokes are
        accurate, the app edits them afterwards). If exact text matters, read it
        back (clipboard or OCR) and compare rather than assuming it landed."""
        win = self._guard(f"type {text[:30]!r}")
        compensated = False
        try:
            send = text
            if fix_capslock and self._capslock_on():
                # Compensate in software rather than toggling the user's CapsLock.
                # Pressing capslock would change a physical keyboard state they
                # control - and if they happen to be typing at that moment, it
                # corrupts THEIR input. Inverting the string instead means
                # CapsLock inverts it back and the right text lands, with their
                # keyboard left exactly as they set it.
                send = text.swapcase()
                compensated = True
            pyautogui.typewrite(send, interval=interval)
            time.sleep(0.15)
            return {"ok": True, "typed": text, "capslock_compensated": compensated,
                    "after": self._shot("typed")}
        finally:
            self._done()

    def press(self, *keys, presses=1):
        """Single keys in sequence: press('enter'), press('tab','tab')."""
        win = self._guard(f"press {'+'.join(keys)}")
        try:
            for k in keys:
                pyautogui.press(k, presses=presses)
                time.sleep(0.05)
            return {"ok": True, "keys": list(keys), "after": self._shot("press")}
        finally:
            self._done()

    def hotkey(self, *keys):
        """Chord: hotkey('ctrl','s'). Note some apps' shortcuts are sensitive to
        where the mouse is hovering (Blender especially) - hover() first if so."""
        win = self._guard(f"hotkey {'+'.join(keys)}")
        try:
            pyautogui.hotkey(*keys)
            time.sleep(0.2)
            return {"ok": True, "keys": list(keys), "after": self._shot("hotkey")}
        finally:
            self._done()

    # ---------------------------------------------------------------- find & click

    def _reader(self):
        if self._ocr is None:
            try:
                import torch
                torch.set_num_threads(2)   # otherwise EasyOCR grabs every core
            except Exception:
                pass
            import easyocr
            self._ocr = easyocr.Reader(["en"], gpu=False)
        return self._ocr

    def find_text(self, needle, min_conf=0.35):
        """Locate text in the target window. Returns matches with window-relative
        coordinates, best first. Tries a few spacing/underscore variants because
        OCR is inconsistent about those."""
        img, mon = self.grab()
        results = self._reader().readtext(img[:, :, :3], detail=1)
        variants = {needle.lower(), needle.lower().replace("_", " "),
                    needle.lower().replace(" ", "_")}
        hits = []
        for bbox, text, conf in results:
            if conf < min_conf:
                continue
            low = text.lower()
            if any(v in low for v in variants):
                xs = [p[0] for p in bbox]; ys = [p[1] for p in bbox]
                hits.append({"text": text, "conf": round(float(conf), 3),
                             "x": int(min(xs)), "y": int(min(ys)),
                             "w": int(max(xs) - min(xs)), "h": int(max(ys) - min(ys)),
                             "cx": int((min(xs) + max(xs)) / 2),
                             "cy": int((min(ys) + max(ys)) / 2)})
        hits.sort(key=lambda h: -h["conf"])
        return hits

    def click_text(self, needle, index=0, **kw):
        """Find text by OCR and click its centre. Raises if not found rather
        than clicking a guessed position."""
        hits = self.find_text(needle)
        if not hits:
            raise LookupError(f"Text {needle!r} not found in '{self.window_title}'")
        hit = hits[index]
        return self.click_at(hit["cx"], hit["cy"], desc=f"click text {needle!r}", **kw)

    def find_image(self, template_path, threshold=0.8):
        """Find a known icon/button image anywhere in the target window. This is
        the most app-agnostic targeting method - it needs no text and no
        knowledge of the app's UI framework."""
        tpl = cv2.imread(str(template_path))
        if tpl is None:
            raise FileNotFoundError(f"Template not readable: {template_path}")
        img, _ = self.grab()
        return vision.match_template(img, tpl, threshold=threshold)

    def click_image(self, template_path, index=0, threshold=0.8, **kw):
        hits = self.find_image(template_path, threshold)
        if not hits:
            raise LookupError(f"Image {template_path} not found in '{self.window_title}'")
        hit = hits[index]
        return self.click_at(hit["cx"], hit["cy"],
                             desc=f"click image {Path(template_path).name}", **kw)

    def find_buttons(self):
        """All rectangular UI elements (likely buttons/panels), no OCR needed.
        Useful when you know roughly where something is but not its label."""
        img, _ = self.grab()
        return vision.detect_rectangles(img)

    def find_color_target(self, rgb, tolerance=30):
        """Locate a coloured region - a highlighted selection, an error banner,
        a status light. Works in any app that uses colour to signal state."""
        img, _ = self.grab()
        return vision.find_color(img, rgb, tolerance=tolerance)

    # ---------------------------------------------------------------- waiting

    def wait_for_text(self, needle, timeout=30, poll=1.0):
        """Poll until text appears. Use before acting on something that loads
        asynchronously rather than sleeping a guessed duration."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.find_text(needle):
                    return True
            except Exception:
                pass
            time.sleep(poll)
        return False

    def wait_for_stable(self, timeout=15, stable_for=1.0, poll=0.3):
        """Wait until the window stops changing - i.e. a render/load finished.
        Compares perceptual hashes rather than guessing a fixed sleep."""
        deadline = time.time() + timeout
        last_hash, stable_since = None, None
        while time.time() < deadline:
            img, _ = self.grab()
            h = vision.perceptual_hash(img, size=32)
            if last_hash is not None and vision.hamming_distance(h, last_hash) <= 1:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= stable_for:
                    return True
            else:
                stable_since = None
            last_hash = h
            time.sleep(poll)
        return False

    def changed_since(self, prev_hash, threshold=2):
        """Did anything visibly change? Returns (changed, new_hash)."""
        img, _ = self.grab()
        h = vision.perceptual_hash(img, size=32)
        if prev_hash is None:
            return True, h
        return vision.hamming_distance(h, prev_hash) > threshold, h

    # ---------------------------------------------------------------- sequences

    def run_steps(self, steps, stop_on_error=True):
        """Run a list of actions as one uninterrupted sequence, in ONE process.

        This matters: splitting a multi-step interaction across separate script
        runs is what caused repeated failures - an open menu closes and focus
        reverts the instant a script exits, so step 2 lands somewhere else
        entirely. Keep dependent steps inside a single run_steps call.

            c.run_steps([
                ("click_text", ["Add"], {}),
                ("click_text", ["Armature"], {}),
            ])
        """
        results = []
        for i, step in enumerate(steps):
            name, args, kwargs = (list(step) + [[], {}])[:3] if len(step) < 3 else step
            try:
                fn = getattr(self, name)
                res = fn(*args, **kwargs)
                results.append({"step": i, "action": name, "ok": True, "result": res})
            except Exception as e:
                results.append({"step": i, "action": name, "ok": False, "error": str(e)})
                if stop_on_error:
                    break
        return results


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "Notepad"
    c = Clicker(target)
    print(f"Target: {target!r}")
    try:
        win = c.focus()
        print(f"OK focused: {win.title!r} at ({win.left},{win.top}) {win.width}x{win.height}")
        print(f"Foreground confirmed: {c._is_foreground()}")
        rects = c.find_buttons()
        print(f"Found {len(rects)} rectangular UI elements")
        for r in rects[:5]:
            print(f"   rect at ({r['cx']},{r['cy']}) {r['w']}x{r['h']}")
    except WindowNotReady as e:
        print(f"REFUSED (this is the guard working, not a crash): {e}")
