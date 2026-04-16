"""
layer.py — Deterministic tar writer and layer helpers.

Rules for reproducibility:
  - Files added in lexicographically sorted order (sorted(paths))
  - Each TarInfo: mtime=0, uid=0, gid=0, uname="", gname=""
  - Format: tarfile.PAX_FORMAT, pax_headers={} (no variable PAX records)
  - Result: same directory contents → same SHA-256 every time

Public API:
  make_layer(src_dir, dest_prefix, paths) -> (digest, tarball_bytes)
  write_layer(src_dir, dest_prefix, paths, layers_dir) -> digest
  repack_tarball(src_path, layers_dir) -> digest
  extract_layer(digest, layers_dir, target_dir) -> None
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path


def _make_tar_bytes(
    src_dir: str | Path,
    paths: list[str | Path],
    dest_prefix: str = "",
) -> bytes:
    """
    Build a deterministic PAX tar in memory.

    src_dir     — base directory; all paths must be under it or absolute.
    paths       — list of paths to include (files and symlinks only; dirs via os.walk).
    dest_prefix — prepend this prefix to each name in the archive (e.g. '/').
    """
    src_dir = Path(src_dir)
    buf = io.BytesIO()

    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        # Collect all entries: explicit paths + walk directories
        all_entries: list[Path] = []
        for p in paths:
            p = Path(p)
            abs_p = p if p.is_absolute() else src_dir / p
            if abs_p.is_dir():
                for root, dirs, files in os.walk(abs_p):
                    dirs.sort()  # stable traversal
                    for fname in sorted(files):
                        all_entries.append(Path(root) / fname)
                    for dname in sorted(dirs):
                        all_entries.append(Path(root) / dname)
            else:
                all_entries.append(abs_p)

        # Deduplicate while preserving deterministic order
        seen: set[str] = set()
        deduped: list[Path] = []
        for entry in all_entries:
            k = str(entry)
            if k not in seen:
                seen.add(k)
                deduped.append(entry)

        # Sort lexicographically by archive name
        def archive_name(abs_p: Path) -> str:
            try:
                rel = abs_p.relative_to(src_dir)
            except ValueError:
                rel = abs_p
            name = str(rel)
            if dest_prefix:
                name = dest_prefix.rstrip("/") + "/" + name.lstrip("/")
            return name

        deduped.sort(key=archive_name)

        for abs_p in deduped:
            name = archive_name(abs_p)
            ti = tf.gettarinfo(str(abs_p), arcname=name)
            # gettarinfo returns None for device nodes, FIFOs, sockets — skip them
            if ti is None:
                continue
            # Normalize all metadata for reproducibility
            ti.mtime = 0
            ti.uid = 0
            ti.gid = 0
            ti.uname = ""
            ti.gname = ""
            ti.pax_headers = {}

            if ti.isreg():
                with open(abs_p, "rb") as fh:
                    tf.addfile(ti, fh)
            else:
                tf.addfile(ti)

    return buf.getvalue()


def make_layer(
    src_dir: str | Path,
    paths: list[str | Path],
    dest_prefix: str = "",
) -> tuple[str, bytes]:
    """
    Build a deterministic layer tar from *paths* under *src_dir*.
    Returns (digest, tar_bytes) where digest is 'sha256:<hex>'.
    """
    data = _make_tar_bytes(src_dir, paths, dest_prefix=dest_prefix)
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    return digest, data


def write_layer(
    src_dir: str | Path,
    paths: list[str | Path],
    layers_dir: str | Path,
    dest_prefix: str = "",
) -> str:
    """
    Build a layer, persist it to layers_dir/<hex>.tar, return digest.
    If a layer with the same digest already exists on disk it is not rewritten.
    """
    digest, data = make_layer(src_dir, paths, dest_prefix=dest_prefix)
    hex_val = digest.removeprefix("sha256:")
    dest = Path(layers_dir) / f"{hex_val}.tar"
    if not dest.exists():
        dest.write_bytes(data)
    return digest


def make_delta_layer(
    before_dir: str | Path,
    after_dir: str | Path,
    layers_dir: str | Path,
) -> str:
    """
    Compute a delta tar between before_dir and after_dir:
    includes files that are new or modified in after_dir.
    Returns digest.
    """
    before_dir = Path(before_dir)
    after_dir = Path(after_dir)
    changed: list[Path] = []

    for root, dirs, files in os.walk(after_dir):
        dirs.sort()
        for fname in sorted(files):
            abs_after = Path(root) / fname
            rel = abs_after.relative_to(after_dir)
            abs_before = before_dir / rel

            # Handle symlinks: compare link targets, never open() them
            if abs_after.is_symlink():
                if not abs_before.is_symlink() or \
                        os.readlink(abs_after) != os.readlink(abs_before):
                    changed.append(abs_after)
                continue

            # Skip anything that isn't a plain regular file
            if not abs_after.is_file():
                continue

            if not abs_before.exists():
                changed.append(abs_after)
            elif abs_after.stat().st_size != abs_before.stat().st_size:
                changed.append(abs_after)
            else:
                with open(abs_after, "rb") as f:
                    h_after = hashlib.sha256(f.read()).hexdigest()
                with open(abs_before, "rb") as f:
                    h_before = hashlib.sha256(f.read()).hexdigest()
                if h_after != h_before:
                    changed.append(abs_after)

    # Build layer with changed files (paths are absolute; rebase to after_dir)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        changed.sort(key=lambda p: str(p.relative_to(after_dir)))
        for abs_p in changed:
            rel = abs_p.relative_to(after_dir)
            ti = tf.gettarinfo(str(abs_p), arcname=str(rel))
            if ti is None:
                continue
            ti.mtime = 0
            ti.uid = 0
            ti.gid = 0
            ti.uname = ""
            ti.gname = ""
            ti.pax_headers = {}
            if ti.isreg():
                with open(abs_p, "rb") as fh:
                    tf.addfile(ti, fh)
            else:
                tf.addfile(ti)

    data = buf.getvalue()
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    hex_val = digest.removeprefix("sha256:")
    dest = Path(layers_dir) / f"{hex_val}.tar"
    if not dest.exists():
        dest.write_bytes(data)
    return digest


def repack_tarball(src_path: str | Path, layers_dir: str | Path) -> str:
    """
    Repack an existing tarball (e.g. Alpine minirootfs) deterministically.
    Extracts to a temp dir then rebuilds via the deterministic writer.
    Returns digest of the repacked layer.
    """
    import tempfile
    import shutil

    src_path = Path(src_path)
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(str(src_path), "r:gz") as tf:
            # Safe extraction: no absolute paths, no ..
            members = [m for m in tf.getmembers() if _safe_member(m)]
            tf.extractall(tmp, members=members)

        # Collect all paths
        all_paths: list[Path] = []
        for root, dirs, files in os.walk(tmp):
            dirs.sort()
            for f in sorted(files):
                all_paths.append(Path(root) / f)
            for d in sorted(dirs):
                all_paths.append(Path(root) / d)

        return write_layer(tmp, all_paths, layers_dir)


def _safe_member(member: tarfile.TarInfo) -> bool:
    """Return True if the tar member is safe to extract."""
    name = member.name
    if os.path.isabs(name):
        return False
    if ".." in name.split("/"):
        return False
    return True


def extract_layer(digest: str, layers_dir: str | Path, target_dir: str | Path) -> None:
    """
    Extract a stored layer tarball into target_dir.
    digest may be 'sha256:<hex>' or bare hex.

    Extracts members individually so that later layers properly overwrite earlier
    ones and FileExistsError on special files (FIFOs, devices) is handled cleanly.
    """
    hex_val = digest.removeprefix("sha256:")
    tar_path = Path(layers_dir) / f"{hex_val}.tar"
    if not tar_path.exists():
        raise FileNotFoundError(f"Layer not found: {tar_path}")
    target_dir = str(target_dir)
    with tarfile.open(str(tar_path), "r") as tf:
        for member in tf.getmembers():
            if not _safe_member(member):
                continue
            dest = os.path.join(target_dir, member.name)
            # For non-directory entries: remove any existing path so we can
            # overwrite cleanly (handles FIFO-already-exists and regular
            # file overwrites from later layers).
            if not member.isdir():
                try:
                    if os.path.isdir(dest) and not os.path.islink(dest):
                        pass  # don't remove a real dir for a non-dir member
                    elif os.path.exists(dest) or os.path.islink(dest):
                        os.unlink(dest)
                except OSError:
                    pass
            try:
                tf.extract(member, target_dir)  # set_attrs=True (default) preserves permissions
            except (FileExistsError, IsADirectoryError):
                pass  # directory already exists — fine


def digest_file(path: str | Path) -> str:
    """Return 'sha256:<hex>' digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()
