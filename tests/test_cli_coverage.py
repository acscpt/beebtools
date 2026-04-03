# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests targeting uncovered CLI paths: _parseBootOption, cmdSearch regex
error, cmdExtract single-file, cmdAdd error paths, cmdBuild error,
main() dispatch, and __main__.py entry point.
"""

import argparse
import contextlib
import io
import os
import sys
from argparse import Namespace
from unittest.mock import patch

import pytest

from beebtools.boot import BootOption
from beebtools.cli import (
    _parseBootOption,
    _colour,
    cmdSearch,
    cmdExtract,
    cmdAdd,
    cmdBuild,
    main,
)
from beebtools.dfs import createDiscImage
from beebtools.entry import DiscFile
from beebtools.inf import formatInf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SECTOR_SIZE = 256

# Token constants for building BASIC programs.
TOK_PRINT = 0xF1
TOK_REM = 0xF4


def _makeProgram(*lines: tuple[int, bytes]) -> bytes:
    """Build a minimal tokenized BBC BASIC program from (linenum, content) pairs."""
    buf = bytearray()
    for num, content in lines:
        hi = (num >> 8) & 0xFF
        lo = num & 0xFF
        length = 4 + len(content)
        buf.append(0x0D)
        buf.append(hi)
        buf.append(lo)
        buf.append(length)
        buf.extend(content)
    buf.extend(b"\x0D\xFF")
    return bytes(buf)


def _makeSsdImage(filename: str, file_data: bytes, directory: str = "$") -> bytes:
    """Build a minimal .ssd disc image with one file."""
    image = bytearray(80 * 10 * SECTOR_SIZE)

    # Sector 0: catalogue names.
    name_bytes = filename.encode("ascii").ljust(7)[:7]
    image[8:15] = name_bytes
    image[15] = ord(directory) & 0x7F

    # Sector 1: catalogue metadata for one file.
    off = SECTOR_SIZE
    image[off + 5] = 1 * 8
    length_lo = len(file_data) & 0xFFFF
    image[off + 8] = 0x00
    image[off + 9] = 0x00
    image[off + 10] = 0x00
    image[off + 11] = 0x00
    image[off + 12] = length_lo & 0xFF
    image[off + 13] = (length_lo >> 8) & 0xFF
    image[off + 14] = 0x00
    image[off + 15] = 2

    # File data at sector 2.
    start = 2 * SECTOR_SIZE
    image[start:start + len(file_data)] = file_data

    return bytes(image)


def _makeSsdImageBasic(filename: str, prog: bytes, directory: str = "$") -> bytes:
    """Build a minimal .ssd disc image with one BASIC file (load=0x1900, exec=0x8023)."""
    image = bytearray(80 * 10 * SECTOR_SIZE)

    name_bytes = filename.encode("ascii").ljust(7)[:7]
    image[8:15] = name_bytes
    image[15] = ord(directory) & 0x7F

    off = SECTOR_SIZE
    image[off + 5] = 1 * 8
    length_lo = len(prog) & 0xFFFF
    # load = 0x1900
    image[off + 8] = 0x00
    image[off + 9] = 0x19
    # exec = 0x8023
    image[off + 10] = 0x23
    image[off + 11] = 0x80
    image[off + 12] = length_lo & 0xFF
    image[off + 13] = (length_lo >> 8) & 0xFF
    image[off + 14] = 0x00
    image[off + 15] = 2

    start = 2 * SECTOR_SIZE
    image[start:start + len(prog)] = prog

    return bytes(image)


# ---------------------------------------------------------------------------
# _parseBootOption
# ---------------------------------------------------------------------------

class TestParseBootOption:
    """Tests for the argparse type wrapper around BootOption.parse()."""

    def testValidName(self) -> None:
        """A valid name string returns the corresponding BootOption."""
        assert _parseBootOption("RUN") == BootOption.RUN

    def testValidNumber(self) -> None:
        """A valid numeric string returns the corresponding BootOption."""
        assert _parseBootOption("2") == BootOption.RUN

    def testInvalidRaisesArgumentTypeError(self) -> None:
        """Invalid input raises argparse.ArgumentTypeError, not ValueError."""
        with pytest.raises(argparse.ArgumentTypeError):
            _parseBootOption("BADVALUE")


# ---------------------------------------------------------------------------
# _colour
# ---------------------------------------------------------------------------

class TestColour:
    """Tests for ANSI colour wrapping."""

    def testColourEnabled(self) -> None:
        """When enabled, text is wrapped in ANSI codes."""
        result = _colour("hello", "\x1b[31m", True)
        assert result == "\x1b[31mhello\x1b[0m"

    def testColourDisabled(self) -> None:
        """When disabled, text is returned unchanged."""
        result = _colour("hello", "\x1b[31m", False)
        assert result == "hello"


# ---------------------------------------------------------------------------
# cmdSearch - regex error path
# ---------------------------------------------------------------------------

class TestCmdSearchRegexError:
    """Tests for the cmdSearch regex error handling path."""

    def testInvalidRegexExitsWithError(self, tmp_path) -> None:
        """An invalid regex pattern should print an error and exit."""
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"X"'))
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("PROG", prog))

        args = Namespace(
            image=img, pattern="[unclosed",
            filename=None, ignore_case=False,
            pretty=False, regex=True,
        )

        err = io.StringIO()
        with pytest.raises(SystemExit) as exc_info:
            with contextlib.redirect_stderr(err):
                cmdSearch(args)

        assert exc_info.value.code == 1
        assert "Invalid regex" in err.getvalue()


# ---------------------------------------------------------------------------
# cmdExtract - single-file paths
# ---------------------------------------------------------------------------

class TestCmdExtractSingleFile:
    """Tests for the single-file extraction path in cmdExtract."""

    def testExtractBasicToStdout(self, tmp_path) -> None:
        """Extracting a BASIC file without -o writes LIST-style text to stdout."""
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"HELLO"'))
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImageBasic("PROG", prog))

        args = Namespace(
            image=img, filename="$.PROG", all=False,
            output=None, dir=None, pretty=False, inf=False,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdExtract(args)

        output = buf.getvalue()
        assert "10" in output
        assert "PRINT" in output
        assert "HELLO" in output

    def testExtractBasicToFile(self, tmp_path) -> None:
        """Extracting a BASIC file with -o writes to the specified file."""
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"WORLD"'))
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImageBasic("PROG", prog))

        out_file = str(tmp_path / "output.bas")
        args = Namespace(
            image=img, filename="$.PROG", all=False,
            output=out_file, dir=None, pretty=False, inf=False,
        )

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cmdExtract(args)

        assert os.path.isfile(out_file)
        content = open(out_file).read()
        assert "PRINT" in content
        assert "Extracted to" in err.getvalue()

    def testExtractBasicWithPretty(self, tmp_path) -> None:
        """Extracting a BASIC file with --pretty applies operator spacing."""
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"A"'))
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImageBasic("PROG", prog))

        args = Namespace(
            image=img, filename="$.PROG", all=False,
            output=None, dir=None, pretty=True, inf=False,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdExtract(args)

        # Output should contain the detokenized content.
        assert "PRINT" in buf.getvalue()

    def testExtractBinaryToStdout(self, tmp_path) -> None:
        """Extracting a binary file without -o writes raw bytes to stdout."""
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("DATA", b"\xDE\xAD\xBE\xEF"))

        args = Namespace(
            image=img, filename="$.DATA", all=False,
            output=None, dir=None, pretty=False, inf=False,
        )

        # Capture binary stdout.
        buf = io.BytesIO()
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.buffer = buf
            cmdExtract(args)

        assert buf.getvalue() == b"\xDE\xAD\xBE\xEF"

    def testExtractBinaryToFile(self, tmp_path) -> None:
        """Extracting a binary file with -o writes to the specified path."""
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("DATA", b"\xDE\xAD\xBE\xEF"))

        out_file = str(tmp_path / "output.bin")
        args = Namespace(
            image=img, filename="$.DATA", all=False,
            output=out_file, dir=None, pretty=False, inf=False,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdExtract(args)

        assert os.path.isfile(out_file)
        with open(out_file, "rb") as f:
            assert f.read() == b"\xDE\xAD\xBE\xEF"

    def testExtractFileNotFound(self, tmp_path) -> None:
        """Extracting a non-existent file exits with an error."""
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("DATA", b"\x00"))

        args = Namespace(
            image=img, filename="$.GHOST", all=False,
            output=None, dir=None, pretty=False, inf=False,
        )

        err = io.StringIO()
        with pytest.raises(SystemExit) as exc_info:
            with contextlib.redirect_stderr(err):
                cmdExtract(args)

        assert exc_info.value.code == 1
        assert "not found" in err.getvalue().lower()

    def testExtractNoFilenameNoAll(self, tmp_path) -> None:
        """Calling extract without filename or --all exits with error."""
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("DATA", b"\x00"))

        args = Namespace(
            image=img, filename=None, all=False,
            output=None, dir=None, pretty=False, inf=False,
        )

        err = io.StringIO()
        with pytest.raises(SystemExit) as exc_info:
            with contextlib.redirect_stderr(err):
                cmdExtract(args)

        assert exc_info.value.code == 1
        assert "filename required" in err.getvalue().lower()

    def testExtractAllWithOutputConflict(self, tmp_path) -> None:
        """Using -o with --all exits with error."""
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("DATA", b"\x00"))

        args = Namespace(
            image=img, filename=None, all=True,
            output="foo.bin", dir=None, pretty=False, inf=False,
        )

        err = io.StringIO()
        with pytest.raises(SystemExit) as exc_info:
            with contextlib.redirect_stderr(err):
                cmdExtract(args)

        assert exc_info.value.code == 1
        assert "-o/--output" in err.getvalue()

    def testExtractByBareName(self, tmp_path) -> None:
        """Extracting by bare name (no directory prefix) finds the file."""
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("DATA", b"\xAA\xBB"))

        args = Namespace(
            image=img, filename="DATA", all=False,
            output=None, dir=None, pretty=False, inf=False,
        )

        buf = io.BytesIO()
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.buffer = buf
            cmdExtract(args)

        assert buf.getvalue() == b"\xAA\xBB"


# ---------------------------------------------------------------------------
# cmdAdd - error paths
# ---------------------------------------------------------------------------

class TestCmdAddErrors:
    """Tests for cmdAdd error paths not covered by test_cli_commands.py."""

    def _createBlankSsd(self, tmp_path) -> str:
        """Create a blank 80-track SSD and return its path."""
        out = str(tmp_path / "disc.ssd")
        image = createDiscImage(tracks=80, title="TEST")
        with open(out, "wb") as f:
            f.write(image.serialize())
        return out

    def testMissingNameExits(self, tmp_path) -> None:
        """Calling add without --name (and without --inf) exits with error."""
        img = self._createBlankSsd(tmp_path)
        data_file = str(tmp_path / "file.bin")
        with open(data_file, "wb") as f:
            f.write(b"\x00")

        args = Namespace(
            image=img, file=data_file, name=None,
            load=None, exec_addr=None, locked=False,
            inf=False, side=0, basic=False,
        )

        err = io.StringIO()
        with pytest.raises(SystemExit) as exc_info:
            with contextlib.redirect_stderr(err):
                cmdAdd(args)

        assert exc_info.value.code == 1
        assert "--name" in err.getvalue()

    def testMissingInfSidecarExits(self, tmp_path) -> None:
        """Using --inf when no .inf sidecar exists exits with error."""
        img = self._createBlankSsd(tmp_path)
        data_file = str(tmp_path / "orphan.bin")
        with open(data_file, "wb") as f:
            f.write(b"\x00")

        # No .inf file created.
        args = Namespace(
            image=img, file=data_file, name=None,
            load=None, exec_addr=None, locked=False,
            inf=True, side=0, basic=False,
        )

        err = io.StringIO()
        with pytest.raises(SystemExit) as exc_info:
            with contextlib.redirect_stderr(err):
                cmdAdd(args)

        assert exc_info.value.code == 1
        assert ".inf sidecar not found" in err.getvalue()

    def testBasicTokenizesPlainText(self, tmp_path) -> None:
        """Using --basic with a plain text file retokenizes it."""
        img = self._createBlankSsd(tmp_path)
        data_file = str(tmp_path / "prog.bas")
        with open(data_file, "w") as f:
            f.write("   10 PRINT\"HELLO\"\n")

        args = Namespace(
            image=img, file=data_file, name="T.MYPROG",
            load=None, exec_addr=None, locked=False,
            inf=False, side=0, basic=True,
        )

        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cmdAdd(args)

        # The retokenization should be reported.
        assert "Tokenized" in err.getvalue()
        # The file should have been added.
        assert "Added" in out.getvalue()

    def testAddDiscErrorExits(self, tmp_path) -> None:
        """When addFile raises DiscError, cmdAdd exits with error."""
        img = self._createBlankSsd(tmp_path)

        # Fill the disc with 31 files to trigger catalogue-full error.
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        for i in range(31):
            side.addFile(DiscFile(f"$.F{i:02d}", b"\x00"))
        with open(img, "wb") as f:
            f.write(image.serialize())

        data_file = str(tmp_path / "overflow.bin")
        with open(data_file, "wb") as f:
            f.write(b"\x00")

        args = Namespace(
            image=img, file=data_file, name="$.TOOMANY",
            load=None, exec_addr=None, locked=False,
            inf=False, side=0, basic=False,
        )

        err = io.StringIO()
        with pytest.raises(SystemExit) as exc_info:
            with contextlib.redirect_stderr(err):
                cmdAdd(args)

        assert exc_info.value.code == 1
        assert "Error" in err.getvalue()


# ---------------------------------------------------------------------------
# cmdBuild - DiscError path
# ---------------------------------------------------------------------------

class TestCmdBuildError:
    """Tests for cmdBuild error handling."""

    def testBuildDiscErrorExits(self, tmp_path) -> None:
        """When buildImage raises DiscError, cmdBuild exits with error."""
        # Create a source dir with too many files for a DFS catalogue.
        src = str(tmp_path / "src" / "$")
        os.makedirs(src)

        for i in range(32):
            fname = f"F{i:02d}.bin"
            with open(os.path.join(src, fname), "wb") as f:
                f.write(b"\x00" * 10)
            with open(os.path.join(src, fname + ".inf"), "w") as f:
                f.write(formatInf("$", f"F{i:02d}", 0, 0, 10) + "\n")

        out = str(tmp_path / "overflow.ssd")
        args = Namespace(
            dir=str(tmp_path / "src"),
            output=out, tracks=80, title="", boot=BootOption.OFF,
        )

        err = io.StringIO()
        with pytest.raises(SystemExit) as exc_info:
            with contextlib.redirect_stderr(err):
                cmdBuild(args)

        assert exc_info.value.code == 1
        assert "Error" in err.getvalue()


# ---------------------------------------------------------------------------
# main() entry point dispatch
# ---------------------------------------------------------------------------

class TestMainDispatch:
    """Tests for the main() CLI entry point and argparse wiring."""

    def testNoCommandPrintsHelp(self) -> None:
        """Calling main() with no arguments prints help."""
        with patch("sys.argv", ["beebtools"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main()

        output = buf.getvalue()
        assert "usage" in output.lower() or "BBC Micro" in output

    def testCatCommand(self, tmp_path) -> None:
        """The 'cat' subcommand dispatches to cmdCat."""
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("MYFILE", b"\x00"))

        with patch("sys.argv", ["beebtools", "cat", img]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main()

        assert "MYFILE" in buf.getvalue()

    def testSearchCommand(self, tmp_path) -> None:
        """The 'search' subcommand dispatches to cmdSearch."""
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"FOUND"'))
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImageBasic("PROG", prog))

        with patch("sys.argv", ["beebtools", "search", img, "FOUND"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main()

        assert "FOUND" in buf.getvalue()

    def testExtractCommand(self, tmp_path) -> None:
        """The 'extract' subcommand dispatches to cmdExtract."""
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"GO"'))
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImageBasic("PROG", prog))

        with patch("sys.argv", ["beebtools", "extract", img, "$.PROG"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main()

        output = buf.getvalue()
        assert "10" in output
        assert "PRINT" in output

    def testCreateCommand(self, tmp_path) -> None:
        """The 'create' subcommand dispatches to cmdCreate."""
        out = str(tmp_path / "new.ssd")

        with patch("sys.argv", ["beebtools", "create", out]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main()

        assert os.path.isfile(out)
        assert "Created" in buf.getvalue()

    def testDeleteCommand(self, tmp_path) -> None:
        """The 'delete' subcommand dispatches to cmdDelete."""
        image = createDiscImage(tracks=80)
        image.sides[0].addFile(DiscFile("$.VICTIM", b"\x00"))
        img = str(tmp_path / "disc.ssd")
        with open(img, "wb") as f:
            f.write(image.serialize())

        with patch("sys.argv", ["beebtools", "delete", img, "$.VICTIM"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main()

        assert "Deleted" in buf.getvalue()

    def testAddCommand(self, tmp_path) -> None:
        """The 'add' subcommand dispatches to cmdAdd."""
        img = str(tmp_path / "disc.ssd")
        image = createDiscImage(tracks=80)
        with open(img, "wb") as f:
            f.write(image.serialize())

        data_file = str(tmp_path / "prog.bin")
        with open(data_file, "wb") as f:
            f.write(b"\x01\x02")

        with patch("sys.argv", ["beebtools", "add", img, data_file,
                                 "-n", "$.PROG"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main()

        assert "Added" in buf.getvalue()

    def testBuildCommand(self, tmp_path) -> None:
        """The 'build' subcommand dispatches to cmdBuild."""
        src = str(tmp_path / "src" / "$")
        os.makedirs(src)

        with open(os.path.join(src, "BOOT.bin"), "wb") as f:
            f.write(b"*RUN GAME\r")
        with open(os.path.join(src, "BOOT.bin.inf"), "w") as f:
            f.write(formatInf("$", "BOOT", 0, 0, 10) + "\n")

        out = str(tmp_path / "built.ssd")

        with patch("sys.argv", ["beebtools", "build",
                                 str(tmp_path / "src"), out]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main()

        assert os.path.isfile(out)
        assert "Built" in buf.getvalue()

    def testErrorHandlingWithoutDebug(self, tmp_path) -> None:
        """Unhandled errors are caught by main() and printed to stderr."""
        with patch("sys.argv", ["beebtools", "cat", "/nonexistent/file.ssd"]):
            err = io.StringIO()
            with pytest.raises(SystemExit) as exc_info:
                with contextlib.redirect_stderr(err):
                    main()

            assert exc_info.value.code == 1
            assert "Error" in err.getvalue()

    def testBootOptionViaArgparse(self, tmp_path) -> None:
        """The --boot flag in create uses _parseBootOption correctly."""
        out = str(tmp_path / "boot.ssd")

        with patch("sys.argv", ["beebtools", "create", out, "--boot", "RUN"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main()

        assert os.path.isfile(out)

    def testInvalidBootOptionViaArgparse(self, tmp_path) -> None:
        """An invalid --boot value produces an argparse error."""
        out = str(tmp_path / "bad.ssd")

        with patch("sys.argv", ["beebtools", "create", out, "--boot", "BADOPT"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 2  # argparse exits with 2


# ---------------------------------------------------------------------------
# __main__.py
# ---------------------------------------------------------------------------

class TestMainModule:
    """Test the __main__.py entry point."""

    def testMainModuleCallsMain(self) -> None:
        """Importing __main__ invokes main(), which with no args prints help."""
        with patch("sys.argv", ["beebtools"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                # Run __main__.py as a script.
                import importlib
                import beebtools.__main__ as mm
                importlib.reload(mm)

        output = buf.getvalue()
        assert "usage" in output.lower() or "BBC Micro" in output
