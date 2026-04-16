"""Unit tests for builder cache predecessor selection."""

from docksmith.builder import BuildState, _prev_layer_digest


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
