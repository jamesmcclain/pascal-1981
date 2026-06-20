# Implementation notes — `DEVICE UNIT` Phase 2.2 (stop dumping predeclared externs unconditionally)

Rationale companion to the Phase-2.2 diff of
`device-unit-migration-checklist.md` (§2.2.1 the gated skip, §2.2.2 the
emitted-IR guard). Builds on the Phase-2.1 tree.

## Verdict

§2.2 is implemented as the checklist's recommended "minimal, green-safe gated
skip": a device compiland (`unit.is_device`) that lowers to a **GPU triple**
no longer registers the host-runtime extern family, so its IR carries **zero**
of `{abort, fflush, memmove, movel, mover, movesl, movesr, fillc, fillsc,
pas_read_int, pas_read_word, pas_read_real}` and **zero** `declare`s. Host,
vintage, plain `MODULE`, **and the x86 CPU-device** outputs are byte-identical
to the Phase-2.1 tree (golden compare). Full suite green (`647 passed, 54
subtests`).

## 1. (Key finding) The dump runs at construction, *before* device-ness is known

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

## 2. (Decision) The whole host-runtime set is dead in device-GPU code — so skip all of it

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

## 3. (Decision, and the honest caveat) GPU-triple-only — x86 CPU-device deliberately keeps the externs

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

## 4. (Refactor) `_is_gpu_triple` is now the single source of truth

The "is this a GPU triple" predicate previously lived inline only in
`_space_addrspace`. It is now a module-level `_is_gpu_triple(triple)` in
`base.py`, used by both `_space_addrspace` (refactored, behavior-preserving) and
the new skip decision in `compile_to_llvm`. One predicate, so addrspace lowering
and extern-skipping can never disagree about what "device GPU" means — the same
single-boolean discipline the rest of the device machinery follows.

## 5. (Musing) The guard test asserts the full set, at the artifact

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

## Things checked and found OK (so they are not re-litigated later)

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
