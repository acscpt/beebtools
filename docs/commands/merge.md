# merge - Merge two SSD images into a single DSD

```bash
beebtools merge <side0> <side1> <output> [--seq] [-f|--force]
```

Combines two single-sided DFS disc images (`.ssd`) into one double-sided
image (`.dsd`). The two source catalogues are preserved unchanged; the
result is a disc whose drive 0 and drive 2 surfaces match the inputs
byte-for-byte.

Only DFS images are supported. ADFS extensions are rejected on any
path with a clear error.

## Layout

By default the output is written as a standard interleaved DSD
(sectors alternate between side 0 and side 1 track-by-track, 5120
bytes per cylinder). Pass `--seq` to produce a sequential image
instead, where side 0 occupies the first half of the file followed
by side 1.

The same flag applies symmetrically to the `split` command, so a
round-trip `split` followed by `merge` preserves byte-for-byte content
regardless of which layout was used, provided the same flag is passed
to both operations.

The two source images do not need to be the same capacity, but both
must be valid SSDs. The output capacity is determined by the larger
of the two sides.

## Options

- `--seq` - write sequential layout (side 0 followed by side 1) rather
  than interleaved track-by-track

- `-f` / `--force` - overwrite an existing output file

## Examples

```bash
# Merge two SSDs into a standard interleaved DSD
beebtools merge front.ssd back.ssd combined.dsd

# Write sequential layout instead of interleaved
beebtools merge front.ssd back.ssd combined.dsd --seq

# Overwrite an existing output file
beebtools merge front.ssd back.ssd combined.dsd -f
```

## See also

- [split](split.md) - the inverse operation
