# beebtools

[![PyPI](https://img.shields.io/pypi/v/beebtools.svg)](https://pypi.org/project/beebtools/)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Tests](https://github.com/acscpt/beebtools/actions/workflows/tests.yml/badge.svg)](https://github.com/acscpt/beebtools/actions/workflows/tests.yml)

A Python tool for working with BBC Micro DFS disc images.

`beebtools` can read disc catalogues, extract and detokenize BBC BASIC programs to
a more human-readable (and text editor friendly) format, including a pretty-printer 
that makes dense BBC BASIC code more legible.

## Disc images and the DFS catalogue

BBC Micro software is widely preserved as `.ssd` (single-sided) and `.dsd`
(double-sided interleaved) disc images. Each image is a raw sector-by-sector
dump of the original floppy disc, laid out according to Acorn's Disc Filing
System (DFS).

The first two sectors of each disc side hold the catalogue: disc title, file
count, and one entry per file giving its name, DFS directory prefix, load and
exec addresses, byte length, and start sector. `beebtools` reads this catalogue
and can list it in a human-readable table, sorted by name, catalogue order, or
file size.

Files are extracted by DFS name (`T.MYPROG`, `$.!BOOT`) or by bare name when
unambiguous. On a double-sided `.dsd` image both sides are catalogued; if the
same bare name appears on both sides, `beebtools` tells you and asks you to be
specific. Bulk extraction (`-a`) pulls every file off the disc at once.

## Programs: BBC BASIC and binary files

Most files you will want to look at on a BBC Micro disc are BBC BASIC programs.
They are not stored as text. The BBC Micro's BASIC ROM tokenizes programs before
saving them: keywords like `PRINT`, `GOTO`, and `FOR` are replaced with single
bytes in the range 0x80-0xFF, `GOTO` and `GOSUB` targets are encoded as compact
3-byte line-number references, and the whole thing is written as a sequence of
binary line records with no human-readable structure.

Binary files (machine code, data, sound samples) are stored as raw bytes and
extracted as-is.

For BASIC files, `beebtools` does three things in sequence:

1. **Detokenize** - decode the binary line records back to `LIST`-style text,
   expanding keyword tokens, decoding line-number references, and handling
   `REM` and `DATA` tails correctly (they are literal ASCII and must not be
   expanded).

2. **Pretty-print** (optional, `--pretty`) - add operator spacing to the
   raw detokenized text. BBC BASIC stores only the spaces the programmer
   explicitly typed, so code like `IFx>100THENx=0:y=0` is normal. The
   pretty-printer adds spaces around operators and punctuation while leaving
   string literals, `REM` tails, and `DATA` tails completely untouched.

3. **Anti-listing trap detection** - some 1980s software used `*|` followed
   by `VDU 21` (disable output) bytes as a copy-protection trick. Typing `LIST`
   on the real machine would blank the screen after that line. `beebtools`
   converts `*|` statements to `REM *|` and strips the control characters,
   so the program is readable.

## Features

- Read DFS catalogues from `.ssd` and `.dsd` disc images

- Extract individual files by DFS name (`T.MYPROG`, or bare `MYPROG`)

- Bulk-extract everything from a disc image at once

- Detokenize BBC BASIC II programs to `LIST`-style plain text

- Pretty-printer: add operator spacing to make terse BASIC readable

- Anti-listing trap detection: neutralise copy-protection `*|` traps

- Star command awareness: `*SCUMPI` is passed through verbatim, no false spacing

- Zero dependencies - pure Python 3.8+, single package

## Installation

```bash
pip install beebtools
```

For development (installs `pytest` and uses an editable install):

```bash
git clone https://github.com/acscpt/beebtools
cd beebtools
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Quick start

```bash
# List what is on a disc image
beebtools cat mydisc.dsd

# Extract and detokenize a BASIC program
beebtools extract mydisc.dsd T.MYPROG

# Extract with operator spacing added
beebtools extract mydisc.dsd T.MYPROG --pretty

# Extract everything from a double-sided disc
beebtools extract mydisc.dsd -a --pretty -d output/
```

## Pretty-printer: what it does

Raw BBC BASIC from a tokenized file looks like this when detokenized:

```
  100 IFx>100ORy<0THENx=0:y=0
  110 FORi=1TO8:s=s+x*x:NEXTi
  120 SOUND1,-15,s,5:IFs>9999THENs=0
```

With `--pretty`:

```
  100 IFx > 100ORy < 0THENx = 0 : y = 0
  110 FORi = 1TO8 : s = s + x * x : NEXTi
  120 SOUND1, -15, s, 5 : IFs > 9999THENs = 0
```

Specifically, the pretty-printer adds:

- a space between the line number and the first token

- spaces around comparison operators: `=` `<` `>` `<>` `<=` `>=`

- spaces around arithmetic operators: `+` `-` `*` `/`

- padding around colon statement separators: `a:b` becomes `a : b`

- a trailing space after each comma

- correct unary minus context: `(-x)` and `SOUND 1,-15,s,5` stay unary

- string literals, `REM` tails, and `DATA` tails are never touched

- star commands (`*COMMAND`) are passed through verbatim

Note that spaces between keywords and identifiers are not added - BBC BASIC
stores only the spaces that were explicitly typed. The pretty-printer works on
operators and punctuation, which is where the density tends to be worst.

### Anti-listing traps

A common copy-protection trick was to follow a `*|` MOS comment with
`CHR$(21)` (`VDU 21`, disable output) bytes. When you typed `LIST`, the screen
would go blank after that line. The program was still there - you just couldn't
see it.

`beebtools` detects `*|` at the start of a statement and converts it to `REM *|`,
stripping any control characters from the tail. The comment text (if any) is
preserved.

```
  590 *|                       <- in the tokenized file
  590 REM *|                   <- what beebtools shows you
```

## Usage

### Command Line

`beebtools`, once installed in a Python enabled environment, can be used from the
command line.

#### `cat`

List a disc catalogue.

```bash
beebtools cat <image> [--sort name|catalog|size]
```

Lists all files on all sides of the disc with load address, exec address,
length, and file type.

```text
--- Side 0: BBC_MUSIC_2 (28 files) ---

  Name          Load     Exec   Length  Type
   $.!BOOT  00000000 00000000 00000018
   T.BACHPR 00000E00 00008023 000011A4  BASIC
   T.BEETHO 00000E00 00008023 00000F6C  BASIC
   ...
```

Sort options:

- `name` (default) - alphabetical by filename

- `catalog` - original on-disc DFS order

- `size` - ascending by file length

#### `extract`

Extract a file from a disc image.

```bash
beebtools extract <image> <filename> [-o FILE] [--pretty]
```

BASIC programs are automatically detected and detokenized to plain text.
All other files are extracted as raw bytes.

```bash
# Print to stdout
beebtools extract mydisc.dsd T.MYPROG --pretty

# Write to a file
beebtools extract mydisc.dsd T.MYPROG -o myprog.bas --pretty

# Bare filename - works when the name is unique across all sides
beebtools extract mydisc.dsd MYPROG
```

For binary files written with `-o`, the load address, exec address, and
length are printed so you have the information needed for a disassembler:

```text
Extracted to loader.bin
$.LOADER  load=0x001900  exec=0x001900  length=512 bytes
```

When `-o` is omitted, raw bytes go directly to stdout for piping.

##### Bulk extract

Extract all files from a disc image by specifying the option `-a`.

```bash
beebtools extract <image> -a [-d DIR] [--pretty]
```

Extracts every file from the disc.

- BASIC programs are saved as `.bas` text files

- binaries are saved as `.bin` raw files.

The output directory defaults to the disc image filename stem (`bbc_d1/` for `bbc_d1.dsd`).

On a double-sided `.dsd` image, files from each side are prefixed with
`side0_` or `side1_` to prevent collisions between identically-named files.

##### Filename matching

`extract` accepts DFS filenames in two forms:

- Explicit: `T.MYPROG`, `$.MENU`, `$.!BOOT`
- Bare: `MYPROG` - works when the name is unique on the disc

Ambiguous bare names report all matches:

```text
Ambiguous filename 'LOADER' - specify with directory prefix.
  Side 0: $.LOADER
  Side 1: T.LOADER
```

### Using as a library

`beebtools` can also be used as a Python library. The public API is imported
directly from the `beebtools` package:

```python
from beebtools import openDiscImage, detokenize, prettyPrint, isBasic, looksLikeText

sides = openDiscImage("mydisc.dsd")
for disc in sides:
    title, entries = disc.readCatalogue()
    print(f"Disc: {title}")
    for entry in entries:
        if isBasic(entry):
            data = disc.readFile(entry)
            if looksLikeText(data):
                lines = prettyPrint(detokenize(data))
                print("\n".join(lines))
```

## Supported formats

| Format | Description |
| --- | --- |
| `.ssd` | Single-sided 40 or 80 track |
| `.dsd` | Double-sided interleaved |

Both 40-track and 80-track images are supported. The tool does not
currently support Watford DFS extended catalogues (62-file discs).