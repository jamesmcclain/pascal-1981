## D-001 — Smoke Test (REAL Formatting)
- ** Probe:** t001.pas
- ** Behavior targeted:** Default REAL formatting
- ** Class:** AGREE-ACCEPT
- ** Vintage (1981):** pas1 accepted; pas2 produced t001.obj; ran; output:
  ``\ 1.2345600E+02``\ `[OBSERVED]`
- ** Modern (reimpl @ master):** compiled and ran; output:
## D-001 — Smoke Test (REAL Formatting)
- ** Probe:** t001.pas
- ** Behavior targeted:** Default REAL formatting
- ** Class:** AGREE-ACCEPT
- ** Vintage (1981):** pas1 accepted; pas2 produced t001.obj; ran; output:
  `1.2345600E+02` `[OBSERVED]`
- ** Modern (reimpl @ master):** compiled and ran; output:
  `1.2345600E+02` \[
- ** Adjudication:** baseline test; both systems agreed on default format.
- ** Severity:** N/A (Baseline)
## D-001 — Smoke Test (REAL Formatting)
- ** Probe:** t001.pas
- ** Behavior targeted:** Default REAL formatting
- ** Class:** AGREE-ACCEPT
- ** Vintage (1981):** pas1 accepted; pas2 produced t001.obj; ran; output:
  \`1.2345600E+02\` \`[OBSERVED]\]`
- ** Modern (reimpl @ master):** compiled and ran; output:
  \`1.2345600E+02\` \[`[OBSERVED]\]`
- ** Adjudication:** baseline test; both systems agreed on default format.
- ** Severity:** N/A (Baseline)

## D-002 — WRITE data parameter P::N form
- **Probe:**
  ```pascal
  (* t002.pas — probe: WRITE data parameter P::N form.
     Manual 12-17 documents P::N; reimplementation parser rejects it.
     Expected: vintage accepts, modern rejects at parse. *)
  PROGRAM T002;
  VAR x: REAL;
  BEGIN
    x := 123.456;
    WRITELN(x::2)
  END.
  ```
- **Behavior targeted:** Fixed-point REAL formatting via `P::N` syntax
- **Class:** ACCEPT/REJECT
- **Vintage (1981):** pas1 accepted; pas2 produced t002.obj; ran; output:
  `        123.46` `[OBSERVED]`
- **Modern (reimpl @ master):** parser rejected:
  `expected factor at line 8, column 13 (token COLON ':')` `[OBSERVED]`
- **Adjudication:** manual 12-17 explicitly documents `P::N` for WRITE formatting (source line ~13473). Vintage behavior matches specification; reimplementation lacks context-sensitive colon handling. `[READ]`
- **Cross-references:** checklist item 8.3; EBNF `io_data_param` production.
- **Severity:** high — breaks real-world programs using fixed-point output.
