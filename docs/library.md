# Using beebtools as a library

The public API is imported directly from the `beebtools` package.

## Reading and detokenizing

Open a disc image, walk the catalogue, and convert tokenized BBC BASIC
programs back to readable text. `openImage()` auto-detects the format
from the file extension and works with both DFS and ADFS images. The
optional pretty-printer adds operator spacing in a single pass.

```python
from beebtools import openImage, detokenize, prettyPrint

# openImage auto-detects DFS (.ssd/.dsd) or ADFS (.adf/.adl)
image = openImage("mydisc.dsd")
for side in image.sides:
    catalogue = side.readCatalogue()
    print(f"Disc: {catalogue.title} (boot={catalogue.boot_option.name})")
    for entry in catalogue.entries:
        if entry.isBasic:
            data = side.readFile(entry)
            lines = prettyPrint(detokenize(data))
            print("\n".join(lines))
```

You can also open a specific format directly with `openDiscImage()` (DFS)
or `openAdfsImage()` (ADFS).

## Inspecting catalogue entries

Both `DFSEntry` and `ADFSEntry` carry the file's name, directory, load and
exec addresses, byte length, lock flag, and an `isBasic` property that checks
the exec address against known BASIC entry points. Both types expose
`fullName`, `isBasic`, and `isDirectory` properties for duck-typing
compatibility. Entries can be sorted by name, catalogue order, or size.

```python
from beebtools import openImage, sortCatalogueEntries

image = openImage("mydisc.ssd")
catalogue = image.sides[0].readCatalogue()

for entry in sortCatalogueEntries(catalogue.entries, "size"):
    print(f"{entry.fullName:<12s}  load={entry.load_addr:#010x}  "
          f"length={entry.length}  {'BASIC' if entry.isBasic else ''}")
```

## Reading ADFS disc images

ADFS images use hierarchical directories. The catalogue returned by
`readCatalogue()` is a flattened view of the entire directory tree, with
each entry's `directory` field containing the full parent path.

```python
from beebtools import openAdfsImage

image = openAdfsImage("game.adf")
side = image.sides[0]
catalogue = side.readCatalogue()

for entry in catalogue.entries:
    if entry.isDirectory:
        print(f"  [DIR] {entry.fullName}")
    elif entry.isBasic:
        print(f"  [BAS] {entry.fullName}")
    else:
        print(f"        {entry.fullName}  {entry.length} bytes")
```

You can also walk the raw directory tree for structured access:

```python
side = image.sides[0]
root = side.readDirectory(2)   # root directory at sector 2
for entry in root.entries:
    print(entry.name, "DIR" if entry.isDirectory else "")
```

## Searching BASIC source

The `search()` function detokenizes every BASIC file on a disc and scans
for a pattern, returning a list of match dicts with the filename, line
number, and full line text. Supports literal strings, regular expressions,
case-insensitive matching, and filtering to a single file.

```python
from beebtools import search

# Find all lines containing "GOTO" across every BASIC file on the disc
hits = search("mydisc.ssd", "GOTO")
for hit in hits:
    print(f"{hit['filename']}:{hit['line_number']}  {hit['line']}")

# Case-insensitive regex limited to one file
hits = search("mydisc.ssd", r"PROC\w+", filename="T.MYPROG",
              ignore_case=True, use_regex=True)
```

## Creating and building disc images

Create blank disc images programmatically, add files one at a time, or
build an entire image from a directory of files with `.inf` sidecars.
The `BootOption` enum provides the standard DFS boot options.

```python
from beebtools import createDiscImage, BootOption, buildImage

# Create a blank DFS image and add files programmatically
image = createDiscImage(tracks=80, title="DEMO", boot_option=BootOption.EXEC)
side = image.sides[0]
side.addFile("$", "HELLO", load_addr=0x1900, exec_addr=0x8023, data=b"...")
raw = image.serialize()

# Or build from a directory of files with .inf sidecars
raw = buildImage(source_dir="extracted/", tracks=80, boot_option=BootOption.RUN)
```

## Creating and building ADFS disc images

ADFS images support hierarchical directories. Files are addressed by full
path (e.g. `$.GAMES.ELITE`). Use `createAdfsImage()` to create a blank image
and `addFile()`, `deleteFile()`, `mkdir()` to manipulate it.

```python
from beebtools import createAdfsImage, BootOption
from beebtools import ADFS_S_SECTORS, ADFS_M_SECTORS, ADFS_L_SECTORS

# Create a blank 320K ADFS image
image = createAdfsImage(
    total_sectors=ADFS_M_SECTORS,
    title="GAMES",
    boot_option=BootOption.RUN,
)
side = image.sides[0]

# Create a subdirectory and add a file into it
side.mkdir("$.GAMES")
side.addFile(
    path="$.GAMES.ELITE",
    data=b"\x00" * 1024,
    load_addr=0x1900,
    exec_addr=0x1900,
)

# Add a file to the root directory
side.addFile(path="$.BOOT", data=b"*RUN GAMES.ELITE\r")

# Delete a file
side.deleteFile("$.GAMES.ELITE")

# Write the image to a file
with open("mydisc.adf", "wb") as f:
    f.write(image.serialize())
```

Build an ADFS image from an extracted directory tree:

```python
from beebtools import buildAdfsImage, ADFS_M_SECTORS

raw = buildAdfsImage(source_dir="extracted/", total_sectors=ADFS_M_SECTORS)
with open("rebuilt.adf", "wb") as f:
    f.write(raw)
```

Format sizes: `ADFS_S_SECTORS` (160K, 640 sectors), `ADFS_M_SECTORS` (320K,
1280 sectors), `ADFS_L_SECTORS` (640K, 2560 sectors).

## Working with .inf sidecar files

The `.inf` format is the standard BBC Micro community interchange format for
preserving DFS file metadata (load address, exec address, length, lock flag)
alongside extracted data files. Each `.inf` file is a single line of text
sitting next to the data file it describes:

```
BOOT.txt      <- the extracted data file
BOOT.txt.inf  <- the sidecar: "$.BOOT  FF1900 FF8023 000A00 L"
```

The format is:

```
DIR.NAME  LLLLLL EEEEEE SSSSSS [L] [CRC=XXXX]
```

Where `LLLLLL` is the load address, `EEEEEE` the exec address, `SSSSSS` the
file length, `L` marks a locked file, and the optional `CRC=` field is
preserved on parse but not generated.

```python
from beebtools import parseInf, formatInf

# Parse an existing .inf sidecar
inf = parseInf("$.BOOT  FF1900 FF8023 000A00 L")
print(inf.directory)   # '$'
print(inf.name)        # 'BOOT'
print(inf.load_addr)   # 0xFF1900
print(inf.locked)      # True

# Format a .inf line from catalogue metadata
line = formatInf("T", "MYPROG", 0x0E00, 0x8023, 0x1400)
# -> 'T.MYPROG  000E00 008023 001400'
```
