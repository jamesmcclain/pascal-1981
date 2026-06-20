# Implementation notes — `DEVICE UNIT` Phase 2.1 (no compiler-inserted runtime checks in device code)

Records the findings, decisions, and musings behind the Phase-2.1 change of
`device-unit-migration-checklist.md` (§2.1.1; §2.1.2 deliberately deferred).
Unlike the Phase-1 review notes, this change **does** touch code; this file is
the rationale companion to that diff.

## Verdict

§2.1.1 is implemented and the green gate holds: a `DEVICE UNIT` **and** a
`DEVICE MODULE` lowered to `nvptx64` now contain **zero** `abort`/`fflush`
references, while host/vintage and `MODULE`-on-host IR are byte-identical to
pre-change (verified by a golden compare). Full suite green (`642 passed, 52
subtests` — the `637` Phase-1 baseline plus the 5 new guard tests).

The change is two source edits behind a single shared predicate, plus one
artifact-level test file.

## 1. (Key finding) The checklist's literal anchor was necessary but **not sufficient**

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

## 2. (Decision) `INITCK` is intentionally **not** suppressed

The checklist's five-flag set omits `INITCK`, and that is correct: `INITCK`
does not lower to a host trap — it zero-initializes otherwise-uninitialized
storage (`decls.py:356`, gated separately and defaulting off anyway). On a GPU
there is no `abort` to avoid, and zero-init is harmless — arguably desirable.
Suppressing it would be a gratuitous behavior change. So
`_HOST_TRAPPING_CHECKS` is exactly `{MATHCK, RANGECK, INDEXCK, NILCK,
STACKCK}`, mirroring the checklist verbatim, and `INITCK` is left to its normal
evaluation.

## 3. (Observation) `STACKCK` is already a no-op on this target

`STACKCK` appears in the suppression set for completeness and fidelity to the
checklist, but it emits nothing today: `compile_to_llvm.py:59` documents it as
an accepted no-op on this target, and there is no `STACKCK` codegen site.
Suppressing it is therefore inert — correct to include (so the set reads as the
checklist specifies and stays right if a stack-check site is ever added), but it
moves no IR today.

## 4. (Decision) `DEVICE MODULE` device IR is covered too — and that is intended

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

## 5. (Musing) The test asserts on the artifact, and only on `abort`/`fflush` — for now

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

## 6. (Musing) Terminology, again

This change adds no user-facing strings, so it does not worsen the
"DEVICE MODULE" vs "DEVICE UNIT" message inconsistency the Phase-1 notes raised
(item 1). But it does reinforce that the underlying concept is **"device
code,"** not a specific compiland kind: the predicate, the flag set, and the
suppression all key off `is_device_module` and apply uniformly to module and
unit. If/when the recission messages are generalized to say "device code"
(the 1.3.5 direction), this predicate's naming (`_device_checks_suppressed`,
already compiland-agnostic) is consistent with that destination.

## Things checked and found OK (so they are not re-litigated later)

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
