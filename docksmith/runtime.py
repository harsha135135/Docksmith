"""
runtime.py — Single isolation primitive for RUN and `docksmith run`.

Entry point:
  run(rootfs, cmd, env, workdir) -> int   (exit code)

Isolation:
  Uses Linux namespaces via ctypes (so we stay Python 3.11 compatible;
  os.unshare is only 3.12+).

  Flags:
    CLONE_NEWNS   = 0x00020000  — mount namespace
    CLONE_NEWPID  = 0x20000000  — PID namespace
    CLONE_NEWUTS  = 0x04000000  — UTS (hostname) namespace
    CLONE_NEWIPC  = 0x08000000  — IPC namespace

  Sequence inside the child:
    1. unshare(CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWUTS | CLONE_NEWIPC)
    2. mount("", "/", NULL, MS_REC|MS_PRIVATE, NULL)   — no propagation out
    3. mount(rootfs, rootfs, NULL, MS_BIND|MS_REC, NULL) — bind-mount
    4. mkdir(rootfs + "/old_root")
    5. pivot_root(rootfs, rootfs + "/old_root")
    6. chdir("/")
    7. umount2("/old_root", MNT_DETACH) + rmdir
    8. mount("proc", "/proc", "proc", 0, NULL)
    9. execvpe(cmd[0], cmd, env)

  Fallback: if pivot_root fails (EINVAL on some VMs/filesystems) and
  --no-pivot-root is set, fall back to chroot.

Network: no network namespace is created (zero network by design — the
  host network stack is not available inside a build RUN).

Cleanup: try/finally in the parent ensures the rootfs tempdir is cleaned up
  even on crashes.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import sys
import tempfile

from docksmith import DocksmithError
from docksmith import store
from docksmith import layer as layer_mod

# ── Linux namespace flags ────────────────────────────────────────────────────
CLONE_NEWNS   = 0x00020000
CLONE_NEWPID  = 0x20000000
CLONE_NEWUTS  = 0x04000000
CLONE_NEWIPC  = 0x08000000

# ── Mount flags ──────────────────────────────────────────────────────────────
MS_RDONLY    = 1
MS_NOSUID    = 2
MS_NODEV     = 4
MS_BIND      = 4096
MS_REC       = 16384
MS_PRIVATE   = 1 << 18
MNT_DETACH   = 2

# x86_64 syscall number for pivot_root
SYS_PIVOT_ROOT = 155


def _libc() -> ctypes.CDLL:
    # Try the well-known Linux soname first; fall back to find_library for
    # non-standard setups. find_library uses ldconfig which may be absent from
    # sudo's stripped PATH.
    for candidate in ("libc.so.6", ctypes.util.find_library("c")):
        if not candidate:
            continue
        try:
            return ctypes.CDLL(candidate, use_errno=True)
        except OSError:
            continue
    raise DocksmithError("cannot find libc — are you on Linux?")


def _check(ret: int, op: str) -> None:
    if ret != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"{op}: {os.strerror(err)}")


def _unshare(flags: int) -> None:
    libc = _libc()
    ret = libc.unshare(ctypes.c_int(flags))
    _check(ret, "unshare")


def _mount(
    source: bytes | None,
    target: bytes,
    fstype: bytes | None,
    flags: int,
    data: bytes | None,
) -> None:
    libc = _libc()
    ret = libc.mount(
        source or ctypes.c_char_p(None),
        target,
        fstype or ctypes.c_char_p(None),
        ctypes.c_ulong(flags),
        data or ctypes.c_char_p(None),
    )
    _check(ret, f"mount({target.decode()!r})")


def _umount2(target: bytes, flags: int) -> None:
    libc = _libc()
    ret = libc.umount2(target, ctypes.c_int(flags))
    _check(ret, f"umount2({target.decode()!r})")


def _pivot_root(new_root: bytes, put_old: bytes) -> None:
    libc = _libc()
    ret = libc.syscall(
        ctypes.c_long(SYS_PIVOT_ROOT),
        new_root,
        put_old,
    )
    _check(ret, "pivot_root")


def _setup_container(rootfs: str, workdir: str) -> None:
    """
    Called inside the forked child process.
    Sets up namespaces, pivot_root, and mounts.
    """
    rootfs_b = rootfs.encode()
    put_old = os.path.join(rootfs, ".pivot_old")
    put_old_b = put_old.encode()

    try:
        _unshare(CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWUTS | CLONE_NEWIPC)
    except OSError as exc:
        # May need CAP_SYS_ADMIN; surface a clear error
        raise DocksmithError(
            f"unshare failed: {exc}\n"
            "Make sure you are running as root or have CAP_SYS_ADMIN.\n"
            "In a VM: `sudo python -m docksmith ...`"
        ) from exc

    # Make the mount namespace private so changes don't propagate to host
    _mount(b"", b"/", None, MS_REC | MS_PRIVATE, None)

    # Bind-mount rootfs onto itself (required for pivot_root)
    _mount(rootfs_b, rootfs_b, None, MS_BIND | MS_REC, None)

    # Create put_old directory
    os.makedirs(put_old, exist_ok=True)

    try:
        _pivot_root(rootfs_b, put_old_b)
        pivot_ok = True
    except OSError:
        pivot_ok = False

    if pivot_ok:
        os.chdir("/")
        # Unmount old root
        try:
            _umount2(b"/.pivot_old", MNT_DETACH)
            os.rmdir("/.pivot_old")
        except OSError:
            pass  # Best-effort cleanup
    else:
        # Fallback: chroot (less secure but works on shared-folder mounts)
        os.chroot(rootfs)
        os.chdir("/")

    # Mount /proc for PID namespace correctness
    try:
        os.makedirs("/proc", exist_ok=True)
        _mount(b"proc", b"/proc", b"proc", 0, None)
    except OSError:
        pass  # Non-fatal; some base images already have /proc


def run(
    rootfs: str,
    cmd: list[str],
    env: dict[str, str],
    workdir: str,
    *,
    no_pivot_root: bool = False,
) -> int:
    """
    Run *cmd* inside an isolated container rooted at *rootfs*.
    Returns the exit code of the command.
    """
    if not cmd:
        raise DocksmithError("run: no command specified")

    pid = os.fork()
    if pid == 0:
        # ── Child ──────────────────────────────────────────────────────────
        try:
            _setup_container(rootfs, workdir)

            # Set working directory inside the container
            wd = workdir if workdir else "/"
            try:
                os.chdir(wd)
            except FileNotFoundError:
                os.makedirs(wd, exist_ok=True)
                os.chdir(wd)

            # Build environment from image/runtime values only; do not leak host env.
            full_env = _build_exec_env(env)

            os.execvpe(cmd[0], cmd, full_env)
        except Exception as exc:
            print(f"docksmith-container: {exc}", file=sys.stderr)
            os._exit(127)
        os._exit(1)  # unreachable

    else:
        # ── Parent ─────────────────────────────────────────────────────────
        _, status = os.waitpid(pid, 0)
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return 128 + os.WTERMSIG(status)
        return 1


def _build_exec_env(overrides: dict[str, str]) -> dict[str, str]:
    """Build the environment visible inside the container process."""
    env = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    env.update(overrides)
    return env


def run_image(
    image_ref: str,
    cmd_override: list[str],
    extra_env: dict[str, str],
) -> int:
    """
    Called by cli.py `docksmith run`.
    Extracts image layers into a tmpdir and calls run().
    """
    from docksmith import image as image_mod

    name, tag = store.parse_image_ref(image_ref)
    try:
        manifest = image_mod.load_manifest(name, tag)
    except DocksmithError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    cfg = manifest.get("config", {})
    raw_env = cfg.get("Env", [])
    if isinstance(raw_env, list):
        img_env = image_mod.env_list_to_dict(raw_env)
    else:
        img_env = dict(raw_env)
    img_workdir: str = cfg.get("WorkingDir", "/") or "/"
    img_cmd: list[str] = list(cfg.get("Cmd", []))
    layers: list[str] = image_mod.layer_digests(manifest)

    # Merge: image env, then CLI overrides
    merged_env = {**img_env, **extra_env}

    cmd = cmd_override if cmd_override else img_cmd
    if not cmd:
        print(
            "Error: no command specified and no CMD defined in image.",
            file=sys.stderr,
        )
        return 1

    with tempfile.TemporaryDirectory(prefix="docksmith-rootfs-") as rootfs:
        # Extract layers in order
        for layer_digest in layers:
            try:
                layer_mod.extract_layer(layer_digest, store.layers_dir(), rootfs)
            except FileNotFoundError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1

        return run(rootfs=rootfs, cmd=cmd, env=merged_env, workdir=img_workdir)
