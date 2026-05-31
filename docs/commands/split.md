# split - Split a DSD image into two SSD halves

```bash
beebtools split <source> [outputs...] [--seq] [-f|--force]
```

Splits a double-sided DFS disc image (`.dsd`) into two single-sided images
(`.ssd`), one per disc surface. Each output contains the catalogue and files
from one physical side of the original disc and can be read or written
independently.

Only DFS images are supported. ADFS `.adl` images are a single filesystem
spanning two surfaces and cannot be split into independent discs; an
`.adl` source is rejected with a clear error.

## Output naming

The number of positional output arguments controls how the two output
filenames are derived:

- **0 output names** - derives `<source>-side0.ssd` and `<source>-side1.ssd`
  from the source path with its extension stripped.

- **1 output name** - treats the argument as a stem and derives
  `<stem>-side0.ssd` and `<stem>-side1.ssd`.

- **2 output names** - uses both verbatim as the side-0 and side-1 paths.

Existing output files are not overwritten unless `-f`/`--force` is given.

## Layout

By default the source is treated as a standard interleaved DSD (sectors
alternate between side 0 and side 1 track-by-track, 5120 bytes per
cylinder). Pass `--seq` if the source is a sequential image instead,
where side 0 occupies the first half of the file followed by side 1.

The same flag applies symmetrically to the `merge` command, so a
round-trip `split` followed by `merge` preserves byte-for-byte content
regardless of which layout was used, provided the same flag is passed
to both operations.

## Options

- `--seq` - treat source as sequential (side 0 followed by side 1)
  rather than interleaved track-by-track

- `-f` / `--force` - overwrite existing output files

## Examples

```bash
# Split with auto-derived names: writes mydisc-side0.ssd and mydisc-side1.ssd
beebtools split mydisc.dsd

# Split with a custom stem: writes drive0-side0.ssd and drive0-side1.ssd
beebtools split mydisc.dsd drive0

# Split with explicit output paths
beebtools split mydisc.dsd front.ssd back.ssd

# Split a sequential DSD instead of an interleaved one
beebtools split mydisc.dsd --seq

# Overwrite existing output files
beebtools split mydisc.dsd -f
```

## See also

- [merge](merge.md) - the inverse operation
