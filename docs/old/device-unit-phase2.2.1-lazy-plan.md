# Plan — the "lazy / full" form of checklist item 2.2.1 (on-demand host-runtime externs)

**Context.** Phase 2.2 shipped the *gated-skip* form of 2.2.1: a device compiland on a
**GPU triple** simply never registers the host-runtime extern family
(`device-unit-phase2.2-notes.md` §1). That is green and correct, but it is scoped to GPU
triples by construction, and the migration checklist plus the phase-2.2 notes (§3) both flag
the **wider "lazy" form** — *register each extern on first reference* — as the deferred
follow-up that:

1. makes the "no dead host-runtime declares" property hold for **every** compile (host,
   vintage, plain `MODULE`, x86 CPU-device, GPU-device) — not just device-GPU, because an
   extern that is never referenced is never emitted, full stop; and
2. is the prerequisite for **retiring `-Wl,--allow-multiple-definition`** from
   `tests/integration/test_device_primes.py:109` — the x86 CPU-device link path that the
   gated skip cannot reach (`phase2.2-notes.md` §3).

This is the *better* version: instead of a triple-conditional skip, the dump simply stops
existing as an eager step. "Dead extern" becomes structurally impossible.

---

## 0. The shape of the change (one sentence)

Replace the eager `_register_predeclared_externs()` (which creates ~40 `ir.Function`s at
`CodegenBase.__init__`, `base.py:176`) with a **factory registry** built at init (cheap — no
IR), plus a single accessor `runtime_extern(name)` that materializes-and-caches the
`ir.Function` the *first* time codegen actually references it. Every current call site —
`self.scope.lookup('memmove').llvm_value` and its 20 siblings — routes through that accessor.

Because nothing is emitted until referenced, the `skip_host_runtime_externs` constructor flag,
the `_skip_host_runtime_externs` field, and the GPU-triple branch in `compile_to_llvm`
(`__init__.py:89`) all become **dead and get deleted** — the lazy form subsumes the gated skip.

---

## 1. Inventory (re-grep before editing; lines drift)

- **Eager dump to convert:** `_register_predeclared_externs` (`base.py:217`–~`365`). Every
  `ir.Function(...) ; fn.linkage='external' ; self.scope.define(name, fn, None)` triple becomes
  one entry in the factory registry.
- **The 21 reference sites** (all of form `self.scope.lookup('<name>').llvm_value`):
  - `strings.py:244,270` (`memmove`), `:280` (`positn`), `:328` (`encode_value`),
    `:364` (`decode_value`)  — plus `scaneq`/`scanne` near there (re-grep).
  - `runtime_builtins.py:114` (`malloc`), `:131` (`free`).
  - `files.py:54` (`pas_file_touch_buffer`), `:56` (`pas_file_buffer`).
  - `io_write_read.py:85` (`pas_file_attach_std`), `:218,231` (`pas_write_fmt`),
    `:306` (`pas_fread_lstring`), `:318` (`pas_fread_string`), `:344` (`pas_freadset`),
    `:361` (`pas_fread_filename`), `:377` (`pas_freadln_skip`).
  - `exprs.py:122,487` (`pas_file_attach_std`), `:123,488` (`pas_file_eof`/`pas_file_eoln`).
  - the seg-bridge family (`fillc/fillsc/movel/mover/movesl/movesr`): confirm whether any flat
    variant is still referenced by host code (re-grep `lookup('move` / `lookup('fill`); the
    segmented variants are intercepted inline by `_device_seg_bridge` and may have **zero**
    remaining lookups — they are still declared eagerly today, so they will simply never
    materialize under lazy, which is the whole point).
- **Untouched:** `_register_predeclared_files` (INPUT/OUTPUT globals) and `file_fcb_type` —
  handled separately in §4.1 (owner-defines/units-declare), a different collision class.

---

## 2. Implementation steps

### 2.1 Build the factory registry (replaces the body of `_register_predeclared_externs`)
- Refactor each declaration into a zero-arg factory. Keep the exact `FunctionType`s (don't
  retype them — copy verbatim from the current body so the emitted IR is byte-identical for
  any extern that *is* referenced).
- Store as `self._extern_factories: Dict[str, Callable[[], ir.Function]]`. Many factories share
  derived types (`fcb_ptr`, `ads_ty`, `set_ptr`) — compute those once in a closure-capturing
  scope so the registry build stays cheap and emits **nothing**.
- Call the registry-builder unconditionally from `__init__` (it no longer emits IR, so there is
  nothing to gate).

### 2.2 Add the lazy accessor
```python
def runtime_extern(self, name: str) -> ir.Function:
    sym = self.scope.lookup(name)          # already materialized?
    if sym is not None:
        return sym.llvm_value
    fn = self._extern_factories[name]()    # create the ir.Function now
    fn.linkage = 'external'
    self.scope.define(name, fn, None)      # cache so the next ref reuses it
    return fn
```
Idempotent and self-caching: a second reference finds it via `scope.lookup` and never re-creates
it (so no duplicate `declare`). Define at the **root** scope (where the eager dump defined them)
so nested function scopes resolve it identically — confirm the define target matches the old
behavior (the eager path used `self.scope` at `__init__`, i.e. the module root).

### 2.3 Migrate the 21 call sites
Mechanical: `self.scope.lookup('X').llvm_value` → `self.runtime_extern('X')`. One `edit` per
file with multiple disjoint hunks. Leave genuine *user-symbol* lookups (`INPUT`/`OUTPUT`,
`expr.name`) alone — only the fixed-string runtime-extern lookups move.

### 2.4 Delete the gated-skip scaffolding (now subsumed)
- `base.py`: drop the `skip_host_runtime_externs` param, the `_skip_host_runtime_externs`
  field, and the `if not skip_host_runtime_externs:` guard (`:175-178`).
- `codegen/__init__.py`: drop the param from `Codegen.__init__` (`:42-44`) and the
  `skip_host_runtime_externs = ... _is_gpu_triple(...)` computation in `compile_to_llvm`
  (`:89-90`).
- Keep `_is_gpu_triple` — `_space_addrspace` still needs it (`base.py:198`).

---

## 3. Tests

- **Keep `tests/test_device_no_host_externs.py` green unchanged** — device-GPU IR still carries
  none of the forbidden set (now because nothing references them, not because of a skip). Its
  *negative* assertions need a look: today it asserts a plain unit / x86-device **still carries
  the externs**. Under lazy registration that is **no longer true** for a file that doesn't
  reference them — those negative assertions must be **rewritten** to "x86-device IR references
  exactly the externs its body actually uses" (e.g. a unit that does a `MOVEL` carries `movel`
  and nothing else). This is the one test that *must* change, and the change is the proof the
  lazy form is wider than the skip.
- **New positive test:** a host `PROGRAM` that uses *no* strings/heap/file-IO emits **zero**
  host-runtime `declare`s (previously impossible — the eager dump always added ~40). This is the
  durable artifact-level guard for property (1).
- **Byte-identical golden compare** for any program that *does* exercise each extern: pick
  representative host programs (string ops → `memmove`/`positn`; `NEW` → `malloc`; `WRITELN` →
  `pas_write_fmt`; `READLN` → `pas_read_*`) and confirm the emitted IR for the referenced
  externs is identical to the pre-change tree. The *ordering* of `declare`s in the module may
  shift (lazy emits them in first-reference order, not dump order) — if the golden compare is
  textual, either sort declares or assert set-equality rather than line-equality. Decide this
  up front; it is the most likely source of spurious golden diffs.

## 4. The payoff: retire the link flag

Retiring `-Wl,--allow-multiple-definition` (`test_device_primes.py:109`) has **two independent
halves**. Lazy functions (§2) close one; the INPUT/OUTPUT data globals (§4.1) close the other.
Both are needed — they are different collision classes.

### 4.1 Fix the INPUT/OUTPUT collision (Option 1 — owner-defines, units-declare)

`_register_predeclared_files` (`base.py:253`) emits, in **every** compiland:

```llvm
@output = global i8* null
@input  = global i8* null
```

That `global ... null` (no `external`, no `common`) is a **strong definition**. Two compilands
linked together → two strong defs of `input`/`output` → a real multiple-definition collision
(verified). This — not the function externs — is the data-global collision the phase-2.2 notes
flagged, and the actual reason the integration test carries the flag.

INPUT and OUTPUT are **program-wide singletons**: in separate-compilation Pascal they are owned
once by the program and *referenced* by units. Codegen already knows which compiland it is — the
top-level AST is a `Program`/launchable `ModuleUnit` vs an `Interface`/`ImplementationUnit`. So:

- **Root compiland** (`Program`, and the launchable `MODULE`): emit the strong definition
  exactly as today (`@output = global i8* null`).
- **Any `UNIT`** (`InterfaceUnit` / `ImplementationUnit`): emit a **declaration only** —
  `gv = ir.GlobalVariable(...); gv.linkage = 'external'` and **do not** set an initializer →
  `@output = external global i8*`. Still `scope.define`d under the same name/type, so every
  reference site resolves unchanged; only the linkage/initializer differ.

One definition program-wide; every unit resolves to it. No link flag, and it models the
language's real ownership semantics (so a *genuine* future duplicate-symbol bug still surfaces
instead of being swallowed by the blanket flag).

**Plumbing.** Thread an `is_root_compiland: bool` signal into `_register_predeclared_files`
(or read it off the already-available top-level AST node kind). `compile_to_llvm` is again the
clean site that holds the AST — set it there, mirroring how `skip_host_runtime_externs` was
computed (which §2.4 deletes). Default the flag so that any direct/legacy `Codegen()` caller
and all host single-file compiles stay **root** → byte-identical strong definitions; only `UNIT`
compilands flip to declare-only.

**Considered and rejected:** `common` linkage (one-line, coalesces automatically) — works, but
less honest about ownership and weaker as a future-bug tripwire; keep it only as a fallback if
the `is_root_compiland` plumbing proves awkward. `weak`/`linkonce` — wrong tool (these symbols
are not override-able). Option 1 is the recommended path.

### 4.2 Drop the flag and verify

After §2 (lazy functions) **and** §4.1 (Option 1), regenerate `kernel.ll` + `main.ll` for
`test_device_primes.py` on the x86 CPU-device triple, **drop `-Wl,--allow-multiple-definition`**
(`:109`), and confirm link + run + 25-primes output passes. If any collision survives, dump the
linker's duplicate-symbol name, identify the class (another stray strong def somewhere), and
fix it at the source — do **not** restore the blanket flag. Only if a genuinely intractable
collision remains is the flag left in place, with a one-line note pointing here.

## 5. Green gates (definition of done)

- Full suite green (`PYTHONPATH=src python3 -m pytest tests/ -q`), with
  `test_device_no_host_externs.py`'s negative assertions rewritten per §3.
- Host/vintage/`MODULE`/`DEVICE MODULE`/device-GPU IR for any program that references a given
  extern is byte-identical (modulo declare ordering, §3) to the pre-change tree.
- A host program that references no host-runtime extern emits zero of them (new guard).
- `skip_host_runtime_externs` and its `compile_to_llvm` GPU-triple branch are **deleted**, not
  merely bypassed.
- INPUT/OUTPUT use owner-defines/units-declare linkage (§4.1): a non-root `UNIT` emits
  `@input`/`@output` as `external global` (declare-only), the root compiland keeps the strong
  definition. New multi-file test: link two compilands and assert exactly one strong def of
  each.
- `test_device_primes.py`'s `-Wl,--allow-multiple-definition` is **dropped** and the test is
  green without it (§4.2). If any collision survives, the offending symbol is identified and
  fixed (or documented with the flag left in place and a one-line note pointing here) — but with
  lazy functions + Option 1 there should be none.
```
