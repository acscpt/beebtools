# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Unit tests for the extractAll bulk-extraction logic.

Tests use a minimal in-memory fake disc image so no real disc files are needed.
The helpers below build a valid single-sided or double-sided DFS image in memory
that contains one small binary file per side, allowing the --sides behaviour to
be verified without touching the filesystem beyond the tmp_path fixture.
"""

import os
import struct
import io
import contextlib
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


def _makeSector1(file_data_len: int, start_sector: int = 2,
                  load_addr: int = 0, exec_addr: int = 0,
                  disc_size: int = 800) -> bytes:
    """Build DFS catalogue sector 1 for one file entry."""
    buf = bytearray(SECTOR_SIZE)

    # Disc title continuation (bytes 0-3, leave as zero)
    # Sector count (bytes 4-5): not used in our tests
    # File count: sector1[5] = number_of_entries * 8
    buf[5] = 1 * 8  # one file

    # Disc size in sectors: byte 7 = low 8 bits, byte 6 bits 0-1 = high 2 bits.
    # The boot option occupies bits 4-5 of byte 6.
    buf[6] = (disc_size >> 8) & 0x03
    buf[7] = disc_size & 0xFF

    # File entry at offset 8 in sector 1:
    # bytes 0-1: load_lo, bytes 2-3: exec_lo, bytes 4-5: length_lo
    # byte 6: extra bits, byte 7: start_sector
    length_lo = file_data_len & 0xFFFF
    buf[8] = load_addr & 0xFF
    buf[9] = (load_addr >> 8) & 0xFF
    buf[10] = exec_addr & 0xFF
    buf[11] = (exec_addr >> 8) & 0xFF
    buf[12] = length_lo & 0xFF
    buf[13] = (length_lo >> 8) & 0xFF

    # Extra bits: bits 3-2 = exec high, bits 5-4 = length high,
    #             bits 7-6 = load high, bits 1-0 = start sector high
    exec_hi = (exec_addr >> 16) & 0x03
    load_hi = (load_addr >> 16) & 0x03
    length_hi = (file_data_len >> 16) & 0x03
    start_hi = (start_sector >> 8) & 0x03
    buf[14] = (start_hi | (exec_hi << 2) | (length_hi << 4) | (load_hi << 6))
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


def _makeSsdImageBasic(filename: str, file_data: bytes, directory: str = "$") -> bytes:
    """Build a minimal .ssd disc image with one BASIC file (exec = 0x8023)."""
    image = bytearray(80 * 10 * SECTOR_SIZE)  # 80 tracks, 10 sectors

    sec0 = _makeSector0(filename, directory)
    sec1 = _makeSector1(len(file_data), start_sector=2,
                        load_addr=0x0E00, exec_addr=0x8023)

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


def _makeDsdImageBasic(
    filename0: str,
    data0: bytes,
    filename1: str,
    data1: bytes,
) -> bytes:
    """Build a .dsd image where both files have BASIC exec address 0x8023."""
    image = bytearray(80 * 20 * SECTOR_SIZE)

    def sectorOffset(track: int, side: int, sector: int) -> int:
        return (track * 20 + side * 10 + sector) * SECTOR_SIZE

    # Side 0 catalogue with BASIC exec address.
    s0_sec0 = _makeSector0(filename0)
    s0_sec1 = _makeSector1(len(data0), start_sector=2,
                           load_addr=0x0E00, exec_addr=0x8023)
    image[sectorOffset(0, 0, 0):sectorOffset(0, 0, 0) + SECTOR_SIZE] = s0_sec0
    image[sectorOffset(0, 0, 1):sectorOffset(0, 0, 1) + SECTOR_SIZE] = s0_sec1
    off = sectorOffset(0, 0, 2)
    image[off:off + len(data0)] = data0

    # Side 1 catalogue with BASIC exec address.
    s1_sec0 = _makeSector0(filename1)
    s1_sec1 = _makeSector1(len(data1), start_sector=2,
                           load_addr=0x0E00, exec_addr=0x8023)
    image[sectorOffset(0, 1, 0):sectorOffset(0, 1, 0) + SECTOR_SIZE] = s1_sec0
    image[sectorOffset(0, 1, 1):sectorOffset(0, 1, 1) + SECTOR_SIZE] = s1_sec1
    off = sectorOffset(0, 1, 2)
    image[off:off + len(data1)] = data1

    return bytes(image)


# ---------------------------------------------------------------------------
# sanitizeDfsName unit tests
# ---------------------------------------------------------------------------

class TestSanitizeDfsDir:

    def testNormalDirUnchanged(self):
        """A standard single-letter DFS directory character should pass through the sanitizer unchanged."""
        assert sanitizeDfsDir("T") == "T"

    def testDollarDirUnchanged(self):
        """The '$' default DFS directory character should not be encoded or modified."""
        assert sanitizeDfsDir("$") == "$"

    def testExclamationDirUnchanged(self):
        """The '!' character is a valid DFS directory and should pass through unchanged."""
        assert sanitizeDfsDir("!") == "!"

    def testIllegalCharEncoded(self):
        """A directory character that is illegal on the host filesystem should be percent-encoded to a safe form."""
        assert sanitizeDfsDir("/") == "_x2F_"


class TestSanitizeDfsFilename:

    def testNormalNameUnchanged(self):
        """A plain alphanumeric filename with no special characters should not be altered by the sanitizer."""
        assert sanitizeDfsFilename("MYPROG") == "MYPROG"

    def testForwardSlashEncoded(self):
        """A forward slash in a DFS filename would create a phantom directory on the host and must be percent-encoded."""
        assert sanitizeDfsFilename("A/B") == "A_x2F_B"

    def testBackslashEncoded(self):
        """A backslash is illegal in Windows filenames and should be percent-encoded."""
        assert sanitizeDfsFilename("A\\B") == "A_x5C_B"

    def testSlashAndBackslashDistinct(self):
        """Forward slash and backslash should map to distinct encoded sequences so they remain differentiable."""
        slash = sanitizeDfsFilename("A/B")
        backslash = sanitizeDfsFilename("A\\B")
        assert slash != backslash

    def testColonEncoded(self):
        """A colon is illegal in Windows filenames and should be percent-encoded."""
        assert sanitizeDfsFilename("A:B") == "A_x3A_B"

    def testControlCharDropped(self):
        """Control characters (below 0x20) in a DFS filename should be dropped entirely from the output."""
        assert sanitizeDfsFilename("A\x01B") == "AB"

    def testAllWindowsIllegalCharsEncoded(self):
        """All characters illegal under Windows (< > : " / \\ | ? *) should each be encoded or dropped."""
        illegal = '\\/:*?"<>|'
        for ch in illegal:
            result = sanitizeDfsFilename(f"A{ch}B")
            assert ch not in result, f"Illegal char {repr(ch)} appeared unencoded in {repr(result)}"

    def testWindowsIllegalCharsAllDistinct(self):
        """Each illegal character must map to a distinct encoded output to avoid two different names colliding."""
        illegal = '\\/:*?"<>|'
        results = [sanitizeDfsFilename(f"_{ch}_") for ch in illegal]
        assert len(results) == len(set(results)), "Two illegal chars produced the same output"


# ---------------------------------------------------------------------------
# resolveOutputPath unit tests
# ---------------------------------------------------------------------------

class TestResolveOutputPath:

    def testSingleSideHierarchical(self, tmp_path):
        """A single-sided disc should place extracted files under a subdirectory named after its DFS directory."""
        # Single-sided: out_dir/dir/filename.
        result = resolveOutputPath(str(tmp_path), 0, "$", "FILE", multi_side=False)
        assert result == os.path.join(str(tmp_path), "$", "FILE")

    def testSingleSideNonDefaultDir(self, tmp_path):
        """Files in a non-default DFS directory should be placed in a matching subdirectory of the output."""
        result = resolveOutputPath(str(tmp_path), 0, "T", "PROG", multi_side=False)
        assert result == os.path.join(str(tmp_path), "T", "PROG")

    def testDoubleSideSide0(self, tmp_path):
        """Side 0 files of a double-sided disc should be extracted under a 'side0' subdirectory."""
        result = resolveOutputPath(str(tmp_path), 0, "$", "FILE", multi_side=True)
        assert result == os.path.join(str(tmp_path), "side0", "$", "FILE")

    def testDoubleSideSide1(self, tmp_path):
        """Side 1 files of a double-sided disc should be extracted under a 'side1' subdirectory."""
        result = resolveOutputPath(str(tmp_path), 1, "T", "PROG", multi_side=True)
        assert result == os.path.join(str(tmp_path), "side1", "T", "PROG")

    def testDirectoriesCreated(self, tmp_path):
        """Any missing intermediate directories in the output path should be created automatically."""
        # resolveOutputPath must create all intermediate directories.
        resolveOutputPath(str(tmp_path), 0, "$", "FILE", multi_side=True)
        assert os.path.isdir(os.path.join(str(tmp_path), "side0", "$"))

    def testSingleSideDirectoryCreated(self, tmp_path):
        """The top-level output directory for a single-sided extraction should be created if it does not exist."""
        resolveOutputPath(str(tmp_path), 0, "T", "PROG", multi_side=False)
        assert os.path.isdir(os.path.join(str(tmp_path), "T"))


# ---------------------------------------------------------------------------
# extractAll integration tests using in-memory disc images
# ---------------------------------------------------------------------------

class TestExtractAllSingleSide:

    def testSingleSideExtractsHierarchically(self, tmp_path):
        """Bulk extraction of a single-sided disc should create a directory tree that mirrors the DFS '$' layout."""
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
        """A file whose content is identified as plain ASCII text should be saved with a .txt extension."""
        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(_makeSsdImage("README", b"Hello BBC\rworld\r"))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, pretty=False)

        assert os.path.isfile(os.path.join(out_dir, "$", "README.txt"))
        assert not os.path.isfile(os.path.join(out_dir, "$", "README.bin"))

    def testPlainTextCrNormalisedToLf(self, tmp_path):
        """Bare carriage-return line endings in extracted plain text should be converted to Unix LF."""
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
        """Bulk extraction of a double-sided disc should produce separate side0/ and side1/ directories."""
        img_path = str(tmp_path / "test.dsd")
        with open(img_path, "wb") as f:
            f.write(_makeDsdImage("PROG0", b"\x01" * 16, "PROG1", b"\x02" * 16))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, pretty=False)

        assert os.path.isfile(os.path.join(out_dir, "side0", "$", "PROG0.bin"))
        assert os.path.isfile(os.path.join(out_dir, "side1", "$", "PROG1.bin"))


# ---------------------------------------------------------------------------
# extractAll: BASIC with trailing machine code
# ---------------------------------------------------------------------------

def _makeBasicLine(linenum: int, content: bytes) -> bytes:
    """Build one tokenized BASIC line record."""
    hi = (linenum >> 8) & 0xFF
    lo = linenum & 0xFF
    linelen = 3 + 1 + len(content)
    return bytes([0x0D, hi, lo, linelen]) + content


def _makeBasicProgram(*lines) -> bytes:
    """Build tokenized BASIC from (linenum, content) tuples + end marker."""
    data = bytearray()
    for linenum, content in lines:
        data += _makeBasicLine(linenum, content)
    data += bytes([0x0D, 0xFF])
    return bytes(data)


class TestExtractAllBasicHybrid:

    def testPureBasicExtractedAsBas(self, tmp_path):
        """A file containing only tokenized BASIC should be detokenized and saved as .bas."""
        basic = _makeBasicProgram((10, bytes([0xF1])))  # 10 PRINT
        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(_makeSsdImageBasic("MYPROG", basic))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, pretty=False)

        assert os.path.isfile(os.path.join(out_dir, "$", "MYPROG.bas"))
        assert not os.path.isfile(os.path.join(out_dir, "$", "MYPROG.bin"))

    def testBasicWithTrailingMachineCodeExtractedAsBin(self, tmp_path):
        """A BASIC loader with appended machine code should be saved as .bin to preserve the binary data."""
        basic = _makeBasicProgram((10, bytes([0xF1])))  # 10 PRINT
        machine_code = bytes(range(256)) * 4  # 1024 bytes of binary
        hybrid = basic + machine_code

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(_makeSsdImageBasic("PINBALL", hybrid))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, pretty=False)

        # Must be saved as binary to preserve the machine code.
        assert os.path.isfile(os.path.join(out_dir, "$", "PINBALL.bin"))
        assert not os.path.isfile(os.path.join(out_dir, "$", "PINBALL.bas"))

        # Binary content must be identical to the original.
        with open(os.path.join(out_dir, "$", "PINBALL.bin"), "rb") as f:
            assert f.read() == hybrid

    def testHybridResultTypeIsBasicMC(self, tmp_path):
        """The extractAll result dict for a hybrid file should have type 'BASIC+MC' and include basic_size."""
        basic = _makeBasicProgram((10, bytes([0xF1])))
        machine_code = bytes(range(256)) * 4
        hybrid = basic + machine_code

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(_makeSsdImageBasic("LOADER", hybrid))

        out_dir = str(tmp_path / "out")
        results = extractAll(img_path, out_dir, pretty=False)

        assert len(results) == 1
        assert results[0]["type"] == "BASIC+MC"
        assert "basic_size" in results[0]
        assert results[0]["basic_size"] == len(basic)

    def testDsdHybridExtractedAsBin(self, tmp_path):
        """A BASIC+machine-code hybrid on a double-sided DSD should be saved as .bin on both sides."""
        basic = _makeBasicProgram((10, bytes([0xF1])))  # 10 PRINT
        machine_code = bytes(range(256)) * 4
        hybrid = basic + machine_code

        img_path = str(tmp_path / "test.dsd")
        with open(img_path, "wb") as f:
            f.write(_makeDsdImageBasic("GAME0", hybrid, "GAME1", hybrid))

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, pretty=False)

        # Both sides should get .bin files.
        assert os.path.isfile(os.path.join(out_dir, "side0", "$", "GAME0.bin"))
        assert os.path.isfile(os.path.join(out_dir, "side1", "$", "GAME1.bin"))
        assert not os.path.isfile(os.path.join(out_dir, "side0", "$", "GAME0.bas"))
        assert not os.path.isfile(os.path.join(out_dir, "side1", "$", "GAME1.bas"))

    def testAdfsHybridExtractedAsBin(self, tmp_path):
        """A BASIC+machine-code hybrid in an ADFS image should be saved as .bin."""
        from beebtools.adfs import createAdfsImage, ADFS_S_SECTORS
        from beebtools.entry import DiscFile

        basic = _makeBasicProgram((10, bytes([0xF1])))
        machine_code = bytes(range(256)) * 4
        hybrid = basic + machine_code

        # Create an ADFS-S image with one BASIC hybrid file.
        image = createAdfsImage(total_sectors=ADFS_S_SECTORS, title="TEST")
        side = image.sides[0]
        side.addFile(DiscFile("$.HYBRID", hybrid, 0x0E00, 0x8023))

        img_path = str(tmp_path / "test.adf")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, pretty=False)

        # Should be saved as .bin, not .bas.
        assert os.path.isfile(os.path.join(out_dir, "$", "HYBRID.bin"))
        assert not os.path.isfile(os.path.join(out_dir, "$", "HYBRID.bas"))

        # Binary content must be identical.
        with open(os.path.join(out_dir, "$", "HYBRID.bin"), "rb") as f:
            assert f.read() == hybrid


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
        """A binary file should not receive any type label in the cmdCat output, regardless of content."""
        # Binary data: no type label in default or inspect mode.
        output = self._runCat(tmp_path, b"\xDE\xAD\xBE\xEF" * 4, inspect=False)
        assert "BASIC" not in output
        assert "TEXT" not in output

    def testTextFileNoLabelWithoutInspect(self, tmp_path):
        """Without the --inspect flag, a plain text file should appear in the listing without a type label."""
        # Plain text data without --inspect: type column stays blank.
        output = self._runCat(tmp_path, b"Hello BBC\rworld\r", inspect=False)
        assert "TEXT" not in output

    def testTextFileLabelledWithInspect(self, tmp_path):
        """With --inspect active, a file whose content is plain ASCII should be labelled 'TEXT' in the listing."""
        # Plain text data with --inspect: should show TEXT.
        output = self._runCat(tmp_path, b"Hello BBC\rworld\r", inspect=True)
        assert "TEXT" in output

    def testBinaryFileStillNoTypeWithInspect(self, tmp_path):
        """A binary file should remain unlabelled even when --inspect is active."""
        # Binary data with --inspect: still blank (not TEXT).
        output = self._runCat(tmp_path, b"\xDE\xAD\xBE\xEF" * 4, inspect=True)
        assert "BASIC" not in output
        assert "TEXT" not in output

    def _runCatBasic(self, tmp_path, file_data: bytes, inspect: bool) -> str:
        """Build an SSD with one BASIC file (exec=0x8023) and run cmdCat."""
        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(_makeSsdImageBasic("PROG", file_data))
        args = Namespace(image=img_path, sort="name", inspect=inspect)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCat(args)
        return buf.getvalue()

    def testPureBasicShowsBasicType(self, tmp_path):
        """A pure BASIC file should show 'BASIC' in the type column."""
        basic = _makeBasicProgram((10, bytes([0xF1])))
        output = self._runCatBasic(tmp_path, basic, inspect=False)
        assert "BASIC" in output
        assert "BASIC+MC" not in output

    def testHybridShowsBasicWithoutInspect(self, tmp_path):
        """Without --inspect, a hybrid BASIC+MC file shows plain 'BASIC' since content is not read."""
        basic = _makeBasicProgram((10, bytes([0xF1])))
        hybrid = basic + bytes(range(256)) * 4
        output = self._runCatBasic(tmp_path, hybrid, inspect=False)
        assert "BASIC" in output
        assert "BASIC+MC" not in output

    def testHybridShowsBasicMCWithInspect(self, tmp_path):
        """With --inspect, a hybrid BASIC+MC file should be labelled 'BASIC+MC' in the type column."""
        basic = _makeBasicProgram((10, bytes([0xF1])))
        hybrid = basic + bytes(range(256)) * 4
        output = self._runCatBasic(tmp_path, hybrid, inspect=True)
        assert "BASIC+MC" in output


# ---------------------------------------------------------------------------
# cmdCat track display tests
# ---------------------------------------------------------------------------

class TestCmdCatTracks:

    def testSsdShowsTrackCount(self, tmp_path):
        """An 80-track SSD image should show '80 tracks' in the header."""
        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(_makeSsdImage("README", b"Hello"))
        args = Namespace(image=img_path, sort="name", inspect=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCat(args)
        assert "80 tracks" in buf.getvalue()

    def testDsdShowsTrackCountBothSides(self, tmp_path):
        """A DSD image should show track count in the header for each side."""
        img_path = str(tmp_path / "test.dsd")
        with open(img_path, "wb") as f:
            f.write(_makeDsdImage("FILE0", b"side0", "FILE1", b"side1"))
        args = Namespace(image=img_path, sort="name", inspect=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCat(args)
        output = buf.getvalue()

        # Both sides should report 80 tracks.
        assert output.count("80 tracks") == 2

    def test40TrackSsdShowsTrackCount(self, tmp_path):
        """A 40-track SSD image should show '40 tracks' in the header."""
        # Build a 40-track image (400 sectors).
        image = bytearray(40 * 10 * SECTOR_SIZE)
        sec0 = _makeSector0("README")
        sec1 = _makeSector1(5, start_sector=2, disc_size=400)
        image[0:SECTOR_SIZE] = sec0
        image[SECTOR_SIZE:2 * SECTOR_SIZE] = sec1
        image[2 * SECTOR_SIZE:2 * SECTOR_SIZE + 5] = b"Hello"

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(bytes(image))
        args = Namespace(image=img_path, sort="name", inspect=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCat(args)
        assert "40 tracks" in buf.getvalue()
