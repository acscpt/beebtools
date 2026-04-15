# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Dialect instances consumed by the wopr tokenizer engine.

Each dialect is a `Dialect` instance carrying an ordered tuple of
`Keyword` rows. Engine code never reads from this module; it consumes
a `Dialect` passed by the caller.

Skeleton stage: BBC_BASIC_II is currently empty. Subsequent steps
populate the keyword table in BBC BASIC II ROM order so that
abbreviation precedence (P. -> PRINT, E. -> ENDPROC) is correct by
construction. BBC_BASIC_IV will follow the same shape, sourced from
the Master ROM.
"""

from .wopr import Dialect, Keyword


BBC_BASIC_II = Dialect(
    name="BBC BASIC II",
    keywords=(),
)
