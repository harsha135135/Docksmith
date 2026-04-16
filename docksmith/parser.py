"""
parser.py — Docksmithfile tokenizer + AST.

Produces a list of Instruction named-tuples with:
  line_no    — 1-based source line
  name       — upper-cased instruction keyword
  args_raw   — remainder of the line (stripped)
  args       — parsed args (instruction-specific)

Supported instructions: FROM, COPY, RUN, WORKDIR, ENV, CMD
Unknown instructions raise DocksmithError.
CMD must be a JSON array; shell-string form is rejected.
"""

from __future__ import annotations

import json
import re
from typing import NamedTuple

from docksmith import DocksmithError

KNOWN_INSTRUCTIONS = frozenset({"FROM", "COPY", "RUN", "WORKDIR", "ENV", "CMD"})


class Instruction(NamedTuple):
    line_no: int
    name: str
    args_raw: str
    args: object  # instruction-specific parsed form


def parse_file(path: str) -> list[Instruction]:
    """Parse a Docksmithfile at *path* and return a list of Instructions."""
    with open(path, "r") as fh:
        text = fh.read()
    return parse_text(text, source=path)


def parse_text(text: str, source: str = "<string>") -> list[Instruction]:
    """Parse Docksmithfile text and return a list of Instructions."""
    instructions: list[Instruction] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        # Strip trailing whitespace; skip blank lines and comments
        stripped = raw.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            continue

        # Handle line continuations (\)
        while stripped.endswith("\\"):
            stripped = stripped[:-1]
            if i < len(lines):
                stripped += " " + lines[i].strip()
                i += 1

        line_no = i  # approximate (last line of a continuation)
        # Find instruction keyword
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(.*)", stripped)
        if not m:
            raise DocksmithError(
                f"{source}:{line_no}: cannot parse line: {stripped!r}"
            )

        keyword = m.group(1).upper()
        args_raw = m.group(2).strip()

        if keyword not in KNOWN_INSTRUCTIONS:
            raise DocksmithError(
                f"unknown instruction {keyword!r} at line {line_no} in {source}"
            )

        args = _parse_args(keyword, args_raw, line_no, source)
        instructions.append(Instruction(line_no=line_no, name=keyword, args_raw=args_raw, args=args))

    _validate_structure(instructions, source)
    return instructions


def _parse_args(keyword: str, args_raw: str, line_no: int, source: str) -> object:
    """Return instruction-specific parsed args."""
    if keyword == "FROM":
        # FROM <image>[:<tag>]
        parts = args_raw.split()
        if len(parts) != 1:
            raise DocksmithError(
                f"{source}:{line_no}: FROM requires exactly one argument, got: {args_raw!r}"
            )
        return parts[0]

    if keyword == "COPY":
        # COPY <src> [<src>...] <dest>
        parts = args_raw.split()
        if len(parts) < 2:
            raise DocksmithError(
                f"{source}:{line_no}: COPY requires at least <src> <dest>, got: {args_raw!r}"
            )
        return {"srcs": parts[:-1], "dest": parts[-1]}

    if keyword == "RUN":
        if not args_raw:
            raise DocksmithError(f"{source}:{line_no}: RUN requires a command")
        return args_raw

    if keyword == "WORKDIR":
        if not args_raw:
            raise DocksmithError(f"{source}:{line_no}: WORKDIR requires a path")
        return args_raw

    if keyword == "ENV":
        # ENV KEY=VALUE [KEY2=VALUE2 ...]  or  ENV KEY VALUE  (single pair)
        env = {}
        # Prefer KEY=VALUE form
        if "=" in args_raw:
            # Handle quoted values: KEY="val with space" KEY2=val
            pairs = _split_env_pairs(args_raw, line_no, source)
            for k, v in pairs:
                env[k] = v
        else:
            # Legacy: ENV KEY VALUE
            kv = args_raw.split(None, 1)
            if len(kv) != 2:
                raise DocksmithError(
                    f"{source}:{line_no}: ENV requires KEY=VALUE or KEY VALUE form, got: {args_raw!r}"
                )
            env[kv[0]] = kv[1]
        return env

    if keyword == "CMD":
        # CMD must be JSON array
        try:
            parsed = json.loads(args_raw)
        except json.JSONDecodeError as exc:
            raise DocksmithError(
                f"{source}:{line_no}: CMD must be a JSON array, got: {args_raw!r} ({exc})"
            ) from exc
        if not isinstance(parsed, list):
            raise DocksmithError(
                f"{source}:{line_no}: CMD must be a JSON array, got {type(parsed).__name__}"
            )
        if not all(isinstance(x, str) for x in parsed):
            raise DocksmithError(
                f"{source}:{line_no}: CMD JSON array must contain only strings"
            )
        return parsed

    raise DocksmithError(f"internal: unhandled keyword {keyword!r}")  # unreachable


def _split_env_pairs(text: str, line_no: int, source: str) -> list[tuple[str, str]]:
    """Split 'KEY=val KEY2="val with space"' into [(key,val), ...]."""
    pairs = []
    # Simple state-machine to handle quoted values
    remaining = text.strip()
    while remaining:
        eq = remaining.find("=")
        if eq == -1:
            raise DocksmithError(
                f"{source}:{line_no}: malformed ENV pair: {remaining!r}"
            )
        key = remaining[:eq].strip()
        remaining = remaining[eq + 1:]
        if not key or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            raise DocksmithError(
                f"{source}:{line_no}: invalid ENV key: {key!r}"
            )
        if remaining.startswith('"'):
            # Quoted value — find closing quote (no escape support needed)
            end = remaining.find('"', 1)
            if end == -1:
                raise DocksmithError(
                    f"{source}:{line_no}: unterminated quoted value in ENV"
                )
            value = remaining[1:end]
            remaining = remaining[end + 1:].lstrip()
        else:
            # Unquoted — ends at next whitespace
            parts = remaining.split(None, 1)
            value = parts[0]
            remaining = parts[1].lstrip() if len(parts) > 1 else ""
        pairs.append((key, value))
    return pairs


def _validate_structure(instructions: list[Instruction], source: str) -> None:
    """Validate that FROM is the first instruction."""
    if not instructions:
        raise DocksmithError(f"{source}: Docksmithfile is empty")
    if instructions[0].name != "FROM":
        raise DocksmithError(
            f"{source}:{instructions[0].line_no}: first instruction must be FROM"
        )
