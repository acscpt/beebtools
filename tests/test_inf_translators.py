# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for the inf_translators sub-package.

Covers the stardot .inf access byte translation in both directions for
ADFS and DFS entries, the round-trip identity, and the end-to-end use
of the translator by disc.formatEntryInf.
"""

import pytest

from beebtools.adfs import ADFSEntry
from beebtools.dfs import DFSEntry
from beebtools.disc import formatEntryInf
from beebtools.inf import parseInf
from beebtools.inf_translators import fromStardotAccess, toStardotAccess


def _adfs(access: int, is_directory: bool = False, locked: bool = False) -> ADFSEntry:
    return ADFSEntry(
        name="X", directory="$",
        load_addr=0x1900, exec_addr=0x8023, length=0x400,
        start_sector=10,
        locked=locked, is_directory=is_directory,
        access=access, sequence=0,
    )


def _dfs(locked: bool) -> DFSEntry:
    return DFSEntry(
        name="X", directory="$",
        load_addr=0x1900, exec_addr=0x8023, length=0x400,
        start_sector=2, locked=locked,
    )


# =======================================================================
# ADFS: on-disc <-> stardot translation
# =======================================================================

class TestAdfsToStardot:
    """The ADFS on-disc access byte maps into the stardot .inf layout."""

    def testReadOnlyMapsRBitUnchanged(self) -> None:
        """On-disc R (0x01) maps to stardot R (0x01)."""
        assert toStardotAccess(_adfs(0x01)) == 0x01

    def testWriteMapsWBitUnchanged(self) -> None:
        """On-disc W (0x02) maps to stardot W (0x02)."""
        assert toStardotAccess(_adfs(0x02)) == 0x02

    def testLockedMovesFromBit2ToBit3(self) -> None:
        """On-disc L (0x04) maps to stardot L (0x08)."""
        assert toStardotAccess(_adfs(0x04, locked=True)) == 0x08

    def testExecutableMovesFromBit4ToBit2(self) -> None:
        """On-disc E (0x10) maps to stardot E (0x04)."""
        assert toStardotAccess(_adfs(0x10)) == 0x04

    def testPublicBitsShift(self) -> None:
        """On-disc r/w/e (0x20/0x40/0x80) map to stardot 0x10/0x20/0x40."""
        assert toStardotAccess(_adfs(0x20)) == 0x10
        assert toStardotAccess(_adfs(0x40)) == 0x20
        assert toStardotAccess(_adfs(0x80)) == 0x40

    def testDirectoryBitDropped(self) -> None:
        """On-disc D (0x08) has no stardot equivalent and is dropped."""
        entry = _adfs(0x0D, is_directory=True, locked=True)  # D|L|R
        assert toStardotAccess(entry) == 0x09  # stardot L|R

    def testLockedFileGivesStardotHexEight(self) -> None:
        """A locked ADFS file (on-disc 0x07 = R|W|L) maps to stardot 0x0B."""
        assert toStardotAccess(_adfs(0x07, locked=True)) == 0x0B


class TestAdfsFromStardot:
    """The reverse direction restores the on-disc layout."""

    def testRoundTripFile(self) -> None:
        """Round-trip on-disc -> stardot -> on-disc is identity for files."""
        for access in (0x00, 0x01, 0x03, 0x07, 0x13, 0xF7):
            entry = _adfs(access)
            stardot = toStardotAccess(entry)
            assert fromStardotAccess(entry, stardot) == access

    def testRoundTripDirectory(self) -> None:
        """A directory's D bit is restored from is_directory on the reverse pass."""
        entry = _adfs(0x0D, is_directory=True, locked=True)
        stardot = toStardotAccess(entry)
        assert fromStardotAccess(entry, stardot) == 0x0D

    def testStardotLReturnsOnDiscL(self) -> None:
        """Stardot L (0x08) maps back to on-disc L (0x04)."""
        entry = _adfs(0x00)
        assert fromStardotAccess(entry, 0x08) == 0x04


# =======================================================================
# DFS: locked-only translation
# =======================================================================

class TestDfsToStardot:
    """DFS has a single lock bit; stardot places it at 0x08."""

    def testUnlockedIsZero(self) -> None:
        """An unlocked DFS entry maps to stardot access 0x00."""
        assert toStardotAccess(_dfs(locked=False)) == 0x00

    def testLockedSetsStardotL(self) -> None:
        """A locked DFS entry maps to stardot L (0x08)."""
        assert toStardotAccess(_dfs(locked=True)) == 0x08

    def testRoundTripLocked(self) -> None:
        """Round-trip through the translator preserves the lock state."""
        for locked in (False, True):
            entry = _dfs(locked=locked)
            stardot = toStardotAccess(entry)
            assert fromStardotAccess(entry, stardot) == (0x08 if locked else 0x00)


# =======================================================================
# Dispatch sanity
# =======================================================================

class TestDispatch:
    """singledispatch picks the right translator by concrete type."""

    def testUnregisteredTypeRaises(self) -> None:
        """A type with no registered translator falls through to NotImplementedError."""
        with pytest.raises(NotImplementedError):
            toStardotAccess(object())

        with pytest.raises(NotImplementedError):
            fromStardotAccess(object(), 0x00)


# =======================================================================
# End-to-end: formatEntryInf produces spec-conformant access bytes
# =======================================================================

class TestFormatEntryInfEndToEnd:
    """disc.formatEntryInf emits stardot-layout access bytes."""

    def testLockedAdfsFileEmitsStardotEightInAccessField(self) -> None:
        """A locked ADFS file must appear in the .inf line with stardot L set."""
        entry = _adfs(0x07, locked=True)  # on-disc R|W|L
        line = formatEntryInf(entry)

        parsed = parseInf(line)
        assert parsed.locked is True
        assert parsed.access_byte == 0x0B  # stardot R|W|L

    def testAdfsDirectoryEmitsNoDirectoryBit(self) -> None:
        """A directory entry must not carry any D-like bit in the .inf access byte."""
        entry = _adfs(0x0D, is_directory=True, locked=True)
        line = formatEntryInf(entry)

        parsed = parseInf(line)
        assert parsed.access_byte == 0x09  # stardot L|R, no D

    def testUnlockedDfsFileEmitsZeroAccess(self) -> None:
        """An unlocked DFS entry emits an access byte of 0x00."""
        entry = _dfs(locked=False)
        line = formatEntryInf(entry)

        parsed = parseInf(line)
        assert parsed.access_byte == 0x00
        assert parsed.locked is False

    def testLockedDfsFileEmitsStardotL(self) -> None:
        """A locked DFS entry emits stardot L (0x08) in the .inf access byte."""
        entry = _dfs(locked=True)
        line = formatEntryInf(entry)

        parsed = parseInf(line)
        assert parsed.access_byte == 0x08
        assert parsed.locked is True
