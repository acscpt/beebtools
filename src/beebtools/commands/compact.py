# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'compact' command - defragment a DFS disc image."""

from argparse import Namespace

from ..disc import compactDisc
from ._registry import argument, cli


@cli.command("compact", help="Defragment a DFS disc image")
@argument("image", help="Path to disc image (.ssd or .dsd)")
@argument(
    "--side", type=int, default=0, choices=[0, 1],
    help="Disc side for DFS (default: 0)",
)
def cmdCompact(args: Namespace) -> None:
    """Defragment a DFS disc image by closing gaps between files."""
    freed = compactDisc(args.image, side=args.side)

    # Report the result in sectors and bytes.
    sectors = freed // 256

    if freed == 0:
        print("Disc is already fully compacted")
    else:
        print(f"Freed {sectors} sectors ({freed} bytes)")
