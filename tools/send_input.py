"""Send keystrokes to the FIFA 15 window.

The computer-use tooling resolves applications through the Start menu, so it
cannot see a manually installed game. This drives the game directly with
SendInput against the foreground window, which is what FIFA's input layer reads.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
SW_RESTORE = 9

# Scan codes, because FIFA reads DirectInput-style scan codes rather than
# virtual keys.
SCAN = {
    "enter": 0x1C, "escape": 0x01, "space": 0x39, "tab": 0x0F,
    "up": 0x48, "down": 0x50, "left": 0x4B, "right": 0x4D,
    "a": 0x1E, "b": 0x30, "x": 0x2D, "y": 0x15,
    "e": 0x12, "q": 0x10, "f": 0x21, "r": 0x13,
    "1": 0x02, "2": 0x03, "3": 0x04,
}
EXTENDED = {"up", "down", "left", "right"}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


def game_pids(image_name: str = "fifa15.exe") -> set[int]:
    import subprocess

    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    )
    pids = set()
    for line in result.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def focus(title_fragment: str) -> int | None:
    """Focus the game's window only — never a same-named Explorer window."""
    wanted = game_pids()
    matches: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if title_fragment.lower() in buffer.value.lower():
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in wanted:
                    matches.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    if not matches:
        return None
    hwnd = matches[0]
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    return hwnd


def press(key: str, hold: float = 0.06) -> None:
    scan = SCAN.get(key.lower())
    if scan is None:
        raise ValueError(f"unknown key {key!r}")
    flags = KEYEVENTF_SCANCODE | (0x0001 if key.lower() in EXTENDED else 0)

    down = INPUT(type=INPUT_KEYBOARD,
                 u=INPUT_UNION(ki=KEYBDINPUT(0, scan, flags, 0, None)))
    up = INPUT(type=INPUT_KEYBOARD,
               u=INPUT_UNION(ki=KEYBDINPUT(0, scan, flags | KEYEVENTF_KEYUP, 0, None)))

    user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    time.sleep(hold)
    user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))


MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


def click_window(hwnd: int, x: int, y: int) -> None:
    """Click at (x, y) given in the coordinate space of a window screenshot.

    Screenshots are captured from the window rect, so window coordinates map to
    screen coordinates by adding the window's top-left corner. Menu tiles are
    large targets, which makes this far more reliable than arrow-key navigation.
    """
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    screen_x = rect.left + x
    screen_y = rect.top + y

    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1)
    absolute_x = int(screen_x * 65535 / max(1, width - 1))
    absolute_y = int(screen_y * 65535 / max(1, height - 1))

    user32.mouse_event(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, absolute_x, absolute_y, 0, 0)
    time.sleep(0.35)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, absolute_x, absolute_y, 0, 0)
    time.sleep(0.08)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, absolute_x, absolute_y, 0, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("keys", nargs="*", help="keys to press in order")
    parser.add_argument("--title", default="FIFA 15")
    parser.add_argument("--delay", type=float, default=0.9, help="seconds between keys")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--click", help="x,y in window/screenshot coordinates")
    parser.add_argument("--hover", help="x,y to move the mouse to without clicking")
    args = parser.parse_args()

    hwnd = focus(args.title)
    if hwnd is None:
        print(f"!! no window matching {args.title!r}")
        return 1

    if args.hover:
        x, y = (int(v) for v in args.hover.split(","))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        user32.SetCursorPos(rect.left + x, rect.top + y)
        print(f"hovered {x},{y}")
        time.sleep(args.delay)

    if args.click:
        x, y = (int(v) for v in args.click.split(","))
        click_window(hwnd, x, y)
        print(f"clicked {x},{y}")
        time.sleep(args.delay)

    for _ in range(args.repeat):
        for key in args.keys:
            press(key)
            print(f"pressed {key}")
            time.sleep(args.delay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
