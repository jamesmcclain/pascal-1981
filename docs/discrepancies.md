# Discrepancies Log

## Baselines
- t001.pas — REAL default formatting (` 1.2345600E+02`) — AGREE-ACCEPT

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
- **Class:** REJECT/ACCEPT (vintage accepts multiple `$ELSE` clauses)
- **Vintage (1981):** prints `A` and `C` [OBSERVED]
- **Modern (reimplementation):** prints `A` only [OBSERVED]
- **Adjudication:** Vintage compiler processes both `$ELSE` blocks despite first condition being true; modern implementation stops at first `$ELSE`
- **Cross-references:** checklist item on metacommand semantics; lexer `_skip_source_block` logic
- **Severity:** medium (affects conditional compilation behavior)
- **Follow-up:** Revisit `stop_at_else` fix to allow multiple `$ELSE` processing

## D-004 — { inside string literal in skipped $IF block
- **Probe:** t004.pas (`{$IF 0 $THEN} writeln('{'); {$END} writeln('OK');`)
- **Behavior targeted:** Metacommand skipper quote awareness
- **Class:** REJECT/ACCEPT
- **Vintage (1981):** pas1 rejects with `Unexpected End Of File` and `Program Not Found` errors [OBSERVED]
- **Modern (reimplementation):** accepts; outputs `OK` [OBSERVED]
- **Adjudication:** Vintage skipper not quote-aware — treats `{` inside string as metacommand start; modern lexer correctly skips quoted regions
- **Cross-references:** checklist item on metacommand parsing; lexer `_skip_source_block` quote handling
- **Severity:** medium (breaks conditional compilation with string literals containing `{`)
- **Follow-up:** Document divergence; keep modern quote-aware fix as intentional improvement

## D-005 — PUT after GET in read mode
- **Probe:** t005.pas (`RESET(f); c := f^; GET(f); WRITELN('BEFORE'); PUT(f); WRITELN('AFTER')`)
- **Behavior targeted:** File mode enforcement during PUT after GET
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** prints `BEFORE` followed by runtime error `? Error: Operation error in file T005.DAT Error Code 1110` [OBSERVED]
- **Modern (reimplementation):** aborts with `PUT requires REWRITE/write mode` (exit ≠ 0); `BEFORE` may be missing due to buffering [OBSERVED]
- **Adjudication:** Both enforce mode restrictions but vintage reports error code 1110 and allows `BEFORE` output; modern aborts immediately with descriptive message
- **Cross-references:** checklist item on file runtime semantics
- **Severity:** medium (different error diagnostics despite same semantic enforcement)
- **Follow-up:** Document vintage error code 1110; decide whether modern should mimic vintage's buffered output behavior

## D-006 — READ of an enum value
- **Probe:** t006.pas (`READ(x); WRITELN(ORD(x))` with input `GREEN`)
- **Behavior targeted:** Enum input validity and conversion
- **Class:** ACCEPT/REJECT
- **Vintage (1981):** compiles but fails at runtime with `? Error: Data format error in file USER Error Code 1119` [OBSERVED]
- **Modern (reimplementation):** rejects at typecheck (enum input not supported) [OBSERVED]
- **Adjudication:** Vintage accepts syntax but requires numeric input for enums; modern blocks enum READ entirely. Input `GREEN` is invalid — vintage expects ordinal value
- **Cross-references:** checklist 9.7 on enum I/O; typechecker rules
- **Severity:** medium (modern prevents invalid usage but diverges from vintage's runtime error model)
- **Follow-up:** Implement enum READ as numeric input (matching vintage behavior) rather than full symbolic names


- t007.pas — STRING(3) READ stops at fill (`ABC` / `D` / `E`) — AGREE-ACCEPT

- t008.pas — STRING(5) READ blank-pads and leaves marker (`eol` / `AB   |`) — AGREE-ACCEPT

- t009.pas — LSTRING(3) READ consumes whole line and truncates (`ABC` / `X`) — AGREE-ACCEPT

## D-010 — INTEGER P::N form
- **Probe:** t010.pas (INTEGER `WRITELN(x::4)`)
- **Behavior targeted:** Data-parameter `::N` meaning on INTEGER write values
- **Class:** ACCEPT/REJECT
- **Vintage (1981):** compiled, linked, and then failed at runtime with `? Error: Data format error in file USER` / `Error Code 1123` [OBSERVED]
- **Modern (reimplementation):** accepted and printed `42` [OBSERVED]
- **Adjudication:** vintage treats `x::4` as invalid in this context; modern silently ignores the precision and writes the integer normally [OBSERVED]
- **Cross-references:** checklist 8.3 / I/O formatting; EBNF `io_data_param`
- **Severity:** high (parser/codegen mismatch on integer WRITE formatting)
- **Follow-up:** investigate lowering for integer `::N` to match vintage semantics

## D-011 — STRING P::N form
- **Probe:** t011.pas (STRING `WRITELN(s::3)`)
- **Behavior targeted:** Data-parameter `::N` meaning on STRING write values
- **Class:** ACCEPT/REJECT
- **Vintage (1981):** compiled, linked, and printed `ABCDE` [OBSERVED]
- **Modern (reimplementation):** compiled and printed `ABCDE` [OBSERVED]
- **Adjudication:** vintage ignores the precision on STRING output just like the modern implementation does [OBSERVED]
- **Cross-references:** checklist 8.3 / I/O formatting; EBNF `io_data_param`
- **Severity:** low (accepted but precision operand has no effect)
- **Follow-up:** none

## D-012 — RESET missing-file F.ERRS code
- **Probe:** t012.pas (`ASSIGN(f, NOFILE.XYZ); f.TRAP := TRUE; RESET(f); WRITELN(f.ERRS)`)
- **Behavior targeted:** Vintage `F.ERRS` numeric code for `RESET` on a missing file with trapping enabled
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** compiled, linked, and printed `10` [OBSERVED]
- **Modern (reimplementation):** compiled and printed `1` [OBSERVED]
- **Adjudication:** the vintage error code for missing-file `RESET` is `10`; modern currently uses its own internal code table [OBSERVED]
- **Cross-references:** checklist 8.6 / file error handling; `io_error` table
- **Severity:** medium (error-code mapping mismatch)
- **Follow-up:** renumber modern `io_error` table to match vintage behavior

## D-013 — malformed formatted READ trap behavior
- **Probe:** t013.pas (`ASSIGN(f, T013.DAT); REWRITE(f); WRITELN(f, XYZ); CLOSE(f); RESET(f); f.TRAP := TRUE; READ(f, i); WRITELN(AFTER); WRITELN(f.ERRS)`)
- **Behavior targeted:** Whether a malformed formatted READ is trappable through `f.TRAP` / `f.ERRS`
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** printed `AFTER` and then `14` [OBSERVED]
- **Modern (reimplementation):** aborted with `runtime error: malformed integer input` [OBSERVED]
- **Adjudication:** vintage converts the read-format failure into a trapped file error; modern leaves reader parse errors abort-only [OBSERVED]
- **Cross-references:** checklist 8.6 / readers and file trapping; `io_error` coverage
- **Severity:** medium (trap model gap for formatted readers)
- **Follow-up:** extend reader errors into the trapped I/O path if matching vintage is desired

## D-014 — $INITCK+ sentinel
- **Probe:** t014.pas (`{$INITCK+} VAR x: INTEGER; BEGIN WRITELN(x) END.`)
- **Behavior targeted:** Sentinel value for uninitialized INTEGER under `$INITCK+`
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** printed `-32768` [OBSERVED]
- **Modern (reimplementation):** printed `-2147483648` [OBSERVED]
- **Adjudication:** this is the documented width adaptation: vintage INTEGER is 16-bit, modern INTEGER is 32-bit [OBSERVED]
- **Cross-references:** checklist runtime checks / `$INITCK+`; manual sentinel note
- **Severity:** low (expected width-driven output difference)
- **Follow-up:** none

## D-015 — NIL dereference trap behavior
- **Probe:** t015.pas (`p := NIL; WRITELN(BEFORE); x := p^; WRITELN(AFTER); WRITELN(x)`)
- **Behavior targeted:** NIL pointer dereference under default `$NILCK+`
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** printed `BEFORE` then runtime error `? Error: NIL Pointer Reference` / `Error Code 2031` [OBSERVED]
- **Modern (reimplementation):** aborted on the dereference; `BEFORE` was not preserved in captured stdout, and no runtime text appeared on stderr [OBSERVED]
- **Adjudication:** vintage traps NIL dereference at runtime with code 2031; modern abort path is host-buffering-sensitive on the pre-crash `BEFORE` line [OBSERVED]
- **Cross-references:** checklist runtime checks / `$NILCK+`; nil-pointer runtime
- **Severity:** medium (error-code/abort-model mismatch)
- **Follow-up:** if matching vintage, preserve pre-crash stdout and align nil-check diagnostics

## D-016 — signed integer overflow under $MATHCK+
- **Probe:** t016.pas (`x := 32767; WRITELN(BEFORE); x := x + 1; WRITELN(x)`)
- **Behavior targeted:** INTEGER overflow under default `$MATHCK+`
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** printed `BEFORE` then runtime error `? Error: Signed Math Overflow` / `Error Code 2054` [OBSERVED]
- **Modern (reimplementation):** printed `BEFORE` and `32768` with no error [OBSERVED]
- **Adjudication:** this is the documented 16-bit vs 32-bit width difference; vintage traps overflow, modern does not overflow at i32 width [OBSERVED]
- **Cross-references:** checklist runtime checks / `$MATHCK+`; integer-width note
- **Severity:** low (expected width adaptation)
- **Follow-up:** none

## D-017 — signed overflow with $MATHCK-
- **Probe:** t017.pas (`{$MATHCK-} x := 32767; x := x + 1; WRITELN(x)`)
- **Behavior targeted:** Overflow behavior when math checking is disabled
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** printed `-32768` [OBSERVED]
- **Modern (reimplementation):** printed `32768` [OBSERVED]
- **Adjudication:** vintage wraps silently at 16 bits as the manual claims; modern still uses 32-bit arithmetic, so this is the documented width-driven divergence [OBSERVED]
- **Cross-references:** checklist runtime checks / `$MATHCK-`; integer-width note
- **Severity:** low (expected width adaptation)
- **Follow-up:** none

## D-018 — RESET implicit GET observable form
- **Probe:** t018.pas (`RESET` on empty file then `EOF`; `RESET` on nonempty file then `F^`)
- **Behavior targeted:** Observable form of `RESET`'s implicit GET / lazy-fill behavior
- **Class:** AGREE-ACCEPT
- **Vintage (1981):** printed `eof` then `A` [OBSERVED]
- **Modern (reimplementation):** printed `eof` then `A` [OBSERVED]
- **Adjudication:** the lazy-fill equivalence is confirmed on vintage hardware; no divergence to document [OBSERVED]
- **Cross-references:** checklist runtime checks / `RESET`-implicit-GET; file runtime semantics
- **Severity:** baseline
- **Follow-up:** none

## D-019 — WRITE of an enum value
- **Probe:** t019.pas (`TYPE col = (RED, GREEN, BLUE); x := GREEN; WRITELN(x)`)
- **Behavior targeted:** Enum WRITE acceptance and display format
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** printed `1` [OBSERVED]
- **Modern (reimplementation):** printed `GREEN` [OBSERVED]
- **Adjudication:** vintage writes the ordinal value for enums here; modern has a symbolic-name extension not supported by the 1981 compiler [OBSERVED]
- **Cross-references:** checklist 9.8 / enum WRITE; inferred codegen behavior
- **Severity:** medium (format mismatch and extension beyond vintage)
- **Follow-up:** decide whether to keep symbolic enum WRITE as an extension or align to ordinal output

## D-020 — WRITE of a BOOLEAN value
- **Probe:** t020.pas (`b := TRUE; WRITELN(b); b := FALSE; WRITELN(b)`)
- **Behavior targeted:** BOOLEAN WRITE display format
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** printed `TRUE` then `FALSE` [OBSERVED]
- **Modern (reimplementation):** printed raw byte values `\x01` then `\x00` [OBSERVED]
- **Adjudication:** the vintage compiler formats BOOLEANs textually; the modern implementation is still leaking storage bytes [OBSERVED]
- **Cross-references:** checklist 9.8 / boolean WRITE; latent defect found during campaign drafting
- **Severity:** high (user-visible formatting bug)
- **Follow-up:** fix BOOLEAN output lowering to match vintage text formatting

## Notes — AGREE-REJECT cases from t021-t022
- t021.pas — `EOL` is a ghost: both compilers reject it as an undefined identifier / unknown expression token. No runtime, just dead air.
- t022.pas — `READSET` with `['A'..'Z']` dies the same way on both sides: the set constructor there isn't a character-set literal the vintage pas1 wants, and modern agrees in its own bureaucratic way.
- t023.pas — ASSIGN(f, CHR(0)) temp file round-trip (`42`) — AGREE-ACCEPT

## D-024 — formatted WRITE on file in inspection mode
- **Probe:** t024.pas (`RESET(f); WRITELN('BEFORE'); WRITELN(f, 'BBB'); WRITELN('AFTER'); ...`)
- **Behavior targeted:** Formatted WRITE while a file is open in inspection/read mode
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** printed `BEFORE` then runtime error `? Error: Operation error in file T024.DAT` / `Error Code 1104` [OBSERVED]
- **Modern (reimplementation):** aborted with `file runtime: WRITE requires REWRITE/write mode` [OBSERVED]
- **Adjudication:** both side against write-through, but vintage uses the old file-operation error 1104 while modern throws its own mode-enforcement message [OBSERVED]
- **Cross-references:** checklist file runtime semantics; mode enforcement
- **Severity:** medium (diagnostic mismatch on write-mode violation)
- **Follow-up:** consider aligning error code/message if vintage fidelity is desired

## D-025 — final line marker appended at CLOSE
- **Probe:** t025.pas (`WRITE(f, 'X'); CLOSE(f); RESET(f); READ(f, c); WRITELN(c); IF EOLN(f)...`)
- **Behavior targeted:** Whether CLOSE appends a final TEXT line marker after a WRITE-without-WRITELN
- **Class:** AGREE-ACCEPT
- **Vintage (1981):** printed `X`, `L`, `E` [OBSERVED]
- **Modern (reimplementation):** printed `X`, `L`, `E` [OBSERVED]
- **Adjudication:** the manual's CLOSE-marker claim is confirmed on vintage hardware; both runtimes agree on the observable text semantics [OBSERVED]
- **Cross-references:** TEXT line-marker checklist; manual close-marker note
- **Severity:** baseline
- **Follow-up:** none
- t026.pas — type-prefixed set constructor `COLORS [RED, BLUE]` — AGREE-REJECT-ish: vintage pas1 only warned and likely accepted, while modern type-check rejected `Cannot index non-array type SET OF COLOR`. Needs a rerun if we want to settle whether this is a true ACCEPT/REJECT split or just a compile-time warning path.
- t026.pas — settled rerun: type-prefixed set constructor `COLORS [RED, BLUE]` is ACCEPT/REJECT, not AGREE-REJECT. Vintage pas1 accepted (warnings only), linked, and running the program printed `R`; modern type-check still rejects `Cannot index non-array type SET OF COLOR`.

## Notes — vintage warnings seen in earlier probes
- t021.pas — vintage pas1 rejected `EOL` with `Unknown Identifier In Expression Assumed Zero`, and the listing included a warning-style caret trail rather than a clean hard-stop. Worth remembering: the old compiler complains like a smoker, then still tells you exactly where it choked.
- t022.pas — vintage pas1 rejected `READSET(f, l, ['A'..'Z'])` with `Character Set Expected` after showing the source positions; again, warning-style diagnostics around the fatal parse error.
- t024.pas — vintage pas1 compiled successfully but emitted 3 warnings (`Assumed OUTPUT`) on the `WRITELN` calls before the runtime `Error Code 1104` at execution.
- t025.pas — vintage pas1 compiled successfully but emitted 5 warnings (`Assumed OUTPUT`) around the `WRITELN`/`READLN` lines before running and printing `X / L / E`.
- t026.pas — vintage pas1 compiled successfully with 2 warnings (`Assumed OUTPUT`) and then linked/run printed `R`; the earlier “AGREE-REJECT-ish” note was wrong, but the warnings were real and are now recorded.
- t027.pas — RETYPE round-trip and size-mismatch acceptance (`3` / `65`) — AGREE-ACCEPT
- t028.pas — CASE no-match under default $RANGECK: vintage runtime error `No CASE Value Matches Selector` / `2050` after `BEFORE`, while modern falls through and prints `BEFORE` / `AFTER` — OUTPUT-DIFF

## D-028 — CASE no-match under default $RANGECK
- **Probe:** t028.pas (`n := 5; CASE n OF 1: WRITELN('ONE'); 2: WRITELN('TWO') END; WRITELN('AFTER')`)
- **Behavior targeted:** Default CASE no-match behavior with no `OTHERWISE`
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** printed `BEFORE` and then runtime error `? Error: No CASE Value Matches Selector` / `Error Code 2050` [OBSERVED]
- **Modern (reimplementation):** printed `BEFORE` and `AFTER` [OBSERVED]
- **Adjudication:** vintage traps the unmatched CASE selector under the default range-checking model; modern falls through silently, which is the behavior mismatch the probe was after [OBSERVED]
- **Cross-references:** checklist/runtime CASE semantics; manual note around `RANGECK` and CASE failure
- **Severity:** medium (observable control-flow divergence)
- **Follow-up:** decide whether modern should raise the vintage CASE error or keep fall-through semantics

## D-021 — EOL identifier rejection
- **Probe:** t021.pas (`WRITELN(ORD(EOL))`)
- **Behavior targeted:** Predeclared `EOL` identifier availability
- **Class:** AGREE-REJECT
- **Vintage (1981):** rejected at pas1 with `Unknown Identifier In Expression Assumed Zero` [OBSERVED]
- **Modern (reimplementation):** rejected at typecheck with `Undefined variable: EOL` [OBSERVED]
- **Adjudication:** both compilers agree that `EOL` is not a usable expression-level identifier here; the vintage diagnostic is just the old-school bark [OBSERVED]
- **Cross-references:** checklist around `EOL` / `EOF` / `EOLN`; identifier-table note
- **Severity:** low (same verdict, different wording)
- **Follow-up:** none

## D-022 — READSET character-set literal rejection
- **Probe:** t022.pas (`READSET(f, l, ['A'..'Z'])`)
- **Behavior targeted:** READSET set-constructor form
- **Class:** AGREE-REJECT
- **Vintage (1981):** rejected at pas1 with `Character Set Expected` [OBSERVED]
- **Modern (reimplementation):** rejected at typecheck with `Cannot index non-array type SET OF COLOR` [OBSERVED]
- **Adjudication:** both compilers reject the probe; the source uses a set form neither side accepts in this position [OBSERVED]
- **Cross-references:** manual 12-31; checklist READSET notes
- **Severity:** low (same verdict, different diagnostic path)
- **Follow-up:** none

## D-026 — type-prefixed set constructor `COLORS [RED, BLUE]`
- **Probe:** t026.pas (`s := COLORS [RED, BLUE]; IF RED IN s THEN WRITELN('R'); IF GREEN IN s THEN WRITELN('G')`)
- **Behavior targeted:** Type-prefixed set constructor acceptance
- **Class:** ACCEPT/REJECT
- **Vintage (1981):** accepted with 2 warnings (`Assumed OUTPUT`), linked, and ran; output `R` [OBSERVED]
- **Modern (reimplementation):** rejected at typecheck with `Cannot index non-array type SET OF COLOR` [OBSERVED]
- **Adjudication:** vintage accepts the constructor form and evaluates the set membership; modern does not implement this syntax [OBSERVED]
- **Cross-references:** checklist 2.9; type-prefixed set constructor notes
- **Severity:** medium (grammar/semantics gap)
- **Follow-up:** decide whether to implement the type-prefixed set constructor or document as unsupported
