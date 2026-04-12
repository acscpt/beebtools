# build - Build a disc image from files

```bash
beebtools build <dir> <output> [-t 40|80] [--title TITLE] [--boot OFF|LOAD|RUN|EXEC] [--strict]
```

Assembles a disc image from a directory of files with `.inf` sidecars. The
output format is determined by the file extension (`.ssd`, `.dsd`, `.adf`,
or `.adl`).

## Source directory layout

The source directory should have the same hierarchical layout produced by
`extract -a --inf`:

- **DFS**: one subdirectory per directory character (`$/`, `T/`), with each
  data file accompanied by a `.inf` sidecar. For DSD images, `side0/` and
  `side1/` subdirectories are expected.

- **ADFS**: a `$` directory at the top level containing the file hierarchy,
  with subdirectories matching the ADFS tree structure. Subdirectories are
  created on the image automatically.

### DFS example layout

```
working/
  $/
    BOOT
    BOOT.inf          # $.BOOT  FF1900 FF8023 000100
    MENU
    MENU.inf          # $.MENU  FF0E00 FF802B 000400
  T/
    MYPROG
    MYPROG.inf        # T.MYPROG FF0E00 FF802B 000800
```

Each `.inf` file uses the standard DFS format: `DIR.NAME  LOAD EXEC SIZE [L]`.
The directory character in the `.inf` content matches the subdirectory the file
sits in.

### ADFS example layout

```
working/
  $/
    BOOT
    BOOT.inf          # $.BOOT  FF1900 FF8023 000100
    GAMES/
      ELITE
      ELITE.inf       # $.GAMES.ELITE  FFFF0E00 FFFF802B 004000
      DATA/
        SCORES
        SCORES.inf    # $.GAMES.DATA.SCORES  FF0000 FF0000 001000
```

For ADFS, the directory tree on the filesystem mirrors the ADFS directory
hierarchy. Each `.inf` sidecar must contain the **full ADFS path** of the file
(e.g. `$.GAMES.ELITE`, not just `$.ELITE`), because `build` reads the path
from the `.inf` content when adding the file to the image.

Subdirectories do not need their own `.inf` files - `build` creates them
automatically as it walks the filesystem tree.

## Round-trip workflow

The easiest way to get a valid source directory is to extract from an existing
image:

```bash
# DFS round-trip
beebtools extract original.ssd -a --inf -d working/
beebtools build working/ modified.ssd --title "MODIFIED"

# ADFS round-trip
beebtools extract original.adf -a --inf -d working/
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

Create the directory tree manually with `.inf` sidecars, then build in one
step. You must write the `.inf` files yourself with the correct paths and
addresses:

```bash
mkdir -p working/\$/GAMES
echo '$.LOADER  001900 001900 000400' > working/\$/LOADER.inf
echo '$.GAMES.ELITE  FFFF0E00 FFFF802B 004000' > working/\$/GAMES/ELITE.inf
# ... copy the actual data files alongside each .inf ...
beebtools build working/ mydisc.adf --title "MY DISC"
```

## Sector placement hints

When a `.inf` sidecar contains an `X_START_SECTOR` (or `START_SECTOR`) key,
the builder places the file at that exact sector instead of allocating a fresh
range. This enables byte-exact round-trips on discs whose catalogue entries
share sectors (notably Level 9 copy-protected games).

`extract -a --inf` writes `X_START_SECTOR` on every sidecar automatically, so
the default extract-and-rebuild cycle preserves original sector positions without
any extra flags.

See [A Field Guide to Non-standard BBC Micro Disc Images](../inf-and-nonstandard-discs.md)
for the full story.

## Options

- `-t` / `--tracks` - 40 or 80 tracks (default: 80). For ADFS: 40-track
  `.adf` = ADFS-S (160K), 80-track `.adf` = ADFS-M (320K), `.adl` = ADFS-L
  (640K).

- `--title` - disc title

- `--boot` - boot option: OFF, LOAD, RUN, or EXEC (numbers 0-3 also accepted)

- `--strict` - enforce DFS spec-compliance on filenames. Rejects non-printable
  bytes, `.`, `#`, `*`, `:`, `"`, and space. Use when authoring new discs where
  spec conformance matters. Default behaviour accepts any 7-bit byte, matching
  the real Acorn ROM.
