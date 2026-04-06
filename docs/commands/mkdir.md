# mkdir - Create a subdirectory on an ADFS disc image

```bash
beebtools mkdir <image> <path> [--side 0|1]
```

Creates a new subdirectory on an ADFS disc image. The parent directory must
already exist. The new directory is allocated 5 sectors and initialised with
the standard Hugo markers and an empty entry list.

Only ADFS images (.adf, .adl) support subdirectories. DFS images raise an
error because DFS uses implicit single-character directory prefixes instead
of real subdirectories.

## Options

- `--side` - disc side (default: 0; ignored for ADFS)

## Examples

```bash
# Create a top-level directory
beebtools mkdir game.adf $.GAMES

# Create a nested directory (parent must exist)
beebtools mkdir game.adf $.GAMES.ARCADE
```
