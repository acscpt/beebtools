# extract - Extract files from a disc image

## Single file

```bash
beebtools extract <image> <filename> [-o FILE] [--pretty] [-t MODE]
```

Works with both DFS (`.ssd`/`.dsd`) and ADFS (`.adf`/`.adl`) disc images.
BASIC programs are automatically detected and detokenized to plain text.
All other files are extracted as raw bytes.

```bash
# Print to stdout
beebtools extract mydisc.dsd T.MYPROG --pretty

# Write to a file
beebtools extract mydisc.dsd T.MYPROG -o myprog.bas --pretty

# Bare filename - works when the name is unique across all sides
beebtools extract mydisc.dsd MYPROG

# Extract from an ADFS image by full path
beebtools extract game.adf $.GAMES.ELITE --pretty
```

For binary files written with `-o`, the load address, exec address, and
length are printed so you have the information needed for a disassembler:

```text
Extracted to loader.bin
$.LOADER  load=0x001900  exec=0x001900  length=512 bytes
```

When `-o` is omitted, raw bytes go directly to stdout for piping.

## Text modes

BBC BASIC programs can contain non-ASCII bytes - most commonly teletext
control codes (0x80-0x9F) embedded in `PRINT` strings for colour and
graphics effects. By default these are replaced with `?` when extracting
to plain text, which is lossy. The `-t`/`--text` option controls how
these bytes are handled.

| Mode | Encoding | Lossless | Description |
| --- | --- | --- | --- |
| `ascii` | ASCII | No | Non-ASCII bytes replaced with `?` (default) |
| `utf8` | UTF-8 | Yes | Raw bytes preserved as UTF-8 |
| `escape` | ASCII | Yes | Non-ASCII bytes written as `\xHH` notation |

The `escape` mode is useful when you need the file to remain plain ASCII
but want a lossless round-trip. The `build` command auto-detects escaped
files and reverses the encoding when retokenizing, so all three modes
round-trip correctly through extract/build.

```bash
# Default (lossy) - teletext codes become '?'
beebtools extract mydisc.dsd T.LOTTERY -o lottery.bas

# UTF-8 (lossless) - raw bytes preserved
beebtools extract mydisc.dsd T.LOTTERY -o lottery.bas -t utf8

# Escaped (lossless, plain ASCII) - \x81, \x83 etc.
beebtools extract mydisc.dsd T.LOTTERY -o lottery.bas -t escape
```

## Bulk extract

Extract all files from a disc image by specifying the option `-a`.

```bash
beebtools extract <image> -a [-d DIR] [--pretty] [--inf] [-t MODE]
```

Extracts every file from the disc.

- BASIC programs are saved as `.bas` text files

- plain ASCII text files are saved as `.txt` (BBC CR line endings are normalised to LF)

- everything else is saved as `.bin` raw files

The output directory defaults to the disc image filename stem (`bbc_d1/` for `bbc_d1.dsd`).

### Output layout

Files are laid out hierarchically. On a DFS image the DFS directory character becomes a real subdirectory. On ADFS images the full directory tree is recreated as nested filesystem directories.

**DFS single-sided (`.ssd`):**

```
bbc_d1/
  $/
    BOOT.txt
    LOADER.bin
  T/
    PROG.bas
```

**DFS double-sided (`.dsd`):**

```
bbc_d1/
  side0/
    $/
      BOOT.txt
    T/
      PROG.bas
  side1/
    $/
      BOOT.txt
    T/
      GAME.bas
```

**ADFS (`.adf`/`.adl`):**

```
game/
  $/
    !BOOT.txt
    README.bin
    GAMES/
      ELITE.bas
      REVS.bin
    DATA/
      SCORES.bin
```

Directory entries from ADFS images are skipped - only files are extracted.

### .inf sidecars

Add `--inf` to write `.inf` sidecar files alongside each extracted file,
preserving the load address, exec address, length, and lock flag in the
standard community interchange format.

The `.inf` file format is the same for DFS and ADFS - it records the leaf
directory, filename, load address, exec address, length, and optional lock
flag. The format predates ADFS and has no field for hierarchical paths, so
for ADFS extractions the directory hierarchy is encoded in the *filesystem
layout* instead: subdirectories mirror the ADFS tree, and each `.inf` file
records only the leaf directory name and filename. The `build` command
reconstructs the full ADFS path from the nested directory structure when
rebuilding an image.

## Filename matching

`extract` accepts filenames in several forms:

- DFS explicit: `T.MYPROG`, `$.MENU`, `$.!BOOT`
- ADFS full path: `$.GAMES.ELITE`, `$.DATA.SCORES`
- Bare: `MYPROG` or `ELITE` - works when the name is unique on the disc

Ambiguous bare names report all matches:

```text
Ambiguous filename 'LOADER' - specify with full path.
  Side 0: $.LOADER
  Side 1: T.LOADER
```

## Options

- `-o` / `--output` - write single file to this path instead of stdout

- `-a` / `--all` - extract all files from the disc

- `-d` / `--dir` - output directory for bulk extraction

- `--pretty` - add operator spacing to BASIC output

- `--inf` - write `.inf` sidecar files with bulk extraction

- `-t` / `--text` - text encoding for BASIC `.bas` files: `ascii` (lossy,
  default), `utf8` (lossless), `escape` (`\xHH` notation, lossless).
  See [Text modes](#text-modes) above
