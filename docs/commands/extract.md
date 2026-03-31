# extract - Extract files from a disc image

## Single file

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

## Bulk extract

Extract all files from a disc image by specifying the option `-a`.

```bash
beebtools extract <image> -a [-d DIR] [--pretty] [--inf]
```

Extracts every file from the disc.

- BASIC programs are saved as `.bas` text files

- plain ASCII text files are saved as `.txt` (BBC CR line endings are normalised to LF)

- everything else is saved as `.bin` raw files

The output directory defaults to the disc image filename stem (`bbc_d1/` for `bbc_d1.dsd`).

### Output layout

Files are laid out hierarchically using the DFS directory character as a real
subdirectory. On a single-sided `.ssd` image:

```
bbc_d1/
  $/
    BOOT.txt
    LOADER.bin
  T/
    PROG.bas
```

On a double-sided `.dsd` image, an additional `side0/` and `side1/` level
keeps the two sides separate:

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

### .inf sidecars

Add `--inf` to write `.inf` sidecar files alongside each extracted file,
preserving the DFS load address, exec address, length, and lock flag in the
standard community interchange format.

## Filename matching

`extract` accepts DFS filenames in two forms:

- Explicit: `T.MYPROG`, `$.MENU`, `$.!BOOT`
- Bare: `MYPROG` - works when the name is unique on the disc

Ambiguous bare names report all matches:

```text
Ambiguous filename 'LOADER' - specify with directory prefix.
  Side 0: $.LOADER
  Side 1: T.LOADER
```

## Options

- `-o` / `--output` - write single file to this path instead of stdout

- `-a` / `--all` - extract all files from the disc

- `-d` / `--dir` - output directory for bulk extraction

- `--pretty` - add operator spacing to BASIC output

- `--inf` - write `.inf` sidecar files with bulk extraction
