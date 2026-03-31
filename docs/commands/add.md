# add - Add a file to a disc image

```bash
beebtools add <image> <file> --name <D.NAME> [--load HEX] [--exec HEX] [--locked]
beebtools add <image> <file> --inf [--side 0|1]
```

File metadata can be provided either on the command line or read from a `.inf`
sidecar file (looked up as `<file>.inf`).

## Options

- `-n` / `--name` - DFS name (e.g. `T.MYPROG` or bare `MYPROG` for `$`)

- `--load` - load address in hex (default: 0)

- `--exec` - exec address in hex (default: 0)

- `--locked` - lock the file against deletion

- `--inf` - read metadata from a `.inf` sidecar file instead

- `--side` - disc side for DSD images (default: 0)
