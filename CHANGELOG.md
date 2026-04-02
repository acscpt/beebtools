# Changelog

All notable changes to this project will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
