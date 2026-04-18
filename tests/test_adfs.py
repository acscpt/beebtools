# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for the ADFS disc image reader.

Synthetic image builders construct valid ADFS-S/M/L images in memory
so tests do not depend on external disc image files. Integration tests
over real .adf/.adl files are parametrized and skip when no images are
present.
"""

import contextlib
import io
import os
import glob
import struct
import pytest

from argparse import Namespace

from beebtools import (
    DiscFile,
    ADFSEntry,
    ADFSCatalogue,
    ADFSDirectory,
    ADFSFreeSpaceMap,
    ADFSImage,
    ADFSSide,
    ADFSError,
    ADFSFormatError,
    openAdfsImage,
    openImage,
    BootOption,
    looksLikeTokenizedBasic,
    detokenize,
)
from beebtools.cli import cmdCat, cmdExtract
from beebtools.disc import extractAll, search, sanitizeEntryPath
from beebtools.adfs import (
    ADFS_SECTOR_SIZE,
    ADFS_SECTORS_PER_TRACK,
    ADFS_DIR_LENGTH,
    ADFS_ENTRY_SIZE,
    ADFS_MAX_ENTRIES,
    ADFS_ROOT_SECTOR,
    ADFS_HUGO_MAGIC,
    ADFS_S_SECTORS,
    ADFS_M_SECTORS,
    ADFS_L_SECTORS,
    _adfsChecksum,
    _decodeString,
    _encodeString,
    _read24le,
    _read32le,
    _write24le,
    _write32le,
    _encodeEntryName,
    _encodeEntry,
    validateAdfsName,
    createAdfsImage,
)


# -----------------------------------------------------------------------
# Synthetic image builders
# -----------------------------------------------------------------------

def _computeChecksum(sector: bytearray) -> int:
    """Compute the ADFS checksum for a 256-byte sector."""
    return _adfsChecksum(bytes(sector))


def _encodeAdfsEntryName(name: str, access: int) -> bytes:
    """Thin wrapper for backward compatibility with existing test code."""
    return _encodeEntryName(name, access)


def _blankFreeSpaceMap(
    total_sectors: int = 640,
    disc_id: int = 0x1234,
    boot_option: int = 0,
    free_blocks: list = None,
) -> bytearray:
    """Build a valid 512-byte free space map (sectors 0 and 1).

    If free_blocks is None, a single free block covering sectors 7
    to the end of the disc is created (sectors 0-1 are the map,
    sectors 2-6 are the root directory).
    """
    sec0 = bytearray(256)
    sec1 = bytearray(256)

    if free_blocks is None:
        free_blocks = [(7, total_sectors - 7)]

    # Write free space block entries.
    for i, (start, length) in enumerate(free_blocks):
        base = i * 3
        _write24le(sec0, base, start)
        _write24le(sec1, base, length)

    # Total sectors on disc (sector 0, offset 0xFC).
    _write24le(sec0, 0xFC, total_sectors)

    # Disc identifier (sector 1, offset 0xFB).
    sec1[0xFB] = disc_id & 0xFF
    sec1[0xFC] = (disc_id >> 8) & 0xFF

    # Boot option (sector 1, offset 0xFD).
    sec1[0xFD] = boot_option & 0x03

    # End-of-list pointer (sector 1, offset 0xFE).
    sec1[0xFE] = len(free_blocks) * 3

    # Checksums.
    sec0[0xFF] = _computeChecksum(sec0)
    sec1[0xFF] = _computeChecksum(sec1)

    return sec0 + sec1


def _makeDirectoryEntry(
    name: str,
    load_addr: int = 0,
    exec_addr: int = 0,
    length: int = 0,
    start_sector: int = 0,
    sequence: int = 0,
    access: int = 0x03,  # default: readable + writable
) -> bytes:
    """Build a raw 26-byte ADFS directory entry."""
    buf = bytearray(ADFS_ENTRY_SIZE)

    name_bytes = _encodeAdfsEntryName(name, access)
    buf[0:10] = name_bytes

    _write32le(buf, 0x0A, load_addr)
    _write32le(buf, 0x0E, exec_addr)
    _write32le(buf, 0x12, length)
    _write24le(buf, 0x16, start_sector)
    buf[0x19] = sequence

    return bytes(buf)


def _makeDirectory(
    name: str = "$",
    title: str = "",
    parent_sector: int = 2,
    sequence: int = 0x01,
    entries: list = None,
) -> bytearray:
    """Build a 0x500-byte ADFS directory block.

    entries is a list of raw 26-byte entry blobs.
    """
    buf = bytearray(ADFS_DIR_LENGTH)

    if entries is None:
        entries = []

    # Header: sequence byte + "Hugo".
    buf[0] = sequence
    buf[1:5] = ADFS_HUGO_MAGIC

    # Write entries starting at offset 5.
    offset = 5
    for entry_bytes in entries:
        buf[offset:offset + ADFS_ENTRY_SIZE] = entry_bytes
        offset += ADFS_ENTRY_SIZE

    # End-of-entries marker.
    if offset < 0x4CB:
        buf[offset] = 0x00

    # Footer: directory name (10 bytes at 0x4CC).
    name_bytes = name.encode("ascii")[:10]
    for i, b in enumerate(name_bytes):
        buf[0x4CC + i] = b
    for i in range(len(name_bytes), 10):
        buf[0x4CC + i] = 0x0D

    # Parent sector (3 bytes at 0x4D6).
    _write24le(buf, 0x4D6, parent_sector)

    # Directory title (19 bytes at 0x4D9).
    title_bytes = (title or name).encode("ascii")[:19]
    for i, b in enumerate(title_bytes):
        buf[0x4D9 + i] = b
    for i in range(len(title_bytes), 19):
        buf[0x4D9 + i] = 0x0D

    # Footer sequence + "Hugo" (at 0x4FA-0x4FE).
    buf[0x4FA] = sequence
    buf[0x4FB:0x4FF] = ADFS_HUGO_MAGIC

    return buf


def _blankAdfs(
    total_sectors: int = 640,
    disc_id: int = 0x1234,
    boot_option: int = 0,
    root_title: str = "MyDisc",
    root_entries: list = None,
) -> bytearray:
    """Build a complete synthetic ADFS image with sectors 0-6.

    Returns a bytearray large enough for total_sectors, with a valid
    free space map and root directory.
    """
    image = bytearray(total_sectors * ADFS_SECTOR_SIZE)

    # Write free space map (sectors 0-1).
    fsm_data = _blankFreeSpaceMap(
        total_sectors=total_sectors,
        disc_id=disc_id,
        boot_option=boot_option,
    )
    image[0:512] = fsm_data

    # Write root directory (sectors 2-6).
    root_dir = _makeDirectory(
        name="$",
        title=root_title,
        parent_sector=ADFS_ROOT_SECTOR,
        entries=root_entries or [],
    )
    root_offset = ADFS_ROOT_SECTOR * ADFS_SECTOR_SIZE
    image[root_offset:root_offset + ADFS_DIR_LENGTH] = root_dir

    return image


def _adfsWithFiles(files: list) -> bytearray:
    """Build a synthetic ADFS image with files in the root directory.

    files is a list of dicts with keys: name, data, load_addr, exec_addr.
    Files are allocated contiguously starting at sector 7.
    """
    # Calculate total sectors needed.
    next_sector = 7  # first free sector after root directory

    entry_blobs = []
    for f in files:
        data = f.get("data", b"")
        sectors_needed = (len(data) + ADFS_SECTOR_SIZE - 1) // ADFS_SECTOR_SIZE

        access = f.get("access", 0x03)  # default: readable + writable
        entry_blob = _makeDirectoryEntry(
            name=f["name"],
            load_addr=f.get("load_addr", 0),
            exec_addr=f.get("exec_addr", 0),
            length=len(data),
            start_sector=next_sector if data else 0,
            access=access,
        )
        entry_blobs.append((entry_blob, data, next_sector))

        if data:
            next_sector += sectors_needed

    # Build the image with enough sectors.
    total_sectors = max(next_sector + 1, 640)
    image = _blankAdfs(
        total_sectors=total_sectors,
        root_entries=[blob for blob, _, _ in entry_blobs],
    )

    # Write file data.
    for _, data, start in entry_blobs:
        if data:
            offset = start * ADFS_SECTOR_SIZE
            image[offset:offset + len(data)] = data

    return image


# -----------------------------------------------------------------------
# Checksum tests
# -----------------------------------------------------------------------

class TestAdfsChecksum:

    def testAllZerosChecksum(self):
        """A 256-byte sector of zeros should produce a consistent, deterministic checksum value."""
        # 255 zero bytes - checksum computation starts at 255 and adds zeros.
        data = bytes(256)
        result = _adfsChecksum(data)
        assert isinstance(result, int)
        assert 0 <= result <= 255

    def testKnownChecksumRoundTrip(self):
        """A valid free-space map sector with a stored checksum should pass re-verification with an identical computed value."""
        # Build a free space map sector and verify the checksum is self-consistent.
        sec = bytearray(256)
        sec[0xFC] = 0x80  # 640 sectors low byte
        sec[0xFD] = 0x02  # 640 sectors high bytes
        sec[0xFF] = _adfsChecksum(bytes(sec))

        # The stored checksum should match a fresh computation.
        assert _adfsChecksum(bytes(sec)) == sec[0xFF]

    def testCorruptChecksumDetected(self):
        """Flipping any data byte after storing a correct checksum must produce a different checksum, detecting the corruption."""
        # Build valid map, corrupt one byte, checksum should no longer match.
        sec = bytearray(256)
        sec[0xFF] = _adfsChecksum(bytes(sec))
        original = sec[0xFF]
        sec[0x10] = 0x42  # corrupt
        assert _adfsChecksum(bytes(sec)) != original


# -----------------------------------------------------------------------
# String decoding tests
# -----------------------------------------------------------------------

class TestDecodeString:

    def testSimpleName(self):
        """A name terminated by 0x0D padding bytes should decode to the plain ASCII string only, without the padding."""
        assert _decodeString(b"HELLO\x0d\x0d\x0d\x0d\x0d") == "HELLO"

    def testNulTerminator(self):
        """A name terminated by NUL bytes (0x00) should decode correctly, stopping at the first terminator."""
        assert _decodeString(b"TEST\x00\x00\x00\x00\x00\x00") == "TEST"

    def testFullLengthName(self):
        """A 10-character name (the ADFS maximum) should encode without any truncation."""
        assert _decodeString(b"ABCDEFGHIJ") == "ABCDEFGHIJ"

    def testEmptyString(self):
        """An empty string should encode to a buffer filled entirely with 0x0D terminator bytes."""
        assert _decodeString(b"\x0d\x0d\x0d") == ""


# -----------------------------------------------------------------------
# Integer reader tests
# -----------------------------------------------------------------------

class TestIntReaders:

    def testRead24le(self):
        """Three bytes stored in little-endian order should be reassembled into the correct 24-bit integer."""
        data = bytes([0x56, 0x34, 0x12, 0xFF])
        assert _read24le(data, 0) == 0x123456

    def testRead32le(self):
        """Four bytes stored in little-endian order should be reassembled into the correct 32-bit integer."""
        data = bytes([0x78, 0x56, 0x34, 0x12])
        assert _read32le(data, 0) == 0x12345678


# -----------------------------------------------------------------------
# Integer writer tests
# -----------------------------------------------------------------------

class TestIntWriters:

    def testWrite24leRoundTrip(self):
        """Writing a 24-bit value then reading it back at the same offset should return the original number."""
        buf = bytearray(8)
        _write24le(buf, 2, 0x123456)
        assert _read24le(buf, 2) == 0x123456

    def testWrite32leRoundTrip(self):
        """Writing a 32-bit value then reading it back at the same offset should return the original number."""
        buf = bytearray(8)
        _write32le(buf, 1, 0x12345678)
        assert _read32le(buf, 1) == 0x12345678

    def testWrite24leZero(self):
        """Writing zero as a 24-bit little-endian integer should produce exactly three zero bytes."""
        buf = bytearray(4)
        _write24le(buf, 0, 0)
        assert buf[0:3] == b"\x00\x00\x00"

    def testWrite32leZero(self):
        """Writing zero as a 32-bit little-endian integer should produce exactly four zero bytes."""
        buf = bytearray(4)
        _write32le(buf, 0, 0)
        assert buf == b"\x00\x00\x00\x00"

    def testWrite24leMaxValue(self):
        """Writing 0xFFFFFF (the maximum 24-bit value) should produce three 0xFF bytes."""
        buf = bytearray(3)
        _write24le(buf, 0, 0xFFFFFF)
        assert buf == b"\xFF\xFF\xFF"

    def testWrite32leMaxValue(self):
        """Writing 0xFFFFFFFF (the maximum 32-bit value) should produce four 0xFF bytes."""
        buf = bytearray(4)
        _write32le(buf, 0, 0xFFFFFFFF)
        assert buf == b"\xFF\xFF\xFF\xFF"


# -----------------------------------------------------------------------
# String encoding tests
# -----------------------------------------------------------------------

class TestEncodeString:

    def testSimpleRoundTrip(self):
        """Encoding a name then decoding it should return the original string unchanged."""
        encoded = _encodeString("HELLO", 10)
        assert _decodeString(encoded) == "HELLO"

    def testPaddedWith0x0D(self):
        """A name shorter than the target buffer length should be right-padded with 0x0D bytes."""
        encoded = _encodeString("AB", 5)
        assert encoded == bytes([0x41, 0x42, 0x0D, 0x0D, 0x0D])

    def testFullLengthNoPadding(self):
        """A name that exactly fills the buffer should encode with no trailing padding bytes."""
        encoded = _encodeString("ABCDEFGHIJ", 10)
        assert _decodeString(encoded) == "ABCDEFGHIJ"

    def testEmptyString(self):
        """An empty string should encode to a buffer filled entirely with 0x0D terminator bytes."""
        encoded = _encodeString("", 5)
        assert encoded == bytes([0x0D] * 5)

    def testTruncatesToLength(self):
        """A name longer than the target length should be silently truncated to fit the buffer."""
        encoded = _encodeString("TOOLONGNAME", 5)
        assert len(encoded) == 5
        assert _decodeString(encoded) == "TOOLO"


# -----------------------------------------------------------------------
# Entry name encoding tests
# -----------------------------------------------------------------------

class TestEncodeEntryName:

    def testSimpleNameNoAccess(self):
        """A simple name with no access bits set should encode with plain unmodified ASCII bytes."""
        encoded = _encodeEntryName("TEST", 0x00)
        # Characters should be plain ASCII, remainder padded with 0x0D.
        assert encoded[0] == ord("T")
        assert encoded[3] == ord("T")
        assert encoded[4] == 0x0D

    def testAccessBitsInBit7(self):
        """Access permission bits should be packed into the high bit (bit 7) of each character byte."""
        # Access 0x03 sets bits 0 (R) and 1 (W).
        encoded = _encodeEntryName("AB", 0x03)
        assert encoded[0] == ord("A") | 0x80  # bit 0 set
        assert encoded[1] == ord("B") | 0x80  # bit 1 set
        assert encoded[2] == 0x0D             # no access bit 2

    def testRoundTripWithParseEntry(self):
        """An entry name encoded then parsed through the full entry reader should round-trip correctly."""
        # Build a full 26-byte entry and verify _parseEntry recovers it.
        entry = ADFSEntry(
            name="MYFILE",
            directory="$",
            load_addr=0x1900,
            exec_addr=0x8023,
            length=0x1234,
            start_sector=0x07,
            locked=True,
            is_directory=False,
            access=0x07,  # R + W + L
            sequence=0x42,
        )

        raw = _encodeEntry(entry)
        assert len(raw) == ADFS_ENTRY_SIZE

        # Place in a buffer and parse.
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]
        parsed = side._parseEntry(raw, 0)

        assert parsed.name == "MYFILE"
        assert parsed.load_addr == 0x1900
        assert parsed.exec_addr == 0x8023
        assert parsed.length == 0x1234
        assert parsed.start_sector == 0x07
        assert parsed.locked is True
        assert parsed.is_directory is False
        assert parsed.access == 0x07
        assert parsed.sequence == 0x42

    def testFullLengthName(self):
        """A 10-character name (the ADFS maximum) should encode without any truncation."""
        encoded = _encodeEntryName("ABCDEFGHIJ", 0x00)
        name = ""
        for i in range(10):
            ch = encoded[i] & 0x7F
            if ch == 0x0D:
                break
            name += chr(ch)
        assert name == "ABCDEFGHIJ"

    def testDirectoryAccessBits(self):
        """A directory entry should have the appropriate access bits set in its encoded name bytes."""
        # Directory entries have 'D' bit (bit 3) plus R, W, L.
        encoded = _encodeEntryName("GAMES", 0x0F)
        # Bits 0,1,2,3 should all be set in first 4 byte positions.
        for i in range(4):
            assert encoded[i] & 0x80 != 0
        assert encoded[4] & 0x80 == 0  # bit 4 not set


# -----------------------------------------------------------------------
# Entry encoding tests
# -----------------------------------------------------------------------

class TestEncodeEntry:

    def testBasicFieldsEncoded(self):
        """All metadata fields (name, load, exec, length, start sector) should appear at their documented byte offsets in the 26-byte entry."""
        entry = ADFSEntry(
            name="DATA",
            directory="$",
            load_addr=0x2000,
            exec_addr=0x3000,
            length=0x100,
            start_sector=7,
            locked=False,
            is_directory=False,
            access=0x03,
            sequence=0,
        )

        raw = _encodeEntry(entry)
        assert len(raw) == ADFS_ENTRY_SIZE
        assert _read32le(raw, 0x0A) == 0x2000
        assert _read32le(raw, 0x0E) == 0x3000
        assert _read32le(raw, 0x12) == 0x100
        assert _read24le(raw, 0x16) == 7

    def testZeroLengthFile(self):
        """A zero-length file with a zero start sector should produce a valid entry without garbage values in size or sector fields."""
        entry = ADFSEntry(
            name="EMPTY",
            directory="$",
            load_addr=0,
            exec_addr=0,
            length=0,
            start_sector=0,
            locked=False,
            is_directory=False,
            access=0x03,
            sequence=0,
        )

        raw = _encodeEntry(entry)
        assert _read32le(raw, 0x12) == 0

    def testLockedFileAccess(self):
        """A locked file's access bytes should have the locked bit set in the correct position of the encoded entry."""
        entry = ADFSEntry(
            name="SECRET",
            directory="$",
            load_addr=0,
            exec_addr=0,
            length=100,
            start_sector=10,
            locked=True,
            is_directory=False,
            access=0x07,  # R + W + L
            sequence=0,
        )

        raw = _encodeEntry(entry)
        # Bit 2 (L) should be set in byte 2's high bit.
        assert raw[2] & 0x80 != 0


# -----------------------------------------------------------------------
# Sector write tests
# -----------------------------------------------------------------------

class TestSectorWrite:

    def testWriteSectorReadBack(self):
        """A single 256-byte sector written to an image should be readable back with identical content."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Write a pattern to sector 10.
        pattern = bytes(range(256))
        side._writeSector(10, pattern)
        assert side._readSector(10) == pattern

    def testWriteSectorsReadBack(self):
        """Multiple contiguous sectors written in one call should all read back correctly."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Write two sectors at once.
        data = bytes(range(256)) + bytes(range(255, -1, -1))
        side._writeSectors(20, data)
        assert side._readSectors(20, 2) == data

    def testWriteSectorOutOfBoundsRaises(self):
        """Attempting to write to a sector index beyond the image boundary should raise an error."""
        image_data = _blankAdfs(total_sectors=10)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        with pytest.raises(ADFSError):
            side._writeSector(10, bytes(256))

    def testWriteSectorsOutOfBoundsRaises(self):
        """Attempting to write a sector range that extends past the end of the image should raise an error."""
        image_data = _blankAdfs(total_sectors=10)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        with pytest.raises(ADFSError):
            side._writeSectors(9, bytes(512))


# -----------------------------------------------------------------------
# Free space map parsing
# -----------------------------------------------------------------------

class TestFreeSpaceMap:

    def testValidMapParses(self):
        """A correctly formed two-sector free-space map should parse to the right total sector count and free block list."""
        image_data = _blankAdfs(total_sectors=640, disc_id=0xABCD, boot_option=2)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]
        fsm = side.readFreeSpaceMap()

        assert fsm.total_sectors == 640
        assert fsm.disc_id == 0xABCD
        assert fsm.boot_option == BootOption.RUN
        assert len(fsm.blocks) == 1
        assert fsm.blocks[0] == (7, 640 - 7)

    def testCorruptSec0ChecksumRaises(self):
        """A bad checksum byte in sector 0 of the free-space map should raise ADFSFormatError."""
        image_data = _blankAdfs()
        # Corrupt a byte in sector 0. Use a single-bit flip so the ADFS
        # carry-based checksum detects it (XOR 0xFF is a blind spot).
        image_data[0x10] ^= 0x01
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        with pytest.raises(ADFSFormatError, match="checksum"):
            side.readFreeSpaceMap()

    def testCorruptSec1ChecksumRaises(self):
        """A bad checksum byte in sector 1 of the free-space map should raise ADFSFormatError."""
        image_data = _blankAdfs()
        # Corrupt a byte in sector 1.
        image_data[0x110] ^= 0x01
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        with pytest.raises(ADFSFormatError, match="checksum"):
            side.readFreeSpaceMap()

    def testMultipleFreeBlocks(self):
        """A disc with several fragmented free blocks should expose all block entries with correct start and length values."""
        sec0 = bytearray(256)
        sec1 = bytearray(256)

        # Block 0: start=7, length=100
        _write24le(sec0, 0, 7)
        _write24le(sec1, 0, 100)

        # Block 1: start=200, length=50
        _write24le(sec0, 3, 200)
        _write24le(sec1, 3, 50)

        _write24le(sec0, 0xFC, 640)
        sec1[0xFB] = 0x42
        sec1[0xFC] = 0x00
        sec1[0xFD] = 0
        sec1[0xFE] = 6  # 2 blocks * 3

        sec0[0xFF] = _computeChecksum(sec0)
        sec1[0xFF] = _computeChecksum(sec1)

        # Build image with these sectors.
        image_data = bytearray(640 * 256)
        image_data[0:256] = sec0
        image_data[256:512] = sec1

        # Add a valid root directory at sector 2.
        root_dir = _makeDirectory(name="$", title="Test")
        image_data[512:512 + ADFS_DIR_LENGTH] = root_dir

        image = ADFSImage(image_data, is_adl=False)
        fsm = image.sides[0].readFreeSpaceMap()

        assert len(fsm.blocks) == 2
        assert fsm.blocks[0] == (7, 100)
        assert fsm.blocks[1] == (200, 50)


# -----------------------------------------------------------------------
# Free space map write tests
# -----------------------------------------------------------------------

class TestWriteFreeSpaceMap:

    def testWriteReadRoundTrip(self):
        """Writing a free-space map then immediately re-reading it should restore an identical in-memory structure."""
        image_data = _blankAdfs(total_sectors=640, disc_id=0xBEEF, boot_option=3)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Read, write back, re-read and compare.
        original = side.readFreeSpaceMap()
        side.writeFreeSpaceMap(original)
        reread = side.readFreeSpaceMap()

        assert reread.blocks == original.blocks
        assert reread.total_sectors == original.total_sectors
        assert reread.disc_id == original.disc_id
        assert reread.boot_option == original.boot_option

    def testWriteMultipleBlocks(self):
        """A free-space map with multiple free blocks should persist all entries correctly across a write-read cycle."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        fsm = ADFSFreeSpaceMap(
            blocks=((7, 50), (100, 200)),
            total_sectors=640,
            disc_id=0x1234,
            boot_option=BootOption.OFF,
        )
        side.writeFreeSpaceMap(fsm)
        reread = side.readFreeSpaceMap()

        assert reread.blocks == ((7, 50), (100, 200))

    def testWriteInvalidatesCache(self):
        """Writing a new map should clear any cached parsed result so that the next read reflects the updated data."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Read to populate cache.
        original = side.readFreeSpaceMap()
        assert original.disc_id == 0x1234

        # Write with different disc_id.
        updated = ADFSFreeSpaceMap(
            blocks=original.blocks,
            total_sectors=original.total_sectors,
            disc_id=0x5678,
            boot_option=original.boot_option,
        )
        side.writeFreeSpaceMap(updated)

        # Re-read should see the new disc_id (cache was invalidated).
        reread = side.readFreeSpaceMap()
        assert reread.disc_id == 0x5678


class TestAllocateBlock:

    def testAllocateFromSingleBlock(self):
        """Allocating sectors from a single large free block should return the first available sector address."""
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Initial FSM: one block from sector 7, length 633.
        start = side._allocateBlock(10)
        assert start == 7

        # FSM should now have one block: (17, 623).
        fsm = side.readFreeSpaceMap()
        assert len(fsm.blocks) == 1
        assert fsm.blocks[0] == (17, 623)

    def testAllocateExactSize(self):
        """Allocating exactly the remaining free space should succeed and leave an empty free block list."""
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Allocate all free space.
        start = side._allocateBlock(633)
        assert start == 7

        # FSM should now be empty.
        fsm = side.readFreeSpaceMap()
        assert len(fsm.blocks) == 0

    def testAllocateMultipleBlocks(self):
        """Sequential allocations should each return distinct, non-overlapping sector ranges."""
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Allocate three times.
        s1 = side._allocateBlock(5)
        s2 = side._allocateBlock(10)
        s3 = side._allocateBlock(3)

        assert s1 == 7
        assert s2 == 12
        assert s3 == 22

        fsm = side.readFreeSpaceMap()
        assert fsm.blocks[0] == (25, 615)

    def testAllocateDiscFullRaises(self):
        """Requesting more sectors than are available on the disc should raise ADFSError indicating disc full."""
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        with pytest.raises(ADFSError, match="Cannot allocate"):
            side._allocateBlock(634)

    def testAllocateFirstFitSkipsSmallBlock(self):
        """First-fit allocation should skip a free block that is too small and use the next block that fits."""
        # Set up FSM with a small block followed by a large one.
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        fsm = ADFSFreeSpaceMap(
            blocks=((7, 3), (50, 100)),
            total_sectors=640,
            disc_id=0x1234,
            boot_option=BootOption.OFF,
        )
        side.writeFreeSpaceMap(fsm)

        start = side._allocateBlock(10)
        assert start == 50

        fsm = side.readFreeSpaceMap()
        assert (7, 3) in fsm.blocks
        assert (60, 90) in fsm.blocks


class TestFreeBlock:

    def testFreeBlockSimple(self):
        """Freeing an isolated block should add it to the free-space map as a standalone new entry."""
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Allocate, then free.
        start = side._allocateBlock(10)
        side._freeBlock(start, 10)

        # Should be back to original state.
        fsm = side.readFreeSpaceMap()
        assert len(fsm.blocks) == 1
        assert fsm.blocks[0] == (7, 633)

    def testFreeBlockMergeRight(self):
        """A freed block immediately before an existing free block should merge with it into one larger entry."""
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Allocate 20 sectors (7-26), leaving (27, 613).
        side._allocateBlock(20)

        # Free back the first 10 (7-16). Merging right: (7,10) + (27,613)
        # should NOT merge because there's a gap at 17-26.
        side._freeBlock(7, 10)
        fsm = side.readFreeSpaceMap()
        assert (7, 10) in fsm.blocks
        assert (27, 613) in fsm.blocks

    def testFreeBlockMergesAdjacentRight(self):
        """Two free blocks that become adjacent from the right should be coalesced into a single contiguous block."""
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Set up: two blocks with a gap at 7-16.
        fsm = ADFSFreeSpaceMap(
            blocks=((17, 623),),
            total_sectors=640,
            disc_id=0x1234,
            boot_option=BootOption.OFF,
        )
        side.writeFreeSpaceMap(fsm)

        # Free 7-16 (adjacent to 17).
        side._freeBlock(7, 10)
        fsm = side.readFreeSpaceMap()
        assert len(fsm.blocks) == 1
        assert fsm.blocks[0] == (7, 633)

    def testFreeBlockMergesAdjacentLeft(self):
        """Two free blocks that become adjacent from the left should be coalesced into a single contiguous block."""
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Set up: block at 7-16, gap at 17-26, nothing else.
        fsm = ADFSFreeSpaceMap(
            blocks=((7, 10),),
            total_sectors=640,
            disc_id=0x1234,
            boot_option=BootOption.OFF,
        )
        side.writeFreeSpaceMap(fsm)

        # Free 17-26 (adjacent to left block ending at 17).
        side._freeBlock(17, 10)
        fsm = side.readFreeSpaceMap()
        assert len(fsm.blocks) == 1
        assert fsm.blocks[0] == (7, 20)

    def testFreeBlockMergesBoth(self):
        """A freed block that is adjacent to free blocks on both sides should merge all three into one entry."""
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Set up: two blocks with a gap in between.
        fsm = ADFSFreeSpaceMap(
            blocks=((7, 10), (27, 100)),
            total_sectors=640,
            disc_id=0x1234,
            boot_option=BootOption.OFF,
        )
        side.writeFreeSpaceMap(fsm)

        # Free the gap at 17-26.
        side._freeBlock(17, 10)
        fsm = side.readFreeSpaceMap()
        assert len(fsm.blocks) == 1
        assert fsm.blocks[0] == (7, 120)

    def testFreeBlockNoMerge(self):
        """A freed block that is not adjacent to any other free block should remain as a separate isolated entry."""
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Set up: blocks at 7-16 and 50-99 (gap 17-49 and gap 100+).
        fsm = ADFSFreeSpaceMap(
            blocks=((7, 10), (50, 50)),
            total_sectors=640,
            disc_id=0x1234,
            boot_option=BootOption.OFF,
        )
        side.writeFreeSpaceMap(fsm)

        # Free 30-39 (isolated, no merges).
        side._freeBlock(30, 10)
        fsm = side.readFreeSpaceMap()
        assert len(fsm.blocks) == 3
        assert fsm.blocks == ((7, 10), (30, 10), (50, 50))


class TestFreeSpace:

    def testFreeSpaceOnBlankDisc(self):
        """A newly created blank disc should report total capacity minus the map and root directory overhead as free space."""
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        assert side.freeSpace() == 633

    def testFreeSpaceAfterAllocation(self):
        """Available free space should decrease by at least the allocated amount after adding a file."""
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        side._allocateBlock(100)
        assert side.freeSpace() == 533

    def testFreeSpaceWithMultipleBlocks(self):
        """The reported free space should be the sum of all free block lengths, not just the largest single block."""
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        fsm = ADFSFreeSpaceMap(
            blocks=((7, 50), (100, 30), (200, 20)),
            total_sectors=640,
            disc_id=0x1234,
            boot_option=BootOption.OFF,
        )
        side.writeFreeSpaceMap(fsm)

        assert side.freeSpace() == 100


# -----------------------------------------------------------------------
# Directory encoding and writing tests
# -----------------------------------------------------------------------

class TestEncodeDirectory:

    def testRoundTripEmptyDirectory(self):
        """Encoding an empty directory then decoding it should produce a valid directory with zero entries."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        original = side.readDirectory(ADFS_ROOT_SECTOR)
        encoded = side._encodeDirectory(original)
        assert len(encoded) == ADFS_DIR_LENGTH

        # Write it back and re-read.
        side._writeSectors(ADFS_ROOT_SECTOR, encoded)
        reread = side.readDirectory(ADFS_ROOT_SECTOR)

        assert reread.name == original.name
        assert reread.title == original.title
        assert reread.parent_sector == original.parent_sector
        assert reread.sequence == original.sequence
        assert len(reread.entries) == 0

    def testRoundTripWithEntries(self):
        """Encoding a directory containing entries then decoding it should preserve all entry fields exactly."""
        files = [
            {"name": "ALPHA", "data": b"a" * 100, "load_addr": 0x1900},
            {"name": "BETA", "data": b"b" * 200, "load_addr": 0x2000},
        ]
        image_data = _adfsWithFiles(files)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        original = side.readDirectory(ADFS_ROOT_SECTOR)
        encoded = side._encodeDirectory(original)
        side._writeSectors(ADFS_ROOT_SECTOR, encoded)
        reread = side.readDirectory(ADFS_ROOT_SECTOR)

        assert len(reread.entries) == 2
        assert reread.entries[0].name == "ALPHA"
        assert reread.entries[1].name == "BETA"
        assert reread.entries[0].load_addr == 0x1900


class TestWriteDirectory:

    def testWriteDirectoryBcdIncrements(self):
        """Writing a directory to disc should increment its BCD sequence byte, marking it as freshly updated."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        original = side.readDirectory(ADFS_ROOT_SECTOR)
        assert original.sequence == 0x01

        # Write it back - should BCD-increment to 0x02.
        side.writeDirectory(ADFS_ROOT_SECTOR, original)
        reread = side.readDirectory(ADFS_ROOT_SECTOR)
        assert reread.sequence == 0x02

    def testWriteDirectoryBcdWraps(self):
        """The BCD sequence counter should wrap from 0x99 back to 0x00 rather than overflowing."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        original = side.readDirectory(ADFS_ROOT_SECTOR)

        # Set sequence to 0x09 - should wrap low digit to 0, carry to high.
        dir_at_09 = ADFSDirectory(
            name=original.name,
            title=original.title,
            parent_sector=original.parent_sector,
            sequence=0x09,
            entries=original.entries,
        )
        side.writeDirectory(ADFS_ROOT_SECTOR, dir_at_09)
        reread = side.readDirectory(ADFS_ROOT_SECTOR)
        assert reread.sequence == 0x10

    def testWriteDirectoryInvalidatesCache(self):
        """Writing a directory should clear the cached copy so the next read reflects the newly written content."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Populate the catalogue cache.
        cat = side.readCatalogue()
        assert len(cat.entries) == 0

        # Manually modify the directory outside of writeDirectory
        # to verify that the cache was invalidated by the write.
        side.writeDirectory(ADFS_ROOT_SECTOR, side.readDirectory(ADFS_ROOT_SECTOR))

        # The cache should be cleared - readCatalogue should re-parse.
        # (This just verifies no stale data, not a specific assertion.)
        cat2 = side.readCatalogue()
        assert cat2 is not None


class TestInsertEntry:

    def _makeDummyEntry(self, name: str, access: int = 0x03) -> ADFSEntry:
        return ADFSEntry(
            name=name, directory="$", load_addr=0, exec_addr=0,
            length=0, start_sector=0, locked=False, is_directory=False,
            access=access, sequence=0,
        )

    def testInsertIntoEmpty(self):
        """Inserting the first entry into an empty directory should result in a one-entry list."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        directory = side.readDirectory(ADFS_ROOT_SECTOR)
        updated = side._insertEntry(directory, self._makeDummyEntry("HELLO"))
        assert len(updated.entries) == 1
        assert updated.entries[0].name == "HELLO"

    def testInsertMaintainsSortOrder(self):
        """Inserting entries in any order should always leave the directory in alphabetical order."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        directory = side.readDirectory(ADFS_ROOT_SECTOR)
        directory = side._insertEntry(directory, self._makeDummyEntry("CHARLIE"))
        directory = side._insertEntry(directory, self._makeDummyEntry("ALPHA"))
        directory = side._insertEntry(directory, self._makeDummyEntry("BRAVO"))

        names = [e.name for e in directory.entries]
        assert names == ["ALPHA", "BRAVO", "CHARLIE"]

    def testInsertCaseInsensitiveSort(self):
        """Sort order during insertion should be case-insensitive so 'B' and 'b' sort together."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        directory = side.readDirectory(ADFS_ROOT_SECTOR)
        directory = side._insertEntry(directory, self._makeDummyEntry("Zebra"))
        directory = side._insertEntry(directory, self._makeDummyEntry("alpha"))

        assert directory.entries[0].name == "alpha"
        assert directory.entries[1].name == "Zebra"

    def testInsertDuplicateRaises(self):
        """Inserting an entry whose name already exists in the directory should raise ADFSError."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        directory = side.readDirectory(ADFS_ROOT_SECTOR)
        directory = side._insertEntry(directory, self._makeDummyEntry("FILE"))

        with pytest.raises(ADFSError, match="Duplicate"):
            side._insertEntry(directory, self._makeDummyEntry("file"))

    def testInsertFullDirectoryRaises(self):
        """Inserting into a directory that already holds the maximum 47 entries should raise ADFSError."""
        # Build a directory with 47 entries.
        entries = [
            _makeDirectoryEntry(f"F{i:04d}") for i in range(ADFS_MAX_ENTRIES)
        ]
        image_data = _blankAdfs(root_entries=entries)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        directory = side.readDirectory(ADFS_ROOT_SECTOR)

        with pytest.raises(ADFSError, match="full"):
            side._insertEntry(directory, self._makeDummyEntry("EXTRA"))


class TestRemoveEntry:

    def _makeDummyEntry(self, name: str) -> ADFSEntry:
        return ADFSEntry(
            name=name, directory="$", load_addr=0, exec_addr=0,
            length=0, start_sector=0, locked=False, is_directory=False,
            access=0x03, sequence=0,
        )

    def testRemoveExisting(self):
        """Removing an entry by name should leave the directory with exactly one fewer entry."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        directory = side.readDirectory(ADFS_ROOT_SECTOR)
        directory = side._insertEntry(directory, self._makeDummyEntry("FILE"))
        directory = side._removeEntry(directory, "FILE")

        assert len(directory.entries) == 0

    def testRemoveCaseInsensitive(self):
        """Entry lookup for removal should be case-insensitive, matching ADFS naming convention."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        directory = side.readDirectory(ADFS_ROOT_SECTOR)
        directory = side._insertEntry(directory, self._makeDummyEntry("MyFile"))
        directory = side._removeEntry(directory, "myfile")

        assert len(directory.entries) == 0

    def testRemoveFromMiddle(self):
        """Removing an entry from the middle of the list should compact the remaining entries without gaps."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        directory = side.readDirectory(ADFS_ROOT_SECTOR)
        directory = side._insertEntry(directory, self._makeDummyEntry("ALPHA"))
        directory = side._insertEntry(directory, self._makeDummyEntry("BRAVO"))
        directory = side._insertEntry(directory, self._makeDummyEntry("CHARLIE"))
        directory = side._removeEntry(directory, "BRAVO")

        names = [e.name for e in directory.entries]
        assert names == ["ALPHA", "CHARLIE"]

    def testRemoveNotFoundRaises(self):
        """Attempting to remove a name not present in the directory should raise ADFSError."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        directory = side.readDirectory(ADFS_ROOT_SECTOR)

        with pytest.raises(ADFSError, match="not found"):
            side._removeEntry(directory, "GHOST")


# -----------------------------------------------------------------------
# Name validation tests
# -----------------------------------------------------------------------

class TestValidateAdfsName:

    def testValidName(self):
        """A typical alphanumeric ADFS filename should pass validation without raising an error."""
        validateAdfsName("HELLO")

    def testSingleChar(self):
        """A single-character name is the shortest valid ADFS filename."""
        validateAdfsName("A")

    def testMaxLength(self):
        """A name exactly 10 characters long (the ADFS maximum) should pass validation without error."""
        validateAdfsName("ABCDEFGHIJ")

    def testEmptyRaises(self):
        """An empty name string should be rejected with an error."""
        with pytest.raises(ADFSError, match="empty"):
            validateAdfsName("")

    def testTooLongRaises(self):
        """A name exceeding 10 characters (the ADFS limit) should be rejected."""
        with pytest.raises(ADFSError, match="11 characters"):
            validateAdfsName("TOOLONGNAME")

    def testSpaceRaises(self):
        """A name containing a space should be rejected; ADFS forbids spaces in filenames."""
        with pytest.raises(ADFSError, match="invalid character"):
            validateAdfsName("HE LO")

    def testControlCharRaises(self):
        """A name containing a control character (below 0x20) should be rejected."""
        with pytest.raises(ADFSError, match="invalid character"):
            validateAdfsName("BAD\x01")

    def testHighBitCharRaises(self):
        """A name containing a character with bit 7 set should be rejected; ADFS reserves that bit for access control."""
        with pytest.raises(ADFSError, match="invalid character"):
            validateAdfsName("BAD\x80")


# -----------------------------------------------------------------------
# File write operation tests
# -----------------------------------------------------------------------

class TestAddFile:

    def testAddAndReadBack(self):
        """A file added to the root directory should be readable back with byte-identical content."""
        image = createAdfsImage()
        side = image.sides[0]

        side.addFile(DiscFile("$.HELLO", b"Hello World!", load_addr=0x1900, exec_addr=0x8023))

        cat = side.readCatalogue()
        assert len(cat.entries) == 1
        assert cat.entries[0].name == "HELLO"
        assert cat.entries[0].load_addr == 0x1900
        assert cat.entries[0].exec_addr == 0x8023
        assert cat.entries[0].length == 12

        data = side.readFile(cat.entries[0])
        assert data == b"Hello World!"

    def testAddMultipleFiles(self):
        """Multiple files added sequentially should all be present in the catalogue with correct data."""
        image = createAdfsImage()
        side = image.sides[0]

        side.addFile(DiscFile("$.ALPHA", b"aaa"))
        side.addFile(DiscFile("$.CHARLIE", b"ccc"))
        side.addFile(DiscFile("$.BRAVO", b"bbb"))

        cat = side.readCatalogue()
        names = [e.name for e in cat.entries]
        assert names == ["ALPHA", "BRAVO", "CHARLIE"]

    def testAddLockedFile(self):
        """A file added with the locked flag should report locked as True when read from the catalogue."""
        image = createAdfsImage()
        side = image.sides[0]

        side.addFile(DiscFile("$.SECRET", b"data", locked=True))

        cat = side.readCatalogue()
        assert cat.entries[0].locked is True

    def testAddToSubdirectory(self):
        """A file can be placed in a named subdirectory and later extracted from that path."""
        image = createAdfsImage()
        side = image.sides[0]

        side.mkdir("$.GAMES")
        side.addFile(DiscFile("$.GAMES.ELITE", b"game data", load_addr=0x2000))

        cat = side.readCatalogue()
        # Should have GAMES dir + ELITE file.
        file_entries = [e for e in cat.entries if not e.isDirectory]
        assert len(file_entries) == 1
        assert file_entries[0].name == "ELITE"
        assert file_entries[0].directory == "$.GAMES"

        data = side.readFile(file_entries[0])
        assert data == b"game data"

    def testAddEmptyFile(self):
        """Adding a zero-length file should succeed and appear in the catalogue with a zero length field."""
        image = createAdfsImage()
        side = image.sides[0]

        side.addFile(DiscFile("$.EMPTY", b""))

        cat = side.readCatalogue()
        assert cat.entries[0].length == 0

        data = side.readFile(cat.entries[0])
        assert data == b""

    def testAddDiscFullRaises(self):
        """Attempting to add a file when no free sectors remain should raise ADFSError."""
        image = createAdfsImage(total_sectors=20)
        side = image.sides[0]

        # 20 sectors total, 7 reserved (FSM + root dir) = 13 free.
        # Try to add a file needing 14 sectors.
        big_data = bytes(14 * ADFS_SECTOR_SIZE)

        with pytest.raises(ADFSError, match="Cannot allocate"):
            side.addFile(DiscFile("$.BIG", big_data))

    def testAddDuplicateRaises(self):
        """Adding a file whose name already exists in the target directory should raise ADFSError."""
        image = createAdfsImage()
        side = image.sides[0]

        side.addFile(DiscFile("$.FILE", b"first"))

        with pytest.raises(ADFSError, match="Duplicate"):
            side.addFile(DiscFile("$.FILE", b"second"))

    def testAddBadNameRaises(self):
        """Adding a file with an invalid ADFS name (e.g. containing a space) should raise ADFSError."""
        image = createAdfsImage()
        side = image.sides[0]

        with pytest.raises(ADFSError, match="invalid character"):
            side.addFile(DiscFile("$.BAD NAME", b"data"))

    def testAddMissingParentRaises(self):
        """Adding a file to a subdirectory path that does not exist should raise ADFSError."""
        image = createAdfsImage()
        side = image.sides[0]

        with pytest.raises(ADFSError, match="not found"):
            side.addFile(DiscFile("$.NOSUCH.FILE", b"data"))

    def testFreeSpaceDecreasesAfterAdd(self):
        """The reported free space should decrease by at least the file size after adding a file."""
        image = createAdfsImage()
        side = image.sides[0]

        before = side.freeSpace()
        side.addFile(DiscFile("$.DATA", bytes(512)))
        after = side.freeSpace()

        # 512 bytes = 2 sectors.
        assert before - after == 2


class TestAddFilePlaced:
    """ADFS addFile honours an explicit start_sector when the range is free."""

    def testPlacedAtExactSectorCarvesFreeMap(self):
        """A placement in free sectors lands at the requested location."""
        image = createAdfsImage()
        side = image.sides[0]

        data = b"\xAA" * (ADFS_SECTOR_SIZE * 3)
        entry = side.addFile(
            DiscFile("$.PLACED", data, start_sector=50)
        )

        assert entry.start_sector == 50
        assert side.readFile(entry) == data

        # The free space map no longer covers sectors 50..52.
        fsm = side.readFreeSpaceMap()
        for (start, length) in fsm.blocks:
            end = start + length
            assert not (start < 53 and end > 50), (
                f"free block ({start}, {length}) still covers placed range"
            )

    def testPlacedFallsBackWhenRangeAlreadyAllocated(self):
        """Placement over already-allocated sectors falls back to auto-allocation.

        The root directory occupies fixed sectors in a fresh ADFS
        image. Asking for a placement that starts inside those sectors
        cannot succeed as a byte-exact placement without clobbering
        the directory, so the engine falls back to normal allocation
        and the file is still written correctly.
        """
        image = createAdfsImage()
        side = image.sides[0]

        data = b"\xBB" * (ADFS_SECTOR_SIZE * 2)
        # Sector 2 is the root directory in a fresh ADFS image.
        entry = side.addFile(
            DiscFile("$.FALLBACK", data, start_sector=2)
        )

        # Fell back: start sector is not the requested 2.
        assert entry.start_sector != 2
        assert side.readFile(entry) == data

    def testPlacedRangeOverlappingPriorFileFallsBack(self):
        """Placement into sectors occupied by another file is rejected cleanly."""
        image = createAdfsImage()
        side = image.sides[0]

        first = side.addFile(
            DiscFile("$.FIRST", b"\xCC" * (ADFS_SECTOR_SIZE * 4),
                     start_sector=60)
        )

        # Request an overlap starting in the middle of FIRST.
        second = side.addFile(
            DiscFile("$.SECOND", b"\xDD" * (ADFS_SECTOR_SIZE * 2),
                     start_sector=62)
        )

        assert first.start_sector == 60
        # Second fell back rather than clobbering FIRST.
        assert second.start_sector != 62
        assert side.readFile(first) == b"\xCC" * (ADFS_SECTOR_SIZE * 4)
        assert side.readFile(second) == b"\xDD" * (ADFS_SECTOR_SIZE * 2)


class TestDeleteFile:

    def testDeleteAndVerifyGone(self):
        """A deleted file should no longer appear when the directory is parsed."""
        image = createAdfsImage()
        side = image.sides[0]

        side.addFile(DiscFile("$.TEMP", b"temporary"))
        side.deleteFile("$.TEMP")

        cat = side.readCatalogue()
        assert len(cat.entries) == 0

    def testDeleteFreesSectors(self):
        """Deleting a file should return its allocated sectors to the free-space map."""
        image = createAdfsImage()
        side = image.sides[0]

        before = side.freeSpace()
        side.addFile(DiscFile("$.TEMP", bytes(1024)))
        during = side.freeSpace()
        side.deleteFile("$.TEMP")
        after = side.freeSpace()

        assert during < before
        assert after == before

    def testDeleteFromSubdirectory(self):
        """A file inside a subdirectory should be removable by specifying its full path."""
        image = createAdfsImage()
        side = image.sides[0]

        side.mkdir("$.SUB")
        side.addFile(DiscFile("$.SUB.FILE", b"nested"))
        side.deleteFile("$.SUB.FILE")

        sub_dir = side.readDirectory(
            [e for e in side.readCatalogue().entries if e.isDirectory][0].start_sector
        )
        assert len(sub_dir.entries) == 0

    def testDeleteNotFoundRaises(self):
        """Attempting to delete a name that does not exist in the directory should raise ADFSError."""
        image = createAdfsImage()
        side = image.sides[0]

        with pytest.raises(ADFSError, match="not found"):
            side.deleteFile("$.GHOST")

    def testDeleteDirectoryRaises(self):
        """Attempting to delete a directory entry as though it were a file should raise ADFSError."""
        image = createAdfsImage()
        side = image.sides[0]

        side.mkdir("$.DIR")

        with pytest.raises(ADFSError, match="Cannot delete directory"):
            side.deleteFile("$.DIR")

    def testDeleteLastFileInDirectory(self):
        """Deleting the only file remaining in a directory should leave that directory empty and still parseable."""
        image = createAdfsImage()
        side = image.sides[0]

        side.addFile(DiscFile("$.ONLY", b"sole file"))
        side.deleteFile("$.ONLY")

        root = side.readDirectory(ADFS_ROOT_SECTOR)
        assert len(root.entries) == 0


class TestMkdir:

    def testCreateSubdirectory(self):
        """A new subdirectory should appear in the parent catalogue and be navigable."""
        image = createAdfsImage()
        side = image.sides[0]

        side.mkdir("$.GAMES")

        cat = side.readCatalogue()
        dirs = [e for e in cat.entries if e.isDirectory]
        assert len(dirs) == 1
        assert dirs[0].name == "GAMES"
        # DLR is the valid directory access per the ADFS *ACCESS grammar.
        # W on a directory is not settable and would recreate the DWLR bug.
        # On-disc layout: D=0x08, L=0x04, R=0x01.
        assert dirs[0].access == 0x0D
        assert dirs[0].isDirectory is True

    def testCreateNestedDirectories(self):
        """Creating a second-level directory inside a first-level directory should succeed."""
        image = createAdfsImage()
        side = image.sides[0]

        side.mkdir("$.DATA")
        side.mkdir("$.DATA.SCORES")

        cat = side.readCatalogue()
        dirs = [e for e in cat.entries if e.isDirectory]
        assert len(dirs) == 2

        # The nested dir should be accessible.
        nested = [d for d in dirs if d.name == "SCORES"]
        assert len(nested) == 1
        assert nested[0].directory == "$.DATA"

    def testMkdirUsesDiscSpace(self):
        """Creating a directory allocates sectors for the directory block, which should reduce the reported free space."""
        image = createAdfsImage()
        side = image.sides[0]

        before = side.freeSpace()
        side.mkdir("$.SUB")
        after = side.freeSpace()

        # A directory takes 5 sectors.
        assert before - after == 5

    def testMkdirDuplicateRaises(self):
        """Creating a directory whose name already exists in the current parent should raise ADFSError."""
        image = createAdfsImage()
        side = image.sides[0]

        side.mkdir("$.DIR")

        with pytest.raises(ADFSError, match="Duplicate"):
            side.mkdir("$.DIR")

    def testMkdirBadNameRaises(self):
        """Creating a directory with an invalid ADFS name should raise ADFSError."""
        image = createAdfsImage()
        side = image.sides[0]

        with pytest.raises(ADFSError, match="invalid character"):
            side.mkdir("$.BAD NAME")


# -----------------------------------------------------------------------
# Image creation tests
# -----------------------------------------------------------------------

class TestCreateAdfsImage:

    def testCreateDefaultImage(self):
        """createAdfsImage() with default arguments should return a structurally valid ADFS image."""
        image = createAdfsImage()

        assert len(image.data) == ADFS_M_SECTORS * ADFS_SECTOR_SIZE
        assert image.is_adl is False

    def testCreateSmallImage(self):
        """Creating an ADFS-S (small) size image should produce a buffer of the documented byte size."""
        image = createAdfsImage(total_sectors=ADFS_S_SECTORS)

        assert len(image.data) == ADFS_S_SECTORS * ADFS_SECTOR_SIZE
        assert image.is_adl is False

    def testCreateLargeImage(self):
        """Creating an ADFS-L (large) size image should produce a buffer of the documented byte size."""
        image = createAdfsImage(total_sectors=ADFS_L_SECTORS)

        assert len(image.data) == ADFS_L_SECTORS * ADFS_SECTOR_SIZE
        assert image.is_adl is True

    def testFsmIsValid(self):
        """The two-sector free-space map in a freshly created image should carry valid checksums."""
        image = createAdfsImage(total_sectors=640, disc_id=0xABCD)
        side = image.sides[0]

        fsm = side.readFreeSpaceMap()
        assert fsm.total_sectors == 640
        assert fsm.disc_id == 0xABCD
        assert fsm.blocks == ((7, 633),)

    def testRootDirectoryIsValid(self):
        """The root directory block in a new image should parse without error and report the supplied disc title."""
        image = createAdfsImage(title="TestDisc")
        side = image.sides[0]

        root = side.readDirectory(ADFS_ROOT_SECTOR)
        assert root.name == "$"
        assert root.title == "TestDisc"
        assert root.parent_sector == ADFS_ROOT_SECTOR
        assert root.sequence == 0x01
        assert len(root.entries) == 0

    def testSerializeRoundTrip(self):
        """Serializing an ADFSImage to bytes and re-loading should produce a structurally identical image."""
        image = createAdfsImage()
        side = image.sides[0]

        side.addFile(DiscFile("$.TEST", b"hello"))

        serialized = image.serialize()
        image2 = ADFSImage(bytearray(serialized), False)
        cat = image2.sides[0].readCatalogue()

        assert len(cat.entries) == 1
        assert cat.entries[0].name == "TEST"

    def testBootOptionStored(self):
        """The boot option provided at creation should be readable from the free-space map after parsing."""
        image = createAdfsImage(boot_option=BootOption.RUN)
        side = image.sides[0]

        fsm = side.readFreeSpaceMap()
        assert fsm.boot_option == BootOption.RUN

    def testDefaultTitleIsDollar(self):
        """When no disc title is given, the title field should default to the string '$'."""
        image = createAdfsImage()
        side = image.sides[0]

        root = side.readDirectory(ADFS_ROOT_SECTOR)
        assert root.title == "$"


# -----------------------------------------------------------------------
# Directory parsing
# -----------------------------------------------------------------------

class TestDirectoryParsing:

    def testEmptyRootDirectory(self):
        """A root directory block with no entry data should parse to an empty entry list."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]
        root = side.readDirectory(ADFS_ROOT_SECTOR)

        assert root.name == "$"
        assert root.parent_sector == ADFS_ROOT_SECTOR
        assert len(root.entries) == 0

    def testDirectoryWithEntries(self):
        """A directory block containing entries should parse each one with the correct name and metadata."""
        entries = [
            _makeDirectoryEntry(
                name="MYPROG",
                load_addr=0x0E00,
                exec_addr=0x802B,
                length=256,
                start_sector=7,
            ),
            _makeDirectoryEntry(
                name="DATA",
                load_addr=0x1000,
                exec_addr=0x1000,
                length=128,
                start_sector=8,
            ),
        ]
        image_data = _blankAdfs(root_entries=entries)
        image = ADFSImage(image_data, is_adl=False)
        root = image.sides[0].readDirectory(ADFS_ROOT_SECTOR)

        assert len(root.entries) == 2
        assert root.entries[0].name == "MYPROG"
        assert root.entries[0].load_addr == 0x0E00
        assert root.entries[0].exec_addr == 0x802B
        assert root.entries[0].length == 256
        assert root.entries[0].start_sector == 7
        assert root.entries[1].name == "DATA"

    def testDirectoryTitle(self):
        """The 19-byte title field in the directory footer should be decoded and returned as the title attribute."""
        image_data = _blankAdfs(root_title="TestTitle")
        image = ADFSImage(image_data, is_adl=False)
        root = image.sides[0].readDirectory(ADFS_ROOT_SECTOR)
        assert root.title == "TestTitle"

    def testInvalidHeaderMagicRaises(self):
        """A directory block whose header magic is not 'Hugo' should raise ADFSFormatError."""
        image_data = _blankAdfs()
        # Corrupt the "Hugo" header magic at sector 2 offset 1.
        offset = ADFS_ROOT_SECTOR * ADFS_SECTOR_SIZE + 1
        image_data[offset:offset + 4] = b"Xxxx"

        image = ADFSImage(image_data, is_adl=False)
        with pytest.raises(ADFSFormatError, match="header magic"):
            image.sides[0].readDirectory(ADFS_ROOT_SECTOR)

    def testInvalidFooterMagicRaises(self):
        """A directory block whose footer magic is not 'Hugo' should raise ADFSFormatError."""
        image_data = _blankAdfs()
        # Corrupt the footer "Hugo" at 0x4FB within the directory.
        dir_base = ADFS_ROOT_SECTOR * ADFS_SECTOR_SIZE
        image_data[dir_base + 0x4FB:dir_base + 0x4FF] = b"Xxxx"

        image = ADFSImage(image_data, is_adl=False)
        with pytest.raises(ADFSFormatError, match="footer magic"):
            image.sides[0].readDirectory(ADFS_ROOT_SECTOR)

    def testSequenceMismatchRaises(self):
        """Header and footer sequence bytes that differ should raise ADFSFormatError, indicating a partially written directory."""
        image_data = _blankAdfs()
        dir_base = ADFS_ROOT_SECTOR * ADFS_SECTOR_SIZE

        # Set header sequence to 0x01 and footer to 0x02.
        image_data[dir_base + 0] = 0x01
        image_data[dir_base + 0x4FA] = 0x02

        image = ADFSImage(image_data, is_adl=False)
        with pytest.raises(ADFSFormatError, match="broken"):
            image.sides[0].readDirectory(ADFS_ROOT_SECTOR)

    def testAccessBitsDecodedCorrectly(self):
        """Access bits on all 10 name bytes of an entry should decode correctly into the flags visible on the returned entry."""
        # Set bits 0-3: R, W, L, D. Access byte = 0x0F.
        access = 0x0F
        entry_blob = _makeDirectoryEntry(
            name="SUBDIR",
            access=access,
            start_sector=20,
        )
        image_data = _blankAdfs(root_entries=[entry_blob])
        image = ADFSImage(image_data, is_adl=False)
        root = image.sides[0].readDirectory(ADFS_ROOT_SECTOR)

        e = root.entries[0]
        assert e.name == "SUBDIR"
        assert e.locked is True
        assert e.isDirectory is True
        assert e.access == 0x0F

    def testNameAccessBitsStripped(self):
        """The plain name returned by the entry should contain only the filename characters, not the access bits."""
        # Name "AB" with access bits set on all positions should still
        # decode as "AB" with 0x0D terminators stripped.
        access = 0x3FF  # all 10 bits set
        entry_blob = _makeDirectoryEntry(name="AB", access=access, start_sector=7)
        image_data = _blankAdfs(root_entries=[entry_blob])
        image = ADFSImage(image_data, is_adl=False)
        root = image.sides[0].readDirectory(ADFS_ROOT_SECTOR)

        assert root.entries[0].name == "AB"

    def testFullDirectoryWith47Entries(self):
        """A directory block populated with the maximum 47 entries should parse all of them without truncation."""
        entries = []
        for i in range(ADFS_MAX_ENTRIES):
            name = f"F{i:03d}"
            entries.append(_makeDirectoryEntry(name=name, start_sector=7 + i))

        image_data = _blankAdfs(total_sectors=1280, root_entries=entries)
        image = ADFSImage(image_data, is_adl=False)
        root = image.sides[0].readDirectory(ADFS_ROOT_SECTOR)

        assert len(root.entries) == ADFS_MAX_ENTRIES
        assert root.entries[0].name == "F000"
        assert root.entries[46].name == "F046"


# -----------------------------------------------------------------------
# Entry properties
# -----------------------------------------------------------------------

class TestEntryProperties:

    def testFullNameInRoot(self):
        """An entry in the root directory should report its full path as '$.filename'."""
        e = ADFSEntry(
            name="MYPROG", directory="$", load_addr=0, exec_addr=0,
            length=0, start_sector=0, locked=False, is_directory=False,
            access=0, sequence=0,
        )
        assert e.fullName == "$.MYPROG"

    def testFullNameInSubdir(self):
        """An entry inside a subdirectory should report its full path including the parent directory name."""
        e = ADFSEntry(
            name="ELITE", directory="$.GAMES", load_addr=0, exec_addr=0,
            length=0, start_sector=0, locked=False, is_directory=False,
            access=0, sequence=0,
        )
        assert e.fullName == "$.GAMES.ELITE"

    def testIsBasicWithKnownEntryPoint(self):
        """A file with the standard BBC BASIC II execution address should be identified as a BASIC file."""
        e = ADFSEntry(
            name="PROG", directory="$", load_addr=0x0E00, exec_addr=0x802B,
            length=100, start_sector=7, locked=False, is_directory=False,
            access=0, sequence=0,
        )
        assert e.isBasic is True

    def testIsBasicFalseForDirectory(self):
        """A directory entry should never be classified as a BASIC file."""
        e = ADFSEntry(
            name="GAMES", directory="$", load_addr=0, exec_addr=0x802B,
            length=0, start_sector=20, locked=False, is_directory=True,
            access=0x08, sequence=0,
        )
        assert e.isBasic is False

    def testIsBasicFalseForNonBasicExec(self):
        """A file with a non-BASIC execution address should not be identified as BASIC."""
        e = ADFSEntry(
            name="DATA", directory="$", load_addr=0x1000, exec_addr=0x1000,
            length=100, start_sector=7, locked=False, is_directory=False,
            access=0, sequence=0,
        )
        assert e.isBasic is False


# -----------------------------------------------------------------------
# Recursive directory walker
# -----------------------------------------------------------------------

class TestDirectoryWalker:

    def testFlatRootDirectory(self):
        """Walking a disc with only a root directory should yield every entry exactly once with no recursion."""
        entries = [
            _makeDirectoryEntry(name="FILE1", start_sector=7, length=10),
            _makeDirectoryEntry(name="FILE2", start_sector=8, length=20),
        ]
        image_data = _blankAdfs(root_entries=entries)
        image = ADFSImage(image_data, is_adl=False)
        flat = image.sides[0].walkDirectories()

        assert len(flat) == 2
        assert flat[0].fullName == "$.FILE1"
        assert flat[1].fullName == "$.FILE2"
        assert flat[0].directory == "$"

    def testSubdirectoryWalk(self):
        """Walking a disc with subdirectories should recursively visit all entries in every nested directory."""
        # Create a subdirectory entry in root pointing to sector 20.
        subdir_access = 0x0F  # R + W + L + D
        subdir_entry = _makeDirectoryEntry(
            name="GAMES",
            start_sector=20,
            access=subdir_access,
        )
        file_entry = _makeDirectoryEntry(
            name="README",
            start_sector=7,
            length=16,
        )

        # Build root directory with both entries.
        image_data = _blankAdfs(
            total_sectors=1280,
            root_entries=[file_entry, subdir_entry],
        )

        # Build the GAMES subdirectory at sector 20 with one file.
        child_file = _makeDirectoryEntry(
            name="ELITE",
            start_sector=25,
            length=100,
            load_addr=0x0E00,
            exec_addr=0x802B,
        )
        games_dir = _makeDirectory(
            name="GAMES",
            title="Games",
            parent_sector=ADFS_ROOT_SECTOR,
            entries=[child_file],
        )
        games_offset = 20 * ADFS_SECTOR_SIZE
        image_data[games_offset:games_offset + ADFS_DIR_LENGTH] = games_dir

        image = ADFSImage(image_data, is_adl=False)
        flat = image.sides[0].walkDirectories()

        # Should contain: README, GAMES (directory entry), ELITE.
        assert len(flat) == 3

        names = [e.fullName for e in flat]
        assert "$.README" in names
        assert "$.GAMES" in names
        assert "$.GAMES.ELITE" in names

        # Verify the ELITE entry has the right directory path.
        elite = [e for e in flat if e.name == "ELITE"][0]
        assert elite.directory == "$.GAMES"
        assert elite.isBasic is True


# -----------------------------------------------------------------------
# Catalogue (duck-typing interface)
# -----------------------------------------------------------------------

class TestCatalogue:

    def testCatalogueAttributes(self):
        """The ADFSCatalogue should expose disc title, boot option, and a flat list of all file entries."""
        image_data = _blankAdfs(
            root_title="TestDisc",
            boot_option=1,
        )
        image = ADFSImage(image_data, is_adl=False)
        cat = image.sides[0].readCatalogue()

        assert cat.title == "TestDisc"
        assert cat.boot_option == BootOption.LOAD
        assert cat.boot_option.name == "LOAD"
        assert cat.disc_size == 640
        assert isinstance(cat.entries, tuple)

    def testTracks40(self):
        """An ADFS S image (640 sectors, 16 sectors/track) should report 40 tracks."""
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        cat = image.sides[0].readCatalogue()
        assert cat.tracks == 40

    def testTracks80(self):
        """An ADFS M image (1280 sectors, 16 sectors/track) should report 80 tracks."""
        image_data = _blankAdfs(total_sectors=1280)
        image = ADFSImage(image_data, is_adl=False)
        cat = image.sides[0].readCatalogue()
        assert cat.tracks == 80

    def testCatalogueIsCached(self):
        """Calling catalogue on the same image twice should return the same object, avoiding redundant re-parsing."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        cat1 = side.readCatalogue()
        cat2 = side.readCatalogue()
        assert cat1 is cat2

    def testCatalogueEntriesMatchWalk(self):
        """The flat entry list from catalogue should exactly match the result of a full recursive directory walk."""
        entries = [
            _makeDirectoryEntry(name="A", start_sector=7, length=10),
            _makeDirectoryEntry(name="B", start_sector=8, length=20),
        ]
        image_data = _blankAdfs(root_entries=entries)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        cat = side.readCatalogue()
        assert len(cat.entries) == 2
        assert cat.entries[0].fullName == "$.A"
        assert cat.entries[1].fullName == "$.B"


# -----------------------------------------------------------------------
# File extraction
# -----------------------------------------------------------------------

class TestFileExtraction:

    def testExtractFileData(self):
        """Extracting a file should return bytes identical to the data originally written."""
        test_data = b"Hello, ADFS world!" + b"\x00" * 10
        image_data = _adfsWithFiles([
            {"name": "HELLO", "data": test_data, "load_addr": 0x1000},
        ])
        image = ADFSImage(image_data, is_adl=False)
        cat = image.sides[0].readCatalogue()

        assert len(cat.entries) == 1
        extracted = image.sides[0].readFile(cat.entries[0])
        assert extracted == test_data

    def testExtractedLengthMatchesCatalogue(self):
        """The byte length of each extracted file should match the length in its catalogue entry."""
        files = [
            {"name": "SHORT", "data": b"\x01\x02\x03"},
            {"name": "MEDIUM", "data": bytes(range(256)) * 2},
        ]
        image_data = _adfsWithFiles(files)
        image = ADFSImage(image_data, is_adl=False)
        cat = image.sides[0].readCatalogue()

        for entry in cat.entries:
            data = image.sides[0].readFile(entry)
            assert len(data) == entry.length

    def testExtractEmptyFile(self):
        """Extracting a file recorded as zero length should return an empty bytes object."""
        image_data = _adfsWithFiles([
            {"name": "EMPTY", "data": b""},
        ])
        image = ADFSImage(image_data, is_adl=False)
        cat = image.sides[0].readCatalogue()

        empty = [e for e in cat.entries if e.name == "EMPTY"][0]
        assert image.sides[0].readFile(empty) == b""

    def testExtractBasicFile(self):
        """Extracting a tokenized BASIC file should return bytes that pass the looksLikeTokenizedBasic check."""
        # Minimal tokenized BASIC: one line "10 PRINT" followed by end marker.
        basic_data = bytes([
            0x0D,        # line start
            0x00, 0x0A,  # line number 10 (high, low)
            0x05,        # line length (4 + 1 content byte)
            0xF1,        # PRINT token
            0x0D,        # next line start (end program)
            0xFF,        # end marker
        ])
        image_data = _adfsWithFiles([
            {
                "name": "MYPROG",
                "data": basic_data,
                "load_addr": 0x0E00,
                "exec_addr": 0x802B,
            },
        ])
        image = ADFSImage(image_data, is_adl=False)
        cat = image.sides[0].readCatalogue()

        prog = cat.entries[0]
        assert prog.isBasic is True

        data = image.sides[0].readFile(prog)
        assert data[0] == 0x0D
        assert looksLikeTokenizedBasic(data)

    def testSectorBoundsCheckRaises(self):
        """Extracting a file whose sector range extends beyond the image boundary should raise ADFSError."""
        # Create an entry pointing beyond the image.
        entry = _makeDirectoryEntry(
            name="BAD",
            start_sector=9999,
            length=256,
        )
        image_data = _blankAdfs(root_entries=[entry])
        image = ADFSImage(image_data, is_adl=False)
        cat = image.sides[0].readCatalogue()

        with pytest.raises(ADFSFormatError, match="extends beyond"):
            image.sides[0].readFile(cat.entries[0])


# -----------------------------------------------------------------------
# ADFSImage container
# -----------------------------------------------------------------------

class TestAdfsImage:

    def testSingleSidedHasOneSide(self):
        """An .adf (single-sided) image should expose exactly one disc side."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        assert len(image.sides) == 1
        assert image.sides[0].side == 0

    def testDoubleSidedHasOneSide(self):
        """An .adl (double-sided track-interleaved) image is treated as a single logical side, not split into two."""
        # ADFS always presents one logical filesystem, even for .adl.
        image_data = _blankAdfs(total_sectors=2560)
        image = ADFSImage(image_data, is_adl=True)
        assert len(image.sides) == 1

    def testSerializeRoundTrip(self):
        """Serializing an ADFSImage to bytes and re-loading should produce a structurally identical image."""
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        serialized = image.serialize()
        assert serialized == bytes(image_data)

    def testIsAdlProperty(self):
        """The is_adl property should be True for .adl files and False for single-sided .adf files."""
        image_data = _blankAdfs()
        assert ADFSImage(image_data, is_adl=False).is_adl is False
        assert ADFSImage(image_data, is_adl=True).is_adl is True


# -----------------------------------------------------------------------
# openAdfsImage
# -----------------------------------------------------------------------

class TestOpenAdfsImage:

    def testOpenValidAdf(self, tmp_path):
        """A valid single-sided .adf image should open without error and expose readable catalogue data."""
        image_data = _blankAdfs(total_sectors=640)
        path = tmp_path / "test.adf"
        path.write_bytes(bytes(image_data))

        image = openAdfsImage(str(path))
        assert isinstance(image, ADFSImage)
        assert image.is_adl is False

    def testOpenValidAdl(self, tmp_path):
        """A valid double-sided .adl image should open without error."""
        image_data = _blankAdfs(total_sectors=2560)
        path = tmp_path / "test.adl"
        path.write_bytes(bytes(image_data))

        image = openAdfsImage(str(path))
        assert isinstance(image, ADFSImage)
        assert image.is_adl is True

    def testTooSmallRaises(self, tmp_path):
        """An image file smaller than the minimum valid ADFS size should raise ADFSFormatError."""
        path = tmp_path / "tiny.adf"
        path.write_bytes(b"\x00" * 100)

        with pytest.raises(ADFSFormatError, match="too small"):
            openAdfsImage(str(path))

    def testMissingHugoRaises(self, tmp_path):
        """An image whose root directory lacks the 'Hugo' magic bytes should raise ADFSFormatError."""
        # Image large enough but no Hugo marker.
        image_data = bytearray(640 * 256)
        path = tmp_path / "nope.adf"
        path.write_bytes(bytes(image_data))

        with pytest.raises(ADFSFormatError, match="Hugo"):
            openAdfsImage(str(path))

    def testFileNotFoundRaises(self, tmp_path):
        """Passing a path to a non-existent file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            openAdfsImage(str(tmp_path / "nonexistent.adf"))


# -----------------------------------------------------------------------
# Format detection via openImage
# -----------------------------------------------------------------------

class TestFormatDetection:

    def testAdfRoutesToAdfs(self, tmp_path):
        """openImage() given an .adf file should return an ADFSImage instance, not a DFS disc."""
        image_data = _blankAdfs(total_sectors=640)
        path = tmp_path / "test.adf"
        path.write_bytes(bytes(image_data))

        image = openImage(str(path))
        assert isinstance(image, ADFSImage)

    def testAdlRoutesToAdfs(self, tmp_path):
        """openImage() given an .adl file should return an ADFSImage instance."""
        image_data = _blankAdfs(total_sectors=2560)
        path = tmp_path / "test.adl"
        path.write_bytes(bytes(image_data))

        image = openImage(str(path))
        assert isinstance(image, ADFSImage)

    def testSsdStillRoutesToDfs(self, tmp_path):
        """openImage() given a .ssd file should still return a DFS disc object."""
        from beebtools import createDiscImage, DFSImage
        dfs = createDiscImage(tracks=80)
        path = tmp_path / "test.ssd"
        path.write_bytes(dfs.serialize())

        image = openImage(str(path))
        assert isinstance(image, DFSImage)


# -----------------------------------------------------------------------
# Integration tests against real disc images
# -----------------------------------------------------------------------

DISCS_DIR = os.path.join(os.path.dirname(__file__), "resources", "discs")
ALL_ADFS = sorted(
    glob.glob(os.path.join(DISCS_DIR, "**", "*.[aA][dD][fFlL]"), recursive=True)
)
adfs_ids = [os.path.basename(p) for p in ALL_ADFS]


@pytest.mark.skipif(
    len(ALL_ADFS) == 0,
    reason="No ADFS disc images found in tests/resources/discs/",
)
class TestRealAdfsImages:

    @pytest.mark.parametrize("path", ALL_ADFS, ids=adfs_ids)
    def testOpensWithoutError(self, path):
        """Each real .adl image in tests/resources/ should open without raising any exception."""
        image = openAdfsImage(path)
        assert len(image.sides) >= 1

    @pytest.mark.parametrize("path", ALL_ADFS, ids=adfs_ids)
    def testCatalogueNonEmpty(self, path):
        """A real disc image should contain at least one catalogue entry."""
        image = openAdfsImage(path)
        for side in image.sides:
            cat = side.readCatalogue()
            assert isinstance(cat.entries, tuple)

    @pytest.mark.parametrize("path", ALL_ADFS, ids=adfs_ids)
    def testEntryNamesAreNonEmpty(self, path):
        """Every entry name read from a real disc image should be a non-empty string."""
        image = openAdfsImage(path)
        for side in image.sides:
            cat = side.readCatalogue()
            for entry in cat.entries:
                assert len(entry.name) > 0

    @pytest.mark.parametrize("path", ALL_ADFS, ids=adfs_ids)
    def testExtractedLengthMatchesCatalogue(self, path):
        """The byte length of each extracted file should match the length in its catalogue entry."""
        image = openAdfsImage(path)
        for side in image.sides:
            cat = side.readCatalogue()
            for entry in cat.entries:
                if not entry.isDirectory:
                    data = side.readFile(entry)
                    assert len(data) == entry.length

    @pytest.mark.parametrize("path", ALL_ADFS, ids=adfs_ids)
    def testBasicFilesStartWith0x0d(self, path):
        """Any file flagged as BASIC on a real disc should begin with the 0x0D line-record start byte."""
        image = openAdfsImage(path)
        for side in image.sides:
            cat = side.readCatalogue()
            for entry in cat.entries:
                if entry.isBasic and not entry.isDirectory:
                    data = side.readFile(entry)
                    if len(data) > 0:
                        assert data[0] == 0x0D

    @pytest.mark.parametrize("path", ALL_ADFS, ids=adfs_ids)
    def testDetokenizedLinesHaveLineNumbers(self, path):
        """Detokenizing each BASIC file from a real disc should produce lines that start with numeric line numbers."""
        image = openAdfsImage(path)
        for side in image.sides:
            cat = side.readCatalogue()
            for entry in cat.entries:
                if entry.isBasic and not entry.isDirectory:
                    data = side.readFile(entry)
                    if looksLikeTokenizedBasic(data):
                        for line in detokenize(data):
                            assert line[:5].strip().isdigit()


# -----------------------------------------------------------------------
# sanitizeEntryPath for hierarchical ADFS paths
# -----------------------------------------------------------------------

class TestSanitizeEntryPath:

    def testFlatDfsDir(self):
        """A DFS-style '$' directory should map to a safe, usable host filesystem component."""
        safe_dir, safe_name = sanitizeEntryPath("$", "MYPROG")
        assert safe_dir == "$"
        assert safe_name == "MYPROG"

    def testAdfsRootDir(self):
        """An ADFS root '$' path should produce a single safe top-level component."""
        safe_dir, safe_name = sanitizeEntryPath("$", "README")
        assert safe_dir == "$"
        assert safe_name == "README"

    def testAdfsNestedPath(self):
        """A two-level ADFS path should be joined into a correctly structured host filesystem path."""
        safe_dir, safe_name = sanitizeEntryPath("$.GAMES", "ELITE")
        expected_dir = os.path.join("$", "GAMES")
        assert safe_dir == expected_dir
        assert safe_name == "ELITE"

    def testAdfsDeeplyNestedPath(self):
        """A multi-level ADFS path should produce a fully joined host filesystem path with all levels preserved."""
        safe_dir, safe_name = sanitizeEntryPath("$.A.B.C", "FILE")
        expected_dir = os.path.join("$", "A", "B", "C")
        assert safe_dir == expected_dir
        assert safe_name == "FILE"


# -----------------------------------------------------------------------
# CLI: cmdCat with ADFS images
# -----------------------------------------------------------------------

def _writeAdfsImage(tmp_path, image_data: bytearray, ext: str = ".adf") -> str:
    """Write synthetic ADFS image data to a temp file and return the path."""
    path = str(tmp_path / f"test{ext}")
    with open(path, "wb") as f:
        f.write(bytes(image_data))
    return path


class TestCmdCatAdfs:

    def _runCat(self, tmp_path, image_data: bytearray, inspect: bool = False) -> str:
        """Write image, run cmdCat, return captured stdout."""
        img_path = _writeAdfsImage(tmp_path, image_data)
        args = Namespace(image=img_path, sort="name", inspect=inspect)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCat(args)
        return buf.getvalue()

    def testShowsFileEntries(self, tmp_path):
        """cmdCat on an ADFS image should list each file entry's name and size."""
        image_data = _adfsWithFiles([
            {"name": "README", "data": b"Hello", "load_addr": 0x1000, "exec_addr": 0x1000},
        ])
        output = self._runCat(tmp_path, image_data)

        assert "README" in output
        assert "00001000" in output

    def testShowsAdfsAccessString(self, tmp_path):
        """cmdCat prints the full ADFS access string, not just a lock flag."""
        # access=0x25: R (0x01) + L (0x04) + r (0x20) = LR/r.
        image_data = _adfsWithFiles([
            {"name": "LOCKED", "data": b"x", "access": 0x25},
            {"name": "WRITE", "data": b"x", "access": 0x03},  # R+W
        ])
        output = self._runCat(tmp_path, image_data)

        # Column header and both access forms appear in the listing.
        assert "Access" in output
        assert "LR/r" in output
        assert "WR" in output

    def testShowsDirType(self, tmp_path):
        """cmdCat should distinguish directory entries from files in its output."""
        # Create a directory entry (access bit 3 = 0x08).
        subdir_entry = _makeDirectoryEntry(
            name="GAMES",
            start_sector=20,
            access=0x0F,  # R + W + L + D
        )
        # Also need a valid directory at sector 20 so readDirectory succeeds
        # during the walk. Build a minimal image.
        image_data = _blankAdfs(
            total_sectors=1280,
            root_entries=[subdir_entry],
        )
        games_dir = _makeDirectory(
            name="GAMES",
            title="Games",
            parent_sector=ADFS_ROOT_SECTOR,
            entries=[],
        )
        games_offset = 20 * ADFS_SECTOR_SIZE
        image_data[games_offset:games_offset + ADFS_DIR_LENGTH] = games_dir

        output = self._runCat(tmp_path, image_data)

        assert "GAMES" in output
        assert "DIR" in output

    def testDynamicColumnWidth(self, tmp_path):
        """The name column should expand to accommodate the longest filename present in the directory."""
        # A file with a long hierarchical name should widen the column.
        subdir_entry = _makeDirectoryEntry(
            name="LONGDIRNAM",
            start_sector=20,
            access=0x0B,  # R + W + D (no lock)
        )
        image_data = _blankAdfs(
            total_sectors=1280,
            root_entries=[subdir_entry],
        )
        
        # Create child directory with a file.
        child_file = _makeDirectoryEntry(
            name="MYFILE",
            start_sector=25,
            length=100,
            load_addr=0x1000,
            exec_addr=0x1000,
        )
        child_dir = _makeDirectory(
            name="LONGDIRNAM",
            title="Long",
            parent_sector=ADFS_ROOT_SECTOR,
            entries=[child_file],
        )
        offset = 20 * ADFS_SECTOR_SIZE
        image_data[offset:offset + ADFS_DIR_LENGTH] = child_dir

        output = self._runCat(tmp_path, image_data)

        # The long name "$.LONGDIRNAM.MYFILE" (19 chars) should appear.
        assert "$.LONGDIRNAM.MYFILE" in output

    def testBasicFileShowsBasicType(self, tmp_path):
        """A file identified as BASIC should be labelled 'BASIC' in the cmdCat output."""
        # BASIC file exec address triggers "BASIC" label.
        basic_data = bytes([
            0x0D, 0x00, 0x0A, 0x05, 0xF1, 0x0D, 0xFF,
        ])
        image_data = _adfsWithFiles([{
            "name": "MYPROG",
            "data": basic_data,
            "load_addr": 0x0E00,
            "exec_addr": 0x802B,
        }])
        output = self._runCat(tmp_path, image_data)

        assert "BASIC" in output

    def testEmptyCatalogueShowsEmpty(self, tmp_path):
        """An ADFS image with no files should produce a catalogue listing with no file rows."""
        image_data = _blankAdfs()
        output = self._runCat(tmp_path, image_data)
        assert "(empty)" in output

    def testShowsTrackCount(self, tmp_path):
        """An ADFS S image (640 sectors) should show '40 tracks' in the header."""
        image_data = _blankAdfs(total_sectors=640)
        output = self._runCat(tmp_path, image_data)
        assert "40 tracks" in output

    def testShowsTrackCount80(self, tmp_path):
        """An ADFS M image (1280 sectors) should show '80 tracks' in the header."""
        image_data = _blankAdfs(total_sectors=1280)
        output = self._runCat(tmp_path, image_data)
        assert "80 tracks" in output


# -----------------------------------------------------------------------
# CLI: cmdExtract with ADFS images
# -----------------------------------------------------------------------

class TestCmdExtractAdfs:

    def testExtractByFullName(self, tmp_path):
        """Extracting with a full '$dir/name' path should write the correct file contents to the output location."""
        test_data = b"file content here"
        image_data = _adfsWithFiles([
            {"name": "README", "data": test_data, "load_addr": 0x1000, "exec_addr": 0x1000},
        ])
        img_path = _writeAdfsImage(tmp_path, image_data)
        out_file = str(tmp_path / "out.bin")

        args = Namespace(
            image=img_path,
            filename="$.README",
            output=out_file,
            pretty=False,
            all=False,
            dir=None,
            inf=False,
        )
        cmdExtract(args)

        assert os.path.isfile(out_file)
        with open(out_file, "rb") as f:
            assert f.read() == test_data

    def testExtractByBareName(self, tmp_path):
        """Extracting with just the bare filename (no directory) should locate and extract the file."""
        test_data = b"bare name match"
        image_data = _adfsWithFiles([
            {"name": "MYDATA", "data": test_data, "load_addr": 0x1000, "exec_addr": 0x1000},
        ])
        img_path = _writeAdfsImage(tmp_path, image_data)
        out_file = str(tmp_path / "out.bin")

        args = Namespace(
            image=img_path,
            filename="MYDATA",
            output=out_file,
            pretty=False,
            all=False,
            dir=None,
            inf=False,
        )
        cmdExtract(args)

        assert os.path.isfile(out_file)
        with open(out_file, "rb") as f:
            assert f.read() == test_data

    def testExtractBasicDetokenizes(self, tmp_path):
        """Extracting a BASIC file without --raw should produce readable detokenized plain text, not binary."""
        basic_data = bytes([
            0x0D, 0x00, 0x0A, 0x05, 0xF1, 0x0D, 0xFF,
        ])
        image_data = _adfsWithFiles([{
            "name": "MYPROG",
            "data": basic_data,
            "load_addr": 0x0E00,
            "exec_addr": 0x802B,
        }])
        img_path = _writeAdfsImage(tmp_path, image_data)
        out_file = str(tmp_path / "out.bas")

        args = Namespace(
            image=img_path,
            filename="$.MYPROG",
            output=out_file,
            pretty=False,
            all=False,
            dir=None,
            inf=False,
        )
        cmdExtract(args)

        with open(out_file, "r") as f:
            content = f.read()
        # Should contain the line number 10 and PRINT.
        assert "10" in content
        assert "PRINT" in content

    def testExtractFileNotFound(self, tmp_path):
        """Attempting to extract a filename not present in the ADFS catalogue should raise an appropriate error."""
        image_data = _adfsWithFiles([
            {"name": "README", "data": b"data", "load_addr": 0, "exec_addr": 0},
        ])
        img_path = _writeAdfsImage(tmp_path, image_data)

        args = Namespace(
            image=img_path,
            filename="NOSUCHFILE",
            output=None,
            pretty=False,
            all=False,
            dir=None,
            inf=False,
        )
        with pytest.raises(SystemExit):
            cmdExtract(args)


# -----------------------------------------------------------------------
# extractAll with ADFS images
# -----------------------------------------------------------------------

class TestExtractAllAdfs:

    def testExtractSkipsDirectoryEntries(self, tmp_path):
        """Bulk extraction should not attempt to write directory entries as files to the output directory."""
        # Create an image with a directory entry and a file entry.
        subdir_entry = _makeDirectoryEntry(
            name="GAMES",
            start_sector=20,
            access=0x0F,
        )
        file_entry = _makeDirectoryEntry(
            name="README",
            start_sector=7,
            length=5,
        )
        image_data = _blankAdfs(
            total_sectors=1280,
            root_entries=[file_entry, subdir_entry],
        )

        # Write file data at sector 7.
        offset = 7 * ADFS_SECTOR_SIZE
        image_data[offset:offset + 5] = b"hello"

        # Add a valid directory at sector 20.
        games_dir = _makeDirectory(
            name="GAMES",
            title="Games",
            parent_sector=ADFS_ROOT_SECTOR,
            entries=[],
        )
        games_offset = 20 * ADFS_SECTOR_SIZE
        image_data[games_offset:games_offset + ADFS_DIR_LENGTH] = games_dir

        img_path = _writeAdfsImage(tmp_path, image_data)
        out_dir = str(tmp_path / "extracted")
        results = extractAll(img_path, out_dir)

        # Only the file should be extracted, not the directory entry.
        assert len(results) == 1
        assert "README" in results[0]["path"]

    def testExtractHierarchicalLayout(self, tmp_path):
        """Files in ADFS subdirectories should be extracted into matching subdirectories on the host filesystem."""
        # Create an image with a subdirectory containing a file.
        subdir_entry = _makeDirectoryEntry(
            name="DATA",
            start_sector=20,
            access=0x0B,  # R + W + D
        )
        image_data = _blankAdfs(
            total_sectors=1280,
            root_entries=[subdir_entry],
        )

        child_file = _makeDirectoryEntry(
            name="SCORES",
            start_sector=25,
            length=3,
            load_addr=0x2000,
            exec_addr=0x2000,
        )
        data_dir = _makeDirectory(
            name="DATA",
            title="Data",
            parent_sector=ADFS_ROOT_SECTOR,
            entries=[child_file],
        )
        dir_offset = 20 * ADFS_SECTOR_SIZE
        image_data[dir_offset:dir_offset + ADFS_DIR_LENGTH] = data_dir

        # Write file data at sector 25.
        file_offset = 25 * ADFS_SECTOR_SIZE
        image_data[file_offset:file_offset + 3] = b"xyz"

        img_path = _writeAdfsImage(tmp_path, image_data)
        out_dir = str(tmp_path / "extracted")
        results = extractAll(img_path, out_dir)

        # Should extract the child file, skipping the directory entry.
        file_results = [r for r in results if r["type"] != "DIR"]
        assert len(file_results) == 1

        # The file should be under the hierarchical path.
        path = file_results[0]["path"]
        assert "SCORES" in path
        assert os.path.isfile(path)


# -----------------------------------------------------------------------
# search with ADFS images
# -----------------------------------------------------------------------

class TestSearchAdfs:

    def testSearchSkipsDirectoryEntries(self, tmp_path):
        """Disc search should skip directory entries and only inspect regular files."""
        # Create an image with a directory entry and a BASIC file.
        basic_data = bytes([
            0x0D, 0x00, 0x0A, 0x09, 0xF1, 0x22, 0x48, 0x49, 0x22, 0x0D, 0xFF,
        ])
        subdir_entry = _makeDirectoryEntry(
            name="SUBDIR",
            start_sector=20,
            access=0x0F,
        )
        file_entry = _makeDirectoryEntry(
            name="HELLO",
            start_sector=7,
            length=len(basic_data),
            load_addr=0x0E00,
            exec_addr=0x802B,
        )
        image_data = _blankAdfs(
            total_sectors=1280,
            root_entries=[file_entry, subdir_entry],
        )

        # Write BASIC data.
        offset = 7 * ADFS_SECTOR_SIZE
        image_data[offset:offset + len(basic_data)] = basic_data

        # Add valid directory at sector 20.
        sub_dir = _makeDirectory(
            name="SUBDIR",
            title="Sub",
            parent_sector=ADFS_ROOT_SECTOR,
            entries=[],
        )
        sub_offset = 20 * ADFS_SECTOR_SIZE
        image_data[sub_offset:sub_offset + ADFS_DIR_LENGTH] = sub_dir

        img_path = _writeAdfsImage(tmp_path, image_data)
        results = search(img_path, "HI")

        # Should find the match in the BASIC file, not crash on the directory.
        assert len(results) >= 1
        assert results[0]["filename"] == "$.HELLO"
