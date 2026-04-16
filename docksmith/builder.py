"""
builder.py — Build state machine.

BuildState tracks:
  env           — accumulated ENV dict
  workdir       — current WORKDIR
  cmd           — CMD list
  layer_records — ordered list of {digest, size, createdBy} dicts
  base_digest   — manifest digest of the FROM image (used as "previous" for first layer step)

Output format (spec §5.2):
  Step 1/3 : FROM alpine:3.18
  Step 2/3 : COPY . /app [CACHE MISS] 0.09s
  Step 3/3 : RUN echo "build complete" [CACHE HIT] 3.82s
  Successfully built sha256:a3f9b2c1 myapp:latest (3.91s)

FROM never prints status/timing.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from docksmith import DocksmithError, store
from docksmith import cache as cache_mod
from docksmith import image as image_mod
from docksmith import layer as layer_mod
from docksmith._term import style
from docksmith.parser import Instruction, parse_file


@dataclass
class BuildState:
    env: dict[str, str] = field(default_factory=dict)
    workdir: str = "/"
    cmd: list[str] = field(default_factory=list)
    layer_records: list[dict] = field(default_factory=list)  # {digest, size, createdBy}
    base_digest: str = ""   # manifest digest of the FROM image
    base_layer_count: int = 0  # number of inherited layers from FROM


def _prev_layer_digest(state: BuildState) -> str:
    """Return the digest to use as 'previous layer' for cache key computation."""
    # Spec §5.1: first layer-producing step must use base manifest digest,
    # not the digest of the last inherited base layer tar.
    if len(state.layer_records) <= state.base_layer_count:
        return state.base_digest
    if state.layer_records:
        return state.layer_records[-1]["digest"]
    return state.base_digest


def build_image(
    context_dir: str,
    tag: Optional[str] = None,
    no_cache: bool = False,
    docksmithfile: Optional[str] = None,
) -> int:
    """
    Main entry point called by cli.py.
    Returns 0 on success, 1 on error.
    """
    context_dir = str(Path(context_dir).resolve())
    if not os.path.isdir(context_dir):
        print(f"Error: context directory not found: {context_dir}", file=sys.stderr)
        return 1

    dsfile = docksmithfile or os.path.join(context_dir, "Docksmithfile")
    if not os.path.exists(dsfile):
        print(f"Error: Docksmithfile not found: {dsfile}", file=sys.stderr)
        return 1

    try:
        instructions = parse_file(dsfile)
    except DocksmithError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # Parse tag
    if tag:
        out_name, out_tag = store.parse_image_ref(tag)
    else:
        out_name, out_tag = "unnamed", "latest"

    total = len(instructions)
    state = BuildState()
    build_start = time.monotonic()

    # If all steps hit cache, preserve the original created timestamp
    prev_manifest_created: str | None = None
    all_cache_hit = True

    for step_idx, instr in enumerate(instructions, 1):
        step_num = f"Step {step_idx}/{total}"

        if instr.name == "FROM":
            _handle_from(instr, state, step_num)
            # Load previous created timestamp for all-hit preservation
            try:
                existing_path = store.image_path(out_name, out_tag)
                if existing_path.exists():
                    existing = json.loads(existing_path.read_bytes())
                    prev_manifest_created = existing.get("created")
            except Exception:
                pass
        else:
            result = _handle_instruction(
                instr=instr,
                state=state,
                context_dir=context_dir,
                step_num=step_num,
                no_cache=no_cache,
                cache_cascaded=(not all_cache_hit),
            )
            if result == "miss":
                all_cache_hit = False

    # Write final manifest
    created = None
    if all_cache_hit and prev_manifest_created:
        created = prev_manifest_created  # preserve for byte-identical rebuild

    manifest = image_mod.write_manifest(
        name=out_name,
        tag=out_tag,
        layers=state.layer_records,
        env=state.env,
        workdir=state.workdir,
        cmd=state.cmd,
        created=created,
    )

    elapsed = time.monotonic() - build_start
    short_id = manifest["digest"].removeprefix("sha256:")[:8]
    success_word = style("Successfully built", "bold", "green")
    image_ref = style(f"{out_name}:{out_tag}", "cyan")
    print(f"{success_word} sha256:{short_id} {image_ref} ({elapsed:.2f}s)")
    return 0


def _handle_from(instr: Instruction, state: BuildState, step_num: str) -> None:
    """Process a FROM instruction: load base image manifest into state."""
    ref = instr.args
    print(f"{step_num} : FROM {ref}")

    if ":" in ref:
        bname, btag = ref.rsplit(":", 1)
    else:
        bname, btag = ref, "latest"

    try:
        manifest = image_mod.load_manifest(bname, btag)
    except DocksmithError as exc:
        raise DocksmithError(
            f"FROM {ref}: {exc}\n"
            "Hint: run `bash scripts/import-base-image.sh` to bootstrap Alpine."
        ) from exc

    cfg = manifest.get("config", {})
    # Env: handle both list ["K=V"] and legacy dict {"K": "V"} formats
    raw_env = cfg.get("Env", [])
    if isinstance(raw_env, list):
        state.env = image_mod.env_list_to_dict(raw_env)
    else:
        state.env = dict(raw_env)

    state.workdir = cfg.get("WorkingDir", "/") or "/"
    state.cmd = list(cfg.get("Cmd", []))

    # layers: handle both object [{digest,...}] and legacy [str] formats
    state.layer_records = [
        r if isinstance(r, dict) else {"digest": r, "size": 0, "createdBy": ""}
        for r in manifest.get("layers", [])
    ]
    state.base_layer_count = len(state.layer_records)
    state.base_digest = manifest.get("digest", "")


def _handle_instruction(
    instr: Instruction,
    state: BuildState,
    context_dir: str,
    step_num: str,
    no_cache: bool,
    cache_cascaded: bool,
) -> str:
    """Handle one non-FROM instruction. Returns 'hit' or 'miss'."""
    t_start = time.monotonic()

    if instr.name == "WORKDIR":
        state.workdir = instr.args
        print(f"{step_num} : WORKDIR {instr.args}")
        return "hit"

    if instr.name == "ENV":
        state.env.update(instr.args)
        print(f"{step_num} : ENV {instr.args_raw}")
        return "hit"

    if instr.name == "CMD":
        state.cmd = instr.args
        print(f"{step_num} : CMD {instr.args_raw}")
        return "hit"

    # ── Layer-producing instructions: COPY and RUN ────────────────────────
    # Full instruction text as written (spec §5.1)
    full_text = f"{instr.name} {instr.args_raw}"

    copy_file_digests: list[str] | None = None
    if instr.name == "COPY":
        copy_file_digests = _collect_copy_digests(instr, context_dir)

    cache_key = cache_mod.compute_cache_key(
        instruction_text=full_text,
        prev_layer_digest=_prev_layer_digest(state),
        workdir=state.workdir,
        env=state.env,
        copy_file_digests=copy_file_digests,
    )

    # Cache lookup (unless disabled or cascaded)
    layer_digest: str | None = None
    if not no_cache and not cache_cascaded:
        layer_digest = cache_mod.cache_lookup(cache_key)

    if layer_digest is not None:
        record = image_mod.make_layer_record(layer_digest, store.layers_dir(), full_text)
        state.layer_records.append(record)
        elapsed = time.monotonic() - t_start
        tag = style("[CACHE HIT]", "green")
        print(f"{step_num} : {instr.name} {instr.args_raw} {tag} {elapsed:.2f}s")
        return "hit"

    # Cache miss — execute
    if instr.name == "COPY":
        layer_digest = _exec_copy(instr, state, context_dir)
    elif instr.name == "RUN":
        layer_digest = _exec_run(instr, state)

    record = image_mod.make_layer_record(layer_digest, store.layers_dir(), full_text)
    state.layer_records.append(record)
    if not no_cache:
        cache_mod.cache_store(cache_key, layer_digest)

    elapsed = time.monotonic() - t_start
    tag = style("[CACHE MISS]", "yellow")
    print(f"{step_num} : {instr.name} {instr.args_raw} {tag} {elapsed:.2f}s")
    return "miss"


def _collect_copy_digests(instr: Instruction, context_dir: str) -> list[str]:
    """Collect sha256 of each COPY src file in lex-sorted order."""
    srcs = instr.args["srcs"]
    all_files: list[str] = []
    for pattern in srcs:
        abs_pattern = os.path.join(context_dir, pattern)
        matched = sorted(glob.glob(abs_pattern, recursive=True))
        if not matched:
            raise DocksmithError(
                f"COPY: no files matched pattern {pattern!r} in context {context_dir}"
            )
        for match in matched:
            if os.path.isfile(match):
                all_files.append(match)
            elif os.path.isdir(match):
                for root, dirs, files in os.walk(match):
                    dirs.sort()
                    for f in sorted(files):
                        all_files.append(os.path.join(root, f))

    all_files = sorted(set(all_files))
    return [cache_mod.file_digest(p) for p in all_files]


def _exec_copy(instr: Instruction, state: BuildState, context_dir: str) -> str:
    """Execute a COPY instruction and return layer digest."""
    srcs = instr.args["srcs"]
    dest = instr.args["dest"]

    all_files: list[tuple[str, str]] = []  # (abs_src, dest_path_in_image)
    for pattern in srcs:
        abs_pattern = os.path.join(context_dir, pattern)
        matched = sorted(glob.glob(abs_pattern, recursive=True))
        if not matched:
            raise DocksmithError(f"COPY: no files matched {pattern!r} in {context_dir}")
        for match in matched:
            if os.path.isfile(match):
                rel = os.path.relpath(match, context_dir)
                if dest.endswith("/") or os.path.basename(rel) != rel:
                    dest_path = dest.rstrip("/") + "/" + os.path.basename(rel)
                else:
                    dest_path = dest
                all_files.append((match, dest_path))
            elif os.path.isdir(match):
                for root, dirs, files in os.walk(match):
                    dirs.sort()
                    for f in sorted(files):
                        abs_f = os.path.join(root, f)
                        dest_path = dest.rstrip("/") + "/" + os.path.relpath(abs_f, match)
                        all_files.append((abs_f, dest_path))

    all_files.sort(key=lambda x: x[1])

    with tempfile.TemporaryDirectory() as staging:
        for abs_src, dest_path in all_files:
            rel = dest_path.lstrip("/")
            abs_dest = os.path.join(staging, rel)
            os.makedirs(os.path.dirname(abs_dest), exist_ok=True)
            shutil.copy2(abs_src, abs_dest)

        digest = layer_mod.write_layer(staging, _all_files_in(staging), store.layers_dir())

    return digest


def _exec_run(instr: Instruction, state: BuildState) -> str:
    """Execute a RUN command in isolation. Returns delta layer digest."""
    from docksmith import runtime as runtime_mod

    with tempfile.TemporaryDirectory(prefix="docksmith-run-") as rootfs:
        for record in state.layer_records:
            layer_mod.extract_layer(record["digest"], store.layers_dir(), rootfs)

        # Ensure WORKDIR exists in the rootfs before running
        wd_in_rootfs = os.path.join(rootfs, state.workdir.lstrip("/"))
        os.makedirs(wd_in_rootfs, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="docksmith-pre-") as pre_snap:
            _snapshot_dir(rootfs, pre_snap)

            exit_code = runtime_mod.run(
                rootfs=rootfs,
                cmd=["/bin/sh", "-c", instr.args],
                env=state.env,
                workdir=state.workdir,
            )
            if exit_code != 0:
                raise DocksmithError(
                    f"RUN command failed with exit code {exit_code}: {instr.args!r}"
                )

            digest = layer_mod.make_delta_layer(pre_snap, rootfs, store.layers_dir())

    return digest


def _snapshot_dir(src: str, dest: str) -> None:
    """
    Snapshot the file tree for pre/post delta detection.
    Copies regular files; reproduces symlinks without following them.
    Skips special files (FIFOs, devices).
    """
    for root, dirs, files in os.walk(src):
        dirs.sort()
        for fname in sorted(files):
            abs_src = os.path.join(root, fname)
            rel = os.path.relpath(abs_src, src)
            abs_dest = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(abs_dest), exist_ok=True)
            if os.path.islink(abs_src):
                link_target = os.readlink(abs_src)
                if os.path.lexists(abs_dest):
                    os.unlink(abs_dest)
                os.symlink(link_target, abs_dest)
            elif os.path.isfile(abs_src):
                shutil.copy2(abs_src, abs_dest)


def _all_files_in(directory: str) -> list[Path]:
    result: list[Path] = []
    for root, dirs, files in os.walk(directory):
        dirs.sort()
        for f in sorted(files):
            result.append(Path(root) / f)
    return sorted(result)
