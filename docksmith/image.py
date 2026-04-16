"""
image.py — Manifest I/O and digest rule.

Manifest schema (JSON) — matches spec §4.1:
  {
    "name":    "<image name>",
    "tag":     "<tag>",
    "digest":  "sha256:<hex>",
    "created": "<ISO-8601 UTC>",
    "layers":  [
      {"digest": "sha256:<hex>", "size": <bytes>, "createdBy": "<instruction>"},
      ...
    ],
    "config": {
      "Env":        ["KEY=value", ...],   // list of KEY=value strings
      "WorkingDir": "/path",
      "Cmd":        ["/bin/sh", ...]
    }
  }

Digest rule (spec §4.1):
  1. Serialize manifest with "digest": "" (empty string).
  2. SHA-256 the canonical JSON bytes.
  3. Rewrite "digest" field to "sha256:<hex>".

Canonical JSON: json.dumps(obj, sort_keys=True, separators=(",", ":"))
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from docksmith import DocksmithError
from docksmith import store


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ── Layer record helpers ─────────────────────────────────────────────────────

def make_layer_record(digest: str, layers_dir: Path, created_by: str) -> dict:
    """Build the per-layer manifest object: {digest, size, createdBy}."""
    tar_path = store.layer_path(digest)
    size = tar_path.stat().st_size if tar_path.exists() else 0
    return {"digest": digest, "size": size, "createdBy": created_by}


def layer_digests(manifest: dict) -> list[str]:
    """Extract ordered list of digest strings from a manifest's layers list."""
    layers = manifest.get("layers", [])
    if not layers:
        return []
    # Support both old (bare string) and new (object) formats transparently
    if isinstance(layers[0], str):
        return list(layers)
    return [l["digest"] for l in layers]


# ── Env helpers ──────────────────────────────────────────────────────────────

def env_dict_to_list(env: dict[str, str]) -> list[str]:
    """Convert {KEY: value} → sorted ["KEY=value", ...] (spec §4.1 Env format)."""
    return [f"{k}={v}" for k, v in sorted(env.items())]


def env_list_to_dict(env_list: list[str]) -> dict[str, str]:
    """Convert ["KEY=value", ...] → {KEY: value}."""
    result = {}
    for item in env_list:
        if "=" in item:
            k, v = item.split("=", 1)
            result[k] = v
    return result


# ── Digest rule ──────────────────────────────────────────────────────────────

def compute_digest(manifest: dict) -> str:
    """
    Apply the spec digest rule:
      copy manifest, set digest="", serialize canonically, SHA-256,
      return "sha256:<hex>".
    """
    m = dict(manifest)
    m["digest"] = ""
    raw = _canonical(m)
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def finalize_manifest(manifest: dict) -> dict:
    """Set the digest field in-place (mutates and returns the manifest)."""
    manifest["digest"] = compute_digest(manifest)
    return manifest


# ── I/O ──────────────────────────────────────────────────────────────────────

def write_manifest(
    name: str,
    tag: str,
    layers: list[dict],        # list of {digest, size, createdBy} records
    env: dict[str, str],
    workdir: str,
    cmd: list[str],
    created: str | None = None,
) -> dict:
    """
    Build, finalize, and persist an image manifest.
    layers must be a list of layer-record dicts (use make_layer_record()).
    Returns the manifest dict (including computed digest).
    """
    if created is None:
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    manifest: dict = {
        "name": name,
        "tag": tag,
        "digest": "",          # filled by finalize_manifest
        "created": created,
        "layers": list(layers),
        "config": {
            "Env": env_dict_to_list(env),
            "WorkingDir": workdir,
            "Cmd": list(cmd),
        },
    }
    finalize_manifest(manifest)

    path = store.image_path(name, tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(manifest))
    return manifest


def load_manifest(name: str, tag: str) -> dict:
    """Load and return the manifest dict for name:tag."""
    path = store.image_path(name, tag)
    if not path.exists():
        raise DocksmithError(f"image not found: {name}:{tag}")
    return json.loads(path.read_bytes())


def load_manifest_path(path: str | Path) -> dict:
    """Load a manifest from an explicit path."""
    p = Path(path)
    if not p.exists():
        raise DocksmithError(f"manifest not found: {path}")
    return json.loads(p.read_bytes())


def verify_manifest_digest(manifest: dict) -> bool:
    """Return True if the stored digest matches a fresh computation."""
    stored = manifest.get("digest", "")
    return stored == compute_digest(manifest)
