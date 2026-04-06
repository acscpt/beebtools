# attrib - Read or set file attributes

```bash
beebtools attrib <image> <filename> [--locked|--unlocked] [--load HEX] [--exec HEX] [--side 0|1]
```

Reads or sets the attributes of a single file on a DFS or ADFS disc image.

With no attribute flags, prints the current attributes to stdout: full path,
load address, exec address, length, and locked status.

With one or more attribute flags, updates the specified attributes and writes
the image back to disc. Attributes not specified are left unchanged.

## Options

- `--locked` - set the file's lock flag

- `--unlocked` - clear the file's lock flag

- `--load HEX` - set the load address (hex, no 0x prefix)

- `--exec HEX` - set the exec address (hex, no 0x prefix)

- `--side` - disc side for DFS DSD images (default: 0; ignored for ADFS)

`--locked` and `--unlocked` are mutually exclusive.

## Examples

```bash
# Print attributes of a file
beebtools attrib mydisc.ssd T.MYPROG

# Lock a file
beebtools attrib mydisc.ssd T.MYPROG --locked

# Unlock a file
beebtools attrib mydisc.ssd T.MYPROG --unlocked

# Change load and exec addresses
beebtools attrib mydisc.ssd T.MYPROG --load 1900 --exec 8023

# Lock and set addresses in one call
beebtools attrib mydisc.ssd T.MYPROG --locked --load 1900 --exec 8023

# Set attributes on an ADFS file
beebtools attrib game.adf $.GAMES.ELITE --locked
```
