# cat - List disc catalogue

```bash
beebtools cat <image> [--sort name|catalog|size] [--inspect]
```

Lists all files on all sides of the disc with load address, exec address,
length, and file type. BASIC is identified from the exec address without
reading file data.

Add `--inspect` (`-i`) to also read each file's bytes and label plain ASCII
text files as `TEXT` in the type column:

```text
--- Side 0: BBC_MUSIC_2 (28 files) ---

  Name          Load     Exec   Length  Type
   $.!BOOT  00000000 00000000 00000018  TEXT
   T.BACHPR 00000E00 00008023 000011A4  BASIC
   T.BEETHO 00000E00 00008023 00000F6C  BASIC
   ...
```

## Options

- `-s` / `--sort` - sort order (default: `name`)

- `-i` / `--inspect` - read file contents to detect TEXT files (slower)

## Sort options

- `name` - alphabetical by filename

- `catalog` - original on-disc DFS order

- `size` - ascending by file length
