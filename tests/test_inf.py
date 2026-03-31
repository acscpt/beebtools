# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for the .inf sidecar format parser and formatter."""

import pytest

from beebtools.inf import InfData, parseInf, formatInf


# =======================================================================
# parseInf
# =======================================================================

class TestParseInf:
    """Tests for parsing .inf sidecar lines."""

    def testStandardFormat(self) -> None:
        """Standard DIR.NAME with 6-digit hex addresses."""
        result = parseInf("$.BOOT  FF1900 FF8023 000A00")

        assert result.directory == "$"
        assert result.name == "BOOT"
        assert result.load_addr == 0xFF1900
        assert result.exec_addr == 0xFF8023
        assert result.length == 0x000A00
        assert result.locked is False
        assert result.crc is None

    def testLockedFile(self) -> None:
        """Lock flag appended after the length field."""
        result = parseInf("$.BOOT  FF1900 FF8023 000A00 L")

        assert result.locked is True
        assert result.name == "BOOT"
        assert result.load_addr == 0xFF1900

    def testLowercaseLockFlag(self) -> None:
        """Lock flag is case-insensitive."""
        result = parseInf("$.BOOT  000000 000000 000100 l")

        assert result.locked is True

    def testCrcField(self) -> None:
        """CRC=XXXX field is parsed when present."""
        result = parseInf("$.BOOT  FF1900 FF8023 000A00 CRC=1A2B")

        assert result.crc == 0x1A2B
        assert result.locked is False

    def testLockedWithCrc(self) -> None:
        """Both lock flag and CRC can appear together."""
        result = parseInf("$.BOOT  FF1900 FF8023 000A00 L CRC=ABCD")

        assert result.locked is True
        assert result.crc == 0xABCD

    def testCrcBeforeLock(self) -> None:
        """CRC and lock flag can appear in either order."""
        result = parseInf("$.BOOT  FF1900 FF8023 000A00 CRC=ABCD L")

        assert result.locked is True
        assert result.crc == 0xABCD

    def testNonDefaultDirectory(self) -> None:
        """Directory characters other than $ are parsed correctly."""
        result = parseInf("T.MYPROG  000E00 008023 001400")

        assert result.directory == "T"
        assert result.name == "MYPROG"

    def testBareNameDefaultsToDefault(self) -> None:
        """A bare filename with no directory prefix defaults to $."""
        result = parseInf("BOOT  FF1900 FF8023 000A00")

        assert result.directory == "$"
        assert result.name == "BOOT"
        assert result.load_addr == 0xFF1900

    def testEightDigitHex(self) -> None:
        """8-digit hex addresses are accepted (e.g. BBC BASIC for Windows)."""
        result = parseInf("$.FILE  FFFF1900 FFFF8023 00000A00")

        assert result.load_addr == 0xFFFF1900
        assert result.exec_addr == 0xFFFF8023
        assert result.length == 0x00000A00

    def testExtraWhitespace(self) -> None:
        """Multiple spaces and tabs between fields are tolerated."""
        result = parseInf("$.BOOT    FF1900\tFF8023\t\t000A00")

        assert result.directory == "$"
        assert result.name == "BOOT"
        assert result.load_addr == 0xFF1900

    def testLeadingTrailingWhitespace(self) -> None:
        """Leading and trailing whitespace is stripped by split()."""
        result = parseInf("  $.BOOT  FF1900 FF8023 000A00  ")

        assert result.name == "BOOT"
        assert result.length == 0x000A00

    def testZeroAddresses(self) -> None:
        """All-zero addresses are valid."""
        result = parseInf("$.DATA  000000 000000 000100")

        assert result.load_addr == 0
        assert result.exec_addr == 0
        assert result.length == 0x100

    def testLowercaseHex(self) -> None:
        """Lowercase hex digits are accepted."""
        result = parseInf("$.BOOT  ff1900 ff8023 000a00")

        assert result.load_addr == 0xFF1900
        assert result.exec_addr == 0xFF8023
        assert result.length == 0x000A00

    def testFullName(self) -> None:
        """The fullName property returns DIR.NAME format."""
        result = parseInf("T.MYPROG  000E00 008023 001400")

        assert result.fullName == "T.MYPROG"

    def testTooFewFields(self) -> None:
        """Fewer than 4 fields raises ValueError."""
        with pytest.raises(ValueError, match="at least 4 fields"):
            parseInf("$.BOOT FF1900")

    def testEmptyLine(self) -> None:
        """Empty line raises ValueError."""
        with pytest.raises(ValueError, match="at least 4 fields"):
            parseInf("")

    def testInvalidHex(self) -> None:
        """Non-hex value in an address field raises ValueError."""
        with pytest.raises(ValueError):
            parseInf("$.BOOT  ZZZZZZ FF8023 000A00")

    def testSingleCharName(self) -> None:
        """Single-character filename is valid in DFS."""
        result = parseInf("$.A  000000 000000 000010")

        assert result.name == "A"
        assert result.directory == "$"

    def testLongName(self) -> None:
        """7-character filename (DFS maximum) parses correctly."""
        result = parseInf("$.ABCDEFG  000000 000000 000010")

        assert result.name == "ABCDEFG"


# =======================================================================
# formatInf
# =======================================================================

class TestFormatInf:
    """Tests for formatting .inf sidecar lines."""

    def testBasicFormat(self) -> None:
        """Standard output with 6-digit uppercase hex."""
        line = formatInf("$", "BOOT", 0xFF1900, 0xFF8023, 0x000A00)

        assert line == "$.BOOT  FF1900 FF8023 000A00"

    def testLockedFormat(self) -> None:
        """Lock flag is appended after the length."""
        line = formatInf("$", "BOOT", 0xFF1900, 0xFF8023, 0x000A00, locked=True)

        assert line == "$.BOOT  FF1900 FF8023 000A00 L"

    def testZeroAddresses(self) -> None:
        """Zero addresses are zero-padded to 6 digits."""
        line = formatInf("$", "DATA", 0, 0, 0x100)

        assert line == "$.DATA  000000 000000 000100"

    def testNonDefaultDirectory(self) -> None:
        """Non-default directory character appears in output."""
        line = formatInf("T", "MYPROG", 0x0E00, 0x8023, 0x1400)

        assert line == "T.MYPROG  000E00 008023 001400"

    def testSmallValues(self) -> None:
        """Small values are zero-padded correctly."""
        line = formatInf("$", "X", 1, 2, 3)

        assert line == "$.X  000001 000002 000003"

    def testMaxDfsAddress(self) -> None:
        """Maximum 18-bit DFS address (0x3FFFF) fits in 6 hex digits."""
        line = formatInf("$", "MAX", 0x3FFFF, 0x3FFFF, 0x3FFFF)

        assert line == "$.MAX  03FFFF 03FFFF 03FFFF"


# =======================================================================
# Round-trip
# =======================================================================

class TestInfRoundTrip:
    """Round-trip tests: format then parse back."""

    def testRoundTripUnlocked(self) -> None:
        """Format and parse produces identical metadata."""
        line = formatInf("$", "BOOT", 0xFF1900, 0xFF8023, 0x0A00)
        result = parseInf(line)

        assert result.directory == "$"
        assert result.name == "BOOT"
        assert result.load_addr == 0xFF1900
        assert result.exec_addr == 0xFF8023
        assert result.length == 0x0A00
        assert result.locked is False

    def testRoundTripLocked(self) -> None:
        """Locked flag survives a format-parse cycle."""
        line = formatInf("T", "PROG", 0x0E00, 0x8023, 0x1400, locked=True)
        result = parseInf(line)

        assert result.directory == "T"
        assert result.name == "PROG"
        assert result.load_addr == 0x0E00
        assert result.exec_addr == 0x8023
        assert result.length == 0x1400
        assert result.locked is True

    def testRoundTripZero(self) -> None:
        """Zero-address file survives a format-parse cycle."""
        line = formatInf("$", "EMPTY", 0, 0, 0)
        result = parseInf(line)

        assert result.load_addr == 0
        assert result.exec_addr == 0
        assert result.length == 0

    def testRoundTripAllDirectories(self) -> None:
        """Every printable ASCII directory character round-trips."""
        for code in range(0x21, 0x7F):
            d = chr(code)
            line = formatInf(d, "FILE", 0x1000, 0x2000, 0x100)
            result = parseInf(line)

            assert result.directory == d, f"Failed for directory 0x{code:02X}"
            assert result.name == "FILE"
