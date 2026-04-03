# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for the BootOption enum and its parse() factory method."""

import pytest

from beebtools import BootOption


class TestBootOptionParse:
    """Direct tests for BootOption.parse() - layers 38-48 of boot.py."""

    def testParseByNameOff(self) -> None:
        """Parsing 'OFF' returns BootOption.OFF."""
        assert BootOption.parse("OFF") == BootOption.OFF

    def testParseByNameLoad(self) -> None:
        """Parsing 'LOAD' returns BootOption.LOAD."""
        assert BootOption.parse("LOAD") == BootOption.LOAD

    def testParseByNameRun(self) -> None:
        """Parsing 'RUN' returns BootOption.RUN."""
        assert BootOption.parse("RUN") == BootOption.RUN

    def testParseByNameExec(self) -> None:
        """Parsing 'EXEC' returns BootOption.EXEC."""
        assert BootOption.parse("EXEC") == BootOption.EXEC

    def testParseByNameCaseInsensitive(self) -> None:
        """Name matching is case-insensitive."""
        assert BootOption.parse("off") == BootOption.OFF
        assert BootOption.parse("run") == BootOption.RUN
        assert BootOption.parse("Exec") == BootOption.EXEC
        assert BootOption.parse("lOaD") == BootOption.LOAD

    def testParseByNumber0(self) -> None:
        """Parsing '0' returns BootOption.OFF."""
        assert BootOption.parse("0") == BootOption.OFF

    def testParseByNumber1(self) -> None:
        """Parsing '1' returns BootOption.LOAD."""
        assert BootOption.parse("1") == BootOption.LOAD

    def testParseByNumber2(self) -> None:
        """Parsing '2' returns BootOption.RUN."""
        assert BootOption.parse("2") == BootOption.RUN

    def testParseByNumber3(self) -> None:
        """Parsing '3' returns BootOption.EXEC."""
        assert BootOption.parse("3") == BootOption.EXEC

    def testParseInvalidNumber(self) -> None:
        """An out-of-range numeric string raises ValueError."""
        with pytest.raises(ValueError, match="invalid boot option"):
            BootOption.parse("5")

    def testParseInvalidName(self) -> None:
        """An unrecognised name raises ValueError."""
        with pytest.raises(ValueError, match="invalid boot option"):
            BootOption.parse("INVALID")

    def testParseEmptyString(self) -> None:
        """An empty string raises ValueError."""
        with pytest.raises(ValueError, match="invalid boot option"):
            BootOption.parse("")

    def testParseNegativeNumber(self) -> None:
        """A negative number raises ValueError."""
        with pytest.raises(ValueError, match="invalid boot option"):
            BootOption.parse("-1")


class TestBootOptionEnum:
    """Basic enum sanity checks."""

    def testValuesAreInts(self) -> None:
        """BootOption members are usable as plain integers."""
        assert int(BootOption.OFF) == 0
        assert int(BootOption.EXEC) == 3

    def testMemberCount(self) -> None:
        """There are exactly four boot options."""
        assert len(BootOption) == 4
