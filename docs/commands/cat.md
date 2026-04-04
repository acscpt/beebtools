# cat - List disc catalogue

```bash
beebtools cat <image> [--sort name|catalog|size] [--inspect]
```

Lists all files on all sides of the disc with load address, exec address,
length, and file type. The header line shows the number of files, total
tracks, and boot option for each side. Works with both DFS (`.ssd`/`.dsd`)
and ADFS (`.adf`/`.adl`) disc images. BASIC is identified from the exec
address without reading file data.

Add `--inspect` (`-i`) to also read each file's bytes and label plain ASCII
text files as `TEXT` in the type column.

### DFS example

```text
--- Side 0: BBC_MUSIC_2 (28 files, 80 tracks, boot=OFF) ---

  Name          Load     Exec   Length  Type
   $.!BOOT  00000000 00000000 00000018  TEXT
   T.BACHPR 00000E00 00008023 000011A4  BASIC
   T.BEETHO 00000E00 00008023 00000F6C  BASIC
   ...
```

### ADFS example

ADFS images display the full hierarchical path for each file. Directory
entries are labelled as `DIR`. The name column adjusts its width
automatically for long path names.

```text
--- Side 0: GameDisc (12 files, 80 tracks, boot=EXEC) ---

  Name                  Load     Exec   Length  Type
   $.!BOOT          00000000 00000000 00000020
   $.GAMES          00000000 00000000 00000500  DIR
   $.GAMES.ELITE    00000E00 0000802B 00004800  BASIC
   $.GAMES.REVS     00001900 00001900 00006000
   $.README         00000000 00000000 00000100
   ...
```

## Options

- `-s` / `--sort` - sort order (default: `name`)

- `-i` / `--inspect` - read file contents to detect TEXT files (slower)

## Sort options

- `name` - alphabetical by filename

- `catalog` - original on-disc catalogue order

- `size` - ascending by file length
