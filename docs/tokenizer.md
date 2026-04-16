# How the BBC BASIC Tokenizer Works

## Contents

- [How the BBC BASIC Tokenizer Works](#how-the-bbc-basic-tokenizer-works)
  - [Contents](#contents)
  - [1. Introduction](#1-introduction)
  - [2. What a token is](#2-what-a-token-is)
  - [3. The keyword table](#3-the-keyword-table)
  - [4. The state machine](#4-the-state-machine)
    - [Why four states and not more?](#why-four-states-and-not-more)
    - [Edge-fattening: arrows that eat character runs](#edge-fattening-arrows-that-eat-character-runs)
  - [5. Matching a keyword](#5-matching-a-keyword)
  - [6. Abbreviations and ROM ordering](#6-abbreviations-and-rom-ordering)
  - [7. Line-number references](#7-line-number-references)
  - [8. Pseudo-variables](#8-pseudo-variables)
  - [9. A worked trace: `IF X>&0A THEN 100`](#9-a-worked-trace-if-x0a-then-100)
  - [10. Corners: strings, REM, DATA, assembler](#10-corners-strings-rem-data-assembler)
    - [Strings](#strings)
    - [REM](#rem)
    - [DATA](#data)
    - [Embedded assembler](#embedded-assembler)
  - [11. Dialects: BASIC II and BASIC IV](#11-dialects-basic-ii-and-basic-iv)
  - [12. Round-tripping and detokenizing](#12-round-tripping-and-detokenizing)

---

## 1. Introduction

A BBC BASIC program on disc is not the text you typed. When you press
RETURN on a line of BASIC, the ROM scans it left to right and replaces
every keyword with a single byte. `PRINT "HELLO"` is thirteen
characters of source but only nine bytes once tokenised: one byte
`0xF1` for `PRINT`, a space, then the seven literal characters
`"HELLO"`. This is tokenization, and it is how a 32K machine fits a
useful BASIC interpreter into ROM and a useful program into RAM.

Going the other way, when you type `LIST`, the ROM walks the token
stream and expands every keyword back to its text. This is
detokenization. Together they form a round-trip: type, tokenize,
save, load, detokenize, display, and you see the same text you typed.

`beebtools` tokenizes and detokenizes BBC BASIC programs as part of
its disc extract and build workflow. When you extract a BASIC file
from a disc image, the raw token stream would be unreadable, so
beebtools detokenizes it into text. When you add a text file back to
a disc, beebtools tokenizes it into the byte stream the BBC expects.

This article describes the tokenizer engine — a small deterministic
state machine driven by a single keyword table. The engine handles
both BBC BASIC II (the BBC B ROM) and BBC BASIC IV (the BBC Master
128 ROM) and is straightforward to extend to other dialects.


## 2. What a token is

Every BBC BASIC keyword has a one-byte token in the range `0x80-0xFF`.
That range is reserved for tokens, which means any byte with the high
bit clear is literal text (characters, digits, operators, whitespace)
and any byte with the high bit set is a keyword.

```
0x00 - 0x7F    literal ASCII (text characters, digits, punctuation)
0x80 - 0xFF    keyword tokens (AND, PRINT, GOTO, etc.)
```

Two bytes in the token range are special:

- `0x8D` is **not** a keyword. It is the inline line-number escape,
  followed by three data bytes encoding a 16-bit line number. This
  is how `GOTO 100` stores the `100`.

- `0xCE` is **unused in BBC BASIC II** (a reserved gap). BBC BASIC
  IV fills the slot with `EDIT`.

A BASIC file on disc is a sequence of line records. Each record is:

```
  0x0D  high-byte-of-line-number  low-byte-of-line-number  length  ... tokens ...
```

The tokenizer works on one line at a time, producing the tokens
section. The rest of the line record (the `0x0D` marker, line number,
length byte) is added by the file-format layer, not by the tokenizer.


## 3. The keyword table

Every keyword has three things:

1. a **byte** (its token value, `0x80`–`0xFF`)
2. a **name** (the text the user types, e.g. `PRINT`)
3. a set of **behaviour flags**

The flags say how the keyword interacts with the rest of the line.
They are the non-obvious part: they capture facts like "`TIMER` is a
variable, not `TIME` followed by `R`" and "after `REM`, the rest of
the line is opaque text".

`beebtools` records all three in one place, a single table of
`TokenSpec` rows in `src/beebtools/tokens.py`:

```python
_T(0xF1, "PRINT", "middle", "commonAbbrev"),
_T(0xF4, "REM",   "lineLiteral"),
_T(0x91, "TIME",  "conditional", "middle", "pseudoVarBase", "commonAbbrev"),
```

The flags are:

| Flag | Meaning |
|------|---------|
| `conditional` | Suppress the match if the following character is an identifier char. `TIME` followed by `R` is the variable `TIMER`, not `TIME` + `R`. |
| `middle` | The keyword sits mid-statement; the tokenizer stays in the mid-statement state after emitting it. |
| `startOfStatement` | After this keyword, the tokenizer resets to the start-of-statement state. `ELSE` and `THEN` are the obvious examples. |
| `lineLiteral` | Everything after this keyword, to the end of the line, is opaque literal text. `REM` and `DATA`. |
| `expectLineNumber` | The next digit run should be tokenised as an inline line-number reference. `GOTO`, `GOSUB`, `RESTORE`, `THEN`, `ELSE`. |
| `fnProc` | The next identifier is an opaque user name (a function or procedure name). `FN` and `PROC`. |
| `pseudoVarBase` | The statement form of this keyword is `byte + 0x40`. Pseudo-variables only: `PTR`, `PAGE`, `TIME`, `LOMEM`, `HIMEM`. |
| `commonAbbrev` | ROM-preferred abbreviation winner: short prefixes resolve to this keyword before any alphabetical competitor. `PRINT` before `PAGE`, `ENDPROC` before `END`. |

A keyword can carry any combination of these flags. The engine reads
them directly and branches on them. Adding a new behaviour means
adding a new flag, not a new special case.


## 4. The state machine

The tokenizer is a finite-state machine with four persistent states:

```
AT_START         head of a statement; pseudo-vars get their
                 statement-form token (byte + 0x40)

MID_STATEMENT    mid-statement; pseudo-vars get their function-form
                 token

IN_STRING        inside a double-quoted string; every character is
                 literal

LINE_LITERAL     after REM or DATA; the rest of the line is literal
```

Transitions between states are driven by the current character and,
for keyword matches, by the keyword's flags.

```
                        +-------------+
                        |  AT_START   |<-------------------+
                        +-------------+                    |
                              |                            |
          ':' or end-of-line  |  keyword with              |
                              |  startOfStatement flag     |
                              |                            |
    keyword without 'middle'  |                            |
    and without 'startOf-'    |                            |
                              v                            |
                        +-----------------+                |
                        |  MID_STATEMENT  |----------------+
                        +-----------------+
                          |     |     |
                     '"'  |     |     |  keyword with 'lineLiteral' flag
                          |     |     |  (REM, DATA)
                          v     |     v
                   +-----------+|  +---------------+
                   | IN_STRING ||  | LINE_LITERAL  |
                   +-----------+|  +---------------+
                          |     |           |
                     '"'  |     |           |  end-of-line
                          v     v           v
                        (back to MID_STATEMENT or AT_START)
```

The `':'` character is the statement separator. Hitting it inside
`MID_STATEMENT` returns the machine to `AT_START` — the next statement
on the line begins fresh. End-of-line does the same.

The `IN_STRING` and `LINE_LITERAL` states are absorbing for their
trigger: everything between `"` and `"` is emitted verbatim,
everything between `REM` and end-of-line is emitted verbatim. No
keyword matching happens in either state.

### Why four states and not more?

Embedded `[ ... ]` assembler blocks might look like they need a fifth
state. They do not. The BBC ROM treats `[` and `]` as ordinary ASCII
characters and runs normal tokenization over the contents. Mnemonics
like `LDA` and `STA` are not keywords, so they fall through as
literal text; genuine keywords inside a block (`AND`, `OR`, `EOR`,
`FOR`, `NEXT`) tokenise normally. The state machine needs no special
case.

### Edge-fattening: arrows that eat character runs

There is a subtlety worth naming. In a textbook deterministic finite
automaton, every transition consumes exactly one character. A hex
literal like `&FF00` would need its own state, `HEX_LITERAL`, with a
self-loop on each hex digit and an exit edge on the first non-hex
character:

```
  MID_STATEMENT  --'&'-->   HEX_LITERAL
  HEX_LITERAL    --hex-->   HEX_LITERAL     (self-loop, one digit per tick)
  HEX_LITERAL    --other--> MID_STATEMENT
```

The engine here takes a different shape. Instead of a `HEX_LITERAL`
state, `MID_STATEMENT` has a single fat edge back to itself that
means "saw `&`, then ate every hex digit that followed". The inner
digit-eating loop lives inside the transition function, not in the
state graph:

```
  MID_STATEMENT  --'&' then run of hex digits-->  MID_STATEMENT
```

Same language recognised, same bytes emitted. The trade is one state
plus a self-loop in exchange for one fatter edge. The same thing
happens for identifier runs, for literal decimal numbers, and for
the digit run that encodes as a `0x8D` line-number reference — each
of those is an edge-fattening shortcut where the transition function
owns a sub-loop that would otherwise be an explicit state.

The theoretically-pure shape would promote all four sub-loops into
named states and split `MID_STATEMENT` into two flavours to absorb
the `expectLineNumber` latch. That gives a textbook DFA with ten
states and single-character edges. The current four-state shape is
the same machine with its micro-loops folded into the arrows — a
compactness win that costs nothing in correctness.


## 5. Matching a keyword

When the machine is in `AT_START` or `MID_STATEMENT` and the current
character is a letter, the engine tries to match a keyword. It walks
the dialect's keyword table in order and takes the first row whose
`name` is a prefix of the source at the cursor.

```
  source:  T I M E R = 0
                       ^ cursor
  walk:    ...TIME... matches the 4 chars "TIME"
           conditional? yes
           next char 'R' is an identifier char? yes
           -> suppress the match, treat 'T' as literal
```

The `conditional` flag is what stops `TIMER` being tokenised as `TIME`
+ `R`. It says: only accept this match if the following character is
not a letter, digit, `_`, or `%`/`$` type sigil.

The match is **case-sensitive**. Lowercase `print` does not match
`PRINT`. This mirrors the real ROM: interactive keyboard input is
uppercased before it reaches the tokenizer, but file or tape input
arrives verbatim. Lowercase source stays literal.

Table order matters. The table is sorted into the order the linear
matcher walks it:

1. `commonAbbrev` keywords first.
2. Within each group, longer names before shorter. `ENDPROC` (7
   letters) comes before `END` (3) so that `ENDPROC` gets matched
   first when the cursor is on an `E`.
3. Within equal length, by byte value.

The net effect: full-word matches are always longest-first, so the
cursor never mistakes `ENDPROC` for `END` followed by `PROC`.


## 6. Abbreviations and ROM ordering

BBC BASIC accepts dot-abbreviations. `P.` means `PRINT`, `PR.` means
`PRINT`, `PRO.` means `PROC`, `E.` means `ENDPROC`. The rule is:
letters followed by `.`, resolving to the first keyword in the ROM's
keyword table whose name starts with those letters.

The rub is that "first in the ROM table" is **not** alphabetical.
The BBC ROM's keyword table is hand-ordered so that the *common*
keyword in each shared-prefix cluster comes first:

| Prefix | Alphabetical first | ROM first |
|--------|--------------------|-----------|
| `P.` | PAGE | **PRINT** |
| `E.` | ELSE | **ENDPROC** |
| `R.` | READ | **REPEAT** |
| `T.` | TAB | **TIME** |
| `A.` | ABS | **AND** |
| `PRO.` | PROC | **PROC** |

`PRINT` is the most-typed keyword in a BASIC program. Making `P.`
resolve to `PRINT` (not `PAGE`) saves thousands of keystrokes. The
ROM authors clearly agreed, and ordered the table accordingly.

The `commonAbbrev` flag marks these ROM-preferred keywords. Sort
order puts them first, so the linear matcher hits them first, so the
first abbreviation match is the ROM-correct one.

Full-word matching is unaffected. `PRINT` and `PAGE` are both still
in the table, the full word `PAGE` still resolves to `PAGE`.


## 7. Line-number references

`GOTO 100` does not store the `100` as three ASCII digits. That
would waste bytes and force a base-10 parse at run time. Instead,
the tokenizer encodes the number as four bytes:

```
  0x8D   top-bits-byte   byte1   byte2
```

The 16-bit line number is split into two 6-bit halves. `byte1` and
`byte2` are the low halves with bit 6 set (to keep them in printable
range, and out of the token range). The `top-bits-byte` carries the
top two bits of each half, XOR-scrambled with `0x54` to avoid
colliding with `0x0D` (the line-start marker) and `0x8D` itself.

The trigger is the `expectLineNumber` flag. When a keyword with this
flag emits (for example `GOTO`), the tokenizer latches a flag and the
**next** digit run is encoded as a `0x8D` reference instead of as
literal ASCII digits. The latch clears once the digit run ends.

Keywords with `expectLineNumber`:

```
  ELSE    THEN    AUTO    DELETE    LIST
  RENUMBER    GOSUB    GOTO    RESTORE    TRACE
```

Anything else that looks like digits — a numeric literal in an
expression, a loop counter — encodes as ordinary ASCII digits. The
`0x8D` machinery is strictly for line references.


## 8. Pseudo-variables

Five keywords are pseudo-variables: `PTR`, `PAGE`, `TIME`, `LOMEM`,
`HIMEM`. They behave as variables, but their storage is internal to
the interpreter rather than in user RAM. They each have two token
bytes:

| Keyword | Function form | Statement form |
|---------|---------------|----------------|
| PTR   | 0x8F | 0xCF |
| PAGE  | 0x90 | 0xD0 |
| TIME  | 0x91 | 0xD1 |
| LOMEM | 0x92 | 0xD2 |
| HIMEM | 0x93 | 0xD3 |

The pattern is: statement form = function form + `0x40`.

- **Function form** is used in expression context, on the right-hand
  side: `X = TIME`, `IF PAGE > &1900 THEN ...`
- **Statement form** is used at the head of an assignment, on the
  left: `TIME = 0`, `PAGE = &1900`

The `AT_START` state is exactly the signal the engine needs. When a
pseudo-var keyword with `pseudoVarBase` matches while the machine is
in `AT_START`, it emits `byte + 0x40` (the statement form). In
`MID_STATEMENT`, it emits `byte` (the function form). One flag, two
outcomes, no per-keyword special-casing.

The keyword table only stores the function-form byte. The statement
form is always derivable. This is why `BBC_BASIC_II_TOKENS` has 121
rows but the flat byte-to-name map `TOKENS` has 126 entries: five of
them get their statement form expanded at table-build time.


## 9. A worked trace: `IF X>&0A THEN 100`

One short line exercises most of the engine: a keyword match, a
fall-through identifier, a hex literal, the `expectLineNumber`
latch, and a `0x8D` line-number reference. The source is 17
characters; the output is 14 bytes.

```
source:  I  F     X  >  &  0  A     T  H  E  N     1  0  0
bytes:   E7    20 58 3E 26 30 41 20 8C          20 8D 44 64 40
```

The machine starts in `AT_START` with the `expectLineNumber` latch
clear. Each row below is one iteration of the main loop: "state
before" is what the machine thinks coming in, "emit" is the bytes
produced this step, and "state after" is where it ends up. The
cursor advances by as many characters as the action consumes.

| # | Cursor sees | State before | Latch | Action | Emit | State after |
|---|-------------|--------------|-------|--------|------|-------------|
| 1 | `IF` | AT_START | off | keyword match: IF (0xE7, `middle`) | `E7` | MID_STATEMENT |
| 2 | `' '` | MID_STATEMENT | off | whitespace, literal | `20` | MID_STATEMENT |
| 3 | `X` | MID_STATEMENT | off | no keyword match, identifier run | `58` | MID_STATEMENT |
| 4 | `>` | MID_STATEMENT | off | literal passthrough | `3E` | MID_STATEMENT |
| 5 | `&0A` | MID_STATEMENT | off | hex fat-edge: `&` then hex-digit run | `26 30 41` | MID_STATEMENT |
| 6 | `' '` | MID_STATEMENT | off | whitespace, literal | `20` | MID_STATEMENT |
| 7 | `THEN` | MID_STATEMENT | off | keyword match: THEN (0x8C, `startOfStatement` + `expectLineNumber`) | `8C` | AT_START, latch on |
| 8 | `' '` | AT_START | on | whitespace, latch preserved | `20` | AT_START, latch on |
| 9 | `100` | AT_START | on | latched digit run → encode as line ref | `8D 44 64 40` | MID_STATEMENT |

Three places are worth pausing on.

**Step 5 — edge-fattening in action.** The `&` does not put the
machine into a new state. The engine emits `&` and then runs its
hex-digit inner loop, consuming `0`, then `A`, stopping on the
space. This is the `MID_STATEMENT` → `MID_STATEMENT` fat edge from
§4: one arrow that eats "an ampersand followed by a run of hex
digits". The letter `A` is not reconsidered for a keyword match —
it is inside the hex run.

**Step 7 — two flags at once.** `THEN` carries both
`startOfStatement` and `expectLineNumber`. The first flag sends the
machine back to `AT_START` after emission, so the `100` sits at the
head of a fresh statement. The second flag sets a one-shot latch.
The next digit run the loop sees will be encoded as a line reference
rather than as literal ASCII digits. Whitespace and comma preserve
the latch (step 8); a non-digit, non-space character would clear it.

**Step 9 — line-number packing.** With the latch set and the cursor
on a digit, the engine takes the `0x8D` branch. It consumes the
whole decimal run `100`, parses it as 0x0064, and packs it into
three bytes using the ROM's bit-scrambling:

```
  value    = 100 = 0x0064
  top bits = (((0x0064 & 0x00C0) >> 2) | ((0x0064 & 0xC000) >> 12)) XOR 0x54
           =  ((0x40 >> 2) | 0) XOR 0x54
           =  0x10 XOR 0x54
           =  0x44
  byte1    = (0x0064 & 0x3F) | 0x40 = 0x24 | 0x40 = 0x64
  byte2    = ((0x0064 >> 8) & 0x3F) | 0x40 = 0x00 | 0x40 = 0x40
  emitted  = 8D 44 64 40
```

The XOR with `0x54` is there to guarantee the `top bits` byte can
never collide with `0x0D` (line-record start marker) or `0x8D`
(the escape itself), no matter what the line number is. The
`| 0x40` on the two halves keeps them in printable range and out
of the token range.

With those four bytes emitted, the cursor has reached end-of-line
and the walk finishes. The full output is `E7 20 58 3E 26 30 41 20
8C 20 8D 44 64 40` — 14 bytes for 17 characters of source.


## 10. Corners: strings, REM, DATA, assembler

### Strings

Inside double quotes, everything is literal. Keywords-that-aren't-
keywords-in-strings:

```
  PRINT "THE PRINT STATEMENT"
  ^^^^^  ^^^^^^^^^^^^^^^^^^^^^
  token  literal bytes, no second PRINT token emitted
```

A `"` character triggers the `MID_STATEMENT` → `IN_STRING` transition.
The next `"` triggers `IN_STRING` → `MID_STATEMENT`. The bytes between
are emitted verbatim and no matching happens.

A `"` inside `IN_STRING` followed by another `"` is the standard
BASIC quote-escape, and the engine handles it as part of the
IN_STRING loop — the pair emits one literal `"` and stays in the
state.

### REM

`REM` carries the `lineLiteral` flag. The tokenizer emits the `REM`
byte (`0xF4`), then switches to `LINE_LITERAL` and copies the rest of
the line byte-for-byte. No keyword matching happens, so `REM FOR
EVER` does not tokenise `FOR`.

### DATA

`DATA` works the same way (`0xDC`, `lineLiteral`). One of the
consequences is that lowercase identifiers containing the letters
`DATA` do not accidentally trigger line-literal mode — case-sensitive
matching is load-bearing here. Without it, `lda data,X` inside an
assembler block would match `DATA` on the lowercase `data` and
swallow everything to the end of the line.

### Embedded assembler

```
  FOR I%=0 TO 7
    P%=&70
    [
      LDA block,X
      STA &70
      RTS
    ]
  NEXT
```

The `[` and `]` are ordinary characters — no token, no state
transition. Inside the block, `LDA`/`STA`/`RTS` are not keywords so
they pass through as literal bytes. `block` is a lowercase
identifier (the BBC assembler permits labels in any case), so
keyword matching does not accidentally fire on it. The keywords
`AND`, `OR`, `EOR`, `FOR`, `NEXT`, `TO` that *do* appear in
assembler contexts still tokenise normally; the 6502 `AND` opcode
is written as the keyword `AND` because the BBC assembler reuses the
BASIC tokenizer and expects the token, not the letters.


## 11. Dialects: BASIC II and BASIC IV

BBC BASIC IV is the CMOS-6502 BASIC that ships in the BBC Master 128.
At the byte level it is a **strict superset** of BASIC II: every
byte-token assignment is identical, nothing is removed or renumbered.
Two things are added.

**EDIT** (`0xCE`) fills the one previously-unused gap in the token
range. It is a stand-alone command that invokes the Master's
full-screen editor on a line.

**`TIME$`** is the battery-backed real-time clock pseudo-variable.
Cleverly, it needs no new byte token. The interpreter tokenises
`TIME$` as the ordinary `TIME` byte followed by a literal `$` (`0x24`):

```
  X = TIME$    ->  58 3D 91 24
                   X  =  TIME $

  TIME$ = "..."  ->  D1 24 3D ...
                     TIME$  =
```

The interpreter distinguishes `TIME` (an integer) from `TIME$` (a
string) at run time by the trailing `$`. Which means the tokenizer
does not have to — TIME$ "just works" in BBC BASIC II's tokenizer
tables too, because the `TIME` match happens first and the `$` falls
through as literal.

There are also a handful of purely behavioural BASIC IV additions
that reuse existing tokens with new interpretation (`ON ... PROC`,
`LIST IF`, `EXT#` as a statement, `VDU |`). These need no tokenizer
changes — they are runtime concerns.

In `beebtools`, the two dialects share the same 121-row spec table,
with BASIC IV adding one row:

```
  BBC_BASIC_II_TOKENS  =  (... 121 specs ...)
  BBC_BASIC_IV_TOKENS  =  BBC_BASIC_II_TOKENS  +  EDIT at 0xCE
```

The tokenizer engine is dialect-agnostic. It takes a `Dialect`
object as a parameter and reads keyword rows and flag settings from
it. Adding a new dialect is exactly one `Dialect` instance.


## 12. Round-tripping and detokenizing

Detokenization is the reverse walk. The decoder reads bytes; every
byte in `0x80-0xFF` is looked up in a byte-to-name map and emitted as
text; every other byte is emitted as-is; the `0x8D` escape is decoded
back to a decimal line number. The decoder runs its own small state
machine with three states — `NORMAL`, `IN_STRING`, `LINE_LITERAL` —
mirroring the tokenizer's string and line-literal handling. The
`AT_START` distinction is not needed on the decode side because both
pseudo-variable byte forms (`0x90` and `0xD0`, for example) decode
back to the same keyword name.

The detokenizer takes a `Dialect` parameter in exactly the same way
the tokenizer does. Its byte-to-name map and line-literal set are
derived from the dialect's keyword table, so BASIC IV's `EDIT` (byte
`0xCE`) decodes when the IV dialect is passed and stays unknown when
the II dialect is passed. Adding a keyword to a spec table
propagates to both directions at once — one edit, tokenise and
detokenise both updated.

The round-trip is the acid test:

```
  source text
      |
      | tokenize  (sophie engine + Dialect)
      v
  token bytes
      |
      | write to disc image
      v
  disc file
      |
      | read from disc image
      v
  token bytes
      |
      | detokenize
      v
  source text        <-- must equal the original, byte for byte
```

Any divergence anywhere in this cycle is a bug. `beebtools` runs the
round-trip against real disc images as part of its test suite, which
is how the state machine, the keyword table, and the flag facts get
shaken out against programs written on real machines.
