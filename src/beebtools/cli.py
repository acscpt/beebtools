# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Backward-compatibility shim for the historical ``beebtools.cli`` module.

The CLI was refactored into the ``beebtools.commands`` package: each
subcommand handler now lives in its own module, registered against a
shared ``cli`` instance via decorators (see commands/_registry.py).

This module re-exports the symbols that external callers (tests,
scripts) historically imported from ``beebtools.cli`` so existing
imports keep working without change.

New code should import from ``beebtools`` (public API) or from the
specific command module under ``beebtools.commands.<name>``.
"""

# Import the registry first so the package's main() is available.
from .commands import cli, main
from .commands._registry import parseBootOption as _parseBootOption
from .commands._helpers import (
    colour as _colour,
    loadResourceBundles as _loadResourceBundles,
    useColour as _useColour,
)

# Re-export every subcommand handler under its historical name.
from .commands.cat import cmdCat
from .commands.extract import cmdExtract
from .commands.search import cmdSearch
from .commands.create import cmdCreate
from .commands.add import cmdAdd
from .commands.delete import cmdDelete
from .commands.build import cmdBuild
from .commands.title import cmdTitle
from .commands.boot import cmdBoot
from .commands.disc import cmdDisc
from .commands.attrib import cmdAttrib
from .commands.rename import cmdRename
from .commands.compact import cmdCompact
from .commands.mkdir import cmdMkdir
from .commands.split import cmdSplit
from .commands.merge import cmdMerge

__all__ = [
    "main",
    "cli",
    "_parseBootOption",
    "_colour",
    "_loadResourceBundles",
    "_useColour",
    "cmdCat", "cmdExtract", "cmdSearch", "cmdCreate", "cmdAdd",
    "cmdDelete", "cmdBuild", "cmdTitle", "cmdBoot", "cmdDisc",
    "cmdAttrib", "cmdRename", "cmdCompact", "cmdMkdir",
    "cmdSplit", "cmdMerge",
]
