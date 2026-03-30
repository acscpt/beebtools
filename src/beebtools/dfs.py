# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""DFS disc image reader.

Supports .ssd (single-sided) and .dsd (double-sided interleaved) formats.
Provides catalogue parsing and file extraction for Acorn DFS disc images.
"""

from typing import Dict, List, Optional, Tuple, Union

SECTOR_SIZE = 256
SECTORS_PER_TRACK = 10


class DFSDisc:
    """Represents one side of a DFS disc image (SSD or DSD)."""

    def __init__(self, image_data: bytes, side: int, is_dsd: bool) -> None:
        """Create a DFS disc-side reader.

        Args:
            image_data: Full disc image as bytes.
            side: Disc side number (0 or 1) represented by this instance.
            is_dsd: True when the image is .dsd interleaved format.
        """
        self.image = image_data
        self.side = side
        self.is_dsd = is_dsd

    @staticmethod
    def _dsdSectorOffset(track: int, side: int, sector: int) -> int:
        """Byte offset for a sector in a .dsd interleaved image."""
        return (track * 20 + side * 10 + sector) * SECTOR_SIZE

    @staticmethod
    def _ssdSectorOffset(sector: int) -> int:
        """Byte offset for a sector in a .ssd image."""
        return sector * SECTOR_SIZE

    def _readSector(self, sector_num: int) -> bytes:
        """Read one DFS logical sector from this side.

        Args:
            sector_num: Logical sector number on this side.

        Returns:
            256-byte sector payload as bytes.
        """
        track = sector_num // SECTORS_PER_TRACK
        sector_in_track = sector_num % SECTORS_PER_TRACK

        if self.is_dsd:
            off = self._dsdSectorOffset(track, self.side, sector_in_track)
        else:
            off = self._ssdSectorOffset(sector_num)

        return self.image[off : off + SECTOR_SIZE]

    def readCatalogue(self) -> Tuple[str, List[Dict[str, Union[str, int, bool]]]]:
        """Parse the DFS catalogue and return all file entries.

        Returns a tuple of (title, entries) where entries is a list of dicts,
        each with keys: name, dir, load, exec, length, start_sector, locked.

        Returns:
            Tuple of (disc title string, list of entry dicts).
        """
        # The catalogue lives in sectors 0 and 1 of track 0.
        sec0 = self._readSector(0)
        sec1 = self._readSector(1)

        # Disc title: first 8 characters in sector 0, next 4 in sector 1.
        title = (bytes(sec0[0:8]) + bytes(sec1[0:4])).decode(
            "ascii", errors="replace").rstrip("\x00 ")

        # File count is encoded as (number_of_entries * 8) in sec1[5].
        file_count = sec1[5] // 8
        entries = []

        for i in range(file_count):
            base0 = 8 + i * 8
            base1 = 8 + i * 8

            # Name (7 bytes, space-padded) and directory byte.
            # The top bit of the directory byte indicates locked status.
            raw_name = bytes(sec0[base0 : base0 + 7])
            dir_byte = sec0[base0 + 7]
            locked = bool(dir_byte & 0x80)
            directory = chr(dir_byte & 0x7F)
            name = raw_name.decode("ascii", errors="replace").rstrip()

            # Addresses and length are 18-bit values packed across two sectors.
            # The extra bits live in the high nibble of sec1[base1+6].
            load_lo = sec1[base1] | (sec1[base1 + 1] << 8)
            exec_lo = sec1[base1 + 2] | (sec1[base1 + 3] << 8)
            length_lo = sec1[base1 + 4] | (sec1[base1 + 5] << 8)
            extra = sec1[base1 + 6]
            start_sector = sec1[base1 + 7] | ((extra & 0x03) << 8)

            load_hi = (extra >> 2) & 0x03
            length_hi = (extra >> 4) & 0x03
            exec_hi = (extra >> 6) & 0x03

            load = load_lo | (load_hi << 16)
            exec_ = exec_lo | (exec_hi << 16)
            length = length_lo | (length_hi << 16)

            entries.append({
                "name": name,
                "dir": directory,
                "load": load,
                "exec": exec_,
                "length": length,
                "start_sector": start_sector,
                "locked": locked,
            })

        return title, entries

    def readFile(self, entry: Dict[str, Union[str, int, bool]]) -> bytes:
        """Read raw bytes for one catalogued DFS file.

        Args:
            entry: Catalogue entry dict as returned by readCatalogue().

        Returns:
            File bytes truncated to the recorded file length.
        """
        start = entry["start_sector"]
        length = entry["length"]
        sectors_needed = (length + SECTOR_SIZE - 1) // SECTOR_SIZE

        data = bytearray()
        for s in range(sectors_needed):
            data.extend(self._readSector(start + s))

        return bytes(data[:length])


def openDiscImage(path: str) -> List[DFSDisc]:
    """Open a disc image file and return DFS side readers for it.

    The format is inferred from the file extension:
    - .ssd  - single-sided, returns one DFSDisc
    - .dsd  - double-sided interleaved, returns two DFSDisc instances

    Args:
        path: Path to the .ssd or .dsd disc image file.

    Returns:
        List of DFSDisc instances, one per available side.
    """
    with open(path, "rb") as f:
        image = f.read()

    ext = path.lower()
    is_dsd = ext.endswith(".dsd")

    sides = [DFSDisc(image, 0, is_dsd)]

    if is_dsd and len(image) >= 20 * SECTOR_SIZE:
        sides.append(DFSDisc(image, 1, is_dsd))

    return sides


def isBasic(entry: Dict[str, Union[str, int, bool]]) -> bool:
    """Return True if a catalogue entry looks like a BBC BASIC program.

    Checks the execution address for the well-known BASIC entry points
    0x802B and 0x8023, which are written by the SAVE command.

    Args:
        entry: Catalogue entry dict as returned by readCatalogue().

    Returns:
        True if the entry is likely a BBC BASIC II program.
    """
    exec_lo = entry["exec"] & 0xFFFF
    return exec_lo in (0x801F, 0x8023, 0x802B)


def looksLikeText(data: bytes) -> bool:
    """Return True if the file bytes look like tokenized BASIC.

    Every valid tokenized BASIC program begins with 0x0D (the line start
    marker for the first line).

    Args:
        data: Raw file bytes.

    Returns:
        True when data starts with the BASIC line marker 0x0D.
    """
    return len(data) > 0 and data[0] == 0x0D


# Bytes that are acceptable in a plain-text file: printable ASCII plus
# common whitespace (tab, carriage return, line feed).
_PLAIN_TEXT_BYTES = frozenset(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}


def looksLikePlainText(data: bytes) -> bool:
    """Return True if the file bytes look like plain ASCII text.

    All bytes must be printable ASCII (0x20-0x7E) or common whitespace
    (tab 0x09, line feed 0x0A, carriage return 0x0D). An empty file is
    not considered plain text.

    Note: BBC Micro character set quirks (0x60 = pound sign, 0x7C = broken
    bar) are not translated - the raw bytes are accepted as-is and will
    appear as their standard ASCII equivalents (backtick, pipe) in the
    extracted .txt file.

    Args:
        data: Raw file bytes.

    Returns:
        True when every byte is a printable ASCII or whitespace character.
    """
    if not data:
        return False
    return all(b in _PLAIN_TEXT_BYTES for b in data)


def sortCatalogueEntries(entries: List[Dict[str, Union[str, int, bool]]], sortMode: str) -> List[Dict[str, Union[str, int, bool]]]:
    """Return catalogue entries in the requested output order.

    Args:
        entries: List of catalogue entry dicts.
        sortMode: One of 'name', 'catalog', or 'size'.
            name    - sort alphabetically by filename (ignores directory)
            catalog - preserve original on-disc DFS catalogue order
            size    - sort by file length ascending

    Returns:
        Ordered list of catalogue entry dicts.
    """
    if sortMode == "catalog":
        return entries

    if sortMode == "size":
        return sorted(
            entries,
            key=lambda e: (e["length"], e["name"].upper(), e["dir"].upper()),
        )

    # Default: sort by bare filename, case-insensitive.
    return sorted(entries, key=lambda e: (e["name"].upper(), e["dir"].upper()))
