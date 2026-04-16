<div align="center">

# Docksmith

**A simplified Docker‑like build‑and‑runtime system.**

*PES University · Semester 6 · Cloud Computing Project*

</div>

---

## Overview

Docksmith is a single‑binary CLI that re‑implements three core ideas behind Docker
without using Docker, runc, containerd, or any existing container runtime:

| # | Subsystem | What it does |
|:-:|-----------|--------------|
| 1 | **Build engine** | Reads a `Docksmithfile`, executes 6 instructions, emits content‑addressed delta layers. |
| 2 | **Build cache** | Deterministic SHA‑256 cache keys, cascade invalidation, per‑step `[CACHE HIT]` / `[CACHE MISS]` reporting. |
| 3 | **Container runtime** | Linux namespace isolation (`unshare` + `pivot_root`), the same primitive used for both build‑time `RUN` and `docksmith run`. |

All state lives on disk under `~/.docksmith/`. There is **no daemon**, **no
network access during build or run**, and **no external runtime dependency**
beyond the Linux kernel and Python ≥ 3.11.

---

## Team

| Member | GitHub |
|--------|--------|
| **Harsha**   | [@harsha135135](https://github.com/harsha135135) |
| **Dennis**   | — |
| **Aviyakth** | — |
| **Hrushik**  | — |

---

## Repository Layout

```
docksmith/
├── docksmith/                  # Python package — the CLI + engines
│   ├── cli.py                  #  argparse subcommands: build / images / run / rmi
│   ├── parser.py               #  Docksmithfile tokenizer + AST
│   ├── builder.py              #  build state machine + cache wiring
│   ├── cache.py                #  cache key (spec §5.1) + index.json I/O
│   ├── layer.py                #  deterministic tar writer + delta computation
│   ├── image.py                #  manifest I/O + canonical digest rule
│   ├── runtime.py              #  Linux namespace isolation primitive
│   ├── store.py                #  ~/.docksmith/ layout helpers
│   └── _term.py                #  TTY-aware ANSI colorizer
├── scripts/
│   ├── import-base-image.sh    # one-time Alpine bootstrap (only network call)
│   └── docksmith-sudo-wrapper  # `sudo docksmith` shim (auto-detects repo root)
├── sample-app/                 # demo app — uses ALL 6 instructions
│   ├── Docksmithfile
│   ├── app.sh                  # banner + colored output, ENVs overridable via -e
│   └── vendor/colorize.sh      # bundled ANSI helper (no network needed)
├── tests/
│   ├── unit/                   # 63 tests — parser, layer, cache, image, store, builder, runtime
│   ├── integration/            # cache invalidation matrix + isolation leak test
│   └── reproducibility/        # byte-identical rebuild verification
├── pyproject.toml
└── README.md
```

---

## Spec → Implementation Map

A point‑by‑point summary of every requirement in `DOCKSMITH.pdf` and where it
lives in the codebase.

### §3 — Build Language (6 instructions)

| Instruction | Behaviour | Implemented |
|-------------|-----------|:-:|
| `FROM <image>[:<tag>]` | Loads base image manifest into build state. Errors clearly if missing. | [parser.py](docksmith/parser.py), [builder.py](docksmith/builder.py#L147) |
| `COPY <src> <dest>` | Glob-aware (`*`, `**`); creates missing dirs; produces a delta layer. | [builder.py](docksmith/builder.py#L279) |
| `RUN <command>` | Executes inside the assembled rootfs via the isolation primitive (not the host). | [builder.py](docksmith/builder.py#L320) + [runtime.py](docksmith/runtime.py) |
| `WORKDIR <path>` | Updates state; auto‑creates the dir in rootfs before the next `COPY`/`RUN`. No layer. | [builder.py](docksmith/builder.py#L196) |
| `ENV <K>=<V>` | Stored in image config, injected into every container AND every build‑time `RUN`. No layer. | [builder.py](docksmith/builder.py#L201) |
| `CMD ["a","b"]` | JSON array form required; default container command. No layer. | [parser.py](docksmith/parser.py#L132) |
| Unknown instruction | Errors with line number. | [parser.py](docksmith/parser.py#L71) |

### §4 — Image Format

* **Manifest** (`~/.docksmith/images/<name>_<tag>.json`) carries `name`, `tag`,
  `digest`, `created`, per‑layer `{digest,size,createdBy}` records, and
  `config: {Env, WorkingDir, Cmd}`. — [image.py](docksmith/image.py)
* **Manifest digest rule** (§4.1): serialize with `digest=""`, SHA‑256 the
  canonical bytes, then write the manifest with the computed `sha256:<hex>`.
  — [`compute_digest`](docksmith/image.py#L84)
* **Layers** are content‑addressed by SHA‑256 of the raw tar bytes, stored at
  `~/.docksmith/layers/<hex>.tar`. Identical content → one file on disk.
  Layers are **not** reference‑counted — `rmi` deletes everything the manifest
  references. — [layer.py](docksmith/layer.py), [cli.py](docksmith/cli.py#L62)
* **Base image** must be present locally before any build (§4.3). The bootstrap
  script `scripts/import-base-image.sh` is the **only** place that touches the
  network; after it runs, the system is fully offline.

### §5 — Build Cache

Cache key (§5.1) is `sha256` of the following parts joined by newline:

```
prev_layer_digest        ← last COPY/RUN layer digest, OR base manifest digest for the
                           first layer-producing step (so changing FROM invalidates all
                           downstream entries)
\n  full instruction text  ← exactly as written in the Docksmithfile
\n  workdir                ← current WORKDIR (empty string if unset)
\n  sorted env             ← "KEY=value\n" pairs, lex-sorted by key (empty if no ENV)
\n  copy_file_digests      ← (COPY only) sha256 of each src file, lex-sorted by path
```

Implementation: [cache.py](docksmith/cache.py#L32).

| Spec rule | Implementation |
|-----------|----------------|
| `[CACHE HIT]` reuses stored layer; `[CACHE MISS]` executes & stores | [builder.py](docksmith/builder.py#L228) |
| Cascade: any miss forces all subsequent steps to miss | [builder.py](docksmith/builder.py#L120) (`cache_cascaded`) |
| `--no-cache` skips lookups & writes; layers still persisted | [builder.py](docksmith/builder.py#L229) |
| FROM line printed without `[CACHE …]` or timing | [builder.py](docksmith/builder.py#L150) |
| Hit requires both index entry **and** layer tar on disk | [cache.py](docksmith/cache.py#L88) |

### §5.3 — Invalidation Matrix

| Trigger | Scope | Verified by |
|---------|-------|-------------|
| COPY source file changes | step + below | [test_row1](tests/integration/test_cache_invalidation.py#L73) |
| Instruction text changes | step + below | [test_row2](tests/integration/test_cache_invalidation.py#L91) |
| FROM image changes | all layer steps | covered via `prev_layer_digest = base_manifest_digest` |
| Layer file missing | step + below | [test_row5](tests/integration/test_cache_invalidation.py#L127) |
| `--no-cache` | all steps | [test_row6](tests/integration/test_cache_invalidation.py#L142) |
| WORKDIR change | step + below | [test_row3](tests/integration/test_cache_invalidation.py#L103) |
| ENV change | step + below | [test_row4](tests/integration/test_cache_invalidation.py#L115) |

### §6 — Container Runtime

The same primitive is used for build‑time `RUN` and `docksmith run`
([builder.py:_exec_run](docksmith/builder.py#L320) and
[runtime.py:run](docksmith/runtime.py#L185)).

```
fork()
  child:
    unshare(CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWUTS | CLONE_NEWIPC)
    mount("", "/", MS_REC | MS_PRIVATE)            ← stop propagation to host
    mount(rootfs, rootfs, MS_BIND | MS_REC)        ← required for pivot_root
    mkdir rootfs/.pivot_old
    pivot_root(rootfs, rootfs/.pivot_old)
    chdir("/")
    umount2("/.pivot_old", MNT_DETACH)
    mount("proc", "/proc", "proc")
    execvpe(cmd, env)                              ← image ENV ⊕ -e overrides
  parent:
    waitpid → return exit code
    cleanup rootfs tempdir (try/finally)
```

| Rule | Implementation |
|------|----------------|
| Image ENV injected; `-e KEY=VALUE` overrides win | [runtime.py:run_image](docksmith/runtime.py#L242) |
| Working dir set to image `WorkingDir`, defaults to `/` | [runtime.py](docksmith/runtime.py#L207) |
| No detached mode — CLI blocks until container exits | [cli.py:_cmd_run](docksmith/cli.py#L43) |
| No CMD + no override → clear error | [runtime.py:run_image](docksmith/runtime.py#L274) |
| Container writes do **not** touch host | [test_isolation.py](tests/integration/test_isolation.py) |

### §7 — CLI

| Command | Status |
|---------|:-:|
| `docksmith build -t <name:tag> <context>` | ✅ |
| `--no-cache` | ✅ |
| `docksmith images` (NAME / TAG / IMAGE ID 12‑char / CREATED) | ✅ |
| `docksmith rmi <name:tag>` (manifest + layers, no refcounting) | ✅ |
| `docksmith run <name:tag> [cmd]` | ✅ |
| `-e KEY=VALUE` (repeatable) | ✅ |

### §8 — Constraints

| Constraint | Status |
|------------|:-:|
| No network during build or run | ✅ — verified by `_build_exec_env` not inheriting host env |
| No existing container runtimes | ✅ — only `ctypes` to libc |
| Immutable layers | ✅ — content‑addressed; `write_layer` skips if exists |
| Same isolation primitive for build & run | ✅ |
| File written in container does **not** appear on host | ✅ — `tests/integration/test_isolation.py` |
| Reproducible builds (sorted tar entries, mtime=0, uid/gid=0) | ✅ — `tests/reproducibility/` |
| `created` timestamp preserved on all‑hit rebuild | ✅ — [builder.py:127](docksmith/builder.py#L127) |

---

## Quick Start

> **Linux required** (kernel ≥ 5.4). On macOS / Windows, run inside a Linux VM.

```bash
# 1. Install
git clone https://github.com/harsha135135/Docksmith.git docksmith && cd docksmith
pip install --user -e . --break-system-packages

# 2. Install sudo wrapper so `sudo docksmith` resolves the package
sudo install -m 0755 scripts/docksmith-sudo-wrapper /usr/local/bin/docksmith

# 3. Bootstrap the Alpine base image (one-time, only network call)
sudo bash scripts/import-base-image.sh

# 4. Verify
docksmith images
# alpine    3.18    <id>    <created>
```

---

## 8‑Step Demo (matches §9 of the spec)

```bash
# 1 — Cold build: clear cache index first so every layer step shows [CACHE MISS]
sudo docksmith build --cold -t myapp:latest ./sample-app

# 2 — Warm build: every layer step shows [CACHE HIT], near-instant
sudo docksmith build -t myapp:latest ./sample-app

# 3 — Edit source → partial invalidation cascade
echo '# bump' >> sample-app/app.sh
sudo docksmith build -t myapp:latest ./sample-app

# 4 — List images
docksmith images

# 5 — Run container (banner + colored output, exits 0)
sudo docksmith run myapp:latest

# 6 — ENV override at runtime
sudo docksmith run -e GREETING=Howdy -e TARGET=World -e EMPHASIS=magenta myapp:latest

# 7 — Isolation check (sentinel file written inside must NOT leak)
sudo docksmith run myapp:latest /bin/sh -c 'echo secret > /tmp/leak.txt'
ls /tmp/leak.txt 2>/dev/null && echo "FAIL: leaked" || echo "PASS: isolated"

# 8 — Remove image and its layers
sudo docksmith rmi myapp:latest
docksmith images   # only alpine:3.18 remains
```

---

## Sample App

`sample-app/` exercises **all six** required instructions in one Docksmithfile:

```dockerfile
FROM alpine:3.18

WORKDIR /app

ENV APP_VERSION=1.2.0
ENV GREETING=Hello TARGET=Docksmith
ENV EMPHASIS=cyan

COPY app.sh /app/app.sh
COPY vendor/ /app/vendor/

RUN chmod +x /app/app.sh && mkdir -p /app/.cache

CMD ["/bin/sh", "/app/app.sh"]
```

When you `docksmith run myapp:latest` you get a colored banner, version
info, hostname/uname, PID, and working directory — all printed from inside
the isolated container. Override any of `GREETING`, `TARGET`, `EMPHASIS`,
or `APP_VERSION` with `-e KEY=value`.

---

## CLI Reference

```
docksmith build -t <name:tag> <context>   Build an image from a Docksmithfile
  --cold                                    Clear cache index before build
  --no-cache                                Skip all cache lookups and writes
  -f, --file <path>                         Use a Docksmithfile outside the context

docksmith images                          List all images (Name, Tag, ID, Created)
docksmith run    <name:tag> [cmd]         Run a container in the foreground
  -e KEY=VALUE                              Override/add an env var (repeatable)
docksmith rmi    <name:tag>               Remove image manifest + its layer files
```

`DOCKSMITH_ROOT=/path` overrides the default `~/.docksmith/` state directory.
`NO_COLOR=1` (or `DOCKSMITH_NO_COLOR=1`) disables ANSI output.

---

## Tests

```bash
# Unit tests — no root needed
PYTHONPATH=. pytest tests/unit/ -v
# Expected: 63 passed

# Integration tests — require root (RUN uses Linux namespaces)
sudo PYTHONPATH=. pytest tests/integration/ -v

# Byte-identical rebuild test
sudo bash tests/reproducibility/test_identical_builds.sh
```

---

## Out of Scope (per spec §1)

Networking inside containers • image registries / push / pull • CPU & memory
limits • multi‑stage builds • bind mounts / volumes • detached / daemon
containers • the `EXPOSE`, `VOLUME`, `ADD`, `ARG`, `ENTRYPOINT`, and `SHELL`
instructions.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `sudo docksmith: command not found` | `sudo install -m 0755 scripts/docksmith-sudo-wrapper /usr/local/bin/docksmith` |
| `image not found: alpine:3.18` | `sudo bash scripts/import-base-image.sh` |
| `unshare failed: Operation not permitted` | Use `sudo` — needs `CAP_SYS_ADMIN` |
| `docksmith images` shows nothing after a sudo build | Re‑run the wrapper install — it pins `DOCKSMITH_ROOT` to your home |
| Tests fail with `import pytest` error | `pip install --user pytest --break-system-packages` |
| WSL2 fails on `pivot_root` | Use a real Linux VM (UTM / VirtualBox) instead of WSL |
