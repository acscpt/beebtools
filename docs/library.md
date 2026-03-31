# Using beebtools as a library

The public API is imported directly from the `beebtools` package.

## Reading and detokenizing

Open a disc image, walk the catalogue, and convert tokenized BBC BASIC
programs back to readable text. The optional pretty-printer adds operator
spacing in a single pass.

```python
from beebtools import openDiscImage, detokenize, prettyPrint

image = openDiscImage("mydisc.dsd")
for side in image.sides:
    catalogue = side.readCatalogue()
    print(f"Disc: {catalogue.title} (boot={catalogue.boot_option.name})")
    for entry in catalogue.entries:
        if entry.isBasic:
            data = side.readFile(entry)
            lines = prettyPrint(detokenize(data))
            print("\n".join(lines))
```

## Inspecting catalogue entries

Each `DFSEntry` carries the file's name, directory prefix, load and exec
addresses, byte length, lock flag, and an `isBasic` property that checks
the exec address against known BASIC entry points. Entries can be sorted
by name, catalogue order, or size.

```python
from beebtools import openDiscImage, sortCatalogueEntries

image = openDiscImage("mydisc.ssd")
catalogue = image.sides[0].readCatalogue()

for entry in sortCatalogueEntries(catalogue.entries, "size"):
    print(f"{entry.fullName:<12s}  load={entry.load_addr:#010x}  "
          f"length={entry.length}  {'BASIC' if entry.isBasic else ''}")
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

# Create a blank image and add files programmatically
image = createDiscImage(tracks=80, title="DEMO", boot_option=BootOption.EXEC)
side = image.sides[0]
side.addFile("$", "HELLO", load_addr=0x1900, exec_addr=0x8023, data=b"...")
raw = image.serialize()

# Or build from a directory of files with .inf sidecars
raw = buildImage(source_dir="extracted/", tracks=80, boot_option=BootOption.RUN)
```

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
