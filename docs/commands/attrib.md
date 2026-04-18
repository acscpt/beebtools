# attrib - Read or set file attributes

```bash
beebtools attrib <image> <filename> [--locked|--unlocked|--access VALUE] [--load HEX] [--exec HEX] [--side 0|1]
```

Reads or sets the attributes of a single file on a DFS or ADFS disc image.

With no attribute flags, prints the current attributes to stdout: full path, load address, exec address, length, locked status, and a symbolic access string.

With one or more attribute flags, updates the specified attributes and writes the image back to disc. Attributes not specified are left unchanged.

## Options

| Option | Argument | Purpose |
|--------|----------|---------|
| `--locked` | - | Set the file's lock flag. Equivalent to `--access +L`. |
| `--unlocked` | - | Clear the file's lock flag. Equivalent to `--access -L`. |
| `--access` | `VALUE` | Set or mutate access bits. `VALUE` is disc format specific; see below. |
| `--load` | `HEX` | Set the load address, in hex, no `0x` prefix. |
| `--exec` | `HEX` | Set the exec address, in hex, no `0x` prefix. |
| `--side` | `0` or `1` | Disc side for DFS DSD images. Default `0`. Ignored for ADFS. |

`--locked`, `--unlocked`, and `--access` are mutually exclusive: they all change the access byte, just in different ways. Pass only one at a time.

## `--access` VALUE

### ADFS

ADFS has eight meaningful access bits split into owner and public groups: `L` (locked), `W` (write), `R` (read), `E` (execute-only), and their lowercase `l`, `w`, `r`, `e` counterparts for public (NFS) access.

Two forms, chosen by the first character of `VALUE`:

**Absolute** (first character is a letter, or `VALUE` is empty). Replaces the access byte exactly.

- `--access LWR` sets owner L+W+R, clears everything else.

- `--access LWR/r` sets owner L+W+R and public-r. The `/` is a cosmetic separator; letters after it fold to their public-case equivalents, so `LWR/R` and `LWR/r` mean the same thing.

- `--access LWRr` is equivalent to `--access LWR/r` (mixed case implies mixed owner/public without needing a slash).

- `--access ""` clears the access byte entirely.

**Mutation** (first character is `+` or `-`). Each `+X` / `-X` pair is applied to the current byte in order.

- `--access +L` sets owner-L, leaves everything else untouched.

- `--access -W` clears owner-W.

- `--access +L-W+R` applies all three mutations in sequence.

Mixing absolute letters with mutation operators in the same value (`L+W`, `lr-W`, etc.) is an error. Letters outside `LWRE` (owner) and `wre` (public) are ignored with a warning; `D` is called out separately in the warning because it is the directory type flag (use `mkdir` to create directories), not an access permission.

### DFS

DFS has only one meaningful access bit, `L` (locked). Only `L` and `l` are accepted in `VALUE`; any other letters are ignored with a warning.

- `--access L` or `--access LOCKED` locks the file.

- `--access ""` unlocks the file.

- `--access +L` / `-L` as mutations.

## Getter output

```
File:   $.GAMES.ELITE
Load:   FF001900
Exec:   FF008023
Length: 00002000
Locked: L
Access: LR/r
```

The `Access:` line shows a format-specific symbolic form:

- **ADFS:** `D` prefix if the entry is a directory, followed by owner letters in `LWRE` order; if any public bits are set, a `/` separator precedes public letters in `wre` order.

- **DFS:** `L` when locked, `-` when not.

## Invalid combinations

`attrib` errors out for unrecoverable ambiguity:

- `--access` combined with `--locked` or `--unlocked`.

- Mixed absolute and mutation forms in one `--access` value (`L+W`, `lr-W`).

- Same bit set and cleared in one mutation string (`+L-L`).

It warns and continues for:

- Non-`L` letters on a DFS image: the `L` portion still applies, the rest is ignored.

- Letters outside `LWRE`/`wre` on an ADFS image: the recognised bits still apply; `D` is called out separately in the warning.

- Owner-write or any public bit on an ADFS directory. Directory access permits only `L` and `R`; other bits are stripped.

## Examples

```bash
# Print current attributes, including the Access: line
beebtools attrib mydisc.ssd T.MYPROG

# Lock a file
beebtools attrib mydisc.ssd T.MYPROG --locked

# Unlock a file
beebtools attrib mydisc.ssd T.MYPROG --unlocked

# Set exact ADFS access: owner L+R, public r
beebtools attrib game.adf $.GAMES.ELITE --access LR/r

# Add a public-read bit without changing other bits
beebtools attrib game.adf $.GAMES.ELITE --access +r

# Remove the owner-write bit, leave everything else
beebtools attrib game.adf $.GAMES.ELITE --access -W

# Chain mutations: set L, clear W, set R
beebtools attrib game.adf $.GAMES.ELITE --access +L-W+R

# Clear all access bits on an ADFS file
beebtools attrib game.adf $.GAMES.ELITE --access ""

# Change load and exec addresses
beebtools attrib mydisc.ssd T.MYPROG --load 1900 --exec 8023

# Lock and set addresses in one call
beebtools attrib mydisc.ssd T.MYPROG --locked --load 1900 --exec 8023
```
