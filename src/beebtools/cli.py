# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""CLI commands for beebtools.

Implements the 'cat', 'extract', 'search', 'create', 'add', 'delete',
and 'build' subcommands and the main() entry point.
"""

import argparse
import os
import re
import sys
from argparse import Namespace
from typing import Optional

from .boot import BootOption
from .entry import DiscError, DiscFile
from .inf import parseInf
from .disc import (
    openImage, createImage,
    search, extractAll, buildImage,
    sortCatalogueEntries, classifyFileType,
    extractFile, addFileTo, qualifyDiscPath,
    writeBasicText, escapeNonAscii,
    getTitle, setTitle, getBoot, setBoot, discInfo,
    getFileAttribs, setFileAttribs,
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


def cmdCat(args: Namespace) -> None:
    """Print the disc catalogue to stdout.

    Args:
        args: Parsed argparse namespace for the 'cat' subcommand.
    """
    # Enable colour only when writing to a real terminal.
    use_colour = sys.stdout.isatty()

    sides = openImage(args.image)

    for disc in sides.sides:
        catalogue = disc.readCatalogue()
        side_label = f"Side {disc.side}"
        header = f"--- {side_label}"

        if catalogue.title:
            header += f": {catalogue.title}"

        boot_desc = catalogue.boot_option.name
        header += (f" ({len(catalogue.entries)} files,"
                  f" {catalogue.tracks} tracks,"
                  f" boot={boot_desc}) ---")
        print(_colour(header, _BOLD, use_colour))
        print()

        if not catalogue.entries:
            print("  (empty)")
        else:
            orderedEntries = sortCatalogueEntries(catalogue.entries, args.sort)

            # Dynamic column width for ADFS hierarchical names.
            max_name = max(len(e.fullName) for e in orderedEntries)
            col_width = max(12, max_name + 2)

            print(f"    {'Name':<{col_width}s} {'Load':>8s} {'Exec':>8s} {'Length':>8s}  {'Type'}")

            for e in orderedEntries:
                is_dir = e.isDirectory

                if is_dir:
                    ftype = _colour("DIR", _CYAN, use_colour)
                elif args.inspect:
                    # Content-inspect the file to classify its type.
                    file_data = disc.readFile(e)
                    tag = classifyFileType(e, file_data)

                    _TAG_COLOURS = {
                        "BASIC":    (_CYAN,    "BASIC"),
                        "BASIC+MC": (_MAGENTA, "BASIC+MC"),
                        "BASIC?":   (_GREEN,   "BASIC?"),
                        "TEXT":     (_YELLOW,  "TEXT"),
                    }
                    if tag in _TAG_COLOURS:
                        colour, label = _TAG_COLOURS[tag]
                        ftype = _colour(label, colour, use_colour)
                    else:
                        ftype = ""
                elif e.isBasic:
                    # Metadata-only mode: trust the exec address.
                    ftype = _colour("BASIC", _CYAN, use_colour)
                else:
                    ftype = ""

                lock_char = "L" if e.locked else " "
                lock = _colour(lock_char, _RED, use_colour and e.locked)
                load   = _colour(f"{e.load_addr:08X}",   _GREY, use_colour)
                exec_  = _colour(f"{e.exec_addr:08X}",   _GREY, use_colour)
                length = _colour(f"{e.length:08X}", _GREY, use_colour)
                print(
                    f"  {lock} {e.fullName:<{col_width - 1}s} "
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

        results = extractAll(args.image, out_dir, pretty=args.pretty,
                              write_inf=args.inf, text_mode=text_mode)
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

    if result.file_type == "BASIC" and result.lines is not None:
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

    elif result.file_type == "BASIC+MC":
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
    image = createImage(
        args.output,
        tracks=args.tracks,
        title=args.title or "",
        boot_option=args.boot,
    )

    image_bytes = image.serialize()
    with open(args.output, "wb") as f:
        f.write(image_bytes)

    # Determine human-readable format description from extension.
    ext = os.path.splitext(args.output)[1].lower()
    _FMT_LABELS = {
        ".ssd": f"{args.tracks}-track SSD",
        ".dsd": f"{args.tracks}-track DSD",
        ".adf": f"{len(image_bytes) // 1024}K ADF",
        ".adl": f"{len(image_bytes) // 1024}K ADL",
    }
    label = _FMT_LABELS.get(ext, ext)
    print(f"Created {label}: {args.output}")


def cmdAdd(args: Namespace) -> None:
    """Add a file to an existing disc image (DFS or ADFS).

    Args:
        args: Parsed argparse namespace for the 'add' subcommand.
    """
    image = openImage(args.image)

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
        entry = addFileTo(
            image, args.side,
            DiscFile(
                path=disc_path, data=data,
                load_addr=load_addr, exec_addr=exec_addr,
                locked=locked,
            ),
            retokenize=args.basic,
        )
    except DiscError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Report tokenization if the stored size differs from the original.
    if args.basic and entry.length != original_size:
        print(f"Tokenized {args.file} ({entry.length} bytes)", file=sys.stderr)

    # Write the modified image back to the file.
    with open(args.image, "wb") as f:
        f.write(image.serialize())

    print(f"Added {disc_path} ({entry.length} bytes) to {args.image}")


def cmdDelete(args: Namespace) -> None:
    """Delete a file from a disc image (DFS or ADFS).

    Args:
        args: Parsed argparse namespace for the 'delete' subcommand.
    """
    image = openImage(args.image)
    side = image.sides[args.side]

    path = args.filename

    disc_path = qualifyDiscPath(path)

    try:
        side.deleteFile(disc_path)
    except DiscError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Write the modified image back to the file.
    with open(args.image, "wb") as f:
        f.write(image.serialize())

    print(f"Deleted {disc_path} from {args.image}")


def cmdBuild(args: Namespace) -> None:
    """Build a disc image from a directory of files with .inf sidecars.

    The output format is determined by the file extension of args.output.

    Args:
        args: Parsed argparse namespace for the 'build' subcommand.
    """
    try:
        image_bytes = buildImage(
            source_dir=args.dir,
            output_path=args.output,
            tracks=args.tracks,
            title=args.title or "",
            boot_option=args.boot,
        )
    except DiscError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    with open(args.output, "wb") as f:
        f.write(image_bytes)

    # Determine human-readable format description from extension.
    ext = os.path.splitext(args.output)[1].lower()
    _FMT_LABELS = {
        ".ssd": f"{args.tracks}-track SSD",
        ".dsd": f"{args.tracks}-track DSD",
        ".adf": f"{len(image_bytes) // 1024}K ADF",
        ".adl": f"{len(image_bytes) // 1024}K ADL",
    }
    label = _FMT_LABELS.get(ext, ext)
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
        )

        # Confirm what was changed.
        parts = []
        if args.locked is not None:
            parts.append("locked" if args.locked else "unlocked")
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
        print(f"File:   {attribs.fullName}")
        print(f"Load:   {attribs.load_addr:08X}")
        print(f"Exec:   {attribs.exec_addr:08X}")
        print(f"Length: {attribs.length:08X}")
        print(f"Locked: {lock_display}")


def main() -> None:
    """CLI entry point."""
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
                           help="Write .inf sidecar files with -a/--all")
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
    p_build.add_argument("--title", help="Disc title")
    p_build.add_argument("--boot", type=_parseBootOption,
                         default=BootOption.OFF,
                         help="Boot option: OFF, LOAD, RUN, EXEC (or 0-3)")

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
    p_attrib = sub.add_parser(
        "attrib", help="Read or set file attributes")
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
    p_attrib.add_argument("--load", default=None,
                          help="Load address in hex (e.g. FF1900)")
    p_attrib.add_argument("--exec", dest="exec_addr", default=None,
                          help="Exec address in hex (e.g. FF8023)")
    p_attrib.add_argument("--side", type=int, default=0,
                          choices=[0, 1],
                          help="Disc side for DFS (default: 0; ignored for ADFS)")

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
        else:
            parser.print_help()
    except Exception as e:
        if args.debug:
            raise
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
