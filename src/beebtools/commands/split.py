# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'split' command - split a DSD image into two SSD halves."""

from argparse import Namespace

from ..disc import splitImage
from ._registry import argument, cli


@cli.command(
    "split",
    help="Split a DSD image into two SSD halves",
    description=(
        "Split a DSD disc image into two SSD files. With no output "
        "name, derives '<source>-side0.ssd' and '<source>-side1.ssd'. "
        "With one output name, derives '<name>-side0.ssd' and "
        "'<name>-side1.ssd'. With two output names, uses both "
        "verbatim."
    ),
)
@argument("source", help="Path to source .dsd image")
@argument(
    "outputs", nargs="*",
    help="Optional output stem, or two explicit .ssd paths",
)
@argument(
    "--seq", action="store_true",
    help=(
        "Treat source as sequential (side 0 followed by "
        "side 1) rather than interleaved track-by-track"
    ),
)
@argument(
    "-f", "--force", action="store_true",
    help="Overwrite existing output files",
)
def cmdSplit(args: Namespace) -> None:
    """Split a DSD image into two SSD halves."""
    # Pass through the user's optional output names verbatim so disc.py
    # can apply its 0/1/2-argument naming rules.
    out0, out1 = splitImage(
        args.source,
        *args.outputs,
        sequential=args.seq,
        force=args.force,
    )
    print(f"Wrote {out0}")
    print(f"Wrote {out1}")
