# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for the .inf sidecar format parser and formatter.

Covers the stardot inf_format spec: syntax 1/2/3 forms, quoted strings
with RFC 3986 percent-encoding, 6- and 8-digit hex with sign extension,
DFS and ADFS access shorthand, and preservation of KEY=value extra info.
"""

from typing import Optional

import pytest

from beebtools import BeebToolsWarning
from beebtools.inf import InfData, parseInf, formatInf


# =======================================================================
# parseInf - syntax 1 (name load exec length access)
# =======================================================================

class TestParseSyntax1:
    """Spec-preferred form: name + 4 hex fields + optional extras."""

    def testEightDigitHex(self) -> None:
        """Standard syntax 1 with 8-digit hex and 2-digit access byte."""
        result = parseInf("$.BOOT FFFF1900 FFFF8023 00000A00 00")

        assert result.directory == "$"
        assert result.name == "BOOT"
        assert result.load_addr == 0xFFFF1900
        assert result.exec_addr == 0xFFFF8023
        assert result.length == 0x00000A00
        assert result.locked is False

    def testLockedAccessBit(self) -> None:
        """Bit 3 of the access byte sets the locked flag."""
        result = parseInf("$.BOOT FFFF1900 FFFF8023 00000A00 08")

        assert result.locked is True

    def testLockedCombinedWithOtherBits(self) -> None:
        """Other access bits are ignored for locked but do not block parsing."""
        result = parseInf("$.BOOT FFFF1900 FFFF8023 00000A00 1B")

        assert result.locked is True
        assert result.length == 0x00000A00

    def testExtraInfoFields(self) -> None:
        """KEY=value tokens after the hex region are kept as extra_info."""
        result = parseInf(
            "$.BOOT FFFF1900 FFFF8023 00000A00 00 CRC=1A2B OPT4=3"
        )

        assert result.extra_info == {"CRC": "1A2B", "OPT4": "3"}
        assert result.crc == 0x1A2B


# =======================================================================
# parseInf - syntax 2 (name load exec [dfs_access])
# =======================================================================

class TestParseSyntax2:
    """Historical TubeHost/BeebLink form: 3 hex fields plus optional L."""

    def testSixDigitWithSignExtension(self) -> None:
        """6-digit FFxxxx addresses are sign-extended to 32 bits."""
        result = parseInf("$.BOOT FF1900 FF8023 000A00")

        assert result.load_addr == 0xFFFF1900
        assert result.exec_addr == 0xFFFF8023
        assert result.length == 0x000A00

    def testShortAddressNotExtended(self) -> None:
        """Addresses that do not start with FF are left unchanged."""
        result = parseInf("$.BOOT 001900 008023 000A00")

        assert result.load_addr == 0x001900
        assert result.exec_addr == 0x008023

    def testLockFlagSetsLocked(self) -> None:
        """Bare L token after the hex region sets the locked flag."""
        result = parseInf("$.BOOT FF1900 FF8023 000A00 L")

        assert result.locked is True

    def testLockedKeywordVariants(self) -> None:
        """LOCKED and Locked are also accepted."""
        for keyword in ("Locked", "LOCKED"):
            result = parseInf(f"$.BOOT 001900 008023 000100 {keyword}")
            assert result.locked is True, keyword

    def testCrcBeforeLock(self) -> None:
        """Extra info and L can appear in either order."""
        result = parseInf("$.BOOT FF1900 FF8023 000A00 CRC=ABCD L")

        assert result.locked is True
        assert result.crc == 0xABCD

    def testLockBeforeCrc(self) -> None:
        """L followed by CRC= is still parsed."""
        result = parseInf("$.BOOT FF1900 FF8023 000A00 L CRC=ABCD")

        assert result.locked is True
        assert result.crc == 0xABCD

    def testDeprecatedCrcSeparatedValue(self) -> None:
        """Deprecated CRC= with a space before the value is accepted."""
        result = parseInf("$.BOOT FF1900 FF8023 000A00 CRC= 1A2B")

        assert result.crc == 0x1A2B
        assert result.extra_info["CRC"] == "1A2B"


# =======================================================================
# parseInf - syntax 3 (name access) - ADFS Explorer directory form
# =======================================================================

class TestParseSyntax3:
    """ADFS Explorer directory form: name + symbolic access string."""

    def testSymbolicAccessOnly(self) -> None:
        """Name followed by symbolic access produces zero load/exec."""
        result = parseInf("$.GAMES WR")

        assert result.directory == "$"
        assert result.name == "GAMES"
        assert result.load_addr == 0
        assert result.exec_addr == 0
        assert result.length is None
        assert result.locked is False

    def testSymbolicAccessWithLocked(self) -> None:
        """Symbolic L flag in the access string sets locked."""
        result = parseInf("$.GAMES LWR")

        assert result.locked is True


# =======================================================================
# parseInf - name field splitting
# =======================================================================

class TestParseNameSplit:
    """Tests for dotted-name splitting into (directory, leaf)."""

    def testDfsSingleCharDirectory(self) -> None:
        """A single-character directory prefix is split at offset 1."""
        result = parseInf("T.MYPROG 000E00 008023 001400")

        assert result.directory == "T"
        assert result.name == "MYPROG"

    def testAdfsDottedPath(self) -> None:
        """An ADFS dotted path splits at the last dot."""
        result = parseInf("$.GAMES.ACTION.ELITE 001900 008023 002000")

        assert result.directory == "$.GAMES.ACTION"
        assert result.name == "ELITE"

    def testBareNameDefaultsToDollar(self) -> None:
        """A bare filename with no directory prefix defaults to $."""
        result = parseInf("BOOT 001900 008023 000A00")

        assert result.directory == "$"
        assert result.name == "BOOT"

    def testFullNameProperty(self) -> None:
        """fullName reconstructs the original directory.name form."""
        result = parseInf("T.MYPROG 001400 008023 000100")

        assert result.fullName == "T.MYPROG"


# =======================================================================
# parseInf - quoted strings with percent-encoding
# =======================================================================

class TestParseQuotedNames:
    """Quoted name fields allow any byte via percent-encoding."""

    def testQuotedNameWithSpace(self) -> None:
        """A name containing a space must be quoted in the source line."""
        result = parseInf('"$.HELLO WORLD" 001900 008023 000100')

        assert result.directory == "$"
        assert result.name == "HELLO WORLD"

    def testQuotedNameWithPercentEscape(self) -> None:
        """%XX sequences inside quoted strings decode to byte values."""
        result = parseInf('"$.NAME%20WITH%20SPACES" 000000 000000 000100')

        assert result.name == "NAME WITH SPACES"

    def testQuotedNameWithControlByte(self) -> None:
        """Percent-encoded 0x06 appears as the literal byte in the name."""
        result = parseInf('"$.BLANK%06" 000000 000000 000100')

        assert result.name == "BLANK\x06"

    def testQuotedNameWithLiteralQuote(self) -> None:
        """A percent-encoded DQUOTE survives the round-trip through parsing."""
        result = parseInf('"$.SAY%22HI%22" 000000 000000 000100')

        assert result.name == 'SAY"HI"'

    def testUnterminatedQuoteRaises(self) -> None:
        """A quoted string with no closing DQUOTE raises ValueError."""
        with pytest.raises(ValueError, match="[Uu]nterminated"):
            parseInf('"$.BOOT 000000 000000 000100')


# =======================================================================
# parseInf - miscellaneous
# =======================================================================

class TestParseMisc:
    """Whitespace handling, tape markers, empty input, extra_info."""

    def testMixedWhitespace(self) -> None:
        """Tabs and multiple spaces between fields are tolerated."""
        result = parseInf("$.BOOT\tFF1900  FF8023\t\t000A00")

        assert result.load_addr == 0xFFFF1900
        assert result.length == 0x000A00

    def testLowercaseHex(self) -> None:
        """Lowercase hex digits are accepted."""
        result = parseInf("$.BOOT ff1900 ff8023 000a00")

        assert result.load_addr == 0xFFFF1900
        assert result.exec_addr == 0xFFFF8023
        assert result.length == 0x000A00

    def testTapePrefixSkipped(self) -> None:
        """The deprecated TAPE prefix is skipped and the rest parses."""
        result = parseInf("TAPE $.BOOT FF1900 FF8023 000A00")

        assert result.name == "BOOT"
        assert result.load_addr == 0xFFFF1900

    def testNextTapeMarkerStopsParsing(self) -> None:
        """The NEXT tape marker ends parsing before remaining tokens."""
        result = parseInf("$.BOOT FF1900 FF8023 000A00 NEXT $.NEXTFILE")

        assert result.length == 0x000A00

    def testEmptyLineRaises(self) -> None:
        """An empty line raises ValueError."""
        with pytest.raises(ValueError, match="[Ee]mpty"):
            parseInf("")

    def testTrailingWhitespace(self) -> None:
        """Trailing whitespace and end-of-line characters are harmless."""
        result = parseInf("$.BOOT FF1900 FF8023 000A00\r\n")

        assert result.name == "BOOT"
        assert result.length == 0x000A00


# =======================================================================
# formatInf - syntax 1 output
# =======================================================================

class TestFormatSyntax1:
    """Writer emits syntax 1 with 8-digit hex and a hex access byte."""

    def testBasicFormat(self) -> None:
        """Unlocked entry: 8-digit hex, zero access byte."""
        line = formatInf("$", "BOOT", 0xFFFF1900, 0xFFFF8023, 0x00000A00)

        assert line == "$.BOOT FFFF1900 FFFF8023 00000A00 00"

    def testLockedSetsAccessBit(self) -> None:
        """Locked true emits 08 as the access byte."""
        line = formatInf(
            "$", "BOOT",
            0xFFFF1900, 0xFFFF8023, 0x00000A00,
            access_byte=0x08,
        )

        assert line == "$.BOOT FFFF1900 FFFF8023 00000A00 08"

    def testZeroAddresses(self) -> None:
        """All-zero values pad to 8 digits."""
        line = formatInf("$", "DATA", 0, 0, 0x100)

        assert line == "$.DATA 00000000 00000000 00000100 00"

    def testAdfsDottedDirectory(self) -> None:
        """Nested ADFS directories emit the full dotted path."""
        line = formatInf("$.GAMES", "ELITE", 0x1900, 0x8023, 0x2000)

        assert line == "$.GAMES.ELITE 00001900 00008023 00002000 00"

    def testExtraInfoPassThrough(self) -> None:
        """extra_info dict is appended after the access byte."""
        line = formatInf(
            "$", "BOOT",
            0xFFFF1900, 0xFFFF8023, 0x00000A00,
            extra_info={"CRC": "1A2B", "OPT4": "3"},
        )

        assert line == (
            "$.BOOT FFFF1900 FFFF8023 00000A00 00 CRC=1A2B OPT4=3"
        )


# =======================================================================
# formatInf - name quoting
# =======================================================================

class TestFormatNameQuoting:
    """Names with non-safe bytes are quoted and percent-encoded."""

    def testSpaceTriggersQuoting(self) -> None:
        """A space in the name forces a quoted output (literal space OK)."""
        line = formatInf("$", "HELLO WORLD", 0, 0, 0)

        # Per spec, space is a legal literal byte inside a quoted string.
        # The important invariant is that the name field is wrapped in
        # DQUOTE so the space is not interpreted as a field separator.
        assert line.startswith('"$.HELLO WORLD"')

    def testControlByteEncoded(self) -> None:
        """A 0x06 byte in the name is percent-encoded inside quotes."""
        line = formatInf("$", "BLANK\x06", 0, 0, 0)

        assert line.startswith('"$.BLANK%06"')

    def testLiteralPercentEncoded(self) -> None:
        """A literal '%' in a name is encoded so round-trip is unambiguous."""
        line = formatInf("$", "50%OFF", 0, 0, 0)

        assert line.startswith('"$.50%25OFF"')

    def testLiteralQuoteEncoded(self) -> None:
        """A literal DQUOTE in a name becomes %22."""
        line = formatInf("$", 'SAY"HI', 0, 0, 0)

        assert line.startswith('"$.SAY%22HI"')

    def testSafeNameNotQuoted(self) -> None:
        """A printable-only name is emitted unquoted."""
        line = formatInf("$", "BOOT", 0, 0, 0)

        assert line.startswith("$.BOOT ")


# =======================================================================
# Round-trip: format -> parse -> compare
# =======================================================================

class TestRoundTrip:
    """Format then parse should recover the original fields."""

    def testUnlockedBasic(self) -> None:
        """A plain name round-trips through format and parse."""
        line = formatInf("$", "BOOT", 0xFFFF1900, 0xFFFF8023, 0x0A00)
        result = parseInf(line)

        assert result.directory == "$"
        assert result.name == "BOOT"
        assert result.load_addr == 0xFFFF1900
        assert result.exec_addr == 0xFFFF8023
        assert result.length == 0x0A00
        assert result.locked is False

    def testLockedEntry(self) -> None:
        """The locked flag survives a format-parse cycle."""
        line = formatInf("T", "PROG", 0x0E00, 0x8023, 0x1400, access_byte=0x08)
        result = parseInf(line)

        assert result.directory == "T"
        assert result.name == "PROG"
        assert result.locked is True

    def testNameWithSpace(self) -> None:
        """A space in the name round-trips via quoted+percent-encoded form."""
        line = formatInf("$", "HELLO WORLD", 0, 0, 0x100)
        result = parseInf(line)

        assert result.directory == "$"
        assert result.name == "HELLO WORLD"

    def testNameWithControlByte(self) -> None:
        """A 0x06 byte in the name round-trips losslessly."""
        line = formatInf("$", "BLANK\x06", 0, 0, 0x100)
        result = parseInf(line)

        assert result.name == "BLANK\x06"
        assert result.nameBytes == b"BLANK\x06"

    def testNameWithQuoteAndPercent(self) -> None:
        """DQUOTE and % bytes in a name both round-trip."""
        line = formatInf("$", 'A"B%C', 0, 0, 0x10)
        result = parseInf(line)

        assert result.name == 'A"B%C'

    def testExtraInfoRoundTrip(self) -> None:
        """Extra KEY=value fields survive a format-parse cycle."""
        extra = {"CRC": "1A2B", "OPT4": "3"}
        line = formatInf("$", "BOOT", 0xFFFF1900, 0xFFFF8023, 0x0A00,
                         extra_info=extra)
        result = parseInf(line)

        assert result.extra_info == extra
        assert result.crc == 0x1A2B

    def testExtraInfoValueWithSpaceRoundTrip(self) -> None:
        """Extra info values containing spaces round-trip via %20 encoding."""
        extra = {"TITLE": "MY DISC"}
        line = formatInf("$", "BOOT", 0xFFFF1900, 0xFFFF8023, 0x0A00,
                         extra_info=extra)

        assert "MY%20DISC" in line

        result = parseInf(line)
        assert result.extra_info["TITLE"] == "MY DISC"

    def testExtraInfoValueWithPercentRoundTrip(self) -> None:
        """A literal % in an extra info value round-trips via %25 encoding."""
        extra = {"TITLE": "100%"}
        line = formatInf("$", "BOOT", 0, 0, 0x100, extra_info=extra)

        result = parseInf(line)
        assert result.extra_info["TITLE"] == "100%"

    def testDfsNameWithDotsRoundTrip(self) -> None:
        """A DFS name containing literal dots stays together on round-trip."""
        line = formatInf("$", "B1.1", 0xFFFF1900, 0xFFFF8023, 0x100)
        result = parseInf(line)

        assert result.directory == "$"
        assert result.name == "B1.1"
        assert result.fullName == "$.B1.1"

    def testDfsNameWithDotsNotAmbiguousWithAdfs(self) -> None:
        """A DFS '$.B1.1' round-trip does not collapse into ADFS '$.B1' dir."""
        line = formatInf("$", "FOO.BAR", 0, 0, 0x10)

        # The leaf dot must have been escaped so the reader cannot
        # mistake it for an ADFS directory boundary.
        assert "%2E" in line or '"' in line

        result = parseInf(line)
        assert result.directory == "$"
        assert result.name == "FOO.BAR"

    def testAdfsDottedPathRoundTrip(self) -> None:
        """Nested ADFS directory path survives round-trip."""
        line = formatInf("$.GAMES.ACTION", "ELITE", 0x1900, 0x8023, 0x2000)
        result = parseInf(line)

        assert result.directory == "$.GAMES.ACTION"
        assert result.name == "ELITE"
        assert result.fullName == "$.GAMES.ACTION.ELITE"

    def testAllPrintableDirectoryChars(self) -> None:
        """Every printable ASCII directory byte 0x21-0x7E round-trips."""
        for code in range(0x21, 0x7F):
            d = chr(code)

            # '.' as a directory byte would be ambiguous with the
            # separator; skip it.
            if d == ".":
                continue

            line = formatInf(d, "FILE", 0x1000, 0x2000, 0x100)
            result = parseInf(line)

            assert result.directory == d, (
                f"Directory byte 0x{code:02X} did not round-trip"
            )
            assert result.name == "FILE"


# =======================================================================
# Access byte (full 8-bit support)
# =======================================================================

class TestAccessByte:
    """Full 8-bit access byte through parse, format, and InfData."""

    def testParseHexAccessBytePreservesAllBits(self) -> None:
        """Syntax 1 hex access byte preserves all 8 bits, not just bit 3."""
        result = parseInf("$.FILE FFFF1900 FFFF8023 00000A00 FF")

        assert result.access_byte == 0xFF

    def testParseAdfsSymbolicAccess(self) -> None:
        """Syntax 3 ADFS symbolic access sets correct bits."""
        result = parseInf("$.FILE RWL")

        assert result.access_byte == 0x0B  # R=01 | W=02 | L=08

    def testParseAdfsFullSymbolicAccess(self) -> None:
        """All eight ADFS symbolic bits are decoded correctly."""
        result = parseInf("$.FILE RWELrwel")

        assert result.access_byte == 0xFF

    def testParseDfsLockedSetsOnlyBit3(self) -> None:
        """DFS 'L' shorthand sets only bit 3 of the access byte."""
        result = parseInf("$.BOOT FFFF1900 FFFF8023 L")

        assert result.access_byte == 0x08

    def testParseUnlockedAccessByteIsZero(self) -> None:
        """An unlocked syntax 1 entry has access_byte 0."""
        result = parseInf("$.BOOT FFFF1900 FFFF8023 00000A00 00")

        assert result.access_byte == 0x00

    def testLockedPropertyReadsBit3(self) -> None:
        """The locked property returns True when bit 3 is set."""
        result = parseInf("$.FILE FFFF1900 FFFF8023 00000A00 3F")

        assert result.locked is True
        assert result.access_byte == 0x3F

    def testLockedPropertyFalseWhenBit3Clear(self) -> None:
        """The locked property returns False even with other bits set."""
        result = parseInf("$.FILE FFFF1900 FFFF8023 00000A00 37")

        assert result.locked is False
        assert result.access_byte == 0x37

    def testFormatEmitsFullAccessByte(self) -> None:
        """formatInf emits all 8 bits as a 2-digit hex field."""
        line = formatInf("$", "FILE", 0x1900, 0x8023, 0x100, access_byte=0xFF)

        assert line.endswith("FF")

    def testFormatEmitsAdfsOwnerBits(self) -> None:
        """ADFS owner RWE bits (no lock) emit as 07."""
        line = formatInf("$", "FILE", 0x1900, 0x8023, 0x100, access_byte=0x07)

        assert "07" in line

    def testRoundTripAdfsAccessByte(self) -> None:
        """A full ADFS access byte survives format -> parse."""
        original_access = 0xB7  # RWE owner + rwe others + L (bit 4 clear)
        line = formatInf("$", "FILE", 0x1900, 0x8023, 0x100,
                         access_byte=original_access)
        result = parseInf(line)

        assert result.access_byte == original_access

    def testRoundTripDfsLockedAccessByte(self) -> None:
        """DFS locked (0x08) survives format -> parse."""
        line = formatInf("$", "BOOT", 0xFFFF1900, 0xFFFF8023, 0x0A00,
                         access_byte=0x08)
        result = parseInf(line)

        assert result.access_byte == 0x08
        assert result.locked is True

    def testRoundTripDfsUnlockedAccessByte(self) -> None:
        """DFS unlocked (0x00) survives format -> parse."""
        line = formatInf("$", "BOOT", 0xFFFF1900, 0xFFFF8023, 0x0A00,
                         access_byte=0x00)
        result = parseInf(line)

        assert result.access_byte == 0x00
        assert result.locked is False

    def testRoundTripEveryBitPattern(self) -> None:
        """All 256 possible access byte values round-trip correctly."""
        for access in range(256):
            line = formatInf("$", "FILE", 0, 0, 0x100, access_byte=access)
            result = parseInf(line)

            assert result.access_byte == access, (
                f"Access byte 0x{access:02X} did not round-trip"
            )


# =======================================================================
# InfData.startSector
# =======================================================================

class TestStartSector:
    """The experimental X_START_SECTOR / START_SECTOR extra_info field."""

    def testAbsentReturnsNone(self) -> None:
        """An .inf with no start sector key returns None."""
        result = parseInf("$.BOOT FFFF1900 FFFF8023 00000A00 00")

        assert result.startSector is None

    def testExperimentalFormParses(self) -> None:
        """X_START_SECTOR alone returns the parsed integer."""
        result = parseInf(
            "$.BOOT FFFF1900 FFFF8023 00000A00 00 X_START_SECTOR=42"
        )

        assert result.startSector == 42

    def testPlainFormParses(self) -> None:
        """START_SECTOR alone returns the parsed integer."""
        result = parseInf(
            "$.BOOT FFFF1900 FFFF8023 00000A00 00 START_SECTOR=42"
        )

        assert result.startSector == 42

    def testBothPresentPrefersPlainAndWarns(self) -> None:
        """Both keys present: plain wins, a warning is emitted."""
        result = parseInf(
            "$.BOOT FFFF1900 FFFF8023 00000A00 00 "
            "START_SECTOR=7 X_START_SECTOR=99"
        )

        with pytest.warns(BeebToolsWarning, match="both"):
            value = result.startSector

        assert value == 7

    def testInvalidIntegerWarnsAndReturnsNone(self) -> None:
        """An unparseable value emits a warning and returns None."""
        result = parseInf(
            "$.BOOT FFFF1900 FFFF8023 00000A00 00 X_START_SECTOR=notanumber"
        )

        with pytest.warns(BeebToolsWarning, match="not a.*valid integer"):
            value = result.startSector

        assert value is None

    def testNegativeValueWarnsAndReturnsNone(self) -> None:
        """A negative integer emits a warning and returns None."""
        result = parseInf(
            "$.BOOT FFFF1900 FFFF8023 00000A00 00 X_START_SECTOR=-5"
        )

        with pytest.warns(BeebToolsWarning, match="negative"):
            value = result.startSector

        assert value is None

    def testZeroIsValid(self) -> None:
        """Sector 0 is a legal value; no warning, returns 0."""
        result = parseInf(
            "$.BOOT FFFF1900 FFFF8023 00000A00 00 X_START_SECTOR=0"
        )

        assert result.startSector == 0

    def testHexPrefixAccepted(self) -> None:
        """int(value, 0) accepts 0x-prefixed hex start sectors."""
        result = parseInf(
            "$.BOOT FFFF1900 FFFF8023 00000A00 00 X_START_SECTOR=0x10"
        )

        assert result.startSector == 16


# =======================================================================
# InfData.crc32
# =======================================================================

class TestCrc32Property:
    """The CRC32 extra_info field accessor."""

    def testAbsentReturnsNone(self) -> None:
        """No CRC32 key returns None without warning."""
        result = parseInf("$.FILE FFFF1900 FFFF8023 00000A00 00")

        assert result.crc32 is None

    def testValidHexReturnsInt(self) -> None:
        """A valid 8-digit hex CRC32 is returned as an integer."""
        result = parseInf(
            "$.FILE FFFF1900 FFFF8023 00000A00 00 CRC32=FEA8A821"
        )

        assert result.crc32 == 0xFEA8A821

    def testInvalidHexWarnsAndReturnsNone(self) -> None:
        """A non-hex CRC32 value emits a warning and returns None."""
        result = parseInf(
            "$.FILE FFFF1900 FFFF8023 00000A00 00 CRC32=NOTHEX"
        )

        with pytest.warns(BeebToolsWarning, match="CRC32.*not.*valid hex"):
            value = result.crc32

        assert value is None


# =======================================================================
# InfData.crc - warning on bad value
# =======================================================================

class TestCrcWarning:
    """The CRC property warns on unparseable values."""

    def testInvalidCrcWarnsAndReturnsNone(self) -> None:
        """A non-hex CRC value emits a warning and returns None."""
        result = parseInf(
            "$.FILE FFFF1900 FFFF8023 00000A00 00 CRC=ZZZZ"
        )

        with pytest.warns(BeebToolsWarning, match="CRC.*not.*valid hex"):
            value = result.crc

        assert value is None


# =======================================================================
# parseInf - malformed input robustness
# =======================================================================

class TestParseMalformed:
    """Parser warns on unrecognised tokens and malformed escapes."""

    def testUnrecognisedTokenWarns(self) -> None:
        """An unknown token emits a warning and is skipped."""
        with pytest.warns(BeebToolsWarning, match="unrecognised token"):
            result = parseInf(
                "$.BOOT FF1900 FF8023 000A00 00 BADSTUFF"
            )

        assert result.load_addr == 0xFFFF1900
        assert result.length == 0x000A00

    def testUnrecognisedTokenDoesNotBlockLaterFields(self) -> None:
        """Tokens after an unknown one are still parsed."""
        with pytest.warns(BeebToolsWarning, match="unrecognised token"):
            result = parseInf(
                "$.BOOT FF1900 FF8023 000A00 00 JUNK CRC=1A2B"
            )

        assert result.extra_info["CRC"] == "1A2B"
        assert result.crc == 0x1A2B

    def testBadHexInFieldPositionWarns(self) -> None:
        """Non-hex in a hex position warns and skips, remaining fields shift."""
        with pytest.warns(BeebToolsWarning, match="unrecognised token"):
            result = parseInf("$.BOOT GGGGGGGG FF8023 000A00")

        # GGGGGGGG skipped; FF8023 and 000A00 become load and exec.
        assert result.load_addr == 0xFFFF8023
        assert result.exec_addr == 0x000A00

    def testEqualsWithNoKeyWarns(self) -> None:
        """=value with no key is not valid extra info; warns and skips."""
        with pytest.warns(BeebToolsWarning, match="unrecognised token"):
            result = parseInf(
                "$.BOOT FF1900 FF8023 000A00 00 =value"
            )

        assert result.load_addr == 0xFFFF1900

    def testHyphenInKeyWarns(self) -> None:
        """KEY-NAME=value has an invalid key; warns and skips."""
        with pytest.warns(BeebToolsWarning, match="unrecognised token"):
            result = parseInf(
                "$.BOOT FF1900 FF8023 000A00 00 KEY-NAME=value"
            )

        assert "KEY-NAME" not in result.extra_info

    def testInvalidPercentEscapeWarns(self) -> None:
        """A %GG sequence warns and is treated as literal."""
        with pytest.warns(BeebToolsWarning, match="Invalid percent-escape"):
            result = parseInf('"$.TEST%GG" FF1900 FF8023 000100')

        assert result.name == "TEST%GG"

    def testTruncatedPercentEscapeWarns(self) -> None:
        """A trailing % warns and is treated as literal."""
        with pytest.warns(BeebToolsWarning, match="Truncated percent-escape"):
            result = parseInf('"$.TEST%" FF1900 FF8023 000100')

        assert result.name == "TEST%"

    def testNameOnlyParsesWithoutError(self) -> None:
        """A line with just a name is valid (directory .inf use case)."""
        result = parseInf("$.GAMES")

        assert result.directory == "$"
        assert result.name == "GAMES"
        assert result.load_addr == 0
        assert result.exec_addr == 0
        assert result.length is None


# =======================================================================
# Smoke tests: real-world .inf files and spec-derived edge cases
# =======================================================================

# Real .inf lines downloaded from dmcoles/TrailBlazer_BBC_Micro_Conversion
# (GitHub). These are minimal syntax 2 lines with 6-digit hex, the most
# common format in the wild.
_TRAILBLAZER_INFS = [
    ("$.!BOOT   003000 003000 000008", "$", "!BOOT", 0x003000, 0x003000, 0x000008),
    ("$.DEBUG   000053 000053 000004", "$", "DEBUG", 0x000053, 0x000053, 0x000004),
    ("$.GDATA   000000 000000 0007F5", "$", "GDATA", 0x000000, 0x000000, 0x0007F5),
    ("$.GHEIGHT 002000 000700 0000E0", "$", "GHEIGHT", 0x002000, 0x000700, 0x0000E0),
    ("$.GPLOT   002000 000600 0000C4", "$", "GPLOT", 0x002000, 0x000600, 0x0000C4),
    ("$.SCORES  002000 002000 000030", "$", "SCORES", 0x002000, 0x002000, 0x000030),
    ("$.SCRLMOD 001C00 000500 000100", "$", "SCRLMOD", 0x001C00, 0x000500, 0x000100),
    ("$.TMAIN   001100 001100 0016B1", "$", "TMAIN", 0x001100, 0x001100, 0x0016B1),
    ("$.TRAIL   001D00 001D00 0008BF", "$", "TRAIL", 0x001D00, 0x001D00, 0x0008BF),
    ("$.TSCR    002000 002000 001180", "$", "TSCR", 0x002000, 0x002000, 0x001180),
    ("$.LEVELA  002800 002800 00045E", "$", "LEVELA", 0x002800, 0x002800, 0x00045E),
    ("$.LEVELN  002800 002800 0004A9", "$", "LEVELN", 0x002800, 0x002800, 0x0004A9),
]

# Synthetic lines derived from the stardot inf_format spec, Harston
# extended format documentation, and known community tool output.
_SPEC_EDGE_CASES = [
    # BeebEm style: padded name, 6-digit hex, no access
    ("A.DARE    004000 006691 002700",
     "A", "DARE", 0x004000, 0x006691, 0x002700, False),
    # Syntax 1 full 8-digit with lock and CRC
    ("$.ELITE FFFF1900 FFFF8023 00001273 08 CRC=4D2E",
     "$", "ELITE", 0xFFFF1900, 0xFFFF8023, 0x00001273, True),
    # Syntax 2 with Locked keyword and CRC
    ("$.FOO FF1900 FF8023 Locked CRC=AB7A",
     "$", "FOO", 0xFFFF1900, 0xFFFF8023, None, True),
    # Syntax 2 with just L
    ("B.LOADER 000E00 008023 000400 L",
     "B", "LOADER", 0x000E00, 0x008023, 0x000400, True),
    # 6-digit FF sign extension
    ("$.PROG FF0E00 FF8023 000100",
     "$", "PROG", 0xFFFF0E00, 0xFFFF8023, 0x000100, False),
    # 6-digit non-FF stays as-is
    ("$.DATA 001900 008023 000200",
     "$", "DATA", 0x001900, 0x008023, 0x000200, False),
    # Syntax 3 ADFS Explorer directory
    ("$.GAMES WR",
     "$", "GAMES", 0, 0, None, False),
    # Syntax 3 with lock
    ("$.UTILS LWR",
     "$", "UTILS", 0, 0, None, True),
    # ADFS nested path
    ("$.GAMES.ACTION.ELITE 00001900 00008023 00002000 00",
     "$.GAMES.ACTION", "ELITE", 0x00001900, 0x00008023, 0x00002000, False),
    # TAPE prefix (deprecated, must be tolerated)
    ("TAPE $.BOOT FF1900 FF8023 000A00",
     "$", "BOOT", 0xFFFF1900, 0xFFFF8023, 0x000A00, False),
    # NEXT marker stops parsing
    ("$.BOOT FF1900 FF8023 000A00 NEXT $.NEXTFILE",
     "$", "BOOT", 0xFFFF1900, 0xFFFF8023, 0x000A00, False),
    # Harston extended: 10 hex fields (date/time/account after access)
    ("$.FILE FFFF1900 FFFF8023 00001273 33 7B23 123106 7B20 112708 0100 0040",
     "$", "FILE", 0xFFFF1900, 0xFFFF8023, 0x00001273, False),
    # Bare name defaults to $ directory
    ("BOOT 001900 008023 000A00",
     "$", "BOOT", 0x001900, 0x008023, 0x000A00, False),
    # Deprecated CRC= with space
    ("$.TEST FF1900 FF8023 000100 CRC= 1A2B",
     "$", "TEST", 0xFFFF1900, 0xFFFF8023, 0x000100, False),
    # Root directory inf with extra info
    ("$ 00000000 00000000 00000000 00 TITLE=MY%20DISC OPT=3",
     "$", "", 0, 0, 0, False),
    # Tab-separated fields
    ("$.DATA\tFF1900\tFF8023\t000A00",
     "$", "DATA", 0xFFFF1900, 0xFFFF8023, 0x000A00, False),
    # Lowercase hex
    ("$.PROG ff0e00 ff8023 000100 00",
     "$", "PROG", 0xFFFF0E00, 0xFFFF8023, 0x000100, False),
    # Trailing CRLF
    ("$.BOOT FF1900 FF8023 000A00\r\n",
     "$", "BOOT", 0xFFFF1900, 0xFFFF8023, 0x000A00, False),
    # Multiple spaces between fields
    ("$.BOOT    FF1900    FF8023    000A00",
     "$", "BOOT", 0xFFFF1900, 0xFFFF8023, 0x000A00, False),
]


class TestSmokeRealWorld:
    """Smoke tests against real .inf files from public BBC Micro archives."""

    @pytest.mark.parametrize(
        "line,exp_dir,exp_name,exp_load,exp_exec,exp_len",
        _TRAILBLAZER_INFS,
        ids=[t[0].split()[0] for t in _TRAILBLAZER_INFS],
    )
    def testTrailBlazerInfs(
        self,
        line: str,
        exp_dir: str,
        exp_name: str,
        exp_load: int,
        exp_exec: int,
        exp_len: int,
    ) -> None:
        """Real .inf from dmcoles/TrailBlazer_BBC_Micro_Conversion (GitHub)."""
        result = parseInf(line)

        assert result.directory == exp_dir
        assert result.name == exp_name
        assert result.load_addr == exp_load
        assert result.exec_addr == exp_exec
        assert result.length == exp_len


class TestSmokeSpecEdgeCases:
    """Synthetic .inf lines derived from the stardot spec and community tools."""

    @pytest.mark.parametrize(
        "line,exp_dir,exp_name,exp_load,exp_exec,exp_len,exp_locked",
        _SPEC_EDGE_CASES,
        ids=[t[0][:40].strip() for t in _SPEC_EDGE_CASES],
    )
    def testSpecEdgeCase(
        self,
        line: str,
        exp_dir: str,
        exp_name: str,
        exp_load: int,
        exp_exec: int,
        exp_len: Optional[int],
        exp_locked: bool,
    ) -> None:
        """Edge case derived from stardot spec or documented tool output."""
        result = parseInf(line)

        assert result.directory == exp_dir
        assert result.name == exp_name
        assert result.load_addr == exp_load
        assert result.exec_addr == exp_exec
        assert result.length == exp_len
        assert result.locked is exp_locked
