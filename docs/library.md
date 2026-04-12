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

with openAdfsImage("game.adf") as image:
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
from beebtools import openAdfsImage, ADFS_ROOT_SECTOR

with openAdfsImage("game.adf") as image:
    side = image.sides[0]
    root = side.readDirectory(ADFS_ROOT_SECTOR)
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
from beebtools import extractFile, FileType

result = extractFile("mydisc.ssd", "T.MYPROG", pretty=True)

if result.file_type is FileType.BASIC:
    # Pure BASIC - result.lines contains detokenized text
    print("\n".join(result.lines))

elif result.file_type is FileType.BASIC_MC:
    # Hybrid - BASIC with appended machine code
    print(f"BASIC portion: {result.basic_size} bytes")
    print(f"Total size: {len(result.data)} bytes")

else:
    # BASIC_ISH, TEXT, or BINARY - result.data contains raw bytes
    print(f"{result.entry.fullName}  {result.file_type}  {len(result.data)} bytes")
```

The `file_type` field is a `FileType` enum member: `FileType.BASIC`,
`FileType.BASIC_MC`, `FileType.BASIC_ISH`, `FileType.TEXT`, or
`FileType.BINARY`. Stringifying a member (via `str()` or an f-string)
yields the historical short label (`"BASIC"`, `"BASIC+MC"`, `"BASIC?"`,
`"TEXT"`, `"BINARY"`). The `entry` field carries the original catalogue
metadata (`load_addr`, `exec_addr`, `fullName`, etc.).

`FileType.BASIC_ISH` is the interesting one: it means the file looks
like BASIC along one axis but not the other. Either the exec address
claims BASIC but the bytes are not tokenized, or the bytes are valid
tokenized BASIC but the exec address is non-standard. The second case
is usually a deliberately-marked "include" file, produced with `*SAVE`
and explicit addresses so that it cannot be run directly with
`*RUN`/`CHAIN` - callers are expected to `LOAD` it or merge it into
another program. See the `FileType` class docstring for the full story.

## Classifying file contents

`classifyFileType()` inspects a file's metadata and raw content to
determine its type. This is the same logic used by `beebtools cat -i`.
It returns a `FileType` enum member; stringifying it (or using it in
an f-string) yields the historical short label.

```python
from beebtools import openImage, classifyFileType, FileType

with openImage("mydisc.ssd") as image:
    for side in image.sides:
        catalogue = side.readCatalogue()
        for entry in catalogue.entries:
            data = side.readFile(entry)
            file_type = classifyFileType(entry, data)
            print(f"{entry.fullName:12s}  {file_type}")

            if file_type is FileType.BASIC_ISH:
                # Worth a closer look - see Section 5.
                pass
```

## Adding files

Every `DiscSide` exposes `addFile()`, which takes a `DiscFile` spec and
returns the catalogue entry that was written. Use it directly when the
data is already in the format the disc expects (raw bytes, tokenized
BASIC, etc.).

```python
from beebtools import openImage, DiscFile, tokenize

with openImage("mydisc.ssd") as image:
    side = image[0]
    entry = side.addFile(DiscFile(
        path="$.HELLO",
        data=tokenize(['10 PRINT "HELLO WORLD"']),
        load_addr=0x1900,
        exec_addr=0x8023,
    ))
    print(f"Added {entry.fullName} ({entry.length} bytes)")
    image.save("mydisc.ssd")
```

### Retokenizing plain-text BASIC on the way in

`addFileTo()` wraps `side.addFile()` with optional retokenization - if
the source file is plain-text BASIC (e.g. a `.bas` file saved from an
editor), it is tokenized before being written to the disc image.

```python
from beebtools import openImage, addFileTo, DiscFile

with open("game.bas", "rb") as f:
    data = f.read()

with openImage("mydisc.ssd") as image:
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

`createImage()` is the format-dispatching counterpart to `openImage()`. It
returns a blank in-memory `DiscImage` chosen from the output path
extension. `createImageFile()` wraps it to also write the serialized bytes
straight to disk in one call.

```python
from beebtools import createImage, createImageFile, DiscFile, BootOption, tokenize

# In-memory blank image, caller adds files and serializes when ready
with createImage("demo.ssd", tracks=80, title="DEMO",
                 boot_option=BootOption.EXEC) as image:
    image[0].addFile(DiscFile(
        path="$.HELLO",
        data=tokenize(['10 PRINT "HELLO WORLD"']),
        load_addr=0x1900, exec_addr=0x8023,
    ))
    image.save("demo.ssd")

# Or just write a blank image straight to disk
size = createImageFile("blank.ssd", tracks=80, title="BLANK",
                       boot_option=BootOption.EXEC)
```

For DFS-specific code that does not need format dispatch, `createDiscImage()`
returns a `DFSImage` directly and accepts the same `tracks` / `title` /
`boot_option` arguments. `createAdfsImage()` is the ADFS counterpart and is
covered in the next section.

### Building from an extracted directory tree

`buildImage()` assembles a disc from a directory of files with `.inf`
sidecars. The format is chosen from the output path extension. Pass
`save=True` to write the assembled image straight to `output_path`;
the assembled bytes are returned either way.

```python
from beebtools import buildImage, BootOption

# Build and write the image in one call
raw = buildImage(source_dir="extracted/", output_path="rebuilt.ssd",
                 tracks=80, boot_option=BootOption.RUN, save=True)

# Or get the bytes back without touching the filesystem
raw = buildImage(source_dir="extracted/", output_path="rebuilt.ssd",
                 tracks=80, boot_option=BootOption.RUN)
```

#### Sector placement hints

When a `.inf` sidecar contains an `X_START_SECTOR` (or `START_SECTOR`)
key, `buildImage()` passes the value through to the format engine as a
placement hint. DFS honours the hint unconditionally; ADFS honours it
only when the requested range is wholly free in the free-space map.

This enables byte-exact round-trips on discs whose catalogue entries
share sectors (notably Level 9 copy-protected games). `extract -a --inf`
writes `X_START_SECTOR` on every sidecar automatically, so the default
extract-and-rebuild cycle preserves original sector positions.

See [A Field Guide to Non-standard BBC Micro Disc Images](inf-and-nonstandard-discs.md)
for the full story on non-standard discs and the `.inf` extension.

## Creating and building ADFS disc images

ADFS images support hierarchical directories. Files are addressed by full
path (e.g. `$.GAMES.ELITE`). Use `createAdfsImage()` to create a blank image
and `addFile()`, `deleteFile()`, `mkdir()` to manipulate it.

```python
from beebtools import createAdfsImage, DiscFile, BootOption, tokenize
from beebtools import ADFS_S_SECTORS, ADFS_M_SECTORS, ADFS_L_SECTORS

# Create a blank 320K ADFS image
with createAdfsImage(
    total_sectors=ADFS_M_SECTORS,
    title="GAMES",
    boot_option=BootOption.RUN,
) as image:
    side = image[0]

    # Create a subdirectory and add a BASIC loader into it
    side.mkdir("$.GAMES")
    side.addFile(DiscFile(
        path="$.GAMES.ELITE",
        data=tokenize(['10 PRINT "ELITE"', '20 CHAIN "GAME"']),
        load_addr=0x1900,
        exec_addr=0x8023,
    ))

    # Add a boot file to the root directory
    side.addFile(DiscFile(path="$.BOOT", data=b"*RUN GAMES.ELITE\r"))

    # deleteFile removes a catalogue entry by its full path
    # side.deleteFile("$.GAMES.ELITE")

    image.save("mydisc.adf")
```

Build an ADFS image from an extracted directory tree:

```python
from beebtools import buildImage

buildImage(source_dir="extracted/", output_path="rebuilt.adf", save=True)
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

## Strict validation mode

By default, `beebtools` accepts any 7-bit byte in DFS filenames and
directory characters, matching the permissive behaviour of real Acorn
ROMs. This allows round-tripping of disc images that use spec-forbidden
characters (dots, hashes, control bytes) in filenames.

When authoring new disc images where spec compliance matters, wrap the
operation in `strictMode()` to enforce the DFS spec range (`0x21-0x7E`)
and reject `. : " # *` and space.

```python
from beebtools import strictMode, buildImage

# Default: ROM-faithful, accepts any 7-bit byte in filenames
buildImage("src/", "out.ssd", save=True)

# Strict: enforce spec-compliance, raises on forbidden characters
with strictMode():
    buildImage("src/", "out.ssd", save=True)
```

`strictMode()` is a context manager backed by `contextvars.ContextVar`,
so it is thread-safe and async-safe. Every validator in the stack
consults `isStrict()` when deciding whether to apply spec-only rules.

## Capturing warnings

`beebtools` emits diagnostics (missing `.inf` sidecars, malformed
catalogue fields, BASIC line compaction) as `BeebToolsWarning` via
Python's standard `warnings` module. By default these print to stderr.
To capture them programmatically, use `warnings.catch_warnings()`:

```python
import warnings
from beebtools import buildImage, BeebToolsWarning

with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always", BeebToolsWarning)
    raw = buildImage("src/", "out.ssd", save=True)

for w in caught:
    print(w.message)
```

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
