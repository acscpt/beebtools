# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""BBC BASIC tokenizer engine.

A deterministic finite-state machine that tokenizes BBC BASIC source.
The engine is dialect-agnostic: every supported BBC BASIC version is
expressed as an instance of `Dialect` and consumed by the same
machine. Dialect-specific data (keyword table, flag-set membership)
lives in `wopr_dialects.py`.

Formal shape:
    Q   the State enum below
    Σ   one source character at a time
    δ   the TRANSITIONS dispatch table
    q0  State.AT_START
    F   State.AT_START at end of line

Adding a new state is one State entry plus one TRANSITIONS entry.
Adding a new dialect is one Dialect instance.

Skeleton stage (step 1 of the wopr plan): the State enum, the Dialect
dataclass, the Context, and a driver loop wired to a transition
dispatch that emits all input as literal bytes. Subsequent steps port
keyword matching, abbreviations, line-number references, and string /
literal sub-states.
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Dict, Tuple


class State(IntEnum):
    """Tokenizer states.

    These are the persistent states of the FSM: the conditions that
    survive between characters and change how the next character is
    interpreted. Sub-loops (consuming a number, walking an identifier,
    scanning a hex literal) execute within a state and return to it.

    The state set is engine-level, not dialect-level: every supported
    BBC BASIC dialect shares these four states and differs only in
    the keyword table and flag-set membership carried by `Dialect`.
    """

    AT_START = 0          # head of statement; pseudo-vars take statement form
    MID_STATEMENT = 1     # mid-statement; pseudo-vars take function form
    IN_STRING = 2         # inside double-quoted string; everything is literal
    LINE_LITERAL = 3      # rest of line is opaque (after REM, DATA)


@dataclass(frozen=True)
class Keyword:
    """One BBC BASIC keyword and the behaviours it triggers when matched.

    Each keyword in a dialect's table is one Keyword instance. The
    engine reads the boolean flags directly: `kw.conditional`,
    `kw.middle`, etc. The behavioural facts that the legacy tokenizer
    scattered across multiple frozensets are gathered here per keyword,
    one row per fact.
    """

    name: str
    token: int
    conditional: bool = False         # suppress on trailing identifier char
    middle: bool = False              # transitions AT_START -> MID_STATEMENT
    startOfStatement: bool = False    # transitions back to AT_START
    lineLiteral: bool = False         # rest of line is opaque (REM, DATA)
    expectLineNumber: bool = False    # next digit run encodes as 0x8D ref
    fnProc: bool = False              # next identifier is opaque user name
    pseudoVarBase: bool = False       # +0x40 in AT_START gives statement form


@dataclass(frozen=True)
class Dialect:
    """Tokenization rules for one BBC BASIC dialect.

    The engine consults only this dataclass; it never branches on
    dialect identity. Keyword order is significant: abbreviation
    resolution walks `keywords` in order and takes the first prefix
    match, so dialects that ship the keyword table in ROM order get
    ROM-faithful abbreviation precedence (P. -> PRINT, E. -> ENDPROC).
    """

    name: str
    keywords: Tuple[Keyword, ...]


@dataclass
class Context:
    """Per-line tokenizer state. Lives only for the duration of one line."""

    text: str
    pos: int
    out: bytearray
    dialect: Dialect
    expectLineNumber: bool = False


TransitionFn = Callable[[Context], State]


_QUOTE = '"'


def _emitAndAdvance(ctx: Context) -> str:
    """Emit the current character as a literal byte and advance one step.

    Returns the character that was just emitted so the caller can act
    on it without re-reading from `ctx.text`.
    """
    ch = ctx.text[ctx.pos]
    ctx.out.append(ord(ch))
    ctx.pos += 1
    return ch


def _atStart(ctx: Context) -> State:
    """Open-of-statement transition.

    Emits the next character literally. A double quote opens a string;
    any other non-keyword character moves us into the body of the
    statement. (Keyword recognition lands in a later step; until then
    every character is treated as a literal.)
    """
    ch = _emitAndAdvance(ctx)
    if ch == _QUOTE:
        return State.IN_STRING
    return State.MID_STATEMENT


def _midStatement(ctx: Context) -> State:
    """Mid-statement transition.

    Emits the next character literally. A double quote opens a string;
    everything else stays mid-statement.
    """
    ch = _emitAndAdvance(ctx)
    if ch == _QUOTE:
        return State.IN_STRING
    return State.MID_STATEMENT


def _inString(ctx: Context) -> State:
    """Inside a double-quoted string.

    Every character is emitted as a literal byte, including the
    closing quote. The closing quote returns us to mid-statement.
    """
    ch = _emitAndAdvance(ctx)
    if ch == _QUOTE:
        return State.MID_STATEMENT
    return State.IN_STRING


def _lineLiteral(ctx: Context) -> State:
    """Rest of line is opaque (after REM, DATA).

    Every remaining character is emitted as a literal byte.
    """
    _emitAndAdvance(ctx)
    return State.LINE_LITERAL


TRANSITIONS: Dict[State, TransitionFn] = {
    State.AT_START:      _atStart,
    State.MID_STATEMENT: _midStatement,
    State.IN_STRING:     _inString,
    State.LINE_LITERAL:  _lineLiteral,
}


def tokenizeLine(text: str, dialect: "Dialect") -> bytes:
    """Tokenize one source line, returning the encoded byte sequence.

    Drives the state machine character by character: each transition
    consumes input, emits output, and returns the next state. Loop
    terminates when the input is exhausted.
    """
    ctx = Context(text=text, pos=0, out=bytearray(), dialect=dialect)
    state = State.AT_START

    while ctx.pos < len(ctx.text):
        state = TRANSITIONS[state](ctx)

    return bytes(ctx.out)
