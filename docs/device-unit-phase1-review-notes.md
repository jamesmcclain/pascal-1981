# Review notes — `DEVICE UNIT` Phase 1 (parity with `DEVICE MODULE`)

Reviewer pass over commits `9a30fbe..7f6e070` (the Phase-1 range of
`device-unit-migration-checklist.md`). **No code was changed by this review** — this
file only records findings for the owner to action.

## Verdict

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

## 1. (Defect) Recission error messages say "DEVICE MODULE" inside a `DEVICE UNIT`

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

## 2. (Observation) Primes parity link relies on `-Wl,--allow-multiple-definition`

`tests/integration/test_device_primes.py` passes `link_flags=['-Wl,--allow-multiple-definition']`.
This is masking the unconditional predeclared-extern dump that Phase 2.2 is meant to remove
(`_register_predeclared_externs` adds the host-runtime set to *every* module, so `kernel.ll` and
`main.ll` both carry the same definitions and would otherwise collide at link time). This is
**acceptable for the Phase-1 parity milestone** — Phase 2.2 is exactly the fix — but it should
not be mistaken for a clean multi-file link, and the flag should be revisited (and ideally
removed) once 2.2/2.2.2 land, or the green link will keep hiding that class of leak.

## 3. (Minor) Unit-level diagnostics carry no source location

The two init-ban calls (`type_checker.py:600`, `:637`) and the device-consistency check
(`:617`, `device-ness of implementation must match its interface`) pass `node=None`, so these
errors have no location. This matches the existing style for unit-level errors in this file
(the surrounding `self.error(..., None)` calls), so it is parity-consistent, not a regression —
recorded only so it is a conscious choice rather than an oversight.

## 4. (Minor) EBNF places `[ "DEVICE" ]` after `[ include_directive ]` for implementations

`docs/ebnf_grammar.md` `implementation_unit` now reads
`[ include_directive ] [ "DEVICE" ] "IMPLEMENTATION" ...`. The parser recognizes the contextual
`DEVICE` only at the *start* of a compilation unit (`parser.py:_at_device_prefix`, dispatched
from `parse_compilation_unit`); a leading `include_directive` before `DEVICE IMPLEMENTATION`
would not reach that dispatch. No test covers an include-prefixed device implementation, so this
ordering in the grammar is currently aspirational rather than exercised. Worth confirming the
grammar matches the parser before relying on it for the separate-compilation include splicing.

## Things checked and found OK (so they are not re-litigated later)

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
</content>
</invoke>
