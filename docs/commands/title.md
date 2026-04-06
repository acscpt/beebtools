# title - Read or set the disc title

```bash
beebtools title <image> [NEWTITLE] [--side 0|1]
```

Reads or sets the disc title on an existing DFS or ADFS disc image.

With no title argument, prints the current title to stdout. With a title
argument, updates the title and writes the image back to disc.

DFS titles are limited to 12 characters. ADFS titles are limited to 19
characters. Setting a title longer than the format allows raises an error.

## Options

- `--side` - disc side for DFS DSD images (default: 0; ignored for ADFS)

## Examples

```bash
# Print the current title
beebtools title mydisc.ssd

# Set the title
beebtools title mydisc.ssd "MY DISC"

# Set the title on side 1 of a double-sided DFS image
beebtools title mydisc.dsd "SIDE ONE" --side 1

# Read the ADFS title
beebtools title game.adf
```
