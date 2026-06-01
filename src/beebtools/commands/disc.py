# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'disc' command - print disc summary or set disc-level properties."""

from argparse import Namespace

from ..disc import discInfo, setBoot, setTitle
from ._helpers import BOLD, CYAN, colour, useColour
from ._registry import argument, cli, imageArg, parseBootOption, sideArg


@cli.command("disc", help="Print disc summary or set disc properties")
@imageArg()
@argument(
    "--title", dest="set_title", default=None,
    help="Set the disc title",
)
@argument(
    "--boot", dest="set_boot", type=parseBootOption, default=None,
    help="Set boot option: OFF, LOAD, RUN, EXEC",
)
@sideArg()
def cmdDisc(args: Namespace) -> None:
    """Print disc summary or set disc-level properties."""
    has_mutations = args.set_title is not None or args.set_boot is not None

    if has_mutations:
        # Apply requested mutations.
        if args.set_title is not None:
            setTitle(args.image, args.set_title, side=args.side)

        if args.set_boot is not None:
            setBoot(args.image, args.set_boot, side=args.side)

        # Confirm what was changed.
        parts = []
        if args.set_title is not None:
            parts.append(f"title='{args.set_title}'")
        if args.set_boot is not None:
            parts.append(f"boot={args.set_boot.name}")
        print(f"Updated: {', '.join(parts)}")
    else:
        # Summary mode - print disc metadata.
        use_colour = useColour()
        info = discInfo(args.image, side=args.side)

        title_display = info.title if info.title else "(none)"
        print(f"Title:  {colour(title_display, BOLD + CYAN, use_colour)}")
        print(f"Boot:   {info.boot_option.name}")
        print(f"Tracks: {info.tracks}")
        print(f"Free:   {info.free_space:,} bytes "
              f"({info.free_space // 256} sectors)")
