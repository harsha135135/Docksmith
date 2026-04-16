"""
Unit tests for docksmith/layer.py

Key property: same directory contents → same SHA-256 every time.
"""

import hashlib
import io
import os
import tarfile
import tempfile
from pathlib import Path

import pytest

from docksmith.layer import make_layer, write_layer, digest_bytes


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_tree(base: str, files: dict[str, bytes]) -> None:
    """Create files dict {rel_path: content} under base."""
    for rel, content in files.items():
        abs_path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "wb") as f:
            f.write(content)


# ── Tests ────────────────────────────────────────────────────────────────────

class TestDeterministicTar:
    def test_same_content_same_digest(self, tmp_path):
        files = {
            "a.txt": b"hello",
            "b/c.txt": b"world",
            "b/d.txt": b"foo",
        }
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        _make_tree(str(dir1), files)
        _make_tree(str(dir2), files)

        paths1 = list(dir1.rglob("*"))
        paths2 = list(dir2.rglob("*"))

        digest1, data1 = make_layer(str(dir1), [p for p in paths1 if p.is_file()])
        digest2, data2 = make_layer(str(dir2), [p for p in paths2 if p.is_file()])

        assert digest1 == digest2
        assert data1 == data2

    def test_different_content_different_digest(self, tmp_path):
        dir1 = tmp_path / "d1"
        dir2 = tmp_path / "d2"
        dir1.mkdir()
        dir2.mkdir()

        _make_tree(str(dir1), {"a.txt": b"hello"})
        _make_tree(str(dir2), {"a.txt": b"goodbye"})

        digest1, _ = make_layer(str(dir1), [dir1 / "a.txt"])
        digest2, _ = make_layer(str(dir2), [dir2 / "a.txt"])

        assert digest1 != digest2

    def test_digest_format(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_bytes(b"test")
        digest, _ = make_layer(str(tmp_path), [f])
        assert digest.startswith("sha256:")
        assert len(digest) == 7 + 64  # "sha256:" + 64 hex chars

    def test_tar_metadata_zeroed(self, tmp_path):
        """All TarInfo entries must have mtime=0, uid=0, gid=0."""
        import io

        f = tmp_path / "file.txt"
        f.write_bytes(b"content")
        # Set real file mtime to something non-zero
        os.utime(f, (1234567890, 1234567890))

        _, data = make_layer(str(tmp_path), [f])
        with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tf:
            for member in tf.getmembers():
                assert member.mtime == 0, f"{member.name}: mtime={member.mtime}"
                assert member.uid == 0, f"{member.name}: uid={member.uid}"
                assert member.gid == 0, f"{member.name}: gid={member.gid}"
                assert member.uname == "", f"{member.name}: uname={member.uname!r}"
                assert member.gname == "", f"{member.name}: gname={member.gname!r}"

    def test_write_layer_persists(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_bytes(b"persist me")

        layers_dir = tmp_path / "layers"
        layers_dir.mkdir()

        digest = write_layer(str(tmp_path), [f], str(layers_dir))
        hex_val = digest.removeprefix("sha256:")
        assert (layers_dir / f"{hex_val}.tar").exists()

    def test_write_layer_idempotent(self, tmp_path):
        """Writing the same layer twice should not raise and result is identical."""
        f = tmp_path / "x.txt"
        f.write_bytes(b"idempotent")
        layers_dir = tmp_path / "layers"
        layers_dir.mkdir()

        d1 = write_layer(str(tmp_path), [f], str(layers_dir))
        d2 = write_layer(str(tmp_path), [f], str(layers_dir))
        assert d1 == d2


class TestDigestBytes:
    def test_known_value(self):
        data = b"hello"
        expected = "sha256:" + hashlib.sha256(b"hello").hexdigest()
        assert digest_bytes(data) == expected


class TestExtractSafety:
    def test_extract_preserves_absolute_symlinks(self, tmp_path):
        """
        Absolute symlinks (e.g. Alpine's /bin/sh -> /bin/busybox) must be
        preserved on extraction so the rootfs is functional after pivot_root.
        """
        from docksmith.layer import extract_layer

        layers_dir = tmp_path / "layers"
        rootfs = tmp_path / "rootfs"
        layers_dir.mkdir()
        rootfs.mkdir()

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
            link = tarfile.TarInfo("bin/sh")
            link.type = tarfile.SYMTYPE
            link.linkname = "/bin/busybox"
            link.mtime = 0
            tf.addfile(link)

        data = buf.getvalue()
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        (layers_dir / f"{digest.removeprefix('sha256:')}.tar").write_bytes(data)

        extract_layer(digest, layers_dir, rootfs)

        link_path = rootfs / "bin" / "sh"
        assert os.path.islink(link_path)
        assert os.readlink(link_path) == "/bin/busybox"

    def test_extract_does_not_write_outside_target_via_symlink(self, tmp_path):
        """
        A malicious tar with `link -> /outside` followed by `link/payload.txt`
        must NOT result in any write to the host filesystem.  The symlink
        itself may be created (it is inert until pivot_root), but the
        subsequent file write must be rejected by the safety filter.
        """
        from docksmith.layer import extract_layer

        layers_dir = tmp_path / "layers"
        rootfs = tmp_path / "rootfs"
        host_outside = tmp_path / "host_outside"
        layers_dir.mkdir()
        rootfs.mkdir()

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
            link = tarfile.TarInfo("link")
            link.type = tarfile.SYMTYPE
            link.linkname = str(host_outside)
            link.mtime = 0
            tf.addfile(link)

            payload = b"safe"
            member = tarfile.TarInfo("link/payload.txt")
            member.size = len(payload)
            member.mtime = 0
            tf.addfile(member, io.BytesIO(payload))

        data = buf.getvalue()
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        (layers_dir / f"{digest.removeprefix('sha256:')}.tar").write_bytes(data)

        extract_layer(digest, layers_dir, rootfs)

        # The critical safety property: nothing was written to the host.
        assert not (host_outside / "payload.txt").exists()
        assert not host_outside.exists()
