"""Unit tests for runtime environment construction."""

import os
import signal

from docksmith.runtime import _build_exec_env, _wait_status_to_exit_code


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


def test_wait_status_to_exit_code_exited() -> None:
    pid = os.fork()
    if pid == 0:
        os._exit(7)
    _, status = os.waitpid(pid, 0)
    assert _wait_status_to_exit_code(status) == 7


def test_wait_status_to_exit_code_signaled() -> None:
    pid = os.fork()
    if pid == 0:
        os.kill(os.getpid(), signal.SIGTERM)
        os._exit(1)
    _, status = os.waitpid(pid, 0)
    assert _wait_status_to_exit_code(status) == 128 + signal.SIGTERM
