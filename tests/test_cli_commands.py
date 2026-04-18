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

from beebtools import BeebToolsWarning
from beebtools.dfs import createDiscImage, openDiscImage, DFSError, DFSAccessFlags
from beebtools.entry import DiscFile, DiscError
from beebtools.disc import (
    extractAll, buildImage, formatDirectoryInf,
    getTitle, setTitle, getBoot, setBoot, discInfo,
    getFileAttribs, setFileAttribs,
    renameFile, compactDisc, makeDirectory,
)
from beebtools.boot import BootOption
from beebtools.adfs import openAdfsImage, ADFSAccessFlags, ADFSError
from beebtools.inf import formatInf, parseInf, INF_X_START_SECTOR
from beebtools.cli import (
    cmdCreate, cmdAdd, cmdDelete, cmdBuild,
    cmdTitle, cmdBoot, cmdDisc, cmdAttrib, cmdRename,
    cmdCompact, cmdMkdir,
    _loadResourceBundles,
)


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

        # Check that the .inf sidecar exists (flat layout: $.LOADER.bin.inf).
        inf_path = os.path.join(out_dir, "$.LOADER.bin.inf")
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
# Directory-level .inf ($.inf)
# =======================================================================

class TestDirectoryInf:

    def testExtractWritesDollarInf(self, tmp_path) -> None:
        """extract --inf writes a $.inf alongside the $ directory."""
        image = createDiscImage(tracks=80, title="MYTITLE", boot_option=BootOption.RUN)
        side = image.sides[0]
        side.addFile(DiscFile("$.DATA", b"\x00" * 10))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, write_inf=True)

        inf_path = os.path.join(out_dir, "$.inf")
        assert os.path.isfile(inf_path)

        with open(inf_path, "r") as f:
            inf = parseInf(f.readline().strip())

        assert inf.extra_info.get("TITLE") == "MYTITLE"
        assert inf.extra_info.get("OPT") == "2"

    def testNoDollarInfWithoutFlag(self, tmp_path) -> None:
        """Without write_inf, no $.inf is written."""
        image = createDiscImage(tracks=80, title="TEST")
        side = image.sides[0]
        side.addFile(DiscFile("$.DATA", b"\x00" * 10))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, write_inf=False)

        assert not os.path.isfile(os.path.join(out_dir, "$.inf"))

    def testBuildReadsDollarInf(self, tmp_path) -> None:
        """buildImage reads $.inf and applies title and boot option."""
        image = createDiscImage(tracks=80, title="ORIGINAL", boot_option=BootOption.EXEC)
        side = image.sides[0]
        side.addFile(DiscFile("$.DATA", b"\x00" * 10))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        extract_dir = str(tmp_path / "extracted")
        extractAll(img_path, extract_dir, write_inf=True)

        # Rebuild without explicit title/boot -- $.inf is source of truth.
        rebuilt_bytes = buildImage(
            source_dir=extract_dir,
            output_path="rebuilt.ssd",
            tracks=80,
        )

        rebuilt_path = str(tmp_path / "rebuilt.ssd")
        with open(rebuilt_path, "wb") as f:
            f.write(rebuilt_bytes)

        rebuilt = openDiscImage(rebuilt_path)
        cat = rebuilt.sides[0].readCatalogue()

        assert cat.title == "ORIGINAL"
        assert cat.boot_option == BootOption.EXEC

    def testBuildWithExplicitTitleWarns(self, tmp_path) -> None:
        """Explicit title + $.inf emits a warning; .inf wins."""
        image = createDiscImage(tracks=80, title="INFVALUE")
        side = image.sides[0]
        side.addFile(DiscFile("$.DATA", b"\x00" * 10))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        extract_dir = str(tmp_path / "extracted")
        extractAll(img_path, extract_dir, write_inf=True)

        with pytest.warns(BeebToolsWarning, match="using TITLE from .inf"):
            rebuilt_bytes = buildImage(
                source_dir=extract_dir,
                output_path="rebuilt.ssd",
                tracks=80,
                title="OVERRIDE",
            )

        rebuilt_path = str(tmp_path / "rebuilt.ssd")
        with open(rebuilt_path, "wb") as f:
            f.write(rebuilt_bytes)

        rebuilt = openDiscImage(rebuilt_path)
        cat = rebuilt.sides[0].readCatalogue()

        assert cat.title == "INFVALUE"

    def testBuildForceOverridesInf(self, tmp_path) -> None:
        """force=True makes explicit title/boot override $.inf."""
        image = createDiscImage(tracks=80, title="INFVALUE", boot_option=BootOption.LOAD)
        side = image.sides[0]
        side.addFile(DiscFile("$.DATA", b"\x00" * 10))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        extract_dir = str(tmp_path / "extracted")
        extractAll(img_path, extract_dir, write_inf=True)

        rebuilt_bytes = buildImage(
            source_dir=extract_dir,
            output_path="rebuilt.ssd",
            tracks=80,
            title="FORCED",
            boot_option=BootOption.EXEC,
            force=True,
        )

        rebuilt_path = str(tmp_path / "rebuilt.ssd")
        with open(rebuilt_path, "wb") as f:
            f.write(rebuilt_bytes)

        rebuilt = openDiscImage(rebuilt_path)
        cat = rebuilt.sides[0].readCatalogue()

        assert cat.title == "FORCED"
        assert cat.boot_option == BootOption.EXEC

    def testBuildWithoutDollarInfUsesExplicit(self, tmp_path) -> None:
        """When no $.inf exists, explicit title/boot are used."""
        # Create source tree manually with a file .inf but no $.inf.
        src = str(tmp_path / "src")
        dollar = os.path.join(src, "$")
        os.makedirs(dollar)

        with open(os.path.join(dollar, "DATA.bin"), "wb") as f:
            f.write(b"\x00" * 10)

        inf_line = formatInf("$", "DATA", 0, 0, 10)
        with open(os.path.join(dollar, "DATA.bin.inf"), "w") as f:
            f.write(inf_line + "\n")

        rebuilt_bytes = buildImage(
            source_dir=src,
            output_path="rebuilt.ssd",
            tracks=80,
            title="EXPLICIT",
            boot_option=BootOption.RUN,
        )

        rebuilt_path = str(tmp_path / "rebuilt.ssd")
        with open(rebuilt_path, "wb") as f:
            f.write(rebuilt_bytes)

        rebuilt = openDiscImage(rebuilt_path)
        cat = rebuilt.sides[0].readCatalogue()

        assert cat.title == "EXPLICIT"
        assert cat.boot_option == BootOption.RUN

    def testDsdPerSideDollarInf(self, tmp_path) -> None:
        """DSD extract writes $.inf per side; rebuild reads both."""
        from dataclasses import replace as dc_replace
        from beebtools.image import createImage

        image = createImage("test.dsd", tracks=80, title="SIDE0T")
        image.sides[0].addFile(DiscFile("$.F0", b"\x00" * 10))
        image.sides[1].addFile(DiscFile("$.F1", b"\xFF" * 10))

        # Set side 1 title differently.
        cat1 = image.sides[1].readCatalogue()
        image.sides[1].writeCatalogue(dc_replace(cat1, title="SIDE1T"))

        img_path = str(tmp_path / "test.dsd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        extract_dir = str(tmp_path / "extracted")
        extractAll(img_path, extract_dir, write_inf=True)

        # Verify per-side $.inf files exist.
        assert os.path.isfile(os.path.join(extract_dir, "side0", "$.inf"))
        assert os.path.isfile(os.path.join(extract_dir, "side1", "$.inf"))

        # Rebuild and check per-side titles survived.
        rebuilt_bytes = buildImage(
            source_dir=extract_dir,
            output_path="rebuilt.dsd",
            tracks=80,
        )

        rebuilt_path = str(tmp_path / "rebuilt.dsd")
        with open(rebuilt_path, "wb") as f:
            f.write(rebuilt_bytes)

        rebuilt = openDiscImage(rebuilt_path)
        assert rebuilt.sides[0].readCatalogue().title == "SIDE0T"
        assert rebuilt.sides[1].readCatalogue().title == "SIDE1T"

    def testFormatDirectoryInfSyntax1(self) -> None:
        """formatDirectoryInf emits syntax 1 with zeroed hex fields."""
        line = formatDirectoryInf("MY DISC", BootOption.RUN)

        inf = parseInf(line)
        assert inf.load_addr == 0
        assert inf.exec_addr == 0
        assert inf.length == 0
        assert inf.extra_info["TITLE"] == "MY DISC"
        assert inf.extra_info["OPT"] == "2"

    def testFormatDirectoryInfEmptyTitle(self) -> None:
        """Empty title omits the TITLE key."""
        line = formatDirectoryInf("", BootOption.OFF)

        inf = parseInf(line)
        assert "TITLE" not in inf.extra_info
        assert inf.extra_info["OPT"] == "0"

    def testAdfsDirectoryInfWritten(self, tmp_path) -> None:
        """ADFS extract writes .inf sidecars for subdirectory entries."""
        from beebtools.image import createImage

        image = createImage("test.adf", tracks=80, title="ADFSTEST")
        side = image.sides[0]
        side.mkdir("$.GAMES")
        side.addFile(DiscFile("$.GAMES.ELITE", b"\xFF" * 100,
                     load_addr=0x1900, exec_addr=0x1900))

        img_path = str(tmp_path / "test.adf")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, write_inf=True)

        # In flat mode the directory .inf is a standalone file.
        dir_inf_path = os.path.join(out_dir, "$.GAMES.inf")
        assert os.path.isfile(dir_inf_path)

        with open(dir_inf_path, "r") as f:
            inf = parseInf(f.readline().strip())

        assert inf.directory == "$"
        assert inf.name == "GAMES"


# =======================================================================
# CRC generation and validation
# =======================================================================

class TestCrc:

    def testExtractEmitsCrc16(self, tmp_path) -> None:
        """Extract with --inf emits CRC= in the .inf sidecar."""
        image = createDiscImage(tracks=80)
        data = b"\x00" * 256
        image.sides[0].addFile(DiscFile("$.FILE", data, load_addr=0x1900))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, write_inf=True)

        inf_path = os.path.join(out_dir, "$.FILE.bin.inf")
        with open(inf_path, "r") as f:
            inf = parseInf(f.readline().strip())

        import binascii
        expected = binascii.crc_hqx(data, 0)
        assert inf.crc == expected

    def testExtractEmitsCrc32(self, tmp_path) -> None:
        """Extract with --inf emits CRC32= in the .inf sidecar."""
        image = createDiscImage(tracks=80)
        data = b"\xAA" * 100
        image.sides[0].addFile(DiscFile("$.FILE", data))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, write_inf=True)

        inf_path = os.path.join(out_dir, "$.FILE.bin.inf")
        with open(inf_path, "r") as f:
            inf = parseInf(f.readline().strip())

        import binascii
        expected = binascii.crc32(data) & 0xFFFFFFFF
        assert inf.extra_info["CRC32"] == f"{expected:08X}"

    def testDirectoryInfNoCrc(self, tmp_path) -> None:
        """Directory-level $.inf does not contain CRC or CRC32 keys."""
        image = createDiscImage(tracks=80, title="TEST")
        image.sides[0].addFile(DiscFile("$.DATA", b"\x00" * 10))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, write_inf=True)

        with open(os.path.join(out_dir, "$.inf"), "r") as f:
            inf = parseInf(f.readline().strip())

        assert "CRC" not in inf.extra_info
        assert "CRC32" not in inf.extra_info

    def testBuildCrcMatchNoWarning(self, tmp_path) -> None:
        """Build from unmodified .bin files produces no CRC warnings."""
        image = createDiscImage(tracks=80, title="CRCTEST")
        image.sides[0].addFile(DiscFile("$.FILE", b"\xFF" * 256,
                               load_addr=0x1900, exec_addr=0x1900))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        extract_dir = str(tmp_path / "extracted")
        extractAll(img_path, extract_dir, write_inf=True)

        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            buildImage(
                source_dir=extract_dir,
                output_path="rebuilt.ssd",
                tracks=80,
            )

        crc_warnings = [w for w in caught if "CRC" in str(w.message)]
        assert crc_warnings == []

    def testBuildCrcMismatchWarns(self, tmp_path) -> None:
        """Modified file data triggers a CRC mismatch warning on build."""
        image = createDiscImage(tracks=80, title="CRCTEST")
        image.sides[0].addFile(DiscFile("$.FILE", b"\xFF" * 256,
                               load_addr=0x1900, exec_addr=0x1900))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        extract_dir = str(tmp_path / "extracted")
        extractAll(img_path, extract_dir, write_inf=True)

        # Corrupt the extracted file.
        bin_path = os.path.join(extract_dir, "$.FILE.bin")
        with open(bin_path, "wb") as f:
            f.write(b"\x00" * 256)

        with pytest.warns(BeebToolsWarning, match="CRC mismatch"):
            buildImage(
                source_dir=extract_dir,
                output_path="rebuilt.ssd",
                tracks=80,
            )

    def testBuildCrc32MismatchWarns(self, tmp_path) -> None:
        """Modified file data triggers a CRC32 mismatch warning on build."""
        image = createDiscImage(tracks=80, title="CRCTEST")
        image.sides[0].addFile(DiscFile("$.FILE", b"\xFF" * 256,
                               load_addr=0x1900, exec_addr=0x1900))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        extract_dir = str(tmp_path / "extracted")
        extractAll(img_path, extract_dir, write_inf=True)

        # Corrupt the extracted file.
        bin_path = os.path.join(extract_dir, "$.FILE.bin")
        with open(bin_path, "wb") as f:
            f.write(b"\x00" * 256)

        with pytest.warns(BeebToolsWarning, match="CRC32 mismatch"):
            buildImage(
                source_dir=extract_dir,
                output_path="rebuilt.ssd",
                tracks=80,
            )

    def testCrcRoundTripBinaryFile(self, tmp_path) -> None:
        """CRC round-trips correctly for an unmodified binary file."""
        import binascii

        data = bytes(range(256))
        image = createDiscImage(tracks=80, title="CRCRT")
        image.sides[0].addFile(DiscFile("$.BIN", data,
                               load_addr=0x1900, exec_addr=0x1900))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        extract_dir = str(tmp_path / "extracted")
        extractAll(img_path, extract_dir, write_inf=True)

        # Verify .inf has the correct CRC.
        inf_path = os.path.join(extract_dir, "$.BIN.bin.inf")
        with open(inf_path, "r") as f:
            inf = parseInf(f.readline().strip())

        assert inf.crc == binascii.crc_hqx(data, 0)

        # Rebuild -- no CRC warning expected.
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            buildImage(
                source_dir=extract_dir,
                output_path="rebuilt.ssd",
                tracks=80,
            )

        crc_warnings = [w for w in caught if "CRC" in str(w.message)]
        assert crc_warnings == []


# =======================================================================
# Flat extraction layout
# =======================================================================

class TestFlatLayout:

    def testFlatDfsMultipleDirs(self, tmp_path) -> None:
        """Flat extraction places files from different DFS directories
        in the same output directory using dir.name notation."""
        image = createDiscImage(tracks=80, title="FLAT")
        image.sides[0].addFile(DiscFile("$.BOOT", b"\xFF" * 16,
                               load_addr=0x1900, exec_addr=0x1900))
        image.sides[0].addFile(DiscFile("T.DATA", b"\x00" * 16))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir)

        assert os.path.isfile(os.path.join(out_dir, "$.BOOT.bin"))
        assert os.path.isfile(os.path.join(out_dir, "T.DATA.bin"))
        # No subdirectories created for directory characters.
        assert not os.path.isdir(os.path.join(out_dir, "$"))
        assert not os.path.isdir(os.path.join(out_dir, "T"))

    def testFlatAdfsNestedPath(self, tmp_path) -> None:
        """Flat extraction uses the full ADFS path as the filename."""
        from beebtools.image import createImage

        image = createImage("test.adf", tracks=80, title="ADFSFLAT")
        side = image.sides[0]
        side.mkdir("$.GAMES")
        side.addFile(DiscFile("$.GAMES.ELITE", b"\xFF" * 100,
                     load_addr=0x1900, exec_addr=0x1900))

        img_path = str(tmp_path / "test.adf")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir)

        assert os.path.isfile(os.path.join(out_dir, "$.GAMES.ELITE.bin"))
        assert os.path.isfile(os.path.join(out_dir, "$.GAMES.inf"))
        # No ADFS subdirectories created.
        assert not os.path.isdir(os.path.join(out_dir, "$"))

    def testFlatDsd(self, tmp_path) -> None:
        """Flat DSD extraction creates side subdirs with flat contents."""
        from beebtools.image import createImage
        from dataclasses import replace as dc_replace

        image = createImage("test.dsd", tracks=80, title="S0")
        image.sides[0].addFile(DiscFile("$.F0", b"\x00" * 10))
        image.sides[1].addFile(DiscFile("$.F1", b"\xFF" * 10))

        img_path = str(tmp_path / "test.dsd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir)

        assert os.path.isfile(os.path.join(out_dir, "side0", "$.F0.bin"))
        assert os.path.isfile(os.path.join(out_dir, "side1", "$.F1.bin"))
        # No $ subdirectory inside sideN.
        assert not os.path.isdir(os.path.join(out_dir, "side0", "$"))

    def testFlatDirectoryInfStandalone(self, tmp_path) -> None:
        """In flat mode, directory .inf is a standalone file with no
        companion data file."""
        from beebtools.image import createImage

        image = createImage("test.adf", tracks=80, title="DIRTEST")
        side = image.sides[0]
        side.mkdir("$.UTILS")
        side.addFile(DiscFile("$.UTILS.EDIT", b"\x00" * 50))

        img_path = str(tmp_path / "test.adf")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir)

        # Directory .inf exists.
        dir_inf = os.path.join(out_dir, "$.UTILS.inf")
        assert os.path.isfile(dir_inf)

        # No companion data file or directory for UTILS.
        assert not os.path.isfile(os.path.join(out_dir, "$.UTILS"))
        assert not os.path.isdir(os.path.join(out_dir, "$.UTILS"))

    def testMkdirsLayoutCreatesSubdirs(self, tmp_path) -> None:
        """layout='hierarchical' creates subdirectories from Acorn paths."""
        image = createDiscImage(tracks=80, title="HIER")
        image.sides[0].addFile(DiscFile("$.BOOT", b"\xFF" * 16,
                               load_addr=0x1900, exec_addr=0x1900))
        image.sides[0].addFile(DiscFile("T.DATA", b"\x00" * 16))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, layout="hierarchical")

        assert os.path.isfile(os.path.join(out_dir, "$", "BOOT.bin"))
        assert os.path.isfile(os.path.join(out_dir, "T", "DATA.bin"))

    def testInfWrittenByDefault(self, tmp_path) -> None:
        """.inf sidecars are written by default without explicit flag."""
        image = createDiscImage(tracks=80, title="INFDEF")
        image.sides[0].addFile(DiscFile("$.FILE", b"\xFF" * 16))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir)

        assert os.path.isfile(os.path.join(out_dir, "$.FILE.bin.inf"))
        assert os.path.isfile(os.path.join(out_dir, "$.inf"))

    def testNoInfSuppressesSidecars(self, tmp_path) -> None:
        """write_inf=False suppresses all .inf output."""
        image = createDiscImage(tracks=80)
        image.sides[0].addFile(DiscFile("$.FILE", b"\xFF" * 16))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        out_dir = str(tmp_path / "out")
        extractAll(img_path, out_dir, write_inf=False)

        for root, dirs, files in os.walk(out_dir):
            for fname in files:
                assert not fname.endswith(".inf"), f"Unexpected .inf: {fname}"

    def testFlatRoundTrip(self, tmp_path) -> None:
        """Files extracted flat with .inf round-trip through buildImage."""
        image = createDiscImage(tracks=80, title="ROUNDTRP")
        image.sides[0].addFile(DiscFile("$.PROG", b"\xFF" * 256,
                               load_addr=0x1900, exec_addr=0x1900))
        image.sides[0].addFile(DiscFile("T.DATA", b"\xAA" * 100))

        img_path = str(tmp_path / "test.ssd")
        with open(img_path, "wb") as f:
            f.write(image.serialize())

        extract_dir = str(tmp_path / "extracted")
        extractAll(img_path, extract_dir)

        rebuilt_bytes = buildImage(
            source_dir=extract_dir,
            output_path="rebuilt.ssd",
            tracks=80,
        )

        rebuilt_path = str(tmp_path / "rebuilt.ssd")
        with open(rebuilt_path, "wb") as f:
            f.write(rebuilt_bytes)

        rebuilt = openDiscImage(rebuilt_path)
        cat = rebuilt.sides[0].readCatalogue()

        names = {e.fullName for e in cat.entries}
        assert "$.PROG" in names
        assert "T.DATA" in names
        assert cat.title == "ROUNDTRP"


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

        with pytest.warns(BeebToolsWarning, match="ORPHAN"):
            rebuilt_bytes = buildImage(src, "empty.ssd", tracks=80)

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
            0x0E,                       # length (4 + 10 content bytes)
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
        bas_path = os.path.join(extract_dir, "$.HELLO.bas")
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

    def testBuildHonoursStartSectorHint(self, tmp_path) -> None:
        """X_START_SECTOR in an .inf sidecar places the file at that sector.

        The orchestration layer routes the hint through to the format
        engine so byte-exact rebuilds land files at their original
        positions. Without the hint, DFS would pick a different sector
        based on free-space allocation.
        """
        src = str(tmp_path / "src")
        d = os.path.join(src, "$")
        os.makedirs(d)

        data = b"\xAA" * 256
        with open(os.path.join(d, "PLACED.bin"), "wb") as f:
            f.write(data)
        with open(os.path.join(d, "PLACED.bin.inf"), "w") as f:
            f.write(
                formatInf(
                    "$", "PLACED", 0, 0, len(data), False,
                    extra_info={INF_X_START_SECTOR: "123"},
                ) + "\n"
            )

        rebuilt_bytes = buildImage(src, "placed.ssd", tracks=80)

        rebuilt_path = str(tmp_path / "placed.ssd")
        with open(rebuilt_path, "wb") as f:
            f.write(rebuilt_bytes)

        rebuilt = openDiscImage(rebuilt_path)
        entry = next(
            e for e in rebuilt.sides[0].readCatalogue().entries
            if e.fullName == "$.PLACED"
        )

        assert entry.start_sector == 123
        assert rebuilt.sides[0].readFile(entry) == data

    def testBuildHandlesLevel9OverlapWriteOrder(self, tmp_path) -> None:
        """Two placed files with overlapping sectors write in end-sector order.

        The orchestration sorts placed records by end sector ascending
        so that the outer (full-coverage) file writes last and its
        data is authoritative in the overlap region. Mirrors the
        Level 9 copy-protection layout where two catalogue entries
        legitimately share sectors but the content is byte-consistent.
        """
        src = str(tmp_path / "src")
        d = os.path.join(src, "$")
        os.makedirs(d)

        # FULL occupies sectors 200..202, HALF sits inside it at 201.
        shared_sector = b"\xCC" * 256
        full_data = b"\xAA" * 256 + shared_sector + b"\xBB" * 256
        half_data = shared_sector

        with open(os.path.join(d, "FULL.bin"), "wb") as f:
            f.write(full_data)
        with open(os.path.join(d, "FULL.bin.inf"), "w") as f:
            f.write(
                formatInf(
                    "$", "FULL", 0, 0, len(full_data), False,
                    extra_info={INF_X_START_SECTOR: "200"},
                ) + "\n"
            )

        with open(os.path.join(d, "HALF.bin"), "wb") as f:
            f.write(half_data)
        with open(os.path.join(d, "HALF.bin.inf"), "w") as f:
            f.write(
                formatInf(
                    "$", "HALF", 0, 0, len(half_data), False,
                    extra_info={INF_X_START_SECTOR: "201"},
                ) + "\n"
            )

        rebuilt_bytes = buildImage(src, "level9.ssd", tracks=80)

        rebuilt_path = str(tmp_path / "level9.ssd")
        with open(rebuilt_path, "wb") as f:
            f.write(rebuilt_bytes)

        rebuilt = openDiscImage(rebuilt_path)
        entries = {
            e.fullName: e for e in rebuilt.sides[0].readCatalogue().entries
        }

        assert entries["$.FULL"].start_sector == 200
        assert entries["$.HALF"].start_sector == 201
        assert rebuilt.sides[0].readFile(entries["$.FULL"]) == full_data
        assert rebuilt.sides[0].readFile(entries["$.HALF"]) == half_data

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
            f.write(
                formatInf("$.GAMES", "ELITE", 0x1000, 0x2000, 300) + "\n"
            )

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

        with pytest.warns(BeebToolsWarning, match="skipping"):
            image_bytes = buildImage(source_dir=src, output_path="skip.adf")

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


# =======================================================================
# cmdTitle - DFS
# =======================================================================

class TestCmdTitle:

    def _createSsd(self, tmp_path, title="ORIGINAL") -> str:
        """Create a blank 80-track SSD with a title and return its path."""
        out = str(tmp_path / "disc.ssd")
        args = Namespace(output=out, tracks=80, title=title, boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        return out

    def testGetTitle(self, tmp_path) -> None:
        """Read the current disc title."""
        img = self._createSsd(tmp_path, title="HELLO")
        args = Namespace(image=img, title=None, side=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdTitle(args)

        assert buf.getvalue().strip() == "HELLO"

    def testSetTitle(self, tmp_path) -> None:
        """Set the disc title and verify it round-trips."""
        img = self._createSsd(tmp_path)
        args = Namespace(image=img, title="NEWTITLE", side=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdTitle(args)

        assert "Title set" in buf.getvalue()

        # Verify round-trip.
        assert getTitle(img) == "NEWTITLE"

    def testTitleTooLong(self, tmp_path) -> None:
        """Title exceeding 12 chars raises DiscError for DFS."""
        img = self._createSsd(tmp_path)

        with pytest.raises(DiscError, match="Title too long"):
            setTitle(img, "A" * 13)

    def testTitleMaxLength(self, tmp_path) -> None:
        """Title at exactly 12 chars is accepted for DFS."""
        img = self._createSsd(tmp_path)
        setTitle(img, "A" * 12)

        assert getTitle(img) == "A" * 12

    def testSetTitleDsd(self, tmp_path) -> None:
        """Set title on side 1 of a DSD image."""
        out = str(tmp_path / "double.dsd")
        args = Namespace(output=out, tracks=80, title="SIDE0", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        # Set title on side 1.
        setTitle(out, "SIDE1", side=1)

        # Both sides should have independent titles.
        assert getTitle(out, side=0) == "SIDE0"
        assert getTitle(out, side=1) == "SIDE1"


# =======================================================================
# cmdTitle - ADFS
# =======================================================================

class TestCmdTitleAdfs:

    def _createAdf(self, tmp_path, title="ADFSTITLE") -> str:
        """Create a blank ADFS image and return its path."""
        out = str(tmp_path / "disc.adf")
        args = Namespace(output=out, tracks=80, title=title, boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        return out

    def testGetTitleAdfs(self, tmp_path) -> None:
        """Read the current ADFS disc title."""
        img = self._createAdf(tmp_path, title="MYDISC")

        assert getTitle(img) == "MYDISC"

    def testSetTitleAdfs(self, tmp_path) -> None:
        """Set the ADFS disc title and verify round-trip."""
        img = self._createAdf(tmp_path)
        setTitle(img, "NEWADFS")

        assert getTitle(img) == "NEWADFS"

    def testAdfsTitleMaxLength(self, tmp_path) -> None:
        """ADFS allows up to 19-char titles."""
        img = self._createAdf(tmp_path)
        setTitle(img, "A" * 19)

        assert getTitle(img) == "A" * 19

    def testAdfsTitleTooLong(self, tmp_path) -> None:
        """Title exceeding 19 chars raises DiscError for ADFS."""
        img = self._createAdf(tmp_path)

        with pytest.raises(DiscError, match="Title too long"):
            setTitle(img, "A" * 20)


# =======================================================================
# cmdBoot - DFS
# =======================================================================

class TestCmdBoot:

    def _createSsd(self, tmp_path) -> str:
        """Create a blank 80-track SSD and return its path."""
        out = str(tmp_path / "disc.ssd")
        args = Namespace(output=out, tracks=80, title="", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        return out

    def testGetBootDefault(self, tmp_path) -> None:
        """Default boot option is OFF."""
        img = self._createSsd(tmp_path)
        args = Namespace(image=img, boot=None, side=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdBoot(args)

        assert buf.getvalue().strip() == "OFF"

    def testSetBootAllValues(self, tmp_path) -> None:
        """Set and verify each boot option value."""
        img = self._createSsd(tmp_path)

        for opt in BootOption:
            setBoot(img, opt)
            assert getBoot(img) == opt

    def testCmdBootSetRun(self, tmp_path) -> None:
        """cmdBoot setter sets boot to RUN."""
        img = self._createSsd(tmp_path)
        args = Namespace(image=img, boot=BootOption.RUN, side=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdBoot(args)

        assert "RUN" in buf.getvalue()
        assert getBoot(img) == BootOption.RUN

    def testSetBootDsd(self, tmp_path) -> None:
        """Set boot option on side 1 of a DSD image."""
        out = str(tmp_path / "double.dsd")
        args = Namespace(output=out, tracks=80, title="", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        setBoot(out, BootOption.EXEC, side=1)

        assert getBoot(out, side=0) == BootOption.OFF
        assert getBoot(out, side=1) == BootOption.EXEC


# =======================================================================
# cmdBoot - ADFS
# =======================================================================

class TestCmdBootAdfs:

    def _createAdf(self, tmp_path) -> str:
        """Create a blank ADFS image and return its path."""
        out = str(tmp_path / "disc.adf")
        args = Namespace(output=out, tracks=80, title="", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        return out

    def testSetBootAdfsAllValues(self, tmp_path) -> None:
        """Set and verify each boot option on ADFS."""
        img = self._createAdf(tmp_path)

        for opt in BootOption:
            setBoot(img, opt)
            assert getBoot(img) == opt


# =======================================================================
# cmdDisc - DFS
# =======================================================================

class TestCmdDisc:

    def _createSsd(self, tmp_path, title="DISCTEST") -> str:
        """Create a blank 80-track SSD and return its path."""
        out = str(tmp_path / "disc.ssd")
        args = Namespace(output=out, tracks=80, title=title, boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        return out

    def testDiscSummary(self, tmp_path) -> None:
        """Summary mode prints title, boot, tracks, and free space."""
        img = self._createSsd(tmp_path, title="SUMMARY")
        args = Namespace(image=img, set_title=None, set_boot=None, side=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdDisc(args)

        output = buf.getvalue()
        assert "SUMMARY" in output
        assert "OFF" in output
        assert "80" in output

    def testDiscMutateTitle(self, tmp_path) -> None:
        """disc --title sets the title."""
        img = self._createSsd(tmp_path)
        args = Namespace(
            image=img, set_title="CHANGED", set_boot=None, side=0,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdDisc(args)

        assert "Updated" in buf.getvalue()
        assert getTitle(img) == "CHANGED"

    def testDiscMutateBoot(self, tmp_path) -> None:
        """disc --boot sets the boot option."""
        img = self._createSsd(tmp_path)
        args = Namespace(
            image=img, set_title=None, set_boot=BootOption.LOAD, side=0,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdDisc(args)

        assert getBoot(img) == BootOption.LOAD

    def testDiscMutateBoth(self, tmp_path) -> None:
        """disc --title --boot sets both in one call."""
        img = self._createSsd(tmp_path)
        args = Namespace(
            image=img, set_title="BOTH", set_boot=BootOption.EXEC, side=0,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdDisc(args)

        assert getTitle(img) == "BOTH"
        assert getBoot(img) == BootOption.EXEC

    def testDiscInfoFunction(self, tmp_path) -> None:
        """discInfo returns correct metadata."""
        img = self._createSsd(tmp_path, title="INFO")
        info = discInfo(img)

        assert info.title == "INFO"
        assert info.boot_option == BootOption.OFF
        assert info.tracks == 80
        assert info.side == 0
        assert info.free_space > 0


# =======================================================================
# cmdDisc - ADFS
# =======================================================================

class TestCmdDiscAdfs:

    def _createAdf(self, tmp_path, title="ADFSDISC") -> str:
        """Create a blank ADFS image and return its path."""
        out = str(tmp_path / "disc.adf")
        args = Namespace(output=out, tracks=80, title=title, boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        return out

    def testDiscSummaryAdfs(self, tmp_path) -> None:
        """Summary mode for ADFS prints title and boot."""
        img = self._createAdf(tmp_path, title="ADFSSUMM")
        args = Namespace(image=img, set_title=None, set_boot=None, side=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdDisc(args)

        output = buf.getvalue()
        assert "ADFSSUMM" in output
        assert "OFF" in output

    def testDiscMutateTitleAdfs(self, tmp_path) -> None:
        """disc --title on an ADFS image."""
        img = self._createAdf(tmp_path)
        args = Namespace(
            image=img, set_title="NEWADFS", set_boot=None, side=0,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdDisc(args)

        assert getTitle(img) == "NEWADFS"

    def testDiscMutateBootAdfs(self, tmp_path) -> None:
        """disc --boot on an ADFS image."""
        img = self._createAdf(tmp_path)
        args = Namespace(
            image=img, set_title=None, set_boot=BootOption.RUN, side=0,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdDisc(args)

        assert getBoot(img) == BootOption.RUN

    def testDiscInfoAdfs(self, tmp_path) -> None:
        """discInfo returns correct ADFS metadata."""
        img = self._createAdf(tmp_path, title="AINFO")
        info = discInfo(img)

        assert info.title == "AINFO"
        assert info.boot_option == BootOption.OFF
        assert info.free_space > 0
        assert info.total_sectors > 0


# =======================================================================
# cmdAttrib - DFS
# =======================================================================

class TestCmdAttrib:

    def _createSsdWithFile(self, tmp_path) -> str:
        """Create an SSD with one file and return its path."""
        out = str(tmp_path / "disc.ssd")
        args = Namespace(output=out, tracks=80, title="", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        data_path = str(tmp_path / "prog.bin")
        with open(data_path, "wb") as f:
            f.write(b"\x01\x02\x03\x04")

        args = Namespace(
            image=out, file=data_path, name="T.MYPROG",
            load="1900", exec_addr="8023", locked=False,
            inf=False, side=0, basic=False,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        return out

    def testGetAttribs(self, tmp_path) -> None:
        """Getter mode prints file attributes."""
        img = self._createSsdWithFile(tmp_path)
        args = Namespace(
            image=img, filename="T.MYPROG", side=0,
            locked=None, access=None, load=None, exec_addr=None,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAttrib(args)

        output = buf.getvalue()
        assert "T.MYPROG" in output
        assert "00001900" in output
        assert "00008023" in output

    def testSetLocked(self, tmp_path) -> None:
        """Lock a file and verify round-trip."""
        img = self._createSsdWithFile(tmp_path)

        attribs = getFileAttribs(img, "T.MYPROG")
        assert not attribs.locked

        setFileAttribs(img, "T.MYPROG", locked=True)

        attribs = getFileAttribs(img, "T.MYPROG")
        assert attribs.locked

    def testSetUnlocked(self, tmp_path) -> None:
        """Unlock a previously locked file."""
        img = self._createSsdWithFile(tmp_path)

        setFileAttribs(img, "T.MYPROG", locked=True)
        setFileAttribs(img, "T.MYPROG", locked=False)

        attribs = getFileAttribs(img, "T.MYPROG")
        assert not attribs.locked

    def testSetLoadExec(self, tmp_path) -> None:
        """Change load and exec addresses."""
        img = self._createSsdWithFile(tmp_path)

        setFileAttribs(img, "T.MYPROG", load_addr=0x31900, exec_addr=0x38023)

        attribs = getFileAttribs(img, "T.MYPROG")
        assert attribs.load_addr == 0x31900
        assert attribs.exec_addr == 0x38023

    def testCmdAttribSetter(self, tmp_path) -> None:
        """cmdAttrib setter mode locks and changes addresses."""
        img = self._createSsdWithFile(tmp_path)
        args = Namespace(
            image=img, filename="T.MYPROG", side=0,
            locked=True, access=None, load="031900", exec_addr="038023",
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAttrib(args)

        assert "Updated" in buf.getvalue()
        attribs = getFileAttribs(img, "T.MYPROG")
        assert attribs.locked
        assert attribs.load_addr == 0x31900

    def testFileNotFound(self, tmp_path) -> None:
        """Attrib on nonexistent file raises DiscError."""
        img = self._createSsdWithFile(tmp_path)

        with pytest.raises(DiscError, match="not found"):
            getFileAttribs(img, "$.NOPE")

    def testSetAttribsPreservesData(self, tmp_path) -> None:
        """Changing attributes does not corrupt file data."""
        img = self._createSsdWithFile(tmp_path)

        setFileAttribs(img, "T.MYPROG", locked=True, load_addr=0xABCD)

        image = openDiscImage(img)
        entry = image.sides[0]["T.MYPROG"]
        data = image.sides[0].readFile(entry)
        assert data == b"\x01\x02\x03\x04"


# =======================================================================
# cmdAttrib - ADFS
# =======================================================================

class TestCmdAttribAdfs:

    def _createAdfWithFile(self, tmp_path) -> str:
        """Create an ADFS image with one file and return its path."""
        out = str(tmp_path / "disc.adf")
        args = Namespace(output=out, tracks=80, title="", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        data_path = str(tmp_path / "prog.bin")
        with open(data_path, "wb") as f:
            f.write(b"\xAA\xBB\xCC")

        args = Namespace(
            image=out, file=data_path, name="$.MYFILE",
            load="2000", exec_addr="3000", locked=False,
            inf=False, side=0, basic=False,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        return out

    def testSetLockedAdfs(self, tmp_path) -> None:
        """Lock a file on an ADFS image."""
        img = self._createAdfWithFile(tmp_path)

        setFileAttribs(img, "$.MYFILE", locked=True)

        attribs = getFileAttribs(img, "$.MYFILE")
        assert attribs.locked

    def testSetLoadExecAdfs(self, tmp_path) -> None:
        """Change load and exec addresses on an ADFS image."""
        img = self._createAdfWithFile(tmp_path)

        setFileAttribs(img, "$.MYFILE", load_addr=0xFFFF1900, exec_addr=0xFFFF8023)

        attribs = getFileAttribs(img, "$.MYFILE")
        assert attribs.load_addr == 0xFFFF1900
        assert attribs.exec_addr == 0xFFFF8023

    def testSetAttribsPreservesDataAdfs(self, tmp_path) -> None:
        """Changing ADFS attributes does not corrupt file data."""
        img = self._createAdfWithFile(tmp_path)

        setFileAttribs(img, "$.MYFILE", locked=True, load_addr=0x1234)

        image = openAdfsImage(img)
        entry = image.sides[0]["$.MYFILE"]
        data = image.sides[0].readFile(entry)
        assert data == b"\xAA\xBB\xCC"


# =======================================================================
# attrib --access grammar (ADFS)
# =======================================================================

class TestAttribAccessAdfs:

    def _createAdfWithFile(self, tmp_path, name: str = "$.MYFILE") -> str:
        """Create an ADFS image with one file and return its path."""
        out = str(tmp_path / "disc.adf")
        args = Namespace(output=out, tracks=80, title="", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        data_path = str(tmp_path / "prog.bin")
        with open(data_path, "wb") as f:
            f.write(b"\xAA\xBB\xCC")

        args = Namespace(
            image=out, file=data_path, name=name,
            load="2000", exec_addr="3000", locked=False,
            inf=False, side=0, basic=False,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        return out

    def testAbsoluteOwnerOnly(self, tmp_path) -> None:
        """Absolute spec replaces the byte with only the owner bits."""
        img = self._createAdfWithFile(tmp_path)

        setFileAttribs(img, "$.MYFILE", access_flags="LR")

        attribs = getFileAttribs(img, "$.MYFILE")
        assert attribs.access_flags == (
            ADFSAccessFlags.OWNER_L | ADFSAccessFlags.OWNER_R
        )
        assert attribs.access_string == "LR"
        assert attribs.locked is True

    def testAbsoluteSlashFoldsToPublic(self, tmp_path) -> None:
        """Letters after '/' fold to their public-case equivalents."""
        img = self._createAdfWithFile(tmp_path)

        setFileAttribs(img, "$.MYFILE", access_flags="LR/R")

        attribs = getFileAttribs(img, "$.MYFILE")
        # 'R' after the slash is folded to 'r' (PUBLIC_R).
        assert ADFSAccessFlags.OWNER_L in attribs.access_flags
        assert ADFSAccessFlags.OWNER_R in attribs.access_flags
        assert ADFSAccessFlags.PUBLIC_R in attribs.access_flags

    def testAbsoluteMixedCaseNoSlash(self, tmp_path) -> None:
        """Mixed case without a slash still maps owner vs public by case."""
        img = self._createAdfWithFile(tmp_path)

        setFileAttribs(img, "$.MYFILE", access_flags="LRr")

        attribs = getFileAttribs(img, "$.MYFILE")
        assert attribs.access_string == "LR/r"

    def testAbsoluteEmptyClearsAllBits(self, tmp_path) -> None:
        """Empty absolute spec clears the access byte entirely."""
        img = self._createAdfWithFile(tmp_path)
        setFileAttribs(img, "$.MYFILE", access_flags="LR")

        setFileAttribs(img, "$.MYFILE", access_flags="")

        attribs = getFileAttribs(img, "$.MYFILE")
        assert int(attribs.access_flags) == 0
        assert attribs.access_string == ""

    def testMutationAddsAndRemoves(self, tmp_path) -> None:
        """+L-W+R applies each mutation in order."""
        img = self._createAdfWithFile(tmp_path)

        # Start from WR (the default access the addFile path produces).
        setFileAttribs(img, "$.MYFILE", access_flags="+L-W+R")

        attribs = getFileAttribs(img, "$.MYFILE")
        assert ADFSAccessFlags.OWNER_L in attribs.access_flags
        assert ADFSAccessFlags.OWNER_W not in attribs.access_flags
        assert ADFSAccessFlags.OWNER_R in attribs.access_flags

    def testDLetterWarnedAndIgnored(self, tmp_path) -> None:
        """D is a directory type flag; warn, ignore, apply the rest."""
        img = self._createAdfWithFile(tmp_path)

        with pytest.warns(BeebToolsWarning, match="directory type flag"):
            setFileAttribs(img, "$.MYFILE", access_flags="LD")

        attribs = getFileAttribs(img, "$.MYFILE")
        assert ADFSAccessFlags.OWNER_L in attribs.access_flags

    def testLowerDWarnedAndIgnored(self, tmp_path) -> None:
        """Lowercase 'd' warns the same way as 'D' in a mutation spec."""
        img = self._createAdfWithFile(tmp_path)

        with pytest.warns(BeebToolsWarning, match="directory type flag"):
            setFileAttribs(img, "$.MYFILE", access_flags="+L+d")

        attribs = getFileAttribs(img, "$.MYFILE")
        assert ADFSAccessFlags.OWNER_L in attribs.access_flags

    def testUnknownLetterWarnedAndIgnored(self, tmp_path) -> None:
        """Non-access letters (not D) warn under a generic message."""
        img = self._createAdfWithFile(tmp_path)

        with pytest.warns(BeebToolsWarning, match="non-access letters"):
            setFileAttribs(img, "$.MYFILE", access_flags="LQ")

        attribs = getFileAttribs(img, "$.MYFILE")
        assert ADFSAccessFlags.OWNER_L in attribs.access_flags

    def testMixedAbsoluteAndMutationRejected(self, tmp_path) -> None:
        """'L+W' combines absolute and mutation forms - error."""
        img = self._createAdfWithFile(tmp_path)

        with pytest.raises(ADFSError, match="absolute.*mutation"):
            setFileAttribs(img, "$.MYFILE", access_flags="L+W")

    def testMutationPlusMinusSameBitRejected(self, tmp_path) -> None:
        """'+L-L' sets and clears the same bit - ambiguous, reject."""
        img = self._createAdfWithFile(tmp_path)

        with pytest.raises(ADFSError, match="sets and clears"):
            setFileAttribs(img, "$.MYFILE", access_flags="+L-L")


# =======================================================================
# attrib --access grammar (DFS)
# =======================================================================

class TestAttribAccessDfs:

    def _createSsdWithFile(self, tmp_path) -> str:
        """Create an SSD with one file and return its path."""
        out = str(tmp_path / "disc.ssd")
        args = Namespace(output=out, tracks=80, title="", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        data_path = str(tmp_path / "prog.bin")
        with open(data_path, "wb") as f:
            f.write(b"\x01\x02\x03\x04")

        args = Namespace(
            image=out, file=data_path, name="T.MYPROG",
            load="1900", exec_addr="8023", locked=False,
            inf=False, side=0, basic=False,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        return out

    def testAbsoluteLLocks(self, tmp_path) -> None:
        """'L' as an absolute spec locks the file."""
        img = self._createSsdWithFile(tmp_path)

        setFileAttribs(img, "T.MYPROG", access_flags="L")

        attribs = getFileAttribs(img, "T.MYPROG")
        assert attribs.locked is True
        assert attribs.access_string == "L"

    def testAbsoluteEmptyUnlocks(self, tmp_path) -> None:
        """Empty absolute spec unlocks the file on DFS."""
        img = self._createSsdWithFile(tmp_path)
        setFileAttribs(img, "T.MYPROG", locked=True)

        setFileAttribs(img, "T.MYPROG", access_flags="")

        attribs = getFileAttribs(img, "T.MYPROG")
        assert attribs.locked is False

    def testAbsoluteLockedWord(self, tmp_path) -> None:
        """'LOCKED' is accepted as a synonym for 'L'."""
        img = self._createSsdWithFile(tmp_path)

        setFileAttribs(img, "T.MYPROG", access_flags="LOCKED")

        attribs = getFileAttribs(img, "T.MYPROG")
        assert attribs.locked is True

    def testNonLLettersWarnAndStrip(self, tmp_path) -> None:
        """Non-L letters emit a warning and are stripped on DFS."""
        img = self._createSsdWithFile(tmp_path)

        with pytest.warns(BeebToolsWarning, match="non-L"):
            setFileAttribs(img, "T.MYPROG", access_flags="LWR")

        # L still applied; W and R silently dropped after warning.
        attribs = getFileAttribs(img, "T.MYPROG")
        assert attribs.locked is True


# =======================================================================
# setFileAttribs library API - IntFlag pass-through
# =======================================================================

class TestSetFileAttribsFlagClass:

    def _makeDfs(self, tmp_path) -> str:
        out = str(tmp_path / "disc.ssd")
        args = Namespace(output=out, tracks=80, title="", boot=0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        data_path = str(tmp_path / "d.bin")
        with open(data_path, "wb") as f:
            f.write(b"x")
        args = Namespace(
            image=out, file=data_path, name="T.FILE",
            load="0", exec_addr="0", locked=False,
            inf=False, side=0, basic=False,
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)
        return out

    def _makeAdf(self, tmp_path) -> str:
        out = str(tmp_path / "disc.adf")
        args = Namespace(output=out, tracks=80, title="", boot=0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        data_path = str(tmp_path / "d.bin")
        with open(data_path, "wb") as f:
            f.write(b"x")
        args = Namespace(
            image=out, file=data_path, name="$.FILE",
            load="0", exec_addr="0", locked=False,
            inf=False, side=0, basic=False,
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)
        return out

    def testAdfsIntFlagAbsolute(self, tmp_path) -> None:
        """IntFlag argument is an absolute replacement on ADFS."""
        img = self._makeAdf(tmp_path)

        setFileAttribs(
            img, "$.FILE",
            access_flags=ADFSAccessFlags.OWNER_L | ADFSAccessFlags.OWNER_R,
        )

        attribs = getFileAttribs(img, "$.FILE")
        assert attribs.access_flags == (
            ADFSAccessFlags.OWNER_L | ADFSAccessFlags.OWNER_R
        )

    def testDfsIntFlagLocks(self, tmp_path) -> None:
        """DFSAccessFlags.LOCKED sets the lock bit on DFS."""
        img = self._makeDfs(tmp_path)

        setFileAttribs(img, "T.FILE", access_flags=DFSAccessFlags.LOCKED)

        attribs = getFileAttribs(img, "T.FILE")
        assert attribs.locked is True

    def testDfsIntFlagZeroUnlocks(self, tmp_path) -> None:
        """DFSAccessFlags(0) absolute clears the lock bit."""
        img = self._makeDfs(tmp_path)
        setFileAttribs(img, "T.FILE", locked=True)

        setFileAttribs(img, "T.FILE", access_flags=DFSAccessFlags(0))

        attribs = getFileAttribs(img, "T.FILE")
        assert attribs.locked is False

    def testDfsFlagOnAdfsImageRaises(self, tmp_path) -> None:
        """Passing the wrong format's flag type raises ValueError."""
        img = self._makeAdf(tmp_path)

        with pytest.raises(ValueError, match="ADFSAccessFlags"):
            setFileAttribs(
                img, "$.FILE", access_flags=DFSAccessFlags.LOCKED,
            )

    def testAdfsFlagOnDfsImageRaises(self, tmp_path) -> None:
        """Passing the wrong format's flag type raises ValueError."""
        img = self._makeDfs(tmp_path)

        with pytest.raises(ValueError, match="DFSAccessFlags"):
            setFileAttribs(
                img, "T.FILE", access_flags=ADFSAccessFlags.OWNER_L,
            )

    def testAccessAndLockedMutuallyExclusive(self, tmp_path) -> None:
        """access_flags and locked together is ambiguous - raise."""
        img = self._makeDfs(tmp_path)

        with pytest.raises(ValueError, match="mutually exclusive"):
            setFileAttribs(
                img, "T.FILE",
                locked=True, access_flags=DFSAccessFlags.LOCKED,
            )


# =======================================================================
# ADFS directory DWLR regression guard
# =======================================================================

class TestAdfsDirectoryAccessWarn:

    def testDirectoryStripsOwnerWAndPublicBits(self, tmp_path) -> None:
        """Owner-W and public bits on a directory are warn-and-stripped."""
        out = str(tmp_path / "disc.adf")
        args = Namespace(output=out, tracks=80, title="", boot=0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        # makeDirectory from the high-level API creates a subdirectory.
        makeDirectory(out, "$.SUB")

        # Ask for LWR/r - spec would set owner-L, owner-W, owner-R,
        # and public-r. The applyAccess path must strip owner-W and
        # all public bits on a directory (DWLR regression guard).
        with pytest.warns(BeebToolsWarning):
            setFileAttribs(out, "$.SUB", access_flags="LWR/r")

        attribs = getFileAttribs(out, "$.SUB")
        assert ADFSAccessFlags.OWNER_W not in attribs.access_flags
        assert ADFSAccessFlags.PUBLIC_R not in attribs.access_flags
        # Owner-L and owner-R survive; directory marker is reapplied.
        assert ADFSAccessFlags.OWNER_L in attribs.access_flags
        assert ADFSAccessFlags.OWNER_R in attribs.access_flags
        assert attribs.access_string.startswith("D")


# =======================================================================
# cmdAttrib --access through the CLI wrapper
# =======================================================================

class TestCmdAttribAccess:

    def _createSsdWithFile(self, tmp_path) -> str:
        out = str(tmp_path / "disc.ssd")
        args = Namespace(output=out, tracks=80, title="", boot=0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        data_path = str(tmp_path / "d.bin")
        with open(data_path, "wb") as f:
            f.write(b"x")
        args = Namespace(
            image=out, file=data_path, name="T.F",
            load="0", exec_addr="0", locked=False,
            inf=False, side=0, basic=False,
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)
        return out

    def testCmdAttribAccessThroughSetter(self, tmp_path) -> None:
        """cmdAttrib passes --access down to setFileAttribs."""
        img = self._createSsdWithFile(tmp_path)
        args = Namespace(
            image=img, filename="T.F", side=0,
            locked=None, access="L", load=None, exec_addr=None,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAttrib(args)

        attribs = getFileAttribs(img, "T.F")
        assert attribs.locked is True
        assert "access='L'" in buf.getvalue()

    def testCmdAttribGetterPrintsAccessLine(self, tmp_path) -> None:
        """Getter output includes an Access: line."""
        img = self._createSsdWithFile(tmp_path)
        setFileAttribs(img, "T.F", locked=True)

        args = Namespace(
            image=img, filename="T.F", side=0,
            locked=None, access=None, load=None, exec_addr=None,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAttrib(args)

        output = buf.getvalue()
        assert "Access: L" in output


# =======================================================================
# _loadResourceBundles discovery
# =======================================================================

class TestLoadResourceBundles:

    def testBothFormatsContribute(self) -> None:
        """Both adfs_resources and dfs_resources are discovered."""
        merged = _loadResourceBundles("cli")

        assert "attrib.access" in merged
        body = merged["attrib.access"]
        assert "ADFS:" in body
        assert "DFS:" in body

    def testOrderingDeterministic(self) -> None:
        """Module discovery is sorted alphabetically - ADFS before DFS."""
        merged = _loadResourceBundles("cli")

        body = merged["attrib.access"]
        assert body.index("ADFS:") < body.index("DFS:")

    def testUnknownConsumerReturnsEmpty(self) -> None:
        """A consumer key that no module declares yields an empty dict."""
        merged = _loadResourceBundles("no-such-consumer")

        assert merged == {}


# =======================================================================
# cmdRename - DFS
# =======================================================================

class TestCmdRename:

    def _createSsdWithFile(self, tmp_path) -> str:
        """Create an SSD with one file and return its path."""
        out = str(tmp_path / "disc.ssd")
        args = Namespace(output=out, tracks=80, title="", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        data_path = str(tmp_path / "prog.bin")
        with open(data_path, "wb") as f:
            f.write(b"\x01\x02\x03\x04")

        args = Namespace(
            image=out, file=data_path, name="T.MYPROG",
            load="1900", exec_addr="8023", locked=False,
            inf=False, side=0, basic=False,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        return out

    def testSimpleRename(self, tmp_path) -> None:
        """Rename a file keeping the same directory."""
        img = self._createSsdWithFile(tmp_path)

        renameFile(img, "T.MYPROG", "T.NEWNAME")

        attribs = getFileAttribs(img, "T.NEWNAME")
        assert attribs.fullName == "T.NEWNAME"
        assert attribs.load_addr == 0x1900

    def testChangeDirPrefix(self, tmp_path) -> None:
        """Rename a file to a different DFS directory."""
        img = self._createSsdWithFile(tmp_path)

        renameFile(img, "T.MYPROG", "$.MOVED")

        attribs = getFileAttribs(img, "$.MOVED")
        assert attribs.fullName == "$.MOVED"

    def testDuplicateBlocked(self, tmp_path) -> None:
        """Rename to an existing name raises DiscError."""
        img = self._createSsdWithFile(tmp_path)

        # Add a second file.
        data_path = str(tmp_path / "other.bin")
        with open(data_path, "wb") as f:
            f.write(b"\xAA")

        args = Namespace(
            image=img, file=data_path, name="T.OTHER",
            load="0", exec_addr="0", locked=False,
            inf=False, side=0, basic=False,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        with pytest.raises(DiscError, match="already exists"):
            renameFile(img, "T.MYPROG", "T.OTHER")

    def testSourceNotFound(self, tmp_path) -> None:
        """Rename of nonexistent file raises error."""
        img = self._createSsdWithFile(tmp_path)

        with pytest.raises(Exception, match="not found"):
            renameFile(img, "$.NOPE", "$.NEW")

    def testRenamePreservesData(self, tmp_path) -> None:
        """Renaming does not corrupt file data."""
        img = self._createSsdWithFile(tmp_path)

        renameFile(img, "T.MYPROG", "T.RENAMED")

        image = openDiscImage(img)
        entry = image.sides[0]["T.RENAMED"]
        data = image.sides[0].readFile(entry)
        assert data == b"\x01\x02\x03\x04"

    def testRenamePreservesAttribs(self, tmp_path) -> None:
        """Renaming preserves load/exec/locked attributes."""
        img = self._createSsdWithFile(tmp_path)

        setFileAttribs(img, "T.MYPROG", locked=True)
        renameFile(img, "T.MYPROG", "T.NEWNAME")

        attribs = getFileAttribs(img, "T.NEWNAME")
        assert attribs.locked
        assert attribs.load_addr == 0x1900
        assert attribs.exec_addr == 0x8023

    def testCmdRenameHandler(self, tmp_path) -> None:
        """cmdRename handler prints confirmation."""
        img = self._createSsdWithFile(tmp_path)
        args = Namespace(
            image=img, old_name="T.MYPROG", new_name="T.NEWNAME", side=0,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdRename(args)

        assert "Renamed" in buf.getvalue()
        attribs = getFileAttribs(img, "T.NEWNAME")
        assert attribs.fullName == "T.NEWNAME"


# =======================================================================
# cmdRename - ADFS
# =======================================================================

class TestCmdRenameAdfs:

    def _createAdfWithFile(self, tmp_path) -> str:
        """Create an ADFS image with one file and return its path."""
        out = str(tmp_path / "disc.adf")
        args = Namespace(output=out, tracks=80, title="", boot=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        data_path = str(tmp_path / "prog.bin")
        with open(data_path, "wb") as f:
            f.write(b"\xAA\xBB\xCC")

        args = Namespace(
            image=out, file=data_path, name="$.MYFILE",
            load="2000", exec_addr="3000", locked=False,
            inf=False, side=0, basic=False,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        return out

    def testSimpleRenameAdfs(self, tmp_path) -> None:
        """Rename a file on ADFS within the same directory."""
        img = self._createAdfWithFile(tmp_path)

        renameFile(img, "$.MYFILE", "$.NEWNAME")

        attribs = getFileAttribs(img, "$.NEWNAME")
        assert attribs.fullName == "$.NEWNAME"
        assert attribs.load_addr == 0x2000

    def testRenamePreservesDataAdfs(self, tmp_path) -> None:
        """Renaming on ADFS does not corrupt file data."""
        img = self._createAdfWithFile(tmp_path)

        renameFile(img, "$.MYFILE", "$.RENAMED")

        image = openAdfsImage(img)
        entry = image.sides[0]["$.RENAMED"]
        data = image.sides[0].readFile(entry)
        assert data == b"\xAA\xBB\xCC"

    def testDuplicateBlockedAdfs(self, tmp_path) -> None:
        """Rename to an existing name on ADFS raises error."""
        img = self._createAdfWithFile(tmp_path)

        # Add a second file.
        data_path = str(tmp_path / "other.bin")
        with open(data_path, "wb") as f:
            f.write(b"\xDD")

        args = Namespace(
            image=img, file=data_path, name="$.OTHER",
            load="0", exec_addr="0", locked=False,
            inf=False, side=0, basic=False,
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdAdd(args)

        with pytest.raises(Exception, match="already exists"):
            renameFile(img, "$.MYFILE", "$.OTHER")


# =======================================================================
# compact command
# =======================================================================

class TestCmdCompact:

    def _createSsdWithGap(self, tmp_path) -> str:
        """Create an SSD with a gap (add 3 files, delete the middle one)."""
        image = createDiscImage(tracks=80, title="COMPACT")
        side = image.sides[0]
        side.addFile(DiscFile("$.FILE1", b"\x11" * 512, load_addr=0))
        side.addFile(DiscFile("$.FILE2", b"\x22" * 1024, load_addr=0))
        side.addFile(DiscFile("$.FILE3", b"\x33" * 256, load_addr=0))
        out = str(tmp_path / "disc.ssd")
        with open(out, "wb") as f:
            f.write(image.serialize())

        # Delete the middle file to create a gap.
        from beebtools.cli import cmdDelete
        args = Namespace(image=out, filename="$.FILE2", side=0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdDelete(args)

        return out

    def testCompactFreesSectors(self, tmp_path) -> None:
        """Compacting a disc with a gap frees sectors."""
        img = self._createSsdWithGap(tmp_path)
        args = Namespace(image=img, side=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCompact(args)

        output = buf.getvalue()
        assert "Freed" in output
        assert "sectors" in output

    def testCompactAlreadyPacked(self, tmp_path) -> None:
        """Compacting an already-compact disc reports zero freed."""
        image = createDiscImage(tracks=80, title="PACKED")
        side = image.sides[0]
        side.addFile(DiscFile("$.FILE1", b"\x11" * 256, load_addr=0))
        out = str(tmp_path / "packed.ssd")
        with open(out, "wb") as f:
            f.write(image.serialize())

        args = Namespace(image=out, side=0)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCompact(args)

        assert "already fully compacted" in buf.getvalue()

    def testCompactPreservesFileData(self, tmp_path) -> None:
        """File contents are intact after compaction."""
        img = self._createSsdWithGap(tmp_path)

        compactDisc(img, side=0)

        image = openDiscImage(img)
        side = image.sides[0]
        cat = side.readCatalogue()

        # Check the two remaining files are present and correct.
        names = {e.fullName for e in cat.entries}
        assert "$.FILE1" in names
        assert "$.FILE3" in names

        for entry in cat.entries:
            data = side.readFile(entry)
            if entry.fullName == "$.FILE1":
                assert data == b"\x11" * 512
            elif entry.fullName == "$.FILE3":
                assert data == b"\x33" * 256

    def testCompactEmptyDisc(self, tmp_path) -> None:
        """Compacting an empty disc returns zero freed."""
        image = createDiscImage(tracks=80, title="EMPTY")
        out = str(tmp_path / "empty.ssd")
        with open(out, "wb") as f:
            f.write(image.serialize())

        freed = compactDisc(out, side=0)
        assert freed == 0

    def testCompactAdfsRaisesError(self, tmp_path) -> None:
        """Compacting an ADFS image raises an error."""
        out = str(tmp_path / "disc.adf")
        args = Namespace(output=out, tracks=80, title="", boot=0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)

        with pytest.raises(DiscError, match="Compaction is not supported"):
            compactDisc(out)


# =======================================================================
# mkdir command
# =======================================================================

class TestCmdMkdir:

    def _createBlankAdf(self, tmp_path) -> str:
        """Create a blank 80-track ADF and return its path."""
        out = str(tmp_path / "disc.adf")
        args = Namespace(output=out, tracks=80, title="", boot=0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdCreate(args)
        return out

    def testMkdirCreatesDirectory(self, tmp_path) -> None:
        """mkdir creates a subdirectory on an ADFS image."""
        img = self._createBlankAdf(tmp_path)

        args = Namespace(image=img, path="$.GAMES", side=0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdMkdir(args)

        assert "Created directory $.GAMES" in buf.getvalue()

        # Verify the directory entry exists.
        image = openAdfsImage(img)
        side = image.sides[0]
        cat = side.readCatalogue()
        names = [e.name for e in cat.entries]
        assert "GAMES" in names

    def testMkdirNestedDirectory(self, tmp_path) -> None:
        """mkdir creates nested directories on ADFS."""
        img = self._createBlankAdf(tmp_path)

        # Create parent first.
        makeDirectory(img, "$.GAMES")

        # Create child.
        args = Namespace(image=img, path="$.GAMES.ARCADE", side=0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmdMkdir(args)

        assert "Created directory $.GAMES.ARCADE" in buf.getvalue()

    def testMkdirParentNotFound(self, tmp_path) -> None:
        """mkdir on a non-existent parent directory raises an error."""
        img = self._createBlankAdf(tmp_path)

        with pytest.raises(Exception):
            makeDirectory(img, "$.NOSUCH.CHILD")

    def testMkdirDfsRaisesError(self, tmp_path) -> None:
        """mkdir on a DFS image raises an error."""
        image = createDiscImage(tracks=80, title="DFSTEST")
        out = str(tmp_path / "test.ssd")
        with open(out, "wb") as f:
            f.write(image.serialize())

        with pytest.raises(DiscError, match="Subdirectories are not supported"):
            makeDirectory(out, "$.GAMES")

    def testMkdirViaLibrary(self, tmp_path) -> None:
        """makeDirectory library function creates a directory."""
        img = self._createBlankAdf(tmp_path)

        makeDirectory(img, "$.MYDIR")

        # Verify.
        image = openAdfsImage(img)
        entry = image.sides[0]["$.MYDIR"]
        assert entry.is_directory is True
