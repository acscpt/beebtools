# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'mkdir' command - create a subdirectory on an ADFS disc image."""

from argparse import Namespace

from ..disc import makeDirectory
from ._registry import argument, cli


@cli.command("mkdir", help="Create a subdirectory on an ADFS disc image")
@argument("image", help="Path to disc image (.adf or .adl)")
@argument(
    "path",
    help="Directory path (e.g. $.GAMES or $.GAMES.ARCADE)",
)
@argument(
    "--side", type=int, default=0, choices=[0, 1],
    help="Disc side (default: 0; ignored for ADFS)",
)
def cmdMkdir(args: Namespace) -> None:
    """Create a subdirectory on an ADFS disc image."""
    makeDirectory(args.image, args.path, side=args.side)
    print(f"Created directory {args.path}")
