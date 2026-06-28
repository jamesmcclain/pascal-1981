# Gaps for a full Pascal port of `mandelbrot-gpu`

> **Status (current).** Both items that were blocking or endangering the port —
> `SIZEOF` of named types, and command-line argument handling — have been
> **fixed in-tree** and are no longer gaps. This revision records what changed,
> verified against the actual tree and against C, and re-scopes what remains
> (ergonomics, not blockers). PTX/NVPTX toolchain skew remains a non-concern by
> direction. Earlier revisions of this document treated `SIZEOF` and the command
> line as open problems; that is now out of date.

## Context

Two repositories are in play:

- `mandelbrot-gpu`: the existing Python GPU Mandelbrot renderer (CLI, view/precision
  selection, color mapping, PNG output; CUDA path loads `mandelbrot.ptx` via PyCUDA and
  launches `mandelbrot_f32`/`mandelbrot_f64`).
- `pascal-1981`: the Pascal compiler/runtime, with host-side C-ABI FFI (Phases 0–4), a
  device-PTX path, host-side CUDA orchestration builtins (`DEVALLOC`, `DEVCOPYTO`,
  `LAUNCH`, …), correct record `SIZEOF`, and vintage command-line argument binding.

The goal is to add a Pascal implementation beside the Python one — a Pascal host program
plus Pascal device kernels — with the PTX artifact interchangeable with the CUDA/PTX the
Python code uses.

---

## Recently resolved

### `SIZEOF` of named types — FIXED

**Symptom (was):** `SIZEOF` of a variable or type whose name was a user `TYPE` returned the
4-byte fallback — `SIZEOF(record)` → 4, and likewise named arrays, named wide-int aliases,
and the C alias `CLONG` (→ 4 instead of 8). Anonymous/inline aggregates sized correctly,
which is why the bug looked like "records only" at first.

**Root cause:** `get_type_size` (`codegen/types_map.py`) never resolved a `NamedType`
before sizing it; the branch fell through to `_scalar_size`, whose default is 4. A
secondary issue: the record arm summed field bytes with no alignment/tail padding.

**Fix (`sizeof-named-types.patch`):**

- `get_type_size` now resolves named aliases first (`resolve_type_alias`), so a variable or
  type spelled with a user `TYPE` name or a C alias is sized by its definition. This alone
  fixes records, named arrays, named wide-int aliases, and `CLONG`.
- Records are now sized through the **same layout helper the C-ABI marshaller uses**
  (`c_abi._size_of` on the record's LLVM type), so the result includes field alignment and
  tail padding and matches both the actual allocation and C's `sizeof`.
- The `SIZEOF(typename)` path was unified through `get_type_size`, so the type-name form
  resolves aliases too (previously it independently hit `_scalar_size`).

**Verified** against C `sizeof` on the same shapes:

| Pascal | `SIZEOF` | C `sizeof` |
|---|---:|---:|
| `rec = RECORD a,b: INTEGER32; c: ARRAY[0..63] OF CHAR END` (var and type) | 72 | 72 |
| `RECORD x: CHAR; y: INTEGER32 END` (padded) | 8 | 8 |
| `RECORD h: rec; n: INTEGER32 END` (nested) | 76 | 76 |
| named `ARRAY[0..9] OF CHAR` | 10 | 10 |
| named `INTEGER64`, and `CLONG` | 8 | 8 |
| scalars/pointer/inline aggregates (regression guard) | 2/2/1/8/1/8/8/10 | — |

Coverage: `tests/test_sizeof.py`.

### Command-line arguments — IMPLEMENTED

**Was:** `main` had no `argc`/`argv`; program-heading parameters were ignored
(`./prog 42` left the parameter zero). The document had framed this as "no
`PARAMSTR`/`PARAMCOUNT`," which is the wrong (Turbo Pascal) model.

**Now (`cmdline-program-parameters.patch`):** the faithful vintage model is implemented
(IBM manual 13-5…13-7). `main` is `i32 @main(i32 %argc, i8** %argv)`, and each
heading parameter other than `INPUT`/`OUTPUT` is populated, in order, from the command line,
prompting at the keyboard when an argument is absent.

```pascal
PROGRAM mandel(view, scale, tag);
VAR view: INTEGER; scale: REAL; tag: LSTRING(32);
...
```
```
$ mandel 3 0.75 zoomA      { view:=3, scale:=0.75, tag:='zoomA' }
$ mandel 8                 { view:=8; scale and tag are prompted }
```

Supported parameter types: everything `READ` accepts (`INTEGER`/`WORD`/`REAL`/`CHAR`/
`BOOLEAN`/enumerated/subrange/`STRING`/`LSTRING`) plus `FILE` types, where the token is the
filename and a later `RESET`/`REWRITE` opens it. Parsing reuses the ordinary `READ`
machinery (via a per-parameter `stdin` redirect), so command-line and interactive parsing
are identical. Programs that take no command-line input are unaffected. Details and limits
are in `docs/command-line-support.md`; coverage in `tests/test_cmdline.py`.

---

## Remaining gaps (ergonomics, not blockers)

### 1. C-string ergonomics are still a little rough

Passing a Pascal value as a C `char*` still means building a NUL-terminated buffer by hand
(`ARRAY[..] OF CHAR`, fill, append `CHR(0)`, pass `ADR`). `ADR` is the right primitive; the
friction is the manual NUL packing.

This is now smaller than it was, for two reasons. First, the command-line work means an
output filename can arrive as a proper `LSTRING`/`STRING` (or a `TEXT` file parameter)
rather than a hand-filled char array. Second, a thin, well-defined `LSTRING`/`STRING` →
`char*` bridge (a small runtime helper, or a documented `ADR` + explicit-NUL convention)
would cover the remaining libpng/`fopen`-style needs. **Priority: medium.** Doesn't prevent
the port; a small bridge removes most of the ugliness.

### 2. No header-import tooling; C declarations are translated by hand

For libpng even the simplified API needs manual transcription of constants, record fields,
and Pascal type aliases (`CINT`, `ADRMEM`, `INTEGER32`, …), checked against the host ABI.
Manageable for a small subset, easy to get subtly wrong. The `SIZEOF` fix **de-risks** this:
a hand-mistranslated struct can now be cross-checked because `SIZEOF` finally agrees with
the C layout. **Priority: medium.** Not required to finish the port; it raises risk.

---

## Resolved / not a blocker

- **`SIZEOF(record)`** — fixed (see above), and now padding-accurate / C-matching.
- **Command-line arguments** — implemented via the vintage program-parameter model.
- **PTX toolchain skew** — resolved by direction. The kernel ABI is a drop-in match
  (kernel names, argument order, scalar widths, entry-point form); version/producer/
  instruction-selection differences are accepted. Action item is negative: **do not disturb
  the PTX/NVPTX path** while doing host-side work.
- **libpng** — works from Pascal via the simplified API (extended dialect).
- **Host-side C FFI in general** — alive; re-confirmed on this tree.
- **Kernel symbol/parameter compatibility with the Python launcher** — matches; the existing
  Mandelbrot device example is shaped for exactly this.
- **CUDA orchestration surface** — `DEVALLOC`/`DEVCOPYTO`/`LAUNCH`/free already exist, so the
  port need not rebuild the PyCUDA launch path. (And `DEVCOPYTO`-style byte counts can now
  trust `SIZEOF`.)

---

## Recommended order of attack

The two blockers are cleared, so the path is now straightforward:

1. **Build the first Pascal host renderer around the libpng simplified API.** Hardcode one
   or a few views to start; take an output filename as a command-line parameter (now
   supported) — an `LSTRING`/`STRING` parameter, or a thin `char*` bridge for libpng.
2. **Reuse the existing Pascal Mandelbrot device example as the kernel base.** It already
   matches the Python CUDA contract; leave the PTX/NVPTX path untouched.
3. **Add the small `LSTRING`/`STRING` → `char*` bridge** (remaining gap 1) when the manual
   NUL packing starts to bite.
4. **Grow CLI coverage** using the program-parameter mechanism (positional, vintage-faithful)
   — view selector as `INTEGER`/enumerated, precision as `CHAR`/enumerated, output name as
   `LSTRING`/`STRING`/file. A flag-style parser, if ever wanted, is a separate convenience
   layer on top of the now-available `argc`/`argv`.
5. **Translate C headers as needed** (remaining gap 2), cross-checking struct layouts with
   the now-correct `SIZEOF`.

---

## Bottom line

The two scary items are done. `SIZEOF` is correct for named records (and named arrays, wide
aliases, and `CLONG`), padding-accurate, and matches C; command-line arguments work the
faithful vintage way, with a keyboard-prompt fallback. PTX is a drop-in match and should be
left alone.

What's left is ergonomics, not feasibility: a small `char*` bridge to make libpng/`fopen`
calls less manual, and (optionally) some discipline or tooling around hand-translated C
headers — now safer because `SIZEOF` can cross-check layouts. The recommended next move is
to build a minimal end-to-end Pascal renderer: simplified libpng output, an output-filename
command-line parameter, and the existing Mandelbrot PTX kernel. The rest is icework.
