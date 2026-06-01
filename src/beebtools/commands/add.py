# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""'add' command - add a file to an existing disc image."""

import contextlib
import os
import sys
from argparse import Namespace

from ..disc import addFile, qualifyDiscPath
from ..entry import DiscError, DiscFile
from ..inf import parseInf
from ..validation import strictMode
from ._registry import argument, cli, imageArg, sideArg


@cli.command("add", help="Add a file to a disc image")
@imageArg()
@argument("file", help="Path to data file to add")
@argument("-n", "--name", help="File name (DFS: T.MYPROG; ADFS: $.DIR.FILE)")
@argument("--load", help="Load address in hex (default: 0)")
@argument(
    "--exec", dest="exec_addr",
    help="Exec address in hex (default: 0)",
)
@argument(
    "--basic", action="store_true",
    help="Set BASIC defaults (load=0x1900, exec=0x8023)",
)
@argument(
    "--locked", action="store_true",
    help="Lock the file against deletion",
)
@argument(
    "--inf", action="store_true",
    help="Read metadata from a .inf sidecar file",
)
@sideArg()
@argument(
    "--strict", action="store_true",
    help="Enforce DFS spec-compliance on the filename",
)
def cmdAdd(args: Namespace) -> None:
    """Add a file to an existing disc image (DFS or ADFS)."""
    if args.inf:
        # Warn if --basic was also specified - .inf overrides everything.
        if args.basic:
            print("Warning: --basic ignored when --inf is used",
                  file=sys.stderr)

        # Read metadata from a .inf sidecar file.
        inf_path = args.file + ".inf"

        if not os.path.isfile(inf_path):
            print(f"Error: .inf sidecar not found: {inf_path}",
                  file=sys.stderr)
            sys.exit(1)

        with open(inf_path, "r", encoding="ascii") as f:
            inf_line = f.readline().strip()

        inf = parseInf(inf_line)
        disc_path = inf.fullName
        load_addr = inf.load_addr
        exec_addr = inf.exec_addr
        locked = inf.locked
    else:
        # Metadata from command-line arguments.
        file_name = args.name

        if not file_name:
            print("Error: --name is required (or use --inf).",
                  file=sys.stderr)
            sys.exit(1)

        # Normalise the path to a fully-qualified disc path.
        disc_path = qualifyDiscPath(file_name)

        # Apply BASIC defaults first, then let explicit flags override.
        if args.basic:
            load_addr = 0x1900
            exec_addr = 0x8023
        else:
            load_addr = 0
            exec_addr = 0

        if args.load:
            override = int(args.load, 16)
            if args.basic:
                print(f"Note: --load overrides BASIC default"
                      f" (0x{load_addr:04X} -> 0x{override:04X})",
                      file=sys.stderr)
            load_addr = override

        if args.exec_addr:
            override = int(args.exec_addr, 16)
            if args.basic:
                print(f"Note: --exec overrides BASIC default"
                      f" (0x{exec_addr:04X} -> 0x{override:04X})",
                      file=sys.stderr)
            exec_addr = override

        locked = args.locked

    with open(args.file, "rb") as f:
        data = f.read()

    original_size = len(data)

    try:
        ctx = strictMode() if getattr(args, "strict", False) else contextlib.nullcontext()
        with ctx:
            entry = addFile(
                args.image,
                DiscFile(
                    path=disc_path, data=data,
                    load_addr=load_addr, exec_addr=exec_addr,
                    locked=locked,
                ),
                side=args.side,
                retokenize=args.basic,
            )
    except DiscError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Report tokenization if the stored size differs from the original.
    if args.basic and entry.length != original_size:
        print(f"Tokenized {args.file} ({entry.length} bytes)", file=sys.stderr)

    print(f"Added {disc_path} ({entry.length} bytes) to {args.image}")
