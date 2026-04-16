"""
store.py — Layout of ~/.docksmith/ and path resolvers.

Directory structure:
  ~/.docksmith/
    images/      — <name>_<tag>.json manifests
    layers/      — <digest>.tar layer tarballs
    cache/       — index.json cache key→layer_digest map
"""

import os
from pathlib import Path

from docksmith import DocksmithError

_DEFAULT_ROOT = Path.home() / ".docksmith"


def _root() -> Path:
    """Return the Docksmith state root, respecting DOCKSMITH_ROOT env override."""
    return Path(os.environ.get("DOCKSMITH_ROOT", str(_DEFAULT_ROOT)))


def init() -> Path:
    """Create ~/.docksmith/{images,layers,cache} idempotently. Returns root path."""
    root = _root()
    for sub in ("images", "layers", "cache"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def images_dir() -> Path:
    return _root() / "images"


def layers_dir() -> Path:
    return _root() / "layers"


def cache_dir() -> Path:
    return _root() / "cache"


def image_path(name: str, tag: str) -> Path:
    """Return path for an image manifest JSON."""
    safe_name = name.replace("/", "_").replace(":", "_")
    return images_dir() / f"{safe_name}_{tag}.json"


def layer_path(digest: str) -> Path:
    """Return path for a layer tarball given its sha256 digest string.

    digest may be bare hex or 'sha256:<hex>'.
    """
    hex_val = digest.removeprefix("sha256:")
    return layers_dir() / f"{hex_val}.tar"


def cache_index_path() -> Path:
    return cache_dir() / "index.json"


def parse_image_ref(ref: str) -> tuple[str, str]:
    """Parse 'name:tag' → (name, tag). Tag defaults to 'latest'."""
    if ":" in ref:
        name, tag = ref.rsplit(":", 1)
    else:
        name, tag = ref, "latest"
    if not name:
        raise DocksmithError(f"invalid image reference: {ref!r}")
    return name, tag


def list_images() -> list[dict]:
    """Return a list of dicts with keys: name, tag, id (short digest), path."""
    import json

    results = []
    d = images_dir()
    if not d.exists():
        return results
    for manifest_file in sorted(d.glob("*.json")):
        try:
            data = json.loads(manifest_file.read_text())
        except Exception:
            continue
        digest = data.get("digest", "")
        short_id = digest.removeprefix("sha256:")[:12] if digest else "????????????"
        # Count layers (handle both object and legacy string formats)
        raw_layers = data.get("layers", [])
        results.append(
            {
                "name": data.get("name", "?"),
                "tag": data.get("tag", "?"),
                "id": short_id,
                "digest": digest,
                "created": data.get("created", ""),
                "layer_count": len(raw_layers),
                "path": manifest_file,
            }
        )
    return results
