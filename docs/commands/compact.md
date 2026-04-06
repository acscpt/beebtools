# compact - Defragment a DFS disc image

```bash
beebtools compact <image> [--side 0|1]
```

Defragments a DFS disc image by closing gaps between files. Files are packed
toward the highest sectors so all free space is contiguous below. The
catalogue is rewritten with updated start sectors and an incremented cycle
number.

Reports the number of sectors and bytes freed. If the disc is already fully
packed, reports that no compaction was needed.

Only DFS images (.ssd, .dsd) support compaction. ADFS images raise an error.

## Options

- `--side` - disc side for DFS DSD images (default: 0)

## Examples

```bash
# Compact a single-sided DFS image
beebtools compact mydisc.ssd

# Compact side 1 of a double-sided image
beebtools compact mydisc.dsd --side 1
```
