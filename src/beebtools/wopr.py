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
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Dict, FrozenSet, List, NamedTuple, Optional, Tuple


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
    commonAbbrev: bool = False        # ROM hand-ordering: claims short prefixes


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
_COLON = ':'
_COMMA = ','
_AMP = '&'
_DOT = '.'
_HEX_DIGITS = frozenset('0123456789ABCDEFabcdef')
_DEC_DIGITS = frozenset('0123456789')


class Match(NamedTuple):
    """A keyword recognised at the cursor and the source span it consumed.

    `consumed` differs between full-keyword matches (the keyword's own
    length) and dot-abbreviation matches (letters before the dot, plus
    the dot itself). The engine advances by `consumed`; the emitted
    token always comes from `kw`.
    """

    kw: "Keyword"
    consumed: int


def _isIdentStartChar(ch: str) -> bool:
    """Letter, underscore, or backtick: the characters a name may begin with."""
    return ch.isalpha() or ch == '_' or ch == '`'


def _isIdentChar(ch: str) -> bool:
    """Identifier continuation: BBC BASIC II range 0x5F..0x7A plus digits."""
    return ch.isalnum() or ch == '_' or ch == '`'


def _emitAndAdvance(ctx: Context) -> str:
    """Emit the current character as a literal byte and advance one step."""
    ch = ctx.text[ctx.pos]
    ctx.out.append(ord(ch))
    ctx.pos += 1
    return ch


def _consumeHex(ctx: Context) -> None:
    """Greedy '&[0-9A-Fa-f]*' scan: emit every byte verbatim.

    The ampersand itself is emitted; following hex digits are emitted
    in source case. Termination is purely character-class: the scan
    stops on the first non-hex character. No keyword lookahead.
    """
    _emitAndAdvance(ctx)
    while ctx.pos < len(ctx.text) and ctx.text[ctx.pos] in _HEX_DIGITS:
        _emitAndAdvance(ctx)


def _consumeIdentifier(ctx: Context) -> None:
    """Emit a run of identifier characters as literal bytes."""
    while ctx.pos < len(ctx.text) and _isIdentChar(ctx.text[ctx.pos]):
        _emitAndAdvance(ctx)


def _emitLineNumberRef(ctx: Context) -> None:
    """Encode a decimal digit run as the 0x8D inline line-number escape.

    The BBC ROM packs a 16-bit line number into three bytes with a
    specific bit-scrambling (see SPGETN in the ROM recce). This mirror
    implementation follows the same packing so the encoded bytes match.
    """
    start = ctx.pos
    while ctx.pos < len(ctx.text) and ctx.text[ctx.pos] in _DEC_DIGITS:
        ctx.pos += 1

    value = int(ctx.text[start:ctx.pos]) & 0xFFFF
    top = (((value & 0x00C0) >> 2) | ((value & 0xC000) >> 12)) ^ 0x54
    byte1 = (value & 0x3F) | 0x40
    byte2 = ((value >> 8) & 0x3F) | 0x40

    ctx.out.append(0x8D)
    ctx.out.append(top)
    ctx.out.append(byte1)
    ctx.out.append(byte2)


def _matchKeyword(ctx: Context) -> Optional[Match]:
    """Match a full keyword or a dot-abbreviation at the cursor.

    Walks the dialect's keyword tuple in order; the first row whose
    name matches the cursor wins. The tuple is ordered with
    ROM-preferred keywords first within each first-letter cluster,
    then longest-first, so the first full match is the longest and
    the first abbreviation match is the ROM-preferred keyword.

    Two match shapes:

    - Full: source text equals `kw.name`. Conditional keywords reject
      when the following character is an identifier char (TIMER is
      not TIME + 'R').
    - Abbreviation: source text is `kw.name`'s leading letters
      followed by '.'. Walks `dialect.keywords` for the first row
      whose name starts with those letters.
    """
    text = ctx.text
    pos = ctx.pos
    length = len(text)

    for kw in ctx.dialect.keywords:
        name = kw.name
        kwLen = len(name)

        if pos + kwLen > length:
            continue

        if text[pos:pos + kwLen] != name:
            continue

        if kw.conditional and pos + kwLen < length:
            if _isIdentChar(text[pos + kwLen]):
                continue

        return Match(kw, kwLen)

    end = pos
    while end < length and text[end].isalpha():
        end += 1

    if end == pos or end >= length or text[end] != _DOT:
        return None

    prefix = text[pos:end]
    consumed = end - pos + 1

    for kw in ctx.dialect.keywords:
        if kw.name.isalpha() and kw.name.startswith(prefix) and len(kw.name) > len(prefix):
            return Match(kw, consumed)

    return None


def _applyKeyword(ctx: Context, match: Match, currentState: State) -> State:
    """Emit a matched keyword's token and return the resulting state.

    Pseudo-variable tokens emit in their statement form (+0x40) when
    matched at start-of-statement; otherwise in the function form.
    After emission, FN and PROC eat the following identifier as
    literal bytes (the user's PROC/FN name is opaque). State
    transitions follow the keyword's flags. The cursor advances by
    `match.consumed`, which is the keyword length for full matches
    and prefix-letters + dot for abbreviations.
    """
    kw = match.kw
    atStart = (currentState == State.AT_START)
    token = kw.token + 0x40 if (kw.pseudoVarBase and atStart) else kw.token
    ctx.out.append(token)
    ctx.pos += match.consumed

    if kw.fnProc:
        _consumeIdentifier(ctx)

    ctx.expectLineNumber = kw.expectLineNumber

    if kw.lineLiteral:
        return State.LINE_LITERAL
    if kw.startOfStatement:
        return State.AT_START
    if kw.middle:
        return State.MID_STATEMENT

    return currentState


def _statementStep(ctx: Context, currentState: State) -> State:
    """Dispatch one character of statement body (AT_START or MID_STATEMENT).

    Recognises strings, hex literals, keywords, identifiers, and
    line-number references. Falls through to literal emission for
    anything else. Whitespace and comma preserve both the current
    state and the expect-line-number latch so `GOTO 10, 20, 30`
    encodes all three as line references.
    """
    ch = ctx.text[ctx.pos]

    if ch == _QUOTE:
        _emitAndAdvance(ctx)
        return State.IN_STRING

    if ctx.expectLineNumber and ch in _DEC_DIGITS:
        _emitLineNumberRef(ctx)
        return State.MID_STATEMENT

    if ch == _AMP:
        _consumeHex(ctx)
        ctx.expectLineNumber = False
        return State.MID_STATEMENT

    if _isIdentStartChar(ch):
        match = _matchKeyword(ctx)
        if match is not None:
            return _applyKeyword(ctx, match, currentState)
        _consumeIdentifier(ctx)
        ctx.expectLineNumber = False
        return State.MID_STATEMENT

    if ch == _COLON:
        _emitAndAdvance(ctx)
        ctx.expectLineNumber = False
        return State.AT_START

    if ch.isspace() or ch == _COMMA:
        _emitAndAdvance(ctx)
        return currentState

    _emitAndAdvance(ctx)
    if ch not in _DEC_DIGITS:
        ctx.expectLineNumber = False
    return State.MID_STATEMENT


def _atStart(ctx: Context) -> State:
    """Open-of-statement transition."""
    return _statementStep(ctx, State.AT_START)


def _midStatement(ctx: Context) -> State:
    """Mid-statement transition."""
    return _statementStep(ctx, State.MID_STATEMENT)


def _inString(ctx: Context) -> State:
    """Inside a double-quoted string: emit until the closing quote."""
    ch = _emitAndAdvance(ctx)
    if ch == _QUOTE:
        return State.MID_STATEMENT
    return State.IN_STRING


def _lineLiteral(ctx: Context) -> State:
    """Rest of line is opaque (after REM, DATA): emit every byte literally."""
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


# =====================================================================
# Detokenizer
# =====================================================================


def decodeLineRef(b0: int, b1: int, b2: int) -> int:
    """Decode a BBC BASIC inline line-number reference.

    The encoding XORs the top two bits of each 6-bit payload half into
    a single control byte, with a sentinel of 0x54. Reverses the
    packing done by the tokenizer's line-reference emitter.
    """
    x = b0 ^ 0x54
    lo = (b1 & 0x3F) | ((x & 0x30) << 2)
    hi = (b2 & 0x3F) | ((x & 0x0C) << 4)
    return hi * 256 + lo


def _buildDecoderMaps(
    dialect: Dialect,
) -> Tuple[Dict[int, str], FrozenSet[int]]:
    """Derive the byte-to-name map and line-literal set from a Dialect.

    The detokenizer consults these two structures on every byte of the
    token stream. Pseudo-variable keywords are present in the dialect
    at their function-form byte only, so the statement form (byte +
    0x40) is expanded here for the decode-time lookup.
    """
    byteToName: Dict[int, str] = {}
    lineLiteral: set = set()

    for kw in dialect.keywords:
        byteToName[kw.token] = kw.name

        if kw.pseudoVarBase:
            byteToName[kw.token + 0x40] = kw.name

        if kw.lineLiteral:
            lineLiteral.add(kw.token)

    return byteToName, frozenset(lineLiteral)


class _DecoderState(IntEnum):
    """Detokenizer states.

    Simpler than the tokenizer: the AT_START vs MID_STATEMENT
    distinction does not exist here because both pseudo-variable
    byte forms decode to the same keyword name.
    """

    NORMAL = 0            # expanding tokens, handling literal ASCII
    IN_STRING = 1         # inside double-quoted string; literal bytes
    LINE_LITERAL = 2      # after REM or DATA; literal to end of line


def _detokenizeWithMaps(
    content: bytes,
    byteToName: Dict[int, str],
    lineLiteral: FrozenSet[int],
) -> str:
    """Walk a line body's token bytes, returning the decoded text.

    State machine: NORMAL -> IN_STRING on '"', NORMAL -> LINE_LITERAL
    on any byte in `lineLiteral`. Both absorbing states run to end of
    content (or for IN_STRING, to the matching close-quote).
    """
    parts: List[str] = []
    state = _DecoderState.NORMAL
    i = 0
    n = len(content)

    while i < n:
        b = content[i]

        if b == 0x0D:
            break

        if state == _DecoderState.IN_STRING:
            parts.append(chr(b))
            if b == 0x22:
                state = _DecoderState.NORMAL
            i += 1
            continue

        if state == _DecoderState.LINE_LITERAL:
            parts.append(chr(b))
            i += 1
            continue

        if b == 0x22:
            parts.append('"')
            state = _DecoderState.IN_STRING
            i += 1
            continue

        if b == 0x8D:
            if i + 3 < n:
                target = decodeLineRef(
                    content[i + 1], content[i + 2], content[i + 3]
                )
                parts.append(str(target))
                i += 4
            else:
                parts.append("?")
                i += 1
            continue

        if b >= 0x80:
            name = byteToName.get(b)
            if name is not None:
                parts.append(name)
                if b in lineLiteral:
                    state = _DecoderState.LINE_LITERAL
            else:
                parts.append(f"[&{b:02X}]")
            i += 1
            continue

        parts.append(chr(b))
        i += 1

    return "".join(parts)


def detokenizeLine(content: bytes, dialect: Dialect) -> str:
    """Decode one line body's token bytes back to source text.

    Dialect-driven: the byte-to-name lookup and the line-literal set
    are derived from `dialect` so BASIC IV's EDIT (0xCE) decodes when
    the IV dialect is passed.
    """
    byteToName, lineLiteral = _buildDecoderMaps(dialect)

    return _detokenizeWithMaps(content, byteToName, lineLiteral)


def detokenize(data: bytes, dialect: Dialect) -> List[str]:
    """Decode a whole tokenised BBC BASIC program to LIST-style lines.

    Walks the line-record structure: each record is 0x0D, high byte,
    low byte, length, body. Returns one string per line, prefixed with
    the 5-character right-justified line number as the ROM's LIST
    command would.
    """
    byteToName, lineLiteral = _buildDecoderMaps(dialect)
    lines: List[str] = []
    pos = 0

    while pos < len(data):
        if data[pos] != 0x0D:
            break

        pos += 1
        if pos >= len(data):
            break

        hi = data[pos]
        if hi == 0xFF:
            break

        if pos + 2 >= len(data):
            break

        lo = data[pos + 1]
        linenum = hi * 256 + lo
        linelen = data[pos + 2]

        if linelen < 4:
            break

        content = data[pos + 3: pos - 1 + linelen]
        pos = pos - 1 + linelen

        text = _detokenizeWithMaps(content, byteToName, lineLiteral)
        lines.append(f"{linenum:>5d}{text}")

    return lines
