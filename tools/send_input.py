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


def focus(title_fragment: str) -> int | None:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("keys", nargs="+", help="keys to press in order")
    parser.add_argument("--title", default="FIFA 15")
    parser.add_argument("--delay", type=float, default=0.9, help="seconds between keys")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()

    hwnd = focus(args.title)
    if hwnd is None:
        print(f"!! no window matching {args.title!r}")
        return 1

    for _ in range(args.repeat):
        for key in args.keys:
            press(key)
            print(f"pressed {key}")
            time.sleep(args.delay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
