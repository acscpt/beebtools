# SPDX-FileCopyrightText: 2026 beebtools contributors
# SPDX-License-Identifier: MIT

"""Unit tests for the extractAll bulk-extraction logic.

Tests use a minimal in-memory fake disc image so no real disc files are needed.
The helpers below build a valid single-sided or double-sided DFS image in memory
that contains one small binary file per side, allowing the --sides behaviour to
be verified without touching the filesystem beyond the tmp_path fixture.
"""

import os
import struct
import pytest

from argparse import Namespace

from beebtools.disc import sanitizeDfsDir, sanitizeDfsFilename, resolveOutputPath, extractAll
from beebtools.cli import cmdCat


# ---------------------------------------------------------------------------
# Helpers to build minimal in-memory DFS disc images
# ---------------------------------------------------------------------------

SECTOR_SIZE = 256


def _makeSector0(filename: str, directory: str = "$") -> bytes:
    """Build DFS catalogue sector 0 with one file entry."""
    buf = bytearray(SECTOR_SIZE)

    # Disc title (first 8 bytes of sector 0)
    title = b"TESTDISC"
    buf[0:8] = title

    # File entry at offset 8: 7 bytes name + 1 byte directory
    name_bytes = filename.encode("ascii").ljust(7)[:7]
    buf[8:15] = name_bytes
    buf[15] = ord(directory) & 0x7F  # directory byte, no lock bit

    return bytes(buf)


def _makeSector1(file_data_len: int, start_sector: int = 2) -> bytes:
    """Build DFS catalogue sector 1 for one file entry."""
    buf = bytearray(SECTOR_SIZE)

    # Disc title continuation (bytes 0-3, leave as zero)
    # Sector count (bytes 4-5): not used in our tests
    # File count: sector1[5] = number_of_entries * 8
    buf[5] = 1 * 8  # one file

    # File entry at offset 8 in sector 1:
    # bytes 0-1: load_lo, bytes 2-3: exec_lo, bytes 4-5: length_lo
    # byte 6: extra bits, byte 7: start_sector
    length_lo = file_data_len & 0xFFFF
    buf[8] = 0x00          # load lo
    buf[9] = 0x00          # load hi
    buf[10] = 0x00         # exec lo
    buf[11] = 0x00         # exec hi
    buf[12] = length_lo & 0xFF
    buf[13] = (length_lo >> 8) & 0xFF
    buf[14] = 0x00         # extra bits (high bits all zero)
    buf[15] = start_sector & 0xFF

    return bytes(buf)


def _makeSsdImage(filename: str, file_data: bytes, directory: str = "$") -> bytes:
    """Build a minimal .ssd disc image with one file."""
    image = bytearray(80 * 10 * SECTOR_SIZE)  # 80 tracks, 10 sectors

    sec0 = _makeSector0(filename, directory)
    sec1 = _makeSector1(len(file_data), start_sector=2)

    image[0:SECTOR_SIZE] = sec0
    image[SECTOR_SIZE:2 * SECTOR_SIZE] = sec1

    # File data starts at sector 2
    start = 2 * SECTOR_SIZE
    image[start:start + len(file_data)] = file_data

    return bytes(image)


def _makeDsdImage(
    filename0: str,
    data0: bytes,
    filename1: str,
    data1: bytes,
) -> bytes:
    """Build a minimal .dsd (double-sided interleaved) image with one file per side.

    In .dsd layout, sectors are interleaved:
    track 0 side 0 sectors 0-9, then track 0 side 1 sectors 0-9, etc.
    Each track occupies 20 sectors (10 per side).
    """
    # 80 tracks * 20 sectors/track * 256 bytes/sector
    image = bytearray(80 * 20 * SECTOR_SIZE)

    def sectorOffset(track: int, side: int, sector: int) -> int:
        return (track * 20 + side * 10 + sector) * SECTOR_SIZE

    # Side 0 catalogue
    s0_sec0 = _makeSector0(filename0)
    s0_sec1 = _makeSector1(len(data0), start_sector=2)
    image[sectorOffset(0, 0, 0):sectorOffset(0, 0, 0) + SECTOR_SIZE] = s0_sec0
    image[sectorOffset(0, 0, 1):sectorOffset(0, 0, 1) + SECTOR_SIZE] = s0_sec1

    # Side 0 file data at logical sector 2 -> track 0, side 0, sector 2
    off = sectorOffset(0, 0, 2)
    image[off:off + len(data0)] = data0

    # Side 1 catalogue
    s1_sec0 = _makeSector0(filename1)
    s1_sec1 = _makeSector1(len(data1), start_sector=2)
    image[sectorOffset(0, 1, 0):sectorOffset(0, 1, 0) + SECTOR_SIZE] = s1_sec0
    image[sectorOffset(0, 1, 1):sectorOffset(0, 1, 1) + SECTOR_SIZE] = s1_sec1

    # Side 1 file data at logical sector 2 -> track 0, side 1, sector 2
    off = sectorOffset(0, 1, 2)
    image[off:off + len(data1)] = data1

    return bytes(image)


# ---------------------------------------------------------------------------
# sanitizeDfsName unit tests
# ---------------------------------------------------------------------------

class TestSanitizeDfsDir:

    def testNormalDirUnchanged(self):
        assert sanitizeDfsDir("T") == "T"

    def testDollarDirUnchanged(self):
        assert sanitizeDfsDir("$") == "$"

    def testExclamationDirUnchanged(self):
        assert sanitizeDfsDir("!") == "!"

    def testIllegalCharEncoded(self):
        assert sanitizeDfsDir("/") == "_x2F_"


class TestSanitizeDfsFilename:

    def testNormalNameUnchanged(self):
        assert sanitizeDfsFilename("MYPROG") == "MYPROG"

    def testForwardSlashEncoded(self):
        assert sanitizeDfsFilename("A/B") == "A_x2F_B"

    def testBackslashEncoded(self):
        assert sanitizeDfsFilename("A\\B") == "A_x5C_B"

    def testSlashAndBackslashDistinct(self):
        slash = sanitizeDfsFilename("A/B")
        backslash = sanitizeDfsFilename("A\\B")
        assert slash != backslash

    def testColonEncoded(self):
        assert sanitizeDfsFilename("A:B") == "A_x3A_B"

    def testControlCharDropped(self):
        assert sanitizeDfsFilename("A\x01B") == "AB"

    def testAllWindowsIllegalCharsEncoded(self):
        illegal = '\\/:*?"<>|'
        for ch in illegal:
            result = sanitizeDfsFilename(f"A{ch}B")
            assert ch not in result, f"Illegal char {repr(ch)} appeared unencoded in {repr(result)}"

    def testWindowsIllegalCharsAllDistinct(self):
        illegal = '\\/:*?"<>|'
        results = [sanitizeDfsFilename(f"_{ch}_") for ch in illegal]
        assert len(results) == len(set(results)), "Two illegal chars produced the same output"


# ---------------------------------------------------------------------------
# resolveOutputPath unit tests
# ---------------------------------------------------------------------------

class TestResolveOutputPath:

    def testSingleSideHierarchical(self, tmp_path):
        # Single-sided: out_dir/dir/filename.
        result = resolveOutputPath(str(tmp_path), 0, "$", "FILE", multi_side=False)
        assert result == os.path.join(str(tmp_path), "$", "FILE")

    def testSingleSideNonDefaultDir(self, tmp_path):
        result = resolveOutputPath(str(tmp_path), 0, "T", "PROG", multi_side=False)
        assert result == os.path.join(str(tmp_path), "T", "PROG")

    def testDoubleSideSide0(self, tmp_path):
        result = resolveOutputPath(str(tmp_path), 0, "$", "FILE", multi_side=True)
        assert result == os.path.join(str(tmp_path), "side0", "$", "FILE")

    def testDoubleSideSide1(self, tmp_path):
        result = resolveOutputPath(str(tmp_path), 1, "T", "PROG", multi_side=True)
        assert result == os.path.join(str(tmp_path), "side1", "T", "PROG")

    def testDirectoriesCreated(self, tmp_path):
        # resolveOutputPath must create all intermediate directories.
        resolveOutputPath(str(tmp_path), 0, "$", "FILE", multi_side=True)
        assert os.path.isdir(os.path.join(str(tmp_path), "side0", "$"))

    def testSingleSideDirectoryCreated(self, tmp_path):
        resolveOutputPath(str(tmp_path), 0, "T", "PROG", multi_side=False)
        assert os.path.isdir(os.path.join(str(tmp_path), "T"))


# ---------------------------------------------------------------------------
# extractAll integration tests using in-memory disc images
# ---------------------------------------------------------------------------

class TestExtractAllSingleSide:

    def testSingleSideExtractsHierarchically(self, tmp_path):
        # Build a single-sided image with one binary file.
        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(_makeSsdImage("MYFILE", b"\xDE\xAD\xBE\xEF" * 4))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, pretty=False)

        # File should be in out_dir/$/MYFILE.bin (hierarchical layout).
        assert os.path.isfile(os.path.join(out_dir, "$", "MYFILE.bin"))
        assert not os.path.isdir(os.path.join(out_dir, "side0"))

    def testPlainTextFileSavedAsTxt(self, tmp_path):
        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(_makeSsdImage("README", b"Hello BBC\rworld\r"))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, pretty=False)

        assert os.path.isfile(os.path.join(out_dir, "$", "README.txt"))
        assert not os.path.isfile(os.path.join(out_dir, "$", "README.bin"))

    def testPlainTextCrNormalisedToLf(self, tmp_path):
        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(_makeSsdImage("NOTES", b"line one\rline two\r"))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, pretty=False)

        txt_path = os.path.join(out_dir, "$", "NOTES.txt")
        with open(txt_path, "rb") as f:
            raw = f.read()

        assert b"\r" not in raw, "CR bytes must have been normalised to LF"
        assert raw == b"line one\nline two\n"


class TestExtractAllDoubleSideSubdir:

    def testDefaultSubdirLayout(self, tmp_path):
        img_path = str(tmp_path / "test.dsd")
        with open(img_path, "wb") as f:
            f.write(_makeDsdImage("PROG0", b"\x01" * 16, "PROG1", b"\x02" * 16))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, pretty=False)

        assert os.path.isfile(os.path.join(out_dir, "side0", "$", "PROG0.bin"))
        assert os.path.isfile(os.path.join(out_dir, "side1", "$", "PROG1.bin"))


# ---------------------------------------------------------------------------
# cmdCat --inspect tests
# ---------------------------------------------------------------------------

class TestCmdCatInspect:

    def _runCat(self, tmp_path, file_data: bytes, inspect: bool) -> str:
        """Build a single-sided image, run cmdCat, and return captured stdout."""
        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(_makeSsdImage("README", file_data))
        args = Namespace(image=img_path, sort="name", inspect=inspect)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCat(args)
        return buf.getvalue()

    def testBinaryFileNoType(self, tmp_path):
        # Binary data: no type label in default or inspect mode.
        output = self._runCat(tmp_path, b"\xDE\xAD\xBE\xEF" * 4, inspect=False)
        assert "BASIC" not in output
        assert "TEXT" not in output

    def testTextFileNoLabelWithoutInspect(self, tmp_path):
        # Plain text data without --inspect: type column stays blank.
        output = self._runCat(tmp_path, b"Hello BBC\rworld\r", inspect=False)
        assert "TEXT" not in output

    def testTextFileLabelledWithInspect(self, tmp_path):
        # Plain text data with --inspect: should show TEXT.
        output = self._runCat(tmp_path, b"Hello BBC\rworld\r", inspect=True)
        assert "TEXT" in output

    def testBinaryFileStillNoTypeWithInspect(self, tmp_path):
        # Binary data with --inspect: still blank (not TEXT).
        output = self._runCat(tmp_path, b"\xDE\xAD\xBE\xEF" * 4, inspect=True)
        assert "BASIC" not in output
        assert "TEXT" not in output
