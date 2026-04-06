# disc - Print disc summary or set disc properties

```bash
beebtools disc <image> [--title TITLE] [--boot OFF|LOAD|RUN|EXEC] [--side 0|1]
```

With no flags, prints a summary of the disc - title, boot option, track
count, and free space.

With `--title` and/or `--boot` flags, sets the specified properties and
writes the image back to disc. Both flags can be combined in a single call.

## Options

- `--title` - set the disc title

- `--boot` - set the boot option: OFF, LOAD, RUN, EXEC (or 0-3)

- `--side` - disc side for DFS DSD images (default: 0; ignored for ADFS)

## Examples

```bash
# Print disc summary
beebtools disc mydisc.ssd

# Set the title
beebtools disc mydisc.ssd --title "MY DISC"

# Set the boot option
beebtools disc mydisc.ssd --boot EXEC

# Set both title and boot in one call
beebtools disc mydisc.ssd --title "BOOTABLE" --boot RUN

# Print summary for an ADFS image
beebtools disc game.adf
```
