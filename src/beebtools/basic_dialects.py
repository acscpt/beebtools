# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Dialect instances consumed by the sophie tokenizer engine.

Each dialect is a `Dialect` instance carrying an ordered tuple of
`Keyword` rows. The keyword bytes, names, and behaviour flags are
the single-source-of-truth records in `tokens.py`; this module does
two things on top of that data:

1. Translate `TokenSpec` records into engine-level `Keyword` rows.
2. Sort the keyword tuple into matching order: ROM-preferred
   abbreviation winners first, longest-first within each cluster.

The sort is a tokenizer concern (abbreviation resolution walks the
tuple in order), which is why it lives here and not in `tokens.py`.
"""

from typing import Tuple

from .tokens import (
    BBC_BASIC_II_TOKENS,
    BBC_BASIC_IV_TOKENS,
    TokenSpec,
)
from .sophie import Dialect, Keyword


def _toKeyword(spec: TokenSpec) -> Keyword:
    """Lift a TokenSpec record into an engine-facing Keyword row."""
    return Keyword(
        name=spec.name,
        token=spec.byte,
        conditional=spec.conditional,
        middle=spec.middle,
        startOfStatement=spec.startOfStatement,
        lineLiteral=spec.lineLiteral,
        expectLineNumber=spec.expectLineNumber,
        fnProc=spec.fnProc,
        pseudoVarBase=spec.pseudoVarBase,
        commonAbbrev=spec.commonAbbrev,
    )


def _buildKeywordRows(specs: Tuple[TokenSpec, ...]) -> Tuple[Keyword, ...]:
    """Build the ordered keyword tuple for a dialect from TokenSpec rows.

    Sorted with ROM-preferred abbreviation winners first, then
    longest-first within each cluster. This is the order the
    linear matcher walks, so `commonAbbrev` rows (PRINT before PAGE,
    ENDPROC before END, REPEAT before READ, TIME before TAN, AND
    before ABS, PROC before POS/PTR/PI) claim short prefixes as
    the ROM does.
    """
    rows = [_toKeyword(spec) for spec in specs]

    rows.sort(key=lambda kw: (not kw.commonAbbrev, -len(kw.name), kw.token))

    return tuple(rows)


BBC_BASIC_II = Dialect(
    name="BBC BASIC II",
    keywords=_buildKeywordRows(BBC_BASIC_II_TOKENS),
)


BBC_BASIC_IV = Dialect(
    name="BBC BASIC IV",
    keywords=_buildKeywordRows(BBC_BASIC_IV_TOKENS),
)
