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

`beebtools` detects `*|` at the start of a statement and converts it to `REM *|`,
stripping any control characters from the tail. The comment text (if any) is
preserved.

```basic
  590 *|                       <- in the tokenized file
  590 REM *|                   <- what beebtools shows you
```
