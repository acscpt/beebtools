# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for disc.py orchestration helpers.

Focused on `classifyFileType`, which lives in disc.py as file-level
orchestration that combines DiscEntry metadata with BASIC content
sniffers to produce a FileType classification. The primitives it
calls (`looksLikeTokenizedBasic`, `looksLikePlainText`,
`basicProgramSize`) are tested in test_basic.py.
"""

from beebtools import FileType, classifyFileType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeEntry:
    """Minimal DiscEntry stand-in for classifier tests."""

    def __init__(self, name: str = "TEST", load_addr: int = 0,
                 exec_addr: int = 0, length: int = 0, locked: bool = False,
                 is_basic: bool = False, is_directory: bool = False):
        self.name = name
        self.load_addr = load_addr
        self.exec_addr = exec_addr
        self.length = length
        self.locked = locked
        self._is_basic = is_basic
        self._is_directory = is_directory

    @property
    def fullName(self) -> str:
        return f"$.{self.name}"

    @property
    def isBasic(self) -> bool:
        return self._is_basic

    @property
    def isDirectory(self) -> bool:
        return self._is_directory

    def __fspath__(self) -> str:
        return self.name


def makeBasicProgram(*line_contents: bytes) -> bytes:
    """Build a minimal tokenized BASIC program from line content bytes.

    Each argument is the raw content of one line. Lines are numbered
    10, 20, 30, etc. The 0x0D-hi-lo-len header and end-of-program
    marker are added automatically.
    """
    result = bytearray()
    for i, content in enumerate(line_contents, start=1):
        linenum = i * 10
        hi = (linenum >> 8) & 0xFF
        lo = linenum & 0xFF
        linelen = 4 + len(content)
        result.extend([0x0D, hi, lo, linelen])
        result.extend(content)

    # End-of-program marker.
    result.extend([0x0D, 0xFF])
    return bytes(result)


# ---------------------------------------------------------------------------
# classifyFileType
# ---------------------------------------------------------------------------

class TestClassifyFileType:
    """Tests for file classification by metadata + content inspection."""

    def testPureBasic(self) -> None:
        """BASIC exec address + valid tokenized data classifies as BASIC."""
        data = makeBasicProgram(b"\xF1\"Hi\"")
        entry = FakeEntry(is_basic=True, length=len(data))
        assert classifyFileType(entry, data) is FileType.BASIC

    def testBasicPlusMachineCode(self) -> None:
        """BASIC exec + trailing binary classifies as BASIC_MC."""
        basic = makeBasicProgram(b"\xF1\"Hi\"")
        # Append 32 bytes of machine code (well past the 16-byte threshold).
        data = basic + bytes(32)
        entry = FakeEntry(is_basic=True, length=len(data))
        assert classifyFileType(entry, data) is FileType.BASIC_MC

    def testBasicExecButNotTokenized(self) -> None:
        """Branch 1 of BASIC_ISH: BASIC exec, non-tokenized content."""
        data = b"REM This is plain text\r\n"
        entry = FakeEntry(is_basic=True, length=len(data))
        assert classifyFileType(entry, data) is FileType.BASIC_ISH

    def testTokenizedWithoutBasicExec(self) -> None:
        """Branch 2 of BASIC_ISH: non-BASIC exec, tokenized content.

        This is the real-world 'include file' case - a BASIC snippet
        saved with `*SAVE` and an explicit non-standard exec address
        so that `*RUN`/`CHAIN` will not work. `LOAD` still does.
        """
        data = makeBasicProgram(b"\xF1\"Hi\"")
        entry = FakeEntry(is_basic=False, length=len(data))
        assert classifyFileType(entry, data) is FileType.BASIC_ISH

    def testTokenizedPlusMcWithoutBasicExec(self) -> None:
        """Tokenized + trailing binary still classifies as BASIC_MC.

        The non-BASIC exec address does not matter when the content
        structure is unambiguous.
        """
        basic = makeBasicProgram(b"\xF1\"Hi\"")
        data = basic + bytes(32)
        entry = FakeEntry(is_basic=False, length=len(data))
        assert classifyFileType(entry, data) is FileType.BASIC_MC

    def testPlainTextFile(self) -> None:
        """A plain ASCII text file classifies as TEXT."""
        data = b"Hello World\r\n"
        entry = FakeEntry(is_basic=False, length=len(data))
        assert classifyFileType(entry, data) is FileType.TEXT

    def testBinaryFile(self) -> None:
        """A file with high-bit bytes and no structure classifies as BINARY."""
        data = bytes(range(256))
        entry = FakeEntry(is_basic=False, length=len(data))
        assert classifyFileType(entry, data) is FileType.BINARY


# ---------------------------------------------------------------------------
# FileType enum
# ---------------------------------------------------------------------------

class TestFileTypeEnum:
    """Tests for the FileType enum surface."""

    def testStrRendersDisplayValue(self) -> None:
        """str(member) and f-string use the historical display string."""
        assert str(FileType.BASIC) == "BASIC"
        assert str(FileType.BASIC_MC) == "BASIC+MC"
        assert str(FileType.BASIC_ISH) == "BASIC?"
        assert str(FileType.TEXT) == "TEXT"
        assert str(FileType.BINARY) == "BINARY"
        assert f"{FileType.BASIC_ISH}" == "BASIC?"

    def testValueMatchesDisplay(self) -> None:
        """The .value attribute exposes the same display string."""
        assert FileType.BASIC.value == "BASIC"
        assert FileType.BASIC_MC.value == "BASIC+MC"
        assert FileType.BASIC_ISH.value == "BASIC?"
        assert FileType.TEXT.value == "TEXT"
        assert FileType.BINARY.value == "BINARY"
