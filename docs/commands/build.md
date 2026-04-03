# build - Build a disc image from files

```bash
beebtools build <dir> <output> [-t 40|80] [--title TITLE] [--boot OFF|LOAD|RUN|EXEC]
```

Assembles a disc image from a directory of files with `.inf` sidecars. The
output format is determined by the file extension (`.ssd`, `.dsd`, `.adf`,
or `.adl`).

The source directory should have the same hierarchical layout produced by
`extract -a --inf`:

- **DFS**: one subdirectory per directory character (`$/`, `T/`), with each
  data file accompanied by a `.inf` sidecar. For DSD images, `side0/` and
  `side1/` subdirectories are expected.

- **ADFS**: a `$` directory at the top level containing the file hierarchy,
  with subdirectories matching the ADFS tree structure. Subdirectories are
  created on the image automatically.

This enables a full round-trip workflow:

```bash
# DFS round-trip
beebtools extract original.ssd -a --inf -d working/
beebtools build working/ modified.ssd --title "MODIFIED"

# ADFS round-trip
beebtools extract original.adf -a --inf -d working/
beebtools build working/ modified.adf --title "MODIFIED"
```

## Options

- `-t` / `--tracks` - 40 or 80 tracks (default: 80). For ADFS: 40-track
  `.adf` = ADFS-S (160K), 80-track `.adf` = ADFS-M (320K), `.adl` = ADFS-L
  (640K).

- `--title` - disc title

- `--boot` - boot option: OFF, LOAD, RUN, or EXEC (numbers 0-3 also accepted)
