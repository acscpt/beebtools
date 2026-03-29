# SPDX-FileCopyrightText: 2026 beebtools contributors
# SPDX-License-Identifier: MIT

"""CLI commands for beebtools.

Implements the 'cat' and 'extract' subcommands and the main() entry point.
"""

import argparse
import os
import sys

from .detokenize import detokenize
from .pretty import prettyPrint
from .dfs import isBasic, looksLikeText, openDiscImage, sortCatalogueEntries


def cmdCat(args):
    """Print the disc catalogue to stdout.

    Args:
        args: Parsed argparse namespace for the 'cat' subcommand.
    """
    sides = openDiscImage(args.image)

    for disc in sides:
        title, entries = disc.readCatalogue()
        side_label = f"Side {disc.side}"
        header = f"--- {side_label}"

        if title:
            header += f": {title}"

        header += f" ({len(entries)} files) ---"
        print(header)
        print()

        if not entries:
            print("  (empty)")
        else:
            orderedEntries = sortCatalogueEntries(entries, args.sort)
            print(f"  {'Name':<12s} {'Load':>8s} {'Exec':>8s} {'Length':>8s}  {'Type'}")

            for e in orderedEntries:
                ftype = "BASIC" if isBasic(e) else ""
                lock = "L" if e["locked"] else " "
                full_name = f"{e['dir']}.{e['name']}"
                print(
                    f"  {lock}{full_name:<11s} "
                    f"{e['load']:08X} "
                    f"{e['exec']:08X} "
                    f"{e['length']:08X}  "
                    f"{ftype}"
                )
        print()


def _extractAll(args):
    """Extract every file from a disc image into a directory.

    BASIC programs are saved as .bas plain text files.
    Binary files are saved as .bin raw bytes.

    Args:
        args: Parsed argparse namespace for the 'extract' subcommand.
    """
    sides = openDiscImage(args.image)

    # Default output directory is the image filename stem (disc39 for disc39.dsd).
    if args.dir:
        out_dir = args.dir
    else:
        out_dir = os.path.splitext(os.path.basename(args.image))[0]

    os.makedirs(out_dir, exist_ok=True)

    # Prefix filenames with the side number when the image has two sides,
    # to avoid collisions between identically-named files on each side.
    multi_side = len(sides) > 1

    for disc in sides:
        _title, entries = disc.readCatalogue()

        for entry in entries:
            base = f"{entry['dir']}.{entry['name']}"
            if multi_side:
                base = f"side{disc.side}_{base}"

            data = disc.readFile(entry)

            if isBasic(entry) and looksLikeText(data):
                # Detokenize BASIC and write as plain text.
                out_path = os.path.join(out_dir, base + ".bas")
                text_lines = detokenize(data)
                if args.pretty:
                    text_lines = prettyPrint(text_lines)
                with open(out_path, "w", encoding="ascii", errors="replace") as f:
                    f.write("\n".join(text_lines) + "\n")
                print(f"  BASIC   {out_path}")

            else:
                # Write binary file and report addressing metadata.
                out_path = os.path.join(out_dir, base + ".bin")
                with open(out_path, "wb") as f:
                    f.write(data)
                print(
                    f"  binary  {out_path}  "
                    f"load=0x{entry['load']:06X}  "
                    f"exec=0x{entry['exec']:06X}  "
                    f"length={entry['length']} bytes"
                )


def cmdExtract(args):
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
        _extractAll(args)
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


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="BBC Micro DFS disc image tool",
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

    args = parser.parse_args()

    if args.command == "cat":
        cmdCat(args)
    elif args.command == "extract":
        cmdExtract(args)
    else:
        parser.print_help()
