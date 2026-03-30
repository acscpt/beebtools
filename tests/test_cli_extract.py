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

from beebtools.disc import sanitizeDfsName, resolveOutputPath, extractAll
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

class TestSanitizeDfsName:

    def testNormalNameDoesNotChange(self):
        assert sanitizeDfsName("T", "MYPROG") == "T_MYPROG"

    def testDollarDirectoryPreserved(self):
        assert sanitizeDfsName("$", "BOOT") == "$_BOOT"

    def testExclamationDirectoryPreserved(self):
        assert sanitizeDfsName("!", "BOOT") == "!_BOOT"

    def testForwardSlashEncoded(self):
        assert sanitizeDfsName("T", "A/B") == "T_A_x2F_B"

    def testBackslashEncoded(self):
        assert sanitizeDfsName("T", "A\\B") == "T_A_x5C_B"

    def testSlashAndBackslashDistinct(self):
        # Two different illegal chars must produce different output.
        slash = sanitizeDfsName("T", "A/B")
        backslash = sanitizeDfsName("T", "A\\B")
        assert slash != backslash

    def testColonEncoded(self):
        assert sanitizeDfsName("T", "A:B") == "T_A_x3A_B"

    def testControlCharDropped(self):
        assert sanitizeDfsName("T", "A\x01B") == "T_AB"

    def testAllWindowsIllegalCharsEncoded(self):
        # None of the Windows-illegal chars should appear unencoded in output.
        illegal = '\\/:*?"<>|'
        for ch in illegal:
            result = sanitizeDfsName("T", f"A{ch}B")
            assert ch not in result, f"Illegal char {repr(ch)} appeared unencoded in {repr(result)}"

    def testWindowsIllegalCharsAllDistinct(self):
        # Each illegal char must produce a unique encoding.
        illegal = '\\/:*?"<>|'
        results = [sanitizeDfsName("T", f"_{ch}_") for ch in illegal]
        assert len(results) == len(set(results)), "Two illegal chars produced the same output"


# ---------------------------------------------------------------------------
# resolveOutputPath unit tests
# ---------------------------------------------------------------------------

class TestResolveOutputPath:

    def testSingleSideNoSeparation(self):
        # Single-sided: sides_mode is irrelevant, file goes directly in out_dir.
        result = resolveOutputPath("/out", 0, "$.FILE", multi_side=False, sides_mode=None)
        assert result == os.path.join("/out", "$.FILE")

    def testSingleSideIgnoresSidesMode(self):
        result = resolveOutputPath("/out", 0, "$.FILE", multi_side=False, sides_mode="prefix")
        assert result == os.path.join("/out", "$.FILE")

    def testDoubleSideDefaultIsSubdir(self, tmp_path):
        # None sides_mode on a double-sided disc: subdir layout.
        result = resolveOutputPath(str(tmp_path), 0, "$.FILE", multi_side=True, sides_mode=None)
        assert result == os.path.join(str(tmp_path), "side0", "$.FILE")

    def testDoubleSideSubdirMode(self, tmp_path):
        result = resolveOutputPath(str(tmp_path), 1, "T.PROG", multi_side=True, sides_mode="subdir")
        assert result == os.path.join(str(tmp_path), "side1", "T.PROG")

    def testDoubleSidePrefixMode(self):
        result = resolveOutputPath("/out", 0, "$.FILE", multi_side=True, sides_mode="prefix")
        assert result == os.path.join("/out", "side0_$.FILE")

    def testDoubleSidePrefixModeSide1(self):
        result = resolveOutputPath("/out", 1, "T.PROG", multi_side=True, sides_mode="prefix")
        assert result == os.path.join("/out", "side1_T.PROG")

    def testSubdirIsCreated(self, tmp_path):
        # resolveOutputPath must create the side subdirectory.
        resolveOutputPath(str(tmp_path), 0, "$.FILE", multi_side=True, sides_mode=None)
        assert os.path.isdir(os.path.join(str(tmp_path), "side0"))


# ---------------------------------------------------------------------------
# extractAll integration tests using in-memory disc images
# ---------------------------------------------------------------------------

class TestExtractAllSingleSide:

    def testSingleSideExtractsToFlatDir(self, tmp_path):
        # Build a single-sided image with one binary file.
        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(_makeSsdImage("MYFILE", b"\xDE\xAD\xBE\xEF" * 4))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, sides_mode=None, pretty=False)

        # File should be directly in out_dir, no side subdirectory.
        assert os.path.isfile(os.path.join(out_dir, "$_MYFILE.bin"))
        assert not os.path.isdir(os.path.join(out_dir, "side0"))

    def testPlainTextFileSavedAsTxt(self, tmp_path):
        # A file whose bytes are all printable ASCII + CR should get a .txt extension.
        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(_makeSsdImage("README", b"Hello BBC\rworld\r"))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, sides_mode=None, pretty=False)

        assert os.path.isfile(os.path.join(out_dir, "$_README.txt"))
        assert not os.path.isfile(os.path.join(out_dir, "$_README.bin"))

    def testPlainTextCrNormalisedToLf(self, tmp_path):
        # BBC text files use CR (0x0D) as line terminator.
        # Extracted .txt files must have LF-only line endings.
        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(_makeSsdImage("NOTES", b"line one\rline two\r"))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, sides_mode=None, pretty=False)

        txt_path = os.path.join(out_dir, "$_NOTES.txt")
        with open(txt_path, "rb") as f:
            raw = f.read()

        assert b"\r" not in raw, "CR bytes must have been normalised to LF"
        assert raw == b"line one\nline two\n"


class TestExtractAllDoubleSideSubdir:

    def testDefaultSubdirLayout(self, tmp_path):
        # No --sides flag on a double-sided disc: should use subdir automatically.
        img_path = str(tmp_path / "test.dsd")
        with open(img_path, "wb") as f:
            f.write(_makeDsdImage("PROG0", b"\x01" * 16, "PROG1", b"\x02" * 16))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, sides_mode=None, pretty=False)

        assert os.path.isfile(os.path.join(out_dir, "side0", "$_PROG0.bin"))
        assert os.path.isfile(os.path.join(out_dir, "side1", "$_PROG1.bin"))

    def testExplicitSubdirLayout(self, tmp_path):
        img_path = str(tmp_path / "test.dsd")
        with open(img_path, "wb") as f:
            f.write(_makeDsdImage("PROG0", b"\x01" * 16, "PROG1", b"\x02" * 16))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, sides_mode="subdir", pretty=False)

        assert os.path.isfile(os.path.join(out_dir, "side0", "$_PROG0.bin"))
        assert os.path.isfile(os.path.join(out_dir, "side1", "$_PROG1.bin"))


class TestExtractAllDoubleSidePrefix:

    def testPrefixLayoutFlatDir(self, tmp_path):
        img_path = str(tmp_path / "test.dsd")
        with open(img_path, "wb") as f:
            f.write(_makeDsdImage("PROG0", b"\x01" * 16, "PROG1", b"\x02" * 16))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, sides_mode="prefix", pretty=False)

        # Files must be in the flat out_dir, not in subdirectories.
        assert os.path.isfile(os.path.join(out_dir, "side0_$_PROG0.bin"))
        assert os.path.isfile(os.path.join(out_dir, "side1_$_PROG1.bin"))
        assert not os.path.isdir(os.path.join(out_dir, "side0"))
        assert not os.path.isdir(os.path.join(out_dir, "side1"))

    def testPrefixLayoutCollisionBothSides(self, tmp_path):
        # Same filename on both sides - prefix mode must keep both without collision.
        img_path = str(tmp_path / "test.dsd")
        with open(img_path, "wb") as f:
            f.write(_makeDsdImage("SHARED", b"\xAA" * 16, "SHARED", b"\xBB" * 16))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, sides_mode="prefix", pretty=False)

        side0_path = os.path.join(out_dir, "side0_$_SHARED.bin")
        side1_path = os.path.join(out_dir, "side1_$_SHARED.bin")
        assert os.path.isfile(side0_path)
        assert os.path.isfile(side1_path)

        # Content must differ - they are different files from each side.
        with open(side0_path, "rb") as f:
            assert f.read() == b"\xAA" * 16
        with open(side1_path, "rb") as f:
            assert f.read() == b"\xBB" * 16


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
