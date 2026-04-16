"""
colorize.py — Minimal ANSI color helper, bundled as a vendor dependency.
No network needed. Used by sample-app/app.py.
"""

_COLORS = {
    "red":     "\033[31m",
    "green":   "\033[32m",
    "yellow":  "\033[33m",
    "blue":    "\033[34m",
    "magenta": "\033[35m",
    "cyan":    "\033[36m",
    "white":   "\033[37m",
    "reset":   "\033[0m",
}


def colorize(text: str, color: str | None = None) -> str:
    """Wrap *text* in ANSI color codes. Returns plain text if color unknown."""
    code = _COLORS.get(color or "")
    if not code:
        return text
    return f"{code}{text}{_COLORS['reset']}"
