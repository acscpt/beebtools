# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Generate synthetic disc images for the test suite.

Creates a set of disc images covering every file type that beebtools
classifies (BASIC, BASIC+MC, BASIC_ISH, BINARY, TEXT) across all four
supported formats (SSD, DSD, ADF, ADL).

Also creates non-standard DFS images that exhibit each failure category
documented in the field guide article:

    1. Dot in DFS filename
    2. Hash in DFS filename
    3. Overlapping sector allocations
    4. Non-printable control byte in filename
    5. ADFS filenames with host-filesystem-unsafe characters
    6. Degenerate all-space filename
    7. DEL (0x7F) as directory character
    8. Zero disc_size in catalogue header

Usage:
    python tests/scripts/create_test_images.py [--path DIR]

When --path is omitted the images are written to the script's own
directory (tests/scripts/).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from beebtools import (
    BootOption,
    DiscFile,
    createDiscImage,
    createAdfsImage,
    tokenize,
    ADFS_M_SECTORS,
    ADFS_L_SECTORS,
)

SECTOR_SIZE = 256
SECTORS_PER_TRACK = 10


# -----------------------------------------------------------------------
# Sample file content
# -----------------------------------------------------------------------

def _makeBasicProgram():
    """Tokenize a small BBC BASIC program."""
    lines = [
        "   10REM Test program",
        "   20PRINT \"HELLO WORLD\"",
        "   30END",
    ]
    return tokenize(lines)


def _makeBasicWithMachineCode():
    """Tokenize a BASIC loader with appended machine code.

    The machine code payload follows the BASIC end-of-program marker.
    This is the classic pattern for games that use BASIC as a loader
    for a machine code payload assembled at a fixed address.
    """
    lines = [
        "   10REM Loader",
        "   20*RUN MC",
    ]
    basic = tokenize(lines)
    mc_payload = bytes([0xA9, 0x41, 0x20, 0xEE, 0xFF, 0x60] * 4)
    return basic + mc_payload


def _makeBasicIsh():
    """Tokenize valid BASIC but pair it with a non-standard exec address.

    The content is real tokenized BASIC, but the exec address (0x1900)
    is not one of the standard BASIC entry points. This matches the
    real-world pattern of "include" files saved with *SAVE.
    """
    return _makeBasicProgram()


def _makeBinary():
    """Create a block of 6502 machine code."""
    code = bytes([
        0xA9, 0x00,       # LDA #0
        0x8D, 0x00, 0x7C, # STA &7C00
        0xA2, 0xFF,       # LDX #&FF
        0x9A,             # TXS
        0x20, 0xE7, 0xFF, # JSR OSCLI
        0x60,             # RTS
    ])
    return code * 8


def _makeText():
    """Create a plain text file."""
    return b"This is a plain text file.\r\nIt has two lines.\r\n"


# -----------------------------------------------------------------------
# Standard image builders
# -----------------------------------------------------------------------

def _addStandardFiles(image):
    """Add one file of each type to side 0 of a disc image."""
    side = image.sides[0]

    side.addFile(DiscFile(
        path="$.BASIC",
        data=_makeBasicProgram(),
        load_addr=0xFFFF0E00,
        exec_addr=0xFFFF8023,
    ))

    side.addFile(DiscFile(
        path="$.BASMCL",
        data=_makeBasicWithMachineCode(),
        load_addr=0xFFFF0E00,
        exec_addr=0xFFFF8023,
    ))

    side.addFile(DiscFile(
        path="$.BASISH",
        data=_makeBasicIsh(),
        load_addr=0xFFFF1900,
        exec_addr=0x00001900,
    ))

    side.addFile(DiscFile(
        path="$.BINARY",
        data=_makeBinary(),
        load_addr=0xFFFF3000,
        exec_addr=0xFFFF3000,
    ))

    side.addFile(DiscFile(
        path="$.TEXT",
        data=_makeText(),
        load_addr=0x00000000,
        exec_addr=0x00000000,
    ))


def buildStandardSsd():
    """Create a standard SSD with all file types."""
    image = createDiscImage(tracks=80, is_dsd=False, title="STANDARD")
    _addStandardFiles(image)
    return image


def buildStandardDsd():
    """Create a standard DSD with files on both sides."""
    image = createDiscImage(tracks=80, is_dsd=True, title="STANDARD")
    _addStandardFiles(image)

    side1 = image.sides[1]
    side1.addFile(DiscFile(
        path="$.SIDE1",
        data=_makeBasicProgram(),
        load_addr=0xFFFF0E00,
        exec_addr=0xFFFF8023,
    ))

    return image


def buildStandardAdf():
    """Create a standard ADF (ADFS-M, 80-track single-sided)."""
    image = createAdfsImage(
        total_sectors=ADFS_M_SECTORS,
        title="STANDARD",
        boot_option=BootOption.OFF,
    )
    _addStandardFiles(image)
    return image


def buildStandardAdl():
    """Create a standard ADL (ADFS-L, double-sided)."""
    image = createAdfsImage(
        total_sectors=ADFS_L_SECTORS,
        title="STANDARD",
        boot_option=BootOption.OFF,
    )
    _addStandardFiles(image)
    return image


# -----------------------------------------------------------------------
# Raw DFS image builder for non-standard images
# -----------------------------------------------------------------------

class RawDfsBuilder:
    """Build a DFS disc image by writing raw catalogue bytes.

    Bypasses all validation so we can create images with non-standard
    filenames, overlapping sectors, and other anomalies that the
    library API would reject.
    """

    def __init__(self, tracks=80):
        """Initialise a blank DFS image with the given track count."""
        self.tracks = tracks
        self.sectors = tracks * SECTORS_PER_TRACK
        self.data = bytearray(self.sectors * SECTOR_SIZE)
        self.entries = []

    def setTitle(self, title):
        """Write a disc title into the catalogue sectors."""
        raw = title[:12].encode("ascii").ljust(12, b"\x00")
        self.data[0:8] = raw[:8]
        self.data[SECTOR_SIZE + 0 : SECTOR_SIZE + 4] = raw[8:12]

    def setDiscSize(self, disc_size):
        """Set the disc_size field in the catalogue. Can be zero."""
        self.data[SECTOR_SIZE + 7] = disc_size & 0xFF
        descriptor = self.data[SECTOR_SIZE + 6]
        descriptor = (descriptor & 0xFC) | ((disc_size >> 8) & 0x03)
        self.data[SECTOR_SIZE + 6] = descriptor

    def addEntry(self, name, directory, load_addr, exec_addr, data,
                 start_sector=None, locked=False):
        """Add a file entry with raw bytes. Name can contain any byte values.

        If start_sector is None, the file is placed after the last entry.
        Data is written to the image at the specified start sector.
        """
        if start_sector is None:
            if not self.entries:
                start_sector = 2
            else:
                last = self.entries[-1]
                last_end = last["start_sector"] + _sectorsNeeded(last["length"])
                start_sector = last_end

        entry = {
            "name": name,
            "directory": directory,
            "load_addr": load_addr,
            "exec_addr": exec_addr,
            "length": len(data),
            "start_sector": start_sector,
            "locked": locked,
        }
        self.entries.append(entry)

        offset = start_sector * SECTOR_SIZE
        self.data[offset : offset + len(data)] = data

    def build(self):
        """Encode all entries into the catalogue and return the image bytes."""
        sec0 = bytearray(self.data[0 : SECTOR_SIZE])
        sec1 = bytearray(self.data[SECTOR_SIZE : 2 * SECTOR_SIZE])

        sec1[5] = len(self.entries) * 8

        if sec1[6] == 0 and sec1[7] == 0:
            self.setDiscSize(self.sectors)
            sec1[6] = self.data[SECTOR_SIZE + 6]
            sec1[7] = self.data[SECTOR_SIZE + 7]

        for i, entry in enumerate(self.entries):
            base = 8 + i * 8
            sec0_chunk, sec1_chunk = _encodeRawEntry(entry)
            sec0[base : base + 8] = sec0_chunk
            sec1[base : base + 8] = sec1_chunk

        self.data[0 : SECTOR_SIZE] = sec0
        self.data[SECTOR_SIZE : 2 * SECTOR_SIZE] = sec1

        return bytes(self.data)


def _sectorsNeeded(length):
    """Number of 256-byte sectors needed to hold length bytes."""
    return (length + SECTOR_SIZE - 1) // SECTOR_SIZE


def _encodeRawEntry(entry):
    """Encode a raw entry dict into (sec0_chunk, sec1_chunk) byte pairs.

    Accepts arbitrary byte values in name and directory fields.
    """
    name_raw = entry["name"]
    if isinstance(name_raw, str):
        name_bytes = name_raw.encode("ascii")
    else:
        name_bytes = bytes(name_raw)
    name_bytes = name_bytes[:7].ljust(7, b" ")

    dir_raw = entry["directory"]
    if isinstance(dir_raw, int):
        dir_byte = dir_raw & 0x7F
    else:
        dir_byte = ord(dir_raw) & 0x7F
    if entry.get("locked"):
        dir_byte |= 0x80

    sec0_chunk = name_bytes + bytes([dir_byte])

    load = entry["load_addr"]
    exec_ = entry["exec_addr"]
    length = entry["length"]
    start = entry["start_sector"]

    load_lo_0 = load & 0xFF
    load_lo_1 = (load >> 8) & 0xFF
    exec_lo_0 = exec_ & 0xFF
    exec_lo_1 = (exec_ >> 8) & 0xFF
    length_lo_0 = length & 0xFF
    length_lo_1 = (length >> 8) & 0xFF

    start_hi = (start >> 8) & 0x03
    load_hi = (load >> 16) & 0x03
    length_hi = (length >> 16) & 0x03
    exec_hi = (exec_ >> 16) & 0x03

    extra = start_hi | (load_hi << 2) | (length_hi << 4) | (exec_hi << 6)
    start_lo = start & 0xFF

    sec1_chunk = bytes([
        load_lo_0, load_lo_1,
        exec_lo_0, exec_lo_1,
        length_lo_0, length_lo_1,
        extra,
        start_lo,
    ])

    return sec0_chunk, sec1_chunk


# -----------------------------------------------------------------------
# Non-standard image builders (one per failure category)
# -----------------------------------------------------------------------

def buildDotInFilename():
    """Category 1: Filename contains a dot character.

    Real-world example: Level 9 adventures and Blue Ribbon compilations
    used dots in DFS filenames. The DFS spec forbids dots but the ROM
    accepts them.
    """
    builder = RawDfsBuilder()
    builder.setTitle("DOT.NAME")

    builder.addEntry(
        "FL.AT", "$", 0xFFFF0E00, 0xFFFF8023, _makeBasicProgram(),
    )
    builder.addEntry(
        "NORMAL", "$", 0xFFFF3000, 0xFFFF3000, _makeBinary(),
    )
    return builder.build()


def buildHashInFilename():
    """Category 2: Filename contains a hash character.

    Another spec-forbidden character that appears on real discs.
    """
    builder = RawDfsBuilder()
    builder.setTitle("HASH")

    builder.addEntry(
        "FILE#1", "$", 0xFFFF0E00, 0xFFFF8023, _makeBasicProgram(),
    )
    builder.addEntry(
        "NORMAL", "$", 0xFFFF3000, 0xFFFF3000, _makeBinary(),
    )
    return builder.build()


def buildOverlappingSectors():
    """Category 3: Two catalogue entries share the same start sector.

    Mimics the Level 9 copy-protection pattern where multiple entries
    point to the same physical sectors. The shared region contains
    identical data under both entries.
    """
    builder = RawDfsBuilder()
    builder.setTitle("OVERLAP")

    shared_data = _makeBinary()

    builder.addEntry(
        "FILE1", "$", 0xFFFF3000, 0xFFFF3000, shared_data,
        start_sector=2,
    )
    builder.addEntry(
        "FILE2", "$", 0xFFFF3000, 0xFFFF3000, shared_data[:len(shared_data) // 2],
        start_sector=2,
    )
    return builder.build()


def buildControlByteInFilename():
    """Category 4: Filename contains a non-printable control byte.

    The anti-tampering trick: a 0x06 byte at the end of the name is
    invisible on screen but prevents *DELETE or *RENAME from matching
    the entry because the ROM does a full seven-byte comparison.
    """
    builder = RawDfsBuilder()
    builder.setTitle("CTRL")

    name_with_ctrl = b"HIDDEN\x06"
    builder.addEntry(
        name_with_ctrl, "$", 0xFFFF0E00, 0xFFFF8023, _makeBasicProgram(),
    )
    builder.addEntry(
        "NORMAL", "$", 0xFFFF3000, 0xFFFF3000, _makeBinary(),
    )
    return builder.build()


def buildAllSpaceFilename():
    """Category 6: Filename is all spaces (degenerate entry).

    The DFS ROM strips trailing spaces from display but stores all
    seven bytes. An entry of seven spaces has no visible name. Used
    as copy protection or padding on some discs.
    """
    builder = RawDfsBuilder()
    builder.setTitle("SPACES")

    builder.addEntry(
        "       ", "$", 0xFFFF0E00, 0xFFFF8023, _makeBasicProgram(),
    )
    builder.addEntry(
        "NORMAL", "$", 0xFFFF3000, 0xFFFF3000, _makeBinary(),
    )
    return builder.build()


def buildDelDirectory():
    """Category 7: DEL (0x7F) used as the directory character.

    The DFS ROM masks the directory byte to 7 bits, so 0x7F is a valid
    directory character even though it is the DEL control code. This
    hides the file from normal catalogue listings that only show the
    current directory.
    """
    builder = RawDfsBuilder()
    builder.setTitle("DEL DIR")

    builder.addEntry(
        "HIDDEN", 0x7F, 0xFFFF0E00, 0xFFFF8023, _makeBasicProgram(),
    )
    builder.addEntry(
        "NORMAL", "$", 0xFFFF3000, 0xFFFF3000, _makeBinary(),
    )
    return builder.build()


def buildZeroDiscSize():
    """Category 8: disc_size field in the catalogue header is zero.

    The physical image is a full 80-track disc but the catalogue
    metadata says the disc has zero sectors. This is an authoring
    anomaly seen on one real-world disc image.
    """
    builder = RawDfsBuilder()
    builder.setTitle("ZERO SZ")

    builder.addEntry(
        "FILE", "$", 0xFFFF0E00, 0xFFFF8023, _makeBasicProgram(),
    )

    raw = builder.build()

    data = bytearray(raw)
    data[SECTOR_SIZE + 6] &= 0xFC
    data[SECTOR_SIZE + 7] = 0x00
    return bytes(data)


def buildAdfsSanitisedNames():
    """Category 5: ADFS filenames with characters illegal on host filesystems.

    ADFS allows any printable ASCII (0x21-0x7E) in filenames, but
    characters like > / \ : * ? " < | are illegal on Windows. When
    extracted, these are encoded as _xNN_ in the host path. The
    round-trip failure occurred when the rebuild used the sanitised
    filesystem name instead of the .inf sidecar name.

    Real-world examples from the 8bs collection: disc names like
    'T>D' and filenames like 'Arch-S/W'.
    """
    image = createAdfsImage(
        total_sectors=ADFS_M_SECTORS,
        title="SANITISE",
        boot_option=BootOption.OFF,
    )
    side = image.sides[0]

    side.addFile(DiscFile(
        path="$.T>D",
        data=_makeBasicProgram(),
        load_addr=0xFFFF0E00,
        exec_addr=0xFFFF8023,
    ))

    side.addFile(DiscFile(
        path="$.A<B",
        data=_makeBinary(),
        load_addr=0xFFFF3000,
        exec_addr=0xFFFF3000,
    ))

    side.addFile(DiscFile(
        path="$.C:D",
        data=_makeText(),
        load_addr=0x00000000,
        exec_addr=0x00000000,
    ))

    side.addFile(DiscFile(
        path="$.E*F",
        data=_makeBinary(),
        load_addr=0xFFFF3000,
        exec_addr=0xFFFF3000,
    ))

    side.addFile(DiscFile(
        path='$.G"H',
        data=_makeBasicProgram(),
        load_addr=0xFFFF0E00,
        exec_addr=0xFFFF8023,
    ))

    side.addFile(DiscFile(
        path="$.NORMAL",
        data=_makeBasicProgram(),
        load_addr=0xFFFF0E00,
        exec_addr=0xFFFF8023,
    ))

    return image


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------

def buildFragmentedSsd():
    """Create an SSD with gaps between files for compaction testing.

    Three files are added, then the middle one is deleted, leaving a
    gap in the sector map. A fourth small file is added at the end
    so there are gaps on both sides of the free space.
    """
    image = createDiscImage(tracks=80, is_dsd=False, title="FRAGMENT")
    side = image.sides[0]

    side.addFile(DiscFile(
        path="$.FIRST",
        data=_makeBinary(),
        load_addr=0xFFFF3000,
        exec_addr=0xFFFF3000,
    ))

    side.addFile(DiscFile(
        path="$.MIDDLE",
        data=bytes([0xAA]) * (SECTOR_SIZE * 4),
        load_addr=0xFFFF2000,
        exec_addr=0xFFFF2000,
    ))

    side.addFile(DiscFile(
        path="$.LAST",
        data=_makeBasicProgram(),
        load_addr=0xFFFF0E00,
        exec_addr=0xFFFF8023,
    ))

    side.deleteFile("$.MIDDLE")

    return image


STANDARD_IMAGES = {
    "standard.ssd": buildStandardSsd,
    "standard.dsd": buildStandardDsd,
    "standard.adf": buildStandardAdf,
    "standard.adl": buildStandardAdl,
    "fragmented.ssd": buildFragmentedSsd,
}

NONSTANDARD_IMAGES = {
    "cat1_dot_in_filename.ssd": buildDotInFilename,
    "cat2_hash_in_filename.ssd": buildHashInFilename,
    "cat3_overlapping_sectors.ssd": buildOverlappingSectors,
    "cat4_control_byte_filename.ssd": buildControlByteInFilename,
    "cat5_sanitised_names.adf": buildAdfsSanitisedNames,
    "cat6_all_space_filename.ssd": buildAllSpaceFilename,
    "cat7_del_directory.ssd": buildDelDirectory,
    "cat8_zero_disc_size.ssd": buildZeroDiscSize,
}


def generate(output_dir):
    """Generate all synthetic test images into output_dir."""
    os.makedirs(output_dir, exist_ok=True)

    print(f"Output directory: {output_dir}\n")

    for name, builder in STANDARD_IMAGES.items():
        path = os.path.join(output_dir, name)
        image = builder()
        if hasattr(image, "serialize"):
            data = image.serialize()
        else:
            data = image
        with open(path, "wb") as f:
            f.write(data)
        print(f"  {name:40s} {len(data):>8,d} bytes")

    print()

    for name, builder in NONSTANDARD_IMAGES.items():
        path = os.path.join(output_dir, name)
        result = builder()
        if hasattr(result, "serialize"):
            data = result.serialize()
        else:
            data = result
        with open(path, "wb") as f:
            f.write(data)
        print(f"  {name:40s} {len(data):>8,d} bytes")

    total = len(STANDARD_IMAGES) + len(NONSTANDARD_IMAGES)
    print(f"\n{total} images created.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic test disc images.")
    parser.add_argument(
        "--path",
        default=os.path.dirname(__file__),
        help="Directory to write the generated images into (default: script directory).",
    )
    args = parser.parse_args()
    generate(args.path)
