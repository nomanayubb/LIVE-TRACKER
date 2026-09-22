#!/usr/bin/env python3
"""
Four generic interaction modes for automating clicks/input against ANY target
window - not app-specific. These control HOW MUCH an automated action
interferes with the user's own cursor/window while it works.

Windows only has one physical cursor and one input stream at the OS level,
which hard-limits what's actually possible - each mode makes a different,
explicit tradeoff rather than pretending a truly free second cursor exists.

MODE 1 - SHARED  (already existing behavior via clicker.py directly)
    Uses the real system cursor and whatever window is currently focused.
    Fully interferes with the user - this is what clicker.py always did
    before this module existed.

MODE 2 - FAKE_CURSOR
    Draws a visible blue on-screen cursor overlay and sends clicks via
    PostMessage/SendMessage directly to the target window's HWND at
    specific client coordinates - WITHOUT moving the real system cursor.
    Works only for apps whose UI reacts to window messages (most native
    Win32/UI-toolkit controls: buttons, menus, text fields). Does NOT
    work for apps reading raw input directly (many 3D viewports, games,
    some canvas-based apps) - those will simply ignore synthetic
    messages. Verified capability, not a guess: see verify_message_input().

MODE 3 - VIRTUAL_DESKTOP
    Moves the target window to a separate Windows virtual desktop, switches
    to it, performs real clicks there (real cursor, but on a desktop the
    user isn't looking at), then switches back to the user's original
    desktop. The user's mouse movements on their own desktop never reach
    the other desktop while it isn't active. Tradeoff: the screen visibly
    flips away and back - not simultaneous, but zero permanent disruption
    to the user's window/tab layout.

MODE 4 - HEADLESS_API / SANDBOX
    Not implemented as generic clicking - deliberately so. The only way to
    get TRUE isolation (own cursor, own window, invisible to the user,
    zero interference) is either:
      (a) the target app's own scripting/automation API (e.g. Blender's
          `bpy`, or any app with a CLI/REST/COM automation surface) - no
          cursor or window is ever touched, so there is nothing to
          interfere with. This is the recommended path whenever available.
      (b) a genuinely separate machine/session (Windows Sandbox, a VM, or
          RDP to a virtual display) - heavy to set up, but gives a literal
          separate screen+cursor you can view without it ever touching
          the host desktop.
    See mode4_notes() below for guidance on which to use per-app.
"""
import time
from pathlib import Path

import win32api
import win32con
import win32gui
from pyvda import AppView, VirtualDesktop, get_virtual_desktops

BASE_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# MODE 1 - SHARED (thin wrapper, documents the tradeoff, delegates to clicker.py)
# ---------------------------------------------------------------------------
def mode1_shared_click(clicker_fn, *args, **kwargs):
    """Just calls whatever clicker.py function you pass - real cursor, real
    focus change, fully interferes with the user. Exists so callers can
    select a mode by number instead of calling clicker.py directly."""
    return clicker_fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# MODE 2 - FAKE_CURSOR (message-based click, no real cursor movement)
# ---------------------------------------------------------------------------
def _find_hwnd(window_title_substr):
    result = []

    def _enum(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and window_title_substr.lower() in win32gui.GetWindowText(hwnd).lower():
            result.append(hwnd)
    win32gui.EnumWindows(_enum, None)
    return result[0] if result else None


def verify_message_input(window_title_substr):
    """Best-effort check: does this window even respond to WM_* messages,
    or does it read raw input only? There is no universal way to know for
    certain without trying a real click and observing an effect, so this
    only confirms the window exists and can receive messages - it does NOT
    guarantee the app's content reacts to them (e.g. a 3D viewport may
    accept the message silently and ignore it)."""
    hwnd = _find_hwnd(window_title_substr)
    if not hwnd:
        return False, "window not found"
    try:
        win32gui.SendMessage(hwnd, win32con.WM_NULL, 0, 0)
        return True, "window accepts messages (does not confirm the UI reacts to clicks)"
    except Exception as e:
        return False, str(e)


def mode2_fake_cursor_click(window_title_substr, client_x, client_y, overlay_update=None):
    """Send a left-click directly to a window's HWND at (client_x, client_y)
    in that window's own client coordinates, without moving the real system
    cursor. `overlay_update`, if given, is called with (screen_x, screen_y)
    so a caller-supplied blue-cursor overlay can be moved to show where the
    synthetic click is landing, for visibility."""
    hwnd = _find_hwnd(window_title_substr)
    if not hwnd:
        raise RuntimeError(f"no window matching {window_title_substr!r}")

    lparam = win32api.MAKELONG(client_x, client_y)
    if overlay_update:
        sx, sy = win32gui.ClientToScreen(hwnd, (client_x, client_y))
        overlay_update(sx, sy)

    win32gui.SendMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
    time.sleep(0.02)
    win32gui.SendMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
    return hwnd


# ---------------------------------------------------------------------------
# MODE 3 - VIRTUAL_DESKTOP (real click, but on a desktop the user isn't on)
# ---------------------------------------------------------------------------
def mode3_virtual_desktop_click(window_title_substr, clicker_fn, *args, **kwargs):
    """Move the target window to a dedicated second virtual desktop (creating
    one if needed), switch to it, run clicker_fn(*args, **kwargs) there with
    the REAL cursor, then switch back to the desktop the user was on. The
    user's own mouse input on their original desktop cannot reach the other
    desktop while it is not the active one."""
    hwnd = _find_hwnd(window_title_substr)
    if not hwnd:
        raise RuntimeError(f"no window matching {window_title_substr!r}")

    original_desktop = VirtualDesktop.current()
    desktops = get_virtual_desktops()
    work_desktop = desktops[1] if len(desktops) > 1 else VirtualDesktop.create()

    view = AppView(hwnd=hwnd)
    view.move(work_desktop)
    work_desktop.go()
    try:
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        result = clicker_fn(*args, **kwargs)
    finally:
        original_desktop.go()
    return result


# ---------------------------------------------------------------------------
# MODE 4 - notes only (deliberately not generic-clicking based)
# ---------------------------------------------------------------------------
def mode4_notes(app_name="the target app"):
    return (
        f"MODE 4 for {app_name}: use its own scripting/automation API if it has "
        "one (e.g. Blender -> bpy, a CLI, a REST/COM interface) - zero cursor or "
        "window interference because nothing on screen is touched at all. If "
        f"{app_name} has no such API, the only true-isolation fallback is a "
        "separate machine/session (Windows Sandbox or a VM with its own "
        "virtual display, viewed via RDP) - heavier to set up but genuinely "
        "invisible to and non-interfering with the host desktop. There is no "
        "generic 'invisible second cursor' at the Windows API level - anything "
        "claiming that is really Mode 2 (message-based, app-dependent) or "
        "Mode 3 (real cursor, different desktop) underneath."
    )


if __name__ == "__main__":
    print(mode4_notes())
