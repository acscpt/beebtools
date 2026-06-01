# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for the decorator-driven CLI registry and helpers.

Covers the scaffolding introduced when the monolithic ``cli.py`` was
split into the ``beebtools.commands`` package: the ``CLI`` class,
``argument`` and ``cli.command`` decorators, shared-argument shortcuts,
top-level flags (``-v/--version``, ``--debug``), the friendly error
wrapper, and the BeebToolsWarning formatter.
"""

import argparse
import contextlib
import io
import sys
import warnings

import pytest

import beebtools
from beebtools import cli as cli_shim
from beebtools.commands import _helpers, _registry
from beebtools.commands._helpers import (
    BOLD, CYAN, GREY, RED, RESET,
    colour, formatLabel, useColour,
)
from beebtools.commands._registry import (
    CLI, argument, cli, imageArg, parseBootOption,
    sideArg, tracksArg, _formatWarning,
)
from beebtools.boot import BootOption
from beebtools.shared import BeebToolsWarning


# ---------------------------------------------------------------------------
# Subcommand registration
# ---------------------------------------------------------------------------

EXPECTED_COMMANDS = {
    "cat", "extract", "search", "create", "add", "delete", "build",
    "title", "boot", "disc", "attrib", "rename", "compact", "mkdir",
    "split", "merge",
}


class TestSubcommandRegistration:
    """The singleton ``cli`` exposes every shipped command."""

    def testAllExpectedCommandsRegistered(self) -> None:
        """Importing the commands package registers all 16 subcommands."""
        registered = set(cli.subparsers.choices.keys())
        assert registered == EXPECTED_COMMANDS

    def testEveryCommandHasHandler(self) -> None:
        """Each subparser has a ``func`` default set by @cli.command."""
        for name, sub in cli.subparsers.choices.items():
            assert sub.get_default("func") is not None, name


# ---------------------------------------------------------------------------
# argument / command decorators
# ---------------------------------------------------------------------------

class TestArgumentDecorator:

    def testStoresSpecOnFunctionAttribute(self) -> None:
        """@argument appends a (names, kwargs) tuple to func._cli_args."""

        @argument("--foo", help="foo")
        def handler(args):
            pass

        assert handler._cli_args == [(("--foo",), {"help": "foo"})]

    def testStacksMultipleArguments(self) -> None:
        """Stacked @argument decorators accumulate in decoration order."""

        @argument("--first")
        @argument("--second")
        def handler(args):
            pass

        # Decorators apply bottom-up, so 'second' is appended first.
        names = [spec[0] for spec in handler._cli_args]
        assert names == [("--second",), ("--first",)]


class TestCommandDecorator:

    def testRegistersUnderExplicitName(self) -> None:
        """@cli.command(name) uses the given name, not func.__name__."""
        test_cli = CLI(prog="t")

        @test_cli.command("foo", help="help text")
        def handler(args):
            pass

        assert "foo" in test_cli.subparsers.choices
        assert test_cli.subparsers.choices["foo"].get_default("func") is handler

    def testArgumentSourceOrderPreservedInHelp(self) -> None:
        """Source order top-to-bottom matches --help argument order."""
        test_cli = CLI(prog="t")

        @test_cli.command("foo")
        @argument("--alpha", help="a")
        @argument("--beta", help="b")
        @argument("--gamma", help="c")
        def handler(args):
            pass

        sub = test_cli.subparsers.choices["foo"]
        # First two actions are -h and any inherited; pick the option strings
        # we added and check their order.
        opts = [
            a.option_strings[0]
            for a in sub._actions
            if a.option_strings and a.option_strings[0].startswith("--")
            and a.option_strings[0] in {"--alpha", "--beta", "--gamma"}
        ]
        assert opts == ["--alpha", "--beta", "--gamma"]

    def testCommandReturnsFunctionUnchanged(self) -> None:
        """The decorator returns the original function so it stays callable."""
        test_cli = CLI(prog="t")

        @test_cli.command("foo")
        def handler(args):
            return "result"

        assert handler(None) == "result"


# ---------------------------------------------------------------------------
# Shared argument shortcuts
# ---------------------------------------------------------------------------

class TestSharedShortcuts:

    def testImageArgAddsPositional(self) -> None:
        test_cli = CLI(prog="t")

        @test_cli.command("foo")
        @imageArg()
        def handler(args):
            pass

        ns = test_cli.parser.parse_args(["foo", "disc.ssd"])
        assert ns.image == "disc.ssd"

    def testSideArgDefaultsToZero(self) -> None:
        test_cli = CLI(prog="t")

        @test_cli.command("foo")
        @sideArg()
        def handler(args):
            pass

        ns = test_cli.parser.parse_args(["foo"])
        assert ns.side == 0

    def testSideArgRejectsInvalidValues(self) -> None:
        test_cli = CLI(prog="t")

        @test_cli.command("foo")
        @sideArg()
        def handler(args):
            pass

        with pytest.raises(SystemExit):
            test_cli.parser.parse_args(["foo", "--side", "2"])

    def testTracksArgAcceptsOnly40Or80(self) -> None:
        test_cli = CLI(prog="t")

        @test_cli.command("foo")
        @tracksArg()
        def handler(args):
            pass

        ns = test_cli.parser.parse_args(["foo", "-t", "40"])
        assert ns.tracks == 40

        with pytest.raises(SystemExit):
            test_cli.parser.parse_args(["foo", "-t", "60"])


# ---------------------------------------------------------------------------
# Top-level flags: --version, --debug, no-subcommand
# ---------------------------------------------------------------------------

class TestTopLevelFlags:

    def testVersionFlagPrintsVersionAndExits(self, capsys) -> None:
        """--version prints '<prog> <version>' and exits cleanly."""
        with pytest.raises(SystemExit) as exc:
            cli.run(["--version"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert beebtools.__version__ in out
        assert "beebtools" in out

    def testShortVersionFlagWorks(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            cli.run(["-v"])
        assert exc.value.code == 0
        assert beebtools.__version__ in capsys.readouterr().out

    def testNoSubcommandPrintsHelp(self, capsys) -> None:
        """Bare invocation prints help and returns cleanly."""
        cli.run([])
        out = capsys.readouterr().out
        assert "usage:" in out
        assert "cat" in out and "extract" in out

    def testVersionAppearsInDescription(self) -> None:
        """The top-level description embeds the version string."""
        assert beebtools.__version__ in cli.parser.description

    def testDebugFlagIsHidden(self) -> None:
        """--debug is registered but suppressed from --help."""
        help_text = cli.parser.format_help()
        assert "--debug" not in help_text


# ---------------------------------------------------------------------------
# Error handling in CLI.run()
# ---------------------------------------------------------------------------

class TestErrorHandling:

    def testFriendlyErrorWrapsExceptions(self, capsys) -> None:
        """Without --debug a raised exception becomes 'Error: <msg>' + exit 1."""
        test_cli = CLI(prog="t")

        @test_cli.command("boom")
        def handler(args):
            raise RuntimeError("kaboom")

        with pytest.raises(SystemExit) as exc:
            test_cli.run(["boom"])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Error: kaboom" in err

    def testDebugFlagPropagatesException(self) -> None:
        """With --debug the original exception escapes for a full traceback."""
        test_cli = CLI(prog="t")

        @test_cli.command("boom")
        def handler(args):
            raise RuntimeError("kaboom")

        with pytest.raises(RuntimeError, match="kaboom"):
            test_cli.run(["--debug", "boom"])


# ---------------------------------------------------------------------------
# Warnings formatter
# ---------------------------------------------------------------------------

class TestWarningsFormatter:

    def testBeebToolsWarningFormattedPlainly(self) -> None:
        """BeebToolsWarning prints as 'Warning: <msg>' without file/line."""
        out = _formatWarning(
            "something happened", BeebToolsWarning, "ignored.py", 99,
        )
        assert out == "Warning: something happened\n"

    def testOtherWarningsUseDefaultFormat(self) -> None:
        """Non-beebtools warnings fall through to the stdlib formatter."""
        out = _formatWarning(
            "noisy", DeprecationWarning, "some_file.py", 42,
        )
        # The default formatter includes the file path and line number.
        assert "some_file.py" in out
        assert "42" in out

    def testRunInstallsBeebToolsFormatter(self, capsys) -> None:
        """CLI.run() swaps in the BeebToolsWarning formatter."""
        original = warnings.formatwarning
        try:
            test_cli = CLI(prog="t")

            @test_cli.command("warn")
            def handler(args):
                warnings.warn("hi", BeebToolsWarning)

            with warnings.catch_warnings(record=False):
                warnings.simplefilter("always")
                # Capture the formatted output via showwarning.
                captured = []
                warnings.showwarning = lambda msg, cat, fn, ln, file=None, line=None: \
                    captured.append(warnings.formatwarning(msg, cat, fn, ln, line))
                test_cli.run(["warn"])
            assert any("Warning: hi" in s for s in captured)
        finally:
            warnings.formatwarning = original


# ---------------------------------------------------------------------------
# parseBootOption
# ---------------------------------------------------------------------------

class TestParseBootOption:

    def testValidValueReturnsBootOption(self) -> None:
        assert parseBootOption("0") == BootOption.parse("0")

    def testInvalidValueRaisesArgparseError(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError):
            parseBootOption("not-a-boot-option")


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

class TestColourHelpers:

    def testColourWrapsWhenEnabled(self) -> None:
        out = colour("hi", CYAN, enabled=True)
        assert out == f"{CYAN}hi{RESET}"

    def testColourUnwrappedWhenDisabled(self) -> None:
        assert colour("hi", CYAN, enabled=False) == "hi"

    def testUseColourReflectsIsatty(self, monkeypatch) -> None:
        """useColour() mirrors sys.stdout.isatty()."""
        class FakeStdout:
            def isatty(self):
                return True

        monkeypatch.setattr(sys, "stdout", FakeStdout())
        assert useColour() is True

        class NotATty:
            def isatty(self):
                return False

        monkeypatch.setattr(sys, "stdout", NotATty())
        assert useColour() is False


# ---------------------------------------------------------------------------
# formatLabel
# ---------------------------------------------------------------------------

class TestFormatLabel:

    @pytest.mark.parametrize("path,tracks,size,expected", [
        ("disc.ssd", 80, 200_000, "80-track SSD"),
        ("disc.dsd", 40, 200_000, "40-track DSD"),
        ("disc.adf", 80, 163_840, "160K ADF"),
        ("disc.adl", 80, 655_360, "640K ADL"),
    ])
    def testKnownExtensions(self, path, tracks, size, expected) -> None:
        assert formatLabel(path, tracks, size) == expected

    def testUnknownExtensionReturnsExtension(self) -> None:
        assert formatLabel("disc.xyz", 80, 0) == ".xyz"


# ---------------------------------------------------------------------------
# Backwards-compatibility shim
# ---------------------------------------------------------------------------

class TestCliShim:
    """The legacy ``beebtools.cli`` module must keep re-exporting the old API."""

    def testReExportsAllCmdHandlers(self) -> None:
        expected = {
            "cmdCat", "cmdExtract", "cmdSearch", "cmdCreate", "cmdAdd",
            "cmdDelete", "cmdBuild", "cmdTitle", "cmdBoot", "cmdDisc",
            "cmdAttrib", "cmdRename", "cmdCompact", "cmdMkdir",
            "cmdSplit", "cmdMerge",
        }
        for name in expected:
            assert hasattr(cli_shim, name), f"missing: {name}"

    def testReExportsPrivateHelpers(self) -> None:
        for name in ("_parseBootOption", "_colour", "_loadResourceBundles"):
            assert hasattr(cli_shim, name), f"missing: {name}"

    def testMainIsTheCommandsMain(self) -> None:
        """The shim's main() is the same object as commands.main()."""
        from beebtools.commands import main as commands_main
        assert cli_shim.main is commands_main
