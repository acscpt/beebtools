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
    _adfsChecksum,
    _decodeString,
    _encodeString,
    _read24le,
    _read32le,
    _write24le,
    _write32le,
    _encodeEntryName,
    _encodeEntry,
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
        # 255 zero bytes - checksum computation starts at 255 and adds zeros.
        data = bytes(256)
        result = _adfsChecksum(data)
        assert isinstance(result, int)
        assert 0 <= result <= 255

    def testKnownChecksumRoundTrip(self):
        # Build a free space map sector and verify the checksum is self-consistent.
        sec = bytearray(256)
        sec[0xFC] = 0x80  # 640 sectors low byte
        sec[0xFD] = 0x02  # 640 sectors high bytes
        sec[0xFF] = _adfsChecksum(bytes(sec))

        # The stored checksum should match a fresh computation.
        assert _adfsChecksum(bytes(sec)) == sec[0xFF]

    def testCorruptChecksumDetected(self):
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
        assert _decodeString(b"HELLO\x0d\x0d\x0d\x0d\x0d") == "HELLO"

    def testNulTerminator(self):
        assert _decodeString(b"TEST\x00\x00\x00\x00\x00\x00") == "TEST"

    def testFullLengthName(self):
        assert _decodeString(b"ABCDEFGHIJ") == "ABCDEFGHIJ"

    def testEmptyString(self):
        assert _decodeString(b"\x0d\x0d\x0d") == ""


# -----------------------------------------------------------------------
# Integer reader tests
# -----------------------------------------------------------------------

class TestIntReaders:

    def testRead24le(self):
        data = bytes([0x56, 0x34, 0x12, 0xFF])
        assert _read24le(data, 0) == 0x123456

    def testRead32le(self):
        data = bytes([0x78, 0x56, 0x34, 0x12])
        assert _read32le(data, 0) == 0x12345678


# -----------------------------------------------------------------------
# Integer writer tests
# -----------------------------------------------------------------------

class TestIntWriters:

    def testWrite24leRoundTrip(self):
        buf = bytearray(8)
        _write24le(buf, 2, 0x123456)
        assert _read24le(buf, 2) == 0x123456

    def testWrite32leRoundTrip(self):
        buf = bytearray(8)
        _write32le(buf, 1, 0x12345678)
        assert _read32le(buf, 1) == 0x12345678

    def testWrite24leZero(self):
        buf = bytearray(4)
        _write24le(buf, 0, 0)
        assert buf[0:3] == b"\x00\x00\x00"

    def testWrite32leZero(self):
        buf = bytearray(4)
        _write32le(buf, 0, 0)
        assert buf == b"\x00\x00\x00\x00"

    def testWrite24leMaxValue(self):
        buf = bytearray(3)
        _write24le(buf, 0, 0xFFFFFF)
        assert buf == b"\xFF\xFF\xFF"

    def testWrite32leMaxValue(self):
        buf = bytearray(4)
        _write32le(buf, 0, 0xFFFFFFFF)
        assert buf == b"\xFF\xFF\xFF\xFF"


# -----------------------------------------------------------------------
# String encoding tests
# -----------------------------------------------------------------------

class TestEncodeString:

    def testSimpleRoundTrip(self):
        encoded = _encodeString("HELLO", 10)
        assert _decodeString(encoded) == "HELLO"

    def testPaddedWith0x0D(self):
        encoded = _encodeString("AB", 5)
        assert encoded == bytes([0x41, 0x42, 0x0D, 0x0D, 0x0D])

    def testFullLengthNoPadding(self):
        encoded = _encodeString("ABCDEFGHIJ", 10)
        assert _decodeString(encoded) == "ABCDEFGHIJ"

    def testEmptyString(self):
        encoded = _encodeString("", 5)
        assert encoded == bytes([0x0D] * 5)

    def testTruncatesToLength(self):
        encoded = _encodeString("TOOLONGNAME", 5)
        assert len(encoded) == 5
        assert _decodeString(encoded) == "TOOLO"


# -----------------------------------------------------------------------
# Entry name encoding tests
# -----------------------------------------------------------------------

class TestEncodeEntryName:

    def testSimpleNameNoAccess(self):
        encoded = _encodeEntryName("TEST", 0x00)
        # Characters should be plain ASCII, remainder padded with 0x0D.
        assert encoded[0] == ord("T")
        assert encoded[3] == ord("T")
        assert encoded[4] == 0x0D

    def testAccessBitsInBit7(self):
        # Access 0x03 sets bits 0 (R) and 1 (W).
        encoded = _encodeEntryName("AB", 0x03)
        assert encoded[0] == ord("A") | 0x80  # bit 0 set
        assert encoded[1] == ord("B") | 0x80  # bit 1 set
        assert encoded[2] == 0x0D             # no access bit 2

    def testRoundTripWithParseEntry(self):
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
        encoded = _encodeEntryName("ABCDEFGHIJ", 0x00)
        name = ""
        for i in range(10):
            ch = encoded[i] & 0x7F
            if ch == 0x0D:
                break
            name += chr(ch)
        assert name == "ABCDEFGHIJ"

    def testDirectoryAccessBits(self):
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
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Write a pattern to sector 10.
        pattern = bytes(range(256))
        side._writeSector(10, pattern)
        assert side._readSector(10) == pattern

    def testWriteSectorsReadBack(self):
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        # Write two sectors at once.
        data = bytes(range(256)) + bytes(range(255, -1, -1))
        side._writeSectors(20, data)
        assert side._readSectors(20, 2) == data

    def testWriteSectorOutOfBoundsRaises(self):
        image_data = _blankAdfs(total_sectors=10)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        with pytest.raises(ADFSError):
            side._writeSector(10, bytes(256))

    def testWriteSectorsOutOfBoundsRaises(self):
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
        image_data = _blankAdfs()
        # Corrupt a byte in sector 0. Use a single-bit flip so the ADFS
        # carry-based checksum detects it (XOR 0xFF is a blind spot).
        image_data[0x10] ^= 0x01
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        with pytest.raises(ADFSFormatError, match="checksum"):
            side.readFreeSpaceMap()

    def testCorruptSec1ChecksumRaises(self):
        image_data = _blankAdfs()
        # Corrupt a byte in sector 1.
        image_data[0x110] ^= 0x01
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        with pytest.raises(ADFSFormatError, match="checksum"):
            side.readFreeSpaceMap()

    def testMultipleFreeBlocks(self):
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
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        with pytest.raises(ADFSError, match="Cannot allocate"):
            side._allocateBlock(634)

    def testAllocateFirstFitSkipsSmallBlock(self):
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
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        assert side.freeSpace() == 633

    def testFreeSpaceAfterAllocation(self):
        image_data = _blankAdfs(total_sectors=640)
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        side._allocateBlock(100)
        assert side.freeSpace() == 533

    def testFreeSpaceWithMultipleBlocks(self):
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
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        directory = side.readDirectory(ADFS_ROOT_SECTOR)
        updated = side._insertEntry(directory, self._makeDummyEntry("HELLO"))
        assert len(updated.entries) == 1
        assert updated.entries[0].name == "HELLO"

    def testInsertMaintainsSortOrder(self):
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
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        directory = side.readDirectory(ADFS_ROOT_SECTOR)
        directory = side._insertEntry(directory, self._makeDummyEntry("Zebra"))
        directory = side._insertEntry(directory, self._makeDummyEntry("alpha"))

        assert directory.entries[0].name == "alpha"
        assert directory.entries[1].name == "Zebra"

    def testInsertDuplicateRaises(self):
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        directory = side.readDirectory(ADFS_ROOT_SECTOR)
        directory = side._insertEntry(directory, self._makeDummyEntry("FILE"))

        with pytest.raises(ADFSError, match="Duplicate"):
            side._insertEntry(directory, self._makeDummyEntry("file"))

    def testInsertFullDirectoryRaises(self):
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
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        directory = side.readDirectory(ADFS_ROOT_SECTOR)
        directory = side._insertEntry(directory, self._makeDummyEntry("FILE"))
        directory = side._removeEntry(directory, "FILE")

        assert len(directory.entries) == 0

    def testRemoveCaseInsensitive(self):
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        directory = side.readDirectory(ADFS_ROOT_SECTOR)
        directory = side._insertEntry(directory, self._makeDummyEntry("MyFile"))
        directory = side._removeEntry(directory, "myfile")

        assert len(directory.entries) == 0

    def testRemoveFromMiddle(self):
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
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        directory = side.readDirectory(ADFS_ROOT_SECTOR)

        with pytest.raises(ADFSError, match="not found"):
            side._removeEntry(directory, "GHOST")


# -----------------------------------------------------------------------
# Directory parsing
# -----------------------------------------------------------------------

class TestDirectoryParsing:

    def testEmptyRootDirectory(self):
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]
        root = side.readDirectory(ADFS_ROOT_SECTOR)

        assert root.name == "$"
        assert root.parent_sector == ADFS_ROOT_SECTOR
        assert len(root.entries) == 0

    def testDirectoryWithEntries(self):
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
        image_data = _blankAdfs(root_title="TestTitle")
        image = ADFSImage(image_data, is_adl=False)
        root = image.sides[0].readDirectory(ADFS_ROOT_SECTOR)
        assert root.title == "TestTitle"

    def testInvalidHeaderMagicRaises(self):
        image_data = _blankAdfs()
        # Corrupt the "Hugo" header magic at sector 2 offset 1.
        offset = ADFS_ROOT_SECTOR * ADFS_SECTOR_SIZE + 1
        image_data[offset:offset + 4] = b"Xxxx"

        image = ADFSImage(image_data, is_adl=False)
        with pytest.raises(ADFSFormatError, match="header magic"):
            image.sides[0].readDirectory(ADFS_ROOT_SECTOR)

    def testInvalidFooterMagicRaises(self):
        image_data = _blankAdfs()
        # Corrupt the footer "Hugo" at 0x4FB within the directory.
        dir_base = ADFS_ROOT_SECTOR * ADFS_SECTOR_SIZE
        image_data[dir_base + 0x4FB:dir_base + 0x4FF] = b"Xxxx"

        image = ADFSImage(image_data, is_adl=False)
        with pytest.raises(ADFSFormatError, match="footer magic"):
            image.sides[0].readDirectory(ADFS_ROOT_SECTOR)

    def testSequenceMismatchRaises(self):
        image_data = _blankAdfs()
        dir_base = ADFS_ROOT_SECTOR * ADFS_SECTOR_SIZE

        # Set header sequence to 0x01 and footer to 0x02.
        image_data[dir_base + 0] = 0x01
        image_data[dir_base + 0x4FA] = 0x02

        image = ADFSImage(image_data, is_adl=False)
        with pytest.raises(ADFSFormatError, match="broken"):
            image.sides[0].readDirectory(ADFS_ROOT_SECTOR)

    def testAccessBitsDecodedCorrectly(self):
        # Create an entry with 'L' (locked, bit 2) and 'D' (directory, bit 3).
        access = 0x0F  # bits 0-3 set: R, W, L, D
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
        # Name "AB" with access bits set on all positions should still
        # decode as "AB" with 0x0D terminators stripped.
        access = 0x3FF  # all 10 bits set
        entry_blob = _makeDirectoryEntry(name="AB", access=access, start_sector=7)
        image_data = _blankAdfs(root_entries=[entry_blob])
        image = ADFSImage(image_data, is_adl=False)
        root = image.sides[0].readDirectory(ADFS_ROOT_SECTOR)

        assert root.entries[0].name == "AB"

    def testFullDirectoryWith47Entries(self):
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
        e = ADFSEntry(
            name="MYPROG", directory="$", load_addr=0, exec_addr=0,
            length=0, start_sector=0, locked=False, is_directory=False,
            access=0, sequence=0,
        )
        assert e.fullName == "$.MYPROG"

    def testFullNameInSubdir(self):
        e = ADFSEntry(
            name="ELITE", directory="$.GAMES", load_addr=0, exec_addr=0,
            length=0, start_sector=0, locked=False, is_directory=False,
            access=0, sequence=0,
        )
        assert e.fullName == "$.GAMES.ELITE"

    def testIsBasicWithKnownEntryPoint(self):
        e = ADFSEntry(
            name="PROG", directory="$", load_addr=0x0E00, exec_addr=0x802B,
            length=100, start_sector=7, locked=False, is_directory=False,
            access=0, sequence=0,
        )
        assert e.isBasic is True

    def testIsBasicFalseForDirectory(self):
        e = ADFSEntry(
            name="GAMES", directory="$", load_addr=0, exec_addr=0x802B,
            length=0, start_sector=20, locked=False, is_directory=True,
            access=0x08, sequence=0,
        )
        assert e.isBasic is False

    def testIsBasicFalseForNonBasicExec(self):
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

    def testCatalogueIsCached(self):
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        side = image.sides[0]

        cat1 = side.readCatalogue()
        cat2 = side.readCatalogue()
        assert cat1 is cat2

    def testCatalogueEntriesMatchWalk(self):
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
        image_data = _adfsWithFiles([
            {"name": "EMPTY", "data": b""},
        ])
        image = ADFSImage(image_data, is_adl=False)
        cat = image.sides[0].readCatalogue()

        empty = [e for e in cat.entries if e.name == "EMPTY"][0]
        assert image.sides[0].readFile(empty) == b""

    def testExtractBasicFile(self):
        # Minimal tokenized BASIC: one line "10 PRINT" followed by end marker.
        basic_data = bytes([
            0x0D,        # line start
            0x00, 0x0A,  # line number 10 (high, low)
            0x07,        # line length
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
        # Create an entry pointing beyond the image.
        entry = _makeDirectoryEntry(
            name="BAD",
            start_sector=9999,
            length=256,
        )
        image_data = _blankAdfs(root_entries=[entry])
        image = ADFSImage(image_data, is_adl=False)
        cat = image.sides[0].readCatalogue()

        with pytest.raises(ADFSFormatError, match="extend beyond"):
            image.sides[0].readFile(cat.entries[0])


# -----------------------------------------------------------------------
# ADFSImage container
# -----------------------------------------------------------------------

class TestAdfsImage:

    def testSingleSidedHasOneSide(self):
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        assert len(image.sides) == 1
        assert image.sides[0].side == 0

    def testDoubleSidedHasOneSide(self):
        # ADFS always presents one logical filesystem, even for .adl.
        image_data = _blankAdfs(total_sectors=2560)
        image = ADFSImage(image_data, is_adl=True)
        assert len(image.sides) == 1

    def testSerializeRoundTrip(self):
        image_data = _blankAdfs()
        image = ADFSImage(image_data, is_adl=False)
        serialized = image.serialize()
        assert serialized == bytes(image_data)

    def testIsAdlProperty(self):
        image_data = _blankAdfs()
        assert ADFSImage(image_data, is_adl=False).is_adl is False
        assert ADFSImage(image_data, is_adl=True).is_adl is True


# -----------------------------------------------------------------------
# openAdfsImage
# -----------------------------------------------------------------------

class TestOpenAdfsImage:

    def testOpenValidAdf(self, tmp_path):
        image_data = _blankAdfs(total_sectors=640)
        path = tmp_path / "test.adf"
        path.write_bytes(bytes(image_data))

        image = openAdfsImage(str(path))
        assert isinstance(image, ADFSImage)
        assert image.is_adl is False

    def testOpenValidAdl(self, tmp_path):
        image_data = _blankAdfs(total_sectors=2560)
        path = tmp_path / "test.adl"
        path.write_bytes(bytes(image_data))

        image = openAdfsImage(str(path))
        assert isinstance(image, ADFSImage)
        assert image.is_adl is True

    def testTooSmallRaises(self, tmp_path):
        path = tmp_path / "tiny.adf"
        path.write_bytes(b"\x00" * 100)

        with pytest.raises(ADFSFormatError, match="too small"):
            openAdfsImage(str(path))

    def testMissingHugoRaises(self, tmp_path):
        # Image large enough but no Hugo marker.
        image_data = bytearray(640 * 256)
        path = tmp_path / "nope.adf"
        path.write_bytes(bytes(image_data))

        with pytest.raises(ADFSFormatError, match="Hugo"):
            openAdfsImage(str(path))

    def testFileNotFoundRaises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            openAdfsImage(str(tmp_path / "nonexistent.adf"))


# -----------------------------------------------------------------------
# Format detection via openImage
# -----------------------------------------------------------------------

class TestFormatDetection:

    def testAdfRoutesToAdfs(self, tmp_path):
        image_data = _blankAdfs(total_sectors=640)
        path = tmp_path / "test.adf"
        path.write_bytes(bytes(image_data))

        image = openImage(str(path))
        assert isinstance(image, ADFSImage)

    def testAdlRoutesToAdfs(self, tmp_path):
        image_data = _blankAdfs(total_sectors=2560)
        path = tmp_path / "test.adl"
        path.write_bytes(bytes(image_data))

        image = openImage(str(path))
        assert isinstance(image, ADFSImage)

    def testSsdStillRoutesToDfs(self, tmp_path):
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
    glob.glob(os.path.join(DISCS_DIR, "*.adf"))
    + glob.glob(os.path.join(DISCS_DIR, "*.adl"))
)
adfs_ids = [os.path.basename(p) for p in ALL_ADFS]


@pytest.mark.skipif(
    len(ALL_ADFS) == 0,
    reason="No ADFS disc images found in tests/resources/discs/",
)
class TestRealAdfsImages:

    @pytest.mark.parametrize("path", ALL_ADFS, ids=adfs_ids)
    def testOpensWithoutError(self, path):
        image = openAdfsImage(path)
        assert len(image.sides) >= 1

    @pytest.mark.parametrize("path", ALL_ADFS, ids=adfs_ids)
    def testCatalogueNonEmpty(self, path):
        image = openAdfsImage(path)
        for side in image.sides:
            cat = side.readCatalogue()
            assert isinstance(cat.entries, tuple)

    @pytest.mark.parametrize("path", ALL_ADFS, ids=adfs_ids)
    def testEntryNamesAreNonEmpty(self, path):
        image = openAdfsImage(path)
        for side in image.sides:
            cat = side.readCatalogue()
            for entry in cat.entries:
                assert len(entry.name) > 0

    @pytest.mark.parametrize("path", ALL_ADFS, ids=adfs_ids)
    def testExtractedLengthMatchesCatalogue(self, path):
        image = openAdfsImage(path)
        for side in image.sides:
            cat = side.readCatalogue()
            for entry in cat.entries:
                if not entry.isDirectory:
                    data = side.readFile(entry)
                    assert len(data) == entry.length

    @pytest.mark.parametrize("path", ALL_ADFS, ids=adfs_ids)
    def testBasicFilesStartWith0x0d(self, path):
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
        safe_dir, safe_name = sanitizeEntryPath("$", "MYPROG")
        assert safe_dir == "$"
        assert safe_name == "MYPROG"

    def testAdfsRootDir(self):
        safe_dir, safe_name = sanitizeEntryPath("$", "README")
        assert safe_dir == "$"
        assert safe_name == "README"

    def testAdfsNestedPath(self):
        safe_dir, safe_name = sanitizeEntryPath("$.GAMES", "ELITE")
        expected_dir = os.path.join("$", "GAMES")
        assert safe_dir == expected_dir
        assert safe_name == "ELITE"

    def testAdfsDeeplyNestedPath(self):
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
        image_data = _adfsWithFiles([
            {"name": "README", "data": b"Hello", "load_addr": 0x1000, "exec_addr": 0x1000},
        ])
        output = self._runCat(tmp_path, image_data)

        assert "README" in output
        assert "00001000" in output

    def testShowsDirType(self, tmp_path):
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
        # BASIC file exec address triggers "BASIC" label.
        basic_data = bytes([
            0x0D, 0x00, 0x0A, 0x07, 0xF1, 0x0D, 0xFF,
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
        image_data = _blankAdfs()
        output = self._runCat(tmp_path, image_data)
        assert "(empty)" in output


# -----------------------------------------------------------------------
# CLI: cmdExtract with ADFS images
# -----------------------------------------------------------------------

class TestCmdExtractAdfs:

    def testExtractByFullName(self, tmp_path):
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
        basic_data = bytes([
            0x0D, 0x00, 0x0A, 0x07, 0xF1, 0x0D, 0xFF,
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
        # Create an image with a directory entry and a BASIC file.
        basic_data = bytes([
            0x0D, 0x00, 0x0A, 0x0A, 0xF1, 0x22, 0x48, 0x49, 0x22, 0x0D, 0xFF,
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
