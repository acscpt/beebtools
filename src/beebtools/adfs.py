# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""ADFS disc image reader (old map, small directory).

Supports .adf (single-sided) and .adl (double-sided track-interleaved) formats.
Handles ADFS-S (160K), ADFS-M (320K), and ADFS-L (640K) disc images.
Provides catalogue parsing, directory walking, and file extraction for
Acorn ADFS old-map disc images.

Reference: https://mdfs.net/Docs/Comp/Disk/Format/ADFS

Classes:
    ADFSEntry        -- one file entry from an ADFS directory (frozen dataclass)
    ADFSDirectory    -- one parsed ADFS directory (frozen dataclass)
    ADFSFreeSpaceMap -- parsed free space map from sectors 0-1 (frozen dataclass)
    ADFSCatalogue    -- flattened catalogue for one ADFS disc (frozen dataclass)
    ADFSSide         -- sector I/O, directory parsing, and file extraction
    ADFSImage        -- disc image container

Exceptions:
    ADFSError       -- base exception for all ADFS errors
    ADFSFormatError -- raised when the disc image is structurally invalid
"""

import warnings
from dataclasses import dataclass, replace
from enum import IntFlag
from functools import singledispatch
from typing import List, Optional, Tuple, Union

from .boot import BootOption
from .entry import (
    DiscCatalogue, DiscEntry, DiscError, DiscFile, DiscFormatError,
    DiscImage, DiscSide, isBasicExecAddr,
)
from .shared import BeebToolsWarning


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

# The D flag lives at on-disc bit 3 (0x08) but is carried on
# ADFSEntry.is_directory rather than in the access byte for the
# purposes of the public ADFSAccessFlags type. This module-private
# constant is used only at the on-disc encode/decode boundary so the
# directory bit is preserved inside the stored access field.
_ADFS_DIRECTORY_BIT = 0x08

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

class ADFSError(DiscError):
    """Base exception for ADFS disc image errors."""


class ADFSFormatError(ADFSError, DiscFormatError):
    """Raised when a disc image is structurally invalid or corrupted."""


# -----------------------------------------------------------------------
# Access flags
# -----------------------------------------------------------------------

class ADFSAccessFlags(IntFlag):
    """Symbolic access-permission bits for an ADFS entry.

    Values are the physical on-disc bit positions used by the ADFS
    directory entry format, so ``entry.access & ADFSAccessFlags.OWNER_L``
    works directly without a translation step inside the format engine.

    The on-disc D (directory) bit at value 0x08 is carried on
    ``ADFSEntry.is_directory`` rather than in the access byte and has
    no symbol here. The stardot .inf sidecar layout is a separate
    concern handled by :mod:`beebtools.inf_translators`.
    """

    OWNER_R  = 0x01
    OWNER_W  = 0x02
    OWNER_L  = 0x04
    OWNER_E  = 0x10
    PUBLIC_R = 0x20
    PUBLIC_W = 0x40
    PUBLIC_E = 0x80


# All bits this enum can represent, used to clear a byte in absolute
# parse mode before ORing the requested bits back in.
_ADFS_ACCESS_ALL = (
    ADFSAccessFlags.OWNER_R | ADFSAccessFlags.OWNER_W
    | ADFSAccessFlags.OWNER_L | ADFSAccessFlags.OWNER_E
    | ADFSAccessFlags.PUBLIC_R | ADFSAccessFlags.PUBLIC_W
    | ADFSAccessFlags.PUBLIC_E
)

# Bits that are not legal on a directory. Owner-W would recreate the
# DWLR state; public bits (r/w/e) do not apply to directories per the
# ADFS *ACCESS grammar.
_ADFS_DIR_ILLEGAL_BITS = (
    ADFSAccessFlags.OWNER_W
    | ADFSAccessFlags.PUBLIC_R
    | ADFSAccessFlags.PUBLIC_W
    | ADFSAccessFlags.PUBLIC_E
)

# Single-letter spec grammar: uppercase is owner, lowercase is public.
_ADFS_ACCESS_LETTERS = {
    "R": ADFSAccessFlags.OWNER_R,
    "W": ADFSAccessFlags.OWNER_W,
    "L": ADFSAccessFlags.OWNER_L,
    "E": ADFSAccessFlags.OWNER_E,
    "r": ADFSAccessFlags.PUBLIC_R,
    "w": ADFSAccessFlags.PUBLIC_W,
    "e": ADFSAccessFlags.PUBLIC_E,
}


# Display order for rendering access bits back to a human-readable
# string. Owner letters come first (L, W, R, E) followed by public
# letters (l unused, w, r, e). Matches the ordering users would see
# in *ACCESS output on a real Master.
_ADFS_OWNER_DISPLAY = (
    ("L", ADFSAccessFlags.OWNER_L),
    ("W", ADFSAccessFlags.OWNER_W),
    ("R", ADFSAccessFlags.OWNER_R),
    ("E", ADFSAccessFlags.OWNER_E),
)
_ADFS_PUBLIC_DISPLAY = (
    ("w", ADFSAccessFlags.PUBLIC_W),
    ("r", ADFSAccessFlags.PUBLIC_R),
    ("e", ADFSAccessFlags.PUBLIC_E),
)


def _parseAdfsAccessSpec(
    spec: str,
) -> Tuple[ADFSAccessFlags, ADFSAccessFlags]:
    """Parse an ADFS --access spec into ``(set_mask, clear_mask)``.

    Grammar:

    * **Absolute** (empty, or first character is a letter). Each letter
      maps to its owner or public bit. A ``/`` is a cosmetic separator
      that folds following uppercase letters to their public-case
      equivalents. ``LWR/r`` and ``LWRr`` are equivalent. An empty
      string clears every access bit.

    * **Mutation** (first character is ``+`` or ``-``). A sequence of
      ``+X`` / ``-X`` pairs, each adding or removing one bit. ``+L-W+R``
      applies three mutations in order.

    Returns a ``(set_mask, clear_mask)`` pair in on-disc bit positions.
    Absolute specs set ``clear_mask`` to every representable bit so the
    caller can compose with ``(current & ~clear_mask) | set_mask`` for
    both modes uniformly.

    Raises:
        ADFSError: for mixed absolute/mutation forms or contradictory
            ``+L-L`` mutations. Unknown letters (including ``D`` / ``d``)
            are warned and skipped rather than erroring, to match the
            DFS parser's warn-and-continue policy.
    """

    # Mode is picked by the first character: letter or empty is
    # absolute, + or - is mutation. Anything else is a syntax error.
    if spec == "" or spec[0].isalpha():
        return _parseAdfsAbsolute(spec)

    if spec[0] in "+-":
        return _parseAdfsMutation(spec)

    raise ADFSError(
        f"--access value must start with a letter, '+', or '-', got {spec!r}"
    )


def _parseAdfsAbsolute(
    spec: str,
) -> Tuple[ADFSAccessFlags, ADFSAccessFlags]:
    """Parse an absolute ADFS access spec (``LWR``, ``LWR/r``, ``""``).

    Walks the spec once, accumulating bits into ``set_mask``. The
    returned ``clear_mask`` covers every representable bit so the
    caller's composition formula ``(current & ~clear_mask) | set_mask``
    collapses to exact replacement.

    Non-access letters are collected and surfaced in a single
    aggregated warning rather than erroring, matching the DFS parser.
    ``D`` / ``d`` are called out separately in the warning because they
    are the directory type flag (created via ``mkdir``), not junk.
    """

    # Accumulator for the bits the caller wants set. Bits are added
    # one letter at a time as the loop walks the spec.
    set_mask = ADFSAccessFlags(0)

    # Tracks whether we have passed the cosmetic '/' separator. Every
    # letter after the slash folds to its public-case equivalent.
    after_slash = False

    # Two separate buckets for foreign letters so the warning can
    # distinguish "D is a type flag" from "Q is just unknown".
    d_letters: List[str] = []
    other_letters: List[str] = []

    for ch in spec:

        # Slash is a cosmetic divider, not a value. Flip the fold
        # state and move on without consuming a bit.
        if ch == "/":
            after_slash = True
            continue

        # + and - inside an absolute spec means the caller tried to
        # mix modes (e.g. "lr+W"); reject with a clear diagnostic.
        if ch in "+-":
            raise ADFSError(
                "--access value must be either absolute (e.g. LWR) "
                "or mutation (e.g. +L-W), not both"
            )

        # After the slash, fold any uppercase letter to its public
        # equivalent so 'LWR/R' and 'LWR/r' behave identically.
        folded = ch.lower() if after_slash else ch
        bit = _ADFS_ACCESS_LETTERS.get(folded)

        if bit is not None:
            set_mask |= bit
            continue

        if ch in "Dd":
            d_letters.append(ch)
        else:
            other_letters.append(ch)

    _warnAdfsIgnored(spec, d_letters, other_letters)

    # clear_mask is every representable bit so the caller's composition
    # formula yields exact replacement: (current & 0) | set_mask.
    return set_mask, _ADFS_ACCESS_ALL


def _parseAdfsMutation(
    spec: str,
) -> Tuple[ADFSAccessFlags, ADFSAccessFlags]:
    """Parse a mutation ADFS access spec (``+L-W``).

    Walks the spec two characters at a time, splitting operator
    (``+`` or ``-``) from letter. Each ``+X`` adds to ``set_mask``;
    each ``-X`` adds to ``clear_mask``. Bits not named stay
    untouched when the caller composes the masks with the current
    access byte.

    Non-access letters are collected and surfaced in a single
    aggregated warning rather than erroring, matching the DFS parser.
    ``D`` / ``d`` are called out separately in the warning because they
    are the directory type flag (created via ``mkdir``), not junk.
    """

    # Separate set and clear accumulators: bits the user wants on go
    # in set_mask, bits they want off go in clear_mask.
    set_mask = ADFSAccessFlags(0)
    clear_mask = ADFSAccessFlags(0)

    # Two buckets for ignored pairs so the warning can distinguish
    # D/d (directory type flag) from other foreign letters.
    d_pairs: List[str] = []
    other_pairs: List[str] = []

    # Index walks in steps of two: op + letter. A while loop rather
    # than pairwise iteration keeps the error messages precise about
    # which position failed.
    i = 0

    while i < len(spec):
        op = spec[i]

        # A letter where we expected + or - means the user mixed
        # absolute letters into a mutation spec (e.g. "+L-Wr").
        if op not in "+-":
            raise ADFSError(
                "--access value must be either absolute (e.g. LWR) "
                "or mutation (e.g. +L-W), not both"
            )

        # An operator with no following letter, or another operator
        # straight after, means the spec is incomplete: "+", "+-L".
        if i + 1 >= len(spec) or spec[i + 1] in "+-":
            raise ADFSError(
                "mutation form needs at least one +X or -X pair"
            )

        ch = spec[i + 1]
        bit = _ADFS_ACCESS_LETTERS.get(ch)

        if bit is not None:

            # Route the bit into the matching accumulator.
            if op == "+":
                set_mask |= bit
            else:
                clear_mask |= bit

        elif ch in "Dd":
            d_pairs.append(op + ch)
        else:
            other_pairs.append(op + ch)

        i += 2

    # Contradictory mutations like '+L-L' would resolve one way or
    # the other depending on composition order; reject them instead.
    # Only applies to bits we actually recognised; ignored pairs never
    # reach the accumulators so they can't trigger a false conflict.
    conflict = set_mask & clear_mask

    if conflict:
        raise ADFSError(
            f"--access spec {spec!r} both sets and clears the same bit"
        )

    _warnAdfsIgnored(spec, d_pairs, other_pairs)

    return set_mask, clear_mask


def _warnAdfsIgnored(
    spec: str, d_items: List[str], other_items: List[str],
) -> None:
    """Emit one aggregated warning for non-access letters in an ADFS spec.

    D/d are called out separately: they are the directory type flag
    (created via ``mkdir``), not junk, so the warning names that fact
    instead of lumping them in with unknown letters. Everything else
    foreign is listed together under a generic message.
    """

    if not d_items and not other_items:
        return

    parts: List[str] = []

    if other_items:
        parts.append(
            f"ignoring non-access letters {''.join(other_items)!r}"
        )

    if d_items:
        parts.append(
            f"ignoring {''.join(d_items)!r} "
            f"(directory type flag, not an access permission; "
            f"use 'mkdir' to create directories)"
        )

    warnings.warn(
        f"ADFS --access spec {spec!r}: " + "; ".join(parts),
        BeebToolsWarning,
        stacklevel=3,
    )


# -----------------------------------------------------------------------
# Resolve an access argument to the new ADFSAccessFlags value
# -----------------------------------------------------------------------
#
# ``applyAccess`` accepts either a grammar spec string or an
# ``ADFSAccessFlags`` value. The three legal shapes (plus two error
# shapes - wrong IntFlag subclass, wholly wrong type) are dispatched
# here by runtime type rather than with an isinstance ladder inside
# ``applyAccess``. Each per-type handler is small and focused; adding
# a new input type is adding a new ``@register`` and nothing else.

@singledispatch
def _resolveAdfsAccess(
    access: object, current: ADFSAccessFlags,
) -> ADFSAccessFlags:
    """Default handler: the caller passed an unsupported type.

    Runs when the value is neither a ``str`` nor an ``IntFlag`` (and
    therefore no registered handler matches). The specific-IntFlag
    wrong-subclass case is handled by the ``IntFlag`` handler below.
    """

    raise TypeError(
        f"access must be ADFSAccessFlags or str, "
        f"got {type(access).__name__}"
    )


@_resolveAdfsAccess.register
def _(access: str, current: ADFSAccessFlags) -> ADFSAccessFlags:
    """String spec: parse the grammar and compose against ``current``."""

    # Both modes (absolute and mutation) reduce to a (set, clear)
    # pair, so the composition formula is the same for both.
    set_mask, clear_mask = _parseAdfsAccessSpec(access)

    return (current & ~clear_mask) | set_mask


@_resolveAdfsAccess.register
def _(access: ADFSAccessFlags, current: ADFSAccessFlags) -> ADFSAccessFlags:
    """Native flag value: absolute replacement, ``current`` is ignored."""

    return access


@_resolveAdfsAccess.register
def _(access: IntFlag, current: ADFSAccessFlags) -> ADFSAccessFlags:
    """Wrong IntFlag subclass (e.g. ``DfsAccessFlags`` on an ADFS image).

    ADFSAccessFlags is registered above, so dispatch only lands here
    when ``access`` is an ``IntFlag`` of a different subclass.
    """

    raise ValueError(
        f"expected ADFSAccessFlags, got {type(access).__name__}"
    )


# -----------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------

@dataclass(frozen=True)
class ADFSEntry(DiscEntry):
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
    def accessByte(self) -> int:
        """Return the full 8-bit ADFS access byte from the directory entry."""

        return self.access

    @property
    def accessFlags(self) -> ADFSAccessFlags:
        """Return the entry's access bits as ``ADFSAccessFlags``.

        The D bit at 0x08 is not modelled by the IntFlag type and is
        stripped here; ``is_directory`` carries that state separately.
        """

        return ADFSAccessFlags(self.access & ~_ADFS_DIRECTORY_BIT)

    @property
    def accessString(self) -> str:
        """Render the access bits as a display string.

        Leading ``D`` marks a directory. Owner letters follow in
        ``LWRE`` order; if any public bits are set, a ``/`` separator
        precedes the public letters in ``wre`` order. Empty string
        when no bits (and not a directory) are set.
        """

        flags = self.accessFlags

        # Directory prefix comes from is_directory, not from the
        # IntFlag: the D bit is not part of the flag vocabulary.
        parts = []
        if self.is_directory:
            parts.append("D")

        # Owner letters in fixed display order.
        for letter, bit in _ADFS_OWNER_DISPLAY:
            if flags & bit:
                parts.append(letter)

        # Public letters follow a slash separator, but only when at
        # least one public bit is present.
        public_letters = [
            letter for letter, bit in _ADFS_PUBLIC_DISPLAY if flags & bit
        ]
        if public_letters:
            parts.append("/")
            parts.extend(public_letters)

        return "".join(parts)

    @property
    def fullName(self) -> str:
        """Full ADFS path, e.g. '$.GAMES.ELITE'."""
        if self.directory == "$":
            return f"$.{self.name}"
        return f"{self.directory}.{self.name}"

    @property
    def isDirectory(self) -> bool:
        """True if this entry is a directory rather than a file."""
        return self.is_directory

    @property
    def isBasic(self) -> bool:
        """True if this entry looks like a BBC BASIC program.

        Uses the same execution address test as DFS. Directories are
        never treated as BASIC.
        """
        if self.isDirectory:
            return False
        return isBasicExecAddr(self.exec_addr)

    def __repr__(self) -> str:
        """Show class name, full path, load/exec addresses, and length or 'dir'."""
        kind = "dir" if self.is_directory else f"length={self.length}"
        return (f"ADFSEntry('{self.fullName}', "
                f"load=0x{self.load_addr:04X}, "
                f"exec=0x{self.exec_addr:04X}, "
                f"{kind})")

    def __str__(self) -> str:
        """Return the full ADFS path (e.g. '$.GAMES.ELITE')."""
        return self.fullName

    def __fspath__(self) -> str:
        """Host-safe path: convert ADFS '$.' separators to '/'."""
        return self.fullName.replace(".", "/")


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
class ADFSCatalogue(DiscCatalogue):
    """Flattened catalogue for one ADFS disc.

    Entries from the entire directory tree are flattened into a single
    tuple with full paths in each entry's directory field.
    """

    title: str
    cycle: int
    boot_option: BootOption
    disc_size: int
    entries: Tuple[ADFSEntry, ...]

    @property
    def tracks(self) -> int:
        """Number of tracks on this disc (disc_size / 16)."""
        return self.disc_size // ADFS_SECTORS_PER_TRACK


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

    Characters are decoded via the bbc codec (7-bit ASCII), then the
    result is truncated at the first NUL or 0x0D terminator.
    """
    text = data.decode("bbc")

    # Truncate at the first terminator (0x0D decodes to \r, 0x00 to \x00).
    for i, ch in enumerate(text):
        if ch == '\r' or ch == '\x00':
            return text[:i]

    return text


def _encodeString(text: str, length: int) -> bytes:
    """Encode a string into a fixed-length field padded with 0x0D.

    Truncates to length if the text is too long. Used for directory
    name, title, and footer string fields.
    """
    return text[:length].encode("bbc").ljust(length, b"\x0d")


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


def _write24le(buf: bytearray, offset: int, value: int) -> None:
    """Write a 24-bit little-endian unsigned integer into buf."""
    buf[offset] = value & 0xFF
    buf[offset + 1] = (value >> 8) & 0xFF
    buf[offset + 2] = (value >> 16) & 0xFF


def _write32le(buf: bytearray, offset: int, value: int) -> None:
    """Write a 32-bit little-endian unsigned integer into buf."""
    buf[offset] = value & 0xFF
    buf[offset + 1] = (value >> 8) & 0xFF
    buf[offset + 2] = (value >> 16) & 0xFF
    buf[offset + 3] = (value >> 24) & 0xFF


def _encodeEntryName(name: str, access: int) -> bytes:
    """Encode a 10-byte ADFS entry name field with access bits in bit 7.

    The name is padded with 0x0D terminators if shorter than 10 chars.
    Access bits are ORed into bit 7 of each byte position: bit 0 = R,
    bit 1 = W, bit 2 = L, bit 3 = D, etc.
    """
    buf = bytearray(name[:10].encode("bbc").ljust(10, b"\x0d"))

    # OR in the access bit for each byte position.
    for i in range(10):
        if access & (1 << i):
            buf[i] |= 0x80

    return bytes(buf)


def _encodeEntry(entry: ADFSEntry) -> bytes:
    """Encode one ADFSEntry into its 26-byte on-disc representation.

    Produces the exact byte layout expected in a Hugo directory block:
    bytes 0-9 are the name with access bits, 10-13 load address,
    14-17 exec address, 18-21 length, 22-24 start sector, 25 sequence.
    """
    buf = bytearray(ADFS_ENTRY_SIZE)

    buf[0:10] = _encodeEntryName(entry.name, entry.access)

    _write32le(buf, 0x0A, entry.load_addr)
    _write32le(buf, 0x0E, entry.exec_addr)
    _write32le(buf, 0x12, entry.length)
    _write24le(buf, 0x16, entry.start_sector)
    buf[0x19] = entry.sequence

    return bytes(buf)


# -----------------------------------------------------------------------
# ADFSSide - sector I/O and directory parsing
# -----------------------------------------------------------------------

class ADFSSide(DiscSide):
    """Reader for an ADFS disc image filesystem.

    ADFS uses a single flat logical sector space regardless of whether
    the physical disc is single-sided or double-sided. The side number
    is always 0 to satisfy the DiscSide contract.
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

    @property
    def maxTitleLength(self) -> int:
        """Maximum disc title length for ADFS (19 characters)."""
        return 19

    # -------------------------------------------------------------------
    # Python data model
    # -------------------------------------------------------------------

    def __repr__(self) -> str:
        """Show class name, disc title, entry count, and free space."""
        cat = self.readCatalogue()
        return (f"ADFSSide(title='{cat.title}', "
                f"{len(cat.entries)} entries, "
                f"{self.freeSpace()} sectors free)")

    # -------------------------------------------------------------------
    # Sector access
    # -------------------------------------------------------------------

    def _sectorOffset(self, sector_num: int) -> int:
        """Byte offset of a logical sector in the backing store.

        .adf (single-sided) files store sectors sequentially: logical sector
        N is at byte offset N * 256.

        .adl (double-sided) files use track-interleaved layout: track 0 side 0,
        track 0 side 1, track 1 side 0, track 1 side 1, etc.  Logical sectors
        0-1279 are on side 0 and 1280-2559 are on side 1.
        """
        if not self._image.is_adl:
            return sector_num * ADFS_SECTOR_SIZE

        # Interleaved layout: 16 sectors per half-track, 32 file-sectors per
        # track pair.  Side 0 occupies the first 16 file-sectors of each pair;
        # side 1 occupies the second 16.
        half = ADFS_L_SECTORS // 2  # 1280 sectors per side
        if sector_num < half:
            track = sector_num // ADFS_SECTORS_PER_TRACK
            sec_in_track = sector_num % ADFS_SECTORS_PER_TRACK
            file_sector = track * 2 * ADFS_SECTORS_PER_TRACK + sec_in_track
        else:
            logical_s1 = sector_num - half
            track = logical_s1 // ADFS_SECTORS_PER_TRACK
            sec_in_track = logical_s1 % ADFS_SECTORS_PER_TRACK
            file_sector = (track * 2 * ADFS_SECTORS_PER_TRACK
                           + ADFS_SECTORS_PER_TRACK + sec_in_track)

        return file_sector * ADFS_SECTOR_SIZE

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
        """Read multiple contiguous logical sectors.

        Reads sector by sector so that track-crossing reads work correctly
        in the interleaved .adl layout.
        """
        return b"".join(self._readSector(start_sector + i) for i in range(count))

    def _writeSector(self, sector_num: int, data: bytes) -> None:
        """Write one 256-byte sector to the backing store."""
        offset = self._sectorOffset(sector_num)
        end = offset + ADFS_SECTOR_SIZE

        if end > len(self._image.data):
            raise ADFSError(
                f"Sector {sector_num} at offset {offset} extends beyond "
                f"the image ({len(self._image.data)} bytes)"
            )

        self._image.data[offset:end] = data[:ADFS_SECTOR_SIZE]

    def _writeSectors(self, start_sector: int, data: bytes) -> None:
        """Write a contiguous block of sectors to the backing store.

        The data length must be a multiple of 256 bytes.  Writes sector by
        sector so that track-crossing writes work correctly in the interleaved
        .adl layout.
        """
        count = len(data) // ADFS_SECTOR_SIZE
        for i in range(count):
            chunk = data[i * ADFS_SECTOR_SIZE: (i + 1) * ADFS_SECTOR_SIZE]
            self._writeSector(start_sector + i, chunk)

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

    def writeFreeSpaceMap(self, fsm: ADFSFreeSpaceMap) -> None:
        """Encode an ADFSFreeSpaceMap and write it back to sectors 0-1.

        Recomputes both sector checksums. Invalidates the cached FSM
        so the next readFreeSpaceMap() call will re-parse from disc.
        """
        sec0 = bytearray(ADFS_SECTOR_SIZE)
        sec1 = bytearray(ADFS_SECTOR_SIZE)

        # Write free-space block pairs.
        for i, (start, length) in enumerate(fsm.blocks):
            base = i * 3
            _write24le(sec0, base, start)
            _write24le(sec1, base, length)

        # Total sectors on disc (sector 0, offset 0xFC).
        _write24le(sec0, 0xFC, fsm.total_sectors)

        # Disc identifier (sector 1, offset 0xFB).
        sec1[0xFB] = fsm.disc_id & 0xFF
        sec1[0xFC] = (fsm.disc_id >> 8) & 0xFF

        # Boot option (sector 1, offset 0xFD).
        boot_val = (fsm.boot_option.value
                    if isinstance(fsm.boot_option, BootOption)
                    else int(fsm.boot_option))
        sec1[0xFD] = boot_val & 0x03

        # End-of-list pointer (sector 1, offset 0xFE).
        sec1[0xFE] = len(fsm.blocks) * 3

        # Compute and store checksums.
        sec0[0xFF] = _adfsChecksum(bytes(sec0))
        sec1[0xFF] = _adfsChecksum(bytes(sec1))

        self._writeSector(0, bytes(sec0))
        self._writeSector(1, bytes(sec1))

        # Invalidate the cached FSM.
        self._fsm = None

    def _allocateBlock(self, sectors_needed: int) -> int:
        """Allocate a contiguous block from the free space map.

        Uses first-fit: scans blocks in order and takes the first one
        big enough. If the chosen block is larger than needed, it is
        split. Updates the FSM on disc and returns the start sector.

        Raises:
            ADFSError: If no block is large enough.
        """
        fsm = self.readFreeSpaceMap()
        blocks = list(fsm.blocks)

        for i, (start, length) in enumerate(blocks):
            if length >= sectors_needed:
                # Found a suitable block. Split or consume it.
                allocated_start = start

                if length == sectors_needed:
                    blocks.pop(i)
                else:
                    blocks[i] = (start + sectors_needed, length - sectors_needed)

                updated = ADFSFreeSpaceMap(
                    blocks=tuple(blocks),
                    total_sectors=fsm.total_sectors,
                    disc_id=fsm.disc_id,
                    boot_option=fsm.boot_option,
                )
                self.writeFreeSpaceMap(updated)

                return allocated_start

        raise ADFSError(
            f"Cannot allocate {sectors_needed} contiguous sectors: "
            f"largest free block is "
            f"{max((l for _, l in blocks), default=0)} sectors"
        )

    def _reserveBlock(self, start_sector: int, sectors_needed: int) -> bool:
        """Reserve an exact sector range that was requested by a placed write.

        Walks the free space map, finds the free block that contains
        the requested range, and splits it so the range is removed
        from the map. Returns True when the reservation succeeded (the
        range was wholly free), False when the range overlaps sectors
        that are already allocated (for example a directory or an
        earlier file), in which case the FSM is left unchanged and the
        caller should fall back to normal allocation.

        Args:
            start_sector:     First sector to reserve.
            sectors_needed:   Number of contiguous sectors to reserve.

        Returns:
            True if the range was wholly free and has been reserved,
            False if the range overlaps any already-allocated sectors.
        """

        if sectors_needed <= 0:
            return True

        fsm = self.readFreeSpaceMap()
        blocks = list(fsm.blocks)

        end_sector = start_sector + sectors_needed

        # Confirm the entire requested range is covered by free blocks
        # before mutating anything. Any gap means the range collides
        # with an already-allocated sector and the reservation fails.
        cursor = start_sector

        for (b_start, b_length) in blocks:
            if cursor >= end_sector:
                break

            b_end = b_start + b_length

            if b_end <= cursor:
                continue

            if b_start > cursor:
                return False

            cursor = b_end

        if cursor < end_sector:
            return False

        new_blocks: List[Tuple[int, int]] = []

        for (b_start, b_length) in blocks:
            b_end = b_start + b_length

            # Block entirely outside the requested range: keep as-is.
            if b_end <= start_sector or b_start >= end_sector:
                new_blocks.append((b_start, b_length))
                continue

            # Keep the portion of the free block before the reservation.
            if b_start < start_sector:
                new_blocks.append((b_start, start_sector - b_start))

            # Keep the portion of the free block after the reservation.
            if b_end > end_sector:
                new_blocks.append((end_sector, b_end - end_sector))

        new_blocks.sort(key=lambda item: item[0])

        updated = ADFSFreeSpaceMap(
            blocks=tuple(new_blocks),
            total_sectors=fsm.total_sectors,
            disc_id=fsm.disc_id,
            boot_option=fsm.boot_option,
        )
        self.writeFreeSpaceMap(updated)

        return True

    def _freeBlock(self, start_sector: int, length: int) -> None:
        """Return a block of sectors to the free space map.

        Inserts the block in sorted order by start sector, then
        merges with any adjacent blocks to keep the list compact.
        """
        fsm = self.readFreeSpaceMap()
        blocks = list(fsm.blocks)

        # Insert in sorted order.
        insert_pos = 0

        for i, (s, _) in enumerate(blocks):
            if start_sector < s:
                break
            insert_pos = i + 1

        blocks.insert(insert_pos, (start_sector, length))

        # Merge with the right neighbour.
        if insert_pos + 1 < len(blocks):
            curr_start, curr_len = blocks[insert_pos]
            next_start, next_len = blocks[insert_pos + 1]

            if curr_start + curr_len == next_start:
                blocks[insert_pos] = (curr_start, curr_len + next_len)
                blocks.pop(insert_pos + 1)

        # Merge with the left neighbour.
        if insert_pos > 0:
            prev_start, prev_len = blocks[insert_pos - 1]
            curr_start, curr_len = blocks[insert_pos]

            if prev_start + prev_len == curr_start:
                blocks[insert_pos - 1] = (prev_start, prev_len + curr_len)
                blocks.pop(insert_pos)

        updated = ADFSFreeSpaceMap(
            blocks=tuple(blocks),
            total_sectors=fsm.total_sectors,
            disc_id=fsm.disc_id,
            boot_option=fsm.boot_option,
        )
        self.writeFreeSpaceMap(updated)

    def freeSpace(self) -> int:
        """Total number of free sectors across all FSM blocks."""
        fsm = self.readFreeSpaceMap()
        return sum(length for _, length in fsm.blocks)

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

        # Extract name characters from bits 0-6 via decode7bit,
        # then truncate at the first 0x0D or NUL terminator.
        name = _decodeString(raw[offset : offset + 10])

        # Decode access flags of interest. D lives outside ADFSAccessFlags
        # (see _ADFS_DIRECTORY_BIT comment) so it reads as a literal here.
        locked = bool(access & ADFSAccessFlags.OWNER_L)
        is_directory = bool(access & _ADFS_DIRECTORY_BIT)

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
    # Directory writing
    # -------------------------------------------------------------------

    @staticmethod
    def _encodeDirectory(directory: ADFSDirectory) -> bytes:
        """Encode an ADFSDirectory into a 0x500-byte block.

        Entries are written in their existing order (caller must ensure
        case-insensitive sort). The block has Hugo markers in both
        header and footer, with sequence bytes matching.
        """
        buf = bytearray(ADFS_DIR_LENGTH)

        # Header: sequence byte + "Hugo".
        buf[0] = directory.sequence
        buf[1:5] = ADFS_HUGO_MAGIC

        # Write entries starting at offset 5.
        offset = ADFS_HEADER_SIZE

        for entry in directory.entries:
            buf[offset:offset + ADFS_ENTRY_SIZE] = _encodeEntry(entry)
            offset += ADFS_ENTRY_SIZE

        # End-of-entries marker (zero byte).
        if offset < _FOOTER_END_MARKER:
            buf[offset] = 0x00

        # Footer: directory name (10 bytes at 0x4CC).
        buf[_FOOTER_NAME:_FOOTER_NAME + 10] = _encodeString(
            directory.name, 10
        )

        # Parent sector (3 bytes at 0x4D6).
        _write24le(buf, _FOOTER_PARENT, directory.parent_sector)

        # Directory title (19 bytes at 0x4D9).
        buf[_FOOTER_TITLE:_FOOTER_TITLE + 19] = _encodeString(
            directory.title, 19
        )

        # Footer sequence + "Hugo" (at 0x4FA-0x4FE).
        buf[_FOOTER_SEQ] = directory.sequence
        buf[_FOOTER_HUGO:_FOOTER_HUGO + 4] = ADFS_HUGO_MAGIC

        return bytes(buf)

    def writeDirectory(self, sector: int, directory: ADFSDirectory) -> None:
        """Encode a directory and write it to disc.

        BCD-increments the sequence number before writing. Invalidates
        the catalogue cache so the next readCatalogue() re-parses.
        """
        # BCD-increment the sequence number.
        seq = directory.sequence
        low = (seq & 0x0F) + 1

        if low > 9:
            low = 0
            high = ((seq >> 4) + 1) & 0x0F
        else:
            high = (seq >> 4) & 0x0F

        new_seq = (high << 4) | low

        updated = ADFSDirectory(
            name=directory.name,
            title=directory.title,
            parent_sector=directory.parent_sector,
            sequence=new_seq,
            entries=directory.entries,
        )

        raw = self._encodeDirectory(updated)
        self._writeSectors(sector, raw)

        # Invalidate caches.
        self._catalogue = None

    @staticmethod
    def _insertEntry(
        directory: ADFSDirectory, entry: ADFSEntry
    ) -> ADFSDirectory:
        """Return a new directory with the entry inserted in sorted order.

        Entries are sorted case-insensitively by name. Raises ADFSError
        if the directory is full or a duplicate name exists.
        """
        if len(directory.entries) >= ADFS_MAX_ENTRIES:
            raise ADFSError(
                f"Directory '{directory.name}' is full "
                f"({ADFS_MAX_ENTRIES} entries)"
            )

        # Check for duplicate name (case-insensitive).
        entry_upper = entry.name.upper()

        for existing in directory.entries:
            if existing.name.upper() == entry_upper:
                raise ADFSError(
                    f"Duplicate name '{entry.name}' in "
                    f"directory '{directory.name}'"
                )

        # Insert in case-insensitive sorted order.
        entries = list(directory.entries)
        insert_pos = len(entries)

        for i, existing in enumerate(entries):
            if entry_upper < existing.name.upper():
                insert_pos = i
                break

        entries.insert(insert_pos, entry)

        return ADFSDirectory(
            name=directory.name,
            title=directory.title,
            parent_sector=directory.parent_sector,
            sequence=directory.sequence,
            entries=tuple(entries),
        )

    @staticmethod
    def _removeEntry(
        directory: ADFSDirectory, name: str
    ) -> ADFSDirectory:
        """Return a new directory with the named entry removed.

        Name matching is case-insensitive. Raises ADFSError if the
        name is not found.
        """
        name_upper = name.upper()
        entries = list(directory.entries)

        for i, existing in enumerate(entries):
            if existing.name.upper() == name_upper:
                entries.pop(i)

                return ADFSDirectory(
                    name=directory.name,
                    title=directory.title,
                    parent_sector=directory.parent_sector,
                    sequence=directory.sequence,
                    entries=tuple(entries),
                )

        raise ADFSError(
            f"Entry '{name}' not found in directory '{directory.name}'"
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
            if entry.isDirectory:
                child_path = f"{path}.{entry.name}"
                result.extend(
                    self.walkDirectories(entry.start_sector, child_path)
                )

        return result

    # -------------------------------------------------------------------
    # Catalogue (DiscSide interface)
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

    def writeCatalogue(self, catalogue: ADFSCatalogue) -> None:
        """Write catalogue-level metadata back to the ADFS image.

        Updates the root directory title and boot option in the free
        space map. Entries are not modified - use writeDirectory() for
        per-entry changes.

        Clears the catalogue cache so the next readCatalogue() re-parses.
        """
        # Update the root directory title.
        root = self.readDirectory(ADFS_ROOT_SECTOR)
        updated_root = ADFSDirectory(
            name=root.name,
            title=catalogue.title,
            parent_sector=root.parent_sector,
            sequence=root.sequence,
            entries=root.entries,
        )
        self.writeDirectory(ADFS_ROOT_SECTOR, updated_root)

        # Update the boot option in the free space map.
        fsm = self.readFreeSpaceMap()
        updated_fsm = ADFSFreeSpaceMap(
            blocks=fsm.blocks,
            total_sectors=fsm.total_sectors,
            disc_id=fsm.disc_id,
            boot_option=catalogue.boot_option,
        )
        self.writeFreeSpaceMap(updated_fsm)

        # Invalidate the catalogue cache.
        self._catalogue = None

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

    # -------------------------------------------------------------------
    # File and directory write operations
    # -------------------------------------------------------------------

    def writeFile(self, entry: ADFSEntry, data: bytes) -> None:
        """Write raw bytes to the sectors indicated by an entry.

        Pads the data to a sector boundary. Does not update the
        directory or FSM - this is a low-level primitive used by
        addFile and mkdir.
        """
        if len(data) == 0:
            return

        sectors_needed = (
            (len(data) + ADFS_SECTOR_SIZE - 1) // ADFS_SECTOR_SIZE
        )

        # Pad to a full sector boundary.
        padded = bytearray(sectors_needed * ADFS_SECTOR_SIZE)
        padded[:len(data)] = data

        self._writeSectors(entry.start_sector, bytes(padded))

    def _resolveParent(self, path: str) -> Tuple[int, ADFSDirectory, str]:
        """Resolve a dotted ADFS path to its parent directory.

        Returns (parent_sector, parent_directory, leaf_name).
        Raises ADFSError if any intermediate directory is not found.
        """
        parts = path.split(".")

        # Strip the leading '$' root prefix.
        if parts[0] == "$":
            parts = parts[1:]

        if not parts:
            raise ADFSError(f"Invalid path: '{path}'")

        leaf_name = parts[-1]
        dir_parts = parts[:-1]

        # Walk from root to the parent directory.
        current_sector = ADFS_ROOT_SECTOR
        current_dir = self.readDirectory(ADFS_ROOT_SECTOR)

        for part in dir_parts:
            part_upper = part.upper()
            found = False

            for entry in current_dir.entries:
                if entry.name.upper() == part_upper and entry.isDirectory:
                    current_sector = entry.start_sector
                    current_dir = self.readDirectory(entry.start_sector)
                    found = True
                    break

            if not found:
                raise ADFSError(
                    f"Directory '{part}' not found in path '{path}'"
                )

        return current_sector, current_dir, leaf_name

    def addFile(self, spec: DiscFile) -> 'ADFSEntry':
        """Add a file to the disc image at the given ADFS path.

        The parent directory must already exist. The filename is
        validated and the file is inserted in sorted order.

        Args:
            spec: DiscFile describing the file to add.

        Returns:
            The ADFSEntry created for the new file.
        """
        parent_sector, parent_dir, leaf_name = self._resolveParent(
            spec.path
        )

        validateAdfsName(leaf_name)

        # Allocate sectors for the file data, or honour an explicit
        # placement hint when the caller has supplied one. Placed
        # writes are the mechanism used to preserve the on-disc
        # layout of copy-protected discs that declare overlapping
        # sector allocations; byte-consistency in the overlap region
        # is the caller's responsibility. The FSM is carved where it
        # can be and the call tolerates already-reserved ranges from
        # a prior placed write.
        data = spec.data
        if len(data) > 0:
            sectors_needed = (
                (len(data) + ADFS_SECTOR_SIZE - 1) // ADFS_SECTOR_SIZE
            )

            if spec.start_sector is not None and self._reserveBlock(
                spec.start_sector, sectors_needed
            ):
                start_sector = spec.start_sector
            else:
                start_sector = self._allocateBlock(sectors_needed)
        else:
            start_sector = 0

        # Build the access bits: R + W by default, plus L if locked.
        access = int(ADFSAccessFlags.OWNER_R | ADFSAccessFlags.OWNER_W)
        if spec.locked:
            access |= int(ADFSAccessFlags.OWNER_L)

        entry = ADFSEntry(
            name=leaf_name,
            directory="",
            load_addr=spec.load_addr,
            exec_addr=spec.exec_addr,
            length=len(data),
            start_sector=start_sector,
            locked=spec.locked,
            is_directory=False,
            access=access,
            sequence=0,
        )

        # Write the file data.
        self.writeFile(entry, data)

        # Insert the entry into the parent directory and write back.
        updated_dir = self._insertEntry(parent_dir, entry)
        self.writeDirectory(parent_sector, updated_dir)

        return entry

    def deleteFile(self, path: str) -> None:
        """Delete a file from the disc image at the given ADFS path.

        Refuses to delete directories. Frees the file's sectors back
        to the FSM.
        """
        parent_sector, parent_dir, leaf_name = self._resolveParent(path)

        # Find the entry.
        name_upper = leaf_name.upper()
        target = None

        for entry in parent_dir.entries:
            if entry.name.upper() == name_upper:
                target = entry
                break

        if target is None:
            raise ADFSError(
                f"File '{leaf_name}' not found in directory"
            )

        if target.isDirectory:
            raise ADFSError(
                f"Cannot delete directory '{leaf_name}' with deleteFile - "
                f"directories must be empty and removed individually"
            )

        # Remove from directory and write back.
        updated_dir = self._removeEntry(parent_dir, leaf_name)
        self.writeDirectory(parent_sector, updated_dir)

        # Free the file's sectors.
        if target.length > 0:
            sectors_used = (
                (target.length + ADFS_SECTOR_SIZE - 1) // ADFS_SECTOR_SIZE
            )
            self._freeBlock(target.start_sector, sectors_used)

    def updateEntry(self, path: str, updated: 'ADFSEntry') -> None:
        """Replace a directory entry with an updated version.

        Finds the entry matching the given path and substitutes it with
        the updated entry. Used to change attributes (locked, load_addr,
        exec_addr) without moving the file on disc.

        Args:
            path:    Full ADFS path (e.g. '$.GAMES.ELITE').
            updated: Replacement entry with modified attributes.

        Raises:
            ADFSError: If the file is not found.
        """
        parent_sector, parent_dir, leaf_name = self._resolveParent(path)

        # Keep the 'access' bitmask in sync with the 'locked' bool.
        # _encodeEntryName reads access (not locked) when writing to disc.
        owner_l = int(ADFSAccessFlags.OWNER_L)

        if updated.locked and not (updated.access & owner_l):
            updated = replace(updated, access=updated.access | owner_l)
        elif not updated.locked and (updated.access & owner_l):
            updated = replace(updated, access=updated.access & ~owner_l)

        # Find the entry and replace it.
        name_upper = leaf_name.upper()
        new_entries = []
        found = False

        for entry in parent_dir.entries:
            if not found and entry.name.upper() == name_upper:
                new_entries.append(updated)
                found = True
            else:
                new_entries.append(entry)

        if not found:
            raise ADFSError(
                f"File '{leaf_name}' not found in directory"
            )

        updated_dir = ADFSDirectory(
            name=parent_dir.name,
            title=parent_dir.title,
            parent_sector=parent_dir.parent_sector,
            sequence=parent_dir.sequence,
            entries=tuple(new_entries),
        )

        self.writeDirectory(parent_sector, updated_dir)

    def applyAccess(
        self,
        entry: 'DiscEntry',
        access: Union[IntFlag, str],
    ) -> None:
        """Apply an access change to an ADFS entry on disc.

        An ``ADFSAccessFlags`` value is an absolute replacement.
        A ``str`` value is parsed with the ADFS --access grammar
        (``LWR``, ``LWR/r``, ``""``, ``+L-W``) and composed against
        the entry's current access byte.

        Directory invariants are enforced here: owner-W and all
        public bits are stripped with a ``BeebToolsWarning`` to keep
        the directory state in the DLR-only window allowed by
        ``*ACCESS``. The D bit is preserved from ``entry.is_directory``.

        The updated entry is written back via :meth:`updateEntry`.
        """

        # An ADFSSide only mutates its own catalogue entries. A
        # foreign entry is an API misuse, not a user-facing error.
        if not isinstance(entry, ADFSEntry):
            raise ValueError(
                f"applyAccess on ADFSSide expected ADFSEntry, "
                f"got {type(entry).__name__}"
            )

        # Strip the D bit before wrapping into ADFSAccessFlags: the
        # enum does not model the directory flag, and leaving 0x08 in
        # would be silently dropped (or rejected) by IntFlag depending
        # on Python's flag-boundary mode. D is reapplied from
        # is_directory at the end of the method.
        current = ADFSAccessFlags(entry.access & ~_ADFS_DIRECTORY_BIT)

        # Resolve the requested change via the module-level singledispatch
        # helper. It routes str / ADFSAccessFlags / foreign-IntFlag /
        # anything-else to four small dedicated handlers and returns the
        # new absolute flag value.
        new_flags = _resolveAdfsAccess(access, current)

        # Directory invariant: owner-W and public bits are out of
        # grammar on a directory. Stripping them here blocks the DWLR
        # regression via both the CLI and library paths in one place.
        if entry.is_directory:

            illegal = new_flags & _ADFS_DIR_ILLEGAL_BITS

            if illegal:

                warnings.warn(
                    f"stripping invalid access bits "
                    f"{illegal!r} from directory {entry.fullName}",
                    BeebToolsWarning,
                    stacklevel=2,
                )

                new_flags &= ~_ADFS_DIR_ILLEGAL_BITS

        # Compose the on-disc byte: ADFSAccessFlags bits plus the D
        # flag for directories. Done as int arithmetic because the D
        # bit is not part of the IntFlag enum and would otherwise be
        # stripped by CONFORM boundary semantics.
        new_access_byte = int(new_flags)

        if entry.is_directory:
            new_access_byte |= _ADFS_DIRECTORY_BIT

        # Keep the boolean ``locked`` shortcut in sync with the bit so
        # that `entry.locked` and `entry.access & OWNER_L` never drift.
        new_locked = bool(new_flags & ADFSAccessFlags.OWNER_L)

        # updateEntry handles the write-back and cycle increment.
        updated = replace(entry, access=new_access_byte, locked=new_locked)
        self.updateEntry(entry.fullName, updated)

    def renameFile(self, old_path: str, new_path: str) -> None:
        """Rename a file within the same directory.

        Changes the entry's name field and rewrites the parent
        directory with entries re-sorted. The file data is not moved.

        Cross-directory moves are not supported - both paths must
        share the same parent directory.

        Args:
            old_path: Current full ADFS path (e.g. '$.GAMES.ELITE').
            new_path: New full ADFS path (e.g. '$.GAMES.NEWNAME').

        Raises:
            ADFSError: If the source is not found, the destination
                       already exists, or the paths have different
                       parent directories.
        """
        old_sector, old_dir, old_leaf = self._resolveParent(old_path)
        new_sector, new_dir, new_leaf = self._resolveParent(new_path)

        # Both paths must live in the same parent directory.
        if old_sector != new_sector:
            raise ADFSError(
                "Cross-directory rename is not supported - "
                "both paths must be in the same directory"
            )

        # Validate the new name against ADFS naming rules.
        validateAdfsName(new_leaf)

        # Find the source entry.
        old_upper = old_leaf.upper()
        new_upper = new_leaf.upper()
        source = None

        for entry in old_dir.entries:
            if entry.name.upper() == old_upper:
                source = entry
                break

        if source is None:
            raise ADFSError(
                f"File '{old_leaf}' not found in directory"
            )

        # Check the destination name is not already taken.
        for entry in old_dir.entries:
            if entry.name.upper() == new_upper and entry is not source:
                raise ADFSError(
                    f"File '{new_leaf}' already exists in directory"
                )

        # Build the renamed entry.
        renamed = replace(source, name=new_leaf)

        # Remove the old entry and re-insert with the new name so that
        # directory entries remain sorted by name.
        remaining = [
            e for e in old_dir.entries
            if e.name.upper() != old_upper
        ]

        # Insert in case-insensitive sorted order.
        insert_pos = len(remaining)

        for i, existing in enumerate(remaining):
            if new_upper < existing.name.upper():
                insert_pos = i
                break

        remaining.insert(insert_pos, renamed)

        updated_dir = ADFSDirectory(
            name=old_dir.name,
            title=old_dir.title,
            parent_sector=old_dir.parent_sector,
            sequence=old_dir.sequence,
            entries=tuple(remaining),
        )

        self.writeDirectory(old_sector, updated_dir)

    def mkdir(self, path: str) -> None:
        """Create a new subdirectory at the given ADFS path.

        The parent directory must already exist. The new directory is
        allocated 5 sectors from the FSM and initialised with Hugo
        markers, parent pointer, and an empty entry list.
        """
        parent_sector, parent_dir, leaf_name = self._resolveParent(path)

        validateAdfsName(leaf_name)

        # Allocate 5 sectors for the directory.
        dir_sector = self._allocateBlock(5)

        # Build and write the empty directory.
        new_dir = ADFSDirectory(
            name=leaf_name,
            title=leaf_name,
            parent_sector=parent_sector,
            sequence=0x01,
            entries=(),
        )
        raw = self._encodeDirectory(new_dir)
        self._writeSectors(dir_sector, raw)

        # Create the directory entry with D + L + R access (DLR), matching
        # *CDIR. W is not a valid directory access bit per the ADFS *ACCESS
        # grammar.
        dir_access = _ADFS_DIRECTORY_BIT | int(
            ADFSAccessFlags.OWNER_L | ADFSAccessFlags.OWNER_R
        )

        dir_entry = ADFSEntry(
            name=leaf_name,
            directory="",
            load_addr=0,
            exec_addr=0,
            length=ADFS_DIR_LENGTH,
            start_sector=dir_sector,
            locked=True,
            is_directory=True,
            access=dir_access,
            sequence=0,
        )

        # Insert into parent and write back.
        updated_dir = self._insertEntry(parent_dir, dir_entry)
        self.writeDirectory(parent_sector, updated_dir)

# -----------------------------------------------------------------------
# ADFSImage - disc image container
# -----------------------------------------------------------------------

class ADFSImage(DiscImage):
    """Read-only ADFS disc image container.

    Owns the bytearray backing store and provides an ADFSSide view
    for the filesystem. Both ADF (single-sided) and ADL (double-sided
    track-interleaved) layouts are supported as one logical filesystem.
    """

    def __init__(self, data: bytearray, is_adl: bool) -> None:
        """Wrap an existing image bytearray.

        Args:
            data:   Backing store for the disc image.
            is_adl: True for .adl double-sided track-interleaved format.
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

    # -------------------------------------------------------------------
    # Python data model
    # -------------------------------------------------------------------

    def __repr__(self) -> str:
        """Show class name, disc format (ADF/ADL), and side count."""
        fmt = "ADL" if self._is_adl else "ADF"
        return f"ADFSImage({fmt}, {len(self._sides)} sides)"


# -----------------------------------------------------------------------
# Module-level functions
# -----------------------------------------------------------------------

def openAdfsImage(path: str) -> ADFSImage:
    """Open an ADFS disc image file and return an ADFSImage.

    Format is inferred from the file extension:
        .adf  -- single-sided (ADFS-S or ADFS-M)
        .adl  -- double-sided track-interleaved (ADFS-L)

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


def validateAdfsName(name: str) -> None:
    """Validate an ADFS filename.

    An ADFS name must be 1-10 characters, printable ASCII (0x21-0x7E),
    with no spaces or control characters. Bit 7 is reserved for access
    flag encoding so characters above 0x7E are disallowed.

    Raises:
        ADFSError: If the name is invalid.
    """
    if not name:
        raise ADFSError("ADFS filename must not be empty")

    if len(name) > 10:
        raise ADFSError(
            f"ADFS filename '{name}' is {len(name)} characters "
            f"(maximum is 10)"
        )

    for ch in name:
        code = ord(ch)

        if code < 0x21 or code > 0x7E:
            raise ADFSError(
                f"ADFS filename '{name}' contains invalid character "
                f"0x{code:02X} (must be 0x21-0x7E)"
            )


# -----------------------------------------------------------------------
# ADFS format size constants
# -----------------------------------------------------------------------

ADFS_S_SECTORS = 640    # 160K, single-sided 40-track
ADFS_M_SECTORS = 1280   # 320K, single-sided 80-track
ADFS_L_SECTORS = 2560   # 640K, double-sided 80-track


def createAdfsImage(
    total_sectors: int = ADFS_M_SECTORS,
    title: str = "",
    boot_option: BootOption = BootOption.OFF,
    disc_id: int = 0,
) -> ADFSImage:
    """Create a blank ADFS disc image with a valid FSM and root directory.

    Returns an ADFSImage ready for addFile/mkdir operations.
    """
    data = bytearray(total_sectors * ADFS_SECTOR_SIZE)
    is_adl = total_sectors > ADFS_M_SECTORS

    image = ADFSImage(data, is_adl)
    side = image.sides[0]

    # Build and write the free space map. The root directory occupies
    # sectors 2-6, so free space starts at sector 7.
    fsm = ADFSFreeSpaceMap(
        blocks=((7, total_sectors - 7),),
        total_sectors=total_sectors,
        disc_id=disc_id,
        boot_option=boot_option,
    )
    side.writeFreeSpaceMap(fsm)

    # Build and write the root directory.
    root = ADFSDirectory(
        name="$",
        title=title or "$",
        parent_sector=ADFS_ROOT_SECTOR,
        sequence=0x01,
        entries=(),
    )
    raw = ADFSSide._encodeDirectory(root)
    side._writeSectors(ADFS_ROOT_SECTOR, raw)

    return image
