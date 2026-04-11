# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for searchDisc() and the cmdSearch CLI wrapper."""

import sys
import io
import contextlib
from argparse import Namespace

import pytest

from beebtools import search
from beebtools.cli import cmdSearch


# ---------------------------------------------------------------------------
# Helpers: build tokenized BASIC programs and in-memory disc images
# ---------------------------------------------------------------------------

SECTOR_SIZE = 256

# Common tokens
TOK_PRINT  = 0xF1   # PRINT
TOK_GOTO   = 0xE5   # GOTO
TOK_REM    = 0xF4   # REM


def _makeLine(linenum: int, content: bytes) -> bytes:
    """Build one tokenized BASIC line record."""
    hi = (linenum >> 8) & 0xFF
    lo = linenum & 0xFF
    linelen = 3 + 1 + len(content)
    return bytes([0x0D, hi, lo, linelen]) + content


def _makeProgram(*lines) -> bytes:
    """Build a tokenized BASIC program from (linenum, bytes) pairs."""
    data = bytearray()
    for linenum, content in lines:
        data += _makeLine(linenum, bytes(content))
    data += b"\x0D\xFF"
    return bytes(data)


def _makeSector0(filename: str, directory: str = "$") -> bytes:
    buf = bytearray(SECTOR_SIZE)
    buf[0:8] = b"TESTDISC"
    buf[8:15] = filename.encode("ascii").ljust(7)[:7]
    buf[15] = ord(directory) & 0x7F
    return bytes(buf)


def _makeSector1(file_data_len: int, exec_addr: int = 0x00008023, start_sector: int = 2) -> bytes:
    buf = bytearray(SECTOR_SIZE)
    buf[5] = 1 * 8  # one file
    # disc_size: 800 sectors (80-track SSD) so DFSSide._reconcileDiscSize
    # does not trigger a UserWarning on this synthetic image.
    buf[6] = (800 >> 8) & 0x03
    buf[7] = 800 & 0xFF
    length_lo = file_data_len & 0xFFFF
    buf[8]  = 0x00          # load lo
    buf[9]  = 0x0E          # load hi (0x0E00 - typical BASIC load)
    buf[10] = exec_addr & 0xFF
    buf[11] = (exec_addr >> 8) & 0xFF
    buf[12] = length_lo & 0xFF
    buf[13] = (length_lo >> 8) & 0xFF
    buf[14] = 0x00
    buf[15] = start_sector & 0xFF
    return bytes(buf)


def _makeSsdImage(filename: str, file_data: bytes, directory: str = "$",
                  exec_addr: int = 0x00008023) -> bytes:
    """Build a minimal .ssd image with one BASIC file."""
    image = bytearray(80 * 10 * SECTOR_SIZE)
    image[0:SECTOR_SIZE]           = _makeSector0(filename, directory)
    image[SECTOR_SIZE:2*SECTOR_SIZE] = _makeSector1(len(file_data), exec_addr)
    start = 2 * SECTOR_SIZE
    image[start:start + len(file_data)] = file_data
    return bytes(image)


# ---------------------------------------------------------------------------
# search() unit tests
# ---------------------------------------------------------------------------

class TestSearch:

    def testFindsMatchInBasicFile(self, tmp_path):
        """A search pattern present in a BASIC program's detokenized output should be returned as a result with file and line info."""
        # Program has one PRINT line containing "HELLO".
        prog = _makeProgram(
            (10, bytes([TOK_PRINT]) + b'"HELLO"'),
            (20, bytes([TOK_GOTO]) + b"10"),
        )
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("PROG", prog))

        results = search(img, "HELLO")
        assert len(results) == 1
        assert results[0]["filename"] == "$.PROG"
        assert results[0]["line_number"] == 10
        assert "HELLO" in results[0]["line"]

    def testNoMatchReturnsEmpty(self, tmp_path):
        """Searching for a string absent from all disc files should return an empty results list."""
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"HELLO"'))
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("PROG", prog))

        results = search(img, "GOODBYE")
        assert results == []

    def testIgnoreCaseFlagOff(self, tmp_path):
        """Without ignore_case, a search for uppercase 'HELLO' should not match a lowercase 'hello' in the file."""
        # Without ignore_case, "hello" should not match "HELLO".
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"HELLO"'))
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("PROG", prog))

        results = search(img, "hello", ignore_case=False)
        assert results == []

    def testIgnoreCaseFlagOn(self, tmp_path):
        """With ignore_case enabled, the same pattern should match regardless of the case of the file content."""
        # With ignore_case, "hello" should match "HELLO".
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"HELLO"'))
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("PROG", prog))

        results = search(img, "hello", ignore_case=True)
        assert len(results) == 1

    def testMultipleLinesMatched(self, tmp_path):
        """When the search pattern appears on more than one BASIC line, every matching line should appear in the results."""
        # Two lines both contain the search term.
        prog = _makeProgram(
            (10, bytes([TOK_PRINT]) + b'"SCORE"'),
            (20, bytes([TOK_PRINT]) + b'"SCORE=0"'),
            (30, bytes([TOK_GOTO]) + b"10"),
        )
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("PROG", prog))

        results = search(img, "SCORE")
        assert len(results) == 2
        assert results[0]["line_number"] == 10
        assert results[1]["line_number"] == 20

    def testFilenameFilterFullName(self, tmp_path):
        """Passing a full DFS filename (dir.name) as a filter should restrict results to that file only."""
        # When filename given as full DFS name, only that file is searched.
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"HIT"'))
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("PROG", prog))

        # Correct full name -> match found.
        assert len(search(img, "HIT", filename="$.PROG")) == 1
        # Wrong name -> no results.
        assert search(img, "HIT", filename="$.OTHER") == []

    def testFilenameFilterBareName(self, tmp_path):
        """Passing just the bare name without directory prefix should match files in any directory."""
        # Bare name without directory prefix also scopes the search.
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"HIT"'))
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("PROG", prog))

        assert len(search(img, "HIT", filename="PROG")) == 1

    def testNonBasicFileSkipped(self, tmp_path):
        """A file that is not tokenized BASIC should be silently skipped rather than raising an error."""
        # A file with binary data (non-BASIC exec address) is not searched.
        binary_data = b"\xDE\xAD\xBE\xEF" * 16
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            # exec_addr 0x0000 -> not a BASIC file
            f.write(_makeSsdImage("BIN", binary_data, exec_addr=0x0000))

        results = search(img, "\xDE")
        assert results == []

    def testResultKeysPresent(self, tmp_path):
        """Each result dict should contain 'file', 'line_number', and 'text' keys with correct types."""
        # Each result dict must contain all required keys.
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"KEY"'))
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("PROG", prog))

        results = search(img, "KEY")
        assert len(results) == 1
        r = results[0]
        assert "side"        in r
        assert "filename"    in r
        assert "line_number" in r
        assert "line"        in r
        assert r["side"] == 0


# ---------------------------------------------------------------------------
# cmdSearch CLI wrapper tests
# ---------------------------------------------------------------------------

class TestCmdSearch:

    def _run(self, tmp_path, prog: bytes, pattern: str, **kwargs) -> str:
        """Write an image, run cmdSearch, return captured stdout."""
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("PROG", prog))
        args = Namespace(
            image=img,
            pattern=pattern,
            filename=kwargs.get("filename", None),
            ignore_case=kwargs.get("ignore_case", False),
            pretty=kwargs.get("pretty", False),
            regex=kwargs.get("regex", False),
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdSearch(args)
        return buf.getvalue()

    def testMatchPrinted(self, tmp_path):
        """A matching result should be written to stdout in 'filename: line_number: content' format."""
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"WORLD"'))
        output = self._run(tmp_path, prog, "WORLD")
        assert "WORLD" in output
        assert "$.PROG" in output

    def testNoMatchPrintsNothing(self, tmp_path):
        """When no lines match the search pattern, cmdSearch should produce no stdout output."""
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"WORLD"'))
        output = self._run(tmp_path, prog, "MISSING")
        assert output == ""

    def testLineNumberInOutput(self, tmp_path):
        """The matched BASIC line number should appear in the printed output alongside the line content."""
        prog = _makeProgram((42, bytes([TOK_PRINT]) + b'"X"'))
        output = self._run(tmp_path, prog, '"X"')
        assert "42" in output


# ---------------------------------------------------------------------------
# search() regex tests
# ---------------------------------------------------------------------------

class TestSearchRegex:

    def _img(self, tmp_path, prog: bytes) -> str:
        img = str(tmp_path / "test.ssd")
        with open(img, "wb") as f:
            f.write(_makeSsdImage("PROG", prog))
        return img

    def testRegexMatchesPattern(self, tmp_path):
        """In regex mode, a valid pattern should match any line in a BASIC file where the expression finds a hit."""
        # GOTO followed by digits - regex only.
        prog = _makeProgram(
            (10, bytes([TOK_GOTO]) + b"100"),
            (20, bytes([TOK_PRINT]) + b'"HELLO"'),
        )
        img = self._img(tmp_path, prog)
        results = search(img, r"GOTO\s*\d+", use_regex=True)
        assert len(results) == 1
        assert results[0]["line_number"] == 10

    def testLiteralDoesNotInterpretRegexChars(self, tmp_path):
        """In literal mode, regex metacharacters like '.' should match only themselves and not act as wildcards."""
        # Without use_regex, "GOTO\d+" is a literal string, not a pattern.
        prog = _makeProgram((10, bytes([TOK_GOTO]) + b"100"))
        img = self._img(tmp_path, prog)
        results = search(img, r"GOTO\d+", use_regex=False)
        assert results == []

    def testInvalidRegexRaisesReError(self, tmp_path):
        """Passing a syntactically invalid pattern in regex mode should raise re.error."""
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"X"'))
        img = self._img(tmp_path, prog)
        import re
        with pytest.raises(re.error):
            search(img, "[unclosed", use_regex=True)

    def testRegexWithIgnoreCase(self, tmp_path):
        """A regex search combined with ignore_case should match file content regardless of letter case."""
        prog = _makeProgram((10, bytes([TOK_PRINT]) + b'"SCORE"'))
        img = self._img(tmp_path, prog)
        results = search(img, "score", use_regex=True, ignore_case=True)
        assert len(results) == 1
