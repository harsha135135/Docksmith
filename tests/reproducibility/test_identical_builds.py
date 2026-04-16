"""
Reproducibility test: build the same image twice and verify byte-identical output.

Both manifests and all layer tarballs must be bit-for-bit identical.

Requires root / CAP_SYS_ADMIN (for RUN isolation). Skipped otherwise.
"""

import json
import os
import shutil
import tempfile
import pytest

pytestmark = pytest.mark.skipif(
    os.getuid() != 0,
    reason="Container isolation requires root or CAP_SYS_ADMIN",
)

DOCKSMITHFILE = """\
FROM alpine:3.18
WORKDIR /app
ENV GREETING=Hello
COPY app.txt /app/app.txt
RUN echo "build ok"
CMD ["/bin/sh"]
"""


@pytest.fixture
def build_context(tmp_path):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "Docksmithfile").write_text(DOCKSMITHFILE)
    (ctx / "app.txt").write_bytes(b"hello world")
    return ctx


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKSMITH_ROOT", str(tmp_path / "store"))
    from docksmith import store
    store.init()
    yield


def test_two_builds_are_byte_identical(tmp_path, build_context):
    """Build the same context twice; manifests and layers must be byte-identical."""
    from docksmith.builder import build_image
    from docksmith import store

    # Skip if alpine not imported
    try:
        from docksmith import image as image_mod
        image_mod.load_manifest("alpine", "3.18")
    except Exception:
        pytest.skip("alpine:3.18 not imported")

    build_image(str(build_context), tag="repro-test:v1", no_cache=True)
    manifest1_path = store.image_path("repro-test", "v1")
    manifest1 = manifest1_path.read_bytes()
    layers1 = {
        p.name: p.read_bytes()
        for p in store.layers_dir().glob("*.tar")
    }

    # Wipe store and rebuild
    shutil.rmtree(str(store.layers_dir()))
    store.layers_dir().mkdir()
    manifest1_path.unlink()

    build_image(str(build_context), tag="repro-test:v1", no_cache=True)
    manifest2 = manifest1_path.read_bytes()
    layers2 = {
        p.name: p.read_bytes()
        for p in store.layers_dir().glob("*.tar")
    }

    assert manifest1 == manifest2, (
        f"Manifest not byte-identical!\n"
        f"Build 1: {json.loads(manifest1)}\n"
        f"Build 2: {json.loads(manifest2)}"
    )

    assert set(layers1.keys()) == set(layers2.keys()), (
        f"Layer set differs!\n"
        f"Build 1: {sorted(layers1.keys())}\n"
        f"Build 2: {sorted(layers2.keys())}"
    )

    for name in layers1:
        assert layers1[name] == layers2[name], f"Layer {name} is not byte-identical!"
