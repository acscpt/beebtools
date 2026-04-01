# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Integration tests for the DFS disc image reader.

Tests are parametrized over every .dsd file found in tests/resources/discs/.
That directory is git-ignored and must be populated locally before the
tests will run.  The suite is skipped automatically when no images are present.

Assertions target invariants that must hold for ANY valid DFS disc image,
so adding new disc images to the resources directory extends test coverage
with no code changes.
"""

import os
import glob
import pytest

from beebtools import (
    openDiscImage, looksLikeTokenizedBasic, looksLikePlainText, detokenize,
    validateDfsName, DFSError,
)

# Discover all disc images in the resources directory.
DISCS_DIR = os.path.join(os.path.dirname(__file__), "resources", "discs")
ALL_DSDS = sorted(glob.glob(os.path.join(DISCS_DIR, "*.dsd")))

# Readable test IDs: just the filename, not the full path.
disc_ids = [os.path.basename(p) for p in ALL_DSDS]

# Skip the entire module when no discs are present (e.g. in CI).
pytestmark = pytest.mark.skipif(
    len(ALL_DSDS) == 0,
    reason="No disc images found in tests/resources/discs/",
)


# ---------------------------------------------------------------------------
# Parametrized helpers
# ---------------------------------------------------------------------------

def allSides(path):
    """Return (path, side_index, disc, entries) for every side of a disc."""
    image = openDiscImage(path)
    result = []
    for i, disc in enumerate(image.sides):
        catalogue = disc.readCatalogue()
        result.append((disc, i, catalogue.entries))
    return result


# Build a flat parametrize list: one entry per (disc_path, side_index).
disc_side_params = []
disc_side_ids = []
for path in ALL_DSDS:
    name = os.path.basename(path)
    for disc, side_idx, entries in allSides(path):
        disc_side_params.append((disc, entries))
        disc_side_ids.append(f"{name}:side{side_idx}")


# ---------------------------------------------------------------------------
# Catalogue structure - invariants for any valid DFS disc side
# ---------------------------------------------------------------------------

class TestCatalogueStructure:

    @pytest.mark.parametrize("path", ALL_DSDS, ids=disc_ids)
    def testDsdOpensTwoSides(self, path):
        assert len(openDiscImage(path).sides) == 2

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testEntryCountIsReasonable(self, disc, entries):
        # Standard DFS supports at most 31 files per side.
        assert 0 <= len(entries) <= 31

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testEveryEntryHasRequiredAttributes(self, disc, entries):
        required = {"name", "directory", "load_addr", "exec_addr", "length", "start_sector", "locked"}
        for entry in entries:
            entry_attrs = set(vars(entry).keys())
            assert required.issubset(entry_attrs)

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testEntryNamesAreNonEmpty(self, disc, entries):
        for entry in entries:
            assert len(entry.name) > 0

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testEntryLengthsArePositive(self, disc, entries):
        for entry in entries:
            assert entry.length >= 0


# ---------------------------------------------------------------------------
# File extraction - invariants for any file on any disc
# ---------------------------------------------------------------------------

class TestFileExtraction:

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testExtractedLengthMatchesCatalogue(self, disc, entries):
        for entry in entries:
            data = disc.readFile(entry)
            assert len(data) == entry.length, (
                f"{entry.fullName}: "
                f"expected {entry.length} bytes, got {len(data)}"
            )

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testBasicFilesStartWith0x0d(self, disc, entries):
        for entry in entries:
            if entry.isBasic:
                data = disc.readFile(entry)
                if len(data) > 0:
                    assert data[0] == 0x0D, (
                        f"{entry.fullName} does not start with 0x0D"
                    )

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testBasicFilesDetokenizeWithoutError(self, disc, entries):
        for entry in entries:
            if entry.isBasic:
                data = disc.readFile(entry)
                if looksLikeTokenizedBasic(data):
                    lines = detokenize(data)
                    assert isinstance(lines, list)

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testDetokenizedLinesHaveLineNumbers(self, disc, entries):
        for entry in entries:
            if entry.isBasic:
                data = disc.readFile(entry)
                if looksLikeTokenizedBasic(data):
                    for line in detokenize(data):
                        assert line[:5].strip().isdigit(), (
                            f"{entry.fullName}: bad line {repr(line)}"
                        )


# ---------------------------------------------------------------------------
# looksLikePlainText unit tests (no disc images needed)
# ---------------------------------------------------------------------------

class TestLooksLikePlainText:

    def testPrintableAsciiIsText(self):
        assert looksLikePlainText(b"Hello, world!")

    def testTabCrLfAccepted(self):
        assert looksLikePlainText(b"line1\r\nline2\ttabbed")

    def testEmptyIsNotText(self):
        assert not looksLikePlainText(b"")

    def testHighByteIsNotText(self):
        assert not looksLikePlainText(b"Hello\x80world")

    def testNulByteIsNotText(self):
        assert not looksLikePlainText(b"Hello\x00world")

    def testControlCharIsNotText(self):
        # 0x01 is a control char that should not be accepted.
        assert not looksLikePlainText(b"\x01")

    def testBasicMarkerIsNotText(self):
        # 0x0D at the start is the BASIC line marker - but a lone 0x0D is
        # carriage return, which IS accepted as whitespace.
        # A tokenized BASIC file will have non-text bytes after the 0x0D.
        assert not looksLikePlainText(b"\x0D\x00\x0A\x05\xf1")


# ---------------------------------------------------------------------------
# Validation unit tests (synthetic corrupt images, no disc files needed)
# ---------------------------------------------------------------------------

from beebtools import (
    DFSEntry, DFSCatalogue, DFSImage, DFSSide, DFSError, DFSFormatError,
    createDiscImage, BootOption,
)

SECTOR_SIZE = 256


def _blankSsd(tracks: int = 80) -> bytearray:
    """Return a zeroed SSD-sized bytearray for building test images."""
    return bytearray(tracks * 10 * SECTOR_SIZE)


def _blankDsd(tracks: int = 80) -> bytearray:
    """Return a zeroed DSD-sized bytearray for building test images."""
    return bytearray(tracks * 20 * SECTOR_SIZE)


def _ssdWithOneFile(
    name: str = "TEST",
    directory: str = "$",
    length: int = 16,
    start_sector: int = 2,
    load_addr: int = 0x0E00,
    exec_addr: int = 0x802B,
    locked: bool = False,
) -> bytearray:
    """Build a minimal SSD image with one file entry in the catalogue."""
    data = _blankSsd()

    # Sector 0: title + one file entry.
    data[0:8] = b"TESTDISC"
    base0 = 8
    name_bytes = name.encode("ascii")[:7].ljust(7, b" ")
    data[base0 : base0 + 7] = name_bytes
    dir_byte = ord(directory) & 0x7F
    if locked:
        dir_byte |= 0x80
    data[base0 + 7] = dir_byte

    # Sector 1: title cont + catalogue metadata + one file entry.
    sec1_off = SECTOR_SIZE
    data[sec1_off + 5] = 1 * 8  # one file

    # Disc size = 800 sectors (80 tracks * 10).
    data[sec1_off + 6] = 0x03  # bits 0-1 = high disc size (3), boot=0
    data[sec1_off + 7] = 0x20  # low disc size: 0x320 = 800

    base1 = sec1_off + 8
    data[base1] = load_addr & 0xFF
    data[base1 + 1] = (load_addr >> 8) & 0xFF
    data[base1 + 2] = exec_addr & 0xFF
    data[base1 + 3] = (exec_addr >> 8) & 0xFF
    data[base1 + 4] = length & 0xFF
    data[base1 + 5] = (length >> 8) & 0xFF

    start_hi = (start_sector >> 8) & 0x03
    load_hi = (load_addr >> 16) & 0x03
    length_hi = (length >> 16) & 0x03
    exec_hi = (exec_addr >> 16) & 0x03
    extra = start_hi | (load_hi << 2) | (length_hi << 4) | (exec_hi << 6)
    data[base1 + 6] = extra
    data[base1 + 7] = start_sector & 0xFF

    # Write some file data at start_sector (only if it's past the catalogue).
    if start_sector >= 2:
        file_off = start_sector * SECTOR_SIZE
        for i in range(min(length, len(data) - file_off)):
            data[file_off + i] = 0xAA

    return data


class TestValidation:
    """Tests for all DFSFormatError and ValueError validation paths."""

    # --- openDiscImage: image too small ---

    def testSsdTooSmallRaises(self, tmp_path):
        path = str(tmp_path / "tiny.ssd")
        with open(path, "wb") as f:
            f.write(b"\x00" * 100)

        with pytest.raises(DFSFormatError, match="too small for SSD"):
            openDiscImage(path)

    def testDsdTooSmallRaises(self, tmp_path):
        path = str(tmp_path / "tiny.dsd")
        with open(path, "wb") as f:
            f.write(b"\x00" * 100)

        with pytest.raises(DFSFormatError, match="too small for DSD"):
            openDiscImage(path)

    def testSsdMinimumSizeAccepted(self, tmp_path):
        # Exactly 2 sectors (512 bytes) is the minimum valid SSD.
        path = str(tmp_path / "min.ssd")
        with open(path, "wb") as f:
            f.write(b"\x00" * (2 * SECTOR_SIZE))

        image = openDiscImage(path)
        cat = image.sides[0].readCatalogue()
        assert len(cat.entries) == 0

    def testDsdMinimumSizeAccepted(self, tmp_path):
        # 20 sectors (5120 bytes) is the minimum valid DSD.
        path = str(tmp_path / "min.dsd")
        with open(path, "wb") as f:
            f.write(b"\x00" * (20 * SECTOR_SIZE))

        image = openDiscImage(path)
        assert len(image.sides) == 2

    # --- Catalogue: file offset not a multiple of 8 ---

    def testOddFileOffsetRaises(self):
        data = _blankSsd()
        data[SECTOR_SIZE + 5] = 7  # Not a multiple of 8.
        image = DFSImage(data, is_dsd=False)
        side = image.sides[0]

        with pytest.raises(DFSFormatError, match="not a multiple of 8"):
            side.readCatalogue()

    def testFileOffsetOf3Raises(self):
        data = _blankSsd()
        data[SECTOR_SIZE + 5] = 3
        image = DFSImage(data, is_dsd=False)

        with pytest.raises(DFSFormatError, match="not a multiple of 8"):
            image.sides[0].readCatalogue()

    # --- Catalogue: start sector overlaps catalogue ---

    def testStartSectorZeroWithNonEmptyFileRaises(self):
        data = _ssdWithOneFile(start_sector=0, length=16)
        image = DFSImage(data, is_dsd=False)

        with pytest.raises(DFSFormatError, match="overlaps the catalogue"):
            image.sides[0].readCatalogue()

    def testStartSectorOneWithNonEmptyFileRaises(self):
        data = _ssdWithOneFile(start_sector=1, length=16)
        image = DFSImage(data, is_dsd=False)

        with pytest.raises(DFSFormatError, match="overlaps the catalogue"):
            image.sides[0].readCatalogue()

    def testStartSectorZeroWithEmptyFileAccepted(self):
        # An empty file (length 0) at sector 0 is allowed - no data to overlap.
        data = _ssdWithOneFile(start_sector=0, length=0)
        image = DFSImage(data, is_dsd=False)
        cat = image.sides[0].readCatalogue()
        assert len(cat.entries) == 1
        assert cat.entries[0].length == 0

    def testStartSectorTwoAccepted(self):
        data = _ssdWithOneFile(start_sector=2, length=16)
        image = DFSImage(data, is_dsd=False)
        cat = image.sides[0].readCatalogue()
        assert len(cat.entries) == 1
        assert cat.entries[0].start_sector == 2

    # --- readFile: file extends beyond image ---

    def testReadFileBeyondImageRaises(self):
        # Create a tiny image but claim a file starts at a high sector.
        data = _ssdWithOneFile(start_sector=900, length=512)
        # Shrink the image so sector 900 is beyond it.
        data = bytearray(data[:10 * SECTOR_SIZE])
        image = DFSImage(data, is_dsd=False)
        cat = image.sides[0].readCatalogue()

        with pytest.raises(DFSFormatError, match="extends beyond the image"):
            image.sides[0].readFile(cat.entries[0])

    # --- readSector: sector beyond image ---

    def testReadSectorBeyondImageRaises(self):
        data = bytearray(2 * SECTOR_SIZE)  # Only 2 sectors.
        image = DFSImage(data, is_dsd=False)

        with pytest.raises(DFSFormatError, match="extends beyond the image"):
            image.sides[0]._readSector(5)

    # --- createDiscImage: invalid parameters ---

    def testCreateInvalidTrackCountRaises(self):
        with pytest.raises(ValueError, match="Track count must be 40 or 80"):
            createDiscImage(tracks=60)

    def testCreateInvalidBootOptionRaises(self):
        with pytest.raises(ValueError, match="Boot option must be 0-3"):
            createDiscImage(boot_option=5)

    # --- writeSector: wrong data size ---

    def testWriteSectorWrongSizeRaises(self):
        image = createDiscImage(tracks=40)
        side = image.sides[0]

        with pytest.raises(ValueError, match="exactly 256 bytes"):
            side._writeSector(2, b"\x00" * 100)

    # --- writeFile: data length mismatch ---

    def testWriteFileLengthMismatchRaises(self):
        image = createDiscImage(tracks=40)
        side = image.sides[0]
        entry = DFSEntry(
            name="TEST", directory="$",
            load_addr=0, exec_addr=0,
            length=16, start_sector=2, locked=False,
        )

        with pytest.raises(ValueError, match="does not match"):
            side.writeFile(entry, b"\x00" * 32)


class TestDFSEntry:
    """Unit tests for DFSEntry properties."""

    def testFullName(self):
        entry = DFSEntry(
            name="PROG", directory="T",
            load_addr=0, exec_addr=0,
            length=0, start_sector=2, locked=False,
        )
        assert entry.fullName == "T.PROG"

    def testIsBasicWithKnownExecAddresses(self):
        for addr in (0x801F, 0x8023, 0x802B):
            entry = DFSEntry(
                name="X", directory="$",
                load_addr=0x0E00, exec_addr=addr,
                length=100, start_sector=2, locked=False,
            )
            assert entry.isBasic, f"exec 0x{addr:04X} should be BASIC"

    def testIsBasicWithHighBitsMasked(self):
        # The top two bits of exec_addr flag I/O processor memory.
        # isBasic should mask them off.
        entry = DFSEntry(
            name="X", directory="$",
            load_addr=0x0E00, exec_addr=0x0003802B,
            length=100, start_sector=2, locked=False,
        )
        assert entry.isBasic

    def testIsNotBasicForBinary(self):
        entry = DFSEntry(
            name="BIN", directory="$",
            load_addr=0x1900, exec_addr=0x1900,
            length=100, start_sector=2, locked=False,
        )
        assert not entry.isBasic

    def testFrozenCannotMutate(self):
        entry = DFSEntry(
            name="X", directory="$",
            load_addr=0, exec_addr=0,
            length=0, start_sector=2, locked=False,
        )
        with pytest.raises(AttributeError):
            entry.name = "Y"


class TestCatalogueMetadata:
    """Tests for catalogue metadata fields parsed from synthetic images."""

    def testDiscTitle(self):
        data = _blankSsd()
        data[0:8] = b"HELLOSID"
        data[SECTOR_SIZE : SECTOR_SIZE + 4] = b"E\x00\x00\x00"
        image = DFSImage(data, is_dsd=False)
        cat = image.sides[0].readCatalogue()
        assert cat.title == "HELLOSIDE"

    def testDiscTitleStripsNulAndSpace(self):
        data = _blankSsd()
        data[0:8] = b"HI\x00\x00\x00\x00\x00\x00"
        image = DFSImage(data, is_dsd=False)
        cat = image.sides[0].readCatalogue()
        assert cat.title == "HI"

    def testCycleNumber(self):
        data = _blankSsd()
        data[SECTOR_SIZE + 4] = 0x42  # BCD 42.
        image = DFSImage(data, is_dsd=False)
        cat = image.sides[0].readCatalogue()
        assert cat.cycle == 0x42

    def testBootOption(self):
        data = _blankSsd()
        # Boot option in bits 4-5 of sec1[6]. Value 3 = EXEC.
        data[SECTOR_SIZE + 6] = 0x30
        image = DFSImage(data, is_dsd=False)
        cat = image.sides[0].readCatalogue()
        assert cat.boot_option == 3

    def testDiscSize(self):
        data = _blankSsd()
        # disc_size = sec1[7] | (sec1[6] bits 0-1 << 8).
        # Set to 800 = 0x320 -> sec1[7]=0x20, sec1[6] bits 0-1 = 3.
        data[SECTOR_SIZE + 6] = 0x03
        data[SECTOR_SIZE + 7] = 0x20
        image = DFSImage(data, is_dsd=False)
        cat = image.sides[0].readCatalogue()
        assert cat.disc_size == 800

    def testBootOptionEnum(self):
        assert BootOption(0).name == "OFF"
        assert BootOption(1).name == "LOAD"
        assert BootOption(2).name == "RUN"
        assert BootOption(3).name == "EXEC"

    def testEntryLocked(self):
        data = _ssdWithOneFile(locked=True)
        image = DFSImage(data, is_dsd=False)
        cat = image.sides[0].readCatalogue()
        assert cat.entries[0].locked is True

    def testEntryUnlocked(self):
        data = _ssdWithOneFile(locked=False)
        image = DFSImage(data, is_dsd=False)
        cat = image.sides[0].readCatalogue()
        assert cat.entries[0].locked is False

    def testEntryAddresses(self):
        data = _ssdWithOneFile(
            load_addr=0x1900, exec_addr=0x802B, length=256,
        )
        image = DFSImage(data, is_dsd=False)
        cat = image.sides[0].readCatalogue()
        e = cat.entries[0]
        assert e.load_addr == 0x1900
        assert e.exec_addr == 0x802B
        assert e.length == 256


class TestLooksLikeTokenizedBasic:
    """Unit tests for looksLikeTokenizedBasic()."""

    def testStartsWith0x0d(self):
        assert looksLikeTokenizedBasic(b"\x0D\x00\x0A\x05\xF1")

    def testDoesNotStartWith0x0d(self):
        assert not looksLikeTokenizedBasic(b"\x00\x0D\x0A")

    def testEmptyBytes(self):
        assert not looksLikeTokenizedBasic(b"")

    def testSingleByte0x0d(self):
        assert looksLikeTokenizedBasic(b"\x0D")


class TestSortCatalogueEntries:
    """Unit tests for sortCatalogueEntries() with DFSEntry objects."""

    def _entries(self):
        return [
            DFSEntry("ZEBRA", "$", 0, 0, 500, 10, False),
            DFSEntry("ALPHA", "$", 0, 0, 100, 20, False),
            DFSEntry("MIDDLE", "T", 0, 0, 300, 15, False),
        ]

    def testSortByName(self):
        from beebtools import sortCatalogueEntries
        result = sortCatalogueEntries(self._entries(), "name")
        names = [e.name for e in result]
        assert names == ["ALPHA", "MIDDLE", "ZEBRA"]

    def testSortByCatalog(self):
        from beebtools import sortCatalogueEntries
        result = sortCatalogueEntries(self._entries(), "catalog")
        names = [e.name for e in result]
        assert names == ["ZEBRA", "ALPHA", "MIDDLE"]

    def testSortBySize(self):
        from beebtools import sortCatalogueEntries
        result = sortCatalogueEntries(self._entries(), "size")
        lengths = [e.length for e in result]
        assert lengths == [100, 300, 500]


class TestCreateDiscImage:
    """Tests for createDiscImage() and round-tripping."""

    def testCreate40TrackSsd(self):
        image = createDiscImage(tracks=40, is_dsd=False)
        assert len(image.data) == 40 * 10 * SECTOR_SIZE
        assert len(image.sides) == 1

    def testCreate80TrackDsd(self):
        image = createDiscImage(tracks=80, is_dsd=True)
        assert len(image.data) == 80 * 20 * SECTOR_SIZE
        assert len(image.sides) == 2

    def testBlankCatalogueIsEmpty(self):
        image = createDiscImage(tracks=80, title="TEST")
        cat = image.sides[0].readCatalogue()
        assert cat.title == "TEST"
        assert cat.entries == ()
        assert cat.boot_option == 0

    def testBootOptionPreserved(self):
        image = createDiscImage(tracks=80, boot_option=3)
        cat = image.sides[0].readCatalogue()
        assert cat.boot_option == 3

    def testDiscSizeMatchesTracks(self):
        image = createDiscImage(tracks=40)
        cat = image.sides[0].readCatalogue()
        assert cat.disc_size == 400  # 40 * 10

        image = createDiscImage(tracks=80)
        cat = image.sides[0].readCatalogue()
        assert cat.disc_size == 800  # 80 * 10

    def testDsdBothSidesReadable(self):
        image = createDiscImage(tracks=80, is_dsd=True, title="DUAL")
        for side in image.sides:
            cat = side.readCatalogue()
            assert cat.title == "DUAL"
            assert cat.entries == ()

    def testSerializeReturnsBytes(self):
        image = createDiscImage(tracks=40)
        raw = image.serialize()
        assert isinstance(raw, bytes)
        assert len(raw) == 40 * 10 * SECTOR_SIZE


class TestWriteReadRoundTrip:
    """Round-trip tests: write a catalogue then read it back."""

    def testRoundTripEmptyCatalogue(self):
        image = createDiscImage(tracks=80, title="ROUND", boot_option=2)
        cat = image.sides[0].readCatalogue()

        # Write it back and re-read.
        image.sides[0].writeCatalogue(cat)
        cat2 = image.sides[0].readCatalogue()

        assert cat2.title == "ROUND"
        assert cat2.boot_option == 2
        assert cat2.entries == ()

    def testRoundTripWithEntries(self):
        image = createDiscImage(tracks=80, title="FILES")
        side = image.sides[0]

        entry = DFSEntry(
            name="HELLO", directory="$",
            load_addr=0x0E00, exec_addr=0x802B,
            length=10, start_sector=2, locked=True,
        )
        cat = DFSCatalogue(
            title="FILES", cycle=0x01,
            boot_option=1, disc_size=800,
            entries=(entry,),
        )

        # Write file data.
        side.writeFile(entry, b"\x0D" * 10)

        # Write catalogue.
        side.writeCatalogue(cat)

        # Read back.
        cat2 = side.readCatalogue()
        assert len(cat2.entries) == 1
        e = cat2.entries[0]
        assert e.name == "HELLO"
        assert e.directory == "$"
        assert e.load_addr == 0x0E00
        assert e.exec_addr == 0x802B
        assert e.length == 10
        assert e.start_sector == 2
        assert e.locked is True

        # Verify file data.
        data = side.readFile(e)
        assert data == b"\x0D" * 10

    def testBcdIncrement(self):
        from beebtools.dfs import DFSSide
        assert DFSSide._bcdIncrement(0x00) == 0x01
        assert DFSSide._bcdIncrement(0x09) == 0x10
        assert DFSSide._bcdIncrement(0x19) == 0x20
        assert DFSSide._bcdIncrement(0x99) == 0x00
        assert DFSSide._bcdIncrement(0x42) == 0x43


class TestValidateDfsName:
    """Tests for the validateDfsName function."""

    def testValidNameAccepted(self):
        validateDfsName("$", "BOOT")

    def testValidNameNonDefaultDir(self):
        validateDfsName("T", "MYPROG")

    def testSingleCharNameAccepted(self):
        validateDfsName("$", "A")

    def testSevenCharNameAccepted(self):
        validateDfsName("$", "ABCDEFG")

    def testMaxPrintableDir(self):
        # Tilde is 0x7E, the highest valid printable ASCII.
        validateDfsName("~", "FILE")

    def testExclamationDir(self):
        # 0x21, the lowest valid directory character.
        validateDfsName("!", "BOOT")

    def testEmptyDirectoryRejected(self):
        with pytest.raises(DFSError):
            validateDfsName("", "FILE")

    def testMultiCharDirectoryRejected(self):
        with pytest.raises(DFSError):
            validateDfsName("AB", "FILE")

    def testSpaceDirectoryRejected(self):
        with pytest.raises(DFSError):
            validateDfsName(" ", "FILE")

    def testControlCharDirectoryRejected(self):
        with pytest.raises(DFSError):
            validateDfsName("\x01", "FILE")

    def testDelDirectoryRejected(self):
        # 0x7F is DEL, just above printable range.
        with pytest.raises(DFSError):
            validateDfsName("\x7F", "FILE")

    def testEmptyNameRejected(self):
        with pytest.raises(DFSError):
            validateDfsName("$", "")

    def testNameTooLongRejected(self):
        with pytest.raises(DFSError):
            validateDfsName("$", "ABCDEFGH")

    def testNameWithControlCharRejected(self):
        with pytest.raises(DFSError):
            validateDfsName("$", "A\x00B")

    def testNameWithHighBitRejected(self):
        # 0x80 is beyond printable ASCII.
        with pytest.raises(DFSError):
            validateDfsName("$", "A\x80B")

    def testNameWithSpaceRejected(self):
        # Space is forbidden in DFS filenames per the spec.
        with pytest.raises(DFSError):
            validateDfsName("$", "A B")

    def testNameWithDotRejected(self):
        with pytest.raises(DFSError):
            validateDfsName("$", "A.B")

    def testNameWithColonRejected(self):
        with pytest.raises(DFSError):
            validateDfsName("$", "A:B")

    def testNameWithQuoteRejected(self):
        with pytest.raises(DFSError):
            validateDfsName("$", 'A"B')

    def testNameWithHashRejected(self):
        with pytest.raises(DFSError):
            validateDfsName("$", "A#B")

    def testNameWithStarRejected(self):
        with pytest.raises(DFSError):
            validateDfsName("$", "A*B")

    def testForbiddenDirectoryDotRejected(self):
        with pytest.raises(DFSError):
            validateDfsName(".", "FILE")

    def testForbiddenDirectoryHashRejected(self):
        with pytest.raises(DFSError):
            validateDfsName("#", "FILE")


# ---------------------------------------------------------------------------
# Free space tests
# ---------------------------------------------------------------------------

class TestFreeSpace:

    def testEmptyDiscHasFullFreeSpace(self):
        # 80-track disc: 800 sectors, minus 2 for catalogue = 798 free.
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        assert side.freeSpace() == 798 * SECTOR_SIZE

    def testFortyTrackDisc(self):
        image = createDiscImage(tracks=40)
        side = image.sides[0]
        assert side.freeSpace() == 398 * SECTOR_SIZE

    def testOneFileReducesFreeSpace(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        side.addFile("TEST", "$", b"\xAA" * 512)

        # 512 bytes = 2 sectors. File at sectors 798-799.
        # Free: sectors 2-797 = 796 sectors.
        assert side.freeSpace() == 796 * SECTOR_SIZE

    def testTwoFilesReduceFreeSpace(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        side.addFile("FILE1", "$", b"\xAA" * 256)
        side.addFile("FILE2", "$", b"\xBB" * 256)

        # Two 1-sector files: 798+799 occupied. Free: 2-797 = 796 sectors.
        assert side.freeSpace() == 796 * SECTOR_SIZE

    def testDeletedMiddleFileDoesNotFreespace(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]

        # Add three files: A (1 sector), B (1 sector), C (1 sector).
        side.addFile("AFILE", "$", b"\xAA" * 256)
        side.addFile("BFILE", "$", b"\xBB" * 256)
        side.addFile("CFILE", "$", b"\xCC" * 256)

        # Free space before delete: 800 - 2 - 3 = 795 sectors.
        assert side.freeSpace() == 795 * SECTOR_SIZE

        # Delete middle file (BFILE).
        side.deleteFile("BFILE", "$")

        # CFILE is still the lowest. Free space unchanged.
        assert side.freeSpace() == 795 * SECTOR_SIZE

    def testDeleteLowestFileFreesSpace(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        side.addFile("AFILE", "$", b"\xAA" * 256)
        side.addFile("BFILE", "$", b"\xBB" * 256)

        # Delete the lowest file (BFILE, added second).
        side.deleteFile("BFILE", "$")

        # Only AFILE remains (1 sector). Free: 800 - 2 - 1 = 797 sectors.
        assert side.freeSpace() == 797 * SECTOR_SIZE


# ---------------------------------------------------------------------------
# addFile tests
# ---------------------------------------------------------------------------

class TestAddFile:

    def testAddOneFile(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]

        entry = side.addFile("HELLO", "$", b"\x0D" * 100, load_addr=0x0E00, exec_addr=0x802B)

        assert entry.name == "HELLO"
        assert entry.directory == "$"
        assert entry.length == 100
        assert entry.load_addr == 0x0E00
        assert entry.exec_addr == 0x802B
        assert entry.locked is False

    def testAddFileAppearsInCatalogue(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        side.addFile("PROG", "T", b"\xAA" * 50)

        cat = side.readCatalogue()
        assert len(cat.entries) == 1
        assert cat.entries[0].name == "PROG"
        assert cat.entries[0].directory == "T"

    def testAddFileDataReadBack(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        file_data = bytes(range(200))
        side.addFile("DATA", "$", file_data)

        cat = side.readCatalogue()
        read_back = side.readFile(cat.entries[0])
        assert read_back == file_data

    def testAddMultipleFiles(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]

        side.addFile("FILE1", "$", b"\x01" * 100)
        side.addFile("FILE2", "$", b"\x02" * 200)
        side.addFile("FILE3", "T", b"\x03" * 300)

        cat = side.readCatalogue()
        assert len(cat.entries) == 3

        # Entries should be in descending start sector order.
        sectors = [e.start_sector for e in cat.entries]
        assert sectors == sorted(sectors, reverse=True)

    def testAddFileDataIntegrity(self):
        # Add multiple files and verify all data reads back correctly.
        image = createDiscImage(tracks=80)
        side = image.sides[0]

        data1 = b"\xDE\xAD" * 128
        data2 = b"\xBE\xEF" * 256
        data3 = b"\xCA\xFE" * 64

        side.addFile("FILE1", "$", data1)
        side.addFile("FILE2", "$", data2)
        side.addFile("FILE3", "T", data3)

        cat = side.readCatalogue()
        names = {e.name: e for e in cat.entries}

        assert side.readFile(names["FILE1"]) == data1
        assert side.readFile(names["FILE2"]) == data2
        assert side.readFile(names["FILE3"]) == data3

    def testAddLockedFile(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        side.addFile("SECRET", "$", b"\xFF" * 10, locked=True)

        cat = side.readCatalogue()
        assert cat.entries[0].locked is True

    def testAddZeroLengthFile(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        entry = side.addFile("EMPTY", "$", b"")

        assert entry.length == 0

        cat = side.readCatalogue()
        assert len(cat.entries) == 1
        assert side.readFile(cat.entries[0]) == b""

    def testCycleNumberIncremented(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]

        cat_before = side.readCatalogue()
        assert cat_before.cycle == 0

        side.addFile("TEST", "$", b"\xAA")

        cat_after = side.readCatalogue()
        assert cat_after.cycle == 1

    def testDuplicateNameRejected(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        side.addFile("PROG", "$", b"\xAA" * 10)

        with pytest.raises(DFSError, match="already exists"):
            side.addFile("PROG", "$", b"\xBB" * 10)

    def testSameNameDifferentDirAllowed(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        side.addFile("PROG", "$", b"\xAA" * 10)
        side.addFile("PROG", "T", b"\xBB" * 10)

        cat = side.readCatalogue()
        assert len(cat.entries) == 2

    def testCatalogueFullRejected(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]

        # Fill the catalogue to 31 files.
        for i in range(31):
            name = f"F{i:02d}"[:7]
            side.addFile(name, "$", b"\xAA")

        cat = side.readCatalogue()
        assert len(cat.entries) == 31

        with pytest.raises(DFSError, match="full"):
            side.addFile("EXTRA", "$", b"\xBB")

    def testDiscFullRejected(self):
        image = createDiscImage(tracks=40)
        side = image.sides[0]

        # 40-track disc: 400 sectors, 398 usable. Fill most of it.
        big_data = b"\xAA" * (398 * SECTOR_SIZE)
        side.addFile("BIG", "$", big_data)

        # Disc is now full.
        with pytest.raises(DFSError, match="free space"):
            side.addFile("TINY", "$", b"\xBB")

    def testInvalidNameRejected(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]

        with pytest.raises(DFSError):
            side.addFile("TOOLONGNAME", "$", b"\xAA")

    def testExactFitSucceeds(self):
        image = createDiscImage(tracks=40)
        side = image.sides[0]

        # Fill disc leaving exactly 1 sector free.
        big_data = b"\xAA" * (397 * SECTOR_SIZE)
        side.addFile("BIG", "$", big_data)

        # Should succeed - exactly 1 sector (256 bytes) available.
        side.addFile("TINY", "$", b"\xBB" * 256)

        assert side.freeSpace() == 0


# ---------------------------------------------------------------------------
# deleteFile tests
# ---------------------------------------------------------------------------

class TestDeleteFile:

    def testDeleteOnlyFile(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        side.addFile("DOOMED", "$", b"\xAA" * 100)

        removed = side.deleteFile("DOOMED", "$")

        assert removed.name == "DOOMED"
        cat = side.readCatalogue()
        assert len(cat.entries) == 0

    def testDeleteFromMultipleFiles(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        side.addFile("KEEP1", "$", b"\xAA" * 100)
        side.addFile("REMOVE", "$", b"\xBB" * 100)
        side.addFile("KEEP2", "T", b"\xCC" * 100)

        side.deleteFile("REMOVE", "$")

        cat = side.readCatalogue()
        names = [e.name for e in cat.entries]
        assert "KEEP1" in names
        assert "KEEP2" in names
        assert "REMOVE" not in names
        assert len(cat.entries) == 2

    def testDeleteNonexistentRaisesError(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]

        with pytest.raises(DFSError, match="not found"):
            side.deleteFile("GHOST", "$")

    def testDeleteWrongDirRaisesError(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        side.addFile("PROG", "$", b"\xAA" * 10)

        with pytest.raises(DFSError, match="not found"):
            side.deleteFile("PROG", "T")

    def testDeletePreservesOtherFileData(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]

        data_keep = b"\xDE\xAD" * 128
        side.addFile("KEEPER", "$", data_keep)
        side.addFile("DOOMED", "$", b"\xFF" * 100)

        side.deleteFile("DOOMED", "$")

        cat = side.readCatalogue()
        assert len(cat.entries) == 1
        assert side.readFile(cat.entries[0]) == data_keep

    def testDeleteCycleIncremented(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        side.addFile("TEST", "$", b"\xAA")

        cycle_before = side.readCatalogue().cycle
        side.deleteFile("TEST", "$")
        cycle_after = side.readCatalogue().cycle

        assert cycle_after == DFSSide._bcdIncrement(cycle_before)

    def testAddAfterDeleteReusesSpace(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]

        # Add and delete a file, then add a new one. The new file
        # should be able to use the freed space.
        side.addFile("FIRST", "$", b"\xAA" * 256)
        free_after_add = side.freeSpace()

        side.deleteFile("FIRST", "$")
        free_after_del = side.freeSpace()

        assert free_after_del > free_after_add

        side.addFile("SECOND", "$", b"\xBB" * 256)
        assert side.freeSpace() == free_after_add


# ---------------------------------------------------------------------------
# compact tests
# ---------------------------------------------------------------------------

class TestCompact:

    def testCompactEmptyDisc(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        freed = side.compact()
        assert freed == 0

    def testCompactNoGaps(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        side.addFile("FILE1", "$", b"\xAA" * 256)
        side.addFile("FILE2", "$", b"\xBB" * 256)

        freed = side.compact()
        assert freed == 0

    def testCompactReclaimsGap(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]

        # Add three 1-sector files, delete the middle one.
        side.addFile("TOP", "$", b"\x01" * 256)
        side.addFile("MID", "$", b"\x02" * 256)
        side.addFile("BOT", "$", b"\x03" * 256)

        free_before_del = side.freeSpace()
        side.deleteFile("MID", "$")

        # Free space didn't change because BOT is still lowest.
        assert side.freeSpace() == free_before_del

        # Compact should reclaim the 1-sector gap.
        freed = side.compact()
        assert freed == 1 * SECTOR_SIZE

    def testCompactPreservesFileData(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]

        data_top = b"\xDE\xAD" * 128
        data_mid = b"\xBE\xEF" * 128
        data_bot = b"\xCA\xFE" * 128

        side.addFile("TOP", "$", data_top)
        side.addFile("MID", "$", data_mid)
        side.addFile("BOT", "$", data_bot)

        # Delete MID, compact, and verify all remaining data intact.
        side.deleteFile("MID", "$")
        side.compact()

        cat = side.readCatalogue()
        names = {e.name: e for e in cat.entries}

        assert side.readFile(names["TOP"]) == data_top
        assert side.readFile(names["BOT"]) == data_bot

    def testCompactMultipleGaps(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]

        # Add five 1-sector files, delete two non-adjacent ones.
        for i in range(5):
            side.addFile(f"F{i}", "$", b"\xAA" * 256)

        side.deleteFile("F1", "$")
        side.deleteFile("F3", "$")

        freed = side.compact()
        assert freed == 2 * SECTOR_SIZE

        # After compact, 3 remaining files packed at top with no gaps.
        cat = side.readCatalogue()
        assert len(cat.entries) == 3
        sectors = [e.start_sector for e in cat.entries]
        assert sectors == sorted(sectors, reverse=True)

        # Verify files are contiguous: highest start + sectors = disc_size,
        # and each next file starts where the previous ends.
        for i in range(len(cat.entries) - 1):
            e_high = cat.entries[i]
            e_low = cat.entries[i + 1]
            high_sectors = (e_high.length + SECTOR_SIZE - 1) // SECTOR_SIZE
            assert e_low.start_sector + ((e_low.length + SECTOR_SIZE - 1) // SECTOR_SIZE) == e_high.start_sector

    def testCompactLargeFile(self):
        image = createDiscImage(tracks=80)
        side = image.sides[0]

        # Add a big file at the top, a small one in the middle,
        # then a medium one at the bottom. Delete the small one.
        side.addFile("BIG", "$", b"\xAA" * (10 * SECTOR_SIZE))
        side.addFile("SMALL", "$", b"\xBB" * (2 * SECTOR_SIZE))
        side.addFile("MEDIUM", "$", b"\xCC" * (5 * SECTOR_SIZE))

        data_big = side.readFile(side.readCatalogue().entries[0])
        data_med = b"\xCC" * (5 * SECTOR_SIZE)

        side.deleteFile("SMALL", "$")
        freed = side.compact()

        assert freed == 2 * SECTOR_SIZE

        # Verify data integrity after moving.
        cat = side.readCatalogue()
        names = {e.name: e for e in cat.entries}
        assert side.readFile(names["BIG"]) == data_big
        assert side.readFile(names["MEDIUM"]) == data_med

    def testCompactMakesSpaceForNewFile(self):
        image = createDiscImage(tracks=40)
        side = image.sides[0]

        # Fill disc nearly completely with 3 files.
        # 40-track = 400 sectors, 398 usable.
        side.addFile("A", "$", b"\x01" * (196 * SECTOR_SIZE))
        side.addFile("B", "$", b"\x02" * (100 * SECTOR_SIZE))
        side.addFile("C", "$", b"\x03" * (100 * SECTOR_SIZE))

        # Free: 398 - 196 - 100 - 100 = 2 sectors.
        assert side.freeSpace() == 2 * SECTOR_SIZE

        # Delete B (100 sectors), but C is below it so free space stays 2.
        side.deleteFile("B", "$")
        assert side.freeSpace() == 2 * SECTOR_SIZE

        # Can't add a 50-sector file yet.
        with pytest.raises(DFSError, match="free space"):
            side.addFile("NEW", "$", b"\x04" * (50 * SECTOR_SIZE))

        # Compact reclaims the 100 sectors.
        freed = side.compact()
        assert freed == 100 * SECTOR_SIZE

        # Now the 50-sector file fits.
        side.addFile("NEW", "$", b"\x04" * (50 * SECTOR_SIZE))

        cat = side.readCatalogue()
        assert len(cat.entries) == 3


# ---------------------------------------------------------------------------
# Round-trip: create, add files, read back (3.10)
# ---------------------------------------------------------------------------

class TestAddFileRoundTrip:

    def testCreateAddReadRoundTrip(self):
        # Build a disc from scratch, add several files, serialize, reopen,
        # and verify every file reads back correctly.
        image = createDiscImage(tracks=80, title="ROUND", boot_option=2)
        side = image.sides[0]

        files = {
            ("$", "BOOT"):   b"*RUN MENU\r",
            ("$", "MENU"):   b"\x0D" * 500,
            ("T", "SONG1"):  b"\xAA" * 2000,
            ("T", "SONG2"):  b"\xBB" * 3000,
            ("$", "README"): b"Hello BBC\rworld\r",
        }

        for (d, n), data in files.items():
            side.addFile(n, d, data, load_addr=0x0E00, exec_addr=0x802B)

        # Serialize to bytes and reopen as a new image.
        raw = image.serialize()
        reopened = DFSImage(bytearray(raw), is_dsd=False)
        side2 = reopened.sides[0]

        cat = side2.readCatalogue()
        assert cat.title == "ROUND"
        assert cat.boot_option == 2
        assert len(cat.entries) == 5

        # Verify every file's data.
        by_name = {(e.directory, e.name): e for e in cat.entries}
        for (d, n), expected_data in files.items():
            actual = side2.readFile(by_name[(d, n)])
            assert actual == expected_data, f"Mismatch for {d}.{n}"

    def testDsdAddFileBothSides(self):
        image = createDiscImage(tracks=80, is_dsd=True, title="DUAL")
        side0 = image.sides[0]
        side1 = image.sides[1]

        data0 = b"\xDE\xAD" * 100
        data1 = b"\xBE\xEF" * 200

        side0.addFile("PROG", "$", data0)
        side1.addFile("PROG", "$", data1)

        # Both sides have one file, data does not collide.
        assert side0.readFile(side0.readCatalogue().entries[0]) == data0
        assert side1.readFile(side1.readCatalogue().entries[0]) == data1
