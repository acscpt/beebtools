# create - Create a blank disc image

```bash
beebtools create <output> [-t 40|80] [--title TITLE] [--boot OFF|LOAD|RUN|EXEC]
```

Creates a blank formatted DFS disc image. The format (SSD or DSD) is determined
by the output file extension.

## Options

- `-t` / `--tracks` - 40 or 80 tracks (default: 80)

- `--title` - disc title (up to 12 characters)

- `--boot` - boot option: OFF, LOAD, RUN, or EXEC (numbers 0-3 also accepted)
