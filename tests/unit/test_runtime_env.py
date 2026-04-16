"""Unit tests for runtime environment construction."""

import os
import signal
import stat

from docksmith.runtime import _build_exec_env, _ensure_runtime_fs, _wait_status_to_exit_code


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


def test_ensure_runtime_fs_creates_tmp_and_dev_null(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    _ensure_runtime_fs(str(root))

    tmp_dir = root / "tmp"
    dev_null = root / "dev" / "null"

    assert tmp_dir.is_dir()
    assert stat.S_IMODE(tmp_dir.stat().st_mode) == 0o1777
    assert dev_null.exists()
    assert stat.S_ISCHR(dev_null.stat().st_mode) or stat.S_ISREG(dev_null.stat().st_mode)


def test_ensure_runtime_fs_handles_tmp_symlink(tmp_path) -> None:
    root = tmp_path / "root"
    (root / "var").mkdir(parents=True)
    os.makedirs(root / "dev", exist_ok=True)
    os.symlink("var/tmp", root / "tmp")

    _ensure_runtime_fs(str(root))

    assert (root / "var" / "tmp").is_dir()
