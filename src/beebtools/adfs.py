# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""ADFS disc image reader (old map, small directory).

Supports .adf (single-sided) and .adl (double-sided sequential) formats.
Handles ADFS-S (160K), ADFS-M (320K), and ADFS-L (640K) disc images.
Provides catalogue parsing, directory walking, and file extraction for
Acorn ADFS old-map disc images.

Reference: https://mdfs.net/Docs/Comp/Disk/Format/ADFS

Classes:
    ADFSEntry        -- one file entry from an ADFS directory (frozen dataclass)
    ADFSDirectory    -- one parsed ADFS directory (frozen dataclass)
    ADFSFreeSpaceMap -- parsed free space map from sectors 0-1 (frozen dataclass)
    ADFSCatalogue    -- flattened catalogue for duck-typing compat (frozen dataclass)
    ADFSSide         -- sector I/O, directory parsing, and file extraction
    ADFSImage        -- disc image container

Exceptions:
    ADFSError       -- base exception for all ADFS errors
    ADFSFormatError -- raised when the disc image is structurally invalid
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .dfs import BootOption


# -----------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------

ADFS_SECTOR_SIZE = 256
ADFS_SECTORS_PER_TRACK = 16
ADFS_DIR_LENGTH = 0x500        # 5 sectors for small (Hugo) directories
ADFS_HEADER_SIZE = 5           # 1 sequence byte + 4-byte "Hugo" marker
ADFS_ENTRY_SIZE = 0x1A         # 26 bytes per directory entry
ADFS_MAX_ENTRIES = 47          # maximum entries per directory
ADFS_ROOT_SECTOR = 2           # root directory starts at sector 2
ADFS_HUGO_MAGIC = b"Hugo"

# Footer layout within a 0x500-byte directory block.
_FOOTER_END_MARKER = 0x4CB     # 0x00 byte marking the end of entries
_FOOTER_NAME = 0x4CC           # 10-byte directory name
_FOOTER_PARENT = 0x4D6         # 3-byte parent sector
_FOOTER_TITLE = 0x4D9          # 19-byte directory title
_FOOTER_SEQ = 0x4FA            # master sequence number (BCD)
_FOOTER_HUGO = 0x4FB           # 4-byte "Hugo" marker


# -----------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------

class ADFSError(Exception):
    """Base exception for ADFS disc image errors."""


class ADFSFormatError(ADFSError):
    """Raised when a disc image is structurally invalid or corrupted."""


# -----------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------

@dataclass(frozen=True)
class ADFSEntry:
    """One file or directory entry from an ADFS directory.

    All numeric fields are decoded from the packed directory entry
    described in the ADFS disc format specification. Access bits are
    stored in bit 7 of the ten name bytes.
    """

    name: str
    directory: str
    load_addr: int
    exec_addr: int
    length: int
    start_sector: int
    locked: bool
    is_directory: bool
    access: int
    sequence: int

    @property
    def fullName(self) -> str:
        """Full ADFS path, e.g. '$.GAMES.ELITE'."""
        if self.directory == "$":
            return f"$.{self.name}"
        return f"{self.directory}.{self.name}"

    @property
    def isBasic(self) -> bool:
        """True if this entry looks like a BBC BASIC program.

        Uses the same execution address test as DFS. Directories are
        never treated as BASIC.
        """
        if self.is_directory:
            return False
        exec_lo = self.exec_addr & 0xFFFF
        return exec_lo in (0x801F, 0x8023, 0x802B)


@dataclass(frozen=True)
class ADFSDirectory:
    """One parsed ADFS directory.

    Represents the raw directory structure before flattening. Each
    entry may itself be a directory, forming a tree.
    """

    name: str
    title: str
    parent_sector: int
    sequence: int
    entries: Tuple[ADFSEntry, ...]


@dataclass(frozen=True)
class ADFSFreeSpaceMap:
    """Parsed free space map from ADFS sectors 0 and 1.

    Each block is a (start_sector, length_in_sectors) pair describing
    one contiguous free region.
    """

    blocks: Tuple[Tuple[int, int], ...]
    total_sectors: int
    disc_id: int
    boot_option: BootOption


@dataclass(frozen=True)
class ADFSCatalogue:
    """Flattened catalogue for duck-typing compatibility with DFSCatalogue.

    Entries from the entire directory tree are flattened into a single
    tuple with full paths in each entry's directory field.
    """

    title: str
    cycle: int
    boot_option: BootOption
    disc_size: int
    entries: Tuple[ADFSEntry, ...]


# -----------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------

def _adfsChecksum(data: bytes) -> int:
    """Compute the ADFS free space map checksum for a 256-byte sector.

    Starts at 255, then adds bytes counting downward from byte 254 to
    byte 0, propagating carry after each addition. The checksum itself
    lives at byte 255 and is not included in the computation.
    """
    total = 255

    for i in range(254, -1, -1):
        if total > 255:
            total = (total + 1) & 0xFF
        total += data[i]

    return total & 0xFF


def _decodeString(data: bytes) -> str:
    """Decode an ADFS directory string terminated by 0x0D or 0x00.

    ADFS strings are not space-padded. Characters are masked to 7 bits
    as a safety measure (footer strings have no access bits, but this
    ensures clean ASCII regardless).
    """
    chars = []

    for b in data:
        if b == 0x0D or b == 0x00:
            break
        chars.append(chr(b & 0x7F))

    return "".join(chars)


def _read24le(data: bytes, offset: int) -> int:
    """Read a 24-bit little-endian unsigned integer."""
    return data[offset] | (data[offset + 1] << 8) | (data[offset + 2] << 16)


def _read32le(data: bytes, offset: int) -> int:
    """Read a 32-bit little-endian unsigned integer."""
    return (
        data[offset]
        | (data[offset + 1] << 8)
        | (data[offset + 2] << 16)
        | (data[offset + 3] << 24)
    )


# -----------------------------------------------------------------------
# ADFSSide - sector I/O and directory parsing
# -----------------------------------------------------------------------

class ADFSSide:
    """Reader for an ADFS disc image filesystem.

    ADFS uses a single flat logical sector space regardless of whether
    the physical disc is single-sided or double-sided. The side number
    is always 0 for compatibility with the duck-typing interface.
    """

    def __init__(self, image: "ADFSImage", side: int) -> None:
        """Create an ADFS side reader.

        Args:
            image: Parent ADFSImage that owns the backing data.
            side:  Side number (always 0 for ADFS).
        """
        self._image = image
        self._side = side
        self._catalogue: Optional[ADFSCatalogue] = None
        self._fsm: Optional[ADFSFreeSpaceMap] = None

    @property
    def side(self) -> int:
        """Side number (always 0 for ADFS)."""
        return self._side

    # -------------------------------------------------------------------
    # Sector access
    # -------------------------------------------------------------------

    def _sectorOffset(self, sector_num: int) -> int:
        """Byte offset of a logical sector in the backing store.

        ADFS uses sequential track layout with no interleaving.
        Logical sectors map directly to byte offsets.
        """
        return sector_num * ADFS_SECTOR_SIZE

    def _readSector(self, sector_num: int) -> bytes:
        """Read one 256-byte logical sector."""
        offset = self._sectorOffset(sector_num)
        end = offset + ADFS_SECTOR_SIZE

        if end > len(self._image.data):
            raise ADFSFormatError(
                f"Sector {sector_num} at offset {offset} extends beyond "
                f"the image ({len(self._image.data)} bytes)"
            )

        return bytes(self._image.data[offset:end])

    def _readSectors(self, start_sector: int, count: int) -> bytes:
        """Read multiple contiguous sectors as a single block."""
        offset = self._sectorOffset(start_sector)
        end = offset + count * ADFS_SECTOR_SIZE

        if end > len(self._image.data):
            raise ADFSFormatError(
                f"Sectors {start_sector}-{start_sector + count - 1} "
                f"extend beyond the image ({len(self._image.data)} bytes)"
            )

        return bytes(self._image.data[offset:end])

    # -------------------------------------------------------------------
    # Free space map
    # -------------------------------------------------------------------

    def readFreeSpaceMap(self) -> ADFSFreeSpaceMap:
        """Parse the free space map from sectors 0 and 1.

        Validates both sector checksums. The map contains up to 82
        free-space block descriptors plus disc metadata.

        Raises:
            ADFSFormatError: If either checksum is invalid.
        """
        if self._fsm is not None:
            return self._fsm

        sec0 = self._readSector(0)
        sec1 = self._readSector(1)

        # Validate checksums. Each sector's checksum is at byte 0xFF
        # and covers bytes 0x00-0xFE counting downward.
        expected0 = sec0[0xFF]
        computed0 = _adfsChecksum(sec0)

        if computed0 != expected0:
            raise ADFSFormatError(
                f"Free space map sector 0 checksum mismatch: "
                f"expected 0x{expected0:02X}, got 0x{computed0:02X}"
            )

        expected1 = sec1[0xFF]
        computed1 = _adfsChecksum(sec1)

        if computed1 != expected1:
            raise ADFSFormatError(
                f"Free space map sector 1 checksum mismatch: "
                f"expected 0x{expected1:02X}, got 0x{computed1:02X}"
            )

        # Total sectors on disc (3-byte LE at sector 0 offset 0xFC).
        total_sectors = _read24le(sec0, 0xFC)

        # Disc identifier (2-byte LE at sector 1 offset 0xFB).
        disc_id = sec1[0xFB] | (sec1[0xFC] << 8)

        # Boot option (sector 1 offset 0xFD).
        raw_boot = sec1[0xFD] & 0x03
        boot_option = BootOption(raw_boot)

        # End-of-list pointer (sector 1 offset 0xFE) gives the byte
        # offset just past the last 3-byte length entry. Dividing by
        # 3 gives the number of free-space blocks.
        list_end = sec1[0xFE]
        block_count = list_end // 3

        # Parse the (start, length) pairs from sectors 0 and 1.
        blocks: List[Tuple[int, int]] = []

        for i in range(block_count):
            base = i * 3
            start = _read24le(sec0, base)
            length = _read24le(sec1, base)
            blocks.append((start, length))

        self._fsm = ADFSFreeSpaceMap(
            blocks=tuple(blocks),
            total_sectors=total_sectors,
            disc_id=disc_id,
            boot_option=boot_option,
        )

        return self._fsm

    # -------------------------------------------------------------------
    # Directory parsing
    # -------------------------------------------------------------------

    def readDirectory(self, sector: int) -> ADFSDirectory:
        """Parse an ADFS directory at the given sector.

        Reads 5 sectors (0x500 bytes), validates the Hugo markers and
        sequence numbers, and decodes all entries.

        Args:
            sector: Starting sector of the directory.

        Returns:
            Parsed ADFSDirectory.

        Raises:
            ADFSFormatError: If the directory is broken or malformed.
        """
        raw = self._readSectors(sector, 5)

        # Validate header: sequence byte at 0, "Hugo" at bytes 1-4.
        header_seq = raw[0]
        header_magic = raw[1:5]

        if header_magic != ADFS_HUGO_MAGIC:
            raise ADFSFormatError(
                f"Directory at sector {sector} has invalid header magic "
                f"{header_magic!r} (expected b'Hugo')"
            )

        # Validate footer: sequence byte at 0x4FA, "Hugo" at 0x4FB-0x4FE.
        footer_seq = raw[_FOOTER_SEQ]
        footer_magic = raw[_FOOTER_HUGO:_FOOTER_HUGO + 4]

        if footer_magic != ADFS_HUGO_MAGIC:
            raise ADFSFormatError(
                f"Directory at sector {sector} has invalid footer magic "
                f"{footer_magic!r} (expected b'Hugo')"
            )

        # Header and footer sequence bytes must match.
        if header_seq != footer_seq:
            raise ADFSFormatError(
                f"Directory at sector {sector} is broken: header sequence "
                f"0x{header_seq:02X} != footer sequence 0x{footer_seq:02X}"
            )

        # Parse footer metadata.
        dir_name = _decodeString(raw[_FOOTER_NAME:_FOOTER_NAME + 10])
        parent_sector = _read24le(raw, _FOOTER_PARENT)
        dir_title = _decodeString(raw[_FOOTER_TITLE:_FOOTER_TITLE + 19])

        # Parse entries starting at offset 5, each 26 bytes.
        entries: List[ADFSEntry] = []
        offset = ADFS_HEADER_SIZE

        for _ in range(ADFS_MAX_ENTRIES):
            # A zero first byte (with bit 7 masked) marks end of entries.
            if (raw[offset] & 0x7F) == 0x00:
                break

            entry = self._parseEntry(raw, offset)
            entries.append(entry)
            offset += ADFS_ENTRY_SIZE

        return ADFSDirectory(
            name=dir_name,
            title=dir_title,
            parent_sector=parent_sector,
            sequence=header_seq,
            entries=tuple(entries),
        )

    def _parseEntry(self, raw: bytes, offset: int) -> ADFSEntry:
        """Decode one 26-byte directory entry.

        Access bits are stored in bit 7 of the ten name bytes.
        The name characters occupy bits 0-6.
        """
        # Extract access bits from bit 7 of bytes 0-9.
        access = 0

        for i in range(10):
            if raw[offset + i] & 0x80:
                access |= (1 << i)

        # Extract name characters from bits 0-6.
        name_chars = []

        for i in range(10):
            ch = raw[offset + i] & 0x7F
            if ch == 0x0D or ch == 0x00:
                break
            name_chars.append(chr(ch))

        name = "".join(name_chars)

        # Decode access flags of interest.
        locked = bool(access & 0x04)        # bit 2 = 'L'
        is_directory = bool(access & 0x08)  # bit 3 = 'D'

        load_addr = _read32le(raw, offset + 0x0A)
        exec_addr = _read32le(raw, offset + 0x0E)
        length = _read32le(raw, offset + 0x12)
        start_sector = _read24le(raw, offset + 0x16)
        sequence = raw[offset + 0x19]

        return ADFSEntry(
            name=name,
            directory="",  # set by the directory walker
            load_addr=load_addr,
            exec_addr=exec_addr,
            length=length,
            start_sector=start_sector,
            locked=locked,
            is_directory=is_directory,
            access=access,
            sequence=sequence,
        )

    # -------------------------------------------------------------------
    # Directory tree walker
    # -------------------------------------------------------------------

    def walkDirectories(
        self, sector: int = ADFS_ROOT_SECTOR, path: str = "$"
    ) -> List[ADFSEntry]:
        """Recursively walk the directory tree and flatten all entries.

        Each entry's directory field is set to the full path of its
        parent directory, providing DFS-style flat catalogue compatibility.

        Args:
            sector: Starting sector of the directory to walk.
            path:   Path prefix for entries in this directory.

        Returns:
            Flat list of all ADFSEntry objects with full paths.
        """
        directory = self.readDirectory(sector)
        result: List[ADFSEntry] = []

        for entry in directory.entries:
            # Rebuild the entry with the directory path filled in.
            located = ADFSEntry(
                name=entry.name,
                directory=path,
                load_addr=entry.load_addr,
                exec_addr=entry.exec_addr,
                length=entry.length,
                start_sector=entry.start_sector,
                locked=entry.locked,
                is_directory=entry.is_directory,
                access=entry.access,
                sequence=entry.sequence,
            )
            result.append(located)

            # Recurse into subdirectories.
            if entry.is_directory:
                child_path = f"{path}.{entry.name}"
                result.extend(
                    self.walkDirectories(entry.start_sector, child_path)
                )

        return result

    # -------------------------------------------------------------------
    # Catalogue (duck-typing interface)
    # -------------------------------------------------------------------

    def readCatalogue(self) -> ADFSCatalogue:
        """Parse and return a flattened catalogue for this filesystem.

        Walks the entire directory tree and combines entries with
        metadata from the free space map. The result is cached after
        the first successful call.

        Returns:
            ADFSCatalogue with all files from all directories.
        """
        if self._catalogue is not None:
            return self._catalogue

        fsm = self.readFreeSpaceMap()
        root = self.readDirectory(ADFS_ROOT_SECTOR)
        flat_entries = self.walkDirectories()

        self._catalogue = ADFSCatalogue(
            title=root.title,
            cycle=root.sequence,
            boot_option=fsm.boot_option,
            disc_size=fsm.total_sectors,
            entries=tuple(flat_entries),
        )

        return self._catalogue

    # -------------------------------------------------------------------
    # File extraction
    # -------------------------------------------------------------------

    def readFile(self, entry: ADFSEntry) -> bytes:
        """Read raw bytes for one catalogued file.

        Reads contiguous sectors from the entry's start sector and
        truncates to the recorded file length.

        Args:
            entry: An ADFSEntry from this filesystem's catalogue.

        Returns:
            File bytes, exactly entry.length long.
        """
        if entry.length == 0:
            return b""

        sectors_needed = (
            (entry.length + ADFS_SECTOR_SIZE - 1) // ADFS_SECTOR_SIZE
        )
        data = self._readSectors(entry.start_sector, sectors_needed)

        return data[:entry.length]


# -----------------------------------------------------------------------
# ADFSImage - disc image container
# -----------------------------------------------------------------------

class ADFSImage:
    """Read-only ADFS disc image container.

    Owns the bytearray backing store and provides an ADFSSide view
    for the filesystem. Both ADF (single-sided) and ADL (double-sided
    sequential) layouts are supported as one logical filesystem.
    """

    def __init__(self, data: bytearray, is_adl: bool) -> None:
        """Wrap an existing image bytearray.

        Args:
            data:   Backing store for the disc image.
            is_adl: True for .adl double-sided sequential format.
        """
        self._data = data
        self._is_adl = is_adl
        self._sides: List[ADFSSide] = [ADFSSide(self, 0)]

    @property
    def data(self) -> bytearray:
        """The backing store."""
        return self._data

    @property
    def is_adl(self) -> bool:
        """True for double-sided sequential format."""
        return self._is_adl

    @property
    def sides(self) -> List[ADFSSide]:
        """List of ADFSSide readers (always one element for ADFS)."""
        return list(self._sides)

    def serialize(self) -> bytes:
        """Return the disc image as immutable bytes for writing to a file."""
        return bytes(self._data)


# -----------------------------------------------------------------------
# Module-level functions
# -----------------------------------------------------------------------

def openAdfsImage(path: str) -> ADFSImage:
    """Open an ADFS disc image file and return an ADFSImage.

    Format is inferred from the file extension:
        .adf  -- single-sided (ADFS-S or ADFS-M)
        .adl  -- double-sided sequential (ADFS-L)

    Validates the minimum image size and checks for the Hugo directory
    marker at sector 2.

    Raises:
        ADFSFormatError: If the image is too small or not a valid ADFS image.
        FileNotFoundError: If the path does not exist.
    """
    with open(path, "rb") as f:
        raw = f.read()

    ext = path.lower()
    is_adl = ext.endswith(".adl")

    # Minimum size: free space map (2 sectors) + root directory (5 sectors).
    min_size = 7 * ADFS_SECTOR_SIZE
    fmt_name = "ADL" if is_adl else "ADF"

    if len(raw) < min_size:
        raise ADFSFormatError(
            f"Image is {len(raw)} bytes, too small for {fmt_name} format "
            f"(minimum {min_size} bytes)"
        )

    # Verify Hugo marker at the start of the root directory (sector 2).
    # The marker is at bytes 1-4 of the directory block.
    hugo_offset = ADFS_ROOT_SECTOR * ADFS_SECTOR_SIZE + 1

    if raw[hugo_offset:hugo_offset + 4] != ADFS_HUGO_MAGIC:
        raise ADFSFormatError(
            "No 'Hugo' directory marker found at sector 2 - "
            "not a valid old-map ADFS image"
        )

    return ADFSImage(bytearray(raw), is_adl)
