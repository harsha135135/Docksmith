"""Unit tests for runtime environment construction."""

from docksmith.runtime import _build_exec_env


def test_build_exec_env_sets_default_path() -> None:
    env = _build_exec_env({})
    assert env["PATH"] == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def test_build_exec_env_applies_overrides() -> None:
    env = _build_exec_env({"GREETING": "Hi", "PATH": "/custom"})
    assert env["GREETING"] == "Hi"
    assert env["PATH"] == "/custom"


def test_build_exec_env_does_not_inherit_host_variables(monkeypatch) -> None:
    monkeypatch.setenv("DO_NOT_LEAK", "secret")
    env = _build_exec_env({})
    assert "DO_NOT_LEAK" not in env
