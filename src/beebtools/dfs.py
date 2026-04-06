# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""DFS disc image reader.

Supports .ssd (single-sided) and .dsd (double-sided interleaved) formats.
Provides catalogue parsing, file extraction, and the foundation for disc
image creation for Acorn DFS disc images.

Reference: https://beebwiki.mdfs.net/Acorn_DFS_disc_format

Classes:
    DFSEntry      -- one file entry from a DFS catalogue (frozen dataclass)
    DFSCatalogue  -- parsed catalogue for one disc side (frozen dataclass)
    DFSSide       -- sector I/O, catalogue parsing, and file extraction
    DFSImage      -- mutable disc image container, owns the backing store

Exceptions:
    DFSError       -- base exception for all DFS errors
    DFSFormatError -- raised when the disc image is structurally invalid
"""

from dataclasses import dataclass, replace
from typing import Iterator, List, Optional, Tuple

from .boot import BootOption
from .entry import DiscError, DiscFormatError, DiscFile, isBasicExecAddr


SECTOR_SIZE = 256
SECTORS_PER_TRACK = 10


# -----------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------

class DFSError(DiscError):
    """Base exception for DFS disc image errors."""


class DFSFormatError(DFSError, DiscFormatError):
    """Raised when a disc image is structurally invalid or corrupted."""


# -----------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------

@dataclass(frozen=True)
class DFSEntry:
    """One file entry from a DFS catalogue.

    All numeric fields are decoded from the packed catalogue format
    described in the Acorn DFS disc format specification.
    """

    name: str
    directory: str
    load_addr: int
    exec_addr: int
    length: int
    start_sector: int
    locked: bool

    @property
    def fullName(self) -> str:
        """Full DFS filename with directory prefix, e.g. 'T.MYPROG'."""
        return f"{self.directory}.{self.name}"

    @property
    def isBasic(self) -> bool:
        """True if this entry looks like a BBC BASIC program.

        Checks the execution address for the well-known BASIC entry points
        0x801F, 0x8023, and 0x802B written by the SAVE command.
        """
        return isBasicExecAddr(self.exec_addr)

    @property
    def isDirectory(self) -> bool:
        """Always False for DFS entries. DFS has no subdirectories."""
        return False

    def __repr__(self) -> str:
        """Show class name, full path, load/exec addresses, and length."""
        return (f"DFSEntry('{self.fullName}', "
                f"load=0x{self.load_addr:04X}, "
                f"exec=0x{self.exec_addr:04X}, "
                f"length={self.length})")

    def __str__(self) -> str:
        """Return the full DFS filename (e.g. 'T.MYPROG')."""
        return self.fullName

    def __fspath__(self) -> str:
        """Host-safe path: replace the DFS directory separator with '/'."""
        return f"{self.directory}/{self.name}"


@dataclass(frozen=True)
class DFSCatalogue:
    """Parsed catalogue for one side of a DFS disc.

    The entries tuple preserves on-disc catalogue order (descending start
    sector). Use sortCatalogueEntries() to reorder for display.
    """

    title: str
    cycle: int
    boot_option: BootOption
    disc_size: int
    entries: Tuple[DFSEntry, ...]

    @property
    def tracks(self) -> int:
        """Number of tracks on this disc side (disc_size / 10)."""
        return self.disc_size // SECTORS_PER_TRACK


# -----------------------------------------------------------------------
# DFSSide - sector I/O and catalogue parsing for one disc side
# -----------------------------------------------------------------------

class DFSSide:
    """Reader for one side of a DFS disc image.

    Provides sector-level access, catalogue parsing with validation, and
    file extraction with bounds checking. The catalogue is parsed lazily
    on the first call to readCatalogue() and cached thereafter.
    """

    def __init__(self, image: "DFSImage", side: int) -> None:
        """Create a DFS side reader.

        Args:
            image: Parent DFSImage that owns the backing data.
            side:  Side number (0 or 1) this instance represents.
        """
        self._image = image
        self._side = side
        self._catalogue: Optional[DFSCatalogue] = None

    @property
    def side(self) -> int:
        """Side number (0 or 1) this reader represents."""
        return self._side

    @property
    def maxTitleLength(self) -> int:
        """Maximum disc title length for DFS (12 characters)."""
        return 12

    def mkdir(self, path: str) -> None:
        """No-op: DFS directories are implicit single-character prefixes."""
        pass

    # -------------------------------------------------------------------
    # Python data model
    # -------------------------------------------------------------------

    def __repr__(self) -> str:
        """Show class name, disc title, entry count, and free space."""
        cat = self.readCatalogue()
        return (f"DFSSide(title='{cat.title}', "
                f"{len(cat.entries)} entries, "
                f"{self.freeSpace()} sectors free)")

    def __iter__(self) -> Iterator[DFSEntry]:
        """Yield catalogue entries for this side."""
        return iter(self.readCatalogue().entries)

    def __len__(self) -> int:
        """Number of catalogue entries on this side."""
        return len(self.readCatalogue().entries)

    def __getitem__(self, key: str) -> DFSEntry:
        """Look up a catalogue entry by full path (e.g. 'T.MYPROG')."""
        for entry in self.readCatalogue().entries:
            if entry.fullName == key:
                return entry
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        """True if an entry with the given full path exists."""
        if not isinstance(key, str):
            return False
        return any(e.fullName == key for e in self.readCatalogue().entries)

    # -------------------------------------------------------------------
    # Sector access
    # -------------------------------------------------------------------

    def _sectorOffset(self, sector_num: int) -> int:
        """Byte offset of a logical sector within the backing store.

        DSD images interleave both sides within each track: track 0
        side 0 (sectors 0-9), track 0 side 1 (sectors 0-9), track 1
        side 0, and so on. SSD images are a flat sequence of sectors.
        """
        track = sector_num // SECTORS_PER_TRACK
        sector_in_track = sector_num % SECTORS_PER_TRACK

        if self._image.is_dsd:
            return (track * 20 + self._side * 10 + sector_in_track) * SECTOR_SIZE

        return sector_num * SECTOR_SIZE

    def _readSector(self, sector_num: int) -> bytes:
        """Read one 256-byte logical sector from this side.

        Raises DFSFormatError if the sector lies beyond the image.
        """
        offset = self._sectorOffset(sector_num)
        end = offset + SECTOR_SIZE

        if end > len(self._image.data):
            raise DFSFormatError(
                f"Sector {sector_num} (side {self._side}) at image offset "
                f"{offset} extends beyond the image "
                f"({len(self._image.data)} bytes)"
            )

        return bytes(self._image.data[offset:end])

    def _writeSector(self, sector_num: int, data: bytes) -> None:
        """Write one 256-byte logical sector to this side.

        Raises DFSFormatError if the sector lies beyond the image.
        Raises ValueError if data is not exactly 256 bytes.
        """
        if len(data) != SECTOR_SIZE:
            raise ValueError(
                f"Sector data must be exactly {SECTOR_SIZE} bytes, "
                f"got {len(data)}"
            )

        offset = self._sectorOffset(sector_num)
        end = offset + SECTOR_SIZE

        if end > len(self._image.data):
            raise DFSFormatError(
                f"Sector {sector_num} (side {self._side}) at image offset "
                f"{offset} extends beyond the image "
                f"({len(self._image.data)} bytes)"
            )

        self._image.data[offset:end] = data

    # -------------------------------------------------------------------
    # Catalogue parsing
    # -------------------------------------------------------------------

    def readCatalogue(self) -> DFSCatalogue:
        """Parse and return the DFS catalogue for this side.

        The result is cached after the first successful call. Write
        operations that modify the catalogue clear the cache.

        Raises:
            DFSFormatError: If the catalogue data is invalid.
        """
        if self._catalogue is not None:
            return self._catalogue

        sec0 = self._readSector(0)
        sec1 = self._readSector(1)

        # --- Disc title ---
        # First 8 characters in sector 0, next 4 in sector 1. Padded with
        # either NULs (DFS 0.90/0.98/2.00+) or spaces (DFS 1.00/1.20).
        title = (
            bytes(sec0[0:8]) + bytes(sec1[0:4])
        ).decode("bbc").rstrip("\x00 ")

        # --- Cycle number ---
        # BCD value in sector 1 byte 4, incremented each catalogue write.
        cycle = sec1[4]

        # --- File count ---
        # Sector 1 byte 5 holds the offset to the last valid file entry,
        # which is 8 * number_of_files. Must be a multiple of 8.
        raw_offset = sec1[5]

        if raw_offset % 8 != 0:
            raise DFSFormatError(
                f"Catalogue file offset byte is {raw_offset} "
                f"(0x{raw_offset:02X}), not a multiple of 8"
            )

        file_count = raw_offset // 8

        if file_count > 31:
            raise DFSFormatError(
                f"File count {file_count} exceeds the DFS maximum of 31"
            )

        # --- Descriptor byte (sector 1 byte 6) ---
        # Bits 4-5: boot option (0-3).
        # Bits 0-1: high two bits of disc size.
        descriptor = sec1[6]
        boot_option = BootOption((descriptor >> 4) & 0x03)
        disc_size_hi = descriptor & 0x03

        # --- Disc size ---
        # Low 8 bits in sector 1 byte 7, high 2 bits from the descriptor.
        disc_size = sec1[7] | (disc_size_hi << 8)

        # --- File entries ---
        entries: List[DFSEntry] = []

        for i in range(file_count):
            entry = self._parseEntry(sec0, sec1, i)
            entries.append(entry)

        self._catalogue = DFSCatalogue(
            title=title,
            cycle=cycle,
            boot_option=boot_option,
            disc_size=disc_size,
            entries=tuple(entries),
        )
        return self._catalogue

    def _parseEntry(self, sec0: bytes, sec1: bytes, index: int) -> DFSEntry:
        """Decode one file entry from the raw catalogue sectors.

        Args:
            sec0:  256 bytes of catalogue sector 0 (names and directories).
            sec1:  256 bytes of catalogue sector 1 (addresses and lengths).
            index: Entry index (0-based).

        Returns:
            Fully decoded DFSEntry.
        """
        base = 8 + index * 8

        # --- Name and directory from sector 0 ---
        # Bytes base..base+6 hold the 7-character filename, space-padded.
        # Byte base+7 holds the directory character in the low 7 bits
        # and the locked attribute in the high bit.
        dir_byte = sec0[base + 7]

        locked = bool(dir_byte & 0x80)
        directory = chr(dir_byte & 0x7F)
        name = sec0[base : base + 7].decode("bbc").rstrip()

        # --- Addresses, length, and start sector from sector 1 ---
        # Low 16 bits of each address/length are stored in byte pairs.
        load_lo = sec1[base] | (sec1[base + 1] << 8)
        exec_lo = sec1[base + 2] | (sec1[base + 3] << 8)
        length_lo = sec1[base + 4] | (sec1[base + 5] << 8)

        # Extra bits are packed into byte base+6:
        #   bits 0-1  start sector high (10-bit LBA)
        #   bits 2-3  load address high  (18-bit)
        #   bits 4-5  length high        (18-bit)
        #   bits 6-7  exec address high  (18-bit)
        extra = sec1[base + 6]
        start_sector = sec1[base + 7] | ((extra & 0x03) << 8)

        load_hi = (extra >> 2) & 0x03
        length_hi = (extra >> 4) & 0x03
        exec_hi = (extra >> 6) & 0x03

        load_addr = load_lo | (load_hi << 16)
        exec_addr = exec_lo | (exec_hi << 16)
        length = length_lo | (length_hi << 16)

        # Sectors 0 and 1 are the catalogue itself. A non-empty file
        # starting there would be corrupt.
        if start_sector < 2 and length > 0:
            raise DFSFormatError(
                f"Entry '{directory}.{name}' has start sector "
                f"{start_sector} which overlaps the catalogue"
            )

        return DFSEntry(
            name=name,
            directory=directory,
            load_addr=load_addr,
            exec_addr=exec_addr,
            length=length,
            start_sector=start_sector,
            locked=locked,
        )

    # -------------------------------------------------------------------
    # Catalogue encoding (write path)
    # -------------------------------------------------------------------

    @staticmethod
    def _encodeTitle(title: str) -> Tuple[bytes, bytes]:
        """Encode a disc title into sector 0 and sector 1 fragments.

        The title is truncated to 12 characters and NUL-padded per the
        DFS 2.00+ convention.

        Returns:
            Tuple of (8 bytes for sec0[0:8], 4 bytes for sec1[0:4]).
        """
        raw = title[:12].encode("bbc").ljust(12, b"\x00")
        return raw[:8], raw[8:12]

    @staticmethod
    def _encodeEntry(entry: DFSEntry) -> Tuple[bytes, bytes]:
        """Encode one DFSEntry into the 8-byte sector 0 and sector 1 chunks.

        Returns:
            Tuple of (8 bytes for sec0, 8 bytes for sec1).
        """
        # --- Sector 0: name + directory/locked ---
        name_bytes = entry.name[:7].encode("bbc").ljust(7, b" ")
        dir_byte = ord(entry.directory) & 0x7F
        if entry.locked:
            dir_byte |= 0x80
        sec0_chunk = name_bytes + bytes([dir_byte])

        # --- Sector 1: addresses, length, start sector ---
        load_lo_0 = entry.load_addr & 0xFF
        load_lo_1 = (entry.load_addr >> 8) & 0xFF
        exec_lo_0 = entry.exec_addr & 0xFF
        exec_lo_1 = (entry.exec_addr >> 8) & 0xFF
        length_lo_0 = entry.length & 0xFF
        length_lo_1 = (entry.length >> 8) & 0xFF

        # Pack the extra bits into one byte:
        #   bits 0-1  start sector high
        #   bits 2-3  load address bits 16-17
        #   bits 4-5  length bits 16-17
        #   bits 6-7  exec address bits 16-17
        start_hi = (entry.start_sector >> 8) & 0x03
        load_hi = (entry.load_addr >> 16) & 0x03
        length_hi = (entry.length >> 16) & 0x03
        exec_hi = (entry.exec_addr >> 16) & 0x03

        extra = start_hi | (load_hi << 2) | (length_hi << 4) | (exec_hi << 6)
        start_lo = entry.start_sector & 0xFF

        sec1_chunk = bytes([
            load_lo_0, load_lo_1,
            exec_lo_0, exec_lo_1,
            length_lo_0, length_lo_1,
            extra,
            start_lo,
        ])

        return sec0_chunk, sec1_chunk

    @staticmethod
    def _bcdIncrement(value: int) -> int:
        """Increment a BCD-encoded byte, wrapping 0x99 -> 0x00.

        Each nibble represents a decimal digit (0-9). The low nibble is
        incremented first; if it overflows, the high nibble is incremented.
        """
        lo = value & 0x0F
        hi = (value >> 4) & 0x0F

        lo += 1
        if lo > 9:
            lo = 0
            hi += 1
            if hi > 9:
                hi = 0

        return (hi << 4) | lo

    def writeCatalogue(self, catalogue: DFSCatalogue) -> None:
        """Encode a DFSCatalogue and write it to sectors 0 and 1.

        Clears the catalogue cache so the next readCatalogue() re-parses
        from the backing store.

        Args:
            catalogue: The catalogue state to write.
        """
        sec0 = bytearray(SECTOR_SIZE)
        sec1 = bytearray(SECTOR_SIZE)

        # --- Disc title ---
        title_sec0, title_sec1 = self._encodeTitle(catalogue.title)
        sec0[0:8] = title_sec0
        sec1[0:4] = title_sec1

        # --- Cycle number ---
        sec1[4] = catalogue.cycle & 0xFF

        # --- File offset ---
        sec1[5] = len(catalogue.entries) * 8

        # --- Descriptor byte ---
        # Bits 4-5: boot option. Bits 0-1: disc size high.
        # Bits 2,3,6,7 must be zero per spec.
        disc_size_hi = (catalogue.disc_size >> 8) & 0x03
        sec1[6] = ((catalogue.boot_option & 0x03) << 4) | disc_size_hi
        sec1[7] = catalogue.disc_size & 0xFF

        # --- File entries ---
        for i, entry in enumerate(catalogue.entries):
            base = 8 + i * 8
            sec0_chunk, sec1_chunk = self._encodeEntry(entry)
            sec0[base : base + 8] = sec0_chunk
            sec1[base : base + 8] = sec1_chunk

        self._writeSector(0, bytes(sec0))
        self._writeSector(1, bytes(sec1))

        # Clear the cache so the next read re-parses.
        self._catalogue = None

    # -------------------------------------------------------------------
    # File extraction
    # -------------------------------------------------------------------

    def readFile(self, entry: DFSEntry) -> bytes:
        """Read raw bytes for one catalogued file.

        Reads contiguous sectors starting from the entry's start sector
        and truncates to the recorded file length. Raises DFSFormatError
        if any required sector lies beyond the image.

        Args:
            entry: A DFSEntry from this side's catalogue.

        Returns:
            File bytes, exactly entry.length long.
        """
        if entry.length == 0:
            return b""

        sectors_needed = (entry.length + SECTOR_SIZE - 1) // SECTOR_SIZE

        data = bytearray()
        for s in range(sectors_needed):
            data.extend(self._readSector(entry.start_sector + s))

        return bytes(data[: entry.length])

    def writeFile(self, entry: DFSEntry, data: bytes) -> None:
        """Write raw file bytes to the sectors indicated by entry.

        The data is padded with zeros to fill the final sector. This
        writes data only - the catalogue must be updated separately via
        writeCatalogue().

        Args:
            entry: DFSEntry describing where to write.
            data:  File bytes (must be exactly entry.length long).
        """
        if len(data) != entry.length:
            raise ValueError(
                f"Data length {len(data)} does not match "
                f"entry length {entry.length}"
            )

        if entry.length == 0:
            return

        # Pad to a full sector boundary.
        sectors_needed = (entry.length + SECTOR_SIZE - 1) // SECTOR_SIZE
        padded = data + b"\x00" * (sectors_needed * SECTOR_SIZE - len(data))

        for s in range(sectors_needed):
            chunk = padded[s * SECTOR_SIZE : (s + 1) * SECTOR_SIZE]
            self._writeSector(entry.start_sector + s, chunk)

    # -------------------------------------------------------------------
    # Free space
    # -------------------------------------------------------------------

    def freeSpace(self) -> int:
        """Return the number of free bytes available on this side.

        Free space is the contiguous gap between sector 2 (the first
        usable data sector) and the lowest-numbered sector occupied by
        any file. This matches the standard DFS free space calculation
        and represents the maximum size of a new file that can be added.

        Gaps left by deleted files in the middle of the disc are not
        counted. Use compact() to reclaim those gaps first.

        Returns:
            Free space in bytes.
        """
        cat = self.readCatalogue()
        lowest = self._lowestUsedSector(cat)
        free_sectors = lowest - 2

        return free_sectors * SECTOR_SIZE

    def _lowestUsedSector(self, cat: DFSCatalogue) -> int:
        """Return the lowest sector number occupied by any file.

        If no files exist, returns disc_size (i.e. one past the last
        sector), meaning the entire data area is free.
        """
        if not cat.entries:
            return cat.disc_size

        return min(e.start_sector for e in cat.entries)

    # -------------------------------------------------------------------
    # File operations
    # -------------------------------------------------------------------

    def addFile(self, spec: DiscFile) -> DFSEntry:
        """Add a file to this disc side.

        Validates the filename, checks for duplicates, allocates sectors
        from the top of free space downward, writes the file data, and
        updates the catalogue with an incremented cycle number.

        The path in spec must be in DFS format: a single directory
        character, a dot, then the filename (e.g. '$.MYPROG').

        Args:
            spec: DiscFile describing the file to add.

        Returns:
            The DFSEntry created for the new file.

        Raises:
            DFSError: If the name is invalid, a duplicate exists, the
                catalogue is full, or there is not enough free space.
        """
        directory, name = splitDfsPath(spec.path)
        validateDfsName(directory, name)

        cat = self.readCatalogue()

        # Check for duplicate name.
        for e in cat.entries:
            if e.name == name and e.directory == directory:
                raise DFSError(
                    f"File {directory}.{name} already exists"
                )

        # DFS catalogue holds at most 31 entries.
        if len(cat.entries) >= 31:
            raise DFSError("Catalogue is full (31 files maximum)")

        # Allocate sectors from the top of free space downward.
        data = spec.data
        if len(data) == 0:
            sectors_needed = 0
        else:
            sectors_needed = (len(data) + SECTOR_SIZE - 1) // SECTOR_SIZE

        lowest = self._lowestUsedSector(cat)
        start_sector = lowest - sectors_needed

        if start_sector < 2:
            available = (lowest - 2) * SECTOR_SIZE
            raise DFSError(
                f"Not enough free space for {len(data)} bytes "
                f"({available} bytes available)"
            )

        # Build the catalogue entry.
        entry = DFSEntry(
            name=name,
            directory=directory,
            load_addr=spec.load_addr,
            exec_addr=spec.exec_addr,
            length=len(data),
            start_sector=start_sector,
            locked=spec.locked,
        )

        # Write file data to the allocated sectors.
        if data:
            self.writeFile(entry, data)

        # Insert entry and maintain descending start sector order.
        new_entries = sorted(
            list(cat.entries) + [entry],
            key=lambda e: e.start_sector,
            reverse=True,
        )

        new_cat = DFSCatalogue(
            title=cat.title,
            cycle=self._bcdIncrement(cat.cycle),
            boot_option=cat.boot_option,
            disc_size=cat.disc_size,
            entries=tuple(new_entries),
        )

        self.writeCatalogue(new_cat)
        return entry

    def deleteFile(self, path: str) -> None:
        """Remove a file from the catalogue.

        The catalogue entry is removed and the cycle number incremented.
        The file's sectors are not zeroed - they remain until overwritten
        by a new file or reclaimed by compact().

        If the deleted file was not the lowest on disc, its space cannot
        be reused until compact() is called.

        Args:
            path: Full DFS path (e.g. '$.MYPROG').

        Raises:
            DFSError: If the file is not found.
        """
        directory, name = splitDfsPath(path)
        cat = self.readCatalogue()

        found = None
        remaining = []

        for e in cat.entries:
            if found is None and e.name == name and e.directory == directory:
                found = e
            else:
                remaining.append(e)

        if found is None:
            raise DFSError(f"File {directory}.{name} not found")

        new_cat = DFSCatalogue(
            title=cat.title,
            cycle=self._bcdIncrement(cat.cycle),
            boot_option=cat.boot_option,
            disc_size=cat.disc_size,
            entries=tuple(remaining),
        )

        self.writeCatalogue(new_cat)

    def updateEntry(self, path: str, updated: 'DFSEntry') -> None:
        """Replace a catalogue entry with an updated version.

        Finds the entry matching the given path and substitutes it with
        the updated entry. The replacement must refer to the same file
        (same start sector and length). Catalogue fields other than the
        entry are preserved; the cycle number is incremented.

        Args:
            path:    Full DFS path (e.g. '$.MYPROG').
            updated: Replacement entry with modified attributes.

        Raises:
            DFSError: If the file is not found.
        """
        directory, name = splitDfsPath(path)
        cat = self.readCatalogue()

        new_entries = []
        found = False

        for e in cat.entries:
            if not found and e.name == name and e.directory == directory:
                new_entries.append(updated)
                found = True
            else:
                new_entries.append(e)

        if not found:
            raise DFSError(f"File {directory}.{name} not found")

        new_cat = DFSCatalogue(
            title=cat.title,
            cycle=self._bcdIncrement(cat.cycle),
            boot_option=cat.boot_option,
            disc_size=cat.disc_size,
            entries=tuple(new_entries),
        )

        self.writeCatalogue(new_cat)

    def renameFile(self, old_path: str, new_path: str) -> None:
        """Rename a file in the catalogue.

        Changes the entry's name and/or directory prefix. The file data
        is not moved - only the catalogue entry is updated.

        Args:
            old_path: Current full DFS path (e.g. '$.MYPROG').
            new_path: New full DFS path (e.g. 'T.NEWNAME').

        Raises:
            DFSError: If the source is not found, or the destination
                      name already exists.
        """
        old_dir, old_name = splitDfsPath(old_path)
        new_dir, new_name = splitDfsPath(new_path)

        # Validate the new name against DFS naming rules.
        validateDfsName(new_dir, new_name)

        cat = self.readCatalogue()

        # Find the source entry.
        source = None

        for e in cat.entries:
            if e.name == old_name and e.directory == old_dir:
                source = e
                break

        if source is None:
            raise DFSError(f"File {old_dir}.{old_name} not found")

        # Check that the destination name is not already taken.
        for e in cat.entries:
            if e.name == new_name and e.directory == new_dir:
                if e is not source:
                    raise DFSError(
                        f"File {new_dir}.{new_name} already exists"
                    )

        # Build the renamed entry and update the catalogue.
        renamed = replace(
            source, name=new_name, directory=new_dir,
        )
        self.updateEntry(old_path, renamed)

    def compact(self) -> int:
        """Defragment file storage by closing gaps between files.

        Files are packed toward the top of the disc (highest sectors)
        so that all free space is contiguous below. The catalogue is
        rewritten with updated start sectors and an incremented cycle
        number.

        Returns:
            Number of bytes freed by compaction (zero if already packed).
        """
        cat = self.readCatalogue()

        if not cat.entries:
            return 0

        free_before = self.freeSpace()

        # Process files from highest start sector to lowest, packing each
        # one immediately below the previous. This preserves the catalogue
        # descending order and avoids data corruption because we read each
        # file's data before writing to the new location.
        sorted_desc = sorted(
            cat.entries, key=lambda e: e.start_sector, reverse=True
        )

        next_free_top = cat.disc_size
        new_entries = []

        for entry in sorted_desc:
            if entry.length == 0:
                # Zero-length files need no sectors. Place them at the
                # current boundary so they sort correctly.
                new_entry = DFSEntry(
                    name=entry.name,
                    directory=entry.directory,
                    load_addr=entry.load_addr,
                    exec_addr=entry.exec_addr,
                    length=0,
                    start_sector=next_free_top,
                    locked=entry.locked,
                )
                new_entries.append(new_entry)
                continue

            sectors_needed = (entry.length + SECTOR_SIZE - 1) // SECTOR_SIZE
            new_start = next_free_top - sectors_needed

            if new_start != entry.start_sector:
                # Read from old location, write to new location.
                data = self.readFile(entry)
                new_entry = DFSEntry(
                    name=entry.name,
                    directory=entry.directory,
                    load_addr=entry.load_addr,
                    exec_addr=entry.exec_addr,
                    length=entry.length,
                    start_sector=new_start,
                    locked=entry.locked,
                )
                self.writeFile(new_entry, data)
                new_entries.append(new_entry)
            else:
                new_entries.append(entry)

            next_free_top = new_start

        new_cat = DFSCatalogue(
            title=cat.title,
            cycle=self._bcdIncrement(cat.cycle),
            boot_option=cat.boot_option,
            disc_size=cat.disc_size,
            entries=tuple(new_entries),
        )

        self.writeCatalogue(new_cat)

        free_after = self.freeSpace()
        return free_after - free_before


# -----------------------------------------------------------------------
# DFSImage - mutable disc image container
# -----------------------------------------------------------------------

class DFSImage:
    """Mutable DFS disc image container.

    Owns the bytearray backing store and provides DFSSide views for each
    side. Both SSD (single-sided) and DSD (double-sided interleaved)
    formats are supported.
    """

    def __init__(self, data: bytearray, is_dsd: bool) -> None:
        """Wrap an existing image bytearray.

        Args:
            data:   Mutable backing store for the disc image.
            is_dsd: True for .dsd interleaved format.
        """
        self._data = data
        self._is_dsd = is_dsd

        # Create side views.
        self._sides: List[DFSSide] = [DFSSide(self, 0)]
        if is_dsd:
            self._sides.append(DFSSide(self, 1))

    @property
    def data(self) -> bytearray:
        """The mutable backing store."""
        return self._data

    @property
    def is_dsd(self) -> bool:
        """True for double-sided interleaved format."""
        return self._is_dsd

    @property
    def sides(self) -> List[DFSSide]:
        """List of DFSSide readers, one per available side."""
        return list(self._sides)

    def serialize(self) -> bytes:
        """Return the disc image as immutable bytes for writing to a file."""
        return bytes(self._data)

    # -------------------------------------------------------------------
    # Python data model
    # -------------------------------------------------------------------

    def __repr__(self) -> str:
        """Show class name, disc format (SSD/DSD), and side count."""
        fmt = "DSD" if self._is_dsd else "SSD"
        return f"DFSImage({fmt}, {len(self._sides)} sides)"

    def __iter__(self) -> Iterator[DFSSide]:
        """Yield each side of the disc image."""
        return iter(self._sides)

    def __len__(self) -> int:
        """Number of sides (1 for SSD, 2 for DSD)."""
        return len(self._sides)

    def __getitem__(self, index: int) -> DFSSide:
        """Return the side at the given index."""
        return self._sides[index]

    def __enter__(self) -> "DFSImage":
        """Enter a context manager block. Returns self."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Exit a context manager block. No-op for in-memory images."""
        pass


# -----------------------------------------------------------------------
# Module-level functions
# -----------------------------------------------------------------------

def openDiscImage(path: str) -> DFSImage:
    """Open a disc image file and return a DFSImage.

    Format is inferred from the file extension:
        .ssd  -- single-sided, one side
        .dsd  -- double-sided interleaved, two sides

    Raises:
        DFSFormatError: If the image is too small for its format.
        FileNotFoundError: If the path does not exist.
    """
    with open(path, "rb") as f:
        raw = f.read()

    ext = path.lower()
    is_dsd = ext.endswith(".dsd")

    # A DFS catalogue occupies sectors 0 and 1 (512 bytes). For DSD,
    # track 0 contains both sides interleaved (20 sectors = 5120 bytes).
    min_size = 20 * SECTOR_SIZE if is_dsd else 2 * SECTOR_SIZE
    fmt_name = "DSD" if is_dsd else "SSD"

    if len(raw) < min_size:
        raise DFSFormatError(
            f"Image is {len(raw)} bytes, too small for {fmt_name} format "
            f"(minimum {min_size} bytes)"
        )

    return DFSImage(bytearray(raw), is_dsd)


def createDiscImage(
    tracks: int = 80,
    is_dsd: bool = False,
    title: str = "",
    boot_option: int = BootOption.OFF,
) -> DFSImage:
    """Create a blank formatted DFS disc image.

    The image is initialized with empty catalogues on each side and the
    disc size field set to match the track count.

    Args:
        tracks:      Number of tracks (40 or 80).
        is_dsd:      True for double-sided interleaved format.
        title:       Disc title (up to 12 characters).
        boot_option: Boot option (0-3, or a BootOption member).

    Returns:
        A new DFSImage ready for file operations.
    """
    if tracks not in (40, 80):
        raise ValueError(f"Track count must be 40 or 80, got {tracks}")

    try:
        boot_option = BootOption(boot_option)
    except ValueError:
        raise ValueError(f"Boot option must be 0-3, got {boot_option}")

    sectors_per_side = tracks * SECTORS_PER_TRACK

    if is_dsd:
        # Both sides interleaved: total sectors = 2 * tracks * 10.
        total_bytes = tracks * 20 * SECTOR_SIZE
    else:
        total_bytes = sectors_per_side * SECTOR_SIZE

    image = DFSImage(bytearray(total_bytes), is_dsd)

    # Write a blank catalogue on each side.
    for side in image.sides:
        blank = DFSCatalogue(
            title=title,
            cycle=0,
            boot_option=boot_option,
            disc_size=sectors_per_side,
            entries=(),
        )
        side.writeCatalogue(blank)

    return image


# -----------------------------------------------------------------------
# Name validation
# -----------------------------------------------------------------------

def splitDfsPath(path: str) -> Tuple[str, str]:
    """Split a full DFS path into (directory, name).

    DFS paths have the form 'D.NAME' where D is a single directory
    character and NAME is the 1-7 character filename.

    Args:
        path: Full DFS path (e.g. '$.MYPROG', 'T.DATA').

    Returns:
        Tuple of (directory, name).

    Raises:
        DFSError: If the path is not in 'D.NAME' format.
    """
    if len(path) < 3 or path[1] != '.':
        raise DFSError(
            f"Invalid DFS path '{path}' - expected format 'D.NAME'"
        )

    return path[0], path[2:]


def validateDfsName(directory: str, name: str) -> None:
    """Validate a DFS directory character and filename.

    DFS filenames consist of a single-character directory prefix and a
    name of 1-7 characters. Both must be printable ASCII (0x21-0x7E)
    excluding the characters . : " # * and space, per the Acorn DFS
    disc format specification.

    Args:
        directory: Single-character DFS directory (e.g. '$', 'T').
        name:      DFS filename, 1-7 characters (e.g. 'MYPROG').

    Raises:
        DFSError: If directory or name violates DFS naming rules.
    """
    # Characters forbidden by the DFS spec.
    forbidden = set('.:"#* ')

    # Directory must be exactly one character.
    if len(directory) != 1:
        raise DFSError(
            f"DFS directory must be a single character, got {len(directory)}"
        )

    # Directory must be printable ASCII but not a space or forbidden char.
    d = ord(directory)
    if d < 0x21 or d > 0x7E:
        raise DFSError(
            f"DFS directory must be printable ASCII (0x21-0x7E), "
            f"got 0x{d:02X}"
        )

    if directory in forbidden:
        raise DFSError(
            f"DFS directory character '{directory}' is not allowed"
        )

    # Name must be 1-7 characters.
    if not name:
        raise DFSError("DFS filename must not be empty")
    if len(name) > 7:
        raise DFSError(
            f"DFS filename must be 1-7 characters, got {len(name)}"
        )

    # Every character must be printable ASCII (0x21-0x7E) and not forbidden.
    for ch in name:
        c = ord(ch)
        if c < 0x21 or c > 0x7E:
            raise DFSError(
                f"DFS filename contains invalid character 0x{c:02X}"
            )

        if ch in forbidden:
            raise DFSError(
                f"DFS filename contains forbidden character '{ch}'"
            )


# -----------------------------------------------------------------------
# Backward-compatibility aliases
# -----------------------------------------------------------------------

# The old API used standalone functions and dict-based entries. These
# aliases ease the transition in callers that have not been updated yet.


