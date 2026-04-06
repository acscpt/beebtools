# boot - Read or set the disc boot option

```bash
beebtools boot <image> [OFF|LOAD|RUN|EXEC] [--side 0|1]
```

Reads or sets the boot option on an existing DFS or ADFS disc image.

With no argument, prints the current boot option to stdout. With a boot
option argument, updates the value and writes the image back to disc.

The boot option controls what happens when you shift-break the disc on a
real BBC Micro:

- **OFF** (0) - no action
- **LOAD** (1) - `*LOAD $.!BOOT`
- **RUN** (2) - `*RUN $.!BOOT`
- **EXEC** (3) - `*EXEC $.!BOOT`

Both names (OFF, LOAD, RUN, EXEC) and numbers (0-3) are accepted.

## Options

- `--side` - disc side for DFS DSD images (default: 0; ignored for ADFS)

## Examples

```bash
# Print the current boot option
beebtools boot mydisc.ssd

# Set boot to EXEC
beebtools boot mydisc.ssd EXEC

# Set boot to RUN on side 1 of a DSD image
beebtools boot mydisc.dsd RUN --side 1

# Set boot option on an ADFS image
beebtools boot game.adf LOAD
```
