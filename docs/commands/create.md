# create - Create a blank disc image

```bash
beebtools create <output> [-t 40|80] [--title TITLE] [--boot OFF|LOAD|RUN|EXEC]
```

Creates a blank formatted disc image. The format is determined by the output
file extension:

- `.ssd` - DFS single-sided
- `.dsd` - DFS double-sided interleaved
- `.adf` - ADFS single-sided (40-track = 160K, 80-track = 320K)
- `.adl` - ADFS double-sided (640K)

## Options

- `-t` / `--tracks` - 40 or 80 tracks (default: 80). For ADFS: 40-track
  `.adf` = ADFS-S (160K), 80-track `.adf` = ADFS-M (320K), `.adl` = ADFS-L
  (640K, always 80 tracks).

- `--title` - disc title

- `--boot` - boot option: OFF, LOAD, RUN, or EXEC (numbers 0-3 also accepted)

## Examples

```bash
# Create a blank 80-track DFS image
beebtools create blank.ssd

# Create a 320K ADFS image with a title
beebtools create mydisc.adf --title "PROGRAMS"

# Create a 160K ADFS image (40-track)
beebtools create small.adf -t 40

# Create a 640K ADFS image
beebtools create big.adl
```
