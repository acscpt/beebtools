# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'rename' command - rename a file on a disc image."""

from argparse import Namespace

from ..disc import renameFile
from ._registry import argument, cli, imageArg, sideArg


@cli.command("rename", help="Rename a file on a disc image")
@imageArg()
@argument("old_name", help="Current filename (e.g. T.MYPROG)")
@argument("new_name", help="New filename (e.g. T.NEWNAME)")
@sideArg()
def cmdRename(args: Namespace) -> None:
    """Rename a file on a disc image."""
    renameFile(
        args.image, args.old_name, args.new_name, side=args.side,
    )
    print(f"Renamed {args.old_name} -> {args.new_name}")
