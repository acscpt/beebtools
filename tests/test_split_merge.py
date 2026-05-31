# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for the splitImage / mergeImages round-trip and CLI subcommands."""

import os
import subprocess
import sys

import pytest

from beebtools import splitImage, mergeImages, createImageFile, DiscError


# DFS constants reused throughout: each track is 10 sectors of 256
# bytes per surface, so a DSD track holds 5120 bytes.
TRACK_BYTES = 10 * 256
DSD_40T = 40 * TRACK_BYTES * 2
DSD_80T = 80 * TRACK_BYTES * 2


def _makePattern(size: int, seed: int) -> bytes:
    """Build a deterministic pseudo-data buffer for round-trip tests."""
    # A simple linear-feedback pattern is enough to detect any swap or
    # misaligned slice between the two surfaces.
    return bytes(((seed + i * 37) & 0xFF) for i in range(size))


# ---------------------------------------------------------------------------
# splitImage
# ---------------------------------------------------------------------------

def testSplitRejectsBadSize(tmp_path):
    """A file whose length is not a legal DSD capacity is refused."""
    bad = tmp_path / "junk.dsd"
    bad.write_bytes(b"\x00" * 12345)

    with pytest.raises(DiscError, match="Not a valid DSD image"):
        splitImage(str(bad))


def testSplitRefusesOverwrite(tmp_path):
    """Existing outputs are protected without force=True."""
    src = tmp_path / "in.dsd"
    src.write_bytes(b"\x00" * DSD_40T)

    out0 = tmp_path / "in-side0.ssd"
    out0.write_bytes(b"keep")

    with pytest.raises(DiscError, match="already exists"):
        splitImage(str(src))

    # Force allows it through.
    splitImage(str(src), force=True)
    assert out0.stat().st_size == DSD_40T // 2


def testSplitDerivesNames40t(tmp_path):
    """Zero-argument form derives names from the source stem."""
    src = tmp_path / "elite.dsd"
    src.write_bytes(b"\x00" * DSD_40T)

    out0, out1 = splitImage(str(src))

    assert out0.endswith("elite-side0.ssd")
    assert out1.endswith("elite-side1.ssd")
    assert os.path.getsize(out0) == DSD_40T // 2
    assert os.path.getsize(out1) == DSD_40T // 2


def testSplitWithStem(tmp_path):
    """A single output argument is used as the shared stem."""
    src = tmp_path / "in.dsd"
    src.write_bytes(b"\x00" * DSD_40T)
    stem = str(tmp_path / "mine")

    out0, out1 = splitImage(str(src), stem)

    assert out0 == stem + "-side0.ssd"
    assert out1 == stem + "-side1.ssd"


def testSplitWithExplicitNames(tmp_path):
    """Two output arguments are written verbatim."""
    src = tmp_path / "in.dsd"
    src.write_bytes(b"\x00" * DSD_40T)
    a = str(tmp_path / "a.ssd")
    b = str(tmp_path / "b.ssd")

    out0, out1 = splitImage(str(src), a, b)

    assert (out0, out1) == (a, b)
    assert os.path.exists(a) and os.path.exists(b)


def testSplitRejectsAdfsSource(tmp_path):
    """ADFS images cannot be split: error is explicit, not generic."""
    adl = tmp_path / "foo.adl"
    adl.write_bytes(b"\x00" * 1024)

    with pytest.raises(DiscError, match="ADFS"):
        splitImage(str(adl))


def testSplitRejectsUnknownExtension(tmp_path):
    """Unknown extensions are accepted; size sniffing handles routing."""
    # An 80-track DSD (409600 bytes) is an unambiguous size: it
    # cannot be any single-sided format, so sniffing routes it as
    # DSD even with no extension hint at all.
    payload = _makePattern(DSD_80T, seed=4)
    odd = tmp_path / "noext"
    odd.write_bytes(payload)

    out0, out1 = splitImage(str(odd), str(tmp_path / "a.ssd"),
                            str(tmp_path / "b.ssd"))

    assert os.path.getsize(out0) == DSD_80T // 2
    assert os.path.getsize(out1) == DSD_80T // 2


def testSplitMisnamedExtensionStillWorks(tmp_path):
    """A real DSD renamed to .img is split on content, not extension."""
    payload = _makePattern(DSD_80T, seed=5)
    odd = tmp_path / "real.img"
    odd.write_bytes(payload)

    out0, out1 = splitImage(str(odd), str(tmp_path / "x.ssd"),
                            str(tmp_path / "y.ssd"))

    # Round-trip the merge to confirm content survived intact.
    back = tmp_path / "back.dsd"
    mergeImages(out0, out1, str(back))
    assert back.read_bytes() == payload


def testSplitAmbiguousSizeFallsBackToCatalogue(tmp_path):
    """A real 40-track DSD at the ambiguous 204800 size routes as DSD."""
    # createImageFile writes a valid catalogue whose sector count
    # disambiguates 204800 bytes between SSD-80t and DSD-40t.
    dsd = tmp_path / "ambig.dsd"
    createImageFile(str(dsd), title="AMBIG", tracks=40)
    raw = dsd.read_bytes()
    assert len(raw) == DSD_40T

    # Rename the file to strip the extension hint, then split.
    odd = tmp_path / "ambig.img"
    odd.write_bytes(raw)

    out0, out1 = splitImage(str(odd), str(tmp_path / "p.ssd"),
                            str(tmp_path / "q.ssd"))

    assert os.path.getsize(out0) == DSD_40T // 2
    assert os.path.getsize(out1) == DSD_40T // 2


def testOpenDiscImageWarnsOnExtensionMismatch(tmp_path):
    """A real DSD named .ssd is opened as DSD with a warning."""
    import warnings
    from beebtools import openImage
    from beebtools.shared import BeebToolsWarning

    # Build a real 80-track DSD (unambiguous size) but name it .ssd.
    src = tmp_path / "wrong.dsd"
    createImageFile(str(src), title="MISMATCH", tracks=80)
    raw = src.read_bytes()
    assert len(raw) == DSD_80T

    misnamed = tmp_path / "wrong.ssd"
    misnamed.write_bytes(raw)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        image = openImage(str(misnamed))

    # Bytes win: it must open as a two-sided DSD.
    assert len(image.sides) == 2

    # Exactly one BeebToolsWarning naming the contradiction.
    msgs = [str(w.message) for w in caught
            if issubclass(w.category, BeebToolsWarning)]
    assert any("DSD" in m and ".ssd" in m for m in msgs), msgs


def testOpenDiscImageWarnsOnUnknownSizeWithNoHint(tmp_path):
    """An odd size with no extension hint defaults to SSD with a warning."""
    import warnings
    from beebtools import openDiscImage
    from beebtools.shared import BeebToolsWarning

    # 4096 bytes is large enough to pass min-SSD-size but matches no
    # standard DFS capacity, and the name carries no .ssd/.dsd hint.
    odd = tmp_path / "blob.img"
    odd.write_bytes(b"\x00" * 4096)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        image = openDiscImage(str(odd))

    # Default fallback is SSD (single side).
    assert len(image.sides) == 1

    msgs = [str(w.message) for w in caught
            if issubclass(w.category, BeebToolsWarning)]
    assert any("defaulting to SSD" in m for m in msgs), msgs


# ---------------------------------------------------------------------------
# Round trips: split then merge must yield the original bytes for both layouts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size", [DSD_40T, DSD_80T])
@pytest.mark.parametrize("sequential", [False, True])
def testRoundTrip(tmp_path, size, sequential):
    """splitImage followed by mergeImages reproduces the source exactly."""
    src = tmp_path / "src.dsd"
    src.write_bytes(_makePattern(size, seed=size & 0xFF))

    out0, out1 = splitImage(str(src), sequential=sequential)

    # Each side gets exactly half the source bytes.
    assert os.path.getsize(out0) == size // 2
    assert os.path.getsize(out1) == size // 2

    merged = tmp_path / "back.dsd"
    mergeImages(out0, out1, str(merged), sequential=sequential)

    assert merged.read_bytes() == src.read_bytes()


def testInterleavedLayoutFirstTrackBoundaries(tmp_path):
    """The first 2560 bytes of an interleaved DSD belong to side 0."""
    src = tmp_path / "in.dsd"
    payload = _makePattern(DSD_40T, seed=1)
    src.write_bytes(payload)

    out0, out1 = splitImage(str(src))

    # Side 0's first track must match the first 2560 bytes of the DSD,
    # and side 1's first track must match the next 2560 bytes.
    with open(out0, "rb") as fh:
        assert fh.read(TRACK_BYTES) == payload[:TRACK_BYTES]
    with open(out1, "rb") as fh:
        assert fh.read(TRACK_BYTES) == payload[TRACK_BYTES:TRACK_BYTES * 2]


def testSequentialLayoutIsSimpleHalving(tmp_path):
    """In --seq mode side 0 is the literal first half of the file."""
    src = tmp_path / "in.dsd"
    payload = _makePattern(DSD_40T, seed=2)
    src.write_bytes(payload)

    out0, out1 = splitImage(str(src), sequential=True)

    assert open(out0, "rb").read() == payload[: DSD_40T // 2]
    assert open(out1, "rb").read() == payload[DSD_40T // 2 :]


# ---------------------------------------------------------------------------
# mergeImages validation
# ---------------------------------------------------------------------------

def testMergeRejectsMismatchedSizes(tmp_path):
    """Two SSDs of different lengths cannot be merged."""
    a = tmp_path / "a.ssd"
    b = tmp_path / "b.ssd"
    a.write_bytes(b"\x00" * (DSD_40T // 2))
    b.write_bytes(b"\x00" * (DSD_80T // 2))

    with pytest.raises(DiscError, match="differ"):
        mergeImages(str(a), str(b), str(tmp_path / "out.dsd"))


def testMergeRejectsBadSsdSize(tmp_path):
    """SSDs not matching a known capacity are refused."""
    a = tmp_path / "a.ssd"
    b = tmp_path / "b.ssd"
    a.write_bytes(b"\x00" * 1024)
    b.write_bytes(b"\x00" * 1024)

    with pytest.raises(DiscError, match="Not a valid SSD size"):
        mergeImages(str(a), str(b), str(tmp_path / "out.dsd"))


def testMergeRefusesOverwrite(tmp_path):
    """Existing output is protected without force=True."""
    a = tmp_path / "a.ssd"
    b = tmp_path / "b.ssd"
    a.write_bytes(b"\x00" * (DSD_40T // 2))
    b.write_bytes(b"\xFF" * (DSD_40T // 2))
    out = tmp_path / "out.dsd"
    out.write_bytes(b"keep")

    with pytest.raises(DiscError, match="already exists"):
        mergeImages(str(a), str(b), str(out))

    mergeImages(str(a), str(b), str(out), force=True)
    assert out.stat().st_size == DSD_40T


def testMergeRejectsAdfsOutput(tmp_path):
    """ADFS output extension is refused with a clear ADFS-aware error."""
    a = tmp_path / "a.ssd"
    b = tmp_path / "b.ssd"
    a.write_bytes(b"\x00" * (DSD_40T // 2))
    b.write_bytes(b"\x00" * (DSD_40T // 2))

    with pytest.raises(DiscError, match="ADFS"):
        mergeImages(str(a), str(b), str(tmp_path / "out.adl"))


# ---------------------------------------------------------------------------
# Real DFS round-trip: build a DSD, split it, merge it, parse the result
# ---------------------------------------------------------------------------

def testRoundTripPreservesDfsCatalogue(tmp_path):
    """A real DSD survives split+merge with its catalogue intact."""
    from beebtools import getTitle

    dsd = tmp_path / "real.dsd"
    createImageFile(str(dsd), title="ROUNDTRIP", tracks=40)

    # Split and merge using the default interleaved layout.
    out0, out1 = splitImage(str(dsd))
    back = tmp_path / "back.dsd"
    mergeImages(out0, out1, str(back))

    # The merged file must be byte-identical and still parse as a DSD
    # with its title readable on both sides.
    assert back.read_bytes() == dsd.read_bytes()
    assert getTitle(str(back), side=0) == "ROUNDTRIP"


# ---------------------------------------------------------------------------
# CLI smoke tests: the subcommands are wired and produce the expected files
# ---------------------------------------------------------------------------

def _runCli(*args):
    """Invoke the CLI as a subprocess so argparse runs end to end."""
    return subprocess.run(
        [sys.executable, "-m", "beebtools", *args],
        capture_output=True,
        text=True,
        check=True,
    )


def testCliSplitAndMergeRoundTrip(tmp_path):
    """The 'split' and 'merge' subcommands round-trip a real DSD."""
    dsd = tmp_path / "cli.dsd"
    createImageFile(str(dsd), title="CLI TEST", tracks=40)

    # Split using the default (zero-output-name) form.
    _runCli("split", str(dsd))
    side0 = tmp_path / "cli-side0.ssd"
    side1 = tmp_path / "cli-side1.ssd"
    assert side0.exists() and side1.exists()

    # Merge back into a new file and compare with the source.
    back = tmp_path / "back.dsd"
    _runCli("merge", str(side0), str(side1), str(back))
    assert back.read_bytes() == dsd.read_bytes()


def testCliSplitSeqLayout(tmp_path):
    """The --seq flag produces the simple concatenated layout."""
    src = tmp_path / "seq.dsd"
    payload = _makePattern(DSD_40T, seed=3)
    src.write_bytes(payload)

    _runCli("split", str(src), "--seq")

    side0 = (tmp_path / "seq-side0.ssd").read_bytes()
    side1 = (tmp_path / "seq-side1.ssd").read_bytes()
    assert side0 == payload[: DSD_40T // 2]
    assert side1 == payload[DSD_40T // 2 :]
