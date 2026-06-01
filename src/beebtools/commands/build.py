# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'build' command - build a disc image from a directory of files + .inf."""

import contextlib
import sys
from argparse import Namespace

from ..disc import buildImage
from ..entry import DiscError
from ..validation import strictMode
from ._helpers import formatLabel
from ._registry import argument, cli, parseBootOption, tracksArg


@cli.command(
    "build", help="Build a disc image from files with .inf sidecars",
)
@argument(
    "dir",
    help="Source directory (DFS: dir subdirs; ADFS: $ tree)",
)
@argument("output", help="Output path (.ssd, .dsd, .adf, or .adl)")
@tracksArg()
@argument(
    "--title", default=None,
    help="Disc title (overrides $.inf when --force is set)",
)
@argument(
    "--boot", type=parseBootOption, default=None,
    help=(
        "Boot option: OFF, LOAD, RUN, EXEC (or 0-3) "
        "(overrides $.inf when --force is set)"
    ),
)
@argument(
    "--force", action="store_true",
    help="Override $.inf disc metadata with explicit --title/--boot values",
)
@argument(
    "--strict", action="store_true",
    help=(
        "Enforce DFS spec-compliance on filenames "
        "(rejects non-printable bytes, '.', '#', '*', ':', '\"', and space)"
    ),
)
def cmdBuild(args: Namespace) -> None:
    """Build a disc image from a directory of files with .inf sidecars."""
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

    label = formatLabel(args.output, args.tracks, len(image_bytes))
    print(f"Built {label}: {args.output}")
