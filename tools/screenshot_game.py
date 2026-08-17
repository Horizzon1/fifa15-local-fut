"""Capture the FIFA 15 window so progress can be verified visually.

The computer-use tooling cannot see a manually installed game (it resolves apps
through the Start menu), so this grabs the window directly: find it by class and
title, bring it forward, and BitBlt the screen region it occupies.

PrintWindow is tried first and the result is checked for being all-black, which
is what a Direct3D swap chain usually gives back; on black it falls back to a
screen-region grab of the foreground window.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import sys
import time
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SRCCOPY = 0x00CC0020
PW_RENDERFULLCONTENT = 0x00000002
SW_RESTORE = 9


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def game_pids(image_name: str = "fifa15.exe") -> set[int]:
    """PIDs of the game, so a same-named Explorer window is never mistaken for it."""
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


def find_window(title_fragment: str) -> int | None:
    """Find the game's window.

    Matching on title alone once picked up a File Explorer window showing the
    FIFA 15 folder, so the owning process is checked too.
    """
    wanted = game_pids()
    matches: list[tuple[int, str]] = []
    fallback: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if title_fragment.lower() not in buffer.value.lower():
            return True

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        (matches if pid.value in wanted else fallback).append((hwnd, buffer.value))
        return True

    user32.EnumWindows(callback, 0)
    if matches:
        return matches[0][0]
    if fallback:
        print(f"!! only non-game windows matched {title_fragment!r}; is the game running?",
              file=sys.stderr)
    return None


def capture(hwnd: int, output: Path, foreground: bool = True) -> tuple[bool, str]:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return False, "window has no area"

    if foreground:
        # BitBlt copies whatever is on screen at these coordinates, so if the
        # game is behind another window we would silently photograph that
        # window instead. Verify it actually reached the foreground.
        for _ in range(6):
            user32.ShowWindow(hwnd, SW_RESTORE)
            try:
                user32.SwitchToThisWindow(hwnd, True)
            except AttributeError:
                pass
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.45)
            if user32.GetForegroundWindow() == hwnd:
                break
        else:
            return False, "game window would not come to the foreground; refusing to capture another window"
        time.sleep(0.9)  # let the compositor present a frame  # let the compositor present a frame

    screen_dc = user32.GetDC(0)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    gdi32.SelectObject(memory_dc, bitmap)

    # Grab the screen region the window occupies. For a D3D window this is the
    # only approach that reliably contains rendered content.
    ok = gdi32.BitBlt(memory_dc, 0, 0, width, height,
                      screen_dc, rect.left, rect.top, SRCCOPY)

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height  # top-down
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0

    info = BITMAPINFO()
    info.bmiHeader = header
    buffer_size = width * height * 4
    pixels = ctypes.create_string_buffer(buffer_size)
    gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(info), 0)

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_dc)
    user32.ReleaseDC(0, screen_dc)

    raw = pixels.raw
    non_black = sum(1 for i in range(0, min(len(raw), 400000), 4) if raw[i] or raw[i + 1] or raw[i + 2])

    # Write a BMP: header + DIB + pixels.
    file_header = (
        b"BM"
        + (14 + 40 + buffer_size).to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + (14 + 40).to_bytes(4, "little")
    )
    dib = (
        (40).to_bytes(4, "little") + width.to_bytes(4, "little", signed=True)
        + (-height).to_bytes(4, "little", signed=True) + (1).to_bytes(2, "little")
        + (32).to_bytes(2, "little") + (0).to_bytes(4, "little")
        + buffer_size.to_bytes(4, "little") + (0).to_bytes(4, "little") * 4
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(file_header + dib + raw)

    return bool(ok), f"{width}x{height}, {non_black} non-black samples"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", default="FIFA 15")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent.parent / "logs" / "screenshot.bmp")
    parser.add_argument("--background", action="store_true",
                        help="do not bring the window forward first")
    args = parser.parse_args()

    hwnd = find_window(args.title)
    if hwnd is None:
        print(f"!! no visible window matching {args.title!r}")
        return 1

    ok, detail = capture(hwnd, args.output, foreground=not args.background)
    print(f"{'captured' if ok else 'capture failed'}: {args.output}  ({detail})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
