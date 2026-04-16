"""
Integration test: container writes must NOT appear on the host filesystem.

The test runs a container that writes a uniquely-named sentinel file.
After the container exits, we scan the host for that filename.
If found → test FAILS (isolation breach).

Requires root / CAP_SYS_ADMIN. Skipped otherwise.
"""

import os
import time
import tempfile
import pytest

pytestmark = pytest.mark.skipif(
    os.getuid() != 0,
    reason="Container isolation requires root or CAP_SYS_ADMIN",
)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKSMITH_ROOT", str(tmp_path / "store"))
    from docksmith import store
    store.init()
    yield


def test_container_writes_do_not_leak(tmp_path):
    """
    Run a container that creates a unique file in /tmp.
    Then verify that file is nowhere on the host filesystem.
    """
    from docksmith.runtime import run

    sentinel = f"docksmith-leak-test-{int(time.time() * 1e9)}.txt"
    sentinel_path = f"/tmp/{sentinel}"

    # Build a minimal rootfs using Alpine layers
    from docksmith import store
    from docksmith import image as image_mod
    from docksmith import layer as layer_mod

    # Need a real Alpine rootfs — skip if not imported
    try:
        manifest = image_mod.load_manifest("alpine", "3.18")
    except Exception:
        pytest.skip("alpine:3.18 not imported — run scripts/import-base-image.sh first")

    with tempfile.TemporaryDirectory(prefix="docksmith-isolation-test-") as rootfs:
        for layer_digest in manifest.get("layers", []):
            layer_mod.extract_layer(layer_digest, store.layers_dir(), rootfs)

        exit_code = run(
            rootfs=rootfs,
            cmd=["/bin/sh", "-c", f"echo leaked > {sentinel_path}"],
            env={},
            workdir="/",
        )

    # Search host filesystem for the sentinel
    found = _find_on_host(sentinel)
    assert not found, (
        f"ISOLATION BREACH: sentinel file {sentinel!r} found at: {found}\n"
        "Container writes leaked onto the host filesystem!"
    )


def _find_on_host(filename: str) -> list[str]:
    """Scan common host directories for filename. Returns list of matches."""
    search_roots = ["/tmp", "/var/tmp", "/root", "/home", "/run"]
    matches = []
    for root in search_roots:
        if not os.path.exists(root):
            continue
        try:
            for dirpath, dirs, files in os.walk(root):
                # Skip docksmith temp dirs
                dirs[:] = [d for d in dirs if "docksmith" not in d]
                if filename in files:
                    matches.append(os.path.join(dirpath, filename))
        except PermissionError:
            continue
    return matches
