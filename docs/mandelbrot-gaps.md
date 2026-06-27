# Gaps for a full Pascal port of `mandelbrot-gpu`

> **Revision note.** This document has been revised against the actual `pascal-ffi`
> tree (the one with C-FFI Phases 0–4 landed) and against the vintage *IBM Personal
> Computer Pascal* manual (Aug 1981). Two of the original findings changed materially:
> the `SIZEOF` bug is broader and differently rooted than first reported, and the
> "command-line" gap is not an open design question at all — the vintage dialect already
> *specifies* a command-line model, and the real gap is that the current compiler doesn't
> implement it. Per direction, **PTX/NVPTX toolchain skew is no longer treated as a
> concern**: the kernel ABI is already drop-in compatible and that path should simply be
> left untouched. Claims below marked "observed" were reproduced on this tree; manual
> citations use the page markers printed in the OCR text (e.g. 13-5).

## Context

Two repositories are in play:

- `mandelbrot-gpu`: the existing Python GPU Mandelbrot renderer. Today it uses Python for
  CLI handling, view selection, precision selection, color mapping, and PNG output; on the
  CUDA path it loads `mandelbrot.ptx` with PyCUDA and launches `mandelbrot_f32` /
  `mandelbrot_f64`.
- `pascal-1981` (this `pascal-ffi` tree): the Pascal compiler/runtime. It now has host-side
  C-ABI foreign-function support (`[C]`/`[CDECL]`, C type aliases, by-value structs,
  variadics, signext/zeroext — Phases 0–4), a device-PTX path, and host-side CUDA
  orchestration builtins (`DEVALLOC`, `DEVCOPYTO`, `LAUNCH`, …).

The goal is to add a Pascal implementation *beside* the Python one: a Pascal host program
plus Pascal device kernels, with the PTX artifact interchangeable with the CUDA/PTX used by
the Python code.

PTX interoperability was a stated requirement in both directions. The experiments below
confirm it is already met at the ABI surface, so it is recorded here as **resolved /
do-not-disturb** rather than as a gap.

---

## Experiments run

### 1. Pascal → PTX for the Mandelbrot device example

Compiled `examples/device_ptx/mandelbrot/mandelbrot.pas` with `--target ptx --sm sm_86 -f
wide-integers` and diffed against the provided `mandelbrot.ptx`.

Matched: kernel names (`mandelbrot_f32`, `mandelbrot_f64`), argument order, scalar widths
(output `.u64`; width/height/max_iter `.u32`; f32 coords `.f32`; f64 coords `.f64`), and
entry-point form (`.visible .entry`). Differed only in producer/version metadata
(`.version 7.1` LLVM-NVPTX vs `8.7` nvcc), pointer-parameter spelling (`.param .u64 .ptr
.global .align 4` vs plain `.param .u64`), and instruction selection / CFG shape.

**Verdict (unchanged, now closed):** the entry-point ABI is a drop-in match. Per direction,
the textual/toolchain differences are *not* a concern — the launcher accepts it, so this
path is considered done. The only action item is a *negative* one: don't perturb the
PTX/NVPTX emitter while fixing the host-side gaps below.

### 2. Host-side libpng call via C-ABI FFI

A small Pascal program using libpng's **simplified** API (`png_image_write_to_file`),
linked with `-lpng`, compiled and wrote a 1×1 PNG. Required `--dialect extended`;
`-f wide-integers` alone is not enough for `[C]` / `CINT` (the whole C-FFI surface is gated
behind the extended dialect — observed and by design).

**Verdict:** host-side C FFI is real and usable. Re-confirmed on this tree with an
independent scalar `[C]` extern (`cube(4) = 64`).

### 3. `SIZEOF` — re-diagnosed

The original report said "`SIZEOF(record)` is broken (reports 4)." Reproduced, but the
boundary is different from what was reported, and the bug is wider. Observed on this tree
(`--dialect extended -f wide-integers`):

| Expression (variable's declared type) | `SIZEOF` | Correct | Status |
|---|---:|---:|:--:|
| `r : rec` where `rec = RECORD a,b: INTEGER32; c: ARRAY[0..63] OF CHAR END` | 4 | 72 | ❌ |
| `rinline : RECORD a,b: INTEGER32; c: ARRAY[0..63] OF CHAR END` (anonymous) | 72 | 72 | ✅ |
| `a : myarr` where `myarr = ARRAY[0..9] OF CHAR` (named) | 4 | 10 | ❌ |
| `arr : ARRAY[0..9] OF CHAR` (anonymous) | 10 | 10 | ✅ |
| `n : mylong` where `mylong = INTEGER64` (named) | 4 | 8 | ❌ |
| `cl : CLONG` (C alias → INTEGER64) | 4 | 8 | ❌ |
| `am : ADRMEM` | 8 | 8 | ✅ |

The pattern is not "records are wrong." It is: **`SIZEOF` of a variable whose declared type
is a *named type* returns the fallback `4`, unless that name is literally one of a handful
of scalar names** (`INTEGER`, `INTEGER32`, `INTEGER64`, `REAL`, `WORD`, `CHAR`, `BOOLEAN`,
`ADRMEM`). Anonymous/inline aggregate types size correctly. Record *layout* and record
*size computation* are fine — the anonymous record reports 72. What's missing is **alias
resolution inside `SIZEOF`**.

This widening matters for the port specifically because **`CLONG` is wrong**: any FFI byte
count computed as `SIZEOF(some_C_aliased_var)` will be `4`. The original "records only"
framing understated the FFI exposure.

### 4. C-facing record layout

A C helper reports `sizeof` = 72 for `struct { int32_t a, b; char c[64]; }` and 104 for a
`png_image`-shaped struct; a by-reference Pascal record behaved layout-compatibly. So the
in-memory record layout is usable; only Pascal's own `SIZEOF` is wrong (gap 1).

---

## Actual gaps that would block or seriously endanger the full port

## 1. `SIZEOF` of named types is broken (root cause located)

The clearest hard blocker, and now pinned to a specific line.

### Root cause

`codegen/exprs.py` (the `SizeofExpr` handler) calls `get_type_size(symbol.type_expr)`.
`get_type_size` in `codegen/types_map.py` has a `NamedType` branch that handles only
`STRING`/`LSTRING` specially and otherwise calls `_scalar_size(name)`. `_scalar_size`
(`codegen/base.py`) is `_SCALAR_SIZES.get(name.upper(), 4)` — a fixed table of builtin
scalars with a **default of 4**. A user `TYPE` name (record, array, wide-int alias) or a C
alias such as `CLONG` is not in that table, so it silently returns 4. The `RecordType`
and `ArrayType` arms of `get_type_size` are correct; they're simply never reached for a
named variable because the alias is never resolved first.

### Fix sketch (concrete, low-risk)

In `get_type_size`'s `NamedType` branch: if the name is a builtin scalar (in
`_SCALAR_SIZES`), keep the current answer; otherwise resolve it with the existing
`resolve_type_alias` (same mixin, `types_map.py`) and recurse on the resolved type. That
single change fixes records, named arrays, named wide-int aliases, and the C aliases at
once.

**Secondary correctness note (worth doing in the same pass):** `get_type_size`'s
`RecordType` arm sums field sizes with *no alignment/padding*. It happens to be exact for
the all-naturally-aligned example structs here (72, 104), but it under-reports any padded
record (e.g. `RECORD x: CHAR; y: INTEGER32 END` → it would compute 5, while the C/LLVM
layout is 8). Since these sizes feed FFI (`DEVCOPYTO`, `memcpy`, struct clears), `SIZEOF`
should match the real ABI layout. The ABI-correct layout logic **already exists in this
tree**: `codegen/c_abi.py::_size_of` / `_align_of` (the System V classifier trusts it).
The robust fix is to size aggregates from their LLVM type via those helpers rather than the
naive field sum, so `SIZEOF` and the C-ABI marshaller agree by construction.

### Priority

**Highest.** Fix before more host-side FFI work. Small, localized, and unblocks safe
allocation/copy/clear of C-facing records.

---

## 2. The vintage command-line model is specified but unimplemented

The original document framed this as "no `PARAMSTR`/`PARAMCOUNT`" and "no command-line
convenience layer yet," and treated it as an open design choice. That framing imports the
*Turbo Pascal* mental model. The dialect this compiler targets has its **own**, fully
specified command-line model, and the actual gap is that the compiler doesn't implement it.

### What the vintage dialect actually says (IBM manual, 13-5 … 13-7, 12-34/35)

- Command-line arguments are bound to the **program parameters** named in the program
  heading: `PROGRAM mandel(view, prec, outfile);`.
- "Every program parameter variable (except `INPUT` and `OUTPUT`) gets a value during
  program initialization by doing a `READFN` of one form or another." (13-5)
- A program parameter may be a **simple type** (`INTEGER`, `WORD`, `CHAR`, `BOOLEAN`,
  enumerated, their subranges, `REAL`), a **pointer type**, **`STRING`**, **`LSTRING`**, or
  a **`FILE`** type — *not* just files. (13-5) So scalar arguments (a view index, a
  precision selector) are first-class, not something you must read from a file.
- Mechanically, the per-parameter `READFN` reads from `INPUT`, but a special `PPMFQQ` call
  first redirects it to take characters from the DOS interface routine `PPMUQQ`, which
  supplies them **from the command line that started the program**; the program identifier
  is passed for use as a prompt. If an argument is absent, the runtime **prompts** for it
  (and always prompts when an `LSTRING` looks omitted). (13-5/13-6)
- `INPUT`/`OUTPUT` are special: if listed as parameters they're bound to the
  keyboard/display, not filled from the command line. (12-34, 13-4)
- For raw/advanced command-line processing, the manual points to the **Unit U procedure
  `PPM`**, used with **no** program parameters in the heading. (13-7)

So the dialect-faithful "argv" is: list the inputs as program parameters of the right
types, and let initialization populate them from the command line with a keyboard fallback.
`A>mandel 3 d output.png` would set `view := 3`, `prec := 'd'`, `outfile := 'output.png'`.

### Current state (observed)

The compiler implements none of this. `codegen_program` emits `define i32 @main()` with **no
`argc`/`argv`**; heading parameters are parsed but, apart from allocating an FCB for
`FILE`-typed ones, are never bound to anything. A program `PROGRAM PARMS(n); VAR n: INTEGER`
run as `./parms 42` prints `n=0` — the argument is ignored and the parameter is left
zero-initialized. There is no `READFN`-from-command-line, no `PPMFQQ`/`PPMUQQ`/`PPM`, and no
argv plumbing in the runtime.

### Why it matters

The Mandelbrot port wants at least a view selector, a precision selector (`f32`/`f64`), and
an output filename. All three map cleanly onto vintage program parameters (`INTEGER`/
enumerated, `CHAR`/enumerated, `STRING`/`LSTRING`). Implementing the mechanism gives CLI
input the *faithful* way, instead of bolting on a foreign `PARAMSTR` API.

### Implementation path (in faithful-first order)

1. **Minimal, unblock-the-port step:** change `main` to `i32 (i32 %argc, i8** %argv)` and
   stash the pair in a runtime global. This alone enables a C-shim fallback and is a
   prerequisite for everything below.
2. **Faithful step:** at program initialization, for each heading parameter other than
   `INPUT`/`OUTPUT`, emit the `READFN`-style population from successive command-line tokens,
   with the documented keyboard-prompt fallback. A small runtime helper plays the
   `PPMUQQ`/`PPMFQQ` role (tokenize argv, hand characters to the existing `READFN`/numeric
   parsers, prompt on exhaustion). Reuse the existing `READFN`/`READ` conversions so
   `INTEGER`/`REAL`/`CHAR`/`STRING`/`LSTRING` parsing stays identical to interactive input.
3. **Escape hatch:** expose `PPM` (Unit U) for raw command-line access when the heading has
   no parameters, matching 13-7, for cases that want their own parsing.

A bare `PARAMSTR`/`PARAMCOUNT` could be added as a *non-faithful convenience*, but it should
be recognized as a Turbo-ism layered on top of step 1, not the dialect's native model.

### Priority

**High.** Second only to `SIZEOF`. Step 1 is tiny and unblocks a hardcoded-views renderer;
steps 2–3 are what "CLI parity, done faithfully" requires.

---

## 3. C-string ergonomics are still rough

Not a hard blocker, but it will make the host port clumsier than the Python original.

### What was observed

The libpng test worked but required hand-building a NUL-terminated filename buffer: allocate
`ARRAY[...] OF CHAR`, fill char by char, append `CHR(0)`, pass `ADR filename`. `ADR` is the
address-of operator and is the right primitive; the friction is purely the manual NUL
packing.

### Relationship to gap 2

This is less isolated than it looks. In the vintage model, `STRING`/`LSTRING` are valid
program-parameter types, and `LSTRING` is length-prefixed — so once gap 2 is implemented,
an output filename arrives as a proper `LSTRING` rather than a hand-filled char array. A
small, well-defined `LSTRING`/`STRING` → `char*` bridge (a runtime helper or a documented
`ADR` + explicit NUL convention) would cover the remaining libpng/`fopen`-style needs.

### Priority

**Medium.** Doesn't prevent the port; a thin bridge removes most of the ugliness.

---

## 4. No header-import tooling; C declarations are translated by hand

Unchanged from the original assessment. For libpng even the simplified API needs manual
transcription of constants, record fields, and Pascal type aliases (`CINT`, `ADRMEM`,
`INTEGER32`, …), checked against the host ABI. Manageable for a small subset, easy to get
subtly wrong — and gap 1 makes a hand-mistranslated struct *harder* to catch, since you
can't currently trust `SIZEOF` to cross-check the layout. Fixing gap 1 partly de-risks this
one.

### Priority

**Medium.** Not required to finish the port; it raises risk.

---

## Resolved / not a blocker

- **PTX toolchain skew** — *resolved per direction.* The kernel ABI is a drop-in match;
  toolchain/version differences are accepted. Action item is to **not disturb** the
  PTX/NVPTX path while doing host-side work. (Previously listed as gap 2, "High priority.")
- **libpng** — works from Pascal via the simplified API.
- **Host-side C FFI in general** — alive and re-confirmed on this tree.
- **Kernel symbol/parameter compatibility with the Python launcher** — matches; the existing
  Mandelbrot device example is shaped for exactly this.
- **CUDA orchestration surface** — `DEVALLOC`/`DEVCOPYTO`/`LAUNCH`/free already exist, so the
  port need not rebuild the PyCUDA launch path. (Note: `DEVCOPYTO`-style byte counts will
  want a correct `SIZEOF` — another reason gap 1 is first.)

---

## Recommended order of attack

1. **Fix `SIZEOF` of named types** (gap 1). One-line alias-resolution fix plus, ideally,
   ABI-accurate aggregate sizing via the existing `c_abi._size_of`/`_align_of`. Highest
   value, lowest risk, and it de-risks gaps 3 and 4.
2. **Implement the vintage command-line model** (gap 2). Start with argv into `main`
   (unblocks a hardcoded-views renderer immediately), then the faithful program-parameter
   `READFN`-from-command-line population, then `PPM` for raw access.
3. **Build the first Pascal host renderer around the libpng simplified API.** Skip fancy CLI
   at first; hardcode one or a few views; add a small `LSTRING → char*` bridge as needed.
4. **Reuse the existing Pascal Mandelbrot device example as the kernel base.** It already
   matches the Python CUDA contract; leave the PTX path alone.
5. **Then add CLI parity** by finishing gap 2's faithful path, not by importing a foreign
   `PARAMSTR` API.

---

## Bottom line

The project is closer than the first pass suggested, and two of the three "scary" items got
smaller on inspection.

Good news:

- Pascal → PTX Mandelbrot kernels already match the launcher's ABI shape; PTX is now a
  do-not-touch, not a risk.
- Host-side libpng output is achievable today through C-FFI (extended dialect).
- The command-line story is not a green-field design problem: the dialect already specifies
  it, so there's a *faithful* target to implement against rather than a taste call.

Real work remaining:

- **`SIZEOF` of named types returns 4** — broader than "records," it also hits named arrays,
  named wide-int aliases, and `CLONG`. Root cause is a missing alias resolution in
  `get_type_size`; fix is small and localized, and aggregate sizing should be routed through
  the ABI-correct layout helpers so `SIZEOF` and the C marshaller agree.
- **The vintage command-line mechanism is unimplemented.** `main` has no argv and heading
  parameters are ignored. The faithful fix is program-parameter population from the command
  line (with prompt fallback) per IBM manual 13-5…13-7; a minimal argv-into-`main` step
  unblocks a first renderer immediately.

Fix record/named `SIZEOF` first, wire up at least minimal command-line handling second, then
push straight into a minimal end-to-end Pascal renderer using the simplified libpng API and
the existing Mandelbrot PTX example. The rest is icework.
