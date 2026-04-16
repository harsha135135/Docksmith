"""
Unit tests for docksmith/image.py

Key property: manifest digest round-trip is byte-stable.
"""

import json
import os
import pytest

from docksmith.image import compute_digest, finalize_manifest, verify_manifest_digest


class TestDigestRule:
    def _sample(self) -> dict:
        return {
            "name": "myapp",
            "tag": "latest",
            "digest": "",
            "created": "2024-01-01T00:00:00Z",
            "layers": ["sha256:abc123"],
            "config": {
                "Cmd": ["/bin/sh"],
                "Env": {"PATH": "/usr/bin"},
                "WorkingDir": "/app",
            },
        }

    def test_digest_not_empty(self):
        m = self._sample()
        d = compute_digest(m)
        assert d.startswith("sha256:")
        assert len(d) == 71  # "sha256:" + 64 hex

    def test_digest_deterministic(self):
        m1 = self._sample()
        m2 = self._sample()
        assert compute_digest(m1) == compute_digest(m2)

    def test_finalize_sets_digest(self):
        m = self._sample()
        finalize_manifest(m)
        assert m["digest"].startswith("sha256:")

    def test_verify_round_trip(self):
        m = self._sample()
        finalize_manifest(m)
        assert verify_manifest_digest(m)

    def test_tampered_digest_fails_verify(self):
        m = self._sample()
        finalize_manifest(m)
        m["digest"] = "sha256:" + "0" * 64
        assert not verify_manifest_digest(m)

    def test_digest_changes_with_content(self):
        m1 = self._sample()
        m2 = self._sample()
        m2["layers"] = ["sha256:different"]
        assert compute_digest(m1) != compute_digest(m2)

    def test_digest_field_excluded_from_hash(self):
        """Digest must be computed with digest="" regardless of stored value."""
        m1 = self._sample()
        m1["digest"] = ""
        m2 = self._sample()
        m2["digest"] = "sha256:previousvalue"
        assert compute_digest(m1) == compute_digest(m2)

    def test_canonical_sort_keys(self):
        """Key order in JSON must not affect digest."""
        m1 = {"name": "a", "tag": "b", "digest": "", "created": "x",
              "layers": [], "config": {"Cmd": [], "Env": {}, "WorkingDir": "/"}}
        m2 = {"config": {"WorkingDir": "/", "Env": {}, "Cmd": []},
              "layers": [], "tag": "b", "name": "a",
              "digest": "", "created": "x"}
        assert compute_digest(m1) == compute_digest(m2)
