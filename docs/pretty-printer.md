# Pretty-printer

The `--pretty` flag adds operator spacing to detokenized BBC BASIC output.

## Before and after

Raw BBC BASIC from a tokenized file looks like this when detokenized:

```basic
  100 IFx>100ORy<0THENx=0:y=0
  110 FORi=1TO8:s=s+x*x:NEXTi
  120 SOUND1,-15,s,5:IFs>9999THENs=0
```

With `--pretty`:

```basic
  100 IFx > 100ORy < 0THENx = 0 : y = 0
  110 FORi = 1TO8 : s = s + x * x : NEXTi
  120 SOUND1, -15, s, 5 : IFs > 9999THENs = 0
```

## What it spaces

- A space between the line number and the first token

- Spaces around comparison operators: `=` `<` `>` `<>` `<=` `>=`

- Spaces around arithmetic operators: `+` `-` `*` `/`

- Padding around colon statement separators: `a:b` becomes `a : b`

- A trailing space after each comma

- Correct unary minus context: `(-x)` and `SOUND 1,-15,s,5` stay unary

- String literals, `REM` tails, and `DATA` tails are never touched

- Star commands (`*COMMAND`) are passed through verbatim

Note that spaces between keywords and identifiers are not added - BBC BASIC
stores only the spaces that were explicitly typed. The pretty-printer works on
operators and punctuation, which is where the density tends to be worst.

## Anti-listing traps

A common copy-protection trick was to follow a `*|` MOS comment with
`CHR$(21)` (`VDU 21`, disable output) bytes. When you typed `LIST`, the screen
would go blank after that line. The program was still there - you just couldn't
see it.

`beebtools` detects `*|` at the start of a statement and preserves it as a
MOS comment. Control characters (e.g. VDU 21 bytes) are kept intact so the
text-encoding layer can handle them appropriately:

- **ASCII mode** (default): control characters are replaced with `?`
- **UTF-8 mode** (`-t utf8`): control characters pass through as-is
- **Escape mode** (`-t escape`): control characters become `\xHH` notation,
  enabling a lossless round-trip back to the original tokenized binary

```basic
  590 *|                       <- in the tokenized file (with VDU 21 bytes)
  590 *|visible                <- default ASCII mode (control chars replaced)
  590 *|\x15\x15visible        <- escape mode (control chars as \xHH)
```

## Round-tripping pretty-printed files

Pretty-printing is inherently lossy - it adds cosmetic spaces around operators
that were not present in the original tokenized binary. When a pretty-printed
file is retokenized (e.g. with `beebtools add` or `beebtools build`), those
extra spaces are preserved as literal `0x20` bytes in the tokenized output.
The retokenized program will run identically but will not be byte-identical
to the original.

For a byte-identical round-trip, extract without `--pretty`:

```bash
beebtools extract mydisc.ssd T.MYPROG -t escape -o myprog.bas
beebtools add rebuilt.ssd myprog.bas --name T.MYPROG
```

If you want readable formatting and the closest achievable fidelity, combine
`--pretty` with `-t escape`:

```bash
beebtools extract mydisc.ssd T.MYPROG --pretty -t escape -o myprog.bas
beebtools add rebuilt.ssd myprog.bas --name T.MYPROG
```

This preserves anti-listing traps, teletext control codes, and all non-ASCII
bytes embedded in PRINT strings. The only difference from the original binary
is the cosmetic spacing the pretty-printer added.

Without `-t escape`, the default ASCII mode replaces non-printable characters
with `?`, which is lossy but produces cleaner output for casual browsing.
