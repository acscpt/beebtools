# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Scoped validation context for BeebTools.

Some validation rules - notably the DFS filename spec - are stricter than
the behaviour of real Acorn DFS/ADFS ROMs. Real ROMs accept a wide range
of "spec-forbidden" but printable bytes in filenames (for example '.', '#',
'*' in Acorn DFS) and our validators should do the same by default so we
can roundtrip real-world discs.

A small set of users still want spec-compliance checking on demand: a
linter that flags questionable filenames when authoring a new disc, a
test that asserts a library is spec-clean, or a CLI build invoked with
--strict. Rather than thread a `strict` parameter through every
`addFile`, `renameFile`, and `buildImage` call site - which would ripple
into the shared `DiscSide` / `DiscImage` ABCs - BeebTools uses a scoped
context variable. Callers opt in via the `strictMode()` context manager
and every validator in the stack consults `isStrict()` when deciding
whether to enforce spec-only rules.

Usage
-----

    from beebtools import strictMode, buildImage

    # Default: ROM-faithful, silent accept of spec-forbidden filenames.
    buildImage("src/", "out.ssd")

    # Opt-in: enforce the DFS spec, raise on '.', '#', '*', ':', '"', ' '.
    with strictMode():
        buildImage("src/", "out.ssd")

The context variable is async-safe and thread-safe, and the context
manager resets cleanly on exit so test fixtures do not leak strict mode
between cases. See `tests/test_validation.py` for the guarantees.
"""

import contextlib
import contextvars
from typing import Iterator


# Module-level ContextVar. Default is False (ROM-faithful behaviour).
# Callers should not mutate this directly - use the strictMode() context
# manager, which takes a token and resets it on exit.
_strictMode: contextvars.ContextVar = contextvars.ContextVar(
    "beebtools_strict_mode", default=False
)


def isStrict() -> bool:
    """Return True if the current context has strict validation enabled.

    Validators in dfs.py and adfs.py call this to decide whether to
    enforce spec-only rules (for example, rejecting '.', '#', '*' in
    DFS filenames). In the default non-strict context this returns
    False and validators allow any printable byte.
    """

    return _strictMode.get()


@contextlib.contextmanager
def strictMode(enabled: bool = True) -> Iterator[None]:
    """Enable or disable strict validation for the enclosed block.

    Args:
        enabled: True to turn strict mode on, False to force it off
                 even inside an outer strict block.

    Yields:
        None. The caller runs its code under the requested strictness
        setting and the previous value is restored on exit.

    Example:
        with strictMode():
            buildImage("src/", "out.ssd")   # raises on spec violations

        with strictMode(False):
            addFile("disc.ssd", my_spec)    # forces ROM-faithful mode
                                             # even under an outer strict
    """

    token = _strictMode.set(enabled)
    try:
        yield
    finally:
        _strictMode.reset(token)
