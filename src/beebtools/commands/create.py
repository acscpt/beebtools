# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'create' command - create a blank formatted disc image."""

from argparse import Namespace

from ..boot import BootOption
from ..disc import createImageFile
from ._helpers import formatLabel
from ._registry import argument, cli, parseBootOption, tracksArg


@cli.command("create", help="Create a blank disc image")
@argument("output", help="Output path (.ssd, .dsd, .adf, or .adl)")
@tracksArg()
@argument("--title", help="Disc title")
@argument(
    "--boot", type=parseBootOption, default=BootOption.OFF,
    help="Boot option: OFF, LOAD, RUN, EXEC (or 0-3)",
)
def cmdCreate(args: Namespace) -> None:
    """Create a blank formatted disc image (DFS or ADFS)."""
    size_bytes = createImageFile(
        args.output,
        tracks=args.tracks,
        title=args.title or "",
        boot_option=args.boot,
    )

    label = formatLabel(args.output, args.tracks, size_bytes)
    print(f"Created {label}: {args.output}")
