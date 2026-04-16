"""
Unit tests for docksmith/cache.py

Key property: cache key is deterministic and matches spec §5.1.
"""

import os
import pytest

from docksmith.cache import compute_cache_key, _serialize_env


class TestSerializeEnv:
    def test_empty(self):
        assert _serialize_env({}) == ""

    def test_sorted_keys(self):
        result = _serialize_env({"B": "2", "A": "1"})
        assert result == "A=1\nB=2\n"

    def test_single(self):
        assert _serialize_env({"X": "y"}) == "X=y\n"


class TestCacheKey:
    def test_deterministic(self):
        k1 = compute_cache_key("RUN echo hi", "sha256:abc", "/app", {"A": "1"})
        k2 = compute_cache_key("RUN echo hi", "sha256:abc", "/app", {"A": "1"})
        assert k1 == k2

    def test_hex_string(self):
        k = compute_cache_key("RUN echo hi", "sha256:abc", "/", {})
        assert len(k) == 64  # bare hex, no prefix

    def test_instruction_change_invalidates(self):
        base = dict(prev_layer_digest="sha256:abc", workdir="/", env={})
        k1 = compute_cache_key("RUN echo hi", **base)
        k2 = compute_cache_key("RUN echo bye", **base)
        assert k1 != k2

    def test_prev_layer_change_invalidates(self):
        k1 = compute_cache_key("RUN echo hi", "sha256:aaa", "/", {})
        k2 = compute_cache_key("RUN echo hi", "sha256:bbb", "/", {})
        assert k1 != k2

    def test_workdir_change_invalidates(self):
        k1 = compute_cache_key("RUN echo hi", "sha256:abc", "/app", {})
        k2 = compute_cache_key("RUN echo hi", "sha256:abc", "/src", {})
        assert k1 != k2

    def test_env_change_invalidates(self):
        k1 = compute_cache_key("RUN echo hi", "sha256:abc", "/", {"A": "1"})
        k2 = compute_cache_key("RUN echo hi", "sha256:abc", "/", {"A": "2"})
        assert k1 != k2

    def test_env_key_order_invariant(self):
        """ENV serialization must be sorted: B=2,A=1 == A=1,B=2."""
        k1 = compute_cache_key("RUN x", "sha256:abc", "/", {"A": "1", "B": "2"})
        k2 = compute_cache_key("RUN x", "sha256:abc", "/", {"B": "2", "A": "1"})
        assert k1 == k2

    def test_copy_file_digest_included(self):
        k1 = compute_cache_key("COPY . /app", "sha256:abc", "/", {},
                               copy_file_digests=["aaa"])
        k2 = compute_cache_key("COPY . /app", "sha256:abc", "/", {},
                               copy_file_digests=["bbb"])
        assert k1 != k2

    def test_copy_no_digest_vs_with_digest(self):
        k1 = compute_cache_key("COPY . /app", "sha256:abc", "/", {},
                               copy_file_digests=None)
        k2 = compute_cache_key("COPY . /app", "sha256:abc", "/", {},
                               copy_file_digests=["aaa"])
        assert k1 != k2

    def test_trailing_whitespace_stripped(self):
        """Trailing whitespace on instruction should not affect key."""
        k1 = compute_cache_key("RUN echo hi", "sha256:abc", "/", {})
        k2 = compute_cache_key("RUN echo hi   ", "sha256:abc", "/", {})
        assert k1 == k2


class TestCacheIndexIO:
    def test_roundtrip(self, tmp_path, monkeypatch):
        import docksmith.store as store_mod
        monkeypatch.setenv("DOCKSMITH_ROOT", str(tmp_path))
        store_mod.init()

        from docksmith.cache import cache_store, cache_lookup

        cache_store("key1", "sha256:layer1")
        result = cache_lookup("key1")
        # No layer tar on disk → miss
        assert result is None

    def test_hit_requires_tar_on_disk(self, tmp_path, monkeypatch):
        import docksmith.store as store_mod
        monkeypatch.setenv("DOCKSMITH_ROOT", str(tmp_path))
        store_mod.init()

        from docksmith.cache import cache_store, cache_lookup

        layer_digest = "sha256:" + "a" * 64
        cache_store("mykey", layer_digest)

        # Create the tar file
        hex_val = layer_digest.removeprefix("sha256:")
        tar_path = store_mod.layers_dir() / f"{hex_val}.tar"
        tar_path.write_bytes(b"fake tar content")

        result = cache_lookup("mykey")
        assert result == layer_digest
