"""Unit tests for CLI argument parsing behavior."""

from docksmith.cli import (
    _extract_leading_env_flags,
    _normalize_cmd_tokens,
    build_parser,
)


def test_run_parser_accepts_dash_options_in_command_tail() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "myapp:latest",
            "/bin/sh",
            "-c",
            "echo secret > /tmp/leak.txt",
        ]
    )

    assert args.image == "myapp:latest"
    assert args.cmd == ["/bin/sh", "-c", "echo secret > /tmp/leak.txt"]


def test_run_parser_handles_env_then_command_tail() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["run", "myapp:latest", "-e", "GREETING=Hi", "/bin/sh", "-c", "echo ok"]
    )

    # With REMAINDER parsing, flags after image are captured in cmd tail.
    assert args.env is None
    env, cmd = _extract_leading_env_flags(args.cmd)
    assert env == {"GREETING": "Hi"}
    assert cmd == ["/bin/sh", "-c", "echo ok"]


def test_normalize_cmd_tokens_strips_optional_separator() -> None:
    assert _normalize_cmd_tokens(["--", "/bin/sh", "-c", "echo ok"]) == [
        "/bin/sh",
        "-c",
        "echo ok",
    ]


def test_extract_leading_env_flags_supports_long_form() -> None:
    env, cmd = _extract_leading_env_flags(["--env", "A=1", "run.sh"])
    assert env == {"A": "1"}
    assert cmd == ["run.sh"]
