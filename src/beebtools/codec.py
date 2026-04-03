# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""BBC Micro 7-bit ASCII codec.

The BBC Micro uses 7-bit ASCII. Bit 7 of each byte may be repurposed
by the filing system (e.g. the lock flag in DFS directory bytes, or
access bits in ADFS entry names) and must be masked off before
interpreting the byte as a character.

This module registers a Python codec named "bbc" so that standard
encode/decode and open() calls work transparently:

    raw_bytes.decode("bbc")           # mask bit 7, return str
    "MYPROG".encode("bbc")            # return 7-bit clean bytes
    open(path, encoding="bbc")        # read/write BBC-safe text

The codec is registered automatically when beebtools is imported.
"""

from __future__ import annotations

import codecs
from typing import Optional


# -----------------------------------------------------------------------
# Codec implementation
# -----------------------------------------------------------------------

def _bbcDecode(data: bytes, errors: str = "strict") -> tuple:
    """Decode bytes to str, masking bit 7 off each byte."""
    clean = bytes(b & 0x7F for b in data)
    return (clean.decode("ascii", errors), len(data))


def _bbcEncode(text: str, errors: str = "strict") -> tuple:
    """Encode str to bytes, masking bit 7 off each character."""
    raw = text.encode("ascii", errors)
    return (bytes(b & 0x7F for b in raw), len(text))


# Incremental codec classes for stream and file I/O.

class _BbcIncrementalDecoder(codecs.IncrementalDecoder):
    """Stateless incremental decoder for the bbc codec."""

    def decode(self, input: bytes, final: bool = False) -> str:
        return _bbcDecode(input)[0]


class _BbcIncrementalEncoder(codecs.IncrementalEncoder):
    """Stateless incremental encoder for the bbc codec."""

    def encode(self, input: str, final: bool = False) -> bytes:
        return _bbcEncode(input)[0]


class _BbcStreamReader(codecs.StreamReader):
    """Stream reader for the bbc codec."""
    decode = staticmethod(_bbcDecode)


class _BbcStreamWriter(codecs.StreamWriter):
    """Stream writer for the bbc codec."""
    encode = staticmethod(_bbcEncode)


# -----------------------------------------------------------------------
# Codec registration
# -----------------------------------------------------------------------

_CODEC_INFO = codecs.CodecInfo(
    name="bbc",
    encode=_bbcEncode,
    decode=_bbcDecode,
    incrementalencoder=_BbcIncrementalEncoder,
    incrementaldecoder=_BbcIncrementalDecoder,
    streamreader=_BbcStreamReader,
    streamwriter=_BbcStreamWriter,
)


def _bbcSearch(name: str) -> Optional[codecs.CodecInfo]:
    """Codec search function for the Python codec registry."""
    if name == "bbc":
        return _CODEC_INFO
    return None


def registerCodec() -> None:
    """Register the 'bbc' codec with Python's codec registry.

    Safe to call multiple times - codecs.register() ignores duplicates.
    """
    codecs.register(_bbcSearch)
