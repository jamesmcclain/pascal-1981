# Discrepancies Log

Classification rule of record: REJECT means a compile stage failed to
produce its artifact; a runtime error after a successful compile+link
is OUTPUT-DIFF, never ACCEPT/REJECT.

## Baselines
- t001.pas — REAL default formatting (` 1.2345600E+02`) — AGREE-ACCEPT
- t007.pas — STRING(3) READ stops at fill (`ABC` / `D` / `E`) — AGREE-ACCEPT
- t008.pas — STRING(5) READ blank-pads and leaves marker (`eol` / `AB   |`) — AGREE-ACCEPT
- t009.pas — LSTRING(3) READ consumes whole line and truncates (`ABC` / `X`) — AGREE-ACCEPT
- t011.pas — STRING `WRITELN(s::3)`: both accept, both print `ABCDE` (precision ignored on both sides) — AGREE-ACCEPT
- t018.pas — RESET implicit GET / lazy-fill (`eof` then `A`) — AGREE-ACCEPT; upgrades the lazy-fill equivalence claim to vintage-[OBSERVED]
- t023.pas — ASSIGN(f, CHR(0)) temp file round-trip (`42`); manual 12-30 documents temp files [READ] — AGREE-ACCEPT
- t025.pas — final line marker appended at CLOSE (`X` / `L` / `E`); confirms the manual's CLOSE-marker claim [READ] — AGREE-ACCEPT
- t027.pas — RETYPE round-trip and CHAR<->INTEGER size-mismatch acceptance (`3` / `65`) — AGREE-ACCEPT

## D-002 — WRITE data parameter P::N form
- **Probe:** t002.pas (REAL `WRITELN(x::2)`)
- **Behavior targeted:** Fixed-point REAL formatting via `P::N`
- **Class:** ACCEPT/REJECT (since RESOLVED in the reimplementation)
- **Vintage (1981):** accepted; output `        123.46` [OBSERVED]
- **Modern (at time of probe):** parser rejected; later fixed — modern now matches the vintage output byte-for-byte [OBSERVED]
- **Adjudication:** manual 12-17 documents `P::N` [READ]
- **Cross-references:** checklist 8.3; EBNF `io_data_param`; patch `d002-p-colon-colon-n`.
- **Severity:** was high; resolved.

## D-003 — Duplicate $ELSE directives
- **Probe:** t003.pas (`{$IF 1 $THEN} A {$ELSE} B {$ELSE} C {$END}`)
- **Behavior targeted:** Metacommand skipper handling of duplicate `$ELSE`
- **Class:** OUTPUT-DIFF (both compile and run; outputs differ)
- **Vintage (1981):** compiled, linked, ran; prints `A` and `C` [OBSERVED]
- **Modern (reimplementation):** compiled, ran; prints `A` only [OBSERVED]
- **Adjudication:** the vintage skipper resumes emission at the second `$ELSE` despite the true first branch; the modern `stop_at_else` fix skips to `$END`. Mechanism is inferred from output; the manual does not document duplicate `$ELSE`. [INFERRED]
- **Cross-references:** checklist metacommand semantics (~line 948/1093); lexer `_skip_source_block`.
- **Severity:** medium (conditional-compilation divergence)
- **Follow-up:** revisit the `stop_at_else` fix to match vintage multi-`$ELSE` processing, or document as a deliberate divergence.

## D-004 — { inside string literal in skipped $IF block
- **Probe:** t004.pas (`{$IF 0 $THEN} writeln('{'); {$END} writeln('OK');`)
- **Behavior targeted:** Metacommand skipper quote awareness
- **Class:** REJECT/ACCEPT
- **Vintage (1981):** pas1 rejects with `Unexpected End Of File` and `Program Not Found` errors [OBSERVED]
- **Modern (reimplementation):** accepts; outputs `OK` [OBSERVED]
- **Adjudication:** consistent with a vintage skipper that is not quote-aware — it treats `{` inside the string as nested comment/metacommand start and runs off the end of the file. Mechanism inferred from the diagnostics; not documented in the manual. [INFERRED]
- **Cross-references:** checklist metacommand parsing (~line 948/1093); lexer `_skip_source_block` quote handling.
- **Severity:** medium
- **Follow-up:** keep the modern quote-aware fix as an intentional, documented divergence.

## D-005 — PUT after GET in read mode
- **Probe:** t005.pas (`RESET(f); c := f^; GET(f); WRITELN('BEFORE'); PUT(f); WRITELN('AFTER')`)
- **Behavior targeted:** File mode enforcement during PUT after GET
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** prints `BEFORE` followed by runtime error `? Error: Operation error in file T005.DAT Error Code 1110` [OBSERVED]
- **Modern (reimplementation):** aborts with `PUT requires REWRITE/write mode` (exit ≠ 0); `BEFORE` missing from captured stdout — known host libc buffering artifact on abort paths [OBSERVED]
- **Adjudication:** both runtimes enforce mode restrictions; the vintage error code for this operation error is 1110. [INFERRED] (code value itself [OBSERVED])
- **Cross-references:** checklist file runtime semantics; campaign plan t005.
- **Severity:** medium (diagnostic mismatch; same semantic enforcement)
- **Follow-up:** record code 1110 in the `io_error` table notes; the modern strict-abort model is kept by design.

## D-006 — READ of an enum value
- **Probe:** t006.pas (`READ(x); WRITELN(ORD(x))` with input `GREEN`)
- **Behavior targeted:** Enum input validity and conversion
- **Class:** ACCEPT/REJECT
- **Vintage (1981):** compiles and links; fails at runtime with `? Error: Data format error in file USER Error Code 1119` on symbolic input `GREEN` [OBSERVED]
- **Modern (reimplementation):** rejects at typecheck (enum READ not supported) [OBSERVED]
- **Adjudication:** vintage accepts enum READ syntactically; the runtime data-format error on `GREEN` suggests it expects numeric/ordinal input rather than symbolic names, but this probe did not test numeric input directly. [INFERRED]
- **Cross-references:** checklist 9.7 enum I/O (~line 1062); typechecker rules.
- **Severity:** medium
- **Follow-up:** follow-on probe feeding a numeric ordinal to confirm the accepted input form before implementing enum READ.

## D-010 — INTEGER P::N form
- **Probe:** t010.pas (INTEGER `WRITELN(x::4)`)
- **Behavior targeted:** Data-parameter `::N` meaning on INTEGER write values
- **Class:** OUTPUT-DIFF (both compile; vintage errors at runtime, modern runs)
- **Vintage (1981):** compiled and linked; failed at runtime with `? Error: Data format error in file USER` / `Error Code 1123` [OBSERVED]
- **Modern (reimplementation):** accepted and printed `42` [OBSERVED]
- **Adjudication:** the vintage runtime treats `::N` on an INTEGER as a data-format error; the modern build silently ignores the precision operand. [INFERRED]
- **Cross-references:** checklist 8.3 / I/O formatting; EBNF `io_data_param`.
- **Severity:** high (modern accepts and runs a form the vintage runtime rejects)
- **Follow-up:** make modern integer `::N` a runtime (or compile-time) error to match, or document the leniency as an extension.

## D-012 — RESET missing-file F.ERRS code
- **Probe:** t012.pas (`ASSIGN(f, 'NOFILE.XYZ'); f.TRAP := TRUE; RESET(f); WRITELN(f.ERRS)`)
- **Behavior targeted:** Vintage `F.ERRS` numeric code for `RESET` on a missing file with trapping enabled
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** compiled, linked, printed `10` [OBSERVED]
- **Modern (reimplementation):** compiled, printed `1` (invented internal code) [OBSERVED]
- **Adjudication:** the vintage missing-file `RESET` error code is 10; the modern table is internal-only. [INFERRED] (value [OBSERVED])
- **Cross-references:** checklist 8.6 / file error handling; `io_error` table.
- **Severity:** medium
- **Follow-up:** renumber the modern `io_error` table to the vintage values as they are observed (10 = missing file on RESET; 14 = malformed formatted READ, see D-013).

## D-013 — malformed formatted READ trap behavior
- **Probe:** t013.pas (`REWRITE`+`WRITELN(f,'XYZ')`; `RESET(f); f.TRAP := TRUE; READ(f, i); WRITELN('AFTER'); WRITELN(f.ERRS)`)
- **Behavior targeted:** Whether a malformed formatted READ is trappable through `f.TRAP` / `f.ERRS`
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** printed `AFTER` then `14` [OBSERVED]
- **Modern (reimplementation):** aborted with `runtime error: malformed integer input` [OBSERVED]
- **Adjudication:** vintage routes reader format failures into the trapped file-error path; modern readers are abort-only. [INFERRED]
- **Cross-references:** checklist 8.6 / readers and file trapping; `io_error` coverage.
- **Severity:** medium (trap-model gap for formatted readers)
- **Follow-up:** extend `io_error` coverage to the formatted readers (code 14 observed).

## D-014 — $INITCK+ sentinel
- **Probe:** t014.pas (`{$INITCK+} VAR x: INTEGER; BEGIN WRITELN(x) END.`)
- **Behavior targeted:** Sentinel value for uninitialized INTEGER under `$INITCK+`
- **Class:** OUTPUT-DIFF (expected — documented width adaptation)
- **Vintage (1981):** printed `-32768` [OBSERVED]
- **Modern (reimplementation):** printed `-2147483648` [OBSERVED]
- **Adjudication:** matches the manual's `-32768` sentinel at 16-bit width [READ]; the modern value is the same sentinel at the documented 32-bit width adaptation.
- **Cross-references:** checklist runtime checks / `$INITCK+`; integer-width note.
- **Severity:** low (expected width-driven difference)
- **Follow-up:** none.

## D-015 — NIL dereference trap behavior
- **Probe:** t015.pas (`p := NIL; WRITELN('BEFORE'); x := p^; WRITELN('AFTER'); WRITELN(x)`)
- **Behavior targeted:** NIL pointer dereference under default `$NILCK+`
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** printed `BEFORE` then runtime error `? Error: NIL Pointer Reference` / `Error Code 2031` [OBSERVED]
- **Modern (reimplementation):** aborted on the dereference; `BEFORE` not preserved in captured stdout (known buffering artifact on abort paths), no runtime text on stderr [OBSERVED]
- **Adjudication:** both trap the dereference; vintage code is 2031 and its runtime writes through unbuffered, consistent with the campaign plan's expectation. [INFERRED] (code value [OBSERVED])
- **Cross-references:** checklist runtime checks / `$NILCK+`.
- **Severity:** medium (diagnostic/abort-model mismatch; host abort model differs by design)
- **Follow-up:** record only, per campaign plan; consider flushing stdout before modern aborts.

## D-016 — signed integer overflow under $MATHCK+
- **Probe:** t016.pas (`x := 32767; WRITELN('BEFORE'); x := x + 1; WRITELN(x)`)
- **Behavior targeted:** INTEGER overflow under default `$MATHCK+`
- **Class:** OUTPUT-DIFF (expected — documented width adaptation)
- **Vintage (1981):** printed `BEFORE` then runtime error `? Error: Signed Math Overflow` / `Error Code 2054` [OBSERVED]
- **Modern (reimplementation):** printed `BEFORE` and `32768`, no error [OBSERVED]
- **Adjudication:** 16-bit vintage overflows and traps (code 2054); 32-bit modern does not overflow at this value — the documented width adaptation. [INFERRED] (code value [OBSERVED])
- **Cross-references:** checklist runtime checks / `$MATHCK+`; integer-width note.
- **Severity:** low (expected)
- **Follow-up:** none.

## D-017 — signed overflow with $MATHCK-
- **Probe:** t017.pas (`{$MATHCK-} x := 32767; x := x + 1; WRITELN(x)`)
- **Behavior targeted:** Overflow behavior when math checking is disabled
- **Class:** OUTPUT-DIFF (expected — documented width adaptation)
- **Vintage (1981):** printed `-32768` [OBSERVED]
- **Modern (reimplementation):** printed `32768` [OBSERVED]
- **Adjudication:** confirms the manual's claim that `$MATHCK-` disables the overflow check (silent 16-bit wrap) [READ]; the modern value is the width adaptation.
- **Cross-references:** checklist runtime checks / `$MATHCK-`; integer-width note.
- **Severity:** low (expected)
- **Follow-up:** none.

## D-019 — WRITE of an enum value
- **Probe:** t019.pas (`TYPE col = (RED, GREEN, BLUE); x := GREEN; WRITELN(x)`)
- **Behavior targeted:** Enum WRITE acceptance and display format
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** compiled, linked, printed `1` [OBSERVED]
- **Modern (reimplementation):** printed `GREEN` [OBSERVED]
- **Adjudication:** vintage writes the ordinal; the modern symbolic-name output is an extension with no vintage or manual basis. [INFERRED]
- **Cross-references:** checklist 9.8 / enum WRITE (built [INFERRED]).
- **Severity:** medium
- **Follow-up:** decide: align to ordinal output for fidelity, or keep symbolic names as a documented extension.

## D-020 — WRITE of a BOOLEAN value
- **Probe:** t020.pas (`b := TRUE; WRITELN(b); b := FALSE; WRITELN(b)`)
- **Behavior targeted:** BOOLEAN WRITE display format
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** printed `TRUE` then `FALSE` [OBSERVED]
- **Modern (reimplementation):** printed raw storage bytes `\x01` then `\x00` (known latent defect, deliberately left unfixed pending this probe) [OBSERVED]
- **Adjudication:** vintage formats BOOLEANs as uppercase text with no observed leading padding in this capture. [INFERRED]
- **Cross-references:** checklist 9.8 / boolean WRITE.
- **Severity:** high (user-visible formatting bug)
- **Follow-up:** fix BOOLEAN WRITE lowering to print `TRUE`/`FALSE`; add a field-width probe if padding matters later.

## D-024 — formatted WRITE on file in inspection mode
- **Probe:** t024.pas (`RESET(f); WRITELN('BEFORE'); WRITELN(f, 'BBB'); WRITELN('AFTER'); ...` then read-back)
- **Behavior targeted:** Formatted WRITE while a file is open in inspection/read mode
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** printed `BEFORE` then runtime error `? Error: Operation error in file T024.DAT` / `Error Code 1104` [OBSERVED]
- **Modern (reimplementation):** aborted with `file runtime: WRITE requires REWRITE/write mode` [OBSERVED]
- **Adjudication:** both sides prevent write-through; vintage uses file-operation error 1104 (distinct from PUT's 1110, D-005), modern uses its own mode-enforcement message. [INFERRED] (code value [OBSERVED])
- **Cross-references:** checklist file runtime semantics; mode enforcement; D-005.
- **Severity:** medium (diagnostic mismatch)
- **Follow-up:** record code 1104; align only if vintage fidelity in diagnostics is desired.

## D-026 — type-prefixed set constructor `COLORS [RED, BLUE]`
- **Probe:** t026.pas (`s := COLORS [RED, BLUE]; IF RED IN s THEN WRITELN('R'); IF GREEN IN s THEN WRITELN('G')`)
- **Behavior targeted:** Type-prefixed set constructor acceptance
- **Class:** ACCEPT/REJECT (settled on rerun; an earlier run misread vintage warnings as rejection)
- **Vintage (1981):** pas1 accepted with 2 warnings (`Assumed OUTPUT`), linked, ran; output `R` [OBSERVED]
- **Modern (reimplementation):** rejected at typecheck with `Cannot index non-array type SET OF COLOR` [OBSERVED]
- **Adjudication:** vintage implements the type-prefixed set constructor; modern parses it as indexing and rejects. [INFERRED]
- **Cross-references:** checklist 2.9 (built [INFERRED] — now vintage-confirmed as real syntax).
- **Severity:** medium (grammar/semantics gap)
- **Follow-up:** implement the type-prefixed set constructor.

## D-028 — CASE no-match under default $RANGECK
- **Probe:** t028.pas (`n := 5; CASE n OF 1: WRITELN('ONE'); 2: WRITELN('TWO') END; WRITELN('AFTER')`)
- **Behavior targeted:** Default CASE no-match behavior with no `OTHERWISE`
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** printed `BEFORE` then runtime error `? Error: No CASE Value Matches Selector` / `Error Code 2050` [OBSERVED]
- **Modern (reimplementation):** printed `BEFORE` and `AFTER` (silent fall-through) [OBSERVED]
- **Adjudication:** manual (src ~9953) documents a runtime error when `$RANGECK` is on and no OTHERWISE matches; this run confirms `$RANGECK` is on by default and the error code is 2050 [READ] (code value [OBSERVED]).
- **Cross-references:** checklist runtime CASE semantics; `$RANGECK`.
- **Severity:** medium (observable control-flow divergence)
- **Follow-up:** emit the no-match CASE trap under default checking in modern codegen.

## Open items and probe-redesign notes

- **t021 (`WRITELN(ORD(EOL))`) — verdict suspect; rerun required.** Vintage pas1 emitted `Unknown Identifier In Expression Assumed Zero`, which is pas1's warning-with-recovery idiom, not a clean hard stop; the run was logged as AGREE-REJECT before the warnings-are-not-rejections rule was adopted (cf. the t026 misread). Whether pas2 produced an `.obj` and what the exe printed was not verified. Modern rejects at typecheck (`Undefined variable: EOL`) [OBSERVED]. Until rerun, the vintage verdict is [UNVERIFIED]; the EOL checklist item (~line 1032) stays open.
- **t022 (`READSET(f, l, ['A'..'Z'])`) — AGREE-REJECT; redesign.** Vintage pas1: `Character Set Expected` [OBSERVED]. Modern also rejected at typecheck (exact diagnostic not reliably captured — the originally recorded text appears to have been copy-pasted from t026 and is discarded). The delimiter-retention question remains [UNVERIFIED]. Redesign: pass a declared `SET OF CHAR` value, e.g. via a type-prefixed constructor (`CHARSET ['A'..'Z']`, now known-good per D-026) or a set variable.

## Notes — vintage warning idioms observed (not rejections)

pas1 emits recoverable warnings that do not imply rejection; the verdict is
whether the pipeline produced the next stage's artifact. Observed idioms:

- `Assumed OUTPUT` — on `WRITELN` calls without a program-heading file list (t024: 3, t025: 5, t026: 2); all compiled, linked, and ran.
- `Unknown Identifier In Expression Assumed Zero` — substitutes zero and may continue (t021; see open item above).
- t029.pas — READSET delimiter retention with declared set variable (`ABC` / `,`) — AGREE-ACCEPT

## D-030 — enum READ with numeric input
- **Probe:** t030.pas (`READ(f, x); WRITELN(ORD(x))` with input `1`)
- **Behavior targeted:** Enum READ numeric-input behavior, per the D-006 follow-up
- **Class:** ACCEPT/REJECT
- **Vintage (1981):** accepted compile (1 warning: `Assumed OUTPUT`), linked, ran, and printed `1` [OBSERVED]
- **Modern (reimplementation):** rejected at typecheck with `READ argument 2 has unreadable type COL` [OBSERVED]
- **Adjudication:** the vintage compiler accepts enum READ when the input is numeric, confirming the ordinal-input hypothesis from D-006 [OBSERVED]
- **Cross-references:** D-006 follow-up; checklist around enum I/O
- **Severity:** medium (missing runtime support for enum input)
- **Follow-up:** implement enum READ against numeric ordinals

## D-031 — PACK/UNPACK round-trip and index convention
- **Probe:** t031.pas (`PACK(a, 2, z); WRITELN(z); UNPACK(z, b, 3); ...`)
- **Behavior targeted:** PACK/UNPACK semantics and index base
- **Class:** ACCEPT/REJECT
- **Vintage (1981):** compiled with 3 warnings (`Assumed OUTPUT`), linked, and ran; output `BCD` then `..BCD.` [OBSERVED]
- **Modern (reimplementation):** rejected at typecheck with `WRITE argument 1 has unwritable type PACKED ARRAY[1..3] OF CHAR` [OBSERVED]
- **Adjudication:** vintage accepts PACK/UNPACK and uses the expected index convention; modern rejects the packed-string write path here [OBSERVED]
- **Cross-references:** checklist 4.9; manual PACK/UNPACK index notes
- **Severity:** medium (missing PACK/UNPACK support / packed-array write gap)
- **Follow-up:** implement PACK/UNPACK and packed-char-array write support

## D-032 — WORD / MAXWORD / WRD edges
- **Probe:** t032.pas (`WRITELN(MAXWORD); w := 40000; WRITELN(w); w := WRD(-1); WRITELN(w)`)
- **Behavior targeted:** WORD range, display, and negative-to-WORD conversion
- **Class:** ACCEPT/REJECT
- **Vintage (1981):** compiled with 3 warnings (`Assumed OUTPUT`), linked, and ran; output `65535`, `40000`, `65535` [OBSERVED]
- **Modern (reimplementation):** rejected at typecheck with `Cannot assign INTEGER to WORD` [OBSERVED]
- **Adjudication:** vintage treats WORD as a 16-bit unsigned range with `WRD(-1) = 65535`; modern lacks the conversion/assignment path here [OBSERVED]
- **Cross-references:** checklist 4.9 / WORD, MAXWORD, WRD; manual edges around WORD range
- **Severity:** medium (missing WORD conversion support)
- **Follow-up:** implement WORD assignment/conversion semantics, including WRD and MAXWORD

## D-033 — NULL LSTRING constant length and display
- **Probe:** t033.pas (`l := NULL; WRITELN(ORD(l.LEN)); WRITELN('<', l, '>')`)
- **Behavior targeted:** `NULL` LSTRING length and textual form
- **Class:** ACCEPT/REJECT
- **Vintage (1981):** compiled with 2 warnings (`Assumed OUTPUT`), linked, and ran; output `0` then `<>` [OBSERVED]
- **Modern (reimplementation):** rejected at typecheck with `Cannot access field on non-record type LSTRING(5)` [OBSERVED]
- **Adjudication:** vintage treats `NULL` as a zero-length LSTRING constant and prints it as empty between delimiters [OBSERVED]
- **Cross-references:** manual ~5731; checklist `NULL` LSTRING note
- **Severity:** medium (missing NULL/LSTRING semantics)
- **Follow-up:** implement `NULL` as a zero-length LSTRING constant with the vintage length semantics

## D-034 — F.MODE values after REWRITE and RESET
- **Probe:** t034.pas (`REWRITE(f); WRITELN(ORD(f.MODE)); ... RESET(f); WRITELN(ORD(f.MODE))`)
- **Behavior targeted:** FILEMODES layout / observable `F.MODE` values
- **Class:** AGREE-ACCEPT
- **Vintage (1981):** compiled with 2 warnings (`Assumed OUTPUT`), linked, and ran; output `0` then `0` [OBSERVED]
- **Modern (reimplementation):** compiled and ran; output `0` then `0` [OBSERVED]
- **Adjudication:** both sides agree that `ORD(f.MODE)` is `0` in the REWRITE and RESET states observed here [OBSERVED]
- **Cross-references:** manual 12-32; FILEMODES layout notes
- **Severity:** baseline
- **Follow-up:** none

## D-035 — OTHERWISE clause grammar
- **Probe:** t035.pas (`CASE n OF ... OTHERWISE WRITELN('OTHER') END; WRITELN('AFTER')`)
- **Behavior targeted:** CASE `OTHERWISE` arm syntax without a colon
- **Class:** AGREE-ACCEPT
- **Vintage (1981):** compiled with 4 warnings (`Assumed OUTPUT`), linked, and ran; output `OTHER` then `AFTER` [OBSERVED]
- **Modern (reimplementation):** compiled and ran; output `OTHER` then `AFTER` [OBSERVED]
- **Adjudication:** vintage accepts the bare `OTHERWISE stmt` form; grammar confirmed [OBSERVED]
- **Cross-references:** checklist/CASE grammar extension; D-028 follow-on
- **Severity:** baseline
- **Follow-up:** none

## D-036 — F^ blank at line marker
- **Probe:** t036.pas (`c := f^; WRITELN(ORD(c)); GET(f); IF EOLN(f) THEN WRITELN('L'); WRITELN(ORD(f^))`)
- **Behavior targeted:** Presentation of `F^` at a TEXT line marker
- **Class:** AGREE-ACCEPT
- **Vintage (1981):** compiled with 4 warnings (`Assumed OUTPUT`), linked, and ran; output `65`, `L`, `32` [OBSERVED]
- **Modern (reimplementation):** compiled and ran; output `65`, `L`, `32` [OBSERVED]
- **Adjudication:** vintage confirms the manual's blank-at-EOLN behavior; the `F^` presentation is a space (ORD 32) [OBSERVED]
- **Cross-references:** checklist TEXT line-marker semantics; manual EOLN/F^ notes
- **Severity:** baseline
- **Follow-up:** none
