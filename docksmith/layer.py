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
import shutil
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


def _resolve_safe(target_dir: str, name: str) -> str | None:
    """
    Return the absolute destination path inside *target_dir* for a tar member
    *name*, or None when extraction would be unsafe.

    Safety rules (no `os.path.realpath` — Alpine's absolute symlinks point to
    host paths a non-root user cannot stat, so realpath blows up):

      * reject absolute names and any name containing ``..``
      * reject members whose parent path traverses an existing symlink — that
        is the only attack vector (a malicious tar pre-creating ``link ->
        /outside`` and then writing ``link/foo``).  The leaf itself is opened
        with ``O_NOFOLLOW`` by the caller.
    """
    norm = name.replace("\\", "/")
    if os.path.isabs(norm) or ".." in norm.split("/"):
        return None
    parts = [p for p in norm.split("/") if p and p != "."]
    if not parts:
        return None
    cur = target_dir
    for component in parts[:-1]:
        cur = os.path.join(cur, component)
        if os.path.islink(cur):
            return None
    return os.path.join(target_dir, *parts)


def extract_layer(digest: str, layers_dir: str | Path, target_dir: str | Path) -> None:
    """
    Extract a stored layer tarball into *target_dir*.

    We do not delegate to tarfile's "data" filter because it calls
    ``os.path.realpath``, which on a base-image rootfs (Alpine) follows
    absolute symlinks like ``/var/run -> /run`` into host paths the running
    user may not be allowed to read.  Instead, every member is validated
    and written manually:

      * symlinks/hard links are created verbatim (absolute targets are
        required for Alpine to function after pivot_root);
      * regular files are opened with ``O_NOFOLLOW`` so a previously created
        symlink at the leaf cannot be used to escape ``target_dir``;
      * any member whose parent path traverses a symlink is dropped — that
        is the only way a malicious tar could redirect a write to the host.
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
            dest = _resolve_safe(target_dir, member.name)
            if dest is None:
                continue

            if member.isdir():
                _extract_dir(dest, member)
            elif member.issym():
                _extract_symlink(dest, member)
            elif member.islnk():
                _extract_hardlink(dest, member, target_dir)
            elif member.isreg():
                _extract_regular_file(dest, member, tf)
            # FIFOs, devices, sockets — not relevant to a build/run rootfs


def _ensure_parent(dest: str) -> bool:
    parent = os.path.dirname(dest)
    if not parent:
        return True
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError:
        return False
    return True


def _clear_existing(dest: str) -> None:
    """Remove any existing entry at *dest* (file, symlink, empty dir)."""
    if not os.path.lexists(dest):
        return
    try:
        if os.path.islink(dest) or not os.path.isdir(dest):
            os.unlink(dest)
        else:
            shutil.rmtree(dest)
    except OSError:
        pass


def _extract_dir(dest: str, member: tarfile.TarInfo) -> None:
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError:
        return
    try:
        os.chmod(dest, member.mode & 0o7777)
    except OSError:
        pass


def _extract_symlink(dest: str, member: tarfile.TarInfo) -> None:
    if not _ensure_parent(dest):
        return
    _clear_existing(dest)
    try:
        os.symlink(member.linkname, dest)
    except OSError:
        pass


def _extract_hardlink(dest: str, member: tarfile.TarInfo, target_dir: str) -> None:
    if not _ensure_parent(dest):
        return
    _clear_existing(dest)
    src = os.path.join(target_dir, member.linkname.lstrip("/"))
    try:
        os.link(src, dest)
    except OSError:
        pass


def _extract_regular_file(dest: str, member: tarfile.TarInfo, tf: tarfile.TarFile) -> None:
    if not _ensure_parent(dest):
        return
    _clear_existing(dest)
    mode = (member.mode & 0o7777) or 0o644
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    try:
        fd = os.open(dest, flags, mode)
    except OSError:
        return
    try:
        with os.fdopen(fd, "wb") as out:
            src = tf.extractfile(member)
            if src is not None:
                shutil.copyfileobj(src, out)
    except OSError:
        pass


def digest_file(path: str | Path) -> str:
    """Return 'sha256:<hex>' digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()
