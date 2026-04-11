# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Unit tests for the scoped validation context.

Exercises the ContextVar-based strictMode() helper in isolation, without
depending on DFS or ADFS validators. These tests pin down the save/restore
semantics that the rest of the codebase relies on: nesting, cleanup on
exception, independence from unrelated context managers, and clean
default state between tests.
"""

import pytest

from beebtools import isStrict, strictMode


# ---------------------------------------------------------------------------
# Autouse fixture: the ContextVar must be in its default state (False) at
# the start of every test and at teardown. If a test leaks strict mode,
# this fixture will flag it immediately rather than letting it contaminate
# the next test.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def assertCleanContext():
    """Assert strict mode is False before and after every test."""
    assert isStrict() is False, (
        "Strict mode leaked into test setup - a previous test failed to "
        "reset the ContextVar"
    )

    yield

    assert isStrict() is False, (
        "Strict mode leaked out of test - a strictMode() context manager "
        "was not exited cleanly"
    )


# ---------------------------------------------------------------------------
# Basic semantics
# ---------------------------------------------------------------------------

class TestStrictModeBasic:
    """Default state and simple enter/exit behaviour."""

    def testDefaultIsFalse(self):
        """Before any strictMode() call the context reads False."""
        assert isStrict() is False

    def testStrictModeTrueInsideBlock(self):
        """Inside strictMode() the context reads True."""
        with strictMode():
            assert isStrict() is True

    def testStrictModeExplicitTrueInsideBlock(self):
        """strictMode(True) is equivalent to strictMode() with no arg."""
        with strictMode(True):
            assert isStrict() is True

    def testStrictModeFalseInsideBlock(self):
        """strictMode(False) forces the context to False."""
        with strictMode(False):
            assert isStrict() is False

    def testResetsAfterBlock(self):
        """After strictMode() exits the context returns to its prior value."""
        with strictMode():
            assert isStrict() is True

        assert isStrict() is False


# ---------------------------------------------------------------------------
# Nesting and composition
# ---------------------------------------------------------------------------

class TestStrictModeNesting:
    """Nested strictMode() blocks stack and unwind via ContextVar tokens."""

    def testNestedTrueInsideTrue(self):
        """A nested strictMode(True) inside strictMode(True) still reads True."""
        with strictMode(True):
            assert isStrict() is True

            with strictMode(True):
                assert isStrict() is True

            assert isStrict() is True

    def testNestedFalseInsideTrue(self):
        """A nested strictMode(False) inside strictMode(True) reads False inside, then True again on exit."""
        with strictMode(True):
            assert isStrict() is True

            with strictMode(False):
                assert isStrict() is False

            assert isStrict() is True

    def testNestedTrueInsideFalse(self):
        """A nested strictMode(True) inside strictMode(False) reads True inside, then False again on exit."""
        with strictMode(False):
            assert isStrict() is False

            with strictMode(True):
                assert isStrict() is True

            assert isStrict() is False

    def testDeeplyNestedLifo(self):
        """Deeply nested blocks unwind in strict LIFO order."""
        with strictMode(True):         # True
            with strictMode(False):    # False
                with strictMode(True): # True
                    assert isStrict() is True

                assert isStrict() is False

            assert isStrict() is True


# ---------------------------------------------------------------------------
# Exception safety
# ---------------------------------------------------------------------------

class TestStrictModeExceptionSafety:
    """The context is restored even when the wrapped code raises."""

    def testResetsAfterExceptionInside(self):
        """An exception raised inside strictMode() still resets the context on exit."""
        with pytest.raises(RuntimeError):
            with strictMode():
                assert isStrict() is True
                raise RuntimeError("boom")

        # Must be back to default even though the block exited abnormally.
        assert isStrict() is False

    def testResetsAfterNestedException(self):
        """A nested exception unwinds the inner block to the outer value."""
        with strictMode(True):
            with pytest.raises(RuntimeError):
                with strictMode(False):
                    assert isStrict() is False
                    raise RuntimeError("boom")

            # Outer strict block is still active.
            assert isStrict() is True

        assert isStrict() is False


# ---------------------------------------------------------------------------
# Independence from other context managers
# ---------------------------------------------------------------------------

class TestStrictModeIndependence:
    """Unrelated context managers must not affect strict mode."""

    def testUnrelatedContextDoesNotReset(self, tmp_path):
        """Opening a file inside strictMode() must not clear the strict flag."""
        test_file = tmp_path / "marker.txt"
        test_file.write_text("hello")

        with strictMode():
            assert isStrict() is True

            with open(test_file, "r") as handle:
                # File I/O lives in a completely different slot; strict mode
                # must remain True here.
                assert isStrict() is True
                assert handle.read() == "hello"

            # Still True after the file handle closes.
            assert isStrict() is True

    def testPytestRaisesDoesNotReset(self):
        """Entering a pytest.raises() context does not affect strict mode."""
        with strictMode():
            with pytest.raises(ValueError):
                assert isStrict() is True
                raise ValueError("unused")

            assert isStrict() is True
