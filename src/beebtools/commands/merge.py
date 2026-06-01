# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'merge' command - merge two SSD images into a single DSD."""

from argparse import Namespace

from ..disc import mergeImages
from ._registry import argument, cli


@cli.command(
    "merge",
    help="Merge two SSD images into a single DSD",
)
@argument("side0", help="Path to side-0 .ssd image")
@argument("side1", help="Path to side-1 .ssd image")
@argument("output", help="Path to write .dsd image")
@argument(
    "--seq", action="store_true",
    help=(
        "Write sequential layout (side 0 followed by "
        "side 1) rather than interleaved track-by-track"
    ),
)
@argument(
    "-f", "--force", action="store_true",
    help="Overwrite an existing output file",
)
def cmdMerge(args: Namespace) -> None:
    """Merge two SSD images into a single DSD."""
    out = mergeImages(
        args.side0,
        args.side1,
        args.output,
        sequential=args.seq,
        force=args.force,
    )
    print(f"Wrote {out}")
