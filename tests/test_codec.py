# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for the BBC Micro 7-bit ASCII codec."""

from __future__ import annotations

import codecs
import io
import tempfile
import os

import pytest

from beebtools.codec import registerCodec

# Ensure the codec is registered before any tests run.
registerCodec()


# =======================================================================
# Basic decode
# =======================================================================

class TestDecode:
    """Tests for bytes.decode('bbc')."""

    def testPlainAscii(self) -> None:
        """Pure 7-bit ASCII bytes decode unchanged."""
        assert b"HELLO".decode("bbc") == "HELLO"

    def testEmptyBytes(self) -> None:
        """Empty input produces empty string."""
        assert b"".decode("bbc") == ""

    def testBit7Stripped(self) -> None:
        """Bit 7 is masked off before decoding."""
        # 0xC8 = 'H' (0x48) with bit 7 set
        # 0xC5 = 'E' (0x45) with bit 7 set
        data = bytes([0xC8, 0xC5, 0x4C, 0x4C, 0x4F])
        assert data.decode("bbc") == "HELLO"

    def testAllBitsSet(self) -> None:
        """0xFF decodes to 0x7F (DEL character)."""
        assert bytes([0xFF]).decode("bbc") == "\x7f"

    def testNulByte(self) -> None:
        """0x00 and 0x80 both decode to NUL."""
        assert bytes([0x00]).decode("bbc") == "\x00"
        assert bytes([0x80]).decode("bbc") == "\x00"

    def testMixedHighLow(self) -> None:
        """Bytes with and without bit 7 set are mixed correctly."""
        # "Ab" with bit 7 set on 'A' only
        data = bytes([0xC1, 0x62])
        assert data.decode("bbc") == "Ab"

    def testControlCharacters(self) -> None:
        """Control characters (0x01-0x1F) pass through unchanged."""
        data = bytes([0x0D, 0x0A, 0x09])
        assert data.decode("bbc") == "\r\n\t"

    def testControlCharsWithBit7(self) -> None:
        """Control characters with bit 7 set are masked to their base value."""
        # 0x8D = 0x0D with bit 7
        assert bytes([0x8D]).decode("bbc") == "\r"

    def testFullRange(self) -> None:
        """Every byte 0x00-0xFF decodes to the corresponding 0x00-0x7F char."""
        for i in range(256):
            result = bytes([i]).decode("bbc")
            assert result == chr(i & 0x7F)


# =======================================================================
# Basic encode
# =======================================================================

class TestEncode:
    """Tests for str.encode('bbc')."""

    def testPlainAscii(self) -> None:
        """7-bit ASCII encodes unchanged."""
        assert "HELLO".encode("bbc") == b"HELLO"

    def testEmptyString(self) -> None:
        """Empty string produces empty bytes."""
        assert "".encode("bbc") == b""

    def testLowercase(self) -> None:
        """Lowercase letters encode correctly."""
        assert "hello".encode("bbc") == b"hello"

    def testSpacesAndDigits(self) -> None:
        """Spaces and digits encode correctly."""
        assert "A 1".encode("bbc") == b"A 1"

    def testNonAsciiStrict(self) -> None:
        """Non-ASCII characters raise UnicodeEncodeError in strict mode."""
        with pytest.raises(UnicodeEncodeError):
            "\u00e9".encode("bbc")

    def testNonAsciiReplace(self) -> None:
        """Non-ASCII characters are replaced with '?' in replace mode."""
        result = "\u00e9".encode("bbc", errors="replace")
        assert result == b"?"

    def testNonAsciiIgnore(self) -> None:
        """Non-ASCII characters are dropped in ignore mode."""
        result = "caf\u00e9".encode("bbc", errors="ignore")
        assert result == b"caf"


# =======================================================================
# Codec lookup
# =======================================================================

class TestCodecLookup:
    """Tests for codec registration and lookup."""

    def testLookupByName(self) -> None:
        """The codec can be found via codecs.lookup()."""
        info = codecs.lookup("bbc")
        assert info.name == "bbc"

    def testRegisterIdempotent(self) -> None:
        """Calling registerCodec() multiple times is harmless."""
        registerCodec()
        registerCodec()
        info = codecs.lookup("bbc")
        assert info.name == "bbc"

    def testUnknownCodecRaisesError(self) -> None:
        """A bogus codec name still raises LookupError (our search returns None)."""
        with pytest.raises(LookupError):
            codecs.lookup("bbc-nonexistent-xxx")


# =======================================================================
# File I/O with the codec
# =======================================================================

class TestFileIO:
    """Tests for using the bbc codec with open()."""

    def testReadFile(self) -> None:
        """Reading a file with encoding='bbc' strips bit 7."""
        data = bytes([0xC8, 0xC5, 0x4C, 0x4C, 0x4F])
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        try:
            with open(tmp_path, "r", encoding="bbc") as f:
                assert f.read() == "HELLO"
        finally:
            os.unlink(tmp_path)

    def testWriteFile(self) -> None:
        """Writing a file with encoding='bbc' produces 7-bit clean bytes."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with open(tmp_path, "w", encoding="bbc") as f:
                f.write("HELLO")

            with open(tmp_path, "rb") as f:
                assert f.read() == b"HELLO"
        finally:
            os.unlink(tmp_path)

    def testRoundTrip(self) -> None:
        """Data written and read back through the codec is preserved."""
        text = "$.BOOT FF1900 FF8023"
        with tempfile.NamedTemporaryFile(delete=False, mode="w", encoding="bbc") as tmp:
            tmp.write(text)
            tmp_path = tmp.name

        try:
            with open(tmp_path, "r", encoding="bbc") as f:
                assert f.read() == text
        finally:
            os.unlink(tmp_path)


# =======================================================================
# Incremental encoder/decoder
# =======================================================================

class TestIncremental:
    """Tests for incremental encoding and decoding."""

    def testIncrementalDecode(self) -> None:
        """IncrementalDecoder processes chunks correctly."""
        decoder = codecs.getincrementaldecoder("bbc")()
        assert decoder.decode(b"HE") == "HE"
        assert decoder.decode(b"LLO", final=True) == "LLO"

    def testIncrementalEncode(self) -> None:
        """IncrementalEncoder processes chunks correctly."""
        encoder = codecs.getincrementalencoder("bbc")()
        assert encoder.encode("HE") == b"HE"
        assert encoder.encode("LLO", final=True) == b"LLO"

    def testIncrementalDecodeBit7(self) -> None:
        """IncrementalDecoder strips bit 7 across chunks."""
        decoder = codecs.getincrementaldecoder("bbc")()
        result = decoder.decode(bytes([0xC8, 0xC5]), False)
        result += decoder.decode(bytes([0x4C, 0x4C, 0x4F]), True)
        assert result == "HELLO"


# =======================================================================
# Stream reader/writer
# =======================================================================

class TestStream:
    """Tests for StreamReader and StreamWriter."""

    def testStreamReader(self) -> None:
        """StreamReader decodes from a byte stream."""
        data = bytes([0xC8, 0xC5, 0x4C, 0x4C, 0x4F])
        stream = io.BytesIO(data)
        reader = codecs.getreader("bbc")(stream)
        assert reader.read() == "HELLO"

    def testStreamWriter(self) -> None:
        """StreamWriter encodes to a byte stream."""
        stream = io.BytesIO()
        writer = codecs.getwriter("bbc")(stream)
        writer.write("HELLO")
        assert stream.getvalue() == b"HELLO"


# =======================================================================
# DFS-realistic scenarios
# =======================================================================

class TestDfsScenarios:
    """Tests for patterns found in real BBC Micro disc images."""

    def testLockedFileName(self) -> None:
        """DFS directory byte with bit 7 set (locked file) decodes correctly.

        In DFS, the directory byte has bit 7 set to indicate the file is locked.
        For example, '$' (0x24) becomes 0xA4 when locked.
        """
        raw = bytes([0xA4])  # '$' with lock bit
        assert raw.decode("bbc") == "$"

    def testCopyProtectedName(self) -> None:
        """Name bytes with bit 7 set (copy protection) decode correctly.

        Some disc images have bit 7 set on filename bytes as a crude
        copy protection scheme.
        """
        # "GAME" with all bits 7 set: G=0xC7, A=0xC1, M=0xCD, E=0xC5
        raw = bytes([0xC7, 0xC1, 0xCD, 0xC5])
        assert raw.decode("bbc") == "GAME"

    def testCatalogueNameWithPadding(self) -> None:
        """A padded DFS filename field decodes and strips correctly.

        DFS name fields are 7 bytes, space-padded. After decoding, the
        caller strips trailing spaces.
        """
        raw = bytes([0x42, 0x4F, 0x4F, 0x54, 0x20, 0x20, 0x20])
        assert raw.decode("bbc").rstrip() == "BOOT"

    def testTitleWithNulPadding(self) -> None:
        """A DFS disc title with NUL padding decodes and strips correctly."""
        raw = bytes([0x54, 0x45, 0x53, 0x54, 0x00, 0x00, 0x00, 0x00])
        assert raw.decode("bbc").rstrip("\x00 ") == "TEST"

    def testEncodeAndPadName(self) -> None:
        """Encoding and padding a DFS name field mirrors the format engine pattern."""
        name = "BOOT"
        encoded = name[:7].encode("bbc").ljust(7, b" ")
        assert encoded == b"BOOT   "
        assert len(encoded) == 7

    def testEncodeAndPadTitle(self) -> None:
        """Encoding and NUL-padding a DFS title field mirrors the format engine pattern."""
        title = "TEST"
        encoded = title[:12].encode("bbc").ljust(12, b"\x00")
        assert encoded == b"TEST\x00\x00\x00\x00\x00\x00\x00\x00"
        assert len(encoded) == 12
