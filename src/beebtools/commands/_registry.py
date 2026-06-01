# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Decorator-driven subcommand registry for the beebtools CLI.

Provides a small ``CLI`` class plus an ``argument`` decorator so each
command module can declare its subcommand and its arguments next to the
handler function. Replaces the historical pattern of building every
subparser inline in ``main()`` and dispatching with a long if/elif chain.

Command modules register themselves by importing the shared ``cli``
instance from this module and applying the ``@cli.command(...)`` and
``@argument(...)`` decorators. Registration runs at import time, so
``commands/__init__.py`` only has to import each command module for its
subcommand to appear in ``--help``.
"""

import argparse
import sys
import warnings
from argparse import ArgumentParser, Namespace
from typing import Any, Callable, List, Optional, Tuple

from .. import __version__
from ..boot import BootOption
from ..shared import BeebToolsWarning


# ---------------------------------------------------------------------------
# Argument type converters
# ---------------------------------------------------------------------------

def parseBootOption(value: str) -> BootOption:
    """Argparse type wrapper around BootOption.parse()."""
    try:
        return BootOption.parse(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def argument(*name_or_flags: str, **kwargs: Any) -> Callable:
    """Attach an argparse argument spec to a command handler.

    Stacks onto a ``_cli_args`` attribute on the function. Multiple
    ``@argument`` decorators stack in source order from top to bottom
    in the final ``--help`` output. The ``@cli.command`` decorator
    (which must appear above the argument decorators) consumes the
    list and calls ``add_argument`` for each entry.
    """

    def decorator(func: Callable) -> Callable:
        # Lazily create the per-function arg list. Stored on the
        # function object so the registry can pick it up later.
        if not hasattr(func, "_cli_args"):
            func._cli_args = []  # type: ignore[attr-defined]

        # Decorators apply bottom-up, so a later append corresponds to
        # an argument declared earlier in the source. We reverse the
        # list when building the parser to restore source order.
        func._cli_args.append((name_or_flags, kwargs))  # type: ignore[attr-defined]
        return func

    return decorator


class CLI:
    """Argparse-based CLI with decorator-registered subcommands.

    A single shared instance lives at module scope (``cli``) so every
    command module can hang its handlers off the same parser tree.
    Call ``run()`` from ``main()`` to parse argv and dispatch.
    """

    def __init__(
        self,
        prog: str,
        description: str = "",
        epilog: str = "",
        version: str = "",
    ) -> None:
        # The top-level parser carries the cross-cutting --debug flag,
        # which suppresses the friendly error wrapper in run() so that
        # exceptions surface with a full traceback.
        self.parser = ArgumentParser(
            prog=prog,
            description=description,
            epilog=epilog,
        )
        self.parser.add_argument(
            "--debug", action="store_true", help=argparse.SUPPRESS,
        )

        # -v/--version: argparse's built-in 'version' action prints the
        # supplied string and exits. The value is also surfaced in the
        # top-level description so 'beebtools --help' shows it.
        if version:
            self.parser.add_argument(
                "-v", "--version",
                action="version",
                version=f"%(prog)s {version}",
            )

        # All commands attach themselves under this subparsers group.
        self.subparsers = self.parser.add_subparsers(dest="command")

    def command(self, name: str, **parser_kwargs: Any) -> Callable:
        """Decorator: register ``func`` as the handler for ``name``.

        ``parser_kwargs`` is forwarded to ``add_parser`` so callers can
        set ``help=``, ``description=``, ``formatter_class=`` and so on
        right next to the handler.
        """

        def decorator(func: Callable[[Namespace], None]) -> Callable:
            sub = self.subparsers.add_parser(name, **parser_kwargs)

            # Apply any @argument decorators stacked above this one.
            # Reverse so source order matches --help output.
            stacked: List[Tuple[Tuple[str, ...], dict]] = list(
                reversed(getattr(func, "_cli_args", []))
            )
            for args_tuple, kwargs in stacked:
                sub.add_argument(*args_tuple, **kwargs)

            sub.set_defaults(func=func)
            return func

        return decorator

    def run(self, argv: Optional[List[str]] = None) -> None:
        """Parse argv and dispatch to the chosen subcommand handler."""

        # Install our warnings formatter so BeebToolsWarning prints
        # without the noisy file/line prefix.
        warnings.formatwarning = _formatWarning

        args = self.parser.parse_args(argv)

        # No subcommand supplied: print top-level help and exit.
        func = getattr(args, "func", None)
        if func is None:
            self.parser.print_help()
            return

        # Friendly error wrapper: in normal use turn exceptions into a
        # one-line message; with --debug, let them propagate.
        try:
            func(args)
        except Exception as e:
            if getattr(args, "debug", False):
                raise
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)


# ---------------------------------------------------------------------------
# Warnings formatter
# ---------------------------------------------------------------------------

_default_formatwarning = warnings.formatwarning


def _formatWarning(
    message: object,
    category: type,
    filename: str,
    lineno: int,
    line: Optional[str] = None,
) -> str:
    """Format BeebToolsWarning as plain 'Warning: ...' for CLI output."""
    if issubclass(category, BeebToolsWarning):
        return f"Warning: {message}\n"
    return _default_formatwarning(message, category, filename, lineno, line)


# ---------------------------------------------------------------------------
# Shared argument shortcuts
# ---------------------------------------------------------------------------

def imageArg(help_text: str = "Path to disc image (.ssd, .dsd, .adf, or .adl)") -> Callable:
    """@imageArg() - add the standard ``image`` positional."""
    return argument("image", help=help_text)


def sideArg(help_text: str = "Disc side for DFS (default: 0; ignored for ADFS)") -> Callable:
    """@sideArg() - add the standard ``--side`` option."""
    return argument(
        "--side", type=int, default=0, choices=[0, 1], help=help_text,
    )


def tracksArg() -> Callable:
    """@tracksArg() - add the standard ``-t/--tracks`` option."""
    return argument(
        "-t", "--tracks", type=int, default=80, choices=[40, 80],
        help=(
            "Track count (default: 80). "
            "For ADFS: 40t .adf=160K, 80t .adf=320K, .adl=640K"
        ),
    )


# ---------------------------------------------------------------------------
# Singleton CLI instance
# ---------------------------------------------------------------------------

cli = CLI(
    prog="beebtools",
    description=(
        f"BBC Micro disc image tool (version {__version__}). "
        "Read catalogues, extract files, detokenize BBC BASIC programs, "
        "and create, modify, and build disc images. "
        "Supports DFS (.ssd, .dsd) and ADFS (.adf, .adl) formats."
    ),
    epilog="Use 'beebtools <command> -h' for detailed help on each command.",
    version=__version__,
)
