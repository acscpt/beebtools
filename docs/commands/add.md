# add - Add a file to a disc image

```bash
beebtools add <image> <file> --name <D.NAME> [--basic] [--load HEX] [--exec HEX] [--locked]
beebtools add <image> <file> --inf [--side 0|1]
```

File metadata can be provided either on the command line or read from a `.inf`
sidecar file (looked up as `<file>.inf`).

## Options

- `-n` / `--name` - DFS name (e.g. `T.MYPROG` or bare `MYPROG` for `$`)

- `--basic` - set BBC BASIC defaults (load=0x1900, exec=0x8023). If the input
  file is plain text (e.g. a `.bas` file from `extract`), it is automatically
  retokenized to BBC BASIC II binary before being added to the disc image. If
  `--load` or `--exec` is given alongside `--basic`, the explicit flag overrides
  that address and a note is printed showing the override. The other address keeps
  the BASIC default. Ignored with a warning when `--inf` is used.

- `--load` - load address in hex (default: 0, overrides `--basic`)

- `--exec` - exec address in hex (default: 0, overrides `--basic`)

- `--locked` - lock the file against deletion

- `--inf` - read metadata from a `.inf` sidecar file instead

- `--side` - disc side for DSD images (default: 0)

## Examples

Add a BBC BASIC program with standard addresses:
```bash
beebtools add mydisc.ssd myprog.bas -n T.MYPROG --basic
```

Add a BASIC program with a non-standard load address:
```bash
beebtools add mydisc.ssd myprog.bas -n T.MYPROG --basic --load E00
```

Add a binary file with explicit addresses:
```bash
beebtools add mydisc.ssd loader.bin -n $.LOADER --load 1900 --exec 1900
```

Add a file using a `.inf` sidecar for metadata:
```bash
beebtools add mydisc.ssd loader.bin --inf
```
