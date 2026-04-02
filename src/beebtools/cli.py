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

from .detokenize import detokenize
from .tokenize import tokenize
from .pretty import prettyPrint
from .dfs import (
    DFSError,
    BootOption,
    createDiscImage,
    looksLikeTokenizedBasic,
    looksLikePlainText,
    openDiscImage,
    sortCatalogueEntries,
)
from .inf import parseInf
from .disc import search, extractAll, buildImage


# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------

_BOLD    = "\x1b[1m"
_CYAN    = "\x1b[36m"
_YELLOW  = "\x1b[33m"
_RED     = "\x1b[31m"
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

    sides = openDiscImage(args.image)

    for disc in sides.sides:
        catalogue = disc.readCatalogue()
        side_label = f"Side {disc.side}"
        header = f"--- {side_label}"

        if catalogue.title:
            header += f": {catalogue.title}"

        boot_desc = catalogue.boot_option.name
        header += f" ({len(catalogue.entries)} files, boot={boot_desc}) ---"
        print(_colour(header, _BOLD, use_colour))
        print()

        if not catalogue.entries:
            print("  (empty)")
        else:
            orderedEntries = sortCatalogueEntries(catalogue.entries, args.sort)
            print(f"  {'Name':<12s} {'Load':>8s} {'Exec':>8s} {'Length':>8s}  {'Type'}")

            for e in orderedEntries:
                if e.isBasic:
                    ftype = _colour("BASIC", _CYAN, use_colour)
                elif args.inspect and looksLikePlainText(disc.readFile(e)):
                    ftype = _colour("TEXT", _YELLOW, use_colour)
                else:
                    ftype = ""
                lock_char = "L" if e.locked else " "
                lock = _colour(lock_char, _RED, use_colour and e.locked)
                load   = _colour(f"{e.load_addr:08X}",   _GREY, use_colour)
                exec_  = _colour(f"{e.exec_addr:08X}",   _GREY, use_colour)
                length = _colour(f"{e.length:08X}", _GREY, use_colour)
                print(
                    f"  {lock}{e.fullName:<11s} "
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
    # --all routes to the bulk extractor.
    if args.all:
        if args.output:
            print("Error: -o/--output cannot be used with -a/--all. Use -d/--dir instead.",
                  file=sys.stderr)
            sys.exit(1)

        # Resolve output directory - default to the image filename stem.
        out_dir = args.dir or os.path.splitext(os.path.basename(args.image))[0]

        results = extractAll(args.image, out_dir, pretty=args.pretty, write_inf=args.inf)
        for result in results:
            if result["type"] == "BASIC":
                print(f"  BASIC   {result['path']}")
            elif result["type"] == "text":
                print(f"  text    {result['path']}")
            else:
                print(
                    f"  binary  {result['path']}  "
                    f"load=0x{result['load']:06X}  "
                    f"exec=0x{result['exec']:06X}  "
                    f"length={result['length']} bytes"
                )
        return

    if not args.filename:
        print("Error: filename required unless -a/--all is specified.", file=sys.stderr)
        sys.exit(1)

    sides = openDiscImage(args.image)
    target = args.filename
    found = None

    if len(target) >= 3 and target[1] == ".":
        # Explicit directory prefix given (e.g. T.MYPROG).
        target_dir = target[0].upper()
        target_name = target[2:]

        for disc in sides.sides:
            catalogue = disc.readCatalogue()
            for e in catalogue.entries:
                if (e.directory.upper() == target_dir
                        and e.name.upper() == target_name.upper()):
                    found = (disc, e)
                    break
            if found:
                break

    else:
        # Bare filename - find a unique match across all sides and directories.
        target_name = target
        matches = []

        for disc in sides.sides:
            catalogue = disc.readCatalogue()
            for e in catalogue.entries:
                if e.name.upper() == target_name.upper():
                    matches.append((disc, e))

        if len(matches) == 1:
            found = matches[0]
        elif len(matches) > 1:
            print(
                f"Ambiguous filename '{target_name}' - specify with directory prefix.",
                file=sys.stderr,
            )
            for disc, entry in matches:
                print(f"  Side {disc.side}: {entry.fullName}",
                      file=sys.stderr)
            sys.exit(1)

    if not found:
        print(f"File not found: {target}", file=sys.stderr)
        sys.exit(1)

    disc, entry = found
    data = disc.readFile(entry)

    if entry.isBasic and looksLikeTokenizedBasic(data):
        # Detokenize and emit as LIST-style text.
        text_lines = detokenize(data)
        if args.pretty:
            text_lines = prettyPrint(text_lines)
        output = "\n".join(text_lines) + "\n"

        if args.output:
            with open(args.output, "w", encoding="ascii", errors="replace") as f:
                f.write(output)
            print(f"Extracted to {args.output}", file=sys.stderr)
        else:
            sys.stdout.write(output)

    else:
        # Binary file.
        if args.output:
            with open(args.output, "wb") as f:
                f.write(data)
            print(f"Extracted to {args.output}")
            print(
                f"{entry.fullName}  "
                f"load=0x{entry.load_addr:06X}  "
                f"exec=0x{entry.exec_addr:06X}  "
                f"length={entry.length} bytes"
            )
        else:
            # Raw bytes to stdout for piping to a disassembler.
            sys.stdout.buffer.write(data)


def cmdCreate(args: Namespace) -> None:
    """Create a blank formatted DFS disc image.

    Args:
        args: Parsed argparse namespace for the 'create' subcommand.
    """
    is_dsd = args.output.lower().endswith(".dsd")

    image = createDiscImage(
        tracks=args.tracks,
        is_dsd=is_dsd,
        title=args.title or "",
        boot_option=args.boot,
    )

    with open(args.output, "wb") as f:
        f.write(image.serialize())

    fmt = "DSD" if is_dsd else "SSD"
    print(f"Created {args.tracks}-track {fmt}: {args.output}")


def cmdAdd(args: Namespace) -> None:
    """Add a file to an existing disc image.

    Args:
        args: Parsed argparse namespace for the 'add' subcommand.
    """
    image = openDiscImage(args.image)
    side = image.sides[args.side]

    if args.inf:
        # Warn if --basic was also specified - .inf overrides everything.
        if args.basic:
            print("Warning: --basic ignored when --inf is used",
                  file=sys.stderr)

        # Read metadata from a .inf sidecar file.
        inf_path = args.file + ".inf"

        if not os.path.isfile(inf_path):
            print(f"Error: .inf sidecar not found: {inf_path}", file=sys.stderr)
            sys.exit(1)

        with open(inf_path, "r", encoding="ascii") as f:
            inf_line = f.readline().strip()

        inf = parseInf(inf_line)
        directory = inf.directory
        name = inf.name
        load_addr = inf.load_addr
        exec_addr = inf.exec_addr
        locked = inf.locked
    else:
        # Metadata from command-line arguments.
        dfs_name = args.name

        if not dfs_name:
            print("Error: --name is required (or use --inf).", file=sys.stderr)
            sys.exit(1)

        if len(dfs_name) >= 3 and dfs_name[1] == ".":
            directory = dfs_name[0]
            name = dfs_name[2:]
        else:
            directory = "$"
            name = dfs_name

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

    # When --basic is set and the file is plain text (not already
    # tokenized binary), retokenize it so the BBC Micro can RUN it.
    if args.basic and not looksLikeTokenizedBasic(data) and looksLikePlainText(data):
        text = data.decode("ascii", errors="replace")
        text_lines = text.splitlines()
        data = tokenize(text_lines)
        print(f"Tokenized {args.file} ({len(data)} bytes)", file=sys.stderr)

    try:
        entry = side.addFile(
            name=name,
            directory=directory,
            data=data,
            load_addr=load_addr,
            exec_addr=exec_addr,
            locked=locked,
        )
    except DFSError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Write the modified image back to the file.
    with open(args.image, "wb") as f:
        f.write(image.serialize())

    print(f"Added {entry.fullName} ({len(data)} bytes) to {args.image}")


def cmdDelete(args: Namespace) -> None:
    """Delete a file from a disc image.

    Args:
        args: Parsed argparse namespace for the 'delete' subcommand.
    """
    image = openDiscImage(args.image)
    side = image.sides[args.side]

    dfs_name = args.filename

    if len(dfs_name) >= 3 and dfs_name[1] == ".":
        directory = dfs_name[0]
        name = dfs_name[2:]
    else:
        directory = "$"
        name = dfs_name

    try:
        entry = side.deleteFile(name, directory)
    except DFSError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Write the modified image back to the file.
    with open(args.image, "wb") as f:
        f.write(image.serialize())

    print(f"Deleted {entry.fullName} from {args.image}")


def cmdBuild(args: Namespace) -> None:
    """Build a disc image from a directory of files with .inf sidecars.

    Args:
        args: Parsed argparse namespace for the 'build' subcommand.
    """
    is_dsd = args.output.lower().endswith(".dsd")

    try:
        image_bytes = buildImage(
            source_dir=args.dir,
            tracks=args.tracks,
            is_dsd=is_dsd,
            title=args.title or "",
            boot_option=args.boot,
        )
    except DFSError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    with open(args.output, "wb") as f:
        f.write(image_bytes)

    fmt = "DSD" if is_dsd else "SSD"
    print(f"Built {args.tracks}-track {fmt}: {args.output}")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "BBC Micro DFS disc image tool. "
            "Read catalogues, extract files, detokenize BBC BASIC programs, "
            "and create, modify, and build disc images "
            "from .ssd and .dsd disc images."
        ),
        epilog="Use 'beebtools <command> -h' for detailed help on each command.",
    )
    parser.add_argument("--debug", action="store_true",
                        help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")

    p_cat = sub.add_parser("cat", help="List disc catalogue")
    p_cat.add_argument("image", help="Path to .ssd or .dsd disc image")
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
    p_extract.add_argument("image", help="Path to .ssd or .dsd disc image")
    p_extract.add_argument("filename", nargs="?",
                           help="DFS filename, e.g. T.MYPROG or MYPROG")
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

    p_search = sub.add_parser("search", help="Search BASIC source for a text pattern")
    p_search.add_argument("image", help="Path to .ssd or .dsd disc image")
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
    p_create.add_argument("output", help="Output path (.ssd or .dsd)")
    p_create.add_argument("-t", "--tracks", type=int, default=80,
                          choices=[40, 80], help="Track count (default: 80)")
    p_create.add_argument("--title", help="Disc title (up to 12 characters)")
    p_create.add_argument("--boot", type=_parseBootOption,
                          default=BootOption.OFF,
                          help="Boot option: OFF, LOAD, RUN, EXEC (or 0-3)")

    # -- add subcommand --
    p_add = sub.add_parser("add", help="Add a file to a disc image")
    p_add.add_argument("image", help="Path to .ssd or .dsd disc image")
    p_add.add_argument("file", help="Path to data file to add")
    p_add.add_argument("-n", "--name",
                       help="DFS name (e.g. T.MYPROG or MYPROG for $)")
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
                       choices=[0, 1], help="Disc side (default: 0)")

    # -- delete subcommand --
    p_delete = sub.add_parser("delete", help="Delete a file from a disc image")
    p_delete.add_argument("image", help="Path to .ssd or .dsd disc image")
    p_delete.add_argument("filename",
                          help="DFS filename to delete (e.g. T.MYPROG)")
    p_delete.add_argument("--side", type=int, default=0,
                          choices=[0, 1], help="Disc side (default: 0)")

    # -- build subcommand --
    p_build = sub.add_parser(
        "build", help="Build a disc image from files with .inf sidecars")
    p_build.add_argument("dir", help="Source directory with DFS dir subdirectories")
    p_build.add_argument("output", help="Output path (.ssd or .dsd)")
    p_build.add_argument("-t", "--tracks", type=int, default=80,
                         choices=[40, 80], help="Track count (default: 80)")
    p_build.add_argument("--title", help="Disc title (up to 12 characters)")
    p_build.add_argument("--boot", type=_parseBootOption,
                         default=BootOption.OFF,
                         help="Boot option: OFF, LOAD, RUN, EXEC (or 0-3)")

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
        else:
            parser.print_help()
    except Exception as e:
        if args.debug:
            raise
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
