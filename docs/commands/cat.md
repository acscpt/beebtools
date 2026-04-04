# cat - List disc catalogue

```bash
beebtools cat <image> [--sort name|catalog|size] [--inspect]
```

Lists all files on all sides of the disc with load address, exec address,
length, and file type. The header line shows the number of files, total
tracks, and boot option for each side. Works with both DFS (`.ssd`/`.dsd`)
and ADFS (`.adf`/`.adl`) disc images. BASIC is identified from the exec
address without reading file data.

Add `--inspect` (`-i`) to read each file's contents and classify files by
their data, not just their metadata. Without `--inspect`, BASIC is
identified solely from the execution address.

### Type labels

- `BASIC` - tokenized BBC BASIC program, identified by its execution
  address matching a known language entry point (0x801F, 0x8023, 0x802B).

- `BASIC?` - tokenized BBC BASIC detected by content inspection. The file
  contains a structurally valid program (0x0D line markers and 0x0D 0xFF
  end marker) but its execution address is non-standard. Common for DATA
  fragments saved with `*SAVE` or OSFILE rather than BASIC's own `SAVE`
  command. Only shown with `--inspect`.

- `BASIC+MC` - tokenized BBC BASIC followed by appended machine code or
  data. The BASIC program's 0x0D 0xFF end marker appears well before the
  end of the file. Only shown with `--inspect`.

- `TEXT` - plain ASCII text (all bytes are printable ASCII, tab, CR, or
  LF). Only shown with `--inspect`.

- `DIR` - ADFS directory entry.

### DFS example

```text
--- Side 1: 8BS-39 (30 files, 80 tracks, boot=EXEC) ---

    Name             Load     Exec   Length  Type
    $.!BOOT     00000000 0003FFFF 00000011  TEXT
  L $.D1        00030E00 000380E7 000000A3  BASIC?
  L $.D2        00030E00 000380E7 00000093  BASIC?
  L $.Editori   00001900 00001904 00004A7F
  L $.LAPPY2    00030E00 0003802B 0000088D  BASIC
  L $.SnowCde   00001100 00001400 00001EA0
  L $.SnowMen   00000000 0003FFFF 00000600  BASIC+MC
    ...
```

### ADFS example

ADFS images display the full hierarchical path for each file. Directory
entries are labelled as `DIR`. The name column adjusts its width
automatically for long path names.

```text
--- Side 0: GameDisc (12 files, 80 tracks, boot=EXEC) ---

    Name                  Load     Exec   Length  Type
    $.!BOOT           00000000 00000000 00000020
    $.GAMES           00000000 00000000 00000500  DIR
    $.GAMES.ELITE     00000E00 0000802B 00004800  BASIC
    $.GAMES.REVS      00001900 00001900 00006000
    $.README          00000000 00000000 00000100  TEXT
    ...
```

## Options

- `-s` / `--sort` - sort order (default: `name`)

- `-i` / `--inspect` - read file contents to classify files by data
  (detects `BASIC?`, `BASIC+MC`, and `TEXT`; slower)

## Sort options

- `name` - alphabetical by filename

- `catalog` - original on-disc catalogue order

- `size` - ascending by file length
