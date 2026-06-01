# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'title' command - read or set the disc title."""

from argparse import Namespace

from ..disc import getTitle, setTitle
from ._registry import argument, cli, imageArg, sideArg


@cli.command("title", help="Read or set the disc title")
@imageArg()
@argument(
    "title", nargs="?", default=None,
    help="New title (omit to print current title)",
)
@sideArg()
def cmdTitle(args: Namespace) -> None:
    """Read or set the disc title on an existing image."""
    if args.title is None:
        # Getter mode - print the current title.
        title = getTitle(args.image, side=args.side)
        print(title)
    else:
        # Setter mode - update the title.
        setTitle(args.image, args.title, side=args.side)
        print(f"Title set to '{args.title}'")
