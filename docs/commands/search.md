# search - Search BASIC source code

```bash
beebtools search <image> <pattern> [filename] [-i] [-r] [--pretty]
```

Detokenizes every BASIC file on the disc and scans each line for the pattern.
Matching lines are printed with the filename and line number:

```text
--- Side 0: T.MYPROG ---
   10 GOTO 100
  230 IF SCORE > 100 THEN GOTO 230
```

## Options

- `filename` - limit the search to one file (e.g. `T.MYPROG` or bare `MYPROG`)

- `-i` / `--ignore-case` - case-insensitive match

- `-r` / `--regex` - treat pattern as a Python regular expression

- `--pretty` - apply operator spacing before matching
