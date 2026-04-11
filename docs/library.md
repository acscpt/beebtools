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
with openImage("mydisc.dsd") as image:
    for side in image:
        catalogue = side.readCatalogue()
        print(f"Disc: {catalogue.title} (boot={catalogue.boot_option.name})")
        for entry in catalogue:
            if entry.isBasic:
                data = side.readFile(entry)
                lines = prettyPrint(detokenize(data))
                print("\n".join(lines))
```

You can also open a specific format directly with `openDiscImage()` (DFS)
or `openAdfsImage()` (ADFS).

## Inspecting catalogue entries

`DFSEntry` and `ADFSEntry` both inherit from the `DiscEntry` abstract base
class. Every entry carries the file's name, directory, load and exec
addresses, byte length, and lock flag, and exposes `fullName`, `isBasic`,
and `isDirectory` properties. `isBasic` checks the exec address against
known BASIC entry points. Entries can be sorted by name, catalogue order,
or size.

```python
from beebtools import openImage, sortCatalogueEntries

with openImage("mydisc.ssd") as image:
    side = image[0]
    catalogue = side.readCatalogue()
    print(f"{len(catalogue)} files, {side.freeSpace()} bytes free")

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
side = image[0]

for entry in side.readCatalogue():
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

## Extracting a single file

`extractFile()` handles file lookup across disc sides, ambiguity
resolution, BASIC/hybrid detection, and detokenization in a single call.
It returns an `ExtractedFile` with the classified data.

```python
from beebtools import extractFile

result = extractFile("mydisc.ssd", "T.MYPROG", pretty=True)

if result.file_type == "BASIC" and result.lines is not None:
    # Pure BASIC - result.lines contains detokenized text
    print("\n".join(result.lines))

elif result.file_type == "BASIC+MC":
    # Hybrid - BASIC with appended machine code
    print(f"BASIC portion: {result.basic_size} bytes")
    print(f"Total size: {len(result.data)} bytes")

else:
    # Binary file - result.data contains raw bytes
    print(f"{result.entry.fullName}  {len(result.data)} bytes")
```

The `file_type` field is one of `"BASIC"`, `"BASIC+MC"`, `"BASIC?"`,
`"TEXT"`, or `"binary"`. The `entry` field carries the original catalogue
metadata (`load_addr`, `exec_addr`, `fullName`, etc.).

## Classifying file contents

`classifyFileType()` inspects a file's metadata and raw content to
determine its type. This is the same logic used by `beebtools cat -i`.

```python
from beebtools import openImage, classifyFileType

image = openImage("mydisc.ssd")
for side in image.sides:
    catalogue = side.readCatalogue()
    for entry in catalogue.entries:
        data = side.readFile(entry)
        file_type = classifyFileType(entry, data)
        print(f"{entry.fullName:12s}  {file_type}")
```

## Adding files with retokenization

`addFileTo()` wraps `side.addFile()` with optional retokenization - if
the source file is plain-text BASIC (e.g. a `.bas` file saved from an
editor), it is tokenized before being written to the disc image.

```python
from beebtools import openImage, addFileTo, DiscFile

image = openImage("mydisc.ssd")

# Add a plain-text BASIC file - it will be tokenized automatically
with open("game.bas", "rb") as f:
    data = f.read()

entry = addFileTo(
    image, side_index=0,
    spec=DiscFile(path="$.GAME", data=data,
                  load_addr=0x1900, exec_addr=0x8023),
    retokenize=True,
)
print(f"Added {entry.fullName} ({entry.length} bytes)")

image.save("mydisc.ssd")
```

## Non-ASCII round-tripping

BBC BASIC programs often contain non-ASCII bytes (teletext control codes,
graphics characters) embedded in PRINT strings. `escapeNonAscii()` and
`unescapeNonAscii()` convert these to `\xHH` notation for lossless
storage in plain ASCII text files.

```python
from beebtools import escapeNonAscii, unescapeNonAscii

line = 'PRINT "\x85Hello"'
escaped = escapeNonAscii(line)     # 'PRINT "\\x85Hello"'
restored = unescapeNonAscii(escaped)  # 'PRINT "\x85Hello"'
assert restored == line
```

The `writeBasicText()` and `readBasicText()` helpers use this internally
when `text_mode="escape"` is specified.

## Creating and building disc images

Create blank disc images programmatically, add files one at a time, or
build an entire image from a directory of files with `.inf` sidecars.
The `BootOption` enum provides the standard boot options (shared by DFS and ADFS).

```python
from beebtools import createDiscImage, DiscFile, BootOption, buildImage

# Create a blank DFS image and add files programmatically
image = createDiscImage(tracks=80, title="DEMO", boot_option=BootOption.EXEC)
side = image[0]
side.addFile(DiscFile(path="$.HELLO", data=b"...",
                      load_addr=0x1900, exec_addr=0x8023))
image.save("demo.ssd")

# Or build from a directory of files with .inf sidecars
raw = buildImage(source_dir="extracted/", output_path="rebuilt.ssd",
                 tracks=80, boot_option=BootOption.RUN)
```

`createImage()` is the format-dispatching counterpart to `openImage()`. It
returns a blank in-memory `DiscImage` chosen from the output path
extension. `createImageFile()` wraps it to also write the serialized bytes
straight to disk in one call.

```python
from beebtools import createImage, createImageFile, BootOption

# In-memory blank image, caller serializes when ready
image = createImage("blank.ssd", tracks=80, title="DEMO",
                    boot_option=BootOption.EXEC)

# Same, but written directly to disk
size = createImageFile("blank.ssd", tracks=80, title="DEMO",
                       boot_option=BootOption.EXEC)
```

## Creating and building ADFS disc images

ADFS images support hierarchical directories. Files are addressed by full
path (e.g. `$.GAMES.ELITE`). Use `createAdfsImage()` to create a blank image
and `addFile()`, `deleteFile()`, `mkdir()` to manipulate it.

```python
from beebtools import createAdfsImage, DiscFile, BootOption
from beebtools import ADFS_S_SECTORS, ADFS_M_SECTORS, ADFS_L_SECTORS

# Create a blank 320K ADFS image
image = createAdfsImage(
    total_sectors=ADFS_M_SECTORS,
    title="GAMES",
    boot_option=BootOption.RUN,
)
side = image[0]

# Create a subdirectory and add a file into it
side.mkdir("$.GAMES")
side.addFile(DiscFile(
    path="$.GAMES.ELITE",
    data=b"\x00" * 1024,
    load_addr=0x1900,
    exec_addr=0x1900,
))

# Add a file to the root directory
side.addFile(DiscFile(path="$.BOOT", data=b"*RUN GAMES.ELITE\r"))

# Delete a file
side.deleteFile("$.GAMES.ELITE")

# Write the image to a file
image.save("mydisc.adf")
```

Build an ADFS image from an extracted directory tree:

```python
from beebtools import buildImage

raw = buildImage(source_dir="extracted/", output_path="rebuilt.adf")
with open("rebuilt.adf", "wb") as f:
    f.write(raw)
```

Format sizes: `ADFS_S_SECTORS` (160K, 640 sectors), `ADFS_M_SECTORS` (320K,
1280 sectors), `ADFS_L_SECTORS` (640K, 2560 sectors).

## Reading and setting disc metadata

The `getTitle`, `setTitle`, `getBoot`, `setBoot`, and `discInfo` functions
provide programmatic access to disc-level properties.

```python
from beebtools import getTitle, setTitle, getBoot, setBoot, discInfo, BootOption

# Read and set the disc title
title = getTitle("mydisc.ssd")
setTitle("mydisc.ssd", "NEW TITLE")

# Read and set the boot option
boot = getBoot("mydisc.ssd")
setBoot("mydisc.ssd", BootOption.EXEC)

# Get a full disc summary
info = discInfo("mydisc.ssd")
print(f"Title: {info.title}")
print(f"Boot: {info.boot_option.name}")
print(f"Free: {info.free_space} bytes ({info.free_space // 256} sectors)")
print(f"Tracks: {info.tracks}")
```

All functions accept a `side` parameter for DFS DSD images (default 0).
Title length is validated against the format limit (12 for DFS, 19 for ADFS).

## Reading and setting file attributes

The `getFileAttribs` and `setFileAttribs` functions read and modify
individual file attributes (locked, load address, exec address) on an
existing disc image.

```python
from beebtools import getFileAttribs, setFileAttribs

# Read attributes
attribs = getFileAttribs("mydisc.ssd", "T.MYPROG")
print(f"Load: {attribs.load_addr:08X}")
print(f"Exec: {attribs.exec_addr:08X}")
print(f"Locked: {attribs.locked}")

# Lock a file
setFileAttribs("mydisc.ssd", "T.MYPROG", locked=True)

# Change load and exec addresses
setFileAttribs("mydisc.ssd", "T.MYPROG", load_addr=0x1900, exec_addr=0x8023)
```

Only the attributes passed as non-None are changed; others are left intact.

## Renaming files

The `renameFile` function renames a file in-place on a disc image.

```python
from beebtools import renameFile

# Simple rename
renameFile("mydisc.ssd", "T.MYPROG", "T.NEWNAME")

# Change DFS directory prefix
renameFile("mydisc.ssd", "$.MYPROG", "T.MYPROG")
```

On DFS, the directory prefix can change. On ADFS, both names must be in the
same parent directory.

## Working with .inf sidecar files

The `.inf` format is the standard BBC Micro community interchange format for
preserving file metadata (load address, exec address, length, lock flag)
alongside extracted data files. Used for both DFS and ADFS files, each `.inf`
file is a single line of text
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
