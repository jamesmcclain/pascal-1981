# Discrepancy Remediation Plan — Pascal-1981 vs IBM Pascal 2.0

Importance-ordered remediation plan for the differential discrepancies recorded
in `docs/discrepancies.md`, derived from the `t003`–`t036` probe campaign
(`probe-campaign.md`). One behavior per item, mirroring the log's convention.

Source of truth for *vintage behavior* is the discrepancies log; every vintage
output / error code below is quoted from a probe run already graded `[OBSERVED]`
there. Source of truth for *modern code locations* is a direct read of the
`probes-branch` tree (also `[OBSERVED]` — line numbers are `≈` and may drift, so
each pointer also names a stable function/string anchor). Priority ordering and
fix approaches are **RECOMMENDATIONS** (my judgment), not established fact, and
items needing a maintainer ruling are tagged `STATUS: DECISION-NEEDED` rather
than presented as settled.

---

## ANCHOR: RM-GREP  —  how to grep this document

Every actionable item has a one-line unique anchor of the form `ANCHOR: RM-...`.
It appears exactly twice: once in the index table, once at the item body. To
jump to an item's full entry, grep its anchor and take the second hit, e.g.:

```
grep -n 'ANCHOR: RM-P0-BOOL' remediation-plan.md      # locate
grep -A 16 'ANCHOR: RM-P0-BOOL$' remediation-plan.md  # read full entry
```

Other greppable axes (stable tokens, one per line in item bodies):

```
grep 'PRIORITY: P0'        remediation-plan.md   # all P0 items
grep 'STATUS: DECISION'    remediation-plan.md   # everything blocked on a ruling
grep 'STATUS: TODO-FIX'    remediation-plan.md   # ready-to-implement fixes
grep 'STATUS: RECORD-ONLY' remediation-plan.md   # bookkeeping (no behavior change)
grep 'STATUS: NO-ACTION'   remediation-plan.md   # do NOT "fix" these
grep 'STATUS: INVESTIGATE' remediation-plan.md   # re-probe / redesign tasks
grep 'EFFORT: L'           remediation-plan.md   # large items
grep 'TOUCH:'              remediation-plan.md   # every file an item touches
grep 'VERIFY:'            remediation-plan.md    # the probe that closes each item
grep '^- D-0'              remediation-plan.md    # index rows by D-number
```

Per-item field labels are fixed: `PRIORITY:`, `STATUS:`, `D-ENTRY:`, `CLASS:`,
`BASIS:`, `VINTAGE:`, `MODERN-NOW:`, `TOUCH:`, `ACTION:`, `DECISION:`,
`EFFORT:`, `RISK-IF-SKIPPED:`, `VERIFY:`, `UPGRADES:`, `XREF:`.

`BASIS:` grades the *vintage* claim the fix targets: `OBSERVED` (probe output),
`INFERRED` (mechanism deduced from output), `UNVERIFIED` (not established by any
probe). `EFFORT:` is S / M / L (engineering size, not importance).

---

## ANCHOR: RM-INDEX  —  importance-ordered index

Tiers are correctness-first: P0 = silent or visible wrongness on ordinary valid
code; P1 = acceptance/format fidelity on features modern already supports; P2 =
compile-blocking feature gaps (loud failures, no corruption); P3 = error-handling
behavioral divergence; P4 = diagnostic-only (semantics already match); P5 =
conditional-compilation skipper (niche); X = cross-cutting; NO-ACTION =
documented-correct; OPEN = re-probe.

| Anchor | D | Prio | Status | One-line | Effort |
|---|---|---|---|---|---|
| RM-P0-BOOL | D-020 | P0 | TODO-FIX | BOOLEAN WRITE prints raw bytes, not `TRUE`/`FALSE` | S |
| RM-P0-CASE | D-028 | P0 | TODO-FIX | CASE no-match silently falls through; vintage traps | S |
| RM-P1-INTPN | D-010 | P1 | DECISION-NEEDED | integer `::N` silently accepted; vintage runtime-rejects | S |
| RM-P1-ENUMWRITE | D-019 | P1 | DECISION-NEEDED | enum WRITE prints name; vintage prints ordinal | S |
| RM-P2-WORD | D-032 | P2 | TODO-FIX | WORD assign/convert (`WRD`,`MAXWORD`) rejected | M |
| RM-P2-PACK | D-031 | P2 | TODO-FIX | `PACK`/`UNPACK` + packed-char-array WRITE rejected | M-L |
| RM-P2-NULL | D-033 | P2 | TODO-FIX | `NULL` LSTRING constant + `.LEN` field rejected | M |
| RM-P2-ENUMREAD | D-030/D-006 | P2 | TODO-FIX | enum READ (numeric ordinal) rejected | M |
| RM-P2-SETCTOR | D-026 | P2 | TODO-FIX | type-prefixed set ctor `COLORS[..]` rejected; unblocks t022 | M |
| RM-P3-READTRAP | D-013 | P3 | TODO-FIX | malformed formatted READ aborts; vintage traps (code 14) | M |
| RM-P3-ERRSCODE | D-012 | P3 | TODO-FIX | `F.ERRS` returns invented code; vintage = 10 on RESET-missing | S |
| RM-P4-PUTCODE | D-005 | P4 | RECORD-ONLY | PUT-after-GET: record vintage op-error code 1110 | S |
| RM-P4-WRITECODE | D-024 | P4 | RECORD-ONLY | WRITE-in-inspection-mode: record code 1104 | S |
| RM-P4-NILCODE | D-015 | P4 | RECORD-ONLY | NIL deref: record code 2031 (+ optional flush) | S |
| RM-P5-DUPELSE | D-003 | P5 | DECISION-NEEDED | duplicate `$ELSE`: modern `A`, vintage `A C` | S |
| RM-P5-SKIPQUOTE | D-004 | P5 | RECORD-ONLY | `{` in skipped-`$IF` string: keep modern fix, document | S |
| RM-XCUT-IOERR | — | X | TODO-FIX | renumber `io_error` table to vintage codes (gates D-005/12/13/24) | M |
| RM-XCUT-FLUSH | — | X | TODO-FIX | flush stdout before modern abort (test fidelity, D-005/15/16) | S |
| RM-XCUT-ENUMBOOL | — | X | DECISION-NEEDED | BOOLEAN-names vs enum-ordinal: do NOT unify (D-019 vs D-020) | S |
| RM-NOACTION | D-014/16/17 + baselines | — | NO-ACTION | width adaptations + AGREE-ACCEPT; do not change | — |
| RM-OPEN-T021 | t021 | OPEN | INVESTIGATE | `ORD(EOL)` verdict `[UNVERIFIED]`; rerun | S |
| RM-OPEN-T022 | t022 | OPEN | INVESTIGATE | `READSET` delimiter retention; redesign (needs RM-P2-SETCTOR) | M |

Sequencing note: P0 → P1 are cheap, high-value, and should land first. Within
P2 the items are largely independent and may be resequenced by team preference;
the recommended order is most-fundamental-type-first (WORD, PACK, NULL/LSTRING)
ahead of the narrower input/grammar gaps (enum READ, set ctor), **except** that
`RM-P2-SETCTOR` (D-026) is the prerequisite for closing `RM-OPEN-T022` and may
be pulled forward if that open item matters. `RM-XCUT-IOERR` should land before
or with D-012/D-013 since all three touch the same table.

---

# P0 — Silent or visible wrongness on ordinary valid code

## ANCHOR: RM-P0-BOOL
**BOOLEAN WRITE emits raw storage bytes instead of `TRUE`/`FALSE`.**
- PRIORITY: P0 (highest — visible corruption, high-frequency operation, trivial fix)
- STATUS: TODO-FIX
- D-ENTRY: D-020
- CLASS: OUTPUT-DIFF
- BASIS: OBSERVED (vintage `TRUE` / `FALSE` printed in t020)
- VINTAGE: prints `TRUE` then `FALSE`; uppercase, no leading pad observed in this capture `[OBSERVED]`. Field-width-with-padding is `[UNVERIFIED]` (not probed).
- MODERN-NOW: prints the raw byte `\x01` / `\x00` `[OBSERVED]`. Known latent defect, deliberately left unfixed pending this probe.
- TOUCH: `codegen/io_write_read.py` → `build_write_format_and_args` (≈L146-150). A loaded BOOLEAN is LLVM `i8` (`codegen/types_map.py` maps `BOOLEAN → ir.IntType(8)`, ≈L31/L63, comment: one byte so adr/sizeof/fillc agree). The dispatch hits `if str(val.type) == 'i8': conv = 'c'` → `%c` → prints the byte. Root cause confirmed by reading the source `[OBSERVED]`; the connection to t020's output is `[INFERRED]` but the log already records the raw-byte output directly.
- ACTION: in the WRITE format builder, branch on the *Pascal* type before the LLVM-type dispatch: if `pas_ty` is `BOOLEAN`, select `"TRUE"`/`"FALSE"` (e.g. a 2-entry name table indexed by the value, mirroring the existing `enum_name_table` path at ≈L99-104, or a `select` between two string globals) and format with `%s`. **Do the fix in WRITE lowering, not in the type map** — `BOOLEAN` is intentionally one byte for layout/`ADR`/`fillc`; changing it to `i1` would break those. See RM-XCUT-ENUMBOOL: BOOLEAN must print *names* even though user enums (D-019) print *ordinals*; do not route BOOLEAN through whatever ordinal path D-019 lands on.
- EFFORT: S
- RISK-IF-SKIPPED: every program that writes a BOOLEAN emits unprintable control bytes — corrupts all boolean console/file output. High and pervasive.
- VERIFY: recompile + run `t020.pas`; expect exactly `TRUE` then `FALSE`. Add a field-width probe later only if padding turns out to matter.
- UPGRADES: checklist 9.8 (boolean WRITE) → vintage-confirmed format.
- XREF: D-020; RM-XCUT-ENUMBOOL; RM-P1-ENUMWRITE.

## ANCHOR: RM-P0-CASE
**CASE with no matching arm and no `OTHERWISE` silently falls through; vintage traps.**
- PRIORITY: P0 (silent control-flow corruption — more dangerous than a loud error because nothing signals it)
- STATUS: TODO-FIX
- D-ENTRY: D-028
- CLASS: OUTPUT-DIFF
- BASIS: OBSERVED (code value 2050) + READ (manual src ≈9953 documents the trap under `$RANGECK`)
- VINTAGE: prints `BEFORE`, then runtime error `? Error: No CASE Value Matches Selector` / `Error Code 2050`; confirms `$RANGECK` is ON by default `[OBSERVED]`.
- MODERN-NOW: prints `BEFORE` and `AFTER` — silent fall-through `[OBSERVED]`.
- TOUCH: `codegen/stmts.py` → `codegen_case_stmt` (≈L394-430). With `stmt.otherwise is None` and no match, control branches straight to `end_block` (≈L425-429). The flag accessor `effective_rangeck(stmt)` already exists (≈L52-54).
- ACTION: when `stmt.otherwise is None` **and** `effective_rangeck(stmt)` is true, replace the fall-through-to-`end_block` with a trap: emit a call to the existing runtime abort and mark `unreachable`. Model: `_emit_runtime_check` in `codegen/base.py` (≈L136-148), which calls `runtime_error_func()` then `self.builder.unreachable()`; or `pascal_abort_func`/`pabort(msg,len,code,status)` in `codegen/runtime_builtins.py` (≈L46-52) if a distinct code/message is wanted to mirror vintage 2050. When `$RANGECK-`, keep current silent fall-through (matches the switch-disables-checking model used elsewhere, cf. D-017).
- EFFORT: S
- RISK-IF-SKIPPED: a ported program that relies on the no-match trap (or that has an unhandled selector value) silently continues past the CASE with whatever state it had — wrong results, no diagnostic. High because invisible.
- VERIFY: recompile + run `t028.pas`; expect `BEFORE` then a non-zero abort (no `AFTER`). Confirm `t035.pas` (explicit `OTHERWISE`) and any matching-arm CASE still pass — the trap must fire ONLY on the no-match-and-no-OTHERWISE path under default checking.
- UPGRADES: checklist runtime CASE semantics; `$RANGECK` default-on confirmed.
- XREF: D-028; D-035 (OTHERWISE grammar, already AGREE-ACCEPT); `$RANGECK`.

---

# P1 — Acceptance / format fidelity on already-supported features (need a ruling)

## ANCHOR: RM-P1-INTPN
**Integer `::N` (precision) is silently accepted and ignored; vintage rejects it at runtime.**
- PRIORITY: P1
- STATUS: DECISION-NEEDED
- D-ENTRY: D-010
- CLASS: OUTPUT-DIFF (both compile; vintage errors at runtime, modern runs)
- BASIS: OBSERVED (vintage data-format error code 1123)
- VINTAGE: compiled and linked, then failed at runtime: `? Error: Data format error in file USER` / `Error Code 1123` `[OBSERVED]`.
- MODERN-NOW: accepted `WRITELN(x::4)` and printed `42`, precision operand silently dropped `[OBSERVED]`.
- TOUCH: `codegen/io_write_read.py` → integer branch of `build_write_format_and_args` (≈L146-164). Contrast: REAL `::N` IS honored (≈L128-144, the D-002 fixed-point path); STRING `::N` is accepted-and-ignored on BOTH sides (t011, AGREE-ACCEPT) so string is intentionally lenient.
- DECISION: choose fidelity vs leniency. (a) **Match vintage** — make integer `::N` an error. Vintage makes it a *runtime* data-format error (1123); a compile-time rejection is simpler and arguably better but is a stricter divergence — pick one and document it. (b) **Keep leniency** — document modern integer `::N` as a deliberate accepted extension. Note the asymmetry to preserve: REAL `::N` is meaningful, STRING `::N` is ignored-by-agreement, INTEGER `::N` is the only contested case.
- ACTION (if 'match'): reject integer `::N` — preferred at typecheck via the `io_data_param` path; if matching vintage's runtime-error timing is required, emit the data-format error at the write site instead.
- EFFORT: S
- RISK-IF-SKIPPED: a ported program using integer `::N` that the vintage runtime would have rejected instead runs silently on modern — masks a real defect in the ported source. Medium (labeled high in the log for the accept-vs-reject fidelity gap).
- VERIFY: re-run `t010.pas`. If 'match' chosen: modern must error (runtime 1123-equivalent, or a documented compile error). If 'keep': record the extension and leave t010 as a documented OUTPUT-DIFF.
- UPGRADES: checklist 8.3 / I/O formatting; EBNF `io_data_param`.
- XREF: D-010; D-002 (REAL `::N`); t011 (STRING `::N`, AGREE-ACCEPT).

## ANCHOR: RM-P1-ENUMWRITE
**Enum WRITE prints the symbolic name; vintage prints the ordinal.**
- PRIORITY: P1
- STATUS: DECISION-NEEDED
- D-ENTRY: D-019
- CLASS: OUTPUT-DIFF
- BASIS: INFERRED (the modern symbolic-name output is an extension with no vintage or manual basis; vintage ordinal is `[OBSERVED]`)
- VINTAGE: `WRITELN(x)` with `x = GREEN` printed `1` (the ordinal) `[OBSERVED]`.
- MODERN-NOW: printed `GREEN` (the symbolic name) `[OBSERVED]` — feature built `[INFERRED]` in checklist 9.8 without manual basis.
- TOUCH: `codegen/io_write_read.py` → `write_enum_names` / `enum_name_table` path (≈L99-104). To match vintage, *skip* the name table for user enums and emit the ordinal (it is already the underlying integer; format as the enum's base width).
- DECISION: fidelity vs ergonomics. (a) **Match vintage** — emit ordinal `1`. (b) **Keep symbolic names** as a documented extension (more readable, but a real output divergence on every enum write). This is a product decision, not a clear bug.
- ACTION (if 'match'): in the enum branch, drop the `enum_name_table` lookup for user-declared enums and let the value fall through to the integer formatter. **Critical interaction (RM-XCUT-ENUMBOOL):** BOOLEAN must still print `TRUE`/`FALSE` per RM-P0-BOOL — do not collapse BOOLEAN and user-enum WRITE into one ordinal path. Treat BOOLEAN as a named-output special case; user enums as ordinal.
- EFFORT: S
- RISK-IF-SKIPPED: any enum-writing program produces different text than vintage (names vs numbers) — pervasive output diff if byte-fidelity matters. Medium.
- VERIFY: re-run `t019.pas`. If 'match': expect `1`. If 'keep': record the extension; leave t019 as a documented OUTPUT-DIFF. Re-run RM-P0-BOOL's `t020.pas` to confirm BOOLEAN still prints `TRUE`/`FALSE` after whatever change lands here.
- UPGRADES: checklist 9.8 / enum WRITE (currently `[INFERRED]`).
- XREF: D-019; RM-P0-BOOL; RM-XCUT-ENUMBOOL.

---

# P2 — Compile-blocking feature gaps (modern rejects valid vintage programs)

These fail loudly at typecheck (no silent corruption) but block real vintage
programs from compiling at all. All confirmed: vintage compiles+links+runs;
modern rejects at typecheck. Items are largely independent.

## ANCHOR: RM-P2-WORD
**WORD assignment / conversion (`WRD`, `MAXWORD`) rejected at typecheck.**
- PRIORITY: P2
- STATUS: TODO-FIX
- D-ENTRY: D-032
- CLASS: ACCEPT/REJECT
- BASIS: OBSERVED (vintage output `65535`, `40000`, `65535`)
- VINTAGE: compiled (3 `Assumed OUTPUT` warnings), linked, ran; `MAXWORD` = `65535`, `w := 40000` prints `40000`, `WRD(-1)` = `65535` — 16-bit unsigned, `WRD(-1)` wraps to max `[OBSERVED]`.
- MODERN-NOW: rejected at typecheck: `Cannot assign INTEGER to WORD` `[OBSERVED]`.
- TOUCH: `type_checker.py` — `WRD` handling at ≈L1824-1839 (currently errors on unsupported arg types); the INTEGER→WORD assignment-compatibility rule (search `Cannot assign` / WORD coercion). WORD already maps to `ir.IntType(16)` in `codegen/types_map.py` (≈L33/L65), so the storage exists; the gap is the typecheck assignment/conversion path and `WRD`/`MAXWORD` lowering.
- ACTION: allow INTEGER→WORD assignment/conversion with 16-bit unsigned semantics; implement `WRD(x)` as conversion-to-WORD with wraparound (`WRD(-1) = 65535`); ensure `MAXWORD` = 65535. WORD is already displayed via the `i16 → %u` branch in the WRITE builder, so display should follow once assignment lands.
- EFFORT: M
- RISK-IF-SKIPPED: any program using WORD arithmetic/assignment won't compile. Medium (loud).
- VERIFY: re-run `t032.pas`; expect `65535`, `40000`, `65535`.
- UPGRADES: checklist 4.9 / WORD, MAXWORD, WRD.
- XREF: D-032.

## ANCHOR: RM-P2-PACK
**`PACK`/`UNPACK` and packed-char-array WRITE rejected.**
- PRIORITY: P2
- STATUS: TODO-FIX
- D-ENTRY: D-031
- CLASS: ACCEPT/REJECT
- BASIS: OBSERVED (vintage output `BCD` then `..BCD.`)
- VINTAGE: compiled (3 `Assumed OUTPUT` warnings), linked, ran; `PACK(a,2,z)` then `WRITELN(z)` → `BCD`; `UNPACK(z,b,3)` round-trip → `..BCD.`; uses the expected index convention `[OBSERVED]`.
- MODERN-NOW: rejected at typecheck: `WRITE argument 1 has unwritable type PACKED ARRAY[1..3] OF CHAR` `[OBSERVED]`.
- TOUCH: `type_checker.py` — `PACK`/`UNPACK` arg checks at ≈L929-933 and `_check_pack_args` at ≈L1208+; the packed-array WRITE rejection at ≈L1080 (`unwritable type`). Two sub-gaps: (1) `PACK`/`UNPACK` semantics + index base, (2) writing a packed char array as a string.
- ACTION: implement `PACK`/`UNPACK` lowering with the vintage index convention (confirmed by `BCD` / `..BCD.`), and allow a `PACKED ARRAY[..] OF CHAR` as a writable string in the WRITE path.
- EFFORT: M-L (two distinct pieces; biggest P2 item)
- RISK-IF-SKIPPED: programs using `PACK`/`UNPACK` or printing packed char arrays won't compile. Medium (loud).
- VERIFY: re-run `t031.pas`; expect `BCD` then `..BCD.`.
- UPGRADES: checklist 4.9; manual PACK/UNPACK index notes.
- XREF: D-031.

## ANCHOR: RM-P2-NULL
**`NULL` LSTRING constant + `.LEN` field access rejected.**
- PRIORITY: P2
- STATUS: TODO-FIX
- D-ENTRY: D-033
- CLASS: ACCEPT/REJECT
- BASIS: OBSERVED (vintage output `0` then `<>`)
- VINTAGE: compiled (2 `Assumed OUTPUT` warnings), linked, ran; `l := NULL; WRITELN(ORD(l.LEN))` → `0`; `WRITELN('<',l,'>')` → `<>` — `NULL` is a zero-length LSTRING constant, printed empty between delimiters `[OBSERVED]`.
- MODERN-NOW: rejected at typecheck: `Cannot access field on non-record type LSTRING(5)` `[OBSERVED]`.
- TOUCH: `type_checker.py` — `.LEN` rejection at the non-record field-access sites ≈L1631 / L1962; `NULL` constant handling (search `NULL`). LSTRING already has a length slot in codegen (the WRITE path loads LSTRING length via `gep ..,0,0`, see `io_write_read.py` ≈L108-110), so the runtime shape exists; the gap is typecheck-level `.LEN` field access on LSTRING and the `NULL` zero-length constant.
- ACTION: implement `NULL` as a zero-length LSTRING constant; allow `.LEN` field access on LSTRING types (map to the length slot). Confirm empty-string display already works once the constant exists.
- EFFORT: M
- RISK-IF-SKIPPED: programs using `NULL` or `LSTRING.LEN` won't compile. Medium (loud); `NULL`/`.LEN` are common LSTRING idioms.
- VERIFY: re-run `t033.pas`; expect `0` then `<>`.
- UPGRADES: manual ≈5731; checklist `NULL` LSTRING note.
- XREF: D-033.

## ANCHOR: RM-P2-ENUMREAD
**Enum READ (numeric ordinal input) rejected at typecheck.**
- PRIORITY: P2
- STATUS: TODO-FIX
- D-ENTRY: D-030 (closes the D-006 follow-up)
- CLASS: ACCEPT/REJECT
- BASIS: OBSERVED (vintage accepts numeric input, prints `1`)
- VINTAGE: with numeric input `1`, `READ(f,x); WRITELN(ORD(x))` compiled (1 `Assumed OUTPUT`), linked, ran, printed `1` — enum READ accepts the ordinal `[OBSERVED]`. (D-006: symbolic input `GREEN` instead gave runtime data-format error 1119 `[OBSERVED]`, so the accepted input form is *numeric ordinal*, not the symbolic name.)
- MODERN-NOW: rejected at typecheck: `READ argument 2 has unreadable type COL` `[OBSERVED]`.
- TOUCH: `type_checker.py` — unreadable-READ rejection at ≈L1110 (`READ argument {i+1} has unreadable type`). Runtime side: the numeric reader needs to land the parsed integer into the enum's storage.
- ACTION: allow enum types as READ targets; read a numeric ordinal and store it as the enum value (match vintage: input `1` → `ORD = 1`). Do NOT attempt symbolic-name input — vintage rejects that at runtime (1119).
- EFFORT: M
- RISK-IF-SKIPPED: programs reading enum-typed values won't compile. Medium (loud).
- VERIFY: re-run `t030.pas` with `in030`-style numeric input `1`; expect `1`. (D-006's symbolic-input behavior is already settled; no action there beyond this.)
- UPGRADES: checklist 9.7 enum I/O (≈L1062); closes the D-006 open follow-up.
- XREF: D-030; D-006.

## ANCHOR: RM-P2-SETCTOR
**Type-prefixed set constructor `COLORS[RED, BLUE]` rejected (parsed as indexing).**
- PRIORITY: P2 (consider pulling forward — unblocks RM-OPEN-T022)
- STATUS: TODO-FIX
- D-ENTRY: D-026
- CLASS: ACCEPT/REJECT (settled on rerun; an earlier run misread vintage `Assumed OUTPUT` warnings as rejection)
- BASIS: INFERRED (vintage implements the constructor; modern parses it as indexing) — vintage acceptance + output `R` is `[OBSERVED]`
- VINTAGE: pas1 accepted (2 `Assumed OUTPUT` warnings), linked, ran; `s := COLORS[RED,BLUE]; IF RED IN s ...` printed `R` (and not `G`) `[OBSERVED]`.
- MODERN-NOW: rejected at typecheck: `Cannot index non-array type SET OF COLOR` `[OBSERVED]`.
- TOUCH: `parser.py` / `type_checker.py` — the `Cannot index non-array type` rejection at ≈L1605 / L1936. The modern front end reads `TypeName[...]` as array indexing; it needs to recognize the type-prefixed *set constructor* form when the prefix names a set/base type.
- ACTION: parse/typecheck `<SetType>[ elements ]` as a set constructor (value of the named set type) rather than an index, when the prefix is a set or its base type.
- EFFORT: M
- RISK-IF-SKIPPED: programs using type-prefixed set constructors won't compile; **and** RM-OPEN-T022 (READSET delimiter retention) stays blocked, since its redesign feeds a declared `SET OF CHAR` via exactly this construct (`CHARSET['A'..'Z']`). Medium.
- VERIFY: re-run `t026.pas`; expect `R` only. Then unblock RM-OPEN-T022.
- UPGRADES: checklist 2.9 (currently `[INFERRED]` → vintage-confirmed real syntax).
- XREF: D-026; RM-OPEN-T022.

---

# P3 — Error-handling behavioral divergence (beyond cosmetics)

## ANCHOR: RM-P3-READTRAP
**Malformed formatted READ aborts on modern; vintage routes it to the trapped-I/O path.**
- PRIORITY: P3
- STATUS: TODO-FIX
- D-ENTRY: D-013
- CLASS: OUTPUT-DIFF
- BASIS: INFERRED (vintage routes reader format failures into the trapped file-error path; code 14 is `[OBSERVED]`)
- VINTAGE: with `f.TRAP := TRUE`, a malformed `READ(f,i)` printed `AFTER` then `14` — the error is trappable and execution continues `[OBSERVED]`.
- MODERN-NOW: aborted with `runtime error: malformed integer input` — readers are abort-only `[OBSERVED]`.
- TOUCH: the formatted-reader runtime path (`runtime/fileops.c` readers and `codegen/io_write_read.py` read lowering). Today reader format failures abort; they must instead consult `F.TRAP` and, when set, record code 14 into `F.ERRS` and return without aborting — the same `io_error(f, code, msg)` mechanism the file ops already use (`runtime/fileops.c` ≈L61: sets `f->errs`, abandons op instead of aborting when trapping).
- ACTION: extend the `io_error`/trap path to cover the formatted readers; on a malformed read with `F.TRAP` set, record 14 in `F.ERRS` and continue; with trapping off, keep the abort. Coordinate the code value with RM-XCUT-IOERR.
- EFFORT: M
- RISK-IF-SKIPPED: a program that enables trapping and handles reader errors instead dies on the first malformed field — behavioral divergence, not just a different message. Medium.
- VERIFY: re-run `t013.pas`; expect `AFTER` then `14` (with trapping on).
- UPGRADES: checklist 8.6 / readers + file trapping; `io_error` coverage.
- XREF: D-013; RM-XCUT-IOERR; RM-P3-ERRSCODE.

## ANCHOR: RM-P3-ERRSCODE
**`F.ERRS` returns an invented internal code; vintage RESET-on-missing-file = 10.**
- PRIORITY: P3
- STATUS: TODO-FIX
- D-ENTRY: D-012
- CLASS: OUTPUT-DIFF
- BASIS: OBSERVED (value 10)
- VINTAGE: `ASSIGN(f,'NOFILE.XYZ'); f.TRAP := TRUE; RESET(f); WRITELN(f.ERRS)` printed `10` `[OBSERVED]`.
- MODERN-NOW: printed `1` — `io_error` table is internal-only `[OBSERVED]`.
- TOUCH: `runtime/fileops.c` — `io_error` codes are currently small internal integers (1=open/create failed, 2=mode, 3=past-eof, 4=read failed, 5=write failed; ≈L122-291). The RESET-missing-file path uses code 1.
- ACTION: renumber the `io_error` table to the observed vintage values (10 = missing file on RESET; 14 = malformed formatted READ per D-013). This is the same renumber as RM-XCUT-IOERR — do them together so the whole table is coherent rather than patched per-probe.
- EFFORT: S (constant changes) — but coordinate the whole table (see RM-XCUT-IOERR).
- RISK-IF-SKIPPED: a program that branches on `F.ERRS` (e.g. `IF f.ERRS = 10`) silently misbehaves on modern. Medium and silent (narrow: only ERRS-inspecting code).
- VERIFY: re-run `t012.pas`; expect `10`.
- UPGRADES: checklist 8.6 / file error handling; `io_error` table.
- XREF: D-012; RM-XCUT-IOERR; RM-P3-READTRAP.

---

# P4 — Diagnostic-only fidelity (semantics already match; record codes)

Same semantic enforcement on both sides; only the error code/message/abort-model
differs. Low effort, low risk, no behavior change — bookkeeping. Modern's
strict-abort model is kept by design per the campaign plan.

## ANCHOR: RM-P4-PUTCODE
**PUT-after-GET in read mode: record vintage operation-error code 1110.**
- PRIORITY: P4
- STATUS: RECORD-ONLY
- D-ENTRY: D-005
- CLASS: OUTPUT-DIFF
- BASIS: INFERRED (both enforce mode restrictions; the code 1110 itself is `[OBSERVED]`)
- VINTAGE: printed `BEFORE`, then `? Error: Operation error in file T005.DAT Error Code 1110` `[OBSERVED]`.
- MODERN-NOW: aborts `PUT requires REWRITE/write mode` (exit ≠ 0); `BEFORE` swallowed by host libc buffering on abort (see RM-XCUT-FLUSH) `[OBSERVED]`.
- TOUCH: documentation / `io_error` notes only — the PUT mode guard already exists (`runtime/fileops.c` ≈L286). Modern strict-abort is intentional.
- ACTION: record code 1110 in the `io_error` table notes as the vintage operation-error for PUT-after-GET. No behavior change.
- EFFORT: S
- RISK-IF-SKIPPED: none functional; only diagnostic-text fidelity (modern aborts vs vintage continues-with-code, but the enforcement is identical).
- VERIFY: n/a (record-only). If RM-XCUT-FLUSH lands, `BEFORE` will appear in modern capture too.
- UPGRADES: checklist file runtime semantics.
- XREF: D-005; D-024 (distinct code 1104); RM-XCUT-FLUSH.

## ANCHOR: RM-P4-WRITECODE
**Formatted WRITE on a file in inspection mode: record vintage code 1104.**
- PRIORITY: P4
- STATUS: RECORD-ONLY
- D-ENTRY: D-024
- CLASS: OUTPUT-DIFF
- BASIS: INFERRED (both prevent write-through; code 1104 is `[OBSERVED]`)
- VINTAGE: printed `BEFORE`, then `? Error: Operation error in file T024.DAT` / `Error Code 1104` `[OBSERVED]`.
- MODERN-NOW: aborts `file runtime: WRITE requires REWRITE/write mode` `[OBSERVED]`.
- TOUCH: documentation / `io_error` notes only. Note 1104 is *distinct* from PUT's 1110 (D-005) — the vintage uses different operation-error codes for the two write paths.
- ACTION: record code 1104. Align modern diagnostics only if vintage error-text fidelity is later desired.
- EFFORT: S
- RISK-IF-SKIPPED: none functional; diagnostic-only.
- VERIFY: n/a (record-only).
- UPGRADES: checklist file runtime semantics; mode enforcement.
- XREF: D-024; D-005.

## ANCHOR: RM-P4-NILCODE
**NIL dereference: record vintage code 2031 (and optionally flush before abort).**
- PRIORITY: P4
- STATUS: RECORD-ONLY
- D-ENTRY: D-015
- CLASS: OUTPUT-DIFF
- BASIS: INFERRED (both trap the dereference; code 2031 is `[OBSERVED]`)
- VINTAGE: printed `BEFORE`, then `? Error: NIL Pointer Reference` / `Error Code 2031` `[OBSERVED]`.
- MODERN-NOW: aborted on the dereference; `BEFORE` not preserved in captured stdout (host buffering artifact); no runtime text on stderr `[OBSERVED]`.
- TOUCH: NIL check is `nilck` in `codegen/types_map.py` (≈L390). Modern abort model differs by design (campaign plan: record only).
- ACTION: record code 2031. Optionally adopt RM-XCUT-FLUSH so `BEFORE` is preserved in modern capture, which also makes t005/t015/t016 byte-comparable.
- EFFORT: S
- RISK-IF-SKIPPED: none functional; diagnostic/abort-model only.
- VERIFY: n/a (record-only); if RM-XCUT-FLUSH lands, expect `BEFORE` in modern capture.
- UPGRADES: checklist runtime checks / `$NILCK+`.
- XREF: D-015; RM-XCUT-FLUSH.

---

# P5 — Conditional-compilation skipper (niche)

## ANCHOR: RM-P5-DUPELSE
**Duplicate `$ELSE`: modern prints `A`, vintage prints `A C`.**
- PRIORITY: P5
- STATUS: DECISION-NEEDED
- D-ENTRY: D-003
- CLASS: OUTPUT-DIFF
- BASIS: INFERRED (mechanism deduced from output; the manual does not document duplicate `$ELSE`) — both outputs are `[OBSERVED]`
- VINTAGE: `{$IF 1 $THEN} A {$ELSE} B {$ELSE} C {$END}` compiled, linked, ran; prints `A` and `C` — the skipper resumes emission at the *second* `$ELSE` despite the true first branch `[OBSERVED, INFERRED mechanism]`.
- MODERN-NOW: prints `A` only — the `stop_at_else` fix skips a completed true-branch forward to `$END`, ignoring a depth-1 `$ELSE` `[OBSERVED]`.
- TOUCH: `lexer.py` — `_skip_source_block` (≈L309) and its `stop_at_else` handling (≈L364; depth-1 `$ELSE` is ignored when skipping a completed true-branch forward). The `ELSE` metacommand tag is `0x0011` (≈L29).
- DECISION: match the vintage multi-`$ELSE` resume behavior, or keep modern's "ignore stray/duplicate `$ELSE`" as a deliberate divergence. The vintage behavior on malformed/duplicate directives is itself `[INFERRED]` from one probe; matching it is low-value and the modern behavior is arguably more sensible. Recommend documenting as a deliberate divergence unless duplicate-`$ELSE` fidelity is specifically required.
- ACTION (if 'match'): change `_skip_source_block` so a depth-1 second `$ELSE` resumes emission (rather than being ignored) when skipping a completed true-branch.
- EFFORT: S
- RISK-IF-SKIPPED: only affects sources with malformed/duplicate `$ELSE` directives. Low (niche).
- VERIFY: re-run `t003.pas`. If 'match': expect `A C`. If 'document': leave as recorded OUTPUT-DIFF.
- UPGRADES: checklist metacommand semantics (≈L948/1093).
- XREF: D-003; D-004 (related skipper item).

## ANCHOR: RM-P5-SKIPQUOTE
**`{` inside a string in a skipped `$IF` block: keep the modern quote-aware fix, document the divergence.**
- PRIORITY: P5
- STATUS: RECORD-ONLY (already decided to keep modern behavior)
- D-ENTRY: D-004
- CLASS: REJECT/ACCEPT
- BASIS: INFERRED (vintage skipper is not quote-aware; mechanism deduced from diagnostics) — both verdicts `[OBSERVED]`
- VINTAGE: pas1 *rejects* with `Unexpected End Of File` and `Program Not Found` — it treats `{` inside the string as a nested comment/metacommand start and runs off the end of file `[OBSERVED, INFERRED mechanism]`.
- MODERN-NOW: accepts; outputs `OK` (quote-aware skipper) `[OBSERVED]`.
- TOUCH: `lexer.py` — `_skip_source_block` quote handling (≈L309+). No code change planned; the modern quote-aware behavior is the intended one.
- ACTION: document this as an intentional, kept divergence (modern is quote-aware, vintage is not). The campaign already rules this a divergence to keep.
- EFFORT: S (doc only)
- RISK-IF-SKIPPED: n/a — keeping current behavior is the decision; this entry just ensures it's documented, not "fixed" by a future agent toward the vintage bug.
- VERIFY: n/a (no change). `t004.pas` continues to output `OK`.
- UPGRADES: checklist metacommand parsing (≈L948/1093) — note as documented divergence.
- XREF: D-004; D-003.

---

# X — Cross-cutting tasks

## ANCHOR: RM-XCUT-IOERR
**Renumber the `io_error` table to vintage codes (one coherent pass).**
- PRIORITY: X (do before/with D-012 and D-013)
- STATUS: TODO-FIX
- BASIS: OBSERVED (codes 10, 14, 1104, 1110, 1119, 1123 all from probe runs)
- SCOPE: `runtime/fileops.c` `io_error` currently uses internal codes 1-5 (≈L122-291). Multiple probes pin vintage values: 10 (RESET missing file, D-012), 14 (malformed formatted READ, D-013), 1104 (WRITE inspection mode, D-024), 1110 (PUT after GET, D-005), 1119 (enum READ symbolic input, D-006), 1123 (integer `::N`, D-010). Note vintage uses two ranges: ~10/14 for `F.ERRS` trapped codes vs 11xx/2xxx for the `? Error: ... Error Code` runtime aborts — keep that distinction.
- ACTION: renumber the table to the observed vintage `F.ERRS` codes (10, 14, ...) where modern surfaces them through `F.ERRS`, and record the 11xx/2xxx abort codes as notes where modern keeps its strict-abort model. Land this as the substrate for RM-P3-ERRSCODE (D-012) and RM-P3-READTRAP (D-013) rather than patching per-probe.
- EFFORT: M
- RISK-IF-SKIPPED: per-probe patching leaves an inconsistent table — exactly the kind of drift the old D-001 corruption warns against.
- VERIFY: t012 → `10`; t013 → `14` (trap on). Re-run any file-error fixtures in the modern suite to confirm no regressions from renumbering.
- XREF: D-012; D-013; D-005; D-024; D-006; D-010.

## ANCHOR: RM-XCUT-FLUSH
**Flush stdout before modern aborts (test-fidelity, not a semantic fix).**
- PRIORITY: X (small, improves comparability of several abort-path probes)
- STATUS: TODO-FIX
- BASIS: OBSERVED (the missing-`BEFORE`-on-abort behavior is a documented host libc buffering artifact, NOT a semantic difference — stated in the campaign plan and D-005/D-015 entries)
- SCOPE: on modern abort paths (`runtime/pabort.c` and the `runtime_error_func` abort), pre-abort stdout printed before the abort can be discarded by libc buffering, so `BEFORE` markers vanish from captured output (t005, t015, and per the campaign possibly t016).
- ACTION: flush stdout (and stderr) immediately before the process aborts in the runtime abort handler. This is an output-ergonomics/test-comparability improvement; it does not change semantics.
- EFFORT: S
- RISK-IF-SKIPPED: none semantic. Several abort-path probes remain non-byte-comparable to vintage (judge by exit status + which markers appear, per the campaign), which is fine but noisier.
- VERIFY: re-run t005 / t015; `BEFORE` should now appear in modern capture before the abort.
- XREF: D-005; D-015; D-016.

## ANCHOR: RM-XCUT-ENUMBOOL
**BOOLEAN prints names; user enums print ordinals — do NOT unify these WRITE paths.**
- PRIORITY: X (a guard rail, not a task — read before touching RM-P0-BOOL or RM-P1-ENUMWRITE)
- STATUS: DECISION-NEEDED (the constraint is fixed; the enum side is the open decision)
- BASIS: OBSERVED — vintage BOOLEAN → `TRUE`/`FALSE` (t020); vintage user enum → ordinal `1` (t019).
- CONSTRAINT: these two WRITE behaviors point in *opposite* directions. RM-P0-BOOL must make BOOLEAN emit the *name*; RM-P1-ENUMWRITE (if 'match' is chosen) must make user enums emit the *ordinal*. A naive "make enum WRITE match vintage by printing the ordinal" applied to BOOLEAN (which is `i8`, enum-like) would re-break t020. Conversely, routing BOOLEAN through the existing `enum_name_table` is the *right* mechanism for BOOLEAN but the *wrong* one for user enums.
- ACTION: implement BOOLEAN as a named-output special case (table `["FALSE","TRUE"]` or a `select`), distinct from user-enum WRITE. Keep the two code paths separate in `build_write_format_and_args`. Whichever way RM-P1-ENUMWRITE is decided, re-run t020 to confirm BOOLEAN still prints `TRUE`/`FALSE`.
- EFFORT: S (already folded into RM-P0-BOOL / RM-P1-ENUMWRITE)
- XREF: D-020; D-019; RM-P0-BOOL; RM-P1-ENUMWRITE.

---

# NO-ACTION — documented-correct; do not "fix"

## ANCHOR: RM-NOACTION
- STATUS: NO-ACTION
- **D-014 — `$INITCK+` sentinel.** Vintage `-32768`, modern `-2147483648` `[OBSERVED]`. Same sentinel at the documented 32-bit width adaptation (manual `[READ]`). Width-driven, expected. Do not change.
- **D-016 — signed overflow under `$MATHCK+`.** Vintage 16-bit traps (code 2054), modern 32-bit does not overflow at `32767+1` → prints `32768` `[OBSERVED]`. Expected width adaptation. Do not change.
- **D-017 — signed overflow under `$MATHCK-`.** Vintage wraps to `-32768`, modern prints `32768` `[OBSERVED]`. Confirms `$MATHCK-` disables checking (manual `[READ]`); the value differs by width. Do not change.
- **Baselines (AGREE-ACCEPT, no divergence):** t001 (REAL default format ` 1.2345600E+02`), t007 (STRING(3) READ stop-at-fill), t008 (STRING(5) blank-pad + marker), t009 (LSTRING(3) whole-line consume + truncate), t011 (STRING `::N` ignored on both sides), t018 (RESET implicit GET / lazy-fill), t023 (temp-file round-trip), t025 (CLOSE-marker), t027 (RETYPE round-trip), **D-034** (`F.MODE` = 0/0 after REWRITE/RESET), **D-035** (bare `OTHERWISE stmt` grammar accepted), **D-036** (`F^` is blank ORD 32 at EOLN). These are evidence-upgrade confirmations only — no code change.
- WHY LISTED: so a future agent does not mistake an expected width adaptation or a confirmed agreement for an open bug and "fix" it toward 16-bit behavior. The 32-bit INTEGER width is the documented modern adaptation; reverting it would regress the whole arithmetic model.

---

# OPEN — re-probe / redesign (investigation, not remediation)

## ANCHOR: RM-OPEN-T021
**`WRITELN(ORD(EOL))` — vintage verdict `[UNVERIFIED]`; rerun required.**
- STATUS: INVESTIGATE
- BASIS: UNVERIFIED — vintage pas1 emitted `Unknown Identifier In Expression Assumed Zero` (a warning-with-recovery idiom, *not* a clean hard stop), logged as AGREE-REJECT before the warnings-are-not-rejections rule. Whether pas2 produced an `.obj` and what the exe printed was never verified.
- MODERN-NOW: rejects at typecheck (`Undefined variable: EOL`) `[OBSERVED]`.
- ACTION: rerun `t021.pas` under the differential-testing skill; determine whether pas2 emits an `.obj` and what the exe prints, then re-grade. This is a re-probe, not a code change. The EOL checklist item (≈L1032) stays open until rerun.
- EFFORT: S (one probe rerun)
- VERIFY: a clean run with a definite verdict (REJECT vs an actual printed value).
- XREF: t021 open item; checklist EOL (≈L1032).

## ANCHOR: RM-OPEN-T022
**`READSET` delimiter retention — `[UNVERIFIED]`; redesign (blocked on RM-P2-SETCTOR).**
- STATUS: INVESTIGATE (blocked: needs D-026 implemented)
- BASIS: UNVERIFIED — original `READSET(f, l, ['A'..'Z'])` probe: vintage pas1 `Character Set Expected` `[OBSERVED]`; modern also rejected at typecheck but the recorded diagnostic was a copy-paste from t026 and was discarded. The delimiter-retention question itself is unanswered.
- ACTION: redesign the probe to pass a declared `SET OF CHAR` value — via the type-prefixed constructor (`CHARSET['A'..'Z']`, which becomes valid once RM-P2-SETCTOR / D-026 lands) or a set variable — then run it differentially to settle delimiter retention. Note t029 already settled READSET delimiter retention *with a declared set variable* (`ABC` / `,`, AGREE-ACCEPT); this redesign extends that to the constructor form.
- EFFORT: M (redesign + run; gated on D-026)
- VERIFY: a clean differential run answering the delimiter-retention question.
- XREF: t022 open item; D-026 / RM-P2-SETCTOR; t029 (READSET with set variable, AGREE-ACCEPT).

---

# ANCHOR: RM-PLAYBOOK  —  per-item execution recipe

Each fix follows the `ibm-pascal-differential-testing` loop. Do not trust `$?`;
read output files and filtered logs; write any new finding into
`docs/discrepancies.md` *before* tearing down temp dirs.

Modern side (per item):
1. Apply the code change in `probes-branch`.
2. `compile_to_llvm.py` on the item's probe → `clang` + `runtime/*.c` → run under `timeout 15s`. The driver exit code IS meaningful on the modern side.
3. Run the full modern suite (the campaign baseline was 448 tests OK at the probed commit) to catch regressions — especially after RM-XCUT-IOERR (table renumber), RM-P0-CASE (CASE codegen), and RM-P1-ENUMWRITE / RM-P0-BOOL (shared WRITE builder).

Vintage side (only when a fix needs fresh vintage ground truth, e.g. a follow-on probe or RM-OPEN-T021/T022):
1. Fresh single-use unpack of the vintage zip per probe.
2. `unix2dos` the source before compiling.
3. `pas1 tNNN.pas;` → `pas2` → `link tNNN.obj,tNNN.exe;` → run with DOS-side `>` redirection (`< in0NN.txt` for input probes); check both `tNNN.exe` and `TNNN.EXE`.
4. `dos2unix` the captured output; harvest filtered logs; verdict from output files, never `$?`.
5. Write the finding into `docs/discrepancies.md` (respect the mechanical entry-format rules — the old D-001 corruption is what they prevent), then tear down.

Probe sources on hand: `t003`–`t020` in `probes.zip` (+ `in006.txt`),
`t021`–`t028` in `probes-t021-t028.zip`, `t029`–`t036` in
`probes-t029-t036.zip`. New follow-on probes (e.g. enum-READ numeric input,
BOOLEAN field-width) should be numbered after `t036` and added with CRLF input
files where input is needed.

Evidence-grade upgrades to apply on green (each item's `UPGRADES:` names the
target): bump the cited `Grand_Unified_Checklist.md` / EBNF entries from
`[INFERRED]`/`[UNVERIFIED]` to vintage-`[OBSERVED]` as each probe confirms them.

---

# ANCHOR: RM-UNKNOWNS  —  unknowns / not determined

Stated explicitly so gaps are not mistaken for findings (per the
anti-confabulation discipline):

- **BOOLEAN WRITE field width / padding** (D-020): the one capture showed no leading pad, but width-with-padding was not probed. `[UNVERIFIED]` — add a probe before assuming `TRUE`/`FALSE` need (or don't need) a field width.
- **Integer `::N` — vintage error timing** (D-010): vintage makes it a *runtime* data-format error (1123). Whether a *compile-time* rejection is an acceptable match, or fidelity demands the runtime timing, is a maintainer decision, not a fact.
- **Duplicate `$ELSE` and non-quote-aware skipper** (D-003, D-004): vintage behavior on malformed directives is `[INFERRED]` from single probes and is not in the manual. Treat the deduced mechanisms as hypotheses, not ground truth.
- **`F.ERRS` full code table**: only codes 10 (RESET-missing) and 14 (malformed READ) are `[OBSERVED]` via `F.ERRS`; the rest of the vintage `F.ERRS` numbering is unprobed. Do not invent values to fill the table — probe or leave noted.
- **t021 EOL verdict** and **t022 READSET-via-constructor delimiter retention**: both `[UNVERIFIED]`; see RM-OPEN-T021 / RM-OPEN-T022. Do not record a verdict until rerun.
- **Vintage symbolic enum READ** (D-006): `GREEN` gave runtime error 1119; this establishes that symbolic *names* are not accepted as input, but the full accepted-input grammar beyond a single numeric ordinal (`1`, t030) is not exhaustively probed.
- **Line numbers in every `TOUCH:` field are `≈`** and read from the `probes-branch` snapshot; they will drift as code changes. The function/string anchors in each `TOUCH:` are the durable locators — grep those, not the line numbers.
