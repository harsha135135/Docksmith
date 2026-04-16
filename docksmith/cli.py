"""
cli.py — argparse subcommands: build, images, run, rmi.
Entry point: docksmith.cli:main
"""

from __future__ import annotations

import argparse
import sys

from docksmith import DocksmithError
from docksmith import store


def _cmd_build(args: argparse.Namespace) -> int:
    from docksmith.builder import build_image

    return build_image(
        context_dir=args.context,
        tag=args.tag,
        no_cache=args.no_cache,
        docksmithfile=args.file,
    )


def _cmd_images(args: argparse.Namespace) -> int:
    store.init()
    images = store.list_images()
    if not images:
        print("No images found.")
        return 0
    # Header
    fmt = "{:<30} {:<15} {:<14} {}"
    print(fmt.format("NAME", "TAG", "IMAGE ID", "CREATED"))
    for img in images:
        print(fmt.format(img["name"], img["tag"], img["id"], img["created"]))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from docksmith.runtime import run_image

    extra_env: dict[str, str] = {}
    for kv in args.env or []:
        if "=" not in kv:
            print(f"docksmith run: -e {kv!r}: must be KEY=VALUE", file=sys.stderr)
            return 1
        k, v = kv.split("=", 1)
        extra_env[k] = v

    rc = run_image(
        image_ref=args.image,
        cmd_override=args.cmd or [],
        extra_env=extra_env,
    )
    print(f"Container exited with code {rc}")
    return rc


def _cmd_rmi(args: argparse.Namespace) -> int:
    import json
    from pathlib import Path

    store.init()
    name, tag = store.parse_image_ref(args.image)
    manifest_path = store.image_path(name, tag)

    if not manifest_path.exists():
        print(f"Error: image {args.image!r} not found", file=sys.stderr)
        return 1

    from docksmith import image as image_mod
    manifest = json.loads(manifest_path.read_text())
    removed_layers = 0
    for layer_digest in image_mod.layer_digests(manifest):
        lp = store.layer_path(layer_digest)
        if lp.exists():
            lp.unlink()
            removed_layers += 1

    manifest_path.unlink()
    print(f"Removed {args.image} (manifest + {removed_layers} layer(s))")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docksmith",
        description="A simplified Docker-like build-and-runtime system",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # build
    p_build = sub.add_parser("build", help="Build an image from a Docksmithfile")
    p_build.add_argument(
        "context",
        metavar="CONTEXT",
        help="Path to the build context directory",
    )
    p_build.add_argument(
        "-t", "--tag",
        metavar="NAME:TAG",
        default=None,
        help="Name and tag for the resulting image (e.g. myapp:latest)",
    )
    p_build.add_argument(
        "-f", "--file",
        metavar="DOCKSMITHFILE",
        default=None,
        help="Path to Docksmithfile (default: CONTEXT/Docksmithfile)",
    )
    p_build.add_argument(
        "--no-cache",
        action="store_true",
        default=False,
        help="Disable layer cache",
    )
    p_build.set_defaults(func=_cmd_build)

    # images
    p_images = sub.add_parser("images", help="List built images")
    p_images.set_defaults(func=_cmd_images)

    # run
    p_run = sub.add_parser("run", help="Run a container from an image")
    p_run.add_argument("image", metavar="IMAGE", help="Image name[:tag] to run")
    p_run.add_argument(
        "-e", "--env",
        metavar="KEY=VALUE",
        action="append",
        help="Set environment variable (repeatable; overrides image ENV)",
    )
    p_run.add_argument(
        "cmd",
        nargs="*",
        metavar="CMD",
        help="Command to run (overrides image CMD)",
    )
    p_run.set_defaults(func=_cmd_run)

    # rmi
    p_rmi = sub.add_parser("rmi", help="Remove an image and its layers")
    p_rmi.add_argument("image", metavar="IMAGE", help="Image name[:tag] to remove")
    p_rmi.set_defaults(func=_cmd_rmi)

    return parser


def main(argv: list[str] | None = None) -> int:
    store.init()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        rc = args.func(args)
        return rc if rc is not None else 0
    except DocksmithError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
