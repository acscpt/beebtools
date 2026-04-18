# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Format-specific translation for the stardot .inf access byte.

Every disc format stores its access bits in a format-native on-disc
layout. The stardot .inf interchange spec defines its own bit layout
that is not identical to any one format's native layout (notably, the
L bit lives at bit 3 in stardot and at bit 2 on ADFS disc).

Translation between on-disc and stardot bytes is done with
:func:`functools.singledispatch`: per-format handlers keyed on the
concrete ``DiscEntry`` subclass let callers dispatch by runtime type
without isinstance ladders in orchestration code.

Scope: access-byte translation only. All other .inf structure (name,
addresses, length, CRC, extras, serialisation) stays in ``inf.py``;
format engines (``adfs.py``, ``dfs.py``) stay agnostic of stardot.
"""

from functools import singledispatch

from .adfs import ADFSEntry
from .dfs import DFSEntry
from .entry import DiscEntry


# =======================================================================
# Generic dispatch stubs
# =======================================================================

@singledispatch
def toStardotAccess(entry: DiscEntry) -> int:
    """Return the stardot-spec access byte for a DiscEntry.

    Dispatches on the concrete DiscEntry subclass. Each disc format
    registers a translator that maps its native on-disc access layout
    to the stardot .inf layout.
    """

    raise NotImplementedError(
        f"no stardot access translator registered for {type(entry).__name__}"
    )


@singledispatch
def fromStardotAccess(entry: DiscEntry, stardot_byte: int) -> int:
    """Return the native on-disc access byte for a stardot-spec value.

    ``entry`` is the dispatch argument so the translator can be chosen
    by concrete DiscEntry subclass, and so format-specific translators
    can fold in flags that are not represented in stardot's access
    byte (for example, ADFS carries the D bit alongside the access
    bits on disc but the .inf spec has no equivalent).
    """

    raise NotImplementedError(
        f"no stardot access translator registered for {type(entry).__name__}"
    )


# =======================================================================
# ADFS <-> stardot
# =======================================================================
#
# ADFS on-disc access byte layout (Hugo directory entry, bit 7 of each
# of the ten name bytes):
#
#     bit 0 = R  (user read)
#     bit 1 = W  (user write)
#     bit 2 = L  (locked)
#     bit 3 = D  (directory)
#     bit 4 = E  (executable, user)
#     bit 5 = r  (public read)
#     bit 6 = w  (public write)
#     bit 7 = e  (public executable)
#
# Stardot .inf access byte layout:
#
#     bit 0 = R  bit 1 = W  bit 2 = E  bit 3 = L
#     bit 4 = r  bit 5 = w  bit 6 = e  bit 7 = l
#
# D has no stardot equivalent; it is folded back in on the reverse
# direction from ``entry.is_directory`` so round-tripping is lossless.

# (on-disc bit, stardot bit) pairs.
_ADFS_STARDOT_MAP = (
    (0x01, 0x01),  # R
    (0x02, 0x02),  # W
    (0x04, 0x08),  # L
    (0x10, 0x04),  # E
    (0x20, 0x10),  # r
    (0x40, 0x20),  # w
    (0x80, 0x40),  # e
)


@toStardotAccess.register
def _(entry: ADFSEntry) -> int:
    """Convert an ADFSEntry's on-disc access byte to stardot layout."""

    stardot = 0

    for disc_bit, stardot_bit in _ADFS_STARDOT_MAP:
        if entry.access & disc_bit:
            stardot |= stardot_bit

    return stardot


@fromStardotAccess.register
def _(entry: ADFSEntry, stardot_byte: int) -> int:
    """Convert a stardot access byte to an ADFS on-disc access byte.

    Reapplies the D bit from ``entry.is_directory`` since stardot has
    no directory bit of its own.
    """

    disc = 0

    for disc_bit, stardot_bit in _ADFS_STARDOT_MAP:
        if stardot_byte & stardot_bit:
            disc |= disc_bit

    if entry.is_directory:
        disc |= 0x08

    return disc


# =======================================================================
# DFS <-> stardot
# =======================================================================
#
# DFS has a single-bit access model (locked only). On disc the lock is
# carried by bit 7 of a filename character byte; the bit is hoisted to
# DFSEntry.locked during parsing. The stardot .inf spec places L at
# bit 3 (0x08); translation is a two-value mapping.

_STARDOT_L = 0x08


@toStardotAccess.register
def _(entry: DFSEntry) -> int:
    """Return stardot L bit if the DFS entry is locked, else 0."""

    return _STARDOT_L if entry.locked else 0x00


@fromStardotAccess.register
def _(entry: DFSEntry, stardot_byte: int) -> int:
    """Return the DFS lock bit (0x08) if the stardot L bit is set.

    The returned value is not the on-disc bit-7 placement used in the
    DFS catalogue sector; callers that need the locked state should
    read it as ``bool(result & 0x08)`` and set ``DFSEntry.locked``
    accordingly.
    """

    return _STARDOT_L if (stardot_byte & _STARDOT_L) else 0x00
