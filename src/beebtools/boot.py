# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Boot option defines the boot behaviour of a disc.

The 2-bit boot option is stored identically in both DFS catalogue
descriptor bytes and ADFS free space maps. This module provides the
enum so that both format engines can import it from a common
Contracts-layer module without lateral coupling.
"""

from enum import IntEnum


class BootOption(IntEnum):
    """The 2-bit boot option stored in disc catalogues.

    Member names match the labels used by the Acorn DFS ROM.  Because
    BootOption is an IntEnum, members work anywhere a plain int is
    expected (bitwise operations, comparisons, etc.).
    """

    OFF = 0
    LOAD = 1
    RUN = 2
    EXEC = 3

    @staticmethod
    def parse(value: str) -> "BootOption":
        """Convert a string to a BootOption.

        Accepts a numeric string ('0'-'3') or a name ('OFF', 'LOAD',
        'RUN', 'EXEC') case-insensitively.

        Raises:
            ValueError: If the value is not a valid boot option.
        """
        by_name = {m.name.lower(): m for m in BootOption}
        low = value.lower()

        if low in by_name:
            return by_name[low]

        try:
            return BootOption(int(value))
        except (ValueError, KeyError):
            names = ", ".join(m.name for m in BootOption)
            raise ValueError(
                f"invalid boot option '{value}' (choose from {names} or 0-3)"
            )
