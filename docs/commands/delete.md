# delete - Delete a file from a disc image

```bash
beebtools delete <image> <filename> [--side 0|1]
```

The filename can be explicit (`$.BOOT`) or bare (`BOOT`, defaults to `$`).

## Options

- `--side` - disc side for DSD images (default: 0)
