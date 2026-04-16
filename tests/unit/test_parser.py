"""
Unit tests for docksmith/parser.py
"""

import pytest
from docksmith import DocksmithError
from docksmith.parser import parse_text, Instruction


class TestParseFrom:
    def test_basic(self):
        instrs = parse_text("FROM alpine:3.18\nCMD [\"/bin/sh\"]")
        assert instrs[0].name == "FROM"
        assert instrs[0].args == "alpine:3.18"

    def test_from_must_be_first(self):
        with pytest.raises(DocksmithError, match="first instruction must be FROM"):
            parse_text("RUN echo hi\nFROM alpine:3.18\nCMD [\"/bin/sh\"]")

    def test_empty_file_raises(self):
        with pytest.raises(DocksmithError, match="empty"):
            parse_text("  \n  # comment\n")


class TestParseCmd:
    def test_json_array(self):
        instrs = parse_text('FROM alpine:3.18\nCMD ["/bin/sh", "-c", "echo hi"]')
        cmd_instr = instrs[-1]
        assert cmd_instr.name == "CMD"
        assert cmd_instr.args == ["/bin/sh", "-c", "echo hi"]

    def test_shell_form_rejected(self):
        with pytest.raises(DocksmithError, match="JSON array"):
            parse_text('FROM alpine:3.18\nCMD echo hi')

    def test_json_object_rejected(self):
        with pytest.raises(DocksmithError, match="JSON array"):
            parse_text('FROM alpine:3.18\nCMD {"key": "val"}')


class TestParseEnv:
    def test_key_value_form(self):
        instrs = parse_text("FROM alpine:3.18\nENV FOO=bar\nCMD [\"/bin/sh\"]")
        env_instr = instrs[1]
        assert env_instr.name == "ENV"
        assert env_instr.args == {"FOO": "bar"}

    def test_multiple_pairs(self):
        instrs = parse_text('FROM alpine:3.18\nENV A=1 B=2\nCMD ["/bin/sh"]')
        assert instrs[1].args == {"A": "1", "B": "2"}

    def test_legacy_form(self):
        instrs = parse_text('FROM alpine:3.18\nENV KEY value with spaces\nCMD ["/bin/sh"]')
        assert instrs[1].args == {"KEY": "value with spaces"}


class TestParseCopy:
    def test_single_src_dest(self):
        instrs = parse_text("FROM alpine:3.18\nCOPY foo.py /app/\nCMD [\"/bin/sh\"]")
        cp = instrs[1]
        assert cp.name == "COPY"
        assert cp.args["srcs"] == ["foo.py"]
        assert cp.args["dest"] == "/app/"

    def test_multi_src(self):
        instrs = parse_text("FROM alpine:3.18\nCOPY a.py b.py /app/\nCMD [\"/bin/sh\"]")
        assert instrs[1].args["srcs"] == ["a.py", "b.py"]

    def test_missing_dest_raises(self):
        with pytest.raises(DocksmithError, match="requires at least"):
            parse_text("FROM alpine:3.18\nCOPY\nCMD [\"/bin/sh\"]")


class TestParseRun:
    def test_shell_string(self):
        instrs = parse_text("FROM alpine:3.18\nRUN echo hello\nCMD [\"/bin/sh\"]")
        assert instrs[1].name == "RUN"
        assert instrs[1].args == "echo hello"

    def test_empty_raises(self):
        with pytest.raises(DocksmithError, match="requires a command"):
            parse_text("FROM alpine:3.18\nRUN\nCMD [\"/bin/sh\"]")


class TestUnknownInstruction:
    def test_unknown_raises(self):
        with pytest.raises(DocksmithError, match="unknown instruction 'EXPOSE'"):
            parse_text("FROM alpine:3.18\nEXPOSE 8080\nCMD [\"/bin/sh\"]")


class TestComments:
    def test_hash_comment_skipped(self):
        instrs = parse_text("FROM alpine:3.18\n# this is a comment\nCMD [\"/bin/sh\"]")
        assert len(instrs) == 2  # FROM + CMD, no comment

    def test_blank_lines_skipped(self):
        instrs = parse_text("FROM alpine:3.18\n\n\nCMD [\"/bin/sh\"]")
        assert len(instrs) == 2
