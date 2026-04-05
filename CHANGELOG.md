# Changelog

All notable changes to this project will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.1] - 2026-04-05

### Added

- `basicProgramSize(data)`: returns the byte length of the BASIC program
  portion of a tokenized file, excluding any appended machine code.

- The `cat` command now shows the track count in the header line for each side.
  
- New property on `DFSCatalogue` and `ADFSCatalogue`: returns the number of tracks on the disc, calculated from the total sector count.

- `cat --inspect` now content-inspects files without a BASIC exec address,
  detecting `BASIC?` (content-detected BASIC with non-standard exec),
  `BASIC+MC` (BASIC with appended machine code), and `TEXT` labels. Each
  label has a distinct colour.

- `extract -t/--text` option controls how non-ASCII bytes in BASIC strings
  (e.g. teletext control codes) are written: `ascii` (lossy, default), `utf8`
  (lossless), or `escape` (`\xHH` notation, lossless). The `build` command
  auto-detects all three formats when retokenizing.

- `extractFile()` function for single-file extraction with automatic lookup,
  BASIC detection, and detokenization. Returns an `ExtractedFile` dataclass.

- `addFileTo()` function wraps `side.addFile()` with optional retokenization
  of plain-text BASIC files.

- `classifyFileType()` inspects a file's metadata and content to return a
  classification string (`"BASIC"`, `"BASIC+MC"`, `"BASIC?"`, `"TEXT"`,
  or `"binary"`).

- `qualifyDiscPath()` normalises user-supplied filenames to fully-qualified
  disc paths (e.g. `"MYPROG"` becomes `"$.MYPROG"`).

### Changed

- Merged `detokenize.py` and `tokenize.py` into `basic.py`. All BASIC
  program operations (tokenize, detokenize, classify, escape) are now in a
  single module. The public API is unchanged - import from `beebtools`
  as before.

### Fixed

- **`looksLikeTokenizedBasic` false positives.** Plain-text files starting
  with CR (0x0D) were misidentified as BASIC. Detection now walks the
  tokenized line structure and requires the 0x0D 0xFF end-of-program marker.

- **Teletext control codes lost during BASIC extraction.** Non-ASCII bytes
  (e.g. colour codes in `PRINT` strings) were silently replaced with `?`
  by the ASCII file writer. Use `--text utf8` or `--text escape`
  to preserve them.

- **Build from extracted content fails.** Rebuilding a disc image from
  extracted files failed because BASIC files were not correctly retokenized
  before being written back to the disc.

- **Fixed garbled file and disc names on some disc images.** BBC Micro ASCII
  is 7-bit; bit 7 is repurposed by some filing systems and copy-protection
  schemes. A `"bbc"` text codec is now registered on `import beebtools`,
  masking bit 7 correctly. The codec is available globally in Python via
  `bytes.decode("bbc")`.

- **Detokenizer hang** on files containing a BASIC program with appended
  machine code. A zero-length line record in the binary tail caused an
  infinite loop in `detokenize()`. Files containing a BASIC loader with appended 
  6502 machine code are now saved as `.bin` (preserving the binary payload) and
  show `BASIC+MC` when cataloguing a disk.

---

## [0.5.0] - 2026-04-03

Internal refactor to enforce strict module layering and eliminate code that
bled across format boundaries. No CLI or behavioural changes - all existing
commands work identically.

### Added

- `createImage()` function: creates a blank disc image in the correct format
  based on the file extension, replacing separate `createDiscImage()` /
  `createAdfsImage()` calls.

### Changed

The library API has been simplified so that DFS and ADFS disc images are
handled through a single set of functions rather than format-specific ones.

- **Breaking:** `addFile()` now takes a single `DiscFile` object instead of
  individual keyword arguments.

- **Breaking:** `buildImage()` now handles both DFS and ADFS formats (detected
  from the output path extension). `buildAdfsImage()` is removed.

- **Breaking:** `sortCatalogueEntries()` accepts any entry type, not just
  `DFSEntry`, and sorts by `fullName`.

- `DFSError` and `ADFSError` now share a common `DiscError` base class.
  Existing `except DFSError` blocks still work; new code can catch `DiscError`
  to handle both formats.

### Removed

Old aliases and wrapper functions that were carried forward from earlier
releases have been cleaned up.

- `buildAdfsImage()` - use `buildImage()` with an `.adf`/`.adl` output path.

- `isBasic()` standalone function - use the `isBasic` property on entries.

- `DFSDisc` alias - use `DFSImage`.

- `looksLikeText()` alias - use `looksLikePlainText()`.

---

## [0.4.0] - 2026-04-03

### Added

- **ADFS disc image support** (read-only): `beebtools` now reads `.adf`
  (single-sided) and `.adl` (double-sided) ADFS disc images using the old-map
  small-directory ("Hugo") format. Supports ADFS-S (160K), ADFS-M (320K), and
  ADFS-L (640K) images. All read commands (`cat`, `extract`, `search`) work
  transparently with both DFS and ADFS images.

- **ADFS write support**: `beebtools create`, `add`, `delete`, and `build`
  commands now work with ADFS disc images (`.adf`, `.adl`). Format is detected
  from the file extension. Create supports ADFS-S (160K, 40-track `.adf`),
  ADFS-M (320K, 80-track `.adf`), and ADFS-L (640K, `.adl`). File paths use
  the ADFS hierarchical syntax (e.g. `$.GAMES.ELITE`). Build walks the source
  directory tree recursively, creating subdirectories as needed.

- `createAdfsImage()` library function: creates a blank ADFS disc image with
  valid free space map and root directory. Accepts format size, title, and boot
  option parameters.

- `validateAdfsName()` library function: validates ADFS filenames (1-10
  printable ASCII characters).

- `buildAdfsImage()` library function: assembles an ADFS disc image from a
  directory tree with `.inf` sidecars, including recursive subdirectory creation.

- `openImage()` format auto-detection dispatcher: detects DFS or ADFS from the
  file extension and delegates to the appropriate parser. Exported from the
  public API.

- ADFS library types exported from the public API: `ADFSEntry`, `ADFSCatalogue`,
  `ADFSDirectory`, `ADFSFreeSpaceMap`, `ADFSImage`, `ADFSSide`, `ADFSError`,
  `ADFSFormatError`, `openAdfsImage`.

- `beebtools cat` on ADFS images shows the full hierarchical directory tree with
  directory entries labelled as `DIR`. Column width adjusts dynamically for long
  ADFS path names.

- `beebtools extract` accepts ADFS full paths (e.g. `$.GAMES.ELITE`) as well as
  bare filenames. Bulk extraction (`-a`) creates nested filesystem directories
  matching the ADFS directory hierarchy. Directory entries are skipped.

- `beebtools search` works on ADFS images, searching all BASIC files across the
  entire directory tree.

- BBC BASIC II tokenizer (`tokenize.py`): converts LIST-style plain text back
  to tokenized binary - the inverse of the detokenizer. `tokenize()` and
  `encodeLineRef()` are exported from the public API.

- `beebtools add --basic` now auto-tokenizes plain-text BASIC files before adding
  them to a disc image, enabling a full detokenize-edit-retokenize workflow.

- `--basic` flag for `beebtools add`: sets BBC BASIC default addresses
  (load=0x1900, exec=0x8023) without needing to specify them manually.
  Explicit `--load` or `--exec` flags override the corresponding default
  with an informational note. Ignored with a warning when `--inf` is used.

### Fixed

- DFS filename validation now rejects the characters `. : " # *` and space,
  matching the Acorn DFS disc format specification. Previously only the
  printable ASCII range was checked.

---

## [0.3.0] - 2026-03-31

### Added

- `beebtools create` command: create blank SSD/DSD disc images with configurable
  track count (40/80), disc title, and boot option.

- `BootOption` enum (`OFF`, `LOAD`, `RUN`, `EXEC`) replaces the plain
  `BOOT_OPTIONS` dict. `BootOption.parse()` accepts names (case-insensitive)
  or numbers 0-3. The `--boot` CLI flag now accepts names like `--boot RUN`.

- `beebtools add` command: add files to existing disc images with metadata from
  command-line flags (`--name`, `--load`, `--exec`, `--locked`) or from a `.inf`
  sidecar file (`--inf`).

- `beebtools delete` command: remove files from disc images by DFS name.

- `beebtools build` command: assemble a disc image from a directory tree of files
  with `.inf` sidecars, enabling a full extract-edit-rebuild workflow.

- `--inf` flag for `beebtools extract -a`: writes `.inf` sidecar files alongside
  extracted data files, preserving DFS load/exec addresses, length, and lock flag.

- `buildImage()` library function for programmatic disc image assembly.

- `.inf` sidecar format module (`inf.py`) with `parseInf()` and `formatInf()`
  for the standard BBC Micro community interchange format. Parses
  `DIR.NAME LLLLLL EEEEEE SSSSSS [L] [CRC=XXXX]` lines, handles 6-digit and
  8-digit hex, bare filenames (default to `$`), optional lock flag and CRC.

- DFS name validation function `validateDfsName()` - checks directory character
  and filename against DFS naming rules before writing to disc.

- File operations on disc images: `addFile()`, `deleteFile()`, `compact()`,
  and `freeSpace()` on `DFSSide` for programmatic disc image manipulation.

### Changed

- Extracted files now use a hierarchical directory layout with the DFS directory
  character as a real subdirectory. Single-sided: `out/$/BOOT.bas`,
  `out/T/MYPROG.bas`. Double-sided: `out/side0/$/BOOT.bas`,
  `out/side1/T/GAME.bas`.

### Changed

- Documentation restructured: README trimmed to a lean overview with detailed
  per-command reference pages, library guide, and pretty-printer docs under
  `docs/`.

### Removed

- `--sides` (`-s`) flag for `beebtools extract -a`. The flat prefix layout has
  been removed in favour of the hierarchical directory layout.

- `BOOT_OPTIONS` dict removed. Use `BootOption` enum instead.

---

## [0.2.0] - 2026-03-30

### Added

- `search` subcommand: search all BASIC files on a disc for lines containing a
  text pattern. Prints matching lines with filename and BBC BASIC line number.
  Flags: `-i`/`--ignore-case` for case-insensitive matching, `--pretty` to apply
  the pretty-printer before searching.

- `searchDisc(image_path, pattern, filename, ignore_case, pretty)` library
  function: returns a list of match dicts with keys `side`, `filename`,
  `line_number`, and `line`.

- `looksLikePlainText()` library function: returns True when all bytes in a file
  are printable ASCII or common whitespace (tab, CR, LF).

- `--inspect` (`-i`) flag for `beebtools cat`: reads each file's bytes to
  detect and label plain ASCII text files as `TEXT` in the type column.
  Without this flag, type detection uses only catalogue metadata (faster).

- `beebtools cat` output is colourised when writing to a terminal: disc header
  in bold, `BASIC` in cyan, `TEXT` in yellow, locked flag `L` in red, and
  load/exec/length addresses in dark grey. Colour is suppressed automatically
  when stdout is piped or redirected.

### Changed

- Bulk extraction from double-sided disc images now separates files by side
  automatically (subdir layout) rather than requiring an explicit flag.

- Bulk extraction now produces three file types: BASIC programs as `.bas`,
  plain ASCII text files as `.txt`, and everything else as `.bin`.
  BBC CR-only line endings in `.txt` files are normalised to LF on output.

- DFS filenames are sanitized in bulk extraction output: the `.` directory
  separator is replaced with `_` (e.g. `T.MYPROG` becomes `T_MYPROG`), and
  Windows-illegal characters are encoded as `_xNN_` to guarantee uniqueness.
  (Note: this flat layout was later replaced by hierarchical directories in
  a subsequent release.)

## [0.1.1] - 2026-03-30

### Changed

- Updated installation instructions in README to reflect PyPI availability.

## [0.1.0] - 2026-03-30

### Added

- DFS disc image reader supporting `.ssd` and `.dsd` formats.

- BBC BASIC II detokenizer: decodes tokenized binary programs to LIST-style text,
  including inline line-number references (GOTO/GOSUB targets).

- Pretty-printer: adds operator spacing to detokenized BASIC, with correct
  handling of string literals, REM tails, DATA tails, and star commands.

- Anti-listing trap detection: `*|` MOS comment traps are converted to `REM *|`
  with control characters stripped.

- `beebtools cat` command: list disc catalogues with load/exec/length and file type.

- `beebtools extract` command: extract single files or bulk-extract all files,
  with optional pretty-printing for BASIC programs.

- Filename matching by explicit DFS path (`T.MYPROG`) or bare name with
  ambiguity detection.
