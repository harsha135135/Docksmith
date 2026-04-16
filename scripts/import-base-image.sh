#!/usr/bin/env bash
# import-base-image.sh — Bootstrap Alpine 3.18 minirootfs into Docksmith.
# This is the ONLY script in the project that touches the network.
#
# Usage:
#   bash scripts/import-base-image.sh
#
# After running, `docksmith images` should list alpine:3.18.

set -euo pipefail

ALPINE_VERSION="3.18.4"
ALPINE_ARCH="x86_64"
TARBALL="alpine-minirootfs-${ALPINE_VERSION}-${ALPINE_ARCH}.tar.gz"
DOWNLOAD_URL="https://dl-cdn.alpinelinux.org/alpine/v3.18/releases/${ALPINE_ARCH}/${TARBALL}"

# Pinned SHA-256 of the official Alpine 3.18.4 minirootfs tarball
EXPECTED_SHA256="c59d5203bc6b8b6ef81f3f6b63e32c28d6e47be806ba8528f8766a4ca506c7ba"

# When run under sudo, use the invoking user's home so the store is shared
# with non-sudo `docksmith` invocations.
if [ -z "${DOCKSMITH_ROOT:-}" ]; then
    if [ -n "${SUDO_USER:-}" ]; then
        _USER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
        DOCKSMITH_ROOT="${_USER_HOME}/.docksmith"
    else
        DOCKSMITH_ROOT="$HOME/.docksmith"
    fi
fi
export DOCKSMITH_ROOT   # must be exported so the Python subprocess inherits it
IMAGES_DIR="${DOCKSMITH_ROOT}/images"
LAYERS_DIR="${DOCKSMITH_ROOT}/layers"
CACHE_DIR="${DOCKSMITH_ROOT}/cache"

echo "==> Bootstrapping Alpine ${ALPINE_VERSION} minirootfs"

# Ensure directories exist
mkdir -p "${IMAGES_DIR}" "${LAYERS_DIR}" "${CACHE_DIR}"

TMPDIR_WORK="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_WORK}"' EXIT

TARBALL_PATH="${TMPDIR_WORK}/${TARBALL}"

# Download
echo "==> Downloading ${DOWNLOAD_URL}"
if command -v curl &>/dev/null; then
    curl -fsSL -o "${TARBALL_PATH}" "${DOWNLOAD_URL}"
elif command -v wget &>/dev/null; then
    wget -q -O "${TARBALL_PATH}" "${DOWNLOAD_URL}"
else
    echo "ERROR: neither curl nor wget found" >&2
    exit 1
fi

# Verify checksum
echo "==> Verifying SHA-256"
ACTUAL_SHA256="$(sha256sum "${TARBALL_PATH}" | awk '{print $1}')"
if [ "${ACTUAL_SHA256}" != "${EXPECTED_SHA256}" ]; then
    echo "ERROR: SHA-256 mismatch!" >&2
    echo "  Expected: ${EXPECTED_SHA256}" >&2
    echo "  Got:      ${ACTUAL_SHA256}" >&2
    exit 1
fi
echo "    OK: ${ACTUAL_SHA256}"

# Import via Python (deterministic repack + manifest creation)
echo "==> Importing into Docksmith store"
python3 - "${TARBALL_PATH}" <<PYEOF
import sys
import os

# Make the docksmith package importable when not installed system-wide.
# SCRIPT_DIR is injected by the shell (heredoc with variable expansion).
_project_root = os.path.dirname(os.path.dirname(os.path.realpath("${BASH_SOURCE[0]}")))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from docksmith import store
from docksmith.layer import repack_tarball
from docksmith.image import write_manifest, make_layer_record

store.init()

src_tarball = sys.argv[1]
print(f"    Repacking {src_tarball} deterministically...")

layer_digest = repack_tarball(src_tarball, store.layers_dir())
print(f"    Layer digest: {layer_digest}")

layer_record = make_layer_record(layer_digest, store.layers_dir(), "alpine base layer")
manifest = write_manifest(
    name="alpine",
    tag="3.18",
    layers=[layer_record],
    env={},
    workdir="/",
    cmd=["/bin/sh"],
    created=None,  # will be set to now
)
print(f"    Image digest: {manifest['digest']}")
print(f"    Written to:   {store.image_path('alpine', '3.18')}")
PYEOF

echo ""
echo "==> Done. Verify with: docksmith images"
