# Docksmith

A simplified Docker-like build-and-runtime system built entirely from scratch using Python and Linux OS primitives.

**PES University — Semester 6 Systems Programming Project**

> Three subsystems implemented from the ground up:
> 1. **Build caching** with content addressing (SHA-256 layer digests)
> 2. **OS-level process isolation** via Linux namespaces (`unshare` + `pivot_root`)
> 3. **Layered image assembly** with reproducible, deterministic tar archives

---

## Team

| Member | Role |
|--------|------|
| Harsha | Lead / Runtime & Isolation |
| Member 2 | Build Engine & Cache |
| Member 3 | Parser & CLI |
| Member 4 | Testing & Sample App |

---

## What Is Built

### 1. Build Engine
Reads a `Docksmithfile` and executes six instructions: `FROM`, `COPY`, `RUN`, `WORKDIR`, `ENV`, `CMD`.  
Each `COPY` and `RUN` produces an immutable delta layer stored as a content-addressed tar file under `~/.docksmith/layers/`.  
The final image is a JSON manifest in `~/.docksmith/images/`.

### 2. Build Cache
Before every `COPY` or `RUN`, a cache key is computed from:
- Previous layer digest (or base image manifest digest for the first layer step)
- Full instruction text as written
- Current `WORKDIR` value
- Current `ENV` state (lex-sorted)
- `COPY` only: SHA-256 of source files (lex-sorted)

A hit prints `[CACHE HIT]` and reuses the stored layer. A miss executes, stores, and prints `[CACHE MISS]`. Any miss cascades all steps below it.

### 3. Container Runtime
Assembles the image filesystem by extracting all layer tars in order into a temporary directory, then isolates a process using:
- `unshare(CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWUTS | CLONE_NEWIPC)`
- `mount(MS_BIND | MS_REC)` + `pivot_root`
- `/proc` mount for PID namespace

The **same isolation primitive** is used for both `RUN` during build and `docksmith run`.

---

## Project Structure

```
docksmith/
├── docksmith/                  # Python package
│   ├── __init__.py             # DocksmithError base exception
│   ├── __main__.py             # python -m docksmith entry point
│   ├── cli.py                  # CLI: build / images / run / rmi
│   ├── store.py                # ~/.docksmith/ layout & path helpers
│   ├── parser.py               # Docksmithfile tokenizer + AST
│   ├── layer.py                # Deterministic tar writer + delta computation
│   ├── image.py                # Manifest I/O + digest rule
│   ├── cache.py                # Cache key (spec §5.1) + index.json I/O
│   ├── builder.py              # Build state machine
│   └── runtime.py              # Linux namespace isolation primitive
├── scripts/
│   ├── import-base-image.sh    # One-time Alpine bootstrap (only network call)
│   └── docksmith-sudo-wrapper  # Wrapper for /usr/local/bin/docksmith
├── sample-app/
│   ├── Docksmithfile           # Uses all 6 instructions
│   ├── app.sh                  # Shell app (GREETING/TARGET overridable via -e)
│   └── vendor/
│       ├── colorize.sh         # Bundled ANSI color helper
│       └── colorize.py         # Python version (unused at runtime)
├── tests/
│   ├── unit/                   # Parser, layer, image, cache, store (63 tests)
│   ├── integration/            # Cache invalidation matrix + isolation leak test
│   └── reproducibility/        # Byte-identical build verification
├── pyproject.toml
└── README.md
```

### Manifest Format (spec §4.1)

```json
{
  "name": "myapp",
  "tag": "latest",
  "digest": "sha256:<hash>",
  "created": "2026-04-16T09:11:01Z",
  "layers": [
    {"digest": "sha256:eb43...", "size": 8632320, "createdBy": "alpine base layer"},
    {"digest": "sha256:bfbb...", "size": 10240,   "createdBy": "COPY app.sh /app/app.sh"},
    {"digest": "sha256:0b08...", "size": 10240,   "createdBy": "COPY vendor/ /app/vendor/"},
    {"digest": "sha256:84ff...", "size": 10240,   "createdBy": "RUN chmod +x /app/app.sh"}
  ],
  "config": {
    "Env": ["APP_VERSION=1.0.0", "GREETING=Hello"],
    "WorkingDir": "/app",
    "Cmd": ["/bin/sh", "/app/app.sh"]
  }
}
```

---

## Requirements

| Requirement | Detail |
|-------------|--------|
| OS | **Linux only** — kernel ≥ 5.4, x86\_64 |
| Python | 3.11 or higher |
| Privileges | `sudo` / `CAP_SYS_ADMIN` required for `build` (RUN steps) and `run` |
| Network | Only needed once — during `import-base-image.sh` |

> **macOS / Windows users: you must run inside a Linux VM.** See setup instructions below.

---

## VM Setup (macOS — UTM / Windows — VirtualBox or WSL2)

### Option A — macOS with UTM (Recommended for Apple Silicon & Intel)

1. Download **UTM**: https://mac.getutm.app
2. Download **Ubuntu Server 24.04 LTS (AMD64)**: https://ubuntu.com/download/server
   - Choose **AMD64 (x86\_64)** — not ARM
3. In UTM → **Create New VM** → **Emulate** → **Linux**
4. Hardware: ≥ 4 CPU cores, ≥ 4 GB RAM, 20 GB storage
5. **Network**: Shared Network → Port Forwarding → Guest `22` → Host `2222`
6. Install Ubuntu Server; when prompted enable **OpenSSH server** (spacebar to select)
7. After install, connect from macOS terminal:
   ```bash
   ssh your_username@127.0.0.1 -p 2222
   ```
8. **Optional — VS Code Remote SSH:**
   - Install extension: *Remote - SSH* (Microsoft)
   - Command Palette → `Remote-SSH: Connect to Host` → `your_username@127.0.0.1:2222`

### Option B — Windows with WSL2

> ⚠️ WSL2 does not support `pivot_root` fully. Use VirtualBox instead.

1. Download **VirtualBox**: https://www.virtualbox.org
2. Download **Ubuntu Server 24.04 LTS (AMD64)**
3. Create VM: ≥ 4 CPU, ≥ 4 GB RAM, 20 GB disk
4. Network → Adapter 1 → NAT → Port Forwarding → Host `2222` → Guest `22`
5. Install Ubuntu Server with OpenSSH
6. Connect: `ssh your_username@127.0.0.1 -p 2222`

### Option C — Any Linux Machine / VM (native)

No VM setup needed. Proceed directly to the install steps below.

---

## Installation & First-Time Setup

Run all of the following **inside your Linux VM** (or native Linux):

### 1. Clone the repo

```bash
git clone https://github.com/harsha135135/Docksmith.git
cd Docksmith
```

### 2. Install Python 3.11+ and pip

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip git
```

### 3. Install the docksmith CLI

```bash
pip install --user -e . --break-system-packages
```

### 4. Install the sudo wrapper (so `sudo docksmith` works)

```bash
sudo cp scripts/docksmith-sudo-wrapper /usr/local/bin/docksmith
sudo chmod +x /usr/local/bin/docksmith
```

### 5. Bootstrap the Alpine base image (one-time, needs internet)

```bash
sudo bash scripts/import-base-image.sh
```

This downloads Alpine 3.18 minirootfs, verifies its SHA-256, repacks it deterministically, and writes it to `~/.docksmith/`. **All subsequent operations are fully offline.**

### 6. Verify

```bash
docksmith images
# Should show: alpine  3.18  <id>  <created>
```

---

## 8-Command Demo

```bash
# 1 — Cold build: all layer steps show [CACHE MISS]
sudo docksmith build -t myapp:latest ./sample-app

# 2 — Warm build: all layer steps show [CACHE HIT], near-instant
sudo docksmith build -t myapp:latest ./sample-app

# 3 — Edit a source file → partial invalidation
echo '# bump' >> sample-app/app.sh
sudo docksmith build -t myapp:latest ./sample-app
# COPY app.sh → MISS, cascades to COPY vendor/ and RUN → MISS

# 4 — List images
docksmith images

# 5 — Run container (produces visible output, exits 0)
sudo docksmith run myapp:latest

# 6 — ENV override at runtime
sudo docksmith run -e GREETING=Howdy -e TARGET=World myapp:latest

# 7 — Isolation check (file written inside must NOT appear on host)
sudo docksmith run myapp:latest /bin/sh -c \
  "echo secret > /tmp/leak.txt && echo 'wrote /tmp/leak.txt'"
ls /tmp/leak.txt 2>/dev/null && echo "FAIL: leaked" || echo "PASS: isolated"

# 8 — Remove image and its layers
sudo docksmith rmi myapp:latest
docksmith images
# Only alpine:3.18 remains
```

---

## CLI Reference

```
docksmith build -t <name:tag> <context>   Build an image from a Docksmithfile
  --no-cache                               Skip all cache lookups and writes

docksmith images                          List all images (Name, Tag, ID, Created)

docksmith run <name:tag> [cmd]            Run a container in the foreground
  -e KEY=VALUE                            Override/add env var (repeatable)

docksmith rmi <name:tag>                  Remove image manifest + layer files
```

---

## Running Tests

### Unit tests — no root needed

```bash
# From the project root
PYTHONPATH=. pytest tests/unit/ -v
```

Expected: **63 passed**

### Integration tests — require root

```bash
sudo PYTHONPATH=. pytest tests/integration/ -v
```

Covers:
- Cache invalidation matrix (all 6 rows from spec §5.3)
- Container isolation leak test

### Reproducibility test

```bash
sudo bash tests/reproducibility/test_identical_builds.sh
```

Builds the same context twice with `--no-cache`, diffs manifests and layer tarballs byte-for-byte.

---

## What Is Done vs. What Is Out of Scope

### Done ✅

| Feature | Status |
|---------|--------|
| All 6 Docksmithfile instructions | ✅ |
| Deterministic layer tars (sorted, mtime=0) | ✅ |
| Content-addressed layer storage | ✅ |
| Manifest with digest, size, createdBy per layer | ✅ |
| Spec-compliant manifest digest rule | ✅ |
| Build cache with cascade invalidation | ✅ |
| All 6 cache invalidation triggers | ✅ |
| `created` timestamp preserved on all-HIT rebuild | ✅ |
| `--no-cache` flag | ✅ |
| Container isolation (unshare + pivot_root) | ✅ |
| Same isolation primitive for RUN and `docksmith run` | ✅ |
| `-e KEY=VALUE` env override at runtime | ✅ |
| `docksmith images` / `rmi` | ✅ |
| No network during build or run | ✅ |
| 63 unit tests green | ✅ |

### Out of Scope (per spec) ❌

- Networking inside containers
- Image registries / push / pull
- Resource limits (CPU, memory)
- Multi-stage builds
- Bind mounts / volumes
- Detached / daemon containers
- EXPOSE, VOLUME, ADD, ARG, ENTRYPOINT, SHELL instructions

---

## Architecture Notes

### Deterministic Layers

Every layer is a PAX tar with:
- Files in **lexicographically sorted** order
- `mtime=0`, `uid=0`, `gid=0`, `uname=""`, `gname=""`
- No variable PAX headers

Same content → same SHA-256 → same cache hit every time.

### Manifest Digest Rule

```
manifest_copy["digest"] = ""
raw = json.dumps(manifest_copy, sort_keys=True, separators=(",", ":"))
digest = "sha256:" + sha256(raw).hexdigest()
```

### Cache Key (spec §5.1)

```
sha256(
  prev_layer_digest  + "\n" +
  full_instruction   + "\n" +      # e.g. "COPY app.sh /app/app.sh"
  workdir            + "\n" +
  sorted_env_kv      + "\n" +      # "KEY=val\n" sorted by key
  [COPY: sorted file sha256s]
)
```

### Container Isolation Sequence

```
fork()
  child:
    unshare(CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWUTS | CLONE_NEWIPC)
    mount("", "/", MS_REC | MS_PRIVATE)
    mount(rootfs, rootfs, MS_BIND | MS_REC)
    mkdir rootfs/.pivot_old
    pivot_root(rootfs, rootfs/.pivot_old)
    chdir("/")
    umount2("/.pivot_old", MNT_DETACH)
    mount("proc", "/proc", "proc")
    execvpe(cmd, env)
  parent:
    waitpid → return exit code
```

### State Directory

```
~/.docksmith/
  images/    ← <name>_<tag>.json  (one per image)
  layers/    ← <sha256-hex>.tar   (content-addressed, immutable)
  cache/     ← index.json         {cache_key → layer_digest}
```

Override the root with `DOCKSMITH_ROOT=/path/to/dir`.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `sudo docksmith: command not found` | `sudo cp scripts/docksmith-sudo-wrapper /usr/local/bin/docksmith` |
| `image not found: alpine:3.18` | `sudo bash scripts/import-base-image.sh` |
| `unshare failed: Operation not permitted` | Run with `sudo`; needs `CAP_SYS_ADMIN` |
| `docksmith images` shows nothing after sudo build | Re-run the sudo wrapper setup — it pins `DOCKSMITH_ROOT` to your home |
| Tests fail with `import pytest` error | `pip install --user pytest --break-system-packages` |
