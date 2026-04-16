"""
cache.py — Build cache key computation and index I/O.

Cache key spec (spec §5.1) — parts joined by newline, in this order:
  1. Previous layer digest  (or base image manifest digest for first layer-producing step)
     "Previous layer" walks back skipping WORKDIR/ENV/CMD.
  2. Full instruction text as written (strip trailing whitespace only).
  3. Current WORKDIR ("" if unset).
  4. Current ENV serialized as k1=v1\\nk2=v2\\n... with lex-sorted keys ("" if none).
  5. COPY only: sha256(file_bytes) for each src path, lex-sorted, concatenated.

Final key: sha256("\\n".join(parts)).hexdigest()

Cache index: ~/.docksmith/cache/index.json  →  {cache_key: layer_digest}
  Append-only writes. A hit requires both index entry AND layer tar on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from docksmith import store


# Instructions that do NOT produce layers (cache skips them for "previous" walk-back)
NON_LAYER_INSTRUCTIONS = frozenset({"WORKDIR", "ENV", "CMD", "FROM"})


def compute_cache_key(
    instruction_text: str,
    prev_layer_digest: str,
    workdir: str,
    env: dict[str, str],
    copy_file_digests: list[str] | None = None,
) -> str:
    """
    Compute the cache key for one build step.

    instruction_text   — full instruction line, trailing whitespace stripped.
    prev_layer_digest  — digest of last layer-producing step (or base manifest digest).
    workdir            — current WORKDIR value ("" if unset).
    env                — current ENV dict.
    copy_file_digests  — list of sha256 hex digests (no prefix) for COPY srcs,
                         lex-sorted by source path. None or [] if not a COPY step.
    """
    parts: list[str] = [
        prev_layer_digest,
        instruction_text.rstrip(),
        workdir,
        _serialize_env(env),
    ]
    if copy_file_digests:
        parts.append("".join(copy_file_digests))

    combined = "\n".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def _serialize_env(env: dict[str, str]) -> str:
    if not env:
        return ""
    return "\n".join(f"{k}={v}" for k, v in sorted(env.items())) + "\n"


def load_index() -> dict[str, str]:
    """Load the cache index from disk. Returns {} if missing or corrupt."""
    path = store.cache_index_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_bytes())
    except (json.JSONDecodeError, OSError):
        return {}


def save_index(index: dict[str, str]) -> None:
    """Persist the cache index atomically."""
    path = store.cache_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(json.dumps(index, sort_keys=True, separators=(",", ":")).encode())
    tmp.replace(path)


def clear_index() -> None:
    """Clear all cache key mappings."""
    save_index({})


def cache_lookup(cache_key: str) -> str | None:
    """
    Return the layer digest for cache_key if:
      - key is in index.json AND
      - the layer tar file exists on disk.
    Returns None on miss.
    """
    index = load_index()
    digest = index.get(cache_key)
    if digest is None:
        return None
    layer_tar = store.layer_path(digest)
    if not layer_tar.exists():
        return None
    return digest


def cache_store(cache_key: str, layer_digest: str) -> None:
    """Record cache_key → layer_digest in the index (append-only)."""
    index = load_index()
    index[cache_key] = layer_digest
    save_index(index)


def file_digest(path: str | Path) -> str:
    """Return bare hex sha256 of a file (no 'sha256:' prefix)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
