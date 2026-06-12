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
- **Probe:** t003.pas (`{$IF 1 $THEN} A {$ELSE} B {$ELSE} C {$END}`)
- **Behavior targeted:** Metacommand skipper handling of duplicate `$ELSE`
- **Class:** REJECT/ACCEPT (vintage accepts multiple `$ELSE` clauses)
- **Vintage (1981):** prints `A` and `C` [OBSERVED]
- **Modern (reimplementation):** prints `A` only [OBSERVED]
- **Adjudication:** Vintage compiler processes both `$ELSE` blocks despite first condition being true; modern implementation stops at first `$ELSE`
- **Cross-references:** checklist item on metacommand semantics; lexer `_skip_source_block` logic
- **Severity:** medium (affects conditional compilation behavior)
- **Follow-up:** Revisit `stop_at_else` fix to allow multiple `$ELSE` processing

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
- **Probe:** t005.pas (`RESET(f); c := f^; GET(f); WRITELN('BEFORE'); PUT(f); WRITELN('AFTER')`)
- **Behavior targeted:** File mode enforcement during PUT after GET
- **Class:** OUTPUT-DIFF
- **Vintage (1981):** prints `BEFORE` followed by runtime error `? Error: Operation error in file T005.DAT Error Code 1110` [OBSERVED]
- **Modern (reimplementation):** aborts with `PUT requires REWRITE/write mode` (exit ≠ 0); `BEFORE` may be missing due to buffering [OBSERVED]
- **Adjudication:** Both enforce mode restrictions but vintage reports error code 1110 and allows `BEFORE` output; modern aborts immediately with descriptive message
- **Cross-references:** checklist item on file runtime semantics
- **Severity:** medium (different error diagnostics despite same semantic enforcement)
- **Follow-up:** Document vintage error code 1110; decide whether modern should mimic vintage's buffered output behavior

