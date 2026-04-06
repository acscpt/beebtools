# rename - Rename a file on a disc image

```bash
beebtools rename <image> <oldname> <newname> [--side 0|1]
```

Renames a file on an existing DFS or ADFS disc image. The file data is not
moved - only the catalogue entry is updated.

On DFS, the directory prefix can change as part of the rename (e.g. renaming
`$.MYPROG` to `T.MYPROG`). On ADFS, both names must be in the same parent
directory - cross-directory moves are not supported.

If the destination name already exists, the command aborts with an error.

## Options

- `--side` - disc side for DFS DSD images (default: 0; ignored for ADFS)

## DFS naming rules

- Directory: single character, printable ASCII (0x21-0x7E)

- Filename: 1-7 characters, printable ASCII

- Forbidden characters: `.` `:` `"` `#` `*` and space

## ADFS naming rules

- Filename: 1-10 characters, printable ASCII (0x21-0x7E)

## Examples

```bash
# Rename a file (same directory)
beebtools rename mydisc.ssd T.MYPROG T.NEWNAME

# Move to a different DFS directory prefix
beebtools rename mydisc.ssd $.MYPROG T.MYPROG

# Rename on side 1 of a double-sided disc
beebtools rename mydisc.dsd T.OLDNAME T.NEWNAME --side 1

# Rename on an ADFS image
beebtools rename game.adf $.GAMES.ELITE $.GAMES.BACKUP
```
