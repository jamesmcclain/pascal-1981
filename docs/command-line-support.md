# Command-line support

This note describes how a compiled Pascal program receives command-line
arguments, what is implemented today, and the known limits.

## The model: program parameters

Command-line arguments are bound to the **parameters named in the program
heading**, following the vintage IBM Pascal model (manual 13-5…13-7, 12-34/35)
rather than a Turbo-style `PARAMSTR`/`PARAMCOUNT` API.

```pascal
PROGRAM mandel(view, scale, tag);
VAR
  view  : INTEGER;
  scale : REAL;
  tag   : LSTRING(32);
BEGIN
  ...
END.
```

```
$ mandel 3 0.75 zoomA      { view := 3,  scale := 0.75,  tag := 'zoomA' }
```

Each heading parameter other than `INPUT`/`OUTPUT` is read, in heading order,
from successive command-line tokens during program initialization. Parameter 0
comes from `argv[1]`, parameter 1 from `argv[2]`, and so on.

When a token is **absent**, the program prompts for it on standard output
(`view: `) and reads the value from the keyboard. This makes the two forms below
equivalent in effect:

```
$ mandel 3 0.75 zoomA
$ mandel
view: 3
scale: 0.75
tag: zoomA
```

Partial command lines are allowed: arguments supplied on the command line are
used as-is, and only the missing trailing parameters are prompted for.

### Supported parameter types

A program parameter may be any type the ordinary `READ` accepts, plus files:

- `INTEGER`, `WORD`, `REAL`, `CHAR`, `BOOLEAN`
- enumerated types and their subranges (numeric ordinal by default; symbolic
  names when the `symbolic-enum-io` feature is enabled)
- `STRING(n)` and `LSTRING(n)`
- `FILE` types (`TEXT`, `FILE OF …`): the token is taken as the **filename** and
  bound to the file, so a later `RESET`/`REWRITE` opens exactly that file. This
  is the canonical vintage use of a file program parameter.

`INPUT` and `OUTPUT`, if listed in the heading, are bound to the keyboard and
display; they are **not** filled from the command line and occupy no positional
slot. A program that takes no command-line input (no heading parameters, or only
`INPUT`/`OUTPUT`) is unaffected and links without the command-line runtime.

## How it works

Parsing a command-line value reuses the *exact* `READ` machinery, so a value
parses identically whether it comes from the command line or the keyboard. For
each parameter the code generator emits, in `main`:

1. `pas_arg_begin(position, name)` — if a command-line token is present, `stdin`
   is redirected to a one-token, newline-terminated in-memory stream (`fmemopen`)
   and `1` is returned; otherwise the `name: ` prompt is written and `0` is
   returned, leaving `stdin` on the keyboard.
2. the ordinary per-target reader (`pas_read_int`/`pas_read_real`/
   `pas_read_lstring`/… for value parameters, or an `LSTRING` read followed by
   `pas_file_assign` for file parameters);
3. a `READLN`-style end-of-line skip, so the keyboard-fallback path advances to
   the next line cleanly (harmless on the discarded token stream);
4. `pas_arg_end()` — restore the real `stdin` and release the token stream.

`main` is emitted as `i32 @main(i32 %argc, i8** %argv)`; `argc`/`argv` are handed
to a small runtime (`runtime/cmdline.c`) by `pas_args_init`. The implementation
deliberately reproduces only the *observable* IBM behavior; the internal IBM
routine names (`PPMUQQ`, `PPMFQQ`, `PPM`) are not reproduced.

Relevant code: `runtime/cmdline.c`; `codegen/decls.py`
(`_codegen_program_parameters`, `_bind_file_parameter`, the `main` signature);
runtime externs registered in `codegen/base.py`. Coverage in
`tests/test_cmdline.py`.

## Limits and non-goals

- **No raw command-line access (`PPM` / Unit U).** The manual offers `PPM` for
  programs that want to parse the whole command line themselves (declared with no
  heading parameters). That escape hatch is not implemented; the
  program-parameter mechanism above is the supported path. It can be added later
  without disturbing this one.
- **Tokenization is the shell's.** Each parameter consumes one `argv` token;
  values with embedded spaces must be quoted on the command line. There is no
  re-splitting of a single combined argument string.
- **Filenames are read as `LSTRING`** (cap 255 chars) before `ASSIGN`; an empty
  filename is rejected by the file runtime, matching the requirement that a file
  parameter name a real file.
- **No flag/option parser.** Arguments are positional, as in the vintage model.
  Conventional `--flag` parsing, if ever wanted, would be a separate convenience
  layer on top of `argc`/`argv` (now available to `main`) or a future `PPM`.

## Relation to the Mandelbrot port

This covers the host renderer's CLI needs directly: a view selector (`INTEGER`
or an enumerated type), a precision selector (`CHAR` or enumerated), and an
output filename (`LSTRING`/`STRING`, or a `TEXT`/`FILE` parameter when the output
goes through Pascal file I/O rather than a C library). For a first milestone a
program can simply hardcode views and accept only an output name; broader CLI
parity remains positional under this model.
