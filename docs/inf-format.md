# The .inf sidecar format

The `.inf` format is the standard BBC Micro community interchange format
for preserving file metadata alongside extracted data files. Each data
file has a companion text file whose name is the data file's PC name
with `.inf` appended (e.g. `$.BOOT.bin.inf`).

beebtools implements the [stardot .inf format specification](https://github.com/stardot/inf_format)
and Gerald Holdsworth's [Guide to Disc Formats](https://www.geraldholdsworth.co.uk/documents/DiscImage.pdf).

## Syntax variants

beebtools accepts all three syntax variants on input. Output always uses
syntax 1 (the most complete form).

### Syntax 1 (canonical)

```
NAME  LOAD EXEC LENGTH ACCESS [KEY=value ...]
```

Example:

```
$.BOOT  FF001900 FF008023 00000100 08 CRC=4D2E X_START_SECTOR=190
```

### Syntax 2 (TubeHost/BeebLink)

```
NAME  LOAD EXEC [L] [KEY=value ...]
```

No length field. `L` indicates locked. Used by older tools.

### Syntax 3 (directory)

```
NAME  ACCESS [KEY=value ...]
```

Only a name and symbolic access string. Used for directory entries by
ADFS Explorer.

## Fields

| Field | Format | Description |
|-------|--------|-------------|
| NAME | text | Full Acorn path, e.g. `$.BOOT`, `T.PROG`, `$.GAMES.ELITE` |
| LOAD | hex | Load address (6 or 8 digits; 6-digit `FFxxxx` sign-extended) |
| EXEC | hex | Execution address |
| LENGTH | hex | File length in bytes |
| ACCESS | hex or symbolic | Access byte as 2-digit hex (`08` = locked) or DFS-style `L` |

### Quoted names and percent-encoding

Names may be wrapped in double quotes. Inside quotes, any byte can be
encoded as `%XX` (RFC 3986 style):

- `%20` - space
- `%22` - double quote
- `%25` - literal percent
- `%06` - control byte
- `%2E` - literal dot (vs path separator)

Example: a DFS filename `Z.BLANK` followed by control byte 0x06:

```
"Z.BLANK%06"  00001900 00008023 00000400 00
```

### Access byte

The access byte is emitted as 2-digit hex. Common values:

| Value | Meaning |
|-------|---------|
| `00` | Not locked |
| `08` | Locked (DFS: bit 3 = not deletable) |

For ADFS, all 8 bits carry meaning per the ADFS access model
(R/W/E/L and owner/public).

**NOTE** The stardot access-byte layout does not always match the bit positions
a particular disc format uses. .inf sidecars always use the stardot layout
documented here.

## Extra-info keys

Everything after the fixed fields is a sequence of `KEY=value` pairs,
whitespace-separated. Values containing spaces are percent-encoded on
write and decoded on read.

### Keys emitted by beebtools

| Key | Format | Emitted on | Description |
|-----|--------|-----------|-------------|
| `CRC` | 4-digit hex | File `.inf` | CRC16/XMODEM checksum of the raw file data (`binascii.crc_hqx(data, 0)`) |
| `CRC32` | 8-digit hex | File `.inf` | CRC32 checksum of the raw file data |
| `X_START_SECTOR` | decimal | File `.inf` | Original on-disc start sector for byte-exact rebuild placement |
| `TITLE` | text | `$.inf` | Disc title (percent-encoded if it contains spaces) |
| `OPT` | 0-3 | `$.inf` | Boot option (0=OFF, 1=LOAD, 2=RUN, 3=EXEC) |

### Keys recognised on input

beebtools reads all keys listed above plus:

| Key | Description |
|-----|-------------|
| `START_SECTOR` | Alias for `X_START_SECTOR` (preferred when both present) |
| `OPT4` | Alternative boot option key (used by some tools on the `!BOOT` file) |
| `DIRTITLE` | Directory title (ADFS) |
| `DATETIME` | Authoring timestamp in `YYYYMMDDhhmmss` format |

Any unrecognised keys are preserved on read and round-tripped on write.

## Directory .inf files

### Root directory (`$.inf`)

The root directory `.inf` carries disc-level metadata. It uses syntax 1
with zeroed hex fields:

```
$ 00000000 00000000 00000000 00 TITLE=MY%20DISC OPT=3
```

For DSD images, each side has its own `$.inf` in its `side0/` or `side1/`
subdirectory.

### Subdirectory entries (ADFS)

ADFS directories produce `.inf` files carrying the directory's access
byte and any directory-level metadata. In flat extraction mode these are
standalone files with no companion data file (e.g. `$.GAMES.inf`).

## Extraction layout examples

beebtools defaults to a flat extraction layout where files are named by
their full Acorn path with dots as separators. The `.inf` sidecar is the
source of truth for the Acorn name; the host filename is cosmetic. This
follows the stardot DFS-style arrangement where all files are immediate
children of the output directory.

### DFS single-sided (.ssd)

```
out_dir/
  $.inf                       root: TITLE, OPT
  $.!BOOT.bas
  $.!BOOT.bas.inf
  $.DATA
  $.DATA.inf
  T.LOADER.bas
  T.LOADER.bas.inf
```

### DFS double-sided (.dsd)

Each side gets its own subdirectory with its own flat layout and `$.inf`:

```
out_dir/
  side0/
    $.inf
    $.!BOOT.bas
    $.!BOOT.bas.inf
    T.PROG.bas
    T.PROG.bas.inf
  side1/
    $.inf
    $.BOOT.txt
    $.BOOT.txt.inf
    T.GAME.bas
    T.GAME.bas.inf
```

### ADFS (.adf/.adl)

The full ADFS path becomes the filename. Directory entries produce
standalone `.inf` files with no companion data file:

```
out_dir/
  $.inf                       root: TITLE, OPT
  $.GAMES.inf                 directory: access byte, DIRTITLE
  $.GAMES.ELITE.bas
  $.GAMES.ELITE.bas.inf
  $.GAMES.PACMAN.bas
  $.GAMES.PACMAN.bas.inf
  $.UTILS.inf                 directory: access byte, DIRTITLE
  $.UTILS.EDITOR.bas
  $.UTILS.EDITOR.bas.inf
```

No collision resolution is needed because the full Acorn path is
inherently unique per side.

Pass `--mkdirs` to `extract -a` (or `layout="hierarchical"` in the
library) to create real subdirectories from Acorn paths instead.

## CRC validation

On extract, beebtools computes CRC16 and CRC32 from the raw file bytes
and emits them in the `.inf` sidecar. On build, if the `.inf` contains
CRC or CRC32 keys, the builder validates the checksums after any
retokenization (BASIC `.bas` files are retokenized to binary) and emits
a `BeebToolsWarning` on mismatch. CRC mismatches are warnings, not
errors, because the user may be intentionally modifying files.

Directory `.inf` files do not carry CRC keys.

## References

- [Stardot .inf format specification](https://github.com/stardot/inf_format)
- [Gerald Holdsworth - Guide to Disc Formats](https://www.geraldholdsworth.co.uk/documents/DiscImage.pdf) (pages 43-44)
- [A Field Guide to Non-standard BBC Micro Disc Images](inf-and-nonstandard-discs.md) - beebtools-specific extensions and round-trip story
