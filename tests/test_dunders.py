# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for Python data model (dunder) methods on DFS and ADFS classes."""

import os
from pathlib import Path

import pytest

from beebtools import DiscFile
from beebtools.dfs import (
    DFSEntry, DFSSide, DFSImage, createDiscImage, openDiscImage,
)
from beebtools.adfs import (
    ADFSEntry, ADFSSide, ADFSImage, createAdfsImage, openAdfsImage,
)


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

@pytest.fixture
def dfs_image():
    """A blank 80-track SSD with two files added."""
    image = createDiscImage(tracks=80, title="DUNDERS")
    side = image.sides[0]
    side.addFile(DiscFile("$.HELLO", b"\x0D\x00\x0A\x05\x0D\xFF", 0x1900, 0x8023))
    side.addFile(DiscFile("T.MYPROG", b"\x0D\x00\x14\x05\x0D\xFF", 0x1900, 0x8023))
    return image


@pytest.fixture
def dsd_image():
    """A blank 80-track DSD (double-sided) with one file on each side."""
    image = createDiscImage(tracks=80, title="DOUBLE", is_dsd=True)
    image.sides[0].addFile(DiscFile("$.PROG1", b"\x01\x02\x03", 0x0E00, 0x0E00))
    image.sides[1].addFile(DiscFile("$.PROG2", b"\x04\x05\x06", 0x0E00, 0x0E00))
    return image


@pytest.fixture
def adfs_image():
    """A blank ADFS-S image with two files added."""
    image = createAdfsImage(title="ADFS")
    side = image.sides[0]
    side.addFile(DiscFile("$.HELLO", b"\x0D\x00\x0A\x05\x0D\xFF", 0x1900, 0x8023))
    side.addFile(DiscFile("$.GAME", b"\x01\x02\x03\x04", 0x0E00, 0x0E00))
    return image


# =======================================================================
# DFSEntry dunders
# =======================================================================

class TestDFSEntryDunders:

    def testRepr(self, dfs_image):
        side = dfs_image.sides[0]
        entry = side.readCatalogue().entries[0]
        r = repr(entry)
        assert r.startswith("DFSEntry(")
        assert entry.fullName in r
        assert "load=0x" in r
        assert "exec=0x" in r
        assert "length=" in r

    def testStr(self, dfs_image):
        side = dfs_image.sides[0]
        entry = side.readCatalogue().entries[0]
        assert str(entry) == entry.fullName

    def testFspath(self, dfs_image):
        side = dfs_image.sides[0]
        entry = side.readCatalogue().entries[0]
        fspath = os.fspath(entry)

        # Should replace the DFS '.' separator with '/'
        assert "." not in fspath
        assert "/" in fspath
        assert fspath == f"{entry.directory}/{entry.name}"

    def testFspathWithPathlib(self, dfs_image):
        """Path(dest) / entry should produce a valid host path."""
        side = dfs_image.sides[0]
        entry = side.readCatalogue().entries[0]
        result = Path("/tmp/out") / entry
        assert str(result) == f"/tmp/out/{entry.directory}/{entry.name}"

    def testEqAndHash(self):
        """Frozen dataclass provides eq and hash."""
        a = DFSEntry("HELLO", "$", 0x1900, 0x8023, 100, 2, False)
        b = DFSEntry("HELLO", "$", 0x1900, 0x8023, 100, 2, False)
        assert a == b
        assert hash(a) == hash(b)

        c = DFSEntry("OTHER", "$", 0x1900, 0x8023, 100, 2, False)
        assert a != c


# =======================================================================
# DFSSide dunders
# =======================================================================

class TestDFSSideDunders:

    def testIter(self, dfs_image):
        side = dfs_image.sides[0]
        entries = list(side)
        cat_entries = list(side.readCatalogue().entries)
        assert entries == cat_entries

    def testLen(self, dfs_image):
        side = dfs_image.sides[0]
        assert len(side) == len(side.readCatalogue().entries)
        assert len(side) == 2

    def testGetitemFound(self, dfs_image):
        side = dfs_image.sides[0]
        entry = side["T.MYPROG"]
        assert entry.fullName == "T.MYPROG"

    def testGetitemNotFound(self, dfs_image):
        side = dfs_image.sides[0]
        with pytest.raises(KeyError):
            side["X.NOSUCH"]

    def testContains(self, dfs_image):
        side = dfs_image.sides[0]
        assert "T.MYPROG" in side
        assert "$.HELLO" in side
        assert "X.NOSUCH" not in side

    def testContainsNonString(self, dfs_image):
        """Non-string keys always return False."""
        side = dfs_image.sides[0]
        assert 42 not in side

    def testRepr(self, dfs_image):
        side = dfs_image.sides[0]
        r = repr(side)
        assert r.startswith("DFSSide(")
        assert "DUNDERS" in r
        assert "2 entries" in r
        assert "sectors free" in r


# =======================================================================
# DFSImage dunders
# =======================================================================

class TestDFSImageDunders:

    def testIterSsd(self, dfs_image):
        sides = list(dfs_image)
        assert len(sides) == 1
        assert isinstance(sides[0], DFSSide)

    def testIterDsd(self, dsd_image):
        sides = list(dsd_image)
        assert len(sides) == 2

    def testLen(self, dfs_image):
        assert len(dfs_image) == 1

    def testLenDsd(self, dsd_image):
        assert len(dsd_image) == 2

    def testGetitem(self, dsd_image):
        side0 = dsd_image[0]
        side1 = dsd_image[1]
        assert side0.side == 0
        assert side1.side == 1

    def testGetitemOutOfRange(self, dfs_image):
        with pytest.raises(IndexError):
            dfs_image[5]

    def testRepr(self, dfs_image):
        r = repr(dfs_image)
        assert "DFSImage" in r
        assert "SSD" in r
        assert "1 sides" in r

    def testReprDsd(self, dsd_image):
        r = repr(dsd_image)
        assert "DSD" in r
        assert "2 sides" in r

    def testContextManager(self, dfs_image):
        with dfs_image as img:
            assert img is dfs_image
            assert len(img) == 1


# =======================================================================
# ADFSEntry dunders
# =======================================================================

class TestADFSEntryDunders:

    def testRepr(self, adfs_image):
        side = adfs_image.sides[0]
        entries = side.readCatalogue().entries
        # Find a non-directory entry
        entry = next(e for e in entries if not e.isDirectory)
        r = repr(entry)
        assert r.startswith("ADFSEntry(")
        assert entry.fullName in r
        assert "load=0x" in r
        assert "exec=0x" in r

    def testStr(self, adfs_image):
        side = adfs_image.sides[0]
        entry = next(e for e in side if not e.isDirectory)
        assert str(entry) == entry.fullName

    def testFspath(self, adfs_image):
        side = adfs_image.sides[0]
        entry = next(e for e in side if not e.isDirectory)
        fspath = os.fspath(entry)

        # Should convert '$.' separators to '/'
        assert "." not in fspath
        assert fspath.startswith("$")

    def testFspathWithPathlib(self, adfs_image):
        side = adfs_image.sides[0]
        entry = next(e for e in side if not e.isDirectory)
        result = Path("/tmp/out") / entry

        # The path should be host-safe
        assert "/tmp/out/$" in str(result)


# =======================================================================
# ADFSSide dunders
# =======================================================================

class TestADFSSideDunders:

    def testIter(self, adfs_image):
        side = adfs_image.sides[0]
        entries = list(side)
        cat_entries = list(side.readCatalogue().entries)
        assert entries == cat_entries

    def testLen(self, adfs_image):
        side = adfs_image.sides[0]
        assert len(side) == len(side.readCatalogue().entries)

    def testGetitemFound(self, adfs_image):
        side = adfs_image.sides[0]
        entry = side["$.HELLO"]
        assert entry.fullName == "$.HELLO"

    def testGetitemNotFound(self, adfs_image):
        side = adfs_image.sides[0]
        with pytest.raises(KeyError):
            side["$.NOSUCH"]

    def testContains(self, adfs_image):
        side = adfs_image.sides[0]
        assert "$.HELLO" in side
        assert "$.GAME" in side
        assert "$.NOSUCH" not in side

    def testRepr(self, adfs_image):
        side = adfs_image.sides[0]
        r = repr(side)
        assert r.startswith("ADFSSide(")
        assert "entries" in r
        assert "sectors free" in r


# =======================================================================
# ADFSImage dunders
# =======================================================================

class TestADFSImageDunders:

    def testIter(self, adfs_image):
        sides = list(adfs_image)
        assert len(sides) == 1
        assert isinstance(sides[0], ADFSSide)

    def testLen(self, adfs_image):
        assert len(adfs_image) == 1

    def testGetitem(self, adfs_image):
        side = adfs_image[0]
        assert side.side == 0

    def testGetitemOutOfRange(self, adfs_image):
        with pytest.raises(IndexError):
            adfs_image[5]

    def testRepr(self, adfs_image):
        r = repr(adfs_image)
        assert "ADFSImage" in r
        assert "ADF" in r
        assert "1 sides" in r

    def testContextManager(self, adfs_image):
        with adfs_image as img:
            assert img is adfs_image
            assert len(img) == 1
