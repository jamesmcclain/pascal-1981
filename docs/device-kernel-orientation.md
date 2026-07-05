# Device Kernel Orientation — Pascal-1981 Extended Dialect

**Read this before writing or debugging device kernels.**
It is short on purpose; a larger context budget is better spent on the design
documents. Three items here have cost other agents significant wasted effort.

---

## 0. Ground rule: this is not "Pascal" in the generic sense

This compiler implements **IBM Pascal 2.0 (1981) as the base dialect**, then
extends it with a device/GPU sublanguage. That origin matters in two ways:

1. **Syntax is the vintage IBM surface, not Turbo Pascal, not Free Pascal, not
   ISO 10206.** When you hit something that looks wrong, look it up in
   `docs/ebnf_grammar.md` and `docs/ads-memory-spaces-design.md` before
   assuming the compiler is broken. There are deliberate divergences from later
   Pascal conventions.

2. **The device dialect is an *extension*, not a replacement.** Device modules
   (`DEVICE INTERFACE` / `DEVICE IMPLEMENTATION OF`) are a new layer built on
   top of the vintage base. They lose a specific set of host features (I/O,
   heap, recursion, initializer blocks — see `docs/old/cuda-kernel-prescription.md §2.3`)
   and gain address-space types (`ADS(GLOBAL) OF T`) and execution-model
   builtins (`THREADIDX_X`, `SYNCTHREADS`, …). Everything that is not explicitly
   rescinded or added still follows vintage IBM Pascal rules. If your mental
   model is CUDA C or a modern Pascal, you will misread error messages and reach
   for syntax that does not exist here.

When you are unsure whether something is a compiler bug or expected behavior,
the sequence is: **(a)** check the grammar, **(b)** check the design docs,
**(c)** look at the working examples under `examples/device_ptx/`, **(d)** only
then consider filing a bug.

---

## 1. Unbounded arrays in parameters: use a TYPE alias with `SUPER`

### What trips agents up

Writing an unbounded array type inline in a procedure parameter:

```pascal
{ WRONG — parse error: 'expected constant at line N (token MUL *)' }
PROCEDURE kernel(inp: ADS(GLOBAL) OF ARRAY [0..*] OF INTEGER32; n: INTEGER32);
```

### Why

`ARRAY` without the `SUPER` prefix does not accept `*` as an upper bound.
The `*` sentinel is gated on the `SUPER ARRAY` production; plain `ARRAY` requires
a constant expression for both bounds. Because parameter types are parsed with
the general `parse_type` rule, the `SUPER` keyword must be present at the use
site or the `*` is seen as a multiply operator and the parser fails.

### Correct pattern

Declare the type alias in the interface; the implementation inherits it
automatically (the compiler seeds the interface's `TYPE` and `CONST` declarations
into the implementation scope before processing implementation declarations):

```pascal
{ In the interface (.inc file) }
DEVICE INTERFACE;
UNIT MYKERNEL (kernel_entry);

TYPE
  BUFFER = SUPER ARRAY [0..*] OF INTEGER32;

PROCEDURE kernel_entry(inp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
END;
```

```pascal
{ In the implementation (.pas file) — TYPE section NOT restated }
(*$INCLUDE:'mykernel.inc'*)
DEVICE IMPLEMENTATION OF MYKERNEL;

PROCEDURE kernel_entry(inp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
...
```

If the implementation needs to override or extend the interface's type it may
declare its own `TYPE` section; the implementation declaration wins. An
implementation-private type (not in the interface) is also fine and resolves
normally.

`SUPER ARRAY [0..*]` lowers to a flat device pointer (no hidden length field).
Dimensions and bounds are the caller's responsibility — exactly the CUDA
convention for device buffers. Explicit bounds in the interface (e.g.
`ARRAY [0..255] OF INTEGER32`) are also fine and carry range-check information
at bounded sites; use them when the size is known and fixed.

---

## 2. Shared memory is a variable attribute, not a type qualifier

### What trips agents up

```pascal
{ WRONG — parser does not know 'SHARED' as a type constructor }
TYPE SCRATCH = SHARED ARRAY [0..255] OF REAL32;
VAR  buf: SHARED ARRAY [0..255] OF REAL32;
```

### Why

`SHARED` is not a type keyword in this dialect. There is no
`SHARED T`, `GLOBAL T`, or `LOCAL T` type prefix. Memory spaces are a property
of *where a variable is allocated*, not of its type. The residence attribute
syntax — a bracketed list in front of the declaration — is the correct surface,
exactly as `ORIGIN(addr)` binds a variable to an absolute address, and
`READONLY`/`PUBLIC`/`STATIC` qualify its storage.

### Correct pattern

```pascal
VAR [SPACE(SHARED)] scratch: ARRAY [0..255] OF REAL32;
```

`SPACE` is accepted inside `[ ]` on a `VAR` declaration. It is contextual —
not a globally reserved word — so existing identifiers named `space` in vintage
code are unaffected. The `SPACE` enum is `(HOST, GLOBAL, SHARED, CONSTANT,
LOCAL)` (ordinals 0–4); see `ads-memory-spaces-design.md §3` for the full
addrspace mapping.

Pointer types use the pointee-space syntax `ADS(SPACE_CONSTANT) OF T`:

```pascal
{ pointer whose target lives in global memory }
VAR p: ADS(GLOBAL) OF REAL32;

{ pointer parameter addressing a global buffer }
PROCEDURE foo(buf: ADS(GLOBAL) OF ARRAY [0..255] OF REAL32; ...);
```

Summary: `[SPACE(s)]` on a variable declaration sets *where it lives*;
`ADS(s) OF T` on a pointer type sets *what space it addresses*.

---

## 3. Interface procedure signatures take types, not runtime expressions

### What trips agents up

Writing a computed value in a procedure's interface declaration:

```pascal
{ WRONG — interface signatures take type descriptions, not runtime expressions }
UNIT MYKERNEL (entry);
PROCEDURE entry(buf: ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32;
                stride: INTEGER32 := BLOCKDIM_X * GRIDDIM_X);  { not valid }
```

or expecting to name a kernel's launch geometry inside the signature:

```pascal
{ WRONG — grid/block dimensions are supplied at launch, not in the signature }
PROCEDURE entry(buf: ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32)
  GRID(256) BLOCK(128);
```

### Why

These are not bugs in the compiler. An interface procedure signature in this
dialect is purely a type contract: parameter names, modes (`VAR`/`CONST`/…),
and types. Default values and launch-geometry annotations in signatures are not
part of the grammar. The execution model (thread index, block/grid dimensions)
is accessed inside the procedure body via the builtin intrinsics, not declared
in the signature.

### Correct pattern

All stride calculations and geometry decisions happen **inside the procedure
body**:

```pascal
PROCEDURE entry(buf: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR
  i, stride: INTEGER32;
BEGIN
  stride := BLOCKDIM_X * GRIDDIM_X;
  i      := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  WHILE i < n DO
  BEGIN
    buf^[i] := i;
    i := i + stride
  END
END;
```

Launch geometry (grid and block dimensions) is always a caller concern — a
host-side `LAUNCH(entry, GRID(...), BLOCK(...), ...)` call or the equivalent
external launch mechanism. The kernel itself is oblivious to its launch
dimensions except through the intrinsics.

---

## Quick reference: working device kernel shape

This template compiles and has been validated against real hardware
(see `docs/old/cuda-kernel-prescription.md` and `examples/device_ptx/`):

```pascal
{ ── interface file (e.g. kernel.inc) ─────────────────────────────── }
DEVICE INTERFACE;
UNIT KERNEL (kernel_entry);

TYPE
  BUFFER = SUPER ARRAY [0..*] OF INTEGER32;

PROCEDURE kernel_entry(buf: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
END;

{ ── implementation file (kernel.pas) ─────────────────────────────── }
(*$INCLUDE:'kernel.inc'*)
DEVICE IMPLEMENTATION OF KERNEL;

{ TYPE BUFFER is declared in the interface; no need to restate it here. }

PROCEDURE kernel_entry(buf: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR
  i, stride: INTEGER32;
BEGIN
  stride := BLOCKDIM_X * GRIDDIM_X;
  i      := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  WHILE i < n DO
  BEGIN
    buf^[i] := i;        { write to global memory }
    i := i + stride
  END
END;
.
{ Note: NO 'BEGIN ... END.' initializer block — banned in DEVICE UNIT }
```

Key rules distilled:
- Exported procedures (listed in `UNIT K (...)`) become PTX `.entry` points.
- Non-exported procedures stay `.func` device helpers.
- No `BEGIN ... END.` initializer block in a `DEVICE IMPLEMENTATION`.
- Thread/block intrinsics: `THREADIDX_X/Y/Z`, `BLOCKIDX_X/Y/Z`,
  `BLOCKDIM_X/Y/Z`, `GRIDDIM_X/Y/Z` (all `INTEGER32`).
- Synchronization barrier: `SYNCTHREADS` (procedure, no arguments).
- Shared memory: `VAR [SPACE(SHARED)] sdata: ARRAY [0..N] OF T;`
- Global buffer parameters: `ADS(GLOBAL) OF BUFFER` where `BUFFER` is a
  `SUPER ARRAY [0..*] OF T` alias.
- Bounded-size parameters are also valid: `ADS(GLOBAL) OF ARRAY [0..255] OF T`.

For host orchestration, PTX emission, and the full CUDA launch path see
`docs/old/cuda-kernel-prescription.md` (archived — all milestones A–D complete).

---

## 4. Kernel-entry parameter facts and the LAUNCH contract

Exported kernel-entry buffer parameters (`ADS(GLOBAL)`/`ADS(CONSTANT) OF T`)
automatically carry whichever of these facts this compiler can establish
without guessing:

- **`align`** — the element type's natural alignment (always on).
- **`dereferenceable(n)`** — only when the pointee is a statically-sized
  `ARRAY[lo..hi] OF T` (always on for that case). A `SUPER ARRAY [lo..*] OF T`
  buffer parameter gets none: there is no compiler-enforced link between such
  a parameter and whichever sibling parameter might carry its runtime length,
  so this compiler does not guess one.
- **`readonly`/`nocapture`** — only when this procedure's own body
  provably never writes through the parameter: no assignment target
  dereferences it, and it is never passed as a bare argument to another call
  (this compiler does not attempt an interprocedural proof that a callee
  itself never writes through it — passing it onward conservatively costs the
  attribute). A body containing any `WITH` statement withholds `readonly`
  from every parameter of that procedure, since `WITH`'s field designators
  are not tied back to the originating pointer by this analysis. This only
  ever *withholds* the attribute, never wrongly grants it.
- **`noalias`** — **opt-in only**, via `-f noalias-kernel-params`
  (registered feature, not part of the `extended` umbrella — `--dialect
  extended` alone does not turn it on). Enabling it asserts the **LAUNCH
  contract**: *distinct `ADS(GLOBAL)`/`ADS(CONSTANT)` buffer parameters of a
  kernel entry do not overlap in memory.* It defaults off and must be
  requested explicitly; see `docs/followups.md` for the rationale and the
  miscompilation hazard it places on the caller.

None of this requires any source-level syntax; it is derived entirely from
the existing parameter declaration and procedure body. The attribute-shape
tests are in `tests/test_kernel_param_attrs.py`; the design/verification
record is the archived "Kernel entries carry no parameter facts" entry in
`docs/old/old-followups.md`.
