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

import os
import warnings as _warnings
from dataclasses import dataclass, replace
from enum import IntFlag
from functools import singledispatch
from typing import List, Optional, Sequence, Tuple, Union

from .boot import BootOption
from .entry import (
    DiscCatalogue, DiscEntry, DiscError, DiscFile, DiscFormatError,
    DiscImage, DiscSide, isBasicExecAddr,
)
from .shared import BeebToolsWarning
from .validation import isStrict


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
# Access flags
# -----------------------------------------------------------------------

class DFSAccessFlags(IntFlag):
    """Symbolic access-permission bits for a DFS entry.

    DFS has a single access flag (locked). The on-disc encoding lives
    in bit 7 of a filename character byte and is re-applied by the
    DFS engine at write time; this IntFlag is a logical symbol for
    library callers, not the physical on-disc bit position.
    """

    LOCKED = 0x01


def _parseDfsAccessSpec(
    spec: str,
) -> Tuple[DFSAccessFlags, DFSAccessFlags]:
    """Parse a DFS --access spec into ``(set_mask, clear_mask)``.

    DFS has a narrower grammar than ADFS because the only meaningful
    flag is locked:

    * **Absolute**: empty clears lock, ``L`` or ``LOCKED`` sets it.
    * **Mutation**: ``+L`` sets lock, ``-L`` clears it.

    Any other letter emits a ``BeebToolsWarning`` naming the ignored
    characters and continues with the L portion. This keeps scripting
    over mixed disc sets ergonomic: an ``--access LWR`` that runs
    across DFS and ADFS images still does the right thing on the
    DFS side.

    Returns a ``(set_mask, clear_mask)`` pair. Absolute specs set
    ``clear_mask`` to ``DFSAccessFlags.LOCKED`` so the caller can
    compose with ``(current & ~clear_mask) | set_mask`` uniformly.
    """

    # Empty absolute spec clears lock (same intent as *ACCESS with
    # no letters on a real Beeb).
    if spec == "":
        return DFSAccessFlags(0), DFSAccessFlags.LOCKED

    # Full-word synonym for L, accepted case-insensitively. Handled
    # before the per-character walk so 'LOCKED' is not treated as a
    # sequence of non-L letters to warn about.
    if spec.upper() == "LOCKED":
        return DFSAccessFlags.LOCKED, DFSAccessFlags.LOCKED

    # Mode dispatch: + or - starts mutation; a letter starts absolute.
    first = spec[0]

    if first in "+-":
        return _parseDfsMutation(spec)

    if first.isalpha():
        return _parseDfsAbsolute(spec)

    raise DFSError(
        f"--access value must start with a letter, '+', or '-', got {spec!r}"
    )


def _parseDfsAbsolute(
    spec: str,
) -> Tuple[DFSAccessFlags, DFSAccessFlags]:
    """Parse an absolute DFS access spec; warn and strip non-L letters.

    DFS only models a lock flag, so any letter other than L or l is
    meaningless on a DFS image. We collect them rather than erroring
    so that a single ``--access LWR`` invocation run over a mixed
    disc set (DFS + ADFS) still locks the DFS files.
    """

    set_mask = DFSAccessFlags(0)

    # Non-L letters collected for a single aggregated warning - less
    # noise than warning per-letter, and the user sees exactly which
    # characters were dropped.
    ignored: List[str] = []

    for ch in spec:

        # + or - in the middle of an absolute spec means the caller
        # is trying to mix modes (e.g. "L+R"). Reject with a clear
        # diagnostic rather than silently treating it as mutation.
        if ch in "+-":
            raise DFSError(
                "--access value must be either absolute (e.g. L) "
                "or mutation (e.g. +L), not both"
            )

        if ch in "Ll":
            set_mask |= DFSAccessFlags.LOCKED
        else:
            ignored.append(ch)

    # Single warning summarising every stripped letter.
    if ignored:
        _warnings.warn(
            f"DFS --access spec {spec!r}: ignoring non-L letters "
            f"{''.join(ignored)!r}",
            BeebToolsWarning,
            stacklevel=3,
        )

    # clear_mask is LOCKED (the only representable DFS bit) so the
    # caller's composition formula yields exact replacement.
    return set_mask, DFSAccessFlags.LOCKED


def _parseDfsMutation(
    spec: str,
) -> Tuple[DFSAccessFlags, DFSAccessFlags]:
    """Parse a DFS mutation spec (``+L`` / ``-L``); warn-and-strip other letters.

    Same warn-and-strip policy as absolute mode: non-L mutation pairs
    are collected and surfaced in a single aggregated warning instead
    of being errors, so mixed-format scripting still does the right
    thing on the DFS side.
    """

    set_mask = DFSAccessFlags(0)
    clear_mask = DFSAccessFlags(0)

    # Non-L pairs (e.g. '+R', '-w') collected for a single warning.
    ignored: List[str] = []

    # Index walks in steps of two: op + letter. A while loop keeps
    # error positions precise for incomplete pairs.
    i = 0

    while i < len(spec):
        op = spec[i]

        # A letter where we expected + or - means the user mixed
        # absolute letters into a mutation spec.
        if op not in "+-":
            raise DFSError(
                "--access value must be either absolute (e.g. L) "
                "or mutation (e.g. +L), not both"
            )

        # Dangling operator with no letter, or two operators in a
        # row, means the spec is incomplete.
        if i + 1 >= len(spec) or spec[i + 1] in "+-":
            raise DFSError(
                "mutation form needs at least one +X or -X pair"
            )

        ch = spec[i + 1]

        # Route L/l into the matching accumulator; collect anything
        # else for the aggregated warning.
        if ch in "Ll":

            if op == "+":
                set_mask |= DFSAccessFlags.LOCKED
            else:
                clear_mask |= DFSAccessFlags.LOCKED

        else:
            ignored.append(op + ch)

        i += 2

    # Single warning summarising every stripped +X / -X pair.
    if ignored:
        _warnings.warn(
            f"DFS --access spec {spec!r}: ignoring non-L mutations "
            f"{' '.join(ignored)!r}",
            BeebToolsWarning,
            stacklevel=3,
        )

    # Contradictory '+L-L' is ambiguous and always user error.
    conflict = set_mask & clear_mask

    if conflict:
        raise DFSError(
            f"--access spec {spec!r} both sets and clears LOCKED"
        )

    return set_mask, clear_mask


# -----------------------------------------------------------------------
# Resolve an access argument to the new DFSAccessFlags value
# -----------------------------------------------------------------------
#
# ``applyAccess`` accepts either a grammar spec string or a
# ``DFSAccessFlags`` value. Dispatch is by runtime type so each input
# shape lives in its own small handler rather than an isinstance
# ladder inside ``applyAccess``.

@singledispatch
def _resolveDfsAccess(
    access: object, current: DFSAccessFlags,
) -> DFSAccessFlags:
    """Default handler: the caller passed an unsupported type."""

    raise TypeError(
        f"access must be DFSAccessFlags or str, "
        f"got {type(access).__name__}"
    )


@_resolveDfsAccess.register
def _(access: str, current: DFSAccessFlags) -> DFSAccessFlags:
    """String spec: parse the grammar and compose against ``current``."""

    set_mask, clear_mask = _parseDfsAccessSpec(access)

    return (current & ~clear_mask) | set_mask


@_resolveDfsAccess.register
def _(access: DFSAccessFlags, current: DFSAccessFlags) -> DFSAccessFlags:
    """Native flag value: absolute replacement, ``current`` is ignored."""

    return access


@_resolveDfsAccess.register
def _(access: IntFlag, current: DFSAccessFlags) -> DFSAccessFlags:
    """Wrong IntFlag subclass (e.g. ``AdfsAccessFlags`` on a DFS image)."""

    raise ValueError(
        f"expected DFSAccessFlags, got {type(access).__name__}"
    )


# -----------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------

@dataclass(frozen=True)
class DFSEntry(DiscEntry):
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
    def accessFlags(self) -> DFSAccessFlags:
        """Return the entry's access bits as ``DFSAccessFlags``.

        DFS models only the lock state; the symbolic ``LOCKED`` flag
        is set iff the entry's ``locked`` boolean is True.
        """

        return DFSAccessFlags.LOCKED if self.locked else DFSAccessFlags(0)

    @property
    def accessString(self) -> str:
        """Render the access bits as a display string.

        DFS has only one meaningful bit, so this is ``L`` when the
        file is locked and empty otherwise.
        """

        return "L" if self.locked else ""

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
class DFSCatalogue(DiscCatalogue):
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

class DFSSide(DiscSide):
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

    # -------------------------------------------------------------------
    # Python data model
    # -------------------------------------------------------------------

    def __repr__(self) -> str:
        """Show class name, disc title, entry count, and free space."""
        cat = self.readCatalogue()
        return (f"DFSSide(title='{cat.title}', "
                f"{len(cat.entries)} entries, "
                f"{self.freeSpace()} sectors free)")

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
        # A few real-world images (e.g. LordOfTheRings-GameDiscSide1.ssd)
        # store disc_size = 0 even though the backing file is a full
        # 80-track SSD. Trust the physical image over the metadata: if
        # the stored value would produce an invalid track count (zero
        # or not 40/80), derive it from the actual byte length of this
        # side and emit a UserWarning so the anomaly is visible.
        disc_size = sec1[7] | (disc_size_hi << 8)
        disc_size = self._reconcileDiscSize(disc_size)

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

    def _reconcileDiscSize(self, stored: int) -> int:
        """Validate a catalogue disc_size against the backing image.

        A well-formed DFS image stores the side's sector count in the
        descriptor byte and sector 1 byte 7. Some real-world images
        (LordOfTheRings-GameDiscSide1.ssd is the canonical example)
        leave that field at zero even though the physical disc is a
        full 40 or 80 tracks. Trust the physical image over the
        metadata: if the stored value is not a sane 40- or 80-track
        sector count, fall back to deriving it from the backing byte
        length and emit a UserWarning so the anomaly is visible.

        Args:
            stored: The disc_size value parsed out of the catalogue.

        Returns:
            The stored value when it is valid, otherwise a value
            derived from the image byte length.
        """
        if stored in (40 * SECTORS_PER_TRACK, 80 * SECTORS_PER_TRACK):
            return stored

        # Compute sectors per side from the backing data. DSD images
        # interleave both sides track-by-track, so each side owns half
        # the bytes.
        total_bytes = len(self._image.data)

        if self._image.is_dsd:
            side_bytes = total_bytes // 2
        else:
            side_bytes = total_bytes

        derived = side_bytes // SECTOR_SIZE
        derived_tracks = derived // SECTORS_PER_TRACK

        if derived_tracks not in (40, 80):
            return stored

        _warnings.warn(
            f"DFS side {self._side} catalogue disc_size={stored} is not "
            f"a 40- or 80-track sector count; trusting the physical "
            f"image length and using {derived} sectors "
            f"({derived_tracks} tracks) instead",
            BeebToolsWarning,
            stacklevel=3,
        )

        return derived

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

        # Strip the 0x20 padding DFS uses to fill unused bytes after
        # the 1-7 character name. A degenerate all-space catalogue
        # entry (seen on some cheat/pokes discs) collapses to a
        # single-space name so the rebuilt image stays byte-identical
        # while keeping the name non-empty for the .inf sidecar.
        raw_name = sec0[base : base + 7].decode("bbc")
        name = raw_name.rstrip(" ") or " "

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

        Free space is the contiguous gap between the sector immediately
        after the highest-addressed file and the end of the disc. This
        matches BBC DFS, which allocates files from sector 2 upward and
        leaves free space at the top of the disc.

        Gaps left by deleted files in the middle of the disc are not
        counted. Use compact() to reclaim those gaps first.

        Returns:
            Free space in bytes.
        """
        cat = self.readCatalogue()
        next_free = self._nextFreeSector(cat)
        free_sectors = cat.disc_size - next_free

        return free_sectors * SECTOR_SIZE

    def _nextFreeSector(self, cat: DFSCatalogue) -> int:
        """Return the lowest sector number not occupied by any file.

        With bottom-up allocation this is sector 2 on an empty disc, or
        one past the end sector of the highest-addressed file otherwise.
        Zero-length files contribute no sectors.
        """
        if not cat.entries:
            return 2

        highest_end = 2

        for e in cat.entries:
            if e.length == 0:
                continue

            sectors = (e.length + SECTOR_SIZE - 1) // SECTOR_SIZE
            end = e.start_sector + sectors

            if end > highest_end:
                highest_end = end

        return highest_end

    # -------------------------------------------------------------------
    # File operations
    # -------------------------------------------------------------------

    def addFile(self, spec: DiscFile) -> DFSEntry:
        """Add a file to this disc side.

        Validates the filename, checks for duplicates, allocates sectors
        from the bottom of free space upward (matching BBC DFS, which
        places the first file at sector 2 and each subsequent file
        immediately after the previous), writes the file data, and
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

        # Allocate sectors from sector 2 upward, packing each new file
        # immediately after the highest-addressed existing file. This
        # matches the behaviour of BBC DFS and avoids leaving an unused
        # gap at the start of the data area that would slow down disc
        # loads. The caller may override allocation by supplying an
        # explicit start_sector hint; placed writes are used for
        # round-tripping copy-protected discs (Level 9 games) where two
        # catalogue entries legitimately claim overlapping sector
        # ranges. Byte consistency in the overlap region is the
        # caller's responsibility; this method does not validate it.
        data = spec.data
        if len(data) == 0:
            sectors_needed = 0
        else:
            sectors_needed = (len(data) + SECTOR_SIZE - 1) // SECTOR_SIZE

        if spec.start_sector is not None:
            start_sector = spec.start_sector

            if start_sector < 2:
                raise DFSError(
                    f"Placed start sector {start_sector} is inside the "
                    f"catalogue area (sectors 0-1 are reserved)"
                )

            end_sector = start_sector + sectors_needed - 1

            if end_sector >= cat.disc_size:
                raise DFSError(
                    f"Placed file would extend past the end of the "
                    f"disc (end sector {end_sector}, disc sectors "
                    f"{cat.disc_size})"
                )
        else:
            start_sector = self._nextFreeSector(cat)
            end_sector = start_sector + sectors_needed - 1

            if start_sector + sectors_needed > cat.disc_size:
                available = (cat.disc_size - start_sector) * SECTOR_SIZE
                raise DFSError(
                    f"Not enough free space for {len(data)} bytes "
                    f"({available} bytes available)"
                )

        # Honour an explicit access byte (typically from a .inf on
        # rebuild). DFS only models the lock bit, so anything else in
        # spec.access is ignored by design.
        if spec.access is not None:
            entry_locked = bool(spec.access & int(DFSAccessFlags.LOCKED))
        else:
            entry_locked = spec.locked

        # Build the catalogue entry.
        entry = DFSEntry(
            name=name,
            directory=directory,
            load_addr=spec.load_addr,
            exec_addr=spec.exec_addr,
            length=len(data),
            start_sector=start_sector,
            locked=entry_locked,
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

    def applyAccess(
        self,
        entry: 'DiscEntry',
        access: Union[IntFlag, str],
    ) -> None:
        """Apply an access change to a DFS catalogue entry.

        A ``DFSAccessFlags`` value is an absolute replacement. A
        ``str`` value is parsed with the DFS --access grammar
        (``L``, ``LOCKED``, ``""``, ``+L``, ``-L``) and composed
        against the entry's current locked state.

        Non-L letters in a string spec are stripped with a
        ``BeebToolsWarning``; the L portion still applies.

        The updated entry is written back via :meth:`updateEntry`.
        """

        # A DFSSide only mutates its own catalogue entries. A foreign
        # entry is API misuse, not a user-facing error.
        if not isinstance(entry, DFSEntry):
            raise ValueError(
                f"applyAccess on DFSSide expected DFSEntry, "
                f"got {type(entry).__name__}"
            )

        # DFS stores lock as a boolean, not a bit in an access byte.
        # Promote it to the symbolic flag so the composition formula
        # used by the dispatcher below is identical to ADFS.
        current = (
            DFSAccessFlags.LOCKED if entry.locked else DFSAccessFlags(0)
        )

        # Resolve the requested change via the module-level singledispatch
        # helper. It routes str / DFSAccessFlags / foreign-IntFlag /
        # anything-else to four small dedicated handlers and returns the
        # new absolute flag value.
        new_flags = _resolveDfsAccess(access, current)

        # Collapse back to the boolean DFSEntry expects. The bit-7
        # on-disc encoding is reapplied inside writeCatalogue.
        new_locked = bool(new_flags & DFSAccessFlags.LOCKED)

        # updateEntry handles the write-back and cycle increment.
        updated = replace(entry, locked=new_locked)
        self.updateEntry(entry.fullName, updated)

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

        Files are packed toward sector 2 (lowest usable sector) so that
        all free space is contiguous at the top of the disc. The
        relative order of files (by ascending start sector) is
        preserved. The catalogue is rewritten with updated start
        sectors and an incremented cycle number.

        Returns:
            Number of bytes freed by compaction (zero if already packed).
        """
        cat = self.readCatalogue()

        if not cat.entries:
            return 0

        free_before = self.freeSpace()

        # Process files from lowest start sector to highest, packing
        # each one immediately after the previous. We process in
        # ascending order so reads of each file's data happen before
        # any write that could overlap its old location.
        sorted_asc = sorted(
            cat.entries, key=lambda e: e.start_sector
        )

        next_free_bottom = 2
        new_entries = []

        for entry in sorted_asc:
            if entry.length == 0:
                # Zero-length files need no sectors. Place them at the
                # current boundary so they sort correctly.
                new_entry = DFSEntry(
                    name=entry.name,
                    directory=entry.directory,
                    load_addr=entry.load_addr,
                    exec_addr=entry.exec_addr,
                    length=0,
                    start_sector=next_free_bottom,
                    locked=entry.locked,
                )
                new_entries.append(new_entry)
                continue

            sectors_needed = (entry.length + SECTOR_SIZE - 1) // SECTOR_SIZE
            new_start = next_free_bottom

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

            next_free_bottom = new_start + sectors_needed

        # Maintain the catalogue convention of descending start sector
        # order (highest sector first), matching addFile().
        new_entries.sort(key=lambda e: e.start_sector, reverse=True)

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

class DFSImage(DiscImage):
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
    # DSD <-> SSD layout conversion
    # -------------------------------------------------------------------

    # A DFS track is always 10 sectors of 256 bytes on a single
    # surface; a DSD track therefore occupies twice that in the image
    # because both surfaces are interleaved.
    _TRACK_BYTES = SECTORS_PER_TRACK * SECTOR_SIZE

    # The two physical DFS capacities, keyed by total DSD byte length.
    _DSD_SIZES = {
        40 * SECTORS_PER_TRACK * SECTOR_SIZE * 2: 40,
        80 * SECTORS_PER_TRACK * SECTOR_SIZE * 2: 80,
    }

    def split(
        self, sequential: bool = False,
    ) -> Tuple["DFSImage", "DFSImage"]:
        """Split a DSD image into two single-sided DFSImage instances.

        Args:
            sequential: If True, treat the backing store as a plain
                        concatenation (entire side 0 followed by entire
                        side 1) rather than the standard track-by-track
                        interleave used by ``.dsd`` files.

        Returns:
            A ``(side0, side1)`` tuple of fresh single-sided DFSImages.

        Raises:
            DFSError: If this image is not double-sided, or its backing
                      store is not a recognised DSD capacity.
        """

        # Only DSD images carry a second surface to split out.
        if not self._is_dsd:
            raise DFSError("split() requires a double-sided (DSD) image")

        # Validate against the legal DSD capacities so callers get a
        # clear diagnostic instead of a silently truncated output.
        size = len(self._data)
        tracks = self._DSD_SIZES.get(size)

        if tracks is None:
            raise DFSError(
                f"Not a valid DSD image: {size} bytes (expected "
                f"{40 * SECTORS_PER_TRACK * SECTOR_SIZE * 2} or "
                f"{80 * SECTORS_PER_TRACK * SECTOR_SIZE * 2})"
            )

        # Sequential layout is a straight halving.
        if sequential:
            half = size // 2
            side0_bytes = bytes(self._data[:half])
            side1_bytes = bytes(self._data[half:])
        else:
            side0_bytes, side1_bytes = self._deinterleave(
                self._data, tracks,
            )

        return (
            DFSImage(bytearray(side0_bytes), is_dsd=False),
            DFSImage(bytearray(side1_bytes), is_dsd=False),
        )

    @classmethod
    def merge(
        cls,
        side0: "DFSImage",
        side1: "DFSImage",
        sequential: bool = False,
    ) -> "DFSImage":
        """Combine two single-sided DFSImages into one DSD image.

        Args:
            side0:      DFSImage that will become side 0 of the DSD.
            side1:      DFSImage that will become side 1 of the DSD.
            sequential: If True, write the concatenated layout (side 0
                        followed by side 1) instead of the standard
                        track-by-track interleave.

        Returns:
            A fresh double-sided DFSImage.

        Raises:
            DFSError: If either input is not single-sided, the two
                      sides differ in size, or the size is not a
                      recognised SSD capacity.
        """

        # Both inputs must be SSDs: merging a DSD into another disc
        # makes no sense and almost certainly indicates a caller bug.
        if side0._is_dsd or side1._is_dsd:
            raise DFSError("merge() requires two single-sided (SSD) images")

        # A DSD has a single track count that applies to both surfaces,
        # so the two SSDs must be the same length.
        if len(side0._data) != len(side1._data):
            raise DFSError(
                f"SSD sizes differ: side 0 is {len(side0._data)} bytes, "
                f"side 1 is {len(side1._data)} bytes"
            )

        # Validate that the matched size is one of the two legal SSD
        # capacities (which are the DSD capacities halved).
        ssd_sizes = {
            sz // 2: tracks for sz, tracks in cls._DSD_SIZES.items()
        }
        tracks = ssd_sizes.get(len(side0._data))

        if tracks is None:
            raise DFSError(
                f"Not a valid SSD size: {len(side0._data)} bytes "
                f"(expected {40 * SECTORS_PER_TRACK * SECTOR_SIZE} or "
                f"{80 * SECTORS_PER_TRACK * SECTOR_SIZE})"
            )

        # Apply the chosen layout.
        if sequential:
            merged = bytes(side0._data) + bytes(side1._data)
        else:
            merged = cls._interleave(side0._data, side1._data, tracks)

        return DFSImage(bytearray(merged), is_dsd=True)

    @classmethod
    def _deinterleave(
        cls, data: bytes, tracks: int,
    ) -> Tuple[bytes, bytes]:
        """Split interleaved DSD bytes into the two surface streams.

        The on-disc layout is track 0 side 0, track 0 side 1, track 1
        side 0, track 1 side 1, ... so walking the data in 2560-byte
        chunks and alternating destinations recovers each SSD stream.
        """

        side0 = bytearray()
        side1 = bytearray()

        # One iteration per physical track copies both surfaces.
        for track in range(tracks):
            base = track * cls._TRACK_BYTES * 2
            side0 += data[base : base + cls._TRACK_BYTES]
            side1 += data[
                base + cls._TRACK_BYTES : base + cls._TRACK_BYTES * 2
            ]

        return bytes(side0), bytes(side1)

    @classmethod
    def _interleave(
        cls, side0: bytes, side1: bytes, tracks: int,
    ) -> bytes:
        """Combine two SSD byte streams into an interleaved DSD image.

        Inverse of :meth:`_deinterleave`: emit each track's side-0
        slice followed immediately by the matching side-1 slice.
        """

        out = bytearray()

        # Walk both surfaces a track at a time in lock-step.
        for track in range(tracks):
            base = track * cls._TRACK_BYTES
            out += side0[base : base + cls._TRACK_BYTES]
            out += side1[base : base + cls._TRACK_BYTES]

        return bytes(out)

    # -------------------------------------------------------------------
    # Python data model
    # -------------------------------------------------------------------

    def __repr__(self) -> str:
        """Show class name, disc format (SSD/DSD), and side count."""
        fmt = "DSD" if self._is_dsd else "SSD"
        return f"DFSImage({fmt}, {len(self._sides)} sides)"


# -----------------------------------------------------------------------
# Module-level functions
# -----------------------------------------------------------------------

def openDiscImage(path: str) -> DFSImage:
    """Open a disc image file and return a DFSImage.

    Format detection prefers extension when it is unambiguous, but
    falls back to content sniffing so files with non-standard names
    (e.g. ``.img`` or no extension) still open correctly:

      * ``.ssd`` -- always single-sided
      * ``.dsd`` -- always double-sided interleaved
      * anything else -- inferred from the file size, with the
        ambiguous 204800-byte case (SSD 80t vs DSD 40t) resolved by
        reading the catalogue's recorded sector count.

    Raises:
        DFSFormatError:    If the image is too small for any DFS format.
        FileNotFoundError: If the path does not exist.
    """
    with open(path, "rb") as f:
        raw = f.read()

    ext = path.lower()
    is_dsd = _detectIsDsd(ext, raw)

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


def _detectIsDsd(ext_lower: str, raw: bytes) -> bool:
    """Decide whether a disc image is DSD or SSD.

    The file bytes are canonical; the extension is treated as a hint
    that the bytes may confirm or contradict. The algorithm is:

      1. Sniff the format from the byte length (and, for the
         ambiguous 204800-byte case, from the catalogue's recorded
         sector count).
      2. Compare the sniffed verdict against any extension hint.
      3. Emit a ``BeebToolsWarning`` if the extension contradicts the
         bytes, or if no signal at all is available and we are
         falling back to a default.

    The bytes always win when they are conclusive. The extension only
    decides when content sniffing is genuinely ambiguous (the
    204800-byte case with an absent or unreadable catalogue).
    """

    # Extract the extension hint, if any. Empty string means none.
    if ext_lower.endswith(".dsd"):
        ext_hint: Optional[bool] = True
    elif ext_lower.endswith(".ssd"):
        ext_hint = False
    else:
        ext_hint = None

    size = len(raw)
    ssd_40 = 40 * SECTORS_PER_TRACK * SECTOR_SIZE
    ssd_80 = 80 * SECTORS_PER_TRACK * SECTOR_SIZE
    dsd_40 = ssd_80                       # 204800: ambiguous
    dsd_80 = 80 * SECTORS_PER_TRACK * SECTOR_SIZE * 2

    # Unambiguous sizes: bytes are conclusive.
    sniffed: Optional[bool] = None

    if size == ssd_40:
        sniffed = False
    elif size == dsd_80:
        sniffed = True
    elif size == dsd_40:
        # 204800 bytes matches both SSD 80t and DSD 40t. Peek at the
        # side-0 catalogue: sector 1 holds the total-sector count
        # (low byte at offset 7, high two bits in offset 6 bits 0-1).
        # A recorded count of 400 means 40 tracks, which at this size
        # must be DSD; 800 means 80 tracks, which must be SSD.
        if len(raw) >= 2 * SECTOR_SIZE:
            cat_sec1 = raw[SECTOR_SIZE : 2 * SECTOR_SIZE]
            sector_count = cat_sec1[7] | ((cat_sec1[6] & 0x03) << 8)

            if sector_count == 40 * SECTORS_PER_TRACK:
                sniffed = True
            elif sector_count == 80 * SECTORS_PER_TRACK:
                sniffed = False

    # If bytes gave a conclusive answer, reconcile with the hint.
    if sniffed is not None:
        if ext_hint is not None and ext_hint != sniffed:
            actual = "DSD" if sniffed else "SSD"
            named = "DSD" if ext_hint else "SSD"
            _warnings.warn(
                f"Image contents are {actual} but file is named .{named.lower()}; "
                f"trusting the bytes",
                BeebToolsWarning,
                stacklevel=3,
            )
        return sniffed

    # Bytes were inconclusive (size unrecognised, or 204800 with no
    # readable catalogue). Fall back to the extension hint when we
    # have one, otherwise default to SSD.
    if ext_hint is not None:
        return ext_hint

    _warnings.warn(
        f"Image size {size} bytes is not a standard DFS capacity and "
        f"the filename gives no .ssd/.dsd hint; defaulting to SSD",
        BeebToolsWarning,
        stacklevel=3,
    )
    return False


def createDiscImage(
    tracks: int = 80,
    is_dsd: bool = False,
    title: str = "",
    boot_option: BootOption = BootOption.OFF,
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
    name of 1-7 characters. In the default (ROM-faithful) mode any
    7-bit byte is accepted, matching the behaviour of real Acorn DFS
    ROMs which byte-push filenames into the catalogue without enforcing
    the spec restrictions. This covers control bytes like 0x06 and
    0x00 that appear in real commercial discs such as cheat menus and
    copy-protection images.

    Under strictMode() the stricter spec rules apply: directory and
    name must be in 0x21-0x7E and must not contain any of the
    spec-forbidden characters . : " # * or space. Use strict mode when
    authoring a disc and you want to avoid creating filenames that
    break the official DFS spec, even though real ROMs would accept
    them.

    Args:
        directory: Single-character DFS directory (e.g. '$', 'T').
        name:      DFS filename, 1-7 characters (e.g. 'MYPROG').

    Raises:
        DFSError: If directory or name violates DFS naming rules.
    """
    # Characters the Acorn DFS spec forbids. Real ROMs accept them;
    # we only reject them under strictMode().
    spec_forbidden = set('.:"#* ')

    # Directory must be exactly one character.
    if len(directory) != 1:
        raise DFSError(
            f"DFS directory must be a single character, got {len(directory)}"
        )

    # Directory byte range. Default mode accepts any 7-bit byte
    # (0x00-0x7F), matching what a real DFS ROM byte-pushes into the
    # catalogue. Strict mode narrows to 0x21-0x7E per the spec.
    d = ord(directory)
    if isStrict():
        if d < 0x21 or d > 0x7E:
            raise DFSError(
                f"DFS directory must be printable ASCII (0x21-0x7E), "
                f"got 0x{d:02X}"
            )
        if directory in spec_forbidden:
            raise DFSError(
                f"DFS directory character '{directory}' is forbidden by "
                f"the DFS spec"
            )
    else:
        if d > 0x7F:
            raise DFSError(
                f"DFS directory must be a 7-bit byte (0x00-0x7F), "
                f"got 0x{d:02X}"
            )

    # Name length. 1-7 bytes regardless of mode.
    if not name:
        raise DFSError("DFS filename must not be empty")
    if len(name) > 7:
        raise DFSError(
            f"DFS filename must be 1-7 characters, got {len(name)}"
        )

    # Name byte range check. Default mode accepts any 7-bit byte, so
    # control bytes such as 0x00 and 0x06 that appear in real commercial
    # discs are preserved verbatim. Strict mode narrows to 0x21-0x7E
    # and also rejects spec-forbidden chars.
    strict = isStrict()

    for ch in name:
        c = ord(ch)

        if strict:
            if c < 0x21 or c > 0x7E:
                raise DFSError(
                    f"DFS filename contains invalid character 0x{c:02X}"
                )
            if ch in spec_forbidden:
                raise DFSError(
                    f"DFS filename contains spec-forbidden character '{ch}'"
                )
        else:
            if c > 0x7F:
                raise DFSError(
                    f"DFS filename contains non-7-bit byte 0x{c:02X}"
                )


# -----------------------------------------------------------------------
# Backward-compatibility aliases
# -----------------------------------------------------------------------

# The old API used standalone functions and dict-based entries. These
# aliases ease the transition in callers that have not been updated yet.


# -----------------------------------------------------------------------
# DSD <-> SSD file orchestration
# -----------------------------------------------------------------------
#
# DFSImage.split / DFSImage.merge handle the in-memory byte layout.
# The helpers below wrap those with file I/O, output-name derivation,
# and overwrite policy so callers can convert between .dsd and pairs
# of .ssd files directly. They live in dfs.py because DSD / SSD
# layouts are entirely DFS-specific; ADFS has no analogous operation.


def _deriveSplitNames(
    source: str,
    args: Sequence[str],
) -> Tuple[str, str]:
    """Work out the two output .ssd paths for splitDsd().

    Three argument shapes are supported, matching the CLI:
      0 args  -> derive both from the source stem (source-side0.ssd,
                 source-side1.ssd)
      1 arg   -> use the supplied stem (stem-side0.ssd,
                 stem-side1.ssd)
      2 args  -> use both names exactly as given
    Anything else is a programming error and raises ValueError.
    """

    # Zero extra arguments: build the two names from the input filename
    # by stripping its extension and appending the side suffixes.
    if len(args) == 0:
        stem, _ext = os.path.splitext(source)
        return f"{stem}-side0.ssd", f"{stem}-side1.ssd"

    # One extra argument: caller has supplied a shared stem for both
    # output files. Append -side0.ssd and -side1.ssd unmodified.
    if len(args) == 1:
        stem = args[0]
        return f"{stem}-side0.ssd", f"{stem}-side1.ssd"

    # Two extra arguments: caller has named both outputs explicitly.
    if len(args) == 2:
        return args[0], args[1]

    raise ValueError(
        f"splitDsd accepts 0, 1, or 2 output names (got {len(args)})"
    )


def splitDsd(
    source: str,
    *output_names: str,
    sequential: bool = False,
    force: bool = False,
) -> Tuple[str, str]:
    """Split a DSD disc image file into its two SSD halves.

    Args:
        source:        Path to the DSD image to read.
        output_names:  Zero, one, or two output paths. Zero derives
                       both names from ``source``; one supplies a
                       shared stem; two names both outputs
                       explicitly. See :func:`_deriveSplitNames`.
        sequential:    If True, treat ``source`` as a concatenated
                       layout (entire side 0 followed by entire side
                       1) rather than the standard track-interleaved
                       layout. Matches MMB_Utils' ``-concat``.
        force:         Overwrite existing output files when True.

    Returns:
        The tuple of output paths actually written.

    Raises:
        DFSError: If the source is not a DSD image, the size is not a
                  valid capacity, or an output exists without ``force``.
    """

    # Compute the output paths up front so any naming error is
    # reported before we touch the filesystem.
    out0, out1 = _deriveSplitNames(source, output_names)

    # Refuse to clobber existing files without explicit consent.
    if not force:
        for path in (out0, out1):
            if os.path.exists(path):
                raise DFSError(
                    f"Output file already exists: {path} "
                    f"(use force=True to overwrite)"
                )

    # openDiscImage sniffs by extension first and falls back to size
    # and catalogue content, so a real DSD with an unconventional
    # name (e.g. .img) still routes here as a double-sided image.
    src = openDiscImage(source)

    # DFSImage.split validates that the image is a DSD of legal size.
    side0, side1 = src.split(sequential=sequential)

    # Write the two halves out as raw .ssd files.
    with open(out0, "wb") as fh:
        fh.write(side0.serialize())
    with open(out1, "wb") as fh:
        fh.write(side1.serialize())

    return out0, out1


def mergeDsd(
    side0_path: str,
    side1_path: str,
    output: str,
    sequential: bool = False,
    force: bool = False,
) -> str:
    """Combine two SSD image files into a single DSD image file.

    Args:
        side0_path:  SSD file that will become side 0 of the DSD.
        side1_path:  SSD file that will become side 1 of the DSD.
        output:      Path to write the combined DSD image.
        sequential:  If True, concatenate side 0 then side 1 without
                     track interleaving. Defaults to the standard
                     track-by-track interleave.
        force:       Overwrite an existing output when True.

    Returns:
        The output path written.

    Raises:
        DFSError: If either input is not an SSD, the sizes differ,
                  the size is not a valid SSD capacity, or the output
                  exists without ``force``.
    """

    # Reject overwrite of an existing file unless explicitly allowed.
    if not force and os.path.exists(output):
        raise DFSError(
            f"Output file already exists: {output} "
            f"(use force=True to overwrite)"
        )

    # openDiscImage sniffs by extension first and by size/catalogue
    # content otherwise, so SSDs with non-standard names route here
    # as single-sided images.
    s0 = openDiscImage(side0_path)
    s1 = openDiscImage(side1_path)

    # DFSImage.merge validates SSD-ness and size compatibility.
    merged = DFSImage.merge(s0, s1, sequential=sequential)

    # Write the combined image.
    with open(output, "wb") as fh:
        fh.write(merged.serialize())

    return output

