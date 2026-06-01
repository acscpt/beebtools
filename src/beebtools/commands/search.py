# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'search' command - search BASIC source on a disc for a pattern."""

import re
import sys
from argparse import Namespace

from ..disc import search
from ._helpers import BOLD, GREY, colour, useColour
from ._registry import argument, cli, imageArg


@cli.command("search", help="Search BASIC source for a text pattern")
@imageArg()
@argument("pattern", help="Text to search for")
@argument(
    "filename", nargs="?",
    help="Limit search to this file (e.g. T.MYPROG or MYPROG)",
)
@argument(
    "-i", "--ignore-case", action="store_true",
    help="Case-insensitive search",
)
@argument(
    "-r", "--regex", action="store_true",
    help="Treat pattern as a Python regular expression",
)
@argument(
    "--pretty", action="store_true",
    help="Match after applying pretty-printer spacing",
)
def cmdSearch(args: Namespace) -> None:
    """Search BASIC files on a disc for lines matching a text pattern."""

    # Enable colour only when writing to a real terminal.
    use_colour = useColour()

    try:
        matches = search(
            args.image,
            args.pattern,
            filename=args.filename,
            ignore_case=args.ignore_case,
            pretty=args.pretty,
            use_regex=args.regex,
        )
    except re.error as e:
        print(f"Invalid regex pattern: {e}", file=sys.stderr)
        sys.exit(1)

    if not matches:
        return

    # Group results by (side, filename) so the file header only prints once.
    current_file = None
    for m in matches:
        file_key = (m["side"], m["filename"])
        if file_key != current_file:
            current_file = file_key
            header = f"--- Side {m['side']}: {m['filename']} ---"
            print(colour(header, BOLD, use_colour))
        linenum = colour(f"{m['line_number']:>5d}", GREY, use_colour)
        content = m["line"][5:]
        print(f"  {linenum}{content}")
