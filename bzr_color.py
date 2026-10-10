"""Terminal colours, with Windows VT support and a no-op fallback.

Colour is enabled only when stdout is a real terminal, so piped or redirected
output stays plain text (log files, CI, the verify scripts' subprocess pipes).
"""

import os
import sys

RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
CYAN = "\033[36m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _enable_vt():
    """Turn on ANSI escape handling for a Windows console."""
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        for handle_id in (-11, -12):  # stdout, stderr
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                continue
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            if kernel32.SetConsoleMode(handle, mode.value | 0x0004):
                return True
        return False
    except Exception:
        return False


def _color_enabled():
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("BZR_FORCE_COLOR"):
        return True
    # The launcher relays Blender's output, so inside Blender stdout is a pipe
    # and never a tty. The launcher passes its own decision down instead.
    if os.environ.get("BZR_COLOR"):
        return os.environ["BZR_COLOR"] == "1"
    if not sys.stdout.isatty():
        return False
    if os.name == "nt":
        return _enable_vt()
    return True


ENABLED = _color_enabled()


def override(enabled):
    """Force colour on or off, e.g. from a CLI flag.

    Called before anything is printed, so the launcher does not need to parse
    arguments before it can import this module.
    """
    global ENABLED
    if enabled is None:
        return
    if enabled and os.name == "nt":
        _enable_vt()
    ENABLED = enabled


def paint(text, color):
    return f"{color}{text}{RESET}" if ENABLED else text


def error(text):
    return paint(text, RED)


def warn(text):
    return paint(text, YELLOW)


def ok(text):
    return paint(text, GREEN)


def info(text):
    return paint(text, CYAN)


def faint(text):
    return paint(text, DIM)


def bold(text):
    return paint(text, BOLD)


def highlight(line):
    """Colour a captured line from Blender's output by severity.

    Blender writes its own diagnostics to stdout, so without this the one thing
    you most want to notice is the one line with no emphasis. Lines the runner
    already coloured are left alone, since re-wrapping would nest escapes and a
    multi-line traceback would leak colour past its closing reset.
    """
    if ENABLED and "\033[" in line:
        return line
    lowered = line.strip().lower()
    if lowered.startswith("error") or ": error" in lowered or "error:" in lowered:
        return paint(line, RED)
    if lowered.startswith("warning") or "warning:" in lowered:
        return paint(line, YELLOW)
    return line
