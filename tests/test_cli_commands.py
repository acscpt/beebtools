# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for the new CLI commands: create, add, delete, build.

Also tests extract --inf and the buildImage orchestration function.
"""

import io
import os
import contextlib
import sys
from argparse import Namespace

import pytest

from beebtools.dfs import createDiscImage, openDiscImage, DFSError
from beebtools.entry import DiscFile
from beebtools.disc import extractAll, buildImage
from beebtools.adfs import openAdfsImage
from beebtools.inf import formatInf, parseInf
from beebtools.cli import cmdCreate, cmdAdd, cmdDelete, cmdBuild


# =======================================================================
# cmdCreate
# =======================================================================

class TestCmdCreate:

    def testCreateSsd(self, tmp_path) -> None:
        """Create a blank 80-track SSD image."""
        out = str(tmp_path / "blank.ssd")
        args = Namespace(output=out, tracks=80, title="TESTDISC", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        assert os.path.isfile(out)
        assert os.path.getsize(out) == 80 * 10 * 256
        assert "80-track SSD" in buf.getvalue()

    def testCreateDsd(self, tmp_path) -> None:
        """Create a blank 80-track DSD image."""
        out = str(tmp_path / "blank.dsd")
        args = Namespace(output=out, tracks=80, title="DOUBLE", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        assert os.path.isfile(out)
        assert os.path.getsize(out) == 80 * 20 * 256

    def testCreate40Track(self, tmp_path) -> None:
        """Create a 40-track SSD image."""
        out = str(tmp_path / "small.ssd")
        args = Namespace(output=out, tracks=40, title="", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        assert os.path.getsize(out) == 40 * 10 * 256

    def testCreateWithBootOption(self, tmp_path) -> None:
        """Boot option is written to the catalogue."""
        out = str(tmp_path / "boot.ssd")
        args = Namespace(output=out, tracks=80, title="BOOTABLE", boot=3)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        image = openDiscImage(out)
        cat = image.sides[0].readCatalogue()
        assert cat.boot_option == 3

    def testCreateWithTitle(self, tmp_path) -> None:
        """Disc title is written to the catalogue."""
        out = str(tmp_path / "titled.ssd")
        args = Namespace(output=out, tracks=80, title="HELLO", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        image = openDiscImage(out)
        cat = image.sides[0].readCatalogue()
        assert cat.title == "HELLO"


# =======================================================================
# cmdAdd
# =======================================================================

class TestCmdAdd:

    def _createBlankSsd(self, tmp_path) -> str:
        """Create a blank 80-track SSD and return its path."""
        out = str(tmp_path / "disc.ssd")
        image = createDiscImage(tracks=80, title="TEST")
        with open(out, "wb") as f:
            f.write(image.serialize())
        return out

    def testAddFileWithExplicitName(self, tmp_path) -> None:
        """Add a file using --name with directory prefix."""
        img = self._createBlankSsd(tmp_path)
        data_file = str(tmp_path / "prog.bin")
        with open(data_file, "wb") as f:
            f.write(b"\x01\x02\x03\x04")

        args = Namespace(
            image=img, file=data_file, name="T.MYPROG",
            load="1900", exec_addr="8023", locked=False,
            inf=False, side=0, basic=False,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        # Verify the file was added.
        image = openDiscImage(img)
        cat = image.sides[0].readCatalogue()
        assert len(cat.entries) == 1
        assert cat.entries[0].fullName == "T.MYPROG"
        assert cat.entries[0].load_addr == 0x1900
        assert cat.entries[0].exec_addr == 0x8023

    def testAddFileBareName(self, tmp_path) -> None:
        """Bare name (no directory prefix) defaults to $."""
        img = self._createBlankSsd(tmp_path)
        data_file = str(tmp_path / "boot.bin")
        with open(data_file, "wb") as f:
            f.write(b"*RUN MYPROG\r")

        args = Namespace(
            image=img, file=data_file, name="BOOT",
            load=None, exec_addr=None, locked=False,
            inf=False, side=0, basic=False,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        image = openDiscImage(img)
        cat = image.sides[0].readCatalogue()
        assert cat.entries[0].fullName == "$.BOOT"

    def testAddFileWithInf(self, tmp_path) -> None:
        """Add a file using --inf to read metadata from sidecar."""
        img = self._createBlankSsd(tmp_path)
        data_file = str(tmp_path / "loader.bin")
        inf_file = data_file + ".inf"

        with open(data_file, "wb") as f:
            f.write(b"\xFF" * 512)

        with open(inf_file, "w") as f:
            f.write("$.LOADER  001900 001900 000200 L\n")

        args = Namespace(
            image=img, file=data_file, name=None,
            load=None, exec_addr=None, locked=False,
            inf=True, side=0, basic=False,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        image = openDiscImage(img)
        cat = image.sides[0].readCatalogue()
        entry = cat.entries[0]
        assert entry.fullName == "$.LOADER"
        assert entry.load_addr == 0x1900
        assert entry.exec_addr == 0x1900
        assert entry.locked is True

    def testAddLockedFile(self, tmp_path) -> None:
        """The --locked flag sets the lock bit."""
        img = self._createBlankSsd(tmp_path)
        data_file = str(tmp_path / "secret.bin")
        with open(data_file, "wb") as f:
            f.write(b"\xAA" * 10)

        args = Namespace(
            image=img, file=data_file, name="$.SECRET",
            load="0", exec_addr="0", locked=True,
            inf=False, side=0, basic=False,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        image = openDiscImage(img)
        entry = image.sides[0].readCatalogue().entries[0]
        assert entry.locked is True

    def testAddMultipleFiles(self, tmp_path) -> None:
        """Add two files sequentially to the same image."""
        img = self._createBlankSsd(tmp_path)

        for name, content in [("$.FILE1", b"aaa"), ("$.FILE2", b"bbb")]:
            data_file = str(tmp_path / f"{name.replace('.', '_')}.bin")
            with open(data_file, "wb") as f:
                f.write(content)

            args = Namespace(
                image=img, file=data_file, name=name,
                load=None, exec_addr=None, locked=False,
                inf=False, side=0, basic=False,
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmdAdd(args)

        image = openDiscImage(img)
        cat = image.sides[0].readCatalogue()
        assert len(cat.entries) == 2

    def testAddBasicFlag(self, tmp_path) -> None:
        """The --basic flag sets load=0x1900 and exec=0x8023."""
        img = self._createBlankSsd(tmp_path)
        data_file = str(tmp_path / "prog.bas")
        with open(data_file, "wb") as f:
            f.write(b"\x0D\x00\x0A\x05\xF1\x0D\xFF")

        args = Namespace(
            image=img, file=data_file, name="T.MYPROG",
            load=None, exec_addr=None, locked=False,
            inf=False, side=0, basic=True,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        image = openDiscImage(img)
        entry = image.sides[0].readCatalogue().entries[0]
        assert entry.load_addr == 0x1900
        assert entry.exec_addr == 0x8023

    def testAddBasicFlagWithLoadOverride(self, tmp_path) -> None:
        """Explicit --load overrides the BASIC default."""
        img = self._createBlankSsd(tmp_path)
        data_file = str(tmp_path / "prog.bas")
        with open(data_file, "wb") as f:
            f.write(b"\x0D\x00\x0A\x05\xF1\x0D\xFF")

        args = Namespace(
            image=img, file=data_file, name="T.MYPROG",
            load="E00", exec_addr=None, locked=False,
            inf=False, side=0, basic=True,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        image = openDiscImage(img)
        entry = image.sides[0].readCatalogue().entries[0]
        assert entry.load_addr == 0x0E00
        assert entry.exec_addr == 0x8023

    def testAddBasicFlagWithExecOverride(self, tmp_path) -> None:
        """Explicit --exec overrides the BASIC default."""
        img = self._createBlankSsd(tmp_path)
        data_file = str(tmp_path / "prog.bas")
        with open(data_file, "wb") as f:
            f.write(b"\x0D\x00\x0A\x05\xF1\x0D\xFF")

        args = Namespace(
            image=img, file=data_file, name="T.MYPROG",
            load=None, exec_addr="802B", locked=False,
            inf=False, side=0, basic=True,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        image = openDiscImage(img)
        entry = image.sides[0].readCatalogue().entries[0]
        assert entry.load_addr == 0x1900
        assert entry.exec_addr == 0x802B

    def testAddBasicIgnoredWithInf(self, tmp_path) -> None:
        """Using --basic with --inf prints a warning and uses .inf metadata."""
        img = self._createBlankSsd(tmp_path)
        data_file = str(tmp_path / "loader.bin")
        inf_file = data_file + ".inf"

        with open(data_file, "wb") as f:
            f.write(b"\xFF" * 64)

        with open(inf_file, "w") as f:
            f.write("$.LOADER  003000 004000 000040\n")

        args = Namespace(
            image=img, file=data_file, name=None,
            load=None, exec_addr=None, locked=False,
            inf=True, side=0, basic=True,
        )

        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cmdAdd(args)

        assert "ignored" in err.getvalue().lower()

        # .inf metadata should be used, not BASIC defaults.
        image = openDiscImage(img)
        entry = image.sides[0].readCatalogue().entries[0]
        assert entry.load_addr == 0x3000
        assert entry.exec_addr == 0x4000

    def testAddBasicNoteWithBothAddresses(self, tmp_path) -> None:
        """Using --basic with both --load and --exec prints override notes."""
        img = self._createBlankSsd(tmp_path)
        data_file = str(tmp_path / "prog.bas")
        with open(data_file, "wb") as f:
            f.write(b"\x0D\x00\x0A\x05\xF1\x0D\xFF")

        args = Namespace(
            image=img, file=data_file, name="T.MYPROG",
            load="E00", exec_addr="802B", locked=False,
            inf=False, side=0, basic=True,
        )

        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cmdAdd(args)

        stderr = err.getvalue()
        assert "--load overrides" in stderr
        assert "--exec overrides" in stderr

        # Explicit addresses should be used.
        image = openDiscImage(img)
        entry = image.sides[0].readCatalogue().entries[0]
        assert entry.load_addr == 0x0E00
        assert entry.exec_addr == 0x802B


# =======================================================================
# cmdDelete
# =======================================================================

class TestCmdDelete:

    def _createSsdWithFile(self, tmp_path) -> str:
        """Create a SSD with one file and return the image path."""
        image = createDiscImage(tracks=80, title="DELTEST")
        side = image.sides[0]
        side.addFile(DiscFile("$.VICTIM", b"\x01" * 100, load_addr=0x1900))
        out = str(tmp_path / "disc.ssd")
        with open(out, "wb") as f:
            f.write(image.serialize())
        return out

    def testDeleteWithPrefix(self, tmp_path) -> None:
        """Delete a file specified as D.NAME."""
        img = self._createSsdWithFile(tmp_path)
        args = Namespace(image=img, filename="$.VICTIM", side=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdDelete(args)

        image = openDiscImage(img)
        cat = image.sides[0].readCatalogue()
        assert len(cat.entries) == 0
        assert "Deleted $.VICTIM" in buf.getvalue()

    def testDeleteBareName(self, tmp_path) -> None:
        """Bare name (no prefix) defaults to $."""
        img = self._createSsdWithFile(tmp_path)
        args = Namespace(image=img, filename="VICTIM", side=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdDelete(args)

        image = openDiscImage(img)
        cat = image.sides[0].readCatalogue()
        assert len(cat.entries) == 0

    def testDeleteNonexistent(self, tmp_path) -> None:
        """Deleting a file that does not exist exits with error."""
        img = self._createSsdWithFile(tmp_path)
        args = Namespace(image=img, filename="$.GHOST", side=0)

        with pytest.raises(SystemExit):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                cmdDelete(args)


# =======================================================================
# extract --inf
# =======================================================================

class TestExtractInf:

    def testInfSidecarsWritten(self, tmp_path) -> None:
        """extract --inf writes .inf sidecars alongside data files."""
        # Build a small image with one file.
        image = createDiscImage(tracks=80, title="INFTEST")
        side = image.sides[0]
        side.addFile(DiscFile("$.LOADER", b"\xFF" * 256, load_addr=0x1900,
                     exec_addr=0x1900, locked=True))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, write_inf=True)

        # Check that the .inf sidecar exists.
        inf_path = os.path.join(out_dir, "$", "LOADER.bin.inf")
        assert os.path.isfile(inf_path)

        # Parse the .inf and verify its content.
        with open(inf_path, "r") as f:
            inf = parseInf(f.readline().strip())

        assert inf.directory == "$"
        assert inf.name == "LOADER"
        assert inf.load_addr == 0x1900
        assert inf.exec_addr == 0x1900
        assert inf.locked is True

    def testNoInfWithoutFlag(self, tmp_path) -> None:
        """Without write_inf, no .inf sidecars are written."""
        image = createDiscImage(tracks=80)
        side = image.sides[0]
        side.addFile(DiscFile("$.DATA", b"\x00" * 10))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, write_inf=False)

        # No .inf file should exist.
        for root, dirs, files in os.walk(out_dir):
            for fname in files:
                assert not fname.endswith(".inf"), f"Unexpected .inf: {fname}"


# =======================================================================
# buildImage
# =======================================================================

class TestBuildImage:

    def testBuildFromExtractedFiles(self, tmp_path) -> None:
        """Extract with --inf, then build a new image from the result."""
        # Create an image with two files.
        original = createDiscImage(tracks=80, title="ROUNDTRP")
        side = original.sides[0]
        side.addFile(DiscFile("T.PROG", b"\x0D\x00\x0A\x05\x20\x0D\xFF", load_addr=0x0E00, exec_addr=0x8023))
        side.addFile(DiscFile("$.DATA", b"Hello\r", load_addr=0))

        img_path = str(tmp_path / "original.ssd")
        with open(img_path, "wb") as f:
            f.write(original.serialize())

        # Extract with .inf sidecars.
        extract_dir = str(tmp_path / "extracted")
        extractAll(img_path, extract_dir, write_inf=True)

        # Build a new image from the extracted files.
        rebuilt_bytes = buildImage(
            source_dir=extract_dir,
            output_path="rebuilt.ssd",
            tracks=80,
            title="REBUILT",
        )

        # Write and reopen to verify.
        rebuilt_path = str(tmp_path / "rebuilt.ssd")
        with open(rebuilt_path, "wb") as f:
            f.write(rebuilt_bytes)

        rebuilt = openDiscImage(rebuilt_path)
        cat = rebuilt.sides[0].readCatalogue()

        # Both files should be present.
        names = {e.fullName for e in cat.entries}
        assert "T.PROG" in names
        assert "$.DATA" in names

        # Verify addresses survived the round-trip.
        for entry in cat.entries:
            if entry.fullName == "T.PROG":
                assert entry.load_addr == 0x0E00
                assert entry.exec_addr == 0x8023

    def testBuildDsd(self, tmp_path) -> None:
        """Build a DSD image from side0/side1 directory layout."""
        # Create source directories manually with .inf sidecars.
        src = str(tmp_path / "src")
        side0_dir = os.path.join(src, "side0", "$")
        side1_dir = os.path.join(src, "side1", "T")
        os.makedirs(side0_dir)
        os.makedirs(side1_dir)

        # Side 0: $.BOOT
        with open(os.path.join(side0_dir, "BOOT.bin"), "wb") as f:
            f.write(b"*RUN GAME\r")
        with open(os.path.join(side0_dir, "BOOT.bin.inf"), "w") as f:
            f.write(formatInf("$", "BOOT", 0, 0, 10) + "\n")

        # Side 1: T.GAME
        with open(os.path.join(side1_dir, "GAME.bin"), "wb") as f:
            f.write(b"\xAA" * 100)
        with open(os.path.join(side1_dir, "GAME.bin.inf"), "w") as f:
            f.write(formatInf("T", "GAME", 0x1900, 0x1900, 100) + "\n")

        rebuilt_bytes = buildImage(src, "rebuilt.dsd", tracks=80, title="DOUBLE")

        rebuilt_path = str(tmp_path / "rebuilt.dsd")
        with open(rebuilt_path, "wb") as f:
            f.write(rebuilt_bytes)

        rebuilt = openDiscImage(rebuilt_path)

        cat0 = rebuilt.sides[0].readCatalogue()
        assert any(e.fullName == "$.BOOT" for e in cat0.entries)

        cat1 = rebuilt.sides[1].readCatalogue()
        assert any(e.fullName == "T.GAME" for e in cat1.entries)

    def testBuildSkipsFilesWithoutInf(self, tmp_path) -> None:
        """Files without .inf sidecars are skipped (with warning)."""
        src = str(tmp_path / "src")
        d = os.path.join(src, "$")
        os.makedirs(d)

        # Data file with no .inf sidecar.
        with open(os.path.join(d, "ORPHAN.bin"), "wb") as f:
            f.write(b"\x00" * 10)

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rebuilt_bytes = buildImage(src, "empty.ssd", tracks=80)

        # Should warn about missing .inf.
        assert "Warning" in err.getvalue()
        assert "ORPHAN" in err.getvalue()

        # Image should have no files.
        rebuilt_path = str(tmp_path / "empty.ssd")
        with open(rebuilt_path, "wb") as f:
            f.write(rebuilt_bytes)

        rebuilt = openDiscImage(rebuilt_path)
        cat = rebuilt.sides[0].readCatalogue()
        assert len(cat.entries) == 0

    def testBuildRetokenizesBasicFiles(self, tmp_path) -> None:
        """BASIC .bas files are retokenized to binary on build.

        The extract step detokenizes BASIC programs into plain text,
        which is larger than the tokenized binary. When building a new
        image from those files, the build step must retokenize .bas
        files so they fit on disc and are valid BBC BASIC programs.
        """
        # Build a tokenized BASIC program:
        #   10 PRINT "HELLO"
        # Token 0xF1 = PRINT, 0x22 = quote.
        basic_bytes = bytes([
            0x0D,                       # line start marker
            0x00, 0x0A,                 # line number 10 (hi, lo)
            0x0E,                       # length = 14 bytes (Russell format)
            0x20,                       # space after line number
            0xF1,                       # PRINT token
            0x20,                       # space
            0x22,                       # open quote
            0x48, 0x45, 0x4C, 0x4C, 0x4F,  # HELLO
            0x22,                       # close quote
            0x0D, 0xFF,                 # end-of-program marker
        ])

        # Create a disc image with this BASIC file.
        original = createDiscImage(tracks=80, title="RETOKEN")
        side = original.sides[0]
        side.addFile(DiscFile("$.HELLO", basic_bytes, load_addr=0x0E00, exec_addr=0x8023))

        img_path = str(tmp_path / "original.ssd")
        with open(img_path, "wb") as f:
            f.write(original.serialize())

        # Extract with .inf sidecars (BASIC is detokenized to .bas text).
        extract_dir = str(tmp_path / "extracted")
        extractAll(img_path, extract_dir, write_inf=True)

        # Verify the extracted file is plain text (.bas), not binary.
        bas_path = os.path.join(extract_dir, "$", "HELLO.bas")
        assert os.path.isfile(bas_path)
        with open(bas_path, "r") as f:
            text = f.read()
        assert "PRINT" in text
        assert "HELLO" in text

        # Rebuild a new image from the extracted directory.
        rebuilt_bytes = buildImage(
            source_dir=extract_dir,
            output_path="rebuilt.ssd",
            tracks=80,
            title="REBUILT",
        )

        rebuilt_path = str(tmp_path / "rebuilt.ssd")
        with open(rebuilt_path, "wb") as f:
            f.write(rebuilt_bytes)

        # Reopen and verify the file is present and is valid tokenized BASIC.
        rebuilt = openDiscImage(rebuilt_path)
        cat = rebuilt.sides[0].readCatalogue()
        entry = next(e for e in cat.entries if e.fullName == "$.HELLO")

        # Addresses should survive the round-trip.
        assert entry.load_addr == 0x0E00
        assert entry.exec_addr == 0x8023

        # The rebuilt data should be tokenized binary, not plain text.
        rebuilt_data = rebuilt.sides[0].readFile(entry)
        assert rebuilt_data[0] == 0x0D, "Should start with BASIC line marker"
        assert rebuilt_data[-2:] == b"\x0D\xFF", "Should end with end-of-program"

        # The tokenized binary should be the same size as the original
        # (or very close - the tokenizer produces identical output for
        # the same program).
        assert len(rebuilt_data) == len(basic_bytes)

    def testBuildRetokenizePreservesNonBasicFiles(self, tmp_path) -> None:
        """Binary files with .bas-like names but non-BASIC exec addresses
        are NOT retokenized - they pass through as raw bytes."""
        src = str(tmp_path / "src")
        d = os.path.join(src, "$")
        os.makedirs(d)

        # A binary file that happens to have a .bas extension but a
        # non-BASIC exec address (0x1900 is not a BASIC entry point).
        raw_data = b"\xAA\xBB\xCC\xDD"
        with open(os.path.join(d, "NOTBAS.bas"), "wb") as f:
            f.write(raw_data)
        with open(os.path.join(d, "NOTBAS.bas.inf"), "w") as f:
            f.write(formatInf("$", "NOTBAS", 0x1900, 0x1900, len(raw_data)) + "\n")

        rebuilt_bytes = buildImage(src, "test.ssd", tracks=80)

        rebuilt_path = str(tmp_path / "test.ssd")
        with open(rebuilt_path, "wb") as f:
            f.write(rebuilt_bytes)

        rebuilt = openDiscImage(rebuilt_path)
        entry = next(e for e in rebuilt.sides[0].readCatalogue().entries
                      if e.fullName == "$.NOTBAS")

        # Data should be the raw bytes, untouched.
        data = rebuilt.sides[0].readFile(entry)
        assert data == raw_data


# =======================================================================
# cmdBuild
# =======================================================================

class TestCmdBuild:

    def testBuildCommandCreatesImage(self, tmp_path) -> None:
        """The build CLI command produces a valid image file."""
        # Set up source directory with one file.
        src = str(tmp_path / "src")
        d = os.path.join(src, "$")
        os.makedirs(d)

        with open(os.path.join(d, "BOOT.txt"), "wb") as f:
            f.write(b"*RUN GAME\r")
        with open(os.path.join(d, "BOOT.txt.inf"), "w") as f:
            f.write(formatInf("$", "BOOT", 0, 0, 10) + "\n")

        out = str(tmp_path / "result.ssd")
        args = Namespace(dir=src, output=out, tracks=80, title="CLI", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdBuild(args)

        assert os.path.isfile(out)
        assert "Built 80-track SSD" in buf.getvalue()

        image = openDiscImage(out)
        cat = image.sides[0].readCatalogue()
        assert any(e.fullName == "$.BOOT" for e in cat.entries)


# =======================================================================
# ADFS CLI commands
# =======================================================================

from beebtools.adfs import (
    openAdfsImage,
    createAdfsImage,
    ADFSError,
    ADFS_S_SECTORS,
    ADFS_M_SECTORS,
    ADFS_L_SECTORS,
)



class TestCmdCreateAdfs:

    def testCreateAdf80Track(self, tmp_path) -> None:
        """Create a blank 80-track ADF image (320K)."""
        out = str(tmp_path / "blank.adf")
        args = Namespace(output=out, tracks=80, title="TESTADFS", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        assert os.path.isfile(out)
        assert os.path.getsize(out) == ADFS_M_SECTORS * 256
        assert "320K ADF" in buf.getvalue()

    def testCreateAdf40Track(self, tmp_path) -> None:
        """Create a blank 40-track ADF image (160K)."""
        out = str(tmp_path / "small.adf")
        args = Namespace(output=out, tracks=40, title="", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        assert os.path.getsize(out) == ADFS_S_SECTORS * 256
        assert "160K ADF" in buf.getvalue()

    def testCreateAdl(self, tmp_path) -> None:
        """Create a blank ADL image (640K)."""
        out = str(tmp_path / "big.adl")
        args = Namespace(output=out, tracks=80, title="BIGDISC", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        assert os.path.getsize(out) == ADFS_L_SECTORS * 256
        assert "640K ADL" in buf.getvalue()

    def testCreateAdfWithTitle(self, tmp_path) -> None:
        """Disc title is written to the root directory."""
        out = str(tmp_path / "titled.adf")
        args = Namespace(output=out, tracks=80, title="MYADFS", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        image = openAdfsImage(out)
        cat = image.sides[0].readCatalogue()
        assert cat.title == "MYADFS"

    def testCreateAdfWithBootOption(self, tmp_path) -> None:
        """Boot option is written to the free space map."""
        out = str(tmp_path / "boot.adf")
        args = Namespace(output=out, tracks=80, title="", boot=3)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        image = openAdfsImage(out)
        cat = image.sides[0].readCatalogue()
        assert cat.boot_option == 3


class TestCmdAddAdfs:

    def _createBlankAdf(self, tmp_path) -> str:
        """Create a blank 80-track ADF and return its path."""
        out = str(tmp_path / "disc.adf")
        args = Namespace(output=out, tracks=80, title="", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        return out

    def testAddFileByName(self, tmp_path) -> None:
        """Add a file using --name to an ADFS image."""
        img = self._createBlankAdf(tmp_path)

        # Write a data file to add.
        data_path = str(tmp_path / "mydata.bin")
        with open(data_path, "wb") as f:
            f.write(b"\x00" * 100)

        args = Namespace(
            image=img, file=data_path, name="$.MYFILE",
            load="1000", exec_addr="2000", basic=False,
            locked=False, inf=False, side=0,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        assert "Added $.MYFILE" in buf.getvalue()

        # Verify the file appears in the catalogue.
        image = openAdfsImage(img)
        cat = image.sides[0].readCatalogue()
        names = [e.fullName for e in cat.entries]
        assert "$.MYFILE" in names

    def testAddFileByNameNoDollarPrefix(self, tmp_path) -> None:
        """A bare name without $. gets the root prefix added."""
        img = self._createBlankAdf(tmp_path)

        data_path = str(tmp_path / "bare.bin")
        with open(data_path, "wb") as f:
            f.write(b"\xFF" * 50)

        args = Namespace(
            image=img, file=data_path, name="BARE",
            load=None, exec_addr=None, basic=False,
            locked=False, inf=False, side=0,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        assert "Added $.BARE" in buf.getvalue()

    def testAddFileWithInf(self, tmp_path) -> None:
        """Add a file using --inf to read metadata from a sidecar."""
        img = self._createBlankAdf(tmp_path)

        data_path = str(tmp_path / "PROG.bin")
        with open(data_path, "wb") as f:
            f.write(b"\xAB" * 200)

        # Write the .inf sidecar.
        inf_path = data_path + ".inf"
        with open(inf_path, "w") as f:
            f.write(formatInf("$", "PROG", 0x1900, 0x8023, 200) + "\n")

        args = Namespace(
            image=img, file=data_path, name=None,
            load=None, exec_addr=None, basic=False,
            locked=False, inf=True, side=0,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        assert "Added $.PROG" in buf.getvalue()

        # Verify metadata is correct.
        image = openAdfsImage(img)
        cat = image.sides[0].readCatalogue()
        entry = [e for e in cat.entries if e.name == "PROG"][0]
        assert entry.load_addr == 0x1900
        assert entry.exec_addr == 0x8023

    def testAddReadBack(self, tmp_path) -> None:
        """Data written by add can be read back identically."""
        img = self._createBlankAdf(tmp_path)

        payload = bytes(range(256)) * 4
        data_path = str(tmp_path / "data.bin")
        with open(data_path, "wb") as f:
            f.write(payload)

        args = Namespace(
            image=img, file=data_path, name="$.DATA",
            load="0", exec_addr="0", basic=False,
            locked=False, inf=False, side=0,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        # Read back the file and verify contents.
        image = openAdfsImage(img)
        side = image.sides[0]
        cat = side.readCatalogue()
        entry = [e for e in cat.entries if e.name == "DATA"][0]
        read_back = side.readFile(entry)
        assert read_back == payload

    def testAddLockedFile(self, tmp_path) -> None:
        """Adding a file with --locked sets the lock bit."""
        img = self._createBlankAdf(tmp_path)

        data_path = str(tmp_path / "locked.bin")
        with open(data_path, "wb") as f:
            f.write(b"\x00" * 10)

        args = Namespace(
            image=img, file=data_path, name="$.LOCKED",
            load=None, exec_addr=None, basic=False,
            locked=True, inf=False, side=0,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        image = openAdfsImage(img)
        cat = image.sides[0].readCatalogue()
        entry = [e for e in cat.entries if e.name == "LOCKED"][0]
        assert entry.locked


class TestCmdDeleteAdfs:

    def _createImageWithFile(self, tmp_path) -> str:
        """Create an ADFS image with one file and return its path."""
        out = str(tmp_path / "disc.adf")
        args = Namespace(output=out, tracks=80, title="", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        data_path = str(tmp_path / "victim.bin")
        with open(data_path, "wb") as f:
            f.write(b"\x00" * 100)

        args = Namespace(
            image=out, file=data_path, name="$.VICTIM",
            load="0", exec_addr="0", basic=False,
            locked=False, inf=False, side=0,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        return out

    def testDeleteFile(self, tmp_path) -> None:
        """Delete a file from an ADFS image."""
        img = self._createImageWithFile(tmp_path)

        args = Namespace(image=img, filename="$.VICTIM", side=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdDelete(args)

        assert "Deleted $.VICTIM" in buf.getvalue()

        # Verify the file is gone.
        image = openAdfsImage(img)
        cat = image.sides[0].readCatalogue()
        names = [e.fullName for e in cat.entries]
        assert "$.VICTIM" not in names

    def testDeleteBareNameGetsDollarPrefix(self, tmp_path) -> None:
        """A bare name without $. gets the root prefix added."""
        img = self._createImageWithFile(tmp_path)

        args = Namespace(image=img, filename="VICTIM", side=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdDelete(args)

        assert "Deleted $.VICTIM" in buf.getvalue()

    def testDeleteNonExistentFileExits(self, tmp_path) -> None:
        """Deleting a non-existent file prints an error and exits."""
        img = self._createImageWithFile(tmp_path)

        args = Namespace(image=img, filename="$.NOPE", side=0)

        with pytest.raises(SystemExit):
            cmdDelete(args)


class TestBuildAdfsImage:

    def testBuildRoundTrip(self, tmp_path) -> None:
        """Build an ADFS image from a directory tree and verify the catalogue."""
        src = str(tmp_path / "src")
        d = os.path.join(src, "$")
        os.makedirs(d)

        # Create a file with .inf sidecar.
        with open(os.path.join(d, "HELLO.txt"), "wb") as f:
            f.write(b"Hello BBC\r")
        with open(os.path.join(d, "HELLO.txt.inf"), "w") as f:
            f.write(formatInf("$", "HELLO", 0, 0, 10) + "\n")

        image_bytes = buildImage(source_dir=src, output_path="built.adf", title="ROUNDTRIP")

        # Write and read back.
        out = str(tmp_path / "built.adf")
        with open(out, "wb") as f:
            f.write(image_bytes)

        image = openAdfsImage(out)
        cat = image.sides[0].readCatalogue()
        names = [e.fullName for e in cat.entries]
        assert "$.HELLO" in names

    def testBuildWithSubdirectory(self, tmp_path) -> None:
        """Build an ADFS image with a subdirectory creates the directory."""
        src = str(tmp_path / "src")
        subdir = os.path.join(src, "$", "GAMES")
        os.makedirs(subdir)

        with open(os.path.join(subdir, "ELITE.bin"), "wb") as f:
            f.write(b"\xFF" * 300)
        with open(os.path.join(subdir, "ELITE.bin.inf"), "w") as f:
            f.write(formatInf("$", "GAMES.ELITE", 0x1000, 0x2000, 300) + "\n")

        image_bytes = buildImage(source_dir=src, output_path="games.adf", title="GAMES")

        out = str(tmp_path / "games.adf")
        with open(out, "wb") as f:
            f.write(image_bytes)

        image = openAdfsImage(out)
        cat = image.sides[0].readCatalogue()

        # The GAMES directory entry should exist.
        dir_names = [e.name for e in cat.entries if e.isDirectory]
        assert "GAMES" in dir_names

    def testBuildEmptyDir(self, tmp_path) -> None:
        """Building from an empty $ directory produces a valid image."""
        src = str(tmp_path / "src")
        os.makedirs(os.path.join(src, "$"))

        image_bytes = buildImage(source_dir=src, output_path="empty.adf")

        out = str(tmp_path / "empty.adf")
        with open(out, "wb") as f:
            f.write(image_bytes)

        # Should be openable and have an empty catalogue.
        image = openAdfsImage(out)
        cat = image.sides[0].readCatalogue()
        assert len(cat.entries) == 0

    def testBuildSkipsFilesWithoutInf(self, tmp_path) -> None:
        """Files without .inf sidecars are skipped with a warning."""
        src = str(tmp_path / "src")
        d = os.path.join(src, "$")
        os.makedirs(d)

        # File without .inf sidecar.
        with open(os.path.join(d, "ORPHAN.bin"), "wb") as f:
            f.write(b"\x00" * 50)

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            image_bytes = buildImage(source_dir=src, output_path="skip.adf")

        assert "Warning" in buf.getvalue()
        assert "skipping" in buf.getvalue()

        # Image should still be valid with no files.
        out = str(tmp_path / "skip.adf")
        with open(out, "wb") as f:
            f.write(image_bytes)

        image = openAdfsImage(out)
        cat = image.sides[0].readCatalogue()
        assert len(cat.entries) == 0


class TestCmdBuildAdfs:

    def testBuildCommandCreatesAdf(self, tmp_path) -> None:
        """The build CLI command produces a valid ADFS image."""
        src = str(tmp_path / "src")
        d = os.path.join(src, "$")
        os.makedirs(d)

        with open(os.path.join(d, "BOOT.txt"), "wb") as f:
            f.write(b"*RUN GAME\r")
        with open(os.path.join(d, "BOOT.txt.inf"), "w") as f:
            f.write(formatInf("$", "BOOT", 0, 0, 10) + "\n")

        out = str(tmp_path / "result.adf")
        args = Namespace(dir=src, output=out, tracks=80, title="CLI", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdBuild(args)

        assert os.path.isfile(out)
        assert "320K ADF" in buf.getvalue()

        image = openAdfsImage(out)
        cat = image.sides[0].readCatalogue()
        assert any(e.fullName == "$.BOOT" for e in cat.entries)

    def testBuildCommandCreatesAdl(self, tmp_path) -> None:
        """The build CLI command produces a valid ADL image."""
        src = str(tmp_path / "src")
        d = os.path.join(src, "$")
        os.makedirs(d)

        with open(os.path.join(d, "DATA.bin"), "wb") as f:
            f.write(b"\xAA" * 50)
        with open(os.path.join(d, "DATA.bin.inf"), "w") as f:
            f.write(formatInf("$", "DATA", 0, 0, 50) + "\n")

        out = str(tmp_path / "result.adl")
        args = Namespace(dir=src, output=out, tracks=80, title="BIG", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdBuild(args)

        assert os.path.isfile(out)
        assert "640K ADL" in buf.getvalue()
        assert os.path.getsize(out) == ADFS_L_SECTORS * 256


# =======================================================================
# ADFS extract-then-rebuild round-trip (11.9)
# =======================================================================

class TestAdfsExtractRebuildRoundTrip:

    def testExtractAndRebuildPreservesCatalogue(self, tmp_path) -> None:
        """Extract an ADFS image, rebuild it, and verify the catalogue matches."""
        # Create an ADFS image with multiple binary files.
        # Use non-text data to avoid CR/LF normalization during extract.
        image = createAdfsImage(title="ROUNDTRIP")
        side = image.sides[0]

        data_a = bytes(range(256)) * 2
        data_b = bytes(range(255, -1, -1)) * 3

        side.addFile(DiscFile("$.FILEA", data_a,
                     load_addr=0x1000, exec_addr=0x2000))
        side.addFile(DiscFile("$.FILEB", data_b,
                     load_addr=0x3000, exec_addr=0x4000))

        original_path = str(tmp_path / "original.adf")
        with open(original_path, "wb") as f:
            f.write(image.serialize())

        # Extract all files with .inf sidecars.
        extract_dir = str(tmp_path / "extracted")
        extractAll(original_path, extract_dir, write_inf=True)

        # Rebuild from extracted files.
        rebuilt_bytes = buildImage(
            source_dir=extract_dir, output_path="rebuilt.adf", title="ROUNDTRIP")

        rebuilt_path = str(tmp_path / "rebuilt.adf")
        with open(rebuilt_path, "wb") as f:
            f.write(rebuilt_bytes)

        # Read both catalogues and compare.
        orig_image = openAdfsImage(original_path)
        orig_cat = orig_image.sides[0].readCatalogue()
        orig_names = sorted(e.fullName for e in orig_cat.entries)

        rebuilt_image = openAdfsImage(rebuilt_path)
        rebuilt_cat = rebuilt_image.sides[0].readCatalogue()
        rebuilt_names = sorted(e.fullName for e in rebuilt_cat.entries)

        assert orig_names == rebuilt_names

        # Also verify file data round-trips.
        for entry in orig_cat.entries:
            orig_data = orig_image.sides[0].readFile(entry)
            rebuilt_entry = [e for e in rebuilt_cat.entries
                             if e.fullName == entry.fullName][0]
            rebuilt_data = rebuilt_image.sides[0].readFile(rebuilt_entry)
            assert orig_data == rebuilt_data, f"Data mismatch for {entry.fullName}"
