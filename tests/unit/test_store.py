"""
Unit tests for docksmith/store.py
"""

import os
import pytest

from docksmith import DocksmithError
from docksmith.store import parse_image_ref, image_path, layer_path


class TestParseImageRef:
    def test_with_tag(self):
        assert parse_image_ref("alpine:3.18") == ("alpine", "3.18")

    def test_without_tag(self):
        assert parse_image_ref("alpine") == ("alpine", "latest")

    def test_name_with_tag_latest(self):
        assert parse_image_ref("myapp:latest") == ("myapp", "latest")

    def test_empty_name_raises(self):
        with pytest.raises(DocksmithError):
            parse_image_ref(":tag")


class TestPathHelpers:
    def test_image_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOCKSMITH_ROOT", str(tmp_path))
        p = image_path("alpine", "3.18")
        assert p.name == "alpine_3.18.json"

    def test_layer_path_with_prefix(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOCKSMITH_ROOT", str(tmp_path))
        p = layer_path("sha256:" + "a" * 64)
        assert p.name == "a" * 64 + ".tar"

    def test_layer_path_bare_hex(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DOCKSMITH_ROOT", str(tmp_path))
        p = layer_path("b" * 64)
        assert p.name == "b" * 64 + ".tar"


class TestInit:
    def test_creates_dirs(self, tmp_path, monkeypatch):
        from docksmith.store import init, images_dir, layers_dir, cache_dir
        monkeypatch.setenv("DOCKSMITH_ROOT", str(tmp_path))
        init()
        assert images_dir().exists()
        assert layers_dir().exists()
        assert cache_dir().exists()

    def test_idempotent(self, tmp_path, monkeypatch):
        from docksmith.store import init
        monkeypatch.setenv("DOCKSMITH_ROOT", str(tmp_path))
        init()
        init()  # Should not raise
