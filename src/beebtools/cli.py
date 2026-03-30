# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""CLI commands for beebtools.

Implements the 'cat' and 'extract' subcommands and the main() entry point.
"""

import argparse
import os
import re
import sys
from argparse import Namespace
from typing import Optional

from .detokenize import detokenize
from .pretty import prettyPrint
from .dfs import isBasic, looksLikeText, looksLikePlainText, openDiscImage, sortCatalogueEntries
from .disc import search, extractAll


# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------

_BOLD    = "\x1b[1m"
_CYAN    = "\x1b[36m"
_YELLOW  = "\x1b[33m"
_RED     = "\x1b[31m"
_GREY    = "\x1b[90m"
_RESET   = "\x1b[0m"


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

    for disc in sides:
        title, entries = disc.readCatalogue()
        side_label = f"Side {disc.side}"
        header = f"--- {side_label}"

        if title:
            header += f": {title}"

        header += f" ({len(entries)} files) ---"
        print(_colour(header, _BOLD, use_colour))
        print()

        if not entries:
            print("  (empty)")
        else:
            orderedEntries = sortCatalogueEntries(entries, args.sort)
            print(f"  {'Name':<12s} {'Load':>8s} {'Exec':>8s} {'Length':>8s}  {'Type'}")

            for e in orderedEntries:
                if isBasic(e):
                    ftype = _colour("BASIC", _CYAN, use_colour)
                elif args.inspect and looksLikePlainText(disc.readFile(e)):
                    ftype = _colour("TEXT", _YELLOW, use_colour)
                else:
                    ftype = ""
                lock_char = "L" if e["locked"] else " "
                lock = _colour(lock_char, _RED, use_colour and e["locked"])
                full_name = f"{e['dir']}.{e['name']}"
                load   = _colour(f"{e['load']:08X}",   _GREY, use_colour)
                exec_  = _colour(f"{e['exec']:08X}",   _GREY, use_colour)
                length = _colour(f"{e['length']:08X}", _GREY, use_colour)
                print(
                    f"  {lock}{full_name:<11s} "
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
        sides_mode = getattr(args, "sides", None)

        results = extractAll(args.image, out_dir, sides_mode=sides_mode, pretty=args.pretty)
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

        for disc in sides:
            _title, entries = disc.readCatalogue()
            for e in entries:
                if (e["dir"].upper() == target_dir
                        and e["name"].upper() == target_name.upper()):
                    found = (disc, e)
                    break
            if found:
                break

    else:
        # Bare filename - find a unique match across all sides and directories.
        target_name = target
        matches = []

        for disc in sides:
            _title, entries = disc.readCatalogue()
            for e in entries:
                if e["name"].upper() == target_name.upper():
                    matches.append((disc, e))

        if len(matches) == 1:
            found = matches[0]
        elif len(matches) > 1:
            print(
                f"Ambiguous filename '{target_name}' - specify with directory prefix.",
                file=sys.stderr,
            )
            for disc, entry in matches:
                print(f"  Side {disc.side}: {entry['dir']}.{entry['name']}",
                      file=sys.stderr)
            sys.exit(1)

    if not found:
        print(f"File not found: {target}", file=sys.stderr)
        sys.exit(1)

    disc, entry = found
    data = disc.readFile(entry)
    full_name = f"{entry['dir']}.{entry['name']}"

    if isBasic(entry) and looksLikeText(data):
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
                f"{full_name}  "
                f"load=0x{entry['load']:06X}  "
                f"exec=0x{entry['exec']:06X}  "
                f"length={entry['length']} bytes"
            )
        else:
            # Raw bytes to stdout for piping to a disassembler.
            sys.stdout.buffer.write(data)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "BBC Micro DFS disc image tool. "
            "Read catalogues, extract files, and detokenize BBC BASIC programs "
            "from .ssd and .dsd disc images."
        ),
        epilog="Use 'beebtools <command> -h' for detailed help on each command.",
    )
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
    p_extract.add_argument(
        "-s", "--sides",
        choices=["subdir", "prefix"],
        default=None,
        help=(
            "How to separate files from each side of a double-sided disc "
            "when using -a/--all. "
            "'subdir' (default for double-sided discs) writes into side0/ and side1/ "
            "subdirectories; "
            "'prefix' prepends side0_ or side1_ to each filename in a flat layout."
        ),
    )

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

    args = parser.parse_args()

    if args.command == "cat":
        cmdCat(args)
    elif args.command == "extract":
        cmdExtract(args)
    elif args.command == "search":
        cmdSearch(args)
    else:
        parser.print_help()
