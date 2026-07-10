# DEVICE UNIT phase notes (archived)

Rationale companions to `device-unit-migration-checklist.md`. Each phase of the
`DEVICE UNIT` migration had its own notes file; concatenated here in phase order.
The checklist remains the index; this file holds the phase-by-phase findings,
decisions, and musings.

## Review notes — `DEVICE UNIT` Phase 1 (parity with `DEVICE MODULE`)

Reviewer pass over commits `9a30fbe..7f6e070` (the Phase-1 range of
`device-unit-migration-checklist.md`). **No code was changed by this review** — this
file only records findings for the owner to action.

### Verdict

Phase 1 is, on the whole, implemented faithfully and is genuinely tested:

- Parser/AST/EBNF (1.1–1.2), type-checker device context (1.3), codegen gating (1.4),
  the initializer-block ban (1.5), and the primes parity milestone (1.6) are all present.
- The device-context save/restore was factored into shared helpers
  (`type_checker.py:_device_context`, `codegen/decls.py:_device_codegen_context`) as 1.3.1/1.4.1
  asked, so `DEVICE MODULE` and `DEVICE UNIT` stay semantically identical by construction.
- Full suite is green (`637 passed, 52 subtests`).
- The Phase-1.6 parity test (`tests/integration/test_device_primes.py`) actually **builds,
  links, and runs** (it is `@requires_exe` and was observed to *run*, not skip, on this host),
  emitting the expected 25 primes. The codegen parity test
  (`tests/test_device_unit_codegen.py`) asserts on the **emitted IR** (addrspace(1)/addrspace(3),
  `ld`→`st` bridge shape, x86 collapse), which is the durable kind of check.

The items below are the deficiencies / things to watch. None of them blocks Phase 1, but
(1) is a real user-facing defect.

### 1. (Defect) Recission error messages say "DEVICE MODULE" inside a `DEVICE UNIT`

The construct-ban diagnostics are hard-coded to the string "DEVICE MODULE":

- `type_checker.py:163` — `dynamic allocation ('NEW') is not available in a DEVICE MODULE`
- `type_checker.py:166` — `host I/O ('WRITELN') is not available in a DEVICE MODULE`
- `type_checker.py:202` — `recursion is not available in a DEVICE MODULE ...`
- `type_checker.py:997` — `GOTO is not available in a DEVICE MODULE`
- `type_checker.py:1943` — dynamic set-range `... is not available in a DEVICE MODULE`

These same messages now fire for code written in a `DEVICE INTERFACE` / `DEVICE IMPLEMENTATION`
(confirmed: `tests/test_device_unit_typecheck.py` exercises `NEW`/`WRITELN`/recursion in a
`DEVICE IMPLEMENTATION OF` and they are rejected by these exact strings).

Meanwhile the **new** initializer-block ban added in this same phase uses the *other*
terminology:

- `type_checker.py:600` and `:637` — `initializer code is not available in a DEVICE UNIT`

Net effect: compiling a single `DEVICE UNIT` can surface a mix of "... in a DEVICE MODULE" and
"... in a DEVICE UNIT" messages for the same file, naming a construct the user did not write.

Note on intent: checklist §1.3's green gate explicitly asked for "the *same* messages a
`DEVICE MODULE` produces," so reusing the strings was a deliberate parity choice and is not a
deviation from the letter of the checklist. But the checklist did not anticipate that §1.5 would
introduce a competing "DEVICE UNIT" string in the same code path. The terminology is now
internally inconsistent. Suggested follow-up (owner's call): make the recission family say
"device code" / "a device compiland" (matches the 1.3.5 comment that `in_device_module` now
means "in device code"), or thread the actual compiland kind into the message.

### 2. (Observation) Primes parity link relies on `-Wl,--allow-multiple-definition`

`tests/integration/test_device_primes.py` passes `link_flags=['-Wl,--allow-multiple-definition']`.
This is masking the unconditional predeclared-extern dump that Phase 2.2 is meant to remove
(`_register_predeclared_externs` adds the host-runtime set to *every* module, so `kernel.ll` and
`main.ll` both carry the same definitions and would otherwise collide at link time). This is
**acceptable for the Phase-1 parity milestone** — Phase 2.2 is exactly the fix — but it should
not be mistaken for a clean multi-file link, and the flag should be revisited (and ideally
removed) once 2.2/2.2.2 land, or the green link will keep hiding that class of leak.

### 3. (Minor) Unit-level diagnostics carry no source location

The two init-ban calls (`type_checker.py:600`, `:637`) and the device-consistency check
(`:617`, `device-ness of implementation must match its interface`) pass `node=None`, so these
errors have no location. This matches the existing style for unit-level errors in this file
(the surrounding `self.error(..., None)` calls), so it is parity-consistent, not a regression —
recorded only so it is a conscious choice rather than an oversight.

### 4. (Minor) EBNF places `[ "DEVICE" ]` after `[ include_directive ]` for implementations

`docs/ebnf_grammar.md` `implementation_unit` now reads
`[ include_directive ] [ "DEVICE" ] "IMPLEMENTATION" ...`. The parser recognizes the contextual
`DEVICE` only at the *start* of a compilation unit (`parser.py:_at_device_prefix`, dispatched
from `parse_compilation_unit`); a leading `include_directive` before `DEVICE IMPLEMENTATION`
would not reach that dispatch. No test covers an include-prefixed device implementation, so this
ordering in the grammar is currently aspirational rather than exercised. Worth confirming the
grammar matches the parser before relying on it for the separate-compilation include splicing.

### Things checked and found OK (so they are not re-litigated later)

- Contextual-keyword safety preserved: `device` as an ordinary identifier still parses
  (regression tests in `tests/test_ads_space_parse.py`).
- `1.3.4` device-ness consistency works under separate compilation: the interface is reloaded
  from disk via `load_interface` (which goes through `parse_compilation_unit` and re-reads the
  `DEVICE` prefix), so `impl.is_device` is compared against a correctly-populated
  `iface.is_device`.
- The codegen refactor's restore semantics match the original (module triple intentionally not
  restored in `finally` in either the old or new code; `is_device_module` restore is
  equivalent for top-level units where the previous value is `False`).
- Moving the implementation init-body codegen inside the `current_interface_decls` scope in
  `codegen_implementation` is behaviorally inert: init-body **statements** resolve names through
  the codegen `scope`, not `current_interface_decls` (verified by tracing
  `codegen_expr`/`codegen_assign_stmt`), so host implementations with init blocks are unaffected.

## Implementation notes — `DEVICE UNIT` Phase 2.1 (no compiler-inserted runtime checks in device code)

Records the findings, decisions, and musings behind the Phase-2.1 change of
`device-unit-migration-checklist.md` (§2.1.1; §2.1.2 deliberately deferred).
Unlike the Phase-1 review notes, this change **does** touch code; this file is
the rationale companion to that diff.

### Verdict

§2.1.1 is implemented and the green gate holds: a `DEVICE UNIT` **and** a
`DEVICE MODULE` lowered to `nvptx64` now contain **zero** `abort`/`fflush`
references, while host/vintage and `MODULE`-on-host IR are byte-identical to
pre-change (verified by a golden compare). Full suite green (`642 passed, 52
subtests` — the `637` Phase-1 baseline plus the 5 new guard tests).

The change is two source edits behind a single shared predicate, plus one
artifact-level test file.

### 1. (Key finding) The checklist's literal anchor was necessary but **not sufficient**

§2.1.1 says: *"Make `check_enabled` return `False` for
MATHCK/RANGECK/INDEXCK/NILCK/STACKCK when `is_device_module`."* Taken
literally, that is incomplete. There are **two** independent flag-evaluation
paths feeding host-trapping emission, and `check_enabled` is only one of them:

| Check | Emitter | Gated by |
|---|---|---|
| MATHCK (overflow / div-zero) | `_emit_runtime_check` (`base.py`) | `check_enabled('MATHCK')` |
| INDEXCK (array bounds) | `_emit_runtime_check` (`types_map.py:400`) | `check_enabled('INDEXCK')` |
| NILCK (pointer deref) | `_emit_runtime_check` (`types_map.py:470`) | `check_enabled('NILCK')` |
| **RANGECK — CASE no-match trap** | `_emit_case_no_match_trap` (`stmts.py:424`) | **`effective_rangeck` → `effective_flag` (`stmts.py:38`)** |
| **RANGECK — string-capacity guard** | `_guard_string_capacity` (`strings.py:103`) | **`effective_rangeck`, passed as `enabled=`** |
| STACKCK | *(none)* | n/a — see §3 |

`check_enabled` is consulted **only** for the expression-level checks
(MATHCK/INDEXCK/NILCK/INITCK). `RANGECK` never flows through it; it flows
through `effective_flag`/`effective_rangeck`. So gating only `check_enabled`
would have left both RANGECK-driven `abort` sites firing in device code.

This was not a hypothetical: a probe `DEVICE MODULE` with a `CASE` and no
`OTHERWISE`, compiled to `nvptx64`, emitted 3 `abort` + 3 `fflush` refs
**before** the change — and one of those `abort`s came from the CASE trap, i.e.
from the path `check_enabled` does not see. The green gate ("zero abort/fflush")
is what surfaces this; the anchor alone would not have.

**Resolution.** A single shared predicate
`_device_checks_suppressed(flag)` (`codegen/base.py`) returning
`self.is_device_module and flag in _HOST_TRAPPING_CHECKS`, consulted at the top
of **both** `check_enabled` and `effective_flag`. One chokepoint keeps the two
paths from drifting and gives §2.1.2 a single place to swap "elide" for
"on-device `llvm.trap()`" later.

### 2. (Decision) `INITCK` is intentionally **not** suppressed

The checklist's five-flag set omits `INITCK`, and that is correct: `INITCK`
does not lower to a host trap — it zero-initializes otherwise-uninitialized
storage (`decls.py:356`, gated separately and defaulting off anyway). On a GPU
there is no `abort` to avoid, and zero-init is harmless — arguably desirable.
Suppressing it would be a gratuitous behavior change. So
`_HOST_TRAPPING_CHECKS` is exactly `{MATHCK, RANGECK, INDEXCK, NILCK,
STACKCK}`, mirroring the checklist verbatim, and `INITCK` is left to its normal
evaluation.

### 3. (Observation) `STACKCK` is already a no-op on this target

`STACKCK` appears in the suppression set for completeness and fidelity to the
checklist, but it emits nothing today: `compile_to_llvm.py:59` documents it as
an accepted no-op on this target, and there is no `STACKCK` codegen site.
Suppressing it is therefore inert — correct to include (so the set reads as the
checklist specifies and stays right if a stack-check site is ever added), but it
moves no IR today.

### 4. (Decision) `DEVICE MODULE` device IR is covered too — and that is intended

Because the suppression keys purely off `is_device_module` (the cheapest
correct implementation, exactly as §2.1.1 prescribes), it fires for **any**
device compiland — `DEVICE UNIT` and the pre-existing `DEVICE MODULE` alike.
This is not scope creep:

- §2.1's green gate names it explicitly: *"a `DEVICE UNIT` **(or DEVICE
  MODULE)** compiled to `nvptx64` contains zero `abort`/`fflush`."*
- Phase 2's preamble frames these items as leaving "the existing `DEVICE MODULE`
  device-function behavior untouched **unless you choose to apply them there
  too**." The green gate makes that choice; keying off the one boolean honors it
  without special-casing.

The "byte-identical" guarantee was never about device IR — it is about
**host/vintage** and **`DEVICE MODULE`/`MODULE`-on-host** output. Those collapse
through `is_device_module == False` and are provably unchanged (golden compare +
the host-still-traps assertions in the new test). No existing test asserted that
device IR *contains* a trap, so nothing regressed.

### 5. (Musing) The test asserts on the artifact, and only on `abort`/`fflush` — for now

The durable check is `tests/test_device_no_runtime_checks.py`, which compiles
to IR and asserts on the emitted module rather than on the checker — catching
the whole leak class, not just today's three sites. It deliberately scopes its
assertion to `{abort, fflush}`, **not** the wider predeclared-extern family
(`memmove`, `movel`, `fillc`, …). Those still leak unconditionally via
`_register_predeclared_externs` — that is precisely **§2.2's** job, and the
Phase-1 review notes (item 2) already flag the
`-Wl,--allow-multiple-definition` link flag that masks it. The test file leaves
a comment and a `_HOST_TRAP_SYMS` tuple positioned so that, once §2.2 lands, the
guard can be widened to the full set in one edit and will then prove both
"beyond" items at the artifact level simultaneously.

### 6. (Musing) Terminology, again

This change adds no user-facing strings, so it does not worsen the
"DEVICE MODULE" vs "DEVICE UNIT" message inconsistency the Phase-1 notes raised
(item 1). But it does reinforce that the underlying concept is **"device
code,"** not a specific compiland kind: the predicate, the flag set, and the
suppression all key off `is_device_module` and apply uniformly to module and
unit. If/when the recission messages are generalized to say "device code"
(the 1.3.5 direction), this predicate's naming (`_device_checks_suppressed`,
already compiland-agnostic) is consistent with that destination.

### Things checked and found OK (so they are not re-litigated later)

- **Host path byte-identical.** Golden compare of a host `PROGRAM` (with
  MATHCK/INDEXCK/RANGECK/CASE) and a plain `MODULE` body, pristine vs patched
  tree: identical. The new code is unreachable when `is_device_module` is False.
- **Suppression is device-only and observable.** The new test asserts the same
  source still emits `abort`+`fflush` on the host path, so a future regression
  that over-broadens the suppression to host code would fail loudly.
- **`x86` CPU-device still suppresses and still lowers the body.** A
  `DEVICE MODULE` on the default triple is device code, so checks are elided
  there too; the arithmetic/store still lower (just unguarded), confirmed by the
  `mul`-present assertion. This matches GPU reality regardless of triple.
- **No new diagnostics, no parser/AST/grammar surface.** §2.1 is purely a
  codegen-time emission gate; nothing in the front end changed.

## Implementation notes — `DEVICE UNIT` Phase 2.2 (stop dumping predeclared externs unconditionally)

Rationale companion to the Phase-2.2 diff of
`device-unit-migration-checklist.md` (§2.2.1 the gated skip, §2.2.2 the
emitted-IR guard). Builds on the Phase-2.1 tree.

### Verdict

§2.2 is implemented as the checklist's recommended "minimal, green-safe gated
skip": a device compiland (`unit.is_device`) that lowers to a **GPU triple**
no longer registers the host-runtime extern family, so its IR carries **zero**
of `{abort, fflush, memmove, movel, mover, movesl, movesr, fillc, fillsc,
pas_read_int, pas_read_word, pas_read_real}` and **zero** `declare`s. Host,
vintage, plain `MODULE`, **and the x86 CPU-device** outputs are byte-identical
to the Phase-2.1 tree (golden compare). Full suite green (`647 passed, 54
subtests`).

### 1. (Key finding) The dump runs at construction, *before* device-ness is known

`_register_predeclared_externs` is called from `CodegenBase.__init__`
(`base.py:158`), but `is_device_module` is only set later, during
`codegen()`/`_device_codegen_context`. So the literal §2.2.1 phrasing — "skip
... when `is_device_module`" — cannot be evaluated where the dump happens. The
flag simply isn't set yet at `__init__`.

`compile_to_llvm` is the one site that holds **both** signals at once: the AST
(hence `getattr(ast, 'is_device', False)`) and the target `device_triple`. So
the skip decision is computed there and threaded into construction via a new
`skip_host_runtime_externs` constructor parameter:

```
skip = bool(getattr(ast, 'is_device', False)) and _is_gpu_triple(device_triple)
```

This keeps the change minimal and makes the byte-identical guarantee hold **by
construction**: the gate defaults `False`, so every existing call path
(bare `Codegen()`, host `PROGRAM`/`MODULE`, x86-device) registers exactly as
before. Only one new case — a device unit/module on an actual GPU triple —
takes the skip.

### 2. (Decision) The whole host-runtime set is dead in device-GPU code — so skip all of it

Nothing in `_register_predeclared_externs` is reachable from device-GPU code:

- **FILLSC/MOVESL/MOVESR** are intercepted in `codegen_proc_call_stmt`
  (`stmts.py:215`, gated on `is_device_module`) and lowered **inline** by
  `_device_seg_bridge` as addrspace-aware byte loops — never as calls to the
  `movesl`/`movesr`/`fillsc` externs.
- **FILLC/MOVEL/MOVER** are the flat host-ABI siblings; device code uses the
  segmented bridge, not these.
- **pas_read_\*, encode/decode, positn/scaneq, the file-control family** are
  host I/O, which the device recissions forbid outright in the checker.
- **memmove** backs host string ops; **malloc/free** back `NEW`, also rescinded.

So the skip is wholesale rather than a curated subset — simpler, and there is no
device-GPU construct that would need any of them. (Verified empirically: a
device-unit vector-add + `MOVESL` lowered to `nvptx64` references none of the
forbidden set and emits zero `declare`s.)

### 3. (Decision, and the honest caveat) GPU-triple-only — x86 CPU-device deliberately keeps the externs

The skip is scoped to GPU triples (`nvptx*`/`amdgcn*`), exactly as §2.2.1 says
("...and the unit lowers to a GPU triple"). The x86 CPU-device is **not** a GPU
triple: that path compiles device code to a *host-CPU* object that is linked and
run on this machine, and it legitimately links the host runtime. Keeping its
externs is what makes the x86-device output byte-identical to pre-change and is
the green-safe boundary.

**The honest consequence — read this.** The Phase-1 review notes (item 2) hoped
that landing 2.2/2.2.2 would let
`tests/integration/test_device_primes.py` drop its
`-Wl,--allow-multiple-definition` link flag. **It does not — and cannot, with
this gated-skip form.** That test compiles its device side on the **default x86
CPU-device** triple (it must, to actually link and run the primes on this host),
so the skip never fires for it: `kernel.ll` still carries the host-runtime
externs, still collides with `main.ll`'s copies, and still needs the flag. The
flag is therefore left in place, and the Phase-1 note's aspiration is only
realized for *GPU-triple* compiles. Closing the x86-device CPU-link collision is
a different problem that the **wider** §2.2.1 option (lazy/on-demand
registration at first reference) would solve — and that option is explicitly
deferred ("Start with the gated skip"). Flagged here so the link flag is not
mistaken for an oversight, and so the next owner knows the lazy form is the
follow-up that retires it.

### 4. (Refactor) `_is_gpu_triple` is now the single source of truth

The "is this a GPU triple" predicate previously lived inline only in
`_space_addrspace`. It is now a module-level `_is_gpu_triple(triple)` in
`base.py`, used by both `_space_addrspace` (refactored, behavior-preserving) and
the new skip decision in `compile_to_llvm`. One predicate, so addrspace lowering
and extern-skipping can never disagree about what "device GPU" means — the same
single-boolean discipline the rest of the device machinery follows.

### 5. (Musing) The guard test asserts the full set, at the artifact

`tests/test_device_no_host_externs.py` compiles the canonical vector-add
**device unit** (to both `nvptx64` and `amdgcn`) and a single-file **DEVICE
MODULE**, and asserts the emitted IR references **none** of the forbidden set —
the S2.2.2 host-runtime externs **plus** the S2.1 trap pair, so this one file is
now the comprehensive "no host-runtime symbol in device-GPU IR" check. It also
asserts the *negative*: a plain unit and the x86 CPU-device **still** carry the
externs, so a future regression that over-broadened the skip (e.g. to host or to
x86-device) would fail loudly. The S2.1 test
(`tests/test_device_no_runtime_checks.py`) stays as the focused trap guard; its
forward-looking comment about "widening once S2.2 lands" is satisfied by this new
file rather than by editing it.

### Things checked and found OK (so they are not re-litigated later)

- **Byte-identical for everything non-GPU.** Golden compare of a host `PROGRAM`,
  a plain `MODULE` (with `MOVEL`), and a `DEVICE MODULE` on the **x86** device
  triple: Phase-2.1 tree vs Phase-2.2 tree — identical. Phase 2.2 moves only
  device-GPU IR.
- **`_register_predeclared_files` (INPUT/OUTPUT) left untouched.** Those are two
  null `i8*` data globals, not host-runtime function `declare`s; they are not in
  the §2.2.2 forbidden set, and leaving them keeps the change minimal and the
  primes parity milestone undisturbed. Revisit only if a future gate wants
  device IR to carry literally nothing predeclared.
- **Direct-`Codegen` caveat (minor).** The skip is decided in `compile_to_llvm`,
  so a caller that constructs `Codegen(device_triple='nvptx64...')` directly and
  calls `.codegen()` on a device AST would *not* get the skip (it would still
  dump externs). No production or test path does this — all device compiles go
  through `compile_to_llvm` — but it is the one seam the gated-skip form leaves
  open; the deferred lazy-registration form would close it by construction.
- **Suite delta is additive.** `642 -> 647 passed` is exactly the five new
  assertions; the `+2 subtests` are the per-GPU-triple `subTest` loop. No
  existing test changed behavior, including the device-primes integration test
  (still green, still on x86-device, still using its link flag — see §3).

## Plan — the "lazy / full" form of checklist item 2.2.1 (on-demand host-runtime externs)

**Context.** Phase 2.2 shipped the *gated-skip* form of 2.2.1: a device compiland on a
**GPU triple** simply never registers the host-runtime extern family
(the Phase 2.2 notes section above). That is green and correct, but it is scoped to GPU
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

### 0. The shape of the change (one sentence)

Replace the eager `_register_predeclared_externs()` (which creates ~40 `ir.Function`s at
`CodegenBase.__init__`, `base.py:176`) with a **factory registry** built at init (cheap — no
IR), plus a single accessor `runtime_extern(name)` that materializes-and-caches the
`ir.Function` the *first* time codegen actually references it. Every current call site —
`self.scope.lookup('memmove').llvm_value` and its 20 siblings — routes through that accessor.

Because nothing is emitted until referenced, the `skip_host_runtime_externs` constructor flag,
the `_skip_host_runtime_externs` field, and the GPU-triple branch in `compile_to_llvm`
(`__init__.py:89`) all become **dead and get deleted** — the lazy form subsumes the gated skip.

---

### 1. Inventory (re-grep before editing; lines drift)

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

### 2. Implementation steps

#### 2.1 Build the factory registry (replaces the body of `_register_predeclared_externs`)
- Refactor each declaration into a zero-arg factory. Keep the exact `FunctionType`s (don't
  retype them — copy verbatim from the current body so the emitted IR is byte-identical for
  any extern that *is* referenced).
- Store as `self._extern_factories: Dict[str, Callable[[], ir.Function]]`. Many factories share
  derived types (`fcb_ptr`, `ads_ty`, `set_ptr`) — compute those once in a closure-capturing
  scope so the registry build stays cheap and emits **nothing**.
- Call the registry-builder unconditionally from `__init__` (it no longer emits IR, so there is
  nothing to gate).

#### 2.2 Add the lazy accessor
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

#### 2.3 Migrate the 21 call sites
Mechanical: `self.scope.lookup('X').llvm_value` → `self.runtime_extern('X')`. One `edit` per
file with multiple disjoint hunks. Leave genuine *user-symbol* lookups (`INPUT`/`OUTPUT`,
`expr.name`) alone — only the fixed-string runtime-extern lookups move.

#### 2.4 Delete the gated-skip scaffolding (now subsumed)
- `base.py`: drop the `skip_host_runtime_externs` param, the `_skip_host_runtime_externs`
  field, and the `if not skip_host_runtime_externs:` guard (`:175-178`).
- `codegen/__init__.py`: drop the param from `Codegen.__init__` (`:42-44`) and the
  `skip_host_runtime_externs = ... _is_gpu_triple(...)` computation in `compile_to_llvm`
  (`:89-90`).
- Keep `_is_gpu_triple` — `_space_addrspace` still needs it (`base.py:198`).

---

### 3. Tests

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

### 4. The payoff: retire the link flag

Retiring `-Wl,--allow-multiple-definition` (`test_device_primes.py:109`) has **two independent
halves**. Lazy functions (§2) close one; the INPUT/OUTPUT data globals (§4.1) close the other.
Both are needed — they are different collision classes.

#### 4.1 Fix the INPUT/OUTPUT collision (Option 1 — owner-defines, units-declare)

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

#### 4.2 Drop the flag and verify

After §2 (lazy functions) **and** §4.1 (Option 1), regenerate `kernel.ll` + `main.ll` for
`test_device_primes.py` on the x86 CPU-device triple, **drop `-Wl,--allow-multiple-definition`**
(`:109`), and confirm link + run + 25-primes output passes. If any collision survives, dump the
linker's duplicate-symbol name, identify the class (another stray strong def somewhere), and
fix it at the source — do **not** restore the blanket flag. Only if a genuinely intractable
collision remains is the flag left in place, with a one-line note pointing here.

### 5. Green gates (definition of done)

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

## Implementation notes — `DEVICE UNIT` Phase 2.3 (emit entry points, not just device functions)

Rationale companion to the Phase-2.3 diff of
`device-unit-migration-checklist.md` (§2.3.1 export = entry, §2.3.2 kernel
calling convention, §2.3.3 entry-shape rules, §2.3.4 acceptance). Builds on the
Phase-2.1 + 2.2 tree.

### Verdict

§2.3 is implemented. In a `DEVICE UNIT`, each routine the interface exports
lowers to a GPU kernel (`ptx_kernel` / `amdgpu_kernel` calling convention),
which PTX renders as a `.visible .entry`; non-exported implementation routines
stay device-internal `.func`s; a `DEVICE MODULE` (no interface) keeps emitting
plain device functions. Verified end-to-end by emitting **real PTX** and
asserting `.visible .entry`/`.func`. Host, vintage, plain `MODULE`, `DEVICE
MODULE`, and x86 CPU-device IR are byte-identical to the Phase-2.2 tree (golden
compare). Full suite green (`658 passed, 54 subtests`).

### 1. (Key finding) The entry-shape rules cannot live in the checker unconditionally

§2.3.3 calls them "entry-point **checker** rules," but enforcing them in the
type checker as written would reject the existing parity milestone. Two facts
collide:

- The Phase-1.6 device-primes parity unit (`tests/integration/test_device_primes.py`)
  **exports two FUNCTIONs** (`prime_count`, `nth_prime`) and runs on the **x86
  CPU-device**, where returning a value is perfectly fine. A blanket "exported
  device routine must be a PROCEDURE" check would reject it and break a green
  milestone the non-goals forbid disturbing.
- The type checker is **triple-blind**. The device triple is a codegen concern
  (`--device-triple`), and most paths call `PascalTypeChecker().check(ast)`
  *without* a triple, handing it only to `compile_to_llvm` afterward. So the
  checker cannot even tell whether a real GPU entry will be formed.

The shape rules ("must be a PROCEDURE", "params device-passable") only bite when
a routine actually becomes a GPU `.entry` — i.e. device code on a GPU triple.
That is exactly where both the constraint and the triple are known: **codegen**.
So the work is split the way the §2.3.2 caveat already points:

- **Checker** marks *which* routines are exports (`is_exported_entry`), which it
  can do for any unit, triple or not.
- **Codegen** enforces the shape rules and sets the kernel convention, gated on
  `is_device_module and _is_gpu_triple(self.device_triple)`.

On x86 CPU-device the whole thing is inert: exported FUNCTIONs and VAR-param
procedures compile and run serially, so the primes parity port is untouched.
(This mirrors the Phase-2.1/2.2 pattern: the literal checklist anchor needed
adjustment because of *where* the deciding information is available.)

### 2. (Caveat handled exactly as prescribed) Marking survives separate compilation

The §2.3.2 caveat is real: codegen's `current_interface_decls` is only populated
when interface + implementation are parsed together; under normal separate
compilation `unit.interface is None` and codegen cannot see the export list. The
checker, by contrast, loads the interface from disk in `check_implementation_unit`
(`load_interface`).

So — as the checklist recommends — the checker marks `decl.is_exported_entry =
True` on each implementation `ProcDecl`/`FuncDecl` whose name is in the loaded
interface's export list (`InterfaceUnit.params`), and codegen reads that flag.
Codegen does **no** disk I/O and does **not** rely on `current_interface_decls`.
The flag rides the same AST object the checker and codegen share, so it persists
across the phase boundary. A dedicated test compiles an implementation *alone*
and asserts only `vecadd` (exported) is flagged, not `helper`.

### 3. (Decision) Calling convention alone suffices; metadata skipped

§2.3.2 lists `nvvm.annotations` metadata as optional. It is not needed: setting
`func.calling_convention = "ptx_kernel"` already yields a real `.visible .entry`
(confirmed by emitting PTX through llvmlite's NVPTX target — `vecadd` becomes
`.visible .entry vecadd`, `helper` stays `.visible .func helper`). Skipping the
metadata keeps the change minimal and avoids the metadata-API surface. The
convention is chosen off the triple: `amdgpu_kernel` for `amdgcn*`, `ptx_kernel`
otherwise.

### 4. (Decision) Device-passability rule, and its leniency

`_param_device_passable` rejects parameters that lower to an addrspace-0
(host-space) pointer a device entry cannot dereference:

- **reference-mode params** (`VAR`/`CONST`/`VARS`/`CONSTS`) — these are
  host-space pointers by construction;
- **plain `^T` heap / `ADR` pointers**, and **`ADS(HOST)`** (or an ADS with no
  space) — also host-space.

Value scalars and non-HOST `ADS(space) OF T` pass. One deliberate leniency: if
the space ordinal cannot be constant-folded, the param is *allowed* rather than
rejected — the checker has already validated the unit, so a fold hiccup should
not block a compile it passed. The rule is a guard rail (`should`), and erring
toward compiling a checker-valid program is safer than a false reject.

### 5. (Honest note) The rules invalidated one of my own earlier fixtures — as intended

The Phase-2.1 trap test exported `go(VAR x: INTEGER)` purely as a convenient
"device routine." Once §2.3.3 landed, that became an invalid kernel entry (a
`VAR` param is a host-space pointer), and the test's `nvptx64` case correctly
failed. This is the rule working, surfaced by the green gate — not a regression.
The §2.3 diff updates that fixture to a value parameter and points the CASE arms
at the local `y` (value params are immutable in this dialect), which keeps the
test exercising MATHCK/INDEXCK/RANGECK while being a valid entry. The 2.2
vector-add fixture already used a value parameter, so it needed no change.

### Things checked and found OK (so they are not re-litigated later)

- **Byte-identical for everything that isn't an exported GPU routine.** Golden
  compare (Phase-2.2 tree vs Phase-2.3 tree) of a host `PROGRAM`, a plain
  `MODULE`, a `DEVICE MODULE` on `nvptx64`, and a device **unit** on x86: all
  identical. §2.3 moves only the calling convention of exported routines on a
  GPU triple.
- **`DEVICE MODULE` has no entries.** No interface ⇒ nothing exported ⇒ no
  routine is flagged ⇒ no kernel convention even on `nvptx64`. Green gate.
- **Non-exported helpers stay `.func`.** Verified in both IR (no convention) and
  emitted PTX (`.func`, never `.entry`).
- **Both GPU families covered.** `nvptx64` ⇒ `ptx_kernel`; `amdgcn` ⇒
  `amdgpu_kernel`, off the single `_is_gpu_triple` predicate introduced in 2.2.
- **AST field is inert elsewhere.** `is_exported_entry` defaults `False` and is
  only ever set for device units, so host/vintage parsing, checking, and codegen
  are unaffected; the suite delta (`647 -> 658`) is exactly the new acceptance
  assertions.

