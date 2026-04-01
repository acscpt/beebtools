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
from beebtools.disc import extractAll, buildImage
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
        side.addFile("VICTIM", "$", b"\x01" * 100, load_addr=0x1900)
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
        side.addFile("LOADER", "$", b"\xFF" * 256, load_addr=0x1900,
                     exec_addr=0x1900, locked=True)

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
        side.addFile("DATA", "$", b"\x00" * 10)

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
        side.addFile("PROG", "T", b"\x0D\x00\x0A\x05\x20\x0D\xFF", load_addr=0x0E00, exec_addr=0x8023)
        side.addFile("DATA", "$", b"Hello\r", load_addr=0)

        img_path = str(tmp_path / "original.ssd")
        with open(img_path, "wb") as f:
            f.write(original.serialize())

        # Extract with .inf sidecars.
        extract_dir = str(tmp_path / "extracted")
        extractAll(img_path, extract_dir, write_inf=True)

        # Build a new image from the extracted files.
        rebuilt_bytes = buildImage(
            source_dir=extract_dir,
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

        rebuilt_bytes = buildImage(src, tracks=80, is_dsd=True, title="DOUBLE")

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
            rebuilt_bytes = buildImage(src, tracks=80)

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
