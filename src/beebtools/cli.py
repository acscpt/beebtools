# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""CLI commands for beebtools.

Implements the 'cat', 'extract', 'search', 'create', 'add', 'delete',
and 'build' subcommands and the main() entry point.
"""

import argparse
import contextlib
import importlib
import os
import pkgutil
import re
import sys
import warnings
from argparse import Namespace
from typing import Dict, Optional

import beebtools

from .boot import BootOption
from .entry import DiscError, DiscFile, FileType
from .inf import parseInf
from .shared import BeebToolsWarning
from .validation import strictMode
from .disc import (
    search, extractAll, buildImage, createImageFile,
    readCatalogue,
    extractFile, addFile, qualifyDiscPath,
    writeBasicText, escapeNonAscii,
    getTitle, setTitle, getBoot, setBoot, discInfo,
    getFileAttribs, setFileAttribs,
    deleteFile, renameFile, compactDisc, makeDirectory,
)


# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------

_BOLD    = "\x1b[1m"
_CYAN    = "\x1b[96m"
_GREEN   = "\x1b[92m"
_YELLOW  = "\x1b[93m"
_MAGENTA = "\x1b[95m"
_RED     = "\x1b[91m"
_GREY    = "\x1b[90m"
_RESET   = "\x1b[0m"


def _parseBootOption(value: str) -> BootOption:
    """Argparse type wrapper around BootOption.parse()."""
    try:
        return BootOption.parse(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _colour(text: str, code: str, enabled: bool) -> str:
    """Wrap text in an ANSI escape sequence when colour is enabled."""
    if not enabled:
        return text
    return f"{code}{text}{_RESET}"


def _loadResourceBundles(consumer: str = "cli") -> Dict[str, str]:
    """Merge ``RESOURCES[consumer]`` from every ``*_resources`` module.

    Walks ``beebtools.__path__`` with ``pkgutil.iter_modules``, imports
    each module whose name ends in ``_resources``, and collects the
    entries under the requested consumer key into a single flat dict.
    When multiple bundles contribute the same key, their values are
    concatenated so every format's help block reaches the user.
    """

    merged: Dict[str, str] = {}

    # Sort by module name so output ordering is deterministic across
    # runs and platforms instead of depending on filesystem order.
    module_names = sorted(
        info.name for info in pkgutil.iter_modules(beebtools.__path__)
        if info.name.endswith("_resources")
    )

    for module_name in module_names:

        module = importlib.import_module(f"beebtools.{module_name}")

        # Resource modules are expected to expose a ``RESOURCES`` dict
        # keyed by consumer name. Missing or malformed modules are
        # ignored rather than exploding at startup.
        resources = getattr(module, "RESOURCES", None)
        if not isinstance(resources, dict):
            continue

        bundle = resources.get(consumer)
        if not isinstance(bundle, dict):
            continue

        # Concatenate on key clashes so each format's block contributes
        # to the combined text. A blank line separates existing content
        # from the newcomer for legibility in aggregated help output.
        for key, value in bundle.items():
            if key in merged:
                merged[key] = merged[key].rstrip("\n") + "\n\n" + value
            else:
                merged[key] = value

    return merged


# Colour mapping for classified file types in the catalogue listing.
# Keyed on FileType enum members; the label is the member's display value.
_TAG_COLOURS = {
    FileType.BASIC:     (_CYAN,    FileType.BASIC.value),
    FileType.BASIC_MC:  (_MAGENTA, FileType.BASIC_MC.value),
    FileType.BASIC_ISH: (_GREEN,   FileType.BASIC_ISH.value),
    FileType.TEXT:      (_YELLOW,  FileType.TEXT.value),
}


def _formatLabel(output_path: str, tracks: int, size_bytes: int) -> str:
    """Return a human-readable disc format label for CLI output.

    Derives the label from the file extension of output_path. DFS
    formats report the track count; ADFS formats report the image
    size in kilobytes.

    Args:
        output_path: Path to the disc image.
        tracks:      Number of tracks (used for DFS labels).
        size_bytes:  Image size in bytes (used for ADFS labels).

    Returns:
        A label such as "80-track SSD" or "640K ADL".
    """
    ext = os.path.splitext(output_path)[1].lower()
    labels = {
        ".ssd": f"{tracks}-track SSD",
        ".dsd": f"{tracks}-track DSD",
        ".adf": f"{size_bytes // 1024}K ADF",
        ".adl": f"{size_bytes // 1024}K ADL",
    }
    return labels.get(ext, ext)


def cmdCat(args: Namespace) -> None:
    """Print the disc catalogue to stdout.

    Args:
        args: Parsed argparse namespace for the 'cat' subcommand.
    """
    # Enable colour only when writing to a real terminal.
    use_colour = sys.stdout.isatty()

    listings = readCatalogue(args.image, sort_mode=args.sort, inspect=args.inspect)

    for listing in listings:
        side_label = f"Side {listing.side}"
        header = f"--- {side_label}"

        if listing.title:
            header += f": {listing.title}"

        boot_desc = listing.boot_option.name
        header += (f" ({listing.entry_count} files,"
                  f" {listing.tracks} tracks,"
                  f" boot={boot_desc}) ---")
        print(_colour(header, _BOLD, use_colour))
        print()

        if not listing.entries:
            print("  (empty)")
        else:
            # Dynamic column widths. The name column grows with ADFS
            # hierarchical paths; the access column grows with ADFS
            # owner/public combinations like LWR/wre.
            max_name = max(len(ce.entry.fullName) for ce in listing.entries)
            col_width = max(12, max_name + 2)

            max_access = max(
                (len(ce.entry.accessString) for ce in listing.entries),
                default=0,
            )
            access_width = max(6, max_access)

            print(
                f"  {'Access':<{access_width}s}  "
                f"{'Name':<{col_width}s} "
                f"{'Load':>8s} {'Exec':>8s} {'Length':>8s}  "
                f"{'Type'}"
            )

            for ce in listing.entries:
                e = ce.entry

                if e.isDirectory:
                    ftype = _colour("DIR", _CYAN, use_colour)
                elif ce.file_type in _TAG_COLOURS:
                    colour, label = _TAG_COLOURS[ce.file_type]
                    ftype = _colour(label, colour, use_colour)
                else:
                    ftype = ""

                access_text = e.accessString or "-"
                access = _colour(
                    f"{access_text:<{access_width}s}",
                    _RED, use_colour and e.locked,
                )
                load   = _colour(f"{e.load_addr:08X}",   _GREY, use_colour)
                exec_  = _colour(f"{e.exec_addr:08X}",   _GREY, use_colour)
                length = _colour(f"{e.length:08X}", _GREY, use_colour)
                print(
                    f"  {access}  "
                    f"{e.fullName:<{col_width}s} "
                    f"{load} "
                    f"{exec_} "
                    f"{length}  "
                    f"{ftype}"
                )
        print()


def cmdSearch(args: Namespace) -> None:
    """Search BASIC files on a disc for lines matching a text pattern.

    Args:
        args: Parsed argparse namespace for the 'search' subcommand.
    """
    # Enable colour only when writing to a real terminal.
    use_colour = sys.stdout.isatty()

    try:
        matches = search(
            args.image,
            args.pattern,
            filename=args.filename,
            ignore_case=args.ignore_case,
            pretty=args.pretty,
            use_regex=args.regex,
        )
    except re.error as e:
        print(f"Invalid regex pattern: {e}", file=sys.stderr)
        sys.exit(1)

    if not matches:
        return

    # Group results by (side, filename) so the file header only prints once.
    current_file = None
    for m in matches:
        file_key = (m["side"], m["filename"])
        if file_key != current_file:
            current_file = file_key
            header = f"--- Side {m['side']}: {m['filename']} ---"
            print(_colour(header, _BOLD, use_colour))
        linenum = _colour(f"{m['line_number']:>5d}", _GREY, use_colour)
        content = m["line"][5:]
        print(f"  {linenum}{content}")




def cmdExtract(args: Namespace) -> None:
    """Extract a file (or all files) from the disc image.

    Args:
        args: Parsed argparse namespace for the 'extract' subcommand.
    """
    # Default text_mode for callers that build a Namespace directly.
    text_mode = getattr(args, "text_mode", "ascii")

    # --all routes to the bulk extractor.
    if args.all:
        if args.output:
            print("Error: -o/--output cannot be used with -a/--all. Use -d/--dir instead.",
                  file=sys.stderr)
            sys.exit(1)

        # Resolve output directory - default to the image filename stem.
        out_dir = args.dir or os.path.splitext(os.path.basename(args.image))[0]

        if getattr(args, "inf", False):
            warnings.warn(
                "--inf is now the default for extract and will be "
                "removed in a future release. See --no-inf.",
                DeprecationWarning,
                stacklevel=2,
            )

        layout = "hierarchical" if args.mkdirs else "flat"
        results = extractAll(args.image, out_dir, pretty=args.pretty,
                              write_inf=not args.no_inf, text_mode=text_mode,
                              layout=layout)
        for result in results:
            if result["type"] == "BASIC":
                print(f"  BASIC     {result['path']}")
            elif result["type"] == "text":
                print(f"  text      {result['path']}")
            elif result["type"] == "BASIC+MC":
                print(
                    f"  BASIC+MC  {result['path']}  "
                    f"load=0x{result['load']:06X}  "
                    f"exec=0x{result['exec']:06X}  "
                    f"length={result['length']} bytes  "
                    f"(BASIC={result['basic_size']}b + machine code)"
                )
            else:
                print(
                    f"  binary    {result['path']}  "
                    f"load=0x{result['load']:06X}  "
                    f"exec=0x{result['exec']:06X}  "
                    f"length={result['length']} bytes"
                )
        return

    if not args.filename:
        print("Error: filename required unless -a/--all is specified.", file=sys.stderr)
        sys.exit(1)

    try:
        result = extractFile(args.image, args.filename,
                             pretty=args.pretty, text_mode=text_mode)
    except DiscError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if result.file_type is FileType.BASIC and result.lines is not None:
        # Pure BASIC - emit as LIST-style text.
        if args.output:
            writeBasicText(args.output, result.lines, text_mode)
            print(f"Extracted to {args.output}", file=sys.stderr)
        else:
            # Stdout: apply escape mode if requested, otherwise raw.
            if text_mode == "escape":
                out_lines = [escapeNonAscii(l) for l in result.lines]
            else:
                out_lines = result.lines
            output = "\n".join(out_lines) + "\n"
            sys.stdout.write(output)

    elif result.file_type is FileType.BASIC_MC:
        # Hybrid file - warn and treat as binary to preserve machine code.
        entry = result.entry
        mc_size = len(result.data) - (result.basic_size or 0)
        print(
            f"BASIC+MC  {entry.fullName}  "
            f"BASIC={result.basic_size}b + {mc_size}b machine code  "
            f"(extracting as binary to preserve machine code)",
            file=sys.stderr,
        )

        if args.output:
            with open(args.output, "wb") as f:
                f.write(result.data)
            print(f"Extracted to {args.output}", file=sys.stderr)
        else:
            sys.stdout.buffer.write(result.data)

    else:
        # Binary file.
        entry = result.entry
        if args.output:
            with open(args.output, "wb") as f:
                f.write(result.data)
            print(f"Extracted to {args.output}")
            print(
                f"{entry.fullName}  "
                f"load=0x{entry.load_addr:06X}  "
                f"exec=0x{entry.exec_addr:06X}  "
                f"length={entry.length} bytes"
            )
        else:
            # Raw bytes to stdout for piping to a disassembler.
            sys.stdout.buffer.write(result.data)


def cmdCreate(args: Namespace) -> None:
    """Create a blank formatted disc image (DFS or ADFS).

    The output format is determined by the file extension of args.output.

    Args:
        args: Parsed argparse namespace for the 'create' subcommand.
    """
    size_bytes = createImageFile(
        args.output,
        tracks=args.tracks,
        title=args.title or "",
        boot_option=args.boot,
    )

    label = _formatLabel(args.output, args.tracks, size_bytes)
    print(f"Created {label}: {args.output}")


def cmdAdd(args: Namespace) -> None:
    """Add a file to an existing disc image (DFS or ADFS).

    Args:
        args: Parsed argparse namespace for the 'add' subcommand.
    """
    if args.inf:
        # Warn if --basic was also specified - .inf overrides everything.
        if args.basic:
            print("Warning: --basic ignored when --inf is used",
                  file=sys.stderr)

        # Read metadata from a .inf sidecar file.
        inf_path = args.file + ".inf"

        if not os.path.isfile(inf_path):
            print(f"Error: .inf sidecar not found: {inf_path}",
                  file=sys.stderr)
            sys.exit(1)

        with open(inf_path, "r", encoding="ascii") as f:
            inf_line = f.readline().strip()

        inf = parseInf(inf_line)
        disc_path = inf.fullName
        load_addr = inf.load_addr
        exec_addr = inf.exec_addr
        locked = inf.locked
    else:
        # Metadata from command-line arguments.
        file_name = args.name

        if not file_name:
            print("Error: --name is required (or use --inf).",
                  file=sys.stderr)
            sys.exit(1)

        # Normalise the path to a fully-qualified disc path.
        disc_path = qualifyDiscPath(file_name)

        # Apply BASIC defaults first, then let explicit flags override.
        if args.basic:
            load_addr = 0x1900
            exec_addr = 0x8023
        else:
            load_addr = 0
            exec_addr = 0

        if args.load:
            override = int(args.load, 16)
            if args.basic:
                print(f"Note: --load overrides BASIC default"
                      f" (0x{load_addr:04X} -> 0x{override:04X})",
                      file=sys.stderr)
            load_addr = override

        if args.exec_addr:
            override = int(args.exec_addr, 16)
            if args.basic:
                print(f"Note: --exec overrides BASIC default"
                      f" (0x{exec_addr:04X} -> 0x{override:04X})",
                      file=sys.stderr)
            exec_addr = override

        locked = args.locked

    with open(args.file, "rb") as f:
        data = f.read()

    original_size = len(data)

    try:
        ctx = strictMode() if getattr(args, "strict", False) else contextlib.nullcontext()
        with ctx:
            entry = addFile(
                args.image,
                DiscFile(
                    path=disc_path, data=data,
                    load_addr=load_addr, exec_addr=exec_addr,
                    locked=locked,
                ),
                side=args.side,
                retokenize=args.basic,
            )
    except DiscError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Report tokenization if the stored size differs from the original.
    if args.basic and entry.length != original_size:
        print(f"Tokenized {args.file} ({entry.length} bytes)", file=sys.stderr)

    print(f"Added {disc_path} ({entry.length} bytes) to {args.image}")


def cmdDelete(args: Namespace) -> None:
    """Delete a file from a disc image (DFS or ADFS).

    Args:
        args: Parsed argparse namespace for the 'delete' subcommand.
    """
    disc_path = qualifyDiscPath(args.filename)

    try:
        deleteFile(args.image, args.filename, side=args.side)
    except DiscError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Deleted {disc_path} from {args.image}")


def cmdBuild(args: Namespace) -> None:
    """Build a disc image from a directory of files with .inf sidecars.

    The output format is determined by the file extension of args.output.

    Args:
        args: Parsed argparse namespace for the 'build' subcommand.
    """
    try:
        ctx = strictMode() if getattr(args, "strict", False) else contextlib.nullcontext()
        with ctx:
            image_bytes = buildImage(
                source_dir=args.dir,
                output_path=args.output,
                tracks=args.tracks,
                title=args.title,
                boot_option=args.boot,
                save=True,
                force=getattr(args, "force", False),
            )
    except DiscError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    label = _formatLabel(args.output, args.tracks, len(image_bytes))
    print(f"Built {label}: {args.output}")


# ---------------------------------------------------------------------------
# title command
# ---------------------------------------------------------------------------

def cmdTitle(args: Namespace) -> None:
    """Read or set the disc title on an existing image.

    With no title argument, prints the current title.
    With a title argument, sets the title and writes it back.

    Args:
        args: Parsed argparse namespace for the 'title' subcommand.
    """
    if args.title is None:
        # Getter mode - print the current title.
        title = getTitle(args.image, side=args.side)
        print(title)
    else:
        # Setter mode - update the title.
        setTitle(args.image, args.title, side=args.side)
        print(f"Title set to '{args.title}'")


# ---------------------------------------------------------------------------
# boot command
# ---------------------------------------------------------------------------

def cmdBoot(args: Namespace) -> None:
    """Read or set the disc boot option on an existing image.

    With no boot argument, prints the current boot option.
    With a boot argument, sets the option and writes it back.

    Args:
        args: Parsed argparse namespace for the 'boot' subcommand.
    """
    if args.boot is None:
        # Getter mode - print the current boot option.
        boot = getBoot(args.image, side=args.side)
        print(boot.name)
    else:
        # Setter mode - update the boot option.
        setBoot(args.image, args.boot, side=args.side)
        print(f"Boot option set to {args.boot.name}")


# ---------------------------------------------------------------------------
# disc command
# ---------------------------------------------------------------------------

def cmdDisc(args: Namespace) -> None:
    """Print disc summary or set disc-level properties.

    With no mutation flags, prints a disc summary (title, boot option,
    free space). With --title and/or --boot, sets the specified properties.

    Args:
        args: Parsed argparse namespace for the 'disc' subcommand.
    """
    has_mutations = args.set_title is not None or args.set_boot is not None

    if has_mutations:
        # Apply requested mutations.
        if args.set_title is not None:
            setTitle(args.image, args.set_title, side=args.side)

        if args.set_boot is not None:
            setBoot(args.image, args.set_boot, side=args.side)

        # Confirm what was changed.
        parts = []
        if args.set_title is not None:
            parts.append(f"title='{args.set_title}'")
        if args.set_boot is not None:
            parts.append(f"boot={args.set_boot.name}")
        print(f"Updated: {', '.join(parts)}")
    else:
        # Summary mode - print disc metadata.
        use_colour = sys.stdout.isatty()
        info = discInfo(args.image, side=args.side)

        title_display = info.title if info.title else "(none)"
        print(f"Title:  {_colour(title_display, _BOLD + _CYAN, use_colour)}")
        print(f"Boot:   {info.boot_option.name}")
        print(f"Tracks: {info.tracks}")
        print(f"Free:   {info.free_space:,} bytes "
              f"({info.free_space // 256} sectors)")


# ---------------------------------------------------------------------------
# attrib command
# ---------------------------------------------------------------------------

def cmdAttrib(args: Namespace) -> None:
    """Read or set file attributes on a disc image.

    With no flags, prints the current attributes. With flags, sets them.

    Args:
        args: Parsed argparse namespace for the 'attrib' subcommand.
    """
    has_mutations = (
        args.locked is not None
        or args.access is not None
        or args.load is not None
        or args.exec_addr is not None
    )

    if has_mutations:
        # Parse hex addresses.
        load_addr = int(args.load, 16) if args.load is not None else None
        exec_addr = int(args.exec_addr, 16) if args.exec_addr is not None else None

        setFileAttribs(
            args.image, args.filename, side=args.side,
            locked=args.locked,
            load_addr=load_addr,
            exec_addr=exec_addr,
            access_flags=args.access,
        )

        # Confirm what was changed.
        parts = []
        if args.locked is not None:
            parts.append("locked" if args.locked else "unlocked")
        if args.access is not None:
            parts.append(f"access={args.access!r}")
        if load_addr is not None:
            parts.append(f"load={load_addr:08X}")
        if exec_addr is not None:
            parts.append(f"exec={exec_addr:08X}")
        print(f"Updated {args.filename}: {', '.join(parts)}")
    else:
        # Getter mode - print current attributes.
        use_colour = sys.stdout.isatty()
        attribs = getFileAttribs(args.image, args.filename, side=args.side)

        lock_str = "L" if attribs.locked else "-"
        lock_display = _colour(lock_str, _RED, use_colour and attribs.locked)
        access_display = attribs.access_string if attribs.access_string else "-"
        print(f"File:   {attribs.fullName}")
        print(f"Load:   {attribs.load_addr:08X}")
        print(f"Exec:   {attribs.exec_addr:08X}")
        print(f"Length: {attribs.length:08X}")
        print(f"Locked: {lock_display}")
        print(f"Access: {access_display}")


# ---------------------------------------------------------------------------
# rename command
# ---------------------------------------------------------------------------

def cmdRename(args: Namespace) -> None:
    """Rename a file on a disc image.

    Args:
        args: Parsed argparse namespace for the 'rename' subcommand.
    """
    renameFile(
        args.image, args.old_name, args.new_name, side=args.side,
    )
    print(f"Renamed {args.old_name} -> {args.new_name}")


def cmdCompact(args: Namespace) -> None:
    """Defragment a DFS disc image by closing gaps between files.

    Args:
        args: Parsed argparse namespace for the 'compact' subcommand.
    """
    freed = compactDisc(args.image, side=args.side)

    # Report the result in sectors and bytes.
    sectors = freed // 256

    if freed == 0:
        print("Disc is already fully compacted")
    else:
        print(f"Freed {sectors} sectors ({freed} bytes)")


def cmdMkdir(args: Namespace) -> None:
    """Create a subdirectory on an ADFS disc image.

    Args:
        args: Parsed argparse namespace for the 'mkdir' subcommand.
    """
    makeDirectory(args.image, args.path, side=args.side)
    print(f"Created directory {args.path}")


_default_formatwarning = warnings.formatwarning


def _formatWarning(
    message: object,
    category: type,
    filename: str,
    lineno: int,
    line: Optional[str] = None,
) -> str:
    """Format BeebToolsWarning for clean CLI output."""
    if issubclass(category, BeebToolsWarning):
        return f"Warning: {message}\n"
    return _default_formatwarning(message, category, filename, lineno, line)


def main() -> None:
    """CLI entry point."""
    warnings.formatwarning = _formatWarning

    parser = argparse.ArgumentParser(
        description=(
            "BBC Micro disc image tool. "
            "Read catalogues, extract files, detokenize BBC BASIC programs, "
            "and create, modify, and build disc images. "
            "Supports DFS (.ssd, .dsd) and ADFS (.adf, .adl) formats."
        ),
        epilog="Use 'beebtools <command> -h' for detailed help on each command.",
    )
    parser.add_argument("--debug", action="store_true",
                        help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")

    p_cat = sub.add_parser("cat", help="List disc catalogue")
    p_cat.add_argument("image", help="Path to disc image (.ssd, .dsd, .adf, or .adl)")
    p_cat.add_argument(
        "-s", "--sort",
        choices=["name", "catalog", "size"],
        default="name",
        help="Sort order: name (default), catalog, or size",
    )
    p_cat.add_argument(
        "-i", "--inspect",
        action="store_true",
        help="Read file contents to detect TEXT files (slower; default is metadata-only)",
    )

    p_extract = sub.add_parser("extract", help="Extract a file, or all files with -a")
    p_extract.add_argument("image", help="Path to disc image (.ssd, .dsd, .adf, or .adl)")
    p_extract.add_argument("filename", nargs="?",
                           help="Filename, e.g. T.MYPROG or $.GAMES.ELITE")
    p_extract.add_argument("-a", "--all", action="store_true",
                           help="Extract all files from the disc")
    p_extract.add_argument("-o", "--output",
                           help="Write single file to this path instead of stdout")
    p_extract.add_argument("-d", "--dir",
                           help="Output directory for -a/--all (default: image name)")
    p_extract.add_argument("--pretty", action="store_true",
                           help="Add operator spacing to BASIC output")
    p_extract.add_argument("--inf", action="store_true",
                           help="Accepted for backwards compatibility (now the default)")
    p_extract.add_argument("--no-inf", action="store_true", dest="no_inf",
                           help="Suppress .inf sidecar files (written by default)")
    p_extract.add_argument("--mkdirs", action="store_true",
                           help="Create subdirectories from Acorn paths instead of flat layout")
    p_extract.add_argument("-t", "--text", choices=["ascii", "utf8", "escape"],
                           default="ascii", dest="text_mode",
                           help="Text encoding for BASIC .bas files: "
                                "ascii (lossy, default), utf8 "
                                "(lossless), escape (\\xHH notation, lossless). "
                                "Controls how non-ASCII bytes such as teletext "
                                "control codes in PRINT strings are written")

    p_search = sub.add_parser("search", help="Search BASIC source for a text pattern")
    p_search.add_argument("image", help="Path to disc image (.ssd, .dsd, .adf, or .adl)")
    p_search.add_argument("pattern", help="Text to search for")
    p_search.add_argument("filename", nargs="?",
                          help="Limit search to this file (e.g. T.MYPROG or MYPROG)")
    p_search.add_argument("-i", "--ignore-case", action="store_true",
                          help="Case-insensitive search")
    p_search.add_argument("-r", "--regex", action="store_true",
                          help="Treat pattern as a Python regular expression")
    p_search.add_argument("--pretty", action="store_true",
                          help="Match after applying pretty-printer spacing")

    # -- create subcommand --
    p_create = sub.add_parser("create", help="Create a blank disc image")
    p_create.add_argument("output",
                          help="Output path (.ssd, .dsd, .adf, or .adl)")
    p_create.add_argument("-t", "--tracks", type=int, default=80,
                          choices=[40, 80],
                          help="Track count (default: 80). "
                               "For ADFS: 40t .adf=160K, 80t .adf=320K, "
                               ".adl=640K")
    p_create.add_argument("--title", help="Disc title")
    p_create.add_argument("--boot", type=_parseBootOption,
                          default=BootOption.OFF,
                          help="Boot option: OFF, LOAD, RUN, EXEC (or 0-3)")

    # -- add subcommand --
    p_add = sub.add_parser("add", help="Add a file to a disc image")
    p_add.add_argument("image",
                       help="Path to disc image (.ssd, .dsd, .adf, or .adl)")
    p_add.add_argument("file", help="Path to data file to add")
    p_add.add_argument("-n", "--name",
                       help="File name (DFS: T.MYPROG; ADFS: $.DIR.FILE)")
    p_add.add_argument("--load", help="Load address in hex (default: 0)")
    p_add.add_argument("--exec", dest="exec_addr",
                       help="Exec address in hex (default: 0)")
    p_add.add_argument("--basic", action="store_true",
                       help="Set BASIC defaults (load=0x1900, exec=0x8023)")
    p_add.add_argument("--locked", action="store_true",
                       help="Lock the file against deletion")
    p_add.add_argument("--inf", action="store_true",
                       help="Read metadata from a .inf sidecar file")
    p_add.add_argument("--side", type=int, default=0,
                       choices=[0, 1],
                       help="Disc side for DFS (default: 0; ignored for ADFS)")
    p_add.add_argument("--strict", action="store_true",
                       help="Enforce DFS spec-compliance on the filename")

    # -- delete subcommand --
    p_delete = sub.add_parser("delete", help="Delete a file from a disc image")
    p_delete.add_argument("image",
                          help="Path to disc image (.ssd, .dsd, .adf, or .adl)")
    p_delete.add_argument("filename",
                          help="Filename to delete (DFS: T.MYPROG; "
                               "ADFS: $.DIR.FILE)")
    p_delete.add_argument("--side", type=int, default=0,
                          choices=[0, 1],
                          help="Disc side for DFS (default: 0; ignored for ADFS)")

    # -- build subcommand --
    p_build = sub.add_parser(
        "build", help="Build a disc image from files with .inf sidecars")
    p_build.add_argument("dir",
                         help="Source directory (DFS: dir subdirs; "
                              "ADFS: $ tree)")
    p_build.add_argument("output",
                         help="Output path (.ssd, .dsd, .adf, or .adl)")
    p_build.add_argument("-t", "--tracks", type=int, default=80,
                         choices=[40, 80],
                         help="Track count (default: 80). "
                              "For ADFS: 40t .adf=160K, 80t .adf=320K, "
                              ".adl=640K")
    p_build.add_argument("--title", default=None,
                         help="Disc title (overrides $.inf when --force is set)")
    p_build.add_argument("--boot", type=_parseBootOption,
                         default=None,
                         help="Boot option: OFF, LOAD, RUN, EXEC (or 0-3) "
                              "(overrides $.inf when --force is set)")
    p_build.add_argument("--force", action="store_true",
                         help="Override $.inf disc metadata with "
                              "explicit --title/--boot values")
    p_build.add_argument("--strict", action="store_true",
                         help="Enforce DFS spec-compliance on filenames "
                              "(rejects non-printable bytes, '.', '#', '*', "
                              "':', '\"', and space)")

    # -- title subcommand --
    p_title = sub.add_parser(
        "title", help="Read or set the disc title")
    p_title.add_argument("image",
                         help="Path to disc image (.ssd, .dsd, .adf, or .adl)")
    p_title.add_argument("title", nargs="?", default=None,
                         help="New title (omit to print current title)")
    p_title.add_argument("--side", type=int, default=0,
                         choices=[0, 1],
                         help="Disc side for DFS (default: 0; ignored for ADFS)")

    # -- boot subcommand --
    p_boot = sub.add_parser(
        "boot", help="Read or set the disc boot option")
    p_boot.add_argument("image",
                        help="Path to disc image (.ssd, .dsd, .adf, or .adl)")
    p_boot.add_argument("boot", nargs="?", type=_parseBootOption,
                        default=None,
                        help="Boot option: OFF, LOAD, RUN, EXEC (omit to "
                             "print current value)")
    p_boot.add_argument("--side", type=int, default=0,
                        choices=[0, 1],
                        help="Disc side for DFS (default: 0; ignored for ADFS)")

    # -- disc subcommand --
    p_disc = sub.add_parser(
        "disc", help="Print disc summary or set disc properties")
    p_disc.add_argument("image",
                        help="Path to disc image (.ssd, .dsd, .adf, or .adl)")
    p_disc.add_argument("--title", dest="set_title", default=None,
                        help="Set the disc title")
    p_disc.add_argument("--boot", dest="set_boot",
                        type=_parseBootOption, default=None,
                        help="Set boot option: OFF, LOAD, RUN, EXEC")
    p_disc.add_argument("--side", type=int, default=0,
                        choices=[0, 1],
                        help="Disc side for DFS (default: 0; ignored for ADFS)")

    # -- attrib subcommand --
    # The --access help text is assembled from every *_resources
    # module so each format contributes its own grammar block without
    # the CLI naming format engines directly.
    cli_resources = _loadResourceBundles("cli")
    access_help_body = cli_resources.get("attrib.access", "")
    access_help = (
        "Set access flags using format-specific grammar.\n"
        "Mutually exclusive with --locked / --unlocked.\n\n"
        + access_help_body
    )

    p_attrib = sub.add_parser(
        "attrib", help="Read or set file attributes",
        formatter_class=argparse.RawTextHelpFormatter)
    p_attrib.add_argument("image",
                          help="Path to disc image (.ssd, .dsd, .adf, or .adl)")
    p_attrib.add_argument("filename",
                          help="Filename (e.g. T.MYPROG or $.GAMES.ELITE)")
    lock_group = p_attrib.add_mutually_exclusive_group()
    lock_group.add_argument("--locked", action="store_const", const=True,
                            default=None, dest="locked",
                            help="Lock the file")
    lock_group.add_argument("--unlocked", action="store_const", const=False,
                            dest="locked",
                            help="Unlock the file")
    lock_group.add_argument("--access", default=None, dest="access",
                            help=access_help)
    p_attrib.add_argument("--load", default=None,
                          help="Load address in hex (e.g. FF1900)")
    p_attrib.add_argument("--exec", dest="exec_addr", default=None,
                          help="Exec address in hex (e.g. FF8023)")
    p_attrib.add_argument("--side", type=int, default=0,
                          choices=[0, 1],
                          help="Disc side for DFS (default: 0; ignored for ADFS)")

    p_rename = sub.add_parser("rename", help="Rename a file on a disc image")
    p_rename.add_argument("image",
                          help="Path to disc image (.ssd, .dsd, .adf, or .adl)")
    p_rename.add_argument("old_name", help="Current filename (e.g. T.MYPROG)")
    p_rename.add_argument("new_name", help="New filename (e.g. T.NEWNAME)")
    p_rename.add_argument("--side", type=int, default=0,
                          choices=[0, 1],
                          help="Disc side for DFS (default: 0; ignored for ADFS)")

    # -- compact subcommand --
    p_compact = sub.add_parser(
        "compact", help="Defragment a DFS disc image")
    p_compact.add_argument("image",
                           help="Path to disc image (.ssd or .dsd)")
    p_compact.add_argument("--side", type=int, default=0,
                           choices=[0, 1],
                           help="Disc side for DFS (default: 0)")

    # -- mkdir subcommand --
    p_mkdir = sub.add_parser(
        "mkdir", help="Create a subdirectory on an ADFS disc image")
    p_mkdir.add_argument("image",
                         help="Path to disc image (.adf or .adl)")
    p_mkdir.add_argument("path",
                         help="Directory path (e.g. $.GAMES or $.GAMES.ARCADE)")
    p_mkdir.add_argument("--side", type=int, default=0,
                         choices=[0, 1],
                         help="Disc side (default: 0; ignored for ADFS)")

    args = parser.parse_args()

    try:
        if args.command == "cat":
            cmdCat(args)
        elif args.command == "extract":
            cmdExtract(args)
        elif args.command == "search":
            cmdSearch(args)
        elif args.command == "create":
            cmdCreate(args)
        elif args.command == "add":
            cmdAdd(args)
        elif args.command == "delete":
            cmdDelete(args)
        elif args.command == "build":
            cmdBuild(args)
        elif args.command == "title":
            cmdTitle(args)
        elif args.command == "boot":
            cmdBoot(args)
        elif args.command == "disc":
            cmdDisc(args)
        elif args.command == "attrib":
            cmdAttrib(args)
        elif args.command == "rename":
            cmdRename(args)
        elif args.command == "compact":
            cmdCompact(args)
        elif args.command == "mkdir":
            cmdMkdir(args)
        else:
            parser.print_help()
    except Exception as e:
        if args.debug:
            raise
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
