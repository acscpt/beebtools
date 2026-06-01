# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'delete' command - delete a file from a disc image."""

import sys
from argparse import Namespace

from ..disc import deleteFile, qualifyDiscPath
from ..entry import DiscError
from ._registry import argument, cli, imageArg, sideArg


@cli.command("delete", help="Delete a file from a disc image")
@imageArg()
@argument(
    "filename",
    help="Filename to delete (DFS: T.MYPROG; ADFS: $.DIR.FILE)",
)
@sideArg()
def cmdDelete(args: Namespace) -> None:
    """Delete a file from a disc image (DFS or ADFS)."""
    disc_path = qualifyDiscPath(args.filename)

    try:
        deleteFile(args.image, args.filename, side=args.side)
    except DiscError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Deleted {disc_path} from {args.image}")
