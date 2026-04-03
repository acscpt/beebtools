# beebtools

[![PyPI](https://img.shields.io/pypi/v/beebtools.svg)](https://pypi.org/project/beebtools/)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Tests](https://github.com/acscpt/beebtools/actions/workflows/tests.yml/badge.svg)](https://github.com/acscpt/beebtools/actions/workflows/tests.yml)

A Python tool for working with BBC Micro DFS and ADFS disc images.

`beebtools` reads DFS (`.ssd`/`.dsd`) and ADFS (`.adf`/`.adl`) disc images.
It can list catalogues, extract and detokenize BBC BASIC programs to a more
human-readable (and text editor friendly) format, and includes a pretty-printer
that makes dense BBC BASIC code more legible.

## Disc images

BBC Micro software is widely preserved as disc images - raw sector-by-sector
dumps of original floppy discs. `beebtools` supports two filing systems:

- **DFS** (Disc Filing System) - `.ssd` (single-sided) and `.dsd` (double-sided
  interleaved) images. Flat catalogue with up to 31 files per side, single-character
  directory prefixes (`$`, `T`, etc.).

- **ADFS** (Advanced Disc Filing System) - `.adf` (single-sided) and `.adl`
  (double-sided) images. Hierarchical directory tree with up to 47 entries per
  directory, full path names like `$.GAMES.ELITE`. Supports ADFS-S (160K),
  ADFS-M (320K), and ADFS-L (640K) old-map disc images.

`beebtools` reads catalogues from both formats and can list them in a
human-readable table, sorted by name, catalogue order, or file size.

Files are extracted by name (`T.MYPROG`, `$.GAMES.ELITE`) or by bare name when
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

For BASIC files, `beebtools` does two things in sequence when extracting:

1. **Detokenize** - decode the binary line records back to `LIST`-style text,
   expanding keyword tokens, decoding line-number references, and handling
   `REM` and `DATA` tails correctly (they are literal ASCII and must not be
   expanded).

2. **Pretty-print** (optional, `--pretty`) - add operator spacing to the
   raw detokenized text. BBC BASIC stores only the spaces the programmer
   explicitly typed, so code like `IFx>100THENx=0:y=0` is normal. The
   pretty-printer adds spaces around operators and punctuation while leaving
   string literals, `REM` tails, and `DATA` tails completely untouched.

   `beebtools` also handles anti-listing traps, a simply constructed statement
   that was embedded within a BASIC program.  A line starting with `*|` followed
   by `VDU 21` (disable output) bytes.  A simple and effective  copy-protection trick.
   The pretty-printer converts `*|` statements to `REM *|` and strips the
   control characters so the program is readable.

When creating images`beebtools` performs the reverse - plain-text BASIC (as produced
by step 1 or 2) is retokenized back to the binary format the BBC Micro
expects.  The anti-listing trick is not reversed and re-injected into the program
though in this case.

## Features

- Read DFS catalogues from `.ssd` and `.dsd` disc images

- Read ADFS catalogues from `.adf` and `.adl` disc images (old-map, Hugo directories)

- Extract individual files by name (`T.MYPROG`, `$.GAMES.ELITE`, or bare `MYPROG`)

- Bulk-extract everything from a disc image at once

- Detokenize BBC BASIC II programs to `LIST`-style plain text

- Retokenize plain-text BASIC back to binary - enabling a full
  detokenize-edit-retokenize workflow

- Pretty-printer: add operator spacing to make terse BASIC readable
  - Anti-listing trap detection: neutralise copy-protection `*|` traps

- Star command awareness: `*SCUMPI` is passed through verbatim, no false spacing

- `.inf` sidecar format support: parse and produce the standard community
  interchange format for preserving DFS/ADFS file metadata alongside extracted files

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

`beebtools` provides commands for inspecting, extracting, and building disc
images. All commands work with both DFS and ADFS images. Each command has its
own detailed reference page.

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

# Extract everything with .inf sidecars preserving file metadata
beebtools extract mydisc.dsd -a --inf -d output/

# List an ADFS disc catalogue
beebtools cat game.adf

# Extract a file from an ADFS disc by full path
beebtools extract game.adf $.GAMES.ELITE --pretty

# Bulk-extract an ADFS disc
beebtools extract game.adf -a -d output/

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

# Create a blank ADFS image (320K)
beebtools create blank.adf -t 80 --title "MY ADFS" --boot RUN

# Add a file to an ADFS image with a hierarchical path
beebtools add mydisc.adf loader.bin --name $.GAMES.LOADER --load 1900 --exec 1900

# Build an ADFS image from a directory tree
beebtools build output/ rebuilt.adl --title "REBUILT"
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
from beebtools import openImage, detokenize, tokenize, prettyPrint

# openImage auto-detects DFS (.ssd/.dsd) or ADFS (.adf/.adl)
image = openImage("mydisc.dsd")
for side in image.sides:
    catalogue = side.readCatalogue()
    for entry in catalogue.entries:
        if entry.isBasic:
            data = side.readFile(entry)
            lines = prettyPrint(detokenize(data))
            print("\n".join(lines))

# Retokenize edited plain text back to binary
edited_lines = ["   10PRINT\"HELLO\"", "   20END"]
binary = tokenize(edited_lines)
```

See [docs/library.md](https://github.com/acscpt/beebtools/blob/main/docs/library.md) for creating disc images, building from
`.inf` sidecars, and working with the `.inf` format programmatically.

## Supported formats

| Format | Filing system | Description |
| --- | --- | --- |
| `.ssd` | DFS | Single-sided 40 or 80 track |
| `.dsd` | DFS | Double-sided interleaved |
| `.adf` | ADFS | Single-sided (ADFS-S 160K, ADFS-M 320K) |
| `.adl` | ADFS | Double-sided (ADFS-L 640K) |

DFS: both 40-track and 80-track images are supported. Watford DFS extended
catalogues (62-file discs) are not supported.

ADFS: old-map (small directory, "Hugo" format) images are supported for both
reading and writing. New-map large-directory formats (ADFS-D/E/F/G) are not
supported.

## Documentation

See the [docs/](https://github.com/acscpt/beebtools/blob/main/docs/README.md) folder for full command reference, pretty-printer
details, and library API guide.