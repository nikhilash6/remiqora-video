"""The installer's console look - the same splash as the studio's server console (app/console.py): «REMIQORA VIDEO» in
ANSI Shadow block letters with the brand gradient, the window title, coloured progress bars. Only the stdlib, so the
downloader (tools/install_models.py) keeps running under any Python.

Colour only where the console takes it: a Windows console with VT processing on (SetConsoleMode) or any other tty,
NO_COLOR unset. Redirected output gets the same words plain. setup_models.ps1 has its own copy of the banner (it runs
before any Python exists) - keep the letters and the gradient the same."""
from __future__ import annotations

import os
import re
import sys

GRAD = ((0x93, 0x33, 0xea), (0xdb, 0x27, 0x77))   # studio.css --grad: violet -> pink
LOGO = [
    "██████╗ ███████╗███╗   ███╗██╗ ██████╗  ██████╗ ██████╗  █████╗ ",
    "██╔══██╗██╔════╝████╗ ████║██║██╔═══██╗██╔═══██╗██╔══██╗██╔══██╗",
    "██████╔╝█████╗  ██╔████╔██║██║██║   ██║██║   ██║██████╔╝███████║",
    "██╔══██╗██╔══╝  ██║╚██╔╝██║██║██║▄▄ ██║██║   ██║██╔══██╗██╔══██║",
    "██║  ██║███████╗██║ ╚═╝ ██║██║╚██████╔╝╚██████╔╝██║  ██║██║  ██║",
    "╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝╚═╝ ╚══▀▀═╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝",
]
TITLE = "Remiqora Video"
RESET, DIM, BOLD = "\x1b[0m", "\x1b[2m", "\x1b[1m"
COLORS = {"violet": (168, 85, 247), "pink": (236, 72, 153), "green": (74, 222, 128), "red": (248, 113, 113),
          "amber": (251, 191, 36), "cyan": (103, 232, 249), "grey": (148, 163, 184)}

color = False   # set by setup()
_ansi = re.compile(r"\x1b\[[0-9;]*m|\x1b\][^\x07]*\x07")


def strip(s: str) -> str:
    """The text without colour codes: its width on the screen."""
    return _ansi.sub("", s)


def _vt_on() -> bool:
    """VT sequences on stdout: a Windows console needs ENABLE_VIRTUAL_TERMINAL_PROCESSING switched on."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetStdHandle.restype = wintypes.HANDLE   # a 64-bit handle: the default int return cuts it
        k32.GetConsoleMode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.SetConsoleMode.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        h = k32.GetStdHandle(-11)   # STD_OUTPUT_HANDLE
        mode = wintypes.DWORD()
        if not k32.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        return bool(mode.value & 0x4) or bool(k32.SetConsoleMode(h, mode.value | 0x4 | 0x1))   # VT | processed output
    except (OSError, AttributeError, ValueError):
        return False


def is_tty() -> bool:
    out = sys.stdout
    return bool(out) and hasattr(out, "isatty") and out.isatty()


def setup() -> bool:
    """Decides about colour once; returns it."""
    global color
    if "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        color = False
    elif os.environ.get("FORCE_COLOR"):   # the convention: colour into a pipe (tests, screenshots)
        color = True
    else:
        color = is_tty() and _vt_on()
    return color


def write(s: str = "", end: str = "\n") -> None:
    out = sys.stdout
    if out is None:
        return
    try:
        out.write(s + end)
        out.flush()
    except UnicodeEncodeError:   # a code page without box drawing: what it can show
        enc = getattr(out, "encoding", None) or "ascii"
        try:
            out.write(s.encode(enc, "replace").decode(enc) + end)
            out.flush()
        except (OSError, ValueError):
            pass
    except (OSError, ValueError):
        pass


def rgb(c: tuple[int, int, int], s: str, bold: bool = False) -> str:
    if not color:
        return s
    return f"\x1b[{'1;' if bold else ''}38;2;{c[0]};{c[1]};{c[2]}m{s}{RESET}"


def paint(name: str, s: str, bold: bool = False) -> str:
    return rgb(COLORS[name], s, bold)


def dim(s: str) -> str:
    return f"{DIM}{s}{RESET}" if color else s


def title(text: str | None = None) -> None:
    """The window's title (OSC 0): «Remiqora Video — <text>»."""
    if color:
        write(f"\x1b]0;{TITLE} — {text}\x07" if text else f"\x1b]0;{TITLE}\x07", end="")


def _gradient_line(row: str) -> str:
    n = max(len(row) - 1, 1)
    out, last = [], None
    for x, ch in enumerate(row):
        if ch == " ":
            out.append(ch)
            continue
        t = x / n
        c = tuple(round(a + (b - a) * t) for a, b in zip(*GRAD))
        if ch not in "█▄▀":   # the shadow's lines: the same hue, darker
            c = tuple(round(v * 0.55) for v in c)
        code = f"\x1b[38;2;{c[0]};{c[1]};{c[2]}m"
        out.append(ch if code == last else code + ch)
        last = code
    return "".join(out) + RESET


def banner(subtitle: str) -> None:
    """The splash: block letters with the gradient, a line under them (what this is · the version)."""
    if color:
        write()
        for row in LOGO:
            write("  " + _gradient_line(row))
        write("  " + rgb(GRAD[1], "V I D E O", bold=True) + dim("  ·  ") + subtitle)
        write("  " + dim("─" * len(LOGO[0])))
    else:
        write()
        write("  REMIQORA VIDEO  ·  " + subtitle)
        write("  " + "-" * 40)


def bar(frac: float, width: int = 28) -> str:
    """A progress bar: the gradient from violet to pink across the filled part, dim track after it."""
    frac = min(max(frac, 0.0), 1.0)
    fill = int(round(frac * width))
    if not color:
        return "[" + "#" * fill + "-" * (width - fill) + "]"
    cells = []
    for i in range(width):
        if i < fill:
            c = tuple(round(a + (b - a) * (i / max(width - 1, 1))) for a, b in zip(*GRAD))
            cells.append(f"\x1b[38;2;{c[0]};{c[1]};{c[2]}m█")
        else:
            cells.append("\x1b[38;2;70;70;85m░")
    return "".join(cells) + RESET


def clock(seconds: float) -> str:
    s = max(int(seconds), 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
