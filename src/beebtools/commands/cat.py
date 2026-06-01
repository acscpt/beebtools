# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'cat' command - list a disc catalogue."""

from argparse import Namespace

from ..disc import readCatalogue
from ..entry import FileType
from ._helpers import (
    BOLD, CYAN, GREY, RED, TAG_COLOURS, colour, useColour,
)
from ._registry import argument, cli, imageArg


@cli.command("cat", help="List disc catalogue")
@imageArg()
@argument(
    "-s", "--sort",
    choices=["name", "catalog", "size"], default="name",
    help="Sort order: name (default), catalog, or size",
)
@argument(
    "-i", "--inspect", action="store_true",
    help="Read file contents to detect TEXT files (slower; default is metadata-only)",
)
def cmdCat(args: Namespace) -> None:
    """Print the disc catalogue to stdout.

    Args:
        args: Parsed argparse namespace for the 'cat' subcommand.
    """
    # Enable colour only when writing to a real terminal.
    use_colour = useColour()

    listings = readCatalogue(args.image, sort_mode=args.sort, inspect=args.inspect)

    for listing in listings:
        side_label = f"Side {listing.side}"
        header = f"--- {side_label}"

        if listing.title:
            header += f": {listing.title}"

        boot_desc = listing.boot_option.name
        header += (f" ({listing.entry_count} files,"
                  f" {listing.tracks} tracks,"
                  f" boot={boot_desc}) ---")
        print(colour(header, BOLD, use_colour))
        print()

        if not listing.entries:
            print("  (empty)")
        else:
            # Dynamic column widths. The name column grows with ADFS
            # hierarchical paths; the access column grows with ADFS
            # owner/public combinations like LWR/wre.
            max_name = max(len(ce.entry.fullName) for ce in listing.entries)
            col_width = max(12, max_name + 2)

            max_access = max(
                (len(ce.entry.accessString) for ce in listing.entries),
                default=0,
            )
            access_width = max(6, max_access)

            print(
                f"  {'Access':<{access_width}s}  "
                f"{'Name':<{col_width}s} "
                f"{'Load':>8s} {'Exec':>8s} {'Length':>8s}  "
                f"{'Type'}"
            )

            for ce in listing.entries:
                e = ce.entry

                if e.isDirectory:
                    ftype = colour("DIR", CYAN, use_colour)
                elif ce.file_type in TAG_COLOURS:
                    tag_colour, label = TAG_COLOURS[ce.file_type]
                    ftype = colour(label, tag_colour, use_colour)
                else:
                    ftype = ""

                access_text = e.accessString or "-"
                access = colour(
                    f"{access_text:<{access_width}s}",
                    RED, use_colour and e.locked,
                )
                load   = colour(f"{e.load_addr:08X}",   GREY, use_colour)
                exec_  = colour(f"{e.exec_addr:08X}",   GREY, use_colour)
                length = colour(f"{e.length:08X}", GREY, use_colour)
                print(
                    f"  {access}  "
                    f"{e.fullName:<{col_width}s} "
                    f"{load} "
                    f"{exec_} "
                    f"{length}  "
                    f"{ftype}"
                )
        print()
