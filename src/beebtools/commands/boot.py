# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'boot' command - read or set the disc boot option."""

from argparse import Namespace

from ..disc import getBoot, setBoot
from ._registry import argument, cli, imageArg, parseBootOption, sideArg


@cli.command("boot", help="Read or set the disc boot option")
@imageArg()
@argument(
    "boot", nargs="?", type=parseBootOption, default=None,
    help="Boot option: OFF, LOAD, RUN, EXEC (omit to print current value)",
)
@sideArg()
def cmdBoot(args: Namespace) -> None:
    """Read or set the disc boot option on an existing image."""
    if args.boot is None:
        # Getter mode - print the current boot option.
        boot = getBoot(args.image, side=args.side)
        print(boot.name)
    else:
        # Setter mode - update the boot option.
        setBoot(args.image, args.boot, side=args.side)
        print(f"Boot option set to {args.boot.name}")
