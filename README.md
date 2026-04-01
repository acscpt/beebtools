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

- `.inf` sidecar format support: parse and produce the standard community
  interchange format for preserving DFS file metadata alongside extracted files

- Create, modify, and build disc images from the command line or as a library

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

## Commands

`beebtools` provides commands for inspecting, extracting, and building DFS disc
images. Each command has its own detailed reference page.

| Command | Description |
| --- | --- |
| [`cat`](https://github.com/acscpt/beebtools/blob/main/docs/commands/cat.md) | List disc catalogue with file types and metadata |
| [`search`](https://github.com/acscpt/beebtools/blob/main/docs/commands/search.md) | Search BASIC source for a text pattern or regex |
| [`extract`](https://github.com/acscpt/beebtools/blob/main/docs/commands/extract.md) | Extract a single file or bulk-extract all files |
| [`create`](https://github.com/acscpt/beebtools/blob/main/docs/commands/create.md) | Create a blank formatted disc image |
| [`add`](https://github.com/acscpt/beebtools/blob/main/docs/commands/add.md) | Add a file to an existing disc image |
| [`delete`](https://github.com/acscpt/beebtools/blob/main/docs/commands/delete.md) | Delete a file from a disc image |
| [`build`](https://github.com/acscpt/beebtools/blob/main/docs/commands/build.md) | Build a disc image from files with `.inf` sidecars |

## Usage

```bash
# List what is on a disc image
beebtools cat mydisc.dsd

# Extract and detokenize a BASIC program
beebtools extract mydisc.dsd T.MYPROG

# Extract with operator spacing added
beebtools extract mydisc.dsd T.MYPROG --pretty

# Extract everything from a double-sided disc
beebtools extract mydisc.dsd -a --pretty -d output/

# Extract everything with .inf sidecars preserving DFS metadata
beebtools extract mydisc.dsd -a --inf -d output/

# Create a blank disc image
beebtools create blank.ssd --title "MY DISC" --boot EXEC

# Add a file to an existing image
beebtools add mydisc.ssd loader.bin --name $.LOADER --load 1900 --exec 1900

# Add a file using its .inf sidecar for metadata
beebtools add mydisc.ssd loader.bin --inf

# Delete a file from an image
beebtools delete mydisc.ssd $.LOADER

# Build a disc image from a directory of files with .inf sidecars
beebtools build output/ rebuilt.ssd --title "REBUILT"
```

## Pretty-printer

When extracting BASIC files from a disc image, the `--pretty` flag adds
operator spacing to make the dense tokenized code more readable.

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

See [docs/pretty-printer.md](https://github.com/acscpt/beebtools/blob/main/docs/pretty-printer.md) for the full list of
spacing rules and anti-listing trap handling.

## Using as a library

```python
from beebtools import openDiscImage, detokenize, prettyPrint

image = openDiscImage("mydisc.dsd")
for side in image.sides:
    catalogue = side.readCatalogue()
    for entry in catalogue.entries:
        if entry.isBasic:
            data = side.readFile(entry)
            lines = prettyPrint(detokenize(data))
            print("\n".join(lines))
```

See [docs/library.md](https://github.com/acscpt/beebtools/blob/main/docs/library.md) for creating disc images, building from
`.inf` sidecars, and working with the `.inf` format programmatically.

## Supported formats

| Format | Description |
| --- | --- |
| `.ssd` | Single-sided 40 or 80 track |
| `.dsd` | Double-sided interleaved |

Both 40-track and 80-track images are supported. The tool does not
currently support Watford DFS extended catalogues (62-file discs).

## Documentation

See the [docs/](https://github.com/acscpt/beebtools/blob/main/docs/README.md) folder for full command reference, pretty-printer
details, and library API guide.