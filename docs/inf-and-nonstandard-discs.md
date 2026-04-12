# A Field Guide to Non-standard BBC Micro Disc Images

## Contents

1. [Introduction](#1-introduction)
2. [Disc images](#2-disc-images)
3. [Round-tripping](#3-round-tripping)
4. [We did not fail, we just found 41 ways that did not work](#4-we-did-not-fail-we-just-found-41-ways-that-did-not-work)
5. [Attacking the problem](#5-attacking-the-problem)
6. [The .inf format: definition and extension](#6-the-inf-format-definition-and-extension)
7. [Putting Humpty together again](#7-putting-humpty-together-again)

---

## 1. Introduction

`beebtools` is a command-line utility and Python library for working with
BBC Micro, BBC Master, and Acorn Electron disc images. 

It reads and writes the DFS and ADFS filing systems, detokenizes and retokenizes
BBC BASIC programs, and provides a complete set of disc operations including catalogue,
extract (BASIC detokenize/tokenize), add, delete, build, rename, search, and more.

The project grew out of a specific _itch_. There was a particular disc
image that needed its files extracted and examined (the curious can find
the backstory in the neighbouring repository 
[BBC-Micro Golden Brown](https://github.com/acscpt/bbc-micro-golden-brown)). 

What started as _"just read this one disc"_ quickly became
_"read any disc"_, then _"rebuild a disc from extracted files"_, and
eventually _"rebuild any disc, byte-for-byte, no matter what the original
authors did to it"_.

It turns out that reading a disc image is the easy part and just like the 
proverbial _Humpty Dumpty_, putting it back together again (and getting the same 
bytes out the other side) is where things get interesting, very interesting.


## 2. Disc images

But first, a quick primer on disc images.

- A BBC Micro disc stores data in 256-byte sectors, arranged in tracks.

- A standard single-sided disc has 40 or 80 tracks of 10 sectors each,
  giving 100K or 200K of storage.

- Double-sided discs double that by using both surfaces, with the two
  sides either stored sequentially (SSD format) or interleaved
  track-by-track (DSD format).

### The catalogue

Every DFS disc reserves its first two sectors (sector 0 and sector 1)
for the catalogue. This is a flat table of up to 31 file entries, each
recording:

- a 7-byte filename (space-padded)

- a single-byte directory character (the low 7 bits; the high bit is
  the lock flag)

- a 16-bit load address

- a 16-bit execution address

- an 18-bit file length

- a 10-bit start sector

The catalogue also holds:

- A 12-character disc title

- A BCD cycle number

- A boot option

- The disc size in sectors
  
Files are allocated from the top of the disc downward. The first file written 
occupies the highest-numbered sectors, and each subsequent file is packed below it.
There is no free-space bitmap; the only gap tracking is implicit in the start 
sectors of the existing entries.

### ADFS

ADFS is a more sophisticated filing system. It supports nested
directories, a free-space map, longer filenames, and access control
bits for read, write, execute, and lock. ADFS disc images come in
several sizes (S, M, L) and the internal layout is quite different
from DFS. For the purposes of this article, the key point is that
ADFS has an explicit free-space map that tracks which sectors are in
use, while DFS has nothing of the sort.

### What DFS does not check

This is the important bit. The DFS ROM on a real BBC Micro is
remarkably permissive. When it writes a catalogue entry, it
byte-pushes the values into the sector without validation. The spec
says filenames should be printable ASCII in the range `0x21-0x7E`,
excluding the characters `. : " # *` and space. The ROM does not
enforce this. It does not check that the disc size field is sane.
It does not check that the sector ranges of two files are disjoint. It
simply writes what it is told and reads what it finds.

This permissiveness is not a bug. It is a design choice in a system
where every byte of ROM space matters and the filing system is a
thin wrapper around raw sector I/O. But it means that real-world
disc images contain things that no spec says should be there, and
any tool that wants to handle real-world discs needs to handle them
too.

## 3. Round-tripping

The natural test for any disc tool is the round-trip. Extract every
file from an image, rebuild a new image from those files, and check
that the result is byte-for-byte identical.

The cycle works like this:

1. Open a disc image and iterate every file on every side.

2. For each file, write its raw bytes to a data file (`.bin`),
   - but this is not enough as the catalogue meta data is lost.  To mitigate this
     we must also write a companion `.inf` sidecar file that records the metadata
     of the original disc on the host filesystem - this is describe in 
     [What is an .inf sidecar](#What-is-an-.inf-sidecar)

3. Point the build command at the directory of extracted files and
   sidecars. The builder reads each sidecar, loads the corresponding
   data file, allocates sectors, writes the catalogue, and produces
   a fresh image.

4. Open the rebuilt image and compare the SHA-256 digest of every file
   against the original.

5. Any mismatch or packing error is a failure.

### What is an .inf sidecar

The `.inf` sidecar is a convention that dates back to early BBC Micro
emulation tools like TubeHost and BeebLink. It is a plain text file,
one line per entry, with whitespace-separated fields:

    $.!BOOT  00000E00 00008023 00000010 00

The fields are the Acorn filename (with directory prefix), the load
address, the execution address, the file length, and an access byte,
all in hexadecimal. 

Every BBC disc tool in the preservation community reads and writes this format.
It is the _lingua franca_ of extracted disc files.

### Sourcing disc images

To test the round-trip at scale we needed disc images. A lot of them.
Two archives proved invaluable:

- [Stairway to Hell](https://www.stairwaytohell.com/) - a curated
  collection of BBC Micro software, painstakingly archived from
  original media. We sourced 2298 disc images from this archive.

- [8bs](https://8bs.com/) - a long-running BBC Micro magazine on
  disc, distributed as disc images. We sourced 143 images from this
  collection, covering both DFS and ADFS formats.

Together, **2441** disc images spanning decades of BBC Micro software:
games, utilities, demos, magazines, educational software, and more.

Truly a comprehensive collection to test against.

## 4. We did not fail, we just found 41 ways that did not work

We used a scripted _round-trip harness_ and pointed it at the combined **2441** disc
images, and promptly _ate our own dogfood_.

The harness used the exact same [library](library.md) calls that
the `beebtools` CLI uses:

- `openImage` to read

- `formatInf` to write sidecars

- `buildImage` to rebuild

No shortcuts, no special cases.

The first run produced 41 failures across the
[Stairway to Hell](https://www.stairwaytohell.com/) (36) and
[8bs](https://8bs.com/) (5) collections. That gave us a 98.3% pass rate, which sounds good until you 
look at the error messages.

### 4.1 Analysis

The 41 failures fell into seven distinct categories. None of them were
bugs in the logic of the tool. Every single one traced back to a disc
image whose catalogue contained something that the DFS spec says
should not be there, but that the DFS ROM accepts without complaint,
or to a mismatch between the filesystem-safe names used on the host
and the original Acorn disc names.

| # | Failure type | Stairway | 8bs | Total | Error reported | Likely purpose |
|---|---|:---:|:---:|:---:|---|---|
| 1 | Spec-forbidden `.` in DFS filename | 18 | 2 | 20 | Validation error on forbidden character | Real-world Acorn filenames |
| 2 | Spec-forbidden `#` in DFS filename | 6 | - | 6 | Validation error on forbidden character | Real-world Acorn filenames |
| 3 | Overlapping sector allocations | 5 | - | 5 | `Not enough free space` on rebuild | Copy protection / space saving |
| 4 | Non-printable control byte in filename | 4 | - | 4 | Validation error on byte `0x06` | Anti-tampering |
| 5 | Sanitised filesystem name vs disc name | - | 3 | 3 | ADFS name length or path mismatch | Filesystem encoding artefact (bug!)|
| 6 | Degenerate all-space filename | 1 | - | 1 | `.inf` parse error (`invalid literal for int()`) | Copy protection / padding |
| 7 | DEL (`0x7F`) as directory character | 1 | - | 1 | Validation error on directory byte `0x7F` | Hidden catalogue padding |
| 8 | Zero disc size in catalogue header | 1 | - | 1 | `Track count must be 40 or 80, got 0` | Authoring anomaly |

A few patterns stand out:

- Categories 1 and 2 are the single biggest lever: 26 of the 41
  failures are DFS filenames that contain a dot or a hash character.
  The DFS spec forbids both, but real commercial discs use them
  freely. Level 9 adventures, Blue Ribbon compilations, and
  Superstar cheat discs all have filenames with dots.

- Category 3 is entirely from one publisher:
  [Level 9 Computing](https://en.wikipedia.org/wiki/Level_9_Computing),
  makers of text adventure games in the 1980s. On the affected discs,
  multiple catalogue entries share the same physical sectors - the
  data is stored once but catalogued twice with different lengths.
  Since the overlapping entries contain the same bytes, this is not a
  space-saving trick. It looks like copy protection because any disc copier
  that extracts files individually and rebuilds with fresh sector
  allocations will break the layout. The pattern is consistent across
  every Level 9 game disc in the collection.

- Category 5 is an ADFS-only problem. When a disc name like `T>D`
  or `Arch-S/W` is extracted to the host filesystem, characters
  illegal on Windows are encoded as `_xNN_` (e.g. `T_x3E_D`). The
  rebuild was using the filesystem directory name instead of the
  `.inf` sidecar, so it tried to create an ADFS file called
  `T_x3E_D` rather than `T>D`.

- Category 4 is another anti-tampering trick. The filename looks
  normal on screen, but a hidden control byte (`0x06`) at the end
  means you cannot `*DELETE` or `*RENAME` the file by typing its
  apparent name - the invisible byte is part of the match and the
  ROM requires an exact seven-byte match to find the entry.

- Categories 6, 7, and 8 are one-off anomalies: a blank filename,
  the DEL character as a directory prefix, and a zero disc-size
  field. Each is a single disc doing something unusual, but each
  reveals a different assumption that needed relaxing.

The failures all share a common theme: the disc works on a real BBC
Micro (or in an emulator), but the assumptions in the tool about what a
"valid" disc looks like are too narrow.

### 4.2 Assumptions about the DFS ROM

The DFS spec is a description of what well-behaved software should
produce. The DFS ROM is a description of what the hardware will
accept. These are not the same thing.

The catalogue handling in the ROM is straightforward:

- **Filenames**: The catalogue stores seven bytes per filename. The
  ROM does not inspect their values. Bytes `0x00` through `0x7F` are
  all stored faithfully. The "printable ASCII only" rule in the spec is a
  recommendation to disc authors, not a check in the code.

- **Directory character**: The catalogue stores one byte per entry.
  The low 7 bits are the directory, the high bit is the lock flag.
  Any 7-bit value works. `0x7F` (DEL) is as valid as `$` or `T` as
  far as the ROM is concerned.

- **Sector allocation**: Each catalogue entry records a start sector
  and a length. The ROM does not check whether the sector ranges of
  two entries overlap. If they do, both entries read successfully; the
  shared sectors simply return the same bytes regardless of which
  filename you asked for.

- **Disc size**: The 10-bit disc size field in sector 1 records the
  total sector count. The ROM does not consult this field when
  reading or writing files. It addresses sectors directly. A disc
  with this field set to zero works identically to one with the
  correct value.

Software engineering is often a balance between the _ivory tower_ of
spec-perfect idealism and the pragmatic reality of what actually
exists in the wild. A level of pragmatism is needed here, the spec
describes the disc that should exist, but the actual ROM accepts and processes 
disc that do not meet that ideal.

Thus any tool needs to recognise that reality.


## 5. Attacking the problem

Before changing any code, the first step was to confirm that the
problematic disc images are genuinely valid. If a disc does not boot
on real hardware (or a faithful emulator), there is nothing much that can be done.

### Emulator verification

Each problematic image was loaded in two independent emulators:

- [jsbeeb](https://bbc.xania.org/) - a cycle-accurate BBC Micro
  emulator that runs in the browser.

- [b2](https://github.com/tom-seddon/b2) - a desktop BBC Micro
  emulator with extensive debugging features.

For each problematic image standard DFS commands were used within the emulator:

- `*CAT` to list the catalogue and confirm the entry was visible.

- `*INFO` to inspect the metadata (load address, exec address,
  length, start sector) of the unusual entries.

- `*TYPE` or `*DUMP` where applicable to inspect the file contents.

- `*LOAD` to attempt loading the file, or `*RUN` / `CHAIN` to
  boot the disc and confirm it runs.

Every image loaded and ran correctly. The _Level 9_ games played. The
cheat discs cheated and in _Lord of the Rings_ _Frodo_ walked into Mordor (eventually).
The disc images are valid. 

The tool was wrong to reject them.

### Fixing each failure mode

With the emulator evidence in hand, each category got its own fix.

#### Overlapping sector allocations

This is the most structurally interesting failure. When two catalogue
entries share the same sectors, extracting both entries produces two
separate data files with their own bytes. Rebuilding then tries to
allocate independent sector ranges for both, and the disc runs out
of space because the shared sectors are now counted twice.

The only way to handle this is to note the original start sector of
each file at extract time. When the image is rebuilt, that recorded
position can be passed through to the format engine so the file is
written at the exact same sector it occupied on the original disc,
rather than being handed a fresh range by the normal free-space
allocator. The shared sectors get written twice, with the same
bytes, and the rebuilt disc is byte-identical.

The mechanism for carrying that start sector through the
extract-rebuild cycle is described in section 6.

There is a subtlety in the write order. If file A occupies sectors
190-274 and file B occupies sectors 190-270 (a subset), the file
with the longer extent must be written last. Otherwise the bytes
from file A in sectors 271-274 would be overwritten by whatever file B
writes next.
The builder sorts placed files by their end sector in ascending
order so that the longest file at any given start position always
has the final word.

#### Relaxed filename validation

Four categories of failure (control bytes, all-space names,
forbidden punctuation, DEL directory) all reduce to the same root
cause: the validator was enforcing the character range from the spec
where the ROM does not.

The fix is a two-tier validation model:

- **Default (ROM-faithful) mode**: accepts any 7-bit byte
  (`0x00-0x7F`) in the directory character and in each byte of the
  filename. This matches what the hardware accepts.

- **Strict mode**: narrows to the spec range (`0x21-0x7E`) and
  rejects the spec-forbidden characters `. : " # *` and space. This
  is for authoring new discs where spec compliance matters.

When working with original disc images default to the relaxed mode.
Use strict mode when authoring new images from scratch.

#### Catalogue disc size reconciliation

One disc image stores a zero in the `disc_size` catalogue field. The
image file is a perfectly valid 40-track SSD (102,400 bytes, 400
sectors), but the metadata says zero.

It was clear that the ROM ignores this field, the fix is to do what the ROM 
does: ignore it when it is obviously wrong. 

The catalogue reader now checks whether the stored disc size is a valid 40- or 
80-track sector count. 

If it is not, the reader derives the track count from the byte length
of the image file and uses that instead. A warning is emitted so the anomaly
is visible, but the pipeline keeps running.

#### Degenerate name fields and `.inf` collisions

The all-space filename produces a name that vanishes when trailing
spaces are stripped, which cascades into a malformed `.inf` line where
the hex address fields run into each other. Two fixes work together
here:

- The catalogue reader preserves a degenerate all-space name as a
  single space character rather than collapsing it to the empty string.

- The `.inf` formatter quotes the name field whenever it contains
  anything that would be ambiguous as a bareword, including space,
  control bytes, or nothing at all.

With quoting, every name field is unambiguously delimited and the
parser never confuses name bytes with address fields.

#### The .inf sidecar as the source of truth

When a disc name like `T>D` or `Arch-S/W` is extracted to the host
filesystem, characters that are illegal on Windows are encoded for
safety (e.g. `T_x3E_D`). The original rebuild path was using these
filesystem directory names as the disc name, so it tried to create
an ADFS file called `T_x3E_D` instead of `T>D`.

The fix is to ignore the filesystem name entirely. The `.inf` sidecar
carries the original Acorn disc name, and the builder now uses that
as the sole source of truth when adding files to the rebuilt image.
The filesystem layout is only used to discover which data files exist;
the disc path comes from the sidecar.

For ADFS images this also required auto-creating parent directories.
If a sidecar says a file lives at `$.GAMES.ELITE`, the builder
creates the `$.GAMES` directory automatically before adding the file.


## 6. The .inf format: definition and extension

The `.inf` sidecar is central to the round-trip story and its intent is to, along 
with the bytes of the actual file, to represent the metadata of that file.

It started life as an informal convention but has gained a formalisation.

### The Stardot spec

[Stardot](https://www.stardot.org.uk/forums/) is the spiritual home of
the BBC Micro and Acorn enthusiast community. A
[forum discussion](https://stardot.org.uk/forums/viewtopic.php?t=31577)
among its members led to the
[Stardot .inf Format Specification](https://github.com/stardot/inf_format),
which formalizes the sidecar grammar.

It defines three syntax variants:

- **Syntax 1**: the canonical five-field form.

  ```
  NAME  LOAD EXEC LENGTH ACCESS [KEY=value ...]
  ```

- **Syntax 2**: the historical TubeHost/BeebLink form, without a
  length field.

  ```
  NAME  LOAD EXEC [L] [KEY=value ...]
  ```

- **Syntax 3**: the ADFS Explorer directory form, with a symbolic
  access string instead of hex addresses.

  ```
  NAME  ACCESS [KEY=value ...]
  ```

All three are accepted on input. Output always uses syntax 1, since
it carries the most information.

#### Quoted names and percent-encoding

The spec introduces RFC 3986 style quoting. A name field can be
wrapped in double quotes, and any byte inside the quotes can be
encoded as `%XX`:

- `%20` for space

- `%22` for double quote

- `%25` for the literal percent character

- `%06` for a control byte

- `%2E` for a literal dot that should not be interpreted as a path
  separator

This last point matters. DFS filenames can contain literal dot bytes,
and ADFS paths use dots as directory separators. The unescaped dot
in a `.inf` line is always a separator; a `%2E` is always a literal
dot inside the filename. This disambiguates the two without
heuristics.

A sidecar for the cheat disc filename `Z.BLANK\x06` looks like:

    "Z.BLANK%06"  00001900 00008023 00000400 00

The filesystem file might be called `BLANK_x06_.bin` (with the
control byte encoded for filesystem safety), but the rebuild ignores
the filesystem name entirely. The sidecar is the source of truth.

#### Extra-info: KEY=value extension fields

Everything after the five fixed fields is free-form:

```
$.MYPROG  00001900 00008023 00002000 00 CRC=4D2E OPT4=3
```

Each `KEY=value` pair is preserved on read and round-tripped on write.
Keys are alphanumeric plus underscore. Common keys in the wild:

- `CRC` - 16-bit file checksum

- `CRC32` - 32-bit file checksum

- `OPT4` - DFS boot option

- `TITLE` - disc title

- `DATETIME` - authoring timestamp

### Extending .inf with placement hints for byte-exact rebuilds

To solve the overlapping sector allocation problem described in
section 5, the original start sector of each file needs to survive
the extract-rebuild cycle. The extra-info mechanism above is the
natural place to carry it.

Borrowing from the HTTP community, where experimental headers are
prefixed with `X-` to signal they are not yet standardised,
`beebtools` adds the key `X_START_SECTOR`:

    $.GData1  00001900 00008023 00005400 00 X_START_SECTOR=190

On rebuild, the `beebtools` builder reads the annotation and passes it to the
format engine as a placement hint. 

- DFS honours the hint unconditionally and writes the file at the specified sector. 

- ADFS honours the hint only when the requested range is wholly free in the
free-space map; if the range overlaps a directory or another file,
it falls back silently to normal allocation. This safety valve
prevents the hint from corrupting an ADFS image, while still enabling
byte-exact round-trips on DFS where the Level 9 overlap trick lives.

For (potential) future proofing the `beebtools` reader accepts both `START_SECTOR` 
and `X_START_SECTOR`. 

## 7. Putting Humpty together again

With all the above mentioned approaches and fixes in place, the round-trip harness 
passes clean:

| Archive | Images | Pass | Fail | Rate |
|---|---|---|---|---|
| [Stairway to Hell](https://www.stairwaytohell.com/) | 2298 | 2298 | 0 | 100% |
| [8bs](https://8bs.com/) | 143 | 143 | 0 | 100% |
| **Total** | **2441** | **2441** | **0** | **100%** |

Every disc image that works on a real BBC Micro (or faithful
emulator) now extracts and rebuilds with byte-identical file content.
All 41 original failures are resolved without special cases or
per-image workarounds.

The fixes are general:

- relaxed validation accepts what the ROM accepts

- sector placement preserves what the catalogue records

- the `.inf` format carries whatever the original filename contained,
  along with additional metadata like the start sector

Disc images are historical artifacts. They record not just the
software that ran on these machines, but the ingenuity of the people
who wrote them.
