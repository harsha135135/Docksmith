#!/usr/bin/env bash
# Reproducibility smoke-test.
# Builds sample-app twice with --no-cache, then diffs manifests and layer digests.
# Must be run as root (or with CAP_SYS_ADMIN) inside the Linux VM.
# Usage: bash tests/reproducibility/test_identical_builds.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export PYTHONPATH="$PROJECT_ROOT"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

STORE1="$TMP/store1"
STORE2="$TMP/store2"

run_build() {
    local store_dir="$1"
    DOCKSMITH_ROOT="$store_dir" python3 -m docksmith build \
        --no-cache \
        -t repro-test:latest \
        "$PROJECT_ROOT/sample-app"
}

echo "==> Build 1 (DOCKSMITH_ROOT=$STORE1)"
run_build "$STORE1"

echo ""
echo "==> Build 2 (DOCKSMITH_ROOT=$STORE2)"
run_build "$STORE2"

echo ""
echo "==> Comparing manifests..."
MANIFEST1="$STORE1/images/repro-test_latest.json"
MANIFEST2="$STORE2/images/repro-test_latest.json"

if diff -q "$MANIFEST1" "$MANIFEST2" >/dev/null; then
    echo "    PASS: manifests are byte-identical"
else
    echo "    FAIL: manifests differ!"
    diff "$MANIFEST1" "$MANIFEST2"
    exit 1
fi

echo ""
echo "==> Comparing layer tarballs..."
LAYERS1="$(ls "$STORE1/layers/")"
LAYERS2="$(ls "$STORE2/layers/")"

if [ "$LAYERS1" != "$LAYERS2" ]; then
    echo "    FAIL: layer sets differ!"
    echo "    Build 1: $LAYERS1"
    echo "    Build 2: $LAYERS2"
    exit 1
fi

for name in $LAYERS1; do
    if diff -q "$STORE1/layers/$name" "$STORE2/layers/$name" >/dev/null; then
        echo "    PASS: $name"
    else
        echo "    FAIL: $name differs!"
        exit 1
    fi
done

echo ""
echo "==> All reproducibility checks passed."
