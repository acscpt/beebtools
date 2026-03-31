# build - Build a disc image from files

```bash
beebtools build <dir> <output> [-t 40|80] [--title TITLE] [--boot OFF|LOAD|RUN|EXEC]
```

Assembles a disc image from a directory of files with `.inf` sidecars. The
source directory should have the same hierarchical layout produced by
`extract -a --inf`: one subdirectory per DFS directory character, with each
data file accompanied by a `.inf` sidecar. For DSD images, `side0/` and
`side1/` subdirectories are expected.

This enables a full round-trip workflow:

```bash
# Extract everything with metadata
beebtools extract original.ssd -a --inf -d working/

# Edit files as needed, then rebuild
beebtools build working/ modified.ssd --title "MODIFIED"
```

## Options

- `-t` / `--tracks` - 40 or 80 tracks (default: 80)

- `--title` - disc title (up to 12 characters)

- `--boot` - boot option: OFF, LOAD, RUN, or EXEC (numbers 0-3 also accepted)
