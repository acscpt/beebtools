# Changelog

All notable changes to this project will be documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `--sides` flag (`-s`) for `beebtools extract -a` on double-sided `.dsd` images.
  `subdir` mode (default) writes files into `side0/` and `side1/` subdirectories;
  `prefix` mode prepends `side0_` or `side1_` for a flat output layout.

### Changed

- Bulk extraction from double-sided disc images now separates files by side
  automatically (subdir layout) rather than requiring an explicit flag.

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
