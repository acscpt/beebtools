# delete - Delete a file from a disc image

```bash
beebtools delete <image> <filename> [--side 0|1]
```

Deletes a file from a DFS or ADFS disc image. The format is detected from the
image file extension.

For DFS, the filename can be explicit (`$.BOOT`) or bare (`BOOT`, defaults to
`$`). For ADFS, use full hierarchical paths (e.g. `$.GAMES.ELITE`) or bare
names for root-level files.

Directories cannot be deleted with this command.

## Options

- `--side` - disc side for DFS DSD images (default: 0; ignored for ADFS)

## Examples

```bash
# Delete from a DFS image
beebtools delete mydisc.ssd T.MYPROG

# Delete from an ADFS image
beebtools delete mydisc.adf $.GAMES.ELITE

# Delete a root-level file (bare name)
beebtools delete mydisc.adf BOOT
```
