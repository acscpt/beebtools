# add - Add a file to a disc image

```bash
beebtools add <image> <file> --name <NAME> [--basic] [--load HEX] [--exec HEX] [--locked]
beebtools add <image> <file> --inf [--side 0|1]
```

Adds a file to a DFS or ADFS disc image. The format is detected from the image
file extension. File metadata can be provided either on the command line or read
from a `.inf` sidecar file (looked up as `<file>.inf`).

For ADFS images, use full hierarchical paths (e.g. `$.GAMES.ELITE`). Bare names
without a `$.` prefix are added to the root directory. Parent directories must
already exist - use `beebtools build` or the library `mkdir()` to create them.

## Options

- `-n` / `--name` - file name. DFS: `T.MYPROG` or bare `MYPROG` for `$`.
  ADFS: `$.DIR.FILE` or bare `FILE` for root.

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

- `--side` - disc side for DFS DSD images (default: 0; ignored for ADFS)

## Examples

Add a BBC BASIC program to a DFS image:
```bash
beebtools add mydisc.ssd myprog.bas -n T.MYPROG --basic
```

Add a file to an ADFS image:
```bash
beebtools add mydisc.adf game.bin -n $.GAMES.ELITE --load 1900 --exec 1900
```

Add a file to the ADFS root directory (bare name):
```bash
beebtools add mydisc.adf boot.bin -n BOOT --load 0 --exec 0
```

Add a file using a `.inf` sidecar for metadata:
```bash
beebtools add mydisc.ssd loader.bin --inf
```
