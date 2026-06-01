# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'extract' command - extract one or all files from a disc image."""

import os
import sys
import warnings
from argparse import Namespace

from ..disc import (
    escapeNonAscii, extractAll, extractFile, writeBasicText,
)
from ..entry import DiscError, FileType
from ._registry import argument, cli, imageArg


@cli.command("extract", help="Extract a file, or all files with -a")
@imageArg()
@argument(
    "filename", nargs="?",
    help="Filename, e.g. T.MYPROG or $.GAMES.ELITE",
)
@argument(
    "-a", "--all", action="store_true",
    help="Extract all files from the disc",
)
@argument(
    "-o", "--output",
    help="Write single file to this path instead of stdout",
)
@argument(
    "-d", "--dir",
    help="Output directory for -a/--all (default: image name)",
)
@argument(
    "--pretty", action="store_true",
    help="Add operator spacing to BASIC output",
)
@argument(
    "--inf", action="store_true",
    help="Accepted for backwards compatibility (now the default)",
)
@argument(
    "--no-inf", action="store_true", dest="no_inf",
    help="Suppress .inf sidecar files (written by default)",
)
@argument(
    "--mkdirs", action="store_true",
    help="Create subdirectories from Acorn paths instead of flat layout",
)
@argument(
    "-t", "--text", choices=["ascii", "utf8", "escape"],
    default="escape", dest="text_mode",
    help=(
        "Text encoding for BASIC .bas files: "
        "escape (\\xHH notation, lossless, default), "
        "utf8 (lossless), ascii (lossy). "
        "Controls how non-ASCII bytes such as teletext "
        "control codes in PRINT strings are written"
    ),
)
def cmdExtract(args: Namespace) -> None:
    """Extract a file (or all files) from the disc image."""
    # Default text_mode for callers that build a Namespace directly.
    text_mode = getattr(args, "text_mode", "escape")

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
