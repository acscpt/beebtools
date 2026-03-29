# SPDX-FileCopyrightText: 2026 beebtools contributors
# SPDX-License-Identifier: MIT

"""Integration tests for the DFS disc image reader.

Tests are parametrized over every .dsd file found in tests/resources/discs/.
That directory is git-ignored and must be populated locally before the
tests will run.  The suite is skipped automatically when no images are present.

Assertions target invariants that must hold for ANY valid DFS disc image,
so adding new disc images to the resources directory extends test coverage
with no code changes.
"""

import os
import glob
import pytest

from beebtools import openDiscImage, isBasic, looksLikeText, detokenize

# Discover all disc images in the resources directory.
DISCS_DIR = os.path.join(os.path.dirname(__file__), "resources", "discs")
ALL_DSDS = sorted(glob.glob(os.path.join(DISCS_DIR, "*.dsd")))

# Readable test IDs: just the filename, not the full path.
disc_ids = [os.path.basename(p) for p in ALL_DSDS]

# Skip the entire module when no discs are present (e.g. in CI).
pytestmark = pytest.mark.skipif(
    len(ALL_DSDS) == 0,
    reason="No disc images found in tests/resources/discs/",
)


# ---------------------------------------------------------------------------
# Parametrized helpers
# ---------------------------------------------------------------------------

def allSides(path):
    """Return (path, side_index, disc, entries) for every side of a disc."""
    sides = openDiscImage(path)
    result = []
    for i, disc in enumerate(sides):
        _, entries = disc.readCatalogue()
        result.append((disc, i, entries))
    return result


# Build a flat parametrize list: one entry per (disc_path, side_index).
disc_side_params = []
disc_side_ids = []
for path in ALL_DSDS:
    name = os.path.basename(path)
    for disc, side_idx, entries in allSides(path):
        disc_side_params.append((disc, entries))
        disc_side_ids.append(f"{name}:side{side_idx}")


# ---------------------------------------------------------------------------
# Catalogue structure - invariants for any valid DFS disc side
# ---------------------------------------------------------------------------

class TestCatalogueStructure:

    @pytest.mark.parametrize("path", ALL_DSDS, ids=disc_ids)
    def testDsdOpensTwoSides(self, path):
        assert len(openDiscImage(path)) == 2

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testEntryCountIsReasonable(self, disc, entries):
        # Standard DFS supports at most 31 files per side.
        assert 0 <= len(entries) <= 31

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testEveryEntryHasRequiredKeys(self, disc, entries):
        required = {"name", "dir", "load", "exec", "length", "start_sector", "locked"}
        for entry in entries:
            assert required.issubset(entry.keys())

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testEntryNamesAreNonEmpty(self, disc, entries):
        for entry in entries:
            assert len(entry["name"]) > 0

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testEntryLengthsArePositive(self, disc, entries):
        for entry in entries:
            assert entry["length"] >= 0


# ---------------------------------------------------------------------------
# File extraction - invariants for any file on any disc
# ---------------------------------------------------------------------------

class TestFileExtraction:

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testExtractedLengthMatchesCatalogue(self, disc, entries):
        for entry in entries:
            data = disc.readFile(entry)
            assert len(data) == entry["length"], (
                f"{entry['dir']}.{entry['name']}: "
                f"expected {entry['length']} bytes, got {len(data)}"
            )

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testBasicFilesStartWith0x0d(self, disc, entries):
        for entry in entries:
            if isBasic(entry):
                data = disc.readFile(entry)
                if len(data) > 0:
                    assert data[0] == 0x0D, (
                        f"{entry['dir']}.{entry['name']} does not start with 0x0D"
                    )

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testBasicFilesDetokenizeWithoutError(self, disc, entries):
        for entry in entries:
            if isBasic(entry):
                data = disc.readFile(entry)
                if looksLikeText(data):
                    lines = detokenize(data)
                    assert isinstance(lines, list)

    @pytest.mark.parametrize("disc,entries", disc_side_params, ids=disc_side_ids)
    def testDetokenizedLinesHaveLineNumbers(self, disc, entries):
        for entry in entries:
            if isBasic(entry):
                data = disc.readFile(entry)
                if looksLikeText(data):
                    for line in detokenize(data):
                        assert line[:5].strip().isdigit(), (
                            f"{entry['dir']}.{entry['name']}: bad line {repr(line)}"
                        )


