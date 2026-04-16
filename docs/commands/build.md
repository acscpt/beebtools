# build - Build a disc image from files

```bash
beebtools build <dir> <output> [-t 40|80] [--title TITLE] [--boot OFF|LOAD|RUN|EXEC] [--force] [--strict]
```

Assembles a disc image from a directory of files with `.inf` sidecars. The
output format is determined by the file extension (`.ssd`, `.dsd`, `.adf`,
or `.adl`).

## Source directory layout

The source directory should have the layout produced by `extract -a`. The
builder supports both flat (default) and hierarchical layouts.

### Flat layout (default from `extract -a`)

All files sit in one directory, named by their full Acorn path with dots
as separators. Each data file has a companion `.inf` sidecar. A `$.inf`
carries the disc title and boot option.

**DFS example:**

```
working/
  $.inf                   # $ 00000000 00000000 00000000 00 TITLE=MY%20DISC OPT=3
  $.BOOT.bin
  $.BOOT.bin.inf          # $.BOOT  FF1900 FF8023 000100 00
  $.MENU.bas
  $.MENU.bas.inf          # $.MENU  FF0E00 FF802B 000400 00
  T.MYPROG.bas
  T.MYPROG.bas.inf        # T.MYPROG FF0E00 FF802B 000800 00
```

**ADFS example:**

```
working/
  $.inf
  $.GAMES.inf             # directory metadata
  $.GAMES.ELITE.bin
  $.GAMES.ELITE.bin.inf   # $.GAMES.ELITE  FFFF0E00 FFFF802B 004000 00
  $.DATA.inf              # directory metadata
  $.DATA.SCORES.bin
  $.DATA.SCORES.bin.inf   # $.DATA.SCORES  FF0000 FF0000 001000 00
```

For DSD images, the builder expects `side0/` and `side1/` subdirectories,
each with its own flat layout and `$.inf`.

### Hierarchical layout (from `extract -a --mkdirs`)

One subdirectory per DFS directory character or ADFS path. This is the
layout produced when `--mkdirs` is passed to `extract`.

**DFS example:**

```
working/
  $.inf
  $/
    BOOT.bin
    BOOT.bin.inf          # $.BOOT  FF1900 FF8023 000100 00
  T/
    MYPROG.bas
    MYPROG.bas.inf        # T.MYPROG FF0E00 FF802B 000800 00
```

The builder auto-detects which layout is present by looking for `.inf`
sidecars in the source tree. Both layouts round-trip correctly.

### $.inf as source of truth

When a `$.inf` exists in the source directory, the builder uses its
`TITLE=` and `OPT=` values for the disc title and boot option. Explicit
`--title`/`--boot` flags emit a warning when they conflict with `$.inf`;
pass `--force` to override.

## Round-trip workflow

```bash
# DFS round-trip
beebtools extract original.ssd -a -d working/
beebtools build working/ modified.ssd --title "MODIFIED"

# ADFS round-trip
beebtools extract original.adf -a -d working/
beebtools build working/ modified.adf --title "MODIFIED"
```

## Building from scratch

If you are building an image from scratch rather than round-tripping, you have
two options:

**Option 1: `create` + `add`** (simplest for a few files)

Use `create` to make a blank image, then `add` files one at a time. No `.inf`
files needed - you pass metadata on the command line:

```bash
beebtools create mydisc.adf --title "MY DISC"
beebtools add mydisc.adf loader.bin --name $.LOADER --load 1900 --exec 1900
beebtools add mydisc.adf game.bin --name $.GAMES.ELITE --load 0E00 --exec 802B
```

**Option 2: `build`** (better for many files)

Create a directory with `.inf` sidecars, then build in one step. You must
write the `.inf` files yourself with the correct paths and addresses:

```bash
echo '$.LOADER  001900 001900 000400 00' > working/$.LOADER.bin.inf
echo '$.GAMES.ELITE  FFFF0E00 FFFF802B 004000 00' > working/$.GAMES.ELITE.bin.inf
# ... copy the actual data files alongside each .inf ...
beebtools build working/ mydisc.adf --title "MY DISC"
```

## Sector placement hints

When a `.inf` sidecar contains an `X_START_SECTOR` (or `START_SECTOR`) key,
the builder places the file at that exact sector instead of allocating a fresh
range. This enables byte-exact round-trips on discs whose catalogue entries
share sectors (notably Level 9 copy-protected games).

`extract -a` writes `X_START_SECTOR` on every sidecar automatically, so
the default extract-and-rebuild cycle preserves original sector positions without
any extra flags.

See [A Field Guide to Non-standard BBC Micro Disc Images](../inf-and-nonstandard-discs.md)
for the full story.

## BASIC source without line numbers

When the builder retokenizes a `.bas` file, it accepts source with or
without line numbers. Numberless source is auto-numbered starting at 1
in steps of 1. Blank lines advance the counter without emitting output.

You can mix numbered and unnumbered lines in the same file. This is
useful when a program has GOTO or GOSUB targets that need stable
numbers but the rest of the source does not:

```basic
INPUT "Guess: " G%
IF G%=42 THEN 100
PRINT "Wrong"
GOTO 1
100 PRINT "Correct!"
```

Auto-numbering happens before tokenization. The tokenizer engine sees
ordinary numbered lines regardless of whether the source was numbered
by hand or by the auto-numberer.

## Options

- `-t` / `--tracks` - 40 or 80 tracks (default: 80). For ADFS: 40-track
  `.adf` = ADFS-S (160K), 80-track `.adf` = ADFS-M (320K), `.adl` = ADFS-L
  (640K).

- `--title` - disc title

- `--boot` - boot option: OFF, LOAD, RUN, or EXEC (numbers 0-3 also accepted)

- `--force` - override `$.inf` disc metadata with explicit `--title`/`--boot`
  values

- `--strict` - enforce DFS spec-compliance on filenames. Rejects non-printable
  bytes, `.`, `#`, `*`, `:`, `"`, and space. Use when authoring new discs where
  spec conformance matters. Default behaviour accepts any 7-bit byte, matching
  the real Acorn ROM.
