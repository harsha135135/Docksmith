"""Unit tests for builder cache predecessor selection."""

import json

import pytest

from docksmith import DocksmithError
from docksmith import store
from docksmith.builder import BuildState, _prev_layer_digest
from docksmith.parser import Instruction
from docksmith.builder import _handle_from


def test_prev_layer_uses_base_manifest_for_first_layer_step() -> None:
    state = BuildState(
        base_digest="sha256:manifest",
        base_layer_count=2,
        layer_records=[
            {"digest": "sha256:base1"},
            {"digest": "sha256:base2"},
        ],
    )

    assert _prev_layer_digest(state) == "sha256:manifest"


def test_prev_layer_uses_last_new_layer_after_first_step() -> None:
    state = BuildState(
        base_digest="sha256:manifest",
        base_layer_count=1,
        layer_records=[
            {"digest": "sha256:base1"},
            {"digest": "sha256:new1"},
        ],
    )

    assert _prev_layer_digest(state) == "sha256:new1"


def test_prev_layer_uses_base_manifest_when_base_has_no_layers() -> None:
    state = BuildState(
        base_digest="sha256:manifest",
        base_layer_count=0,
        layer_records=[],
    )

    assert _prev_layer_digest(state) == "sha256:manifest"


def test_handle_from_fails_when_base_layer_file_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DOCKSMITH_ROOT", str(tmp_path / "store"))
    store.init()

    manifest = {
        "name": "alpine",
        "tag": "3.18",
        "digest": "sha256:" + "a" * 64,
        "created": "2026-04-16T00:00:00Z",
        "layers": [
            {
                "digest": "sha256:" + "b" * 64,
                "size": 123,
                "createdBy": "alpine base layer",
            }
        ],
        "config": {
            "Env": [],
            "WorkingDir": "/",
            "Cmd": ["/bin/sh"],
        },
    }
    store.image_path("alpine", "3.18").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    )

    state = BuildState()
    instr = Instruction(
        line_no=1,
        name="FROM",
        args_raw="alpine:3.18",
        args="alpine:3.18",
    )

    with pytest.raises(DocksmithError, match="base image is incomplete"):
        _handle_from(instr, state, "Step 1/1")
