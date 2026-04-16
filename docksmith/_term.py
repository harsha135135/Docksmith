"""
_term.py — tiny TTY-aware ANSI colorizer for CLI output.

Colors are emitted only when stdout is a real terminal AND the NO_COLOR
environment variable is unset.  When piped/redirected the helpers fall
through unchanged so spec-mandated build output formats still parse cleanly.
"""

from __future__ import annotations

import os
import sys

_CODES = {
    "reset":   "0",
    "bold":    "1",
    "dim":     "2",
    "red":     "31",
    "green":   "32",
    "yellow":  "33",
    "blue":    "34",
    "magenta": "35",
    "cyan":    "36",
    "white":   "37",
}


def _enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("DOCKSMITH_NO_COLOR"):
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def style(text: str, *styles: str) -> str:
    """Wrap *text* in ANSI codes for the given style names; no-op when piped."""
    if not styles or not _enabled():
        return text
    seq = ";".join(_CODES[s] for s in styles if s in _CODES)
    if not seq:
        return text
    return f"\033[{seq}m{text}\033[0m"
