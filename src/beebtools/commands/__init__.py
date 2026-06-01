# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Subcommand package for the beebtools CLI.

Importing this package registers every subcommand against the shared
``cli`` instance from ``_registry``. Each command lives in its own
module so handlers and their argparse arguments are co-located.

To add a new command:
  1. Create ``commands/<name>.py``.
  2. Import ``cli``, ``argument`` (and optional helpers) from
     ``._registry``.
  3. Decorate the handler with ``@cli.command(<name>, ...)`` and stack
     ``@argument(...)`` decorators above it in source-order.
  4. Add an import line to this file so the decorators fire on
     package import.
"""

from ._registry import cli

# Importing each module triggers its @cli.command decorators and
# registers the subcommand. Order here controls the order in --help.
from . import cat       # noqa: F401
from . import extract   # noqa: F401
from . import search    # noqa: F401
from . import create    # noqa: F401
from . import add       # noqa: F401
from . import delete    # noqa: F401
from . import build     # noqa: F401
from . import title     # noqa: F401
from . import boot      # noqa: F401
from . import disc      # noqa: F401
from . import attrib    # noqa: F401
from . import rename    # noqa: F401
from . import compact   # noqa: F401
from . import mkdir     # noqa: F401
from . import split     # noqa: F401
from . import merge     # noqa: F401


def main() -> None:
    """CLI entry point - parse argv and dispatch to the selected command."""
    cli.run()


__all__ = ["cli", "main"]
