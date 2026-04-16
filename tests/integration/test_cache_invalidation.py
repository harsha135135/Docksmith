"""
Integration tests for cache invalidation (spec §5.3).

Each test covers one row of the invalidation matrix.
These tests build a mini image and verify hit/miss behavior.

NOTE: These tests require running as root (or with CAP_SYS_ADMIN) since
the builder executes RUN steps inside namespaces. Skip if not privileged.
"""

import os
import shutil
import tempfile
import pytest

# Skip all tests in this module if not running as root
pytestmark = pytest.mark.skipif(
    os.getuid() != 0,
    reason="Container isolation requires root or CAP_SYS_ADMIN",
)


def _make_context(tmp: str, docksmithfile_content: str, files: dict = None) -> str:
    """Create a build context directory with a Docksmithfile."""
    ctx = os.path.join(tmp, "ctx")
    os.makedirs(ctx, exist_ok=True)
    with open(os.path.join(ctx, "Docksmithfile"), "w") as f:
        f.write(docksmithfile_content)
    if files:
        for rel, content in files.items():
            abs_path = os.path.join(ctx, rel)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "wb") as f:
                f.write(content if isinstance(content, bytes) else content.encode())
    return ctx


def _build(ctx: str, tag: str, no_cache: bool = False) -> list[str]:
    """Run a build and return list of step output lines."""
    import io
    import sys
    from docksmith.builder import build_image

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        build_image(ctx, tag=tag, no_cache=no_cache)
    finally:
        sys.stdout = old_stdout
    return buf.getvalue().splitlines()


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKSMITH_ROOT", str(tmp_path / "store"))
    from docksmith import store
    store.init()
    yield


DOCKSMITHFILE_BASIC = """\
FROM alpine:3.18
COPY app.txt /app/app.txt
RUN echo done
CMD ["/bin/sh"]
"""


class TestCacheInvalidation:
    """Spec §5.3 invalidation matrix."""

    def test_row1_copy_source_change(self, tmp_path):
        """Changing a COPY source file invalidates that step and all after."""
        ctx = _make_context(str(tmp_path), DOCKSMITHFILE_BASIC, {"app.txt": b"v1"})
        lines1 = _build(ctx, "test:1")
        assert any("CACHE MISS" in l and "COPY" in l for l in lines1)
        assert any("CACHE MISS" in l and "RUN" in l for l in lines1)

        lines2 = _build(ctx, "test:1")
        assert any("CACHE HIT" in l and "COPY" in l for l in lines2)
        assert any("CACHE HIT" in l and "RUN" in l for l in lines2)

        # Change source
        with open(os.path.join(ctx, "app.txt"), "wb") as f:
            f.write(b"v2")
        lines3 = _build(ctx, "test:1")
        assert any("CACHE MISS" in l and "COPY" in l for l in lines3)
        assert any("CACHE MISS" in l and "RUN" in l for l in lines3)

    def test_row2_instruction_edit(self, tmp_path):
        """Editing a RUN instruction invalidates that step and all after."""
        dsf = "FROM alpine:3.18\nRUN echo v1\nCMD [\"/bin/sh\"]\n"
        ctx = _make_context(str(tmp_path), dsf)
        _build(ctx, "test:2")

        dsf2 = "FROM alpine:3.18\nRUN echo v2\nCMD [\"/bin/sh\"]\n"
        with open(os.path.join(ctx, "Docksmithfile"), "w") as f:
            f.write(dsf2)
        lines = _build(ctx, "test:2")
        assert any("CACHE MISS" in l and "RUN" in l for l in lines)

    def test_row3_workdir_change_invalidates(self, tmp_path):
        """Changing WORKDIR invalidates subsequent layer-producing steps."""
        dsf1 = "FROM alpine:3.18\nWORKDIR /app\nRUN echo hi\nCMD [\"/bin/sh\"]\n"
        ctx = _make_context(str(tmp_path), dsf1)
        _build(ctx, "test:3")

        dsf2 = "FROM alpine:3.18\nWORKDIR /src\nRUN echo hi\nCMD [\"/bin/sh\"]\n"
        with open(os.path.join(ctx, "Docksmithfile"), "w") as f:
            f.write(dsf2)
        lines = _build(ctx, "test:3")
        assert any("CACHE MISS" in l and "RUN" in l for l in lines)

    def test_row4_env_change_invalidates(self, tmp_path):
        """Changing ENV invalidates subsequent layer-producing steps."""
        dsf1 = "FROM alpine:3.18\nENV FOO=1\nRUN echo ${FOO}\nCMD [\"/bin/sh\"]\n"
        ctx = _make_context(str(tmp_path), dsf1)
        _build(ctx, "test:4")

        dsf2 = "FROM alpine:3.18\nENV FOO=2\nRUN echo ${FOO}\nCMD [\"/bin/sh\"]\n"
        with open(os.path.join(ctx, "Docksmithfile"), "w") as f:
            f.write(dsf2)
        lines = _build(ctx, "test:4")
        assert any("CACHE MISS" in l and "RUN" in l for l in lines)

    def test_row5_layer_deleted_is_miss(self, tmp_path):
        """If a layer tar is deleted from disk, the cache must miss (no stale hit)."""
        from docksmith import store
        dsf = "FROM alpine:3.18\nRUN echo persistent\nCMD [\"/bin/sh\"]\n"
        ctx = _make_context(str(tmp_path), dsf)
        _build(ctx, "test:5")

        # Delete all layer tars
        for p in store.layers_dir().glob("*.tar"):
            p.unlink()

        lines = _build(ctx, "test:5")
        # Must miss because layer files are gone
        assert any("CACHE MISS" in l for l in lines)

    def test_row6_no_cache_flag(self, tmp_path):
        """--no-cache forces all steps to miss."""
        dsf = "FROM alpine:3.18\nRUN echo cached\nCMD [\"/bin/sh\"]\n"
        ctx = _make_context(str(tmp_path), dsf)
        _build(ctx, "test:6")  # warm cache

        lines = _build(ctx, "test:6", no_cache=True)
        assert all("CACHE HIT" not in l for l in lines)
        assert any("CACHE MISS" in l for l in lines)
