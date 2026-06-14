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
grep 'ANCHOR: RM-DEC'      remediation-plan.md    # standing design decisions
```

Per-item field labels are fixed: `PRIORITY:`, `STATUS:`, `D-ENTRY:`, `CLASS:`,
`BASIS:`, `VINTAGE:`, `MODERN-NOW:`, `TOUCH:`, `ACTION:`, `DECISION:`,
`EFFORT:`, `RISK-IF-SKIPPED:`, `VERIFY:`, `UPGRADES:`, `XREF:`. Standing
design-decision items (`RM-DEC-*`, section D) add two: `GOVERNS:` (the
remediation items the ruling controls — they must not be resolved
independently of it) and `SEE:` (the standalone discussion note).

`BASIS:` grades the *vintage* claim the fix targets: `OBSERVED` (probe output),
`INFERRED` (mechanism deduced from output), `UNVERIFIED` (not established by any
probe). `EFFORT:` is S / M / L (engineering size, not importance).

---

## ANCHOR: RM-INDEX  —  importance-ordered index

Tiers are correctness-first: P0 = silent or visible wrongness on ordinary valid
code; P1 = acceptance/format fidelity on features modern already supports; P2 =
compile-blocking feature gaps (loud failures, no corruption); P3 = error-handling
behavioral divergence; P4 = diagnostic-only (semantics already match); P5 =
conditional-compilation skipper (niche); X = cross-cutting; DEC = standing
design decision (gates the items it governs); NO-ACTION = documented-correct;
OPEN = re-probe.

| Anchor | D | Prio | Status | One-line | Effort |
|---|---|---|---|---|---|
| RM-P0-BOOL | D-020 | P0 | RESOLVED | BOOLEAN WRITE now prints `TRUE`/`FALSE` in all modes | S |
| RM-P0-CASE | D-028 | P0 | RESOLVED | CASE no-match now aborts under `$RANGECK` when no `OTHERWISE` matches | S |
| RM-P1-INTPN | D-010 | P1 | RESOLVED | integer `::N` is rejected at typecheck; vintage runtime-rejects | S |
| RM-P1-ENUMWRITE | D-019 | P1 | RESOLVED | enum WRITE is ordinal by default; symbolic names require `-f symbolic-enum-io` | S |
| RM-DEC-ENUMIO | D-019/D-006/D-030 | DEC | DECIDED | faithful default (ordinal write + numeric read); one `symbolic-enum-io` flag gates name write + name read | — |
| RM-P2-WORD | D-032 | P2 | RESOLVED | WORD assign/convert (`WRD`,`MAXWORD`) now matches probed vintage edges | M |
| RM-P2-PACK | D-031 | P2 | TODO-FIX | `PACK`/`UNPACK` + packed-char-array WRITE rejected | M-L |
| RM-P2-NULL | D-033 | P2 | TODO-FIX | `NULL` LSTRING constant + `.LEN` field rejected | M |
| RM-P2-ENUMREAD | D-030/D-006 | P2 | RESOLVED | enum READ accepts numeric ordinals by default; symbolic names under `-f symbolic-enum-io` | M |
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
| RM-XCUT-ENUMBOOL | — | X | HONORED | BOOLEAN-names vs user-enum-ordinal kept separate (D-019 vs D-020) | S |
| RM-DEC-INTWIDTH | D-014/16/17 | DEC | DECIDED (16-bit fork, implemented) | INTEGER now 16-bit; INTEGER32/64 added behind `-f wide-integers`; D-014/16/17 resolved | done |
| RM-NOACTION | baselines | — | NO-ACTION | AGREE-ACCEPT baselines only (the D-014/16/17 width trio is now RESOLVED, see RM-DEC-INTWIDTH); don't fix piecemeal | — |
| RM-OPEN-T021 | t021 | OPEN | INVESTIGATE | `ORD(EOL)` verdict `[UNVERIFIED]`; rerun | S |
| RM-OPEN-T022 | t022 | OPEN | INVESTIGATE | `READSET` delimiter retention; redesign (needs RM-P2-SETCTOR) | M |

Sequencing note: P0 → P1 are cheap, high-value, and should land first. Within
P2 the items are largely independent and may be resequenced by team preference;
the recommended order is most-fundamental-type-first (WORD, PACK, NULL/LSTRING)
ahead of the narrower input/grammar gaps (enum READ, set ctor), **except** that
`RM-P2-SETCTOR` (D-026) is the prerequisite for closing `RM-OPEN-T022` and may
be pulled forward if that open item matters. `RM-XCUT-IOERR` should land before
or with D-012/D-013 since all three touch the same table.

Two standing design decisions (section D) gate other work. `RM-DEC-ENUMIO` must
be settled *before* `RM-P1-ENUMWRITE` and `RM-P2-ENUMREAD` are implemented —
those two are halves of one enum-I/O contract and must not be resolved
independently; whichever fork is chosen sets both. `RM-DEC-INTWIDTH` has been
**decided and implemented**: the 16-bit fork was taken — `INTEGER` is now signed
16-bit, `INTEGER32`/`INTEGER64` are opt-in extension types behind the
`wide-integers` feature flag, and D-014/16/17 are resolved (no longer
NO-ACTION). The feature-flag mechanism introduced for that work (`features.py`,
exposed via `-f`/`--feature`, `--dialect`, and `--list-features`) is the
established pattern for gating any future "extends beyond vintage" behavior; new
extension items should reuse it rather than inventing a parallel mechanism.

---

# P0 — Silent or visible wrongness on ordinary valid code

## ANCHOR: RM-P0-BOOL
**BOOLEAN WRITE emits raw storage bytes instead of `TRUE`/`FALSE`.**
- PRIORITY: P0 (highest — visible corruption, high-frequency operation, trivial fix)
- STATUS: RESOLVED
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
- STATUS: RESOLVED
- D-ENTRY: D-028
- CLASS: OUTPUT-DIFF
- BASIS: OBSERVED (code value 2050) + READ (manual src ≈9953 documents the trap under `$RANGECK`)
- VINTAGE: prints `BEFORE`, then runtime error `? Error: No CASE Value Matches Selector` / `Error Code 2050`; confirms `$RANGECK` is ON by default `[OBSERVED]`.
- MODERN-NOW: under default `$RANGECK`, prints `BEFORE` then aborts before `AFTER`; explicit `OTHERWISE` and matching-arm CASE paths still run normally `[OBSERVED]`.
- TOUCH: `codegen/stmts.py` → `codegen_case_stmt` and `_emit_case_no_match_trap`. With `stmt.otherwise is None`, no match, and `effective_rangeck(stmt)` true, the final no-match path now flushes stdout, calls the existing runtime abort, and marks the block `unreachable`. When `$RANGECK-`, it preserves silent fall-through.
- ACTION: done. Exact vintage diagnostic text/code 2050 is not emitted; this item resolves the P0 silent-control-flow corruption only.
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
- STATUS: RESOLVED
- D-ENTRY: D-010
- CLASS: OUTPUT-DIFF (both compile; vintage errors at runtime, modern runs)
- BASIS: OBSERVED (vintage data-format error code 1123)
- VINTAGE: compiled and linked, then failed at runtime: `? Error: Data format error in file USER` / `Error Code 1123` `[OBSERVED]`.
- MODERN-NOW: rejects `WRITELN(x::4)` at typecheck with `WRITE precision (::N) is not valid for INTEGER-compatible values` `[OBSERVED]`.
- TOUCH: `type_checker.py` → `_check_write_args` rejects precision on INTEGER-compatible WRITE values. Contrast: REAL `::N` IS still honored by `codegen/io_write_read.py`; STRING `::N` remains accepted-and-ignored on BOTH sides (t011, AGREE-ACCEPT) so string stays intentionally lenient.
- DECISION: decided — match vintage by rejecting integer `::N`. The vintage compiler reports a runtime data-format error (1123); modern deliberately rejects at compile time as the remediation plan allowed, so no exact runtime-code fidelity is claimed.
- ACTION: done.
- EFFORT: S
- RISK-IF-SKIPPED: a ported program using integer `::N` that the vintage runtime would have rejected instead runs silently on modern — masks a real defect in the ported source. Medium (labeled high in the log for the accept-vs-reject fidelity gap).
- VERIFY: re-run `t010.pas`. If 'match' chosen: modern must error (runtime 1123-equivalent, or a documented compile error). If 'keep': record the extension and leave t010 as a documented OUTPUT-DIFF.
- UPGRADES: checklist 8.3 / I/O formatting; EBNF `io_data_param`.
- XREF: D-010; D-002 (REAL `::N`); t011 (STRING `::N`, AGREE-ACCEPT).

## ANCHOR: RM-P1-ENUMWRITE
**Enum WRITE prints the symbolic name; vintage prints the ordinal.**
- PRIORITY: P1
- STATUS: RESOLVED
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
- GOVERNED-BY: RM-DEC-ENUMIO — this is the write half of the enum-I/O fork; do not decide it in isolation from enum READ (RM-P2-ENUMREAD). The `DECISION:` above is the local form of that fork's write side.
- XREF: D-019; RM-P0-BOOL; RM-XCUT-ENUMBOOL; RM-DEC-ENUMIO.

---

# P2 — Compile-blocking feature gaps (modern rejects valid vintage programs)

These fail loudly at typecheck (no silent corruption) but block real vintage
programs from compiling at all. All confirmed: vintage compiles+links+runs;
modern rejects at typecheck. Items are largely independent.

## ANCHOR: RM-P2-WORD
**WORD assignment / conversion (`WRD`, `MAXWORD`) rejected at typecheck.**
- PRIORITY: P2
- STATUS: RESOLVED
- D-ENTRY: D-032
- CLASS: ACCEPT/REJECT
- BASIS: OBSERVED (vintage output `65535`, `40000`, `65535`)
- VINTAGE: compiled (3 `Assumed OUTPUT` warnings), linked, ran; `MAXWORD` = `65535`, `w := 40000` prints `40000`, `WRD(-1)` = `65535` — 16-bit unsigned, `WRD(-1)` wraps to max `[OBSERVED]`.
- MODERN-NOW: compiles and prints `65535`, `40000`, `65535` for the D-032 probe `[OBSERVED]`.
- TOUCH: already resolved by prior 16-bit INTEGER/WORD work: `type_system.py` permits INTEGER→WORD assignment, `codegen/exprs.py` lowers `WRD` with 16-bit wrap semantics, `codegen/base.py` defines `MAXWORD = 65535`, and WRITE displays WORD through the unsigned `i16 → %u` path.
- ACTION: done; added regression `test_word_probe_d032_edges` to pin `MAXWORD`, `w := 40000`, and `WRD(-1)`.
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
- STATUS: RESOLVED
- D-ENTRY: D-030 (closes the D-006 follow-up)
- CLASS: ACCEPT/REJECT
- BASIS: OBSERVED (vintage accepts numeric input, prints `1`)
- VINTAGE: with numeric input `1`, `READ(f,x); WRITELN(ORD(x))` compiled (1 `Assumed OUTPUT`), linked, ran, printed `1` — enum READ accepts the ordinal `[OBSERVED]`. (D-006: symbolic input `GREEN` instead gave runtime data-format error 1119 `[OBSERVED]`, so the accepted input form is *numeric ordinal*, not the symbolic name.)
- MODERN-NOW: rejected at typecheck: `READ argument 2 has unreadable type COL` `[OBSERVED]`.
- TOUCH: `type_checker.py` — unreadable-READ rejection at ≈L1110 (`READ argument {i+1} has unreadable type`). Runtime side: the numeric reader needs to land the parsed integer into the enum's storage.
- ACTION: allow enum types as READ targets; read a numeric ordinal and store it as the enum value (match vintage: input `1` → `ORD = 1`). Do NOT attempt symbolic-name input — vintage rejects that at runtime (1119). *This numeric-read target is the **faithful fork** of RM-DEC-ENUMIO (the recommended default).* If that decision instead picks the **symbolic fork**, the read here becomes name-based (a reader the original never had) and must be co-designed with symbolic enum WRITE — do not implement numeric read and symbolic write together (they would not round-trip; see RM-DEC-ENUMIO).
- EFFORT: M
- RISK-IF-SKIPPED: programs reading enum-typed values won't compile. Medium (loud).
- VERIFY: re-run `t030.pas` with `in030`-style numeric input `1`; expect `1` (faithful fork). (D-006's symbolic-input behavior is already settled; no action there beyond this.)
- UPGRADES: checklist 9.7 enum I/O (≈L1062); closes the D-006 open follow-up.
- GOVERNED-BY: RM-DEC-ENUMIO — this is the read half of the enum-I/O fork; the accepted input form (numeric vs symbolic) is set there, not here.
- XREF: D-030; D-006; RM-P1-ENUMWRITE; RM-DEC-ENUMIO.

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
- STATUS: HONORED (constraint implemented)
- BASIS: OBSERVED — vintage BOOLEAN → `TRUE`/`FALSE` (t020); vintage user enum → ordinal `1` (t019).
- CONSTRAINT: these two WRITE behaviors point in *opposite* directions. RM-P0-BOOL must make BOOLEAN emit the *name*; RM-P1-ENUMWRITE (if 'match' is chosen) must make user enums emit the *ordinal*. A naive "make enum WRITE match vintage by printing the ordinal" applied to BOOLEAN (which is `i8`, enum-like) would re-break t020. Conversely, routing BOOLEAN through the existing `enum_name_table` is the *right* mechanism for BOOLEAN but the *wrong* one for user enums.
- ACTION: implement BOOLEAN as a named-output special case (table `["FALSE","TRUE"]` or a `select`), distinct from user-enum WRITE. Keep the two code paths separate in `build_write_format_and_args`. Whichever way RM-P1-ENUMWRITE is decided, re-run t020 to confirm BOOLEAN still prints `TRUE`/`FALSE`.
- EFFORT: S (already folded into RM-P0-BOOL / RM-P1-ENUMWRITE)
- XREF: D-020; D-019; RM-P0-BOOL; RM-P1-ENUMWRITE; RM-DEC-ENUMIO.

---

# D — Design decisions (standing, gate the items they govern)

Two project-level decisions are tracked here rather than buried in the items
they control. Each is `DECISION-NEEDED`, names what it `GOVERNS:`, and points
to a standalone discussion note via `SEE:`. The `RECOMMENDATION:` line is my
lean (consistent with the discussion notes), not a ruling. Settle these before
implementing the items they govern.

## ANCHOR: RM-DEC-ENUMIO
**Enum I/O contract — faithful (ordinal write + numeric read) vs symbolic (name write + name read).**
- PRIORITY: DEC (gates RM-P1-ENUMWRITE [P1] and RM-P2-ENUMREAD [P2] — settle before implementing either)
- STATUS: DECIDED
- D-ENTRY: D-019 (write); D-006 + D-030 (read)
- BASIS: OBSERVED — vintage WRITE emits the ordinal `1` (t019); vintage READ accepts a numeric ordinal `1` (t030) and rejects the symbolic name `GREEN` at runtime with code 1119 (t006). Modern WRITE emits the name `GREEN`; modern READ is currently rejected at type-check.
- DECISION: settled. The default contract is the faithful fork: WRITE emits the ordinal (`1`) and READ consumes a numeric ordinal. The symbolic fork is retained only as the opt-in `-f symbolic-enum-io` extension, where the same single feature flag gates both name-based WRITE and name-based READ. This is the second extension on the generic `features.py` mechanism after `wide-integers`; no parallel enum-specific flag mechanism exists.
- OPTIONS:
  - **Faithful fork (default).** WRITE emits the ordinal (`1`); READ consumes a numeric ordinal. Matches 1981 on both ends, round-trips with itself and with the original, machine-friendly. Lowers to: RM-P1-ENUMWRITE → ordinal; RM-P2-ENUMREAD → numeric.
  - **Symbolic fork (extension only).** WRITE emits the name (`GREEN`); READ consumes the name. Round-trips with itself and is human-friendly, but diverges from 1981 on both ends. Enabled only by `symbolic-enum-io`.
  - **Status quo is NOT an option.** Symbolic write + numeric read means the compiler's own output is unreadable by its own reader. The single feature flag makes that mixed state unreachable.
- CONSTRAINT: BOOLEAN prints `TRUE`/`FALSE` under *either* fork — that is the *faithful* behavior for booleans (the original also names booleans; see RM-P0-BOOL / RM-XCUT-ENUMBOOL). If the faithful fork is chosen, do NOT collapse BOOLEAN into the user-enum ordinal path.
- RECOMMENDATION: faithful fork. For a fidelity-first project, make the default match 1981 and offer symbolic names only as an explicitly documented debug/extension mode, never the default round-trip format.
- GOVERNS: RM-P1-ENUMWRITE (write half), RM-P2-ENUMREAD (read half) — resolve both together per this ruling.
- RISK-IF-SKIPPED: the two enum items get implemented piecemeal and land the incoherent symbolic-write / numeric-read state — the new compiler cannot read its own enum output.
- EFFORT: the decision itself is free; downstream cost is the chosen fork's two implementations (faithful ≈ the S+M already on RM-P1-ENUMWRITE / RM-P2-ENUMREAD; symbolic adds reader design + spec).
- VERIFY: after implementation, a WRITE-then-READ round-trip of an enum value must reproduce the original value. Faithful fork: t019 → `1`, t030 → `1`. Symbolic fork: the round-trip holds on names, and the reader's edge cases are specified and tested.
- UPGRADES: checklist 9.7 (enum I/O) and 9.8 (enum WRITE) — record the chosen contract and grade.
- SEE: `June 12th ENUM discussion.md`.
- XREF: D-019; D-006; D-030; RM-P1-ENUMWRITE; RM-P2-ENUMREAD; RM-XCUT-ENUMBOOL; RM-P0-BOOL.

## ANCHOR: RM-DEC-INTWIDTH
**INTEGER width — RESOLVED: moved to 16-bit + added INTEGER32 / INTEGER64 behind `-f wide-integers`.**
- PRIORITY: DEC (resolved)
- STATUS: DECIDED — 16-bit fork taken and implemented; D-014/16/17 resolved.
- RESOLUTION: `INTEGER` is now signed 16-bit (`i16`), matching 1981 bit-for-bit (range, overflow boundary, `$MATHCK-` wrap, `$INITCK` sentinel, and layout/`SIZEOF`). `MAXINT = 32767`. Wider arithmetic moved behind two opt-in extension types, `INTEGER32` (`i32`) and `INTEGER64` (`i64`), recognized only when the `wide-integers` feature is enabled; without it those identifiers are rejected as unknown types, exactly as vintage would. Implemented decisions: (a) literal-overflow policy is **faithful rejection** — an integer literal is range-checked against its context type and defaults to `INTEGER` (i16) with no context, so out-of-range bare literals are a compile error (no silent widening; unary minus folded before the check, so `-32768` is accepted and bare `32768` is rejected); (b) `$MATHCK` signedness is derived from the Pascal type, not from LLVM width (required once `INTEGER` and unsigned `WORD` share `i16`); (c) `$INITCK` sentinel is each integer type's per-width minimum; (d) a pre-abort `stdout` flush (`RM-XCUT-FLUSH`) lands so output preceding a trap is captured. The feature mechanism (`features.py` + `-f`/`--feature`, `--dialect`, `--list-features`) is generic and is the standing pattern for future extensions.
- D-ENTRY: D-014, D-016, D-017 (one root cause: INTEGER width) — all RESOLVED
- BASIS: OBSERVED — vintage 16-bit behavior at each check site: `$INITCK+` sentinel `-32768` (t014); `$MATHCK+` traps `32767+1` as overflow, code 2054 (t016); `$MATHCK-` wraps to `-32768` (t017). Modern *previously* mapped `INTEGER → i32` (`codegen/types_map.py`) — now `i16`. `WORD` was already `i16` with `MAXWORD = 65535` (D-032), so 16-bit machinery already existed in the runtime, which lowered the implementation cost.
- DECISION (taken): make `INTEGER` 16-bit (matching 1981) and introduce `INTEGER32` / `INTEGER64` as explicit, feature-gated extension types for wider arithmetic. The alternative — keeping `INTEGER` 32-bit — was rejected as a silent, pervasive deviation.
- OPTIONS (historical — the second was chosen):
  - **Keep 32-bit (status quo).** *(not taken)* D-014/16/17 remain documented width adaptations (NO-ACTION). Zero churn. Cost: a silent, pervasive deviation — `$MATHCK+` overflow checks do not fire at the vintage boundary (so they can *hide* overflow bugs in ported code), and struct/array layouts, `SIZEOF`/`ADR`, and on-disk record sizes do not match 1981.
  - **16-bit + INTEGER32/64 (the proposal).** *(taken and implemented)* `INTEGER` range / overflow / sentinel / layout match 1981 bit-for-bit; D-014/16/17 became agreements; `$MATHCK+` regained meaning at the right boundary; `MAXINT` became `32767`. Width deviations moved behind explicit, obviously-non-vintage type names. Costs that materialized: a type-system addition (`INTEGER32`/`INTEGER64` with assignment-compatibility, mixed-arithmetic result types, per-width overflow and WRITE/READ formatting), the faithful literal-overflow rejection policy, signedness threading once `INTEGER` joined `WORD` at `i16`, and a small suite migration (the only baseline fixtures affected were ones asserting the old 32-bit width behavior, which were re-asserted to 16-bit, plus a single fixture moved to `INTEGER32`).
- RECOMMENDATION: *(carried out)* for a differential-fidelity project the 16-bit fork was the consistent choice — faithful default, deviations behind explicit opt-in types.
- GOVERNS: the former NO-ACTION classification of D-014 / D-016 / D-017 (now reclassified to resolved-by-width-change); and any future `INTEGER32` / `INTEGER64` work.
- EFFORT: implemented (was L — type-system addition + suite migration).
- VERIFY: PASSED — t014 → `-32768`; t016 → prints `BEFORE` then traps signed overflow at `32767+1`; t017 → `-32768`; full modern suite green (one fixture migrated to `INTEGER32`; width-assertion tests re-asserted to 16-bit; wide-type and literal-policy regression tests added).
- UPGRADES: D-014/16/17 reclassified from "expected width adaptation (NO-ACTION)" to agreements resolved by the width change.
- SEE: `June 12th width discussion.md` (original proposal note).
- XREF: D-014; D-016; D-017; D-032 (WORD already 16-bit); RM-NOACTION; RM-XCUT-FLUSH.

---

# NO-ACTION — documented-correct; do not "fix"

## ANCHOR: RM-NOACTION
- STATUS: NO-ACTION
- **D-014 / D-016 / D-017 — INTEGER width trio: RESOLVED, no longer NO-ACTION.** These were formerly listed here as expected 32-bit width adaptations. `RM-DEC-INTWIDTH` has since taken the 16-bit fork: `INTEGER` is now signed 16-bit, so the sentinel (`-32768`), the `$MATHCK+` overflow trap at `32767+1`, and the `$MATHCK-` wrap to `-32768` all agree with vintage. See the resolved entries in `docs/discrepancies.md` and `RM-DEC-INTWIDTH`. They are kept out of the NO-ACTION list deliberately — do not re-add them as "expected differences."
- **Baselines (AGREE-ACCEPT, no divergence):** t001 (REAL default format ` 1.2345600E+02`), t007 (STRING(3) READ stop-at-fill), t008 (STRING(5) blank-pad + marker), t009 (LSTRING(3) whole-line consume + truncate), t011 (STRING `::N` ignored on both sides), t018 (RESET implicit GET / lazy-fill), t023 (temp-file round-trip), t025 (CLOSE-marker), t027 (RETYPE round-trip), **D-034** (`F.MODE` = 0/0 after REWRITE/RESET), **D-035** (bare `OTHERWISE stmt` grammar accepted), **D-036** (`F^` is blank ORD 32 at EOLN). These are evidence-upgrade confirmations only — no code change.
- WHY LISTED: so a future agent does not mistake a confirmed agreement for an open bug. (The INTEGER width is no longer an open question; it was settled in `RM-DEC-INTWIDTH` and the deviation now lives only behind the explicit `INTEGER32`/`INTEGER64` extension types under `-f wide-integers`.)

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
