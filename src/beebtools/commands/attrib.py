# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'attrib' command - read or set file attributes on a disc image."""

import argparse
from argparse import Namespace

from ..disc import getFileAttribs, setFileAttribs
from ._helpers import RED, colour, loadResourceBundles, useColour
from ._registry import argument, cli, imageArg, sideArg


# Build the --access help text from every *_resources module so each
# format contributes its own grammar block without the CLI naming
# format engines directly. Resolved at import time so it appears in
# --help output for the subcommand.
_cli_resources = loadResourceBundles("cli")
_access_help = (
    "Set access flags using format-specific grammar.\n"
    "Mutually exclusive with --locked / --unlocked.\n\n"
    + _cli_resources.get("attrib.access", "")
)


@cli.command(
    "attrib", help="Read or set file attributes",
    formatter_class=argparse.RawTextHelpFormatter,
)
@imageArg()
@argument("filename", help="Filename (e.g. T.MYPROG or $.GAMES.ELITE)")
@argument(
    "--locked", action="store_const", const=True, default=None, dest="locked",
    help="Lock the file",
)
@argument(
    "--unlocked", action="store_const", const=False, dest="locked",
    help="Unlock the file",
)
@argument(
    "--access", default=None, dest="access",
    help=_access_help,
)
@argument("--load", default=None, help="Load address in hex (e.g. FF1900)")
@argument(
    "--exec", dest="exec_addr", default=None,
    help="Exec address in hex (e.g. FF8023)",
)
@sideArg()
def cmdAttrib(args: Namespace) -> None:
    """Read or set file attributes on a disc image."""
    has_mutations = (
        args.locked is not None
        or args.access is not None
        or args.load is not None
        or args.exec_addr is not None
    )

    if has_mutations:
        # Parse hex addresses.
        load_addr = int(args.load, 16) if args.load is not None else None
        exec_addr = int(args.exec_addr, 16) if args.exec_addr is not None else None

        setFileAttribs(
            args.image, args.filename, side=args.side,
            locked=args.locked,
            load_addr=load_addr,
            exec_addr=exec_addr,
            access_flags=args.access,
        )

        # Confirm what was changed.
        parts = []
        if args.locked is not None:
            parts.append("locked" if args.locked else "unlocked")
        if args.access is not None:
            parts.append(f"access={args.access!r}")
        if load_addr is not None:
            parts.append(f"load={load_addr:08X}")
        if exec_addr is not None:
            parts.append(f"exec={exec_addr:08X}")
        print(f"Updated {args.filename}: {', '.join(parts)}")
    else:
        # Getter mode - print current attributes.
        use_colour = useColour()
        attribs = getFileAttribs(args.image, args.filename, side=args.side)

        lock_str = "L" if attribs.locked else "-"
        lock_display = colour(lock_str, RED, use_colour and attribs.locked)
        access_display = attribs.access_string if attribs.access_string else "-"
        print(f"File:   {attribs.fullName}")
        print(f"Load:   {attribs.load_addr:08X}")
        print(f"Exec:   {attribs.exec_addr:08X}")
        print(f"Length: {attribs.length:08X}")
        print(f"Locked: {lock_display}")
        print(f"Access: {access_display}")
