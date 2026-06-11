# Plan: Finish Section 8 — items 8.4, 8.5, 8.6

Status: PLANNED — not yet executed.
Audience: any agent/engineer; self-contained. Read alongside
`docs/Grand_Unified_Checklist.md` §8 and `docs/plans/8.2-remediation.md`
(executed; describes the FCB/runtime architecture this plan builds on).

## 0. Evidence discipline (read first)

The IBM Pascal manual OCR text is at
`/home/ubuntu/backup/IBM_Pascal_Compiler_Aug81_djvu.txt` (NOT in the repo).
Line numbers below refer to that file; quotes are from it and are marked
`[READ]`. The OCR is noisy (e.g. `F^` renders as `FA`, `CONST S:` as
`CONST S.`); re-grep before relying on exact characters. Anything still
marked `[VERIFY]` must be confirmed in the manual body before closing the
corresponding checklist item — do not close items on unverified semantics;
that is the failure mode that got 8.2 reopened.

## 1. Current state (verified in-repo at commit `3d547cb`)

- FCB layout (single definition, `codegen/base.py::file_fcb_type`):
  `{ i32 elem_size, i32 structure (0=binary,1=TEXT), i32 touched, i32 mode,
     i8* buffer, i8* handle }`.
  Mode word encoding (see `runtime/fileops.c`): low 2 bits 0=closed,
  1=read/inspection, 2=write/generation; bit `0x4` = eof.
- `RESET`/`REWRITE`/`GET`/`PUT` are real: `tmpfile()` backing stream,
  `fread`/`fwrite`, implicit GET in RESET, truncation in REWRITE. Files are
  *anonymous* — no filename binding yet.
- `INPUT`/`OUTPUT` exist as predeclared TEXT FCBs (`codegen/base.py` ~116,
  `@"input"`/`@"output"` globals) but are NOT attached to stdin/stdout.
- `READ`/`READLN` lower to `runtime/readq.c` helpers that hardcode
  stdin (`scanf`); `WRITE`/`WRITELN` go to stdout via printf. The typechecker
  (8.3a) already accepts a leading TEXT file selector for WRITE/WRITELN and
  codegen *skips* it (`codegen/io_write_read.py` ~63) — file-directed output
  is explicitly punted to here.
- Run-test harness: `tests/test_runtime_fixes.py::build_run_linked(src,
  runtime_files, stdin=...)` compiles IR + named `runtime/*.c` files with
  clang. `tests/test_read_end_to_end.py` shows the piped-stdin pattern.
- Test suite: `python -m unittest discover tests` — 343 green.

## 2. Dependency order

8.4 and 8.5 are interlocked: EOF/EOLN need a real notion of "the stream"
(including stdin for `EOF(INPUT)`), and filename binding (8.5 `ASSIGN`)
is what makes file-directed I/O testable against real files. 8.6 is mostly
surface (predeclared type/constants) once 8.4/8.5 exist. Recommended order:

1. **Phase A** (foundation, no new user-visible verbs): attach
   `INPUT`/`OUTPUT` to stdin/stdout through the FCB; route file-directed
   READ/WRITE through the FCB handle.
2. **Phase B = 8.4**: `EOF`, `EOLN` (and the `EOL` constant), TEXT
   line-marker semantics.
3. **Phase C = 8.5**: `ASSIGN`, `CLOSE`, `DISCARD`, `READFN`, `READSET`.
4. **Phase D = 8.6**: `FILEMODES`, `SEQUENTIAL`, `TERMINAL`, `FCBFQQ` —
   gated on manual verification.

Each phase ends with a green full suite and its own commit; update the
checklist entry per phase, in the established Done/Proof/Does-not-cover style.

---

## Phase A — stream plumbing (prerequisite, fold into the 8.4 commit or keep separate)

A1. **Attach INPUT/OUTPUT.** At program startup (or lazily on first use),
    set INPUT's FCB handle to `stdin`, mode=read; OUTPUT's to `stdout`,
    mode=write. Implementation choice: a runtime initializer
    `pas_file_attach_std(fcb_in, fcb_out)` in `fileops.c` called from main's
    prologue, or lazy attachment inside the helpers when `handle == NULL` and
    a new FCB flag says "std file". Pick one; document it. **Constraint:**
    `RESET(INPUT)`/`REWRITE(OUTPUT)` must not `fclose` stdin/stdout and a
    `tmpfile()` must not replace them — add a "don't close/replace" flag
    (suggest mode bit `0x8 = MODE_STD`).
    - CAUTION: `pas_file_reset` does an implicit GET. `RESET(INPUT)` on an
      attached stdin would consume a character at startup if done eagerly.
      IBM Pascal auto-RESETs INPUT; the buffered first component is the
      correct lazy-evaluation seam — the `touched` flag exists for exactly
      this. Implement lazy fill: mark the buffer "unfilled" and let the first
      `f^` read / EOF query / GET trigger the actual read. This is the
      "lazy fill/flush the touch hook is a seam for" noted in checklist 8.1.
A2. **File-directed READ/WRITE.** Extend the readq/printf paths to take the
    FCB when a leading file selector is present:
    - WRITE side: replace direct `printf` with `fprintf(handle, ...)`-style
      helpers, or keep printf for the no-selector case and add `pas_fwrite_*`
      variants. Smallest diff: pass the FCB (or NULL meaning stdout) as a
      first arg to the existing write helpers.
    - READ side: same treatment for `pas_read_int/word/real/char/lstring`
      (`runtime/readq.c`) — switch `scanf` to `fscanf(h, ...)`.
    - Codegen: `codegen/io_write_read.py` currently *skips* the leading TEXT
      selector; change it to resolve the selector's FCB pointer and thread it
      through. Typechecker already validates the selector (8.3a) — verify
      READ/READLN get the same leading-selector acceptance (they may not yet;
      check `_check_read_args`).
A3. **Tests (Phase A):** run tests proving `WRITE(f, ...)` lands in `f`'s
    stream (write to f, RESET, GET chars back); `READ(f, x)` reads from `f`
    not stdin (populate f via PUT, then READ with *different* data piped to
    stdin — the test fails if the read leaks to stdin); INPUT/OUTPUT still
    work for plain READLN/WRITELN (existing `test_read_end_to_end` must stay
    green).

## Phase B — 8.4: `EOF`, `EOL`, `EOLN`

Manual-verified semantics `[READ]`:
- `FUNCTION EOF: BOOLEAN` / `FUNCTION EOF (VAR F): BOOLEAN` (manual 12-10,
  txt ~13178): "if EOF (F) is true either the file is being written or the
  last GET reached the end of the file. EOF with no parameter is equivalent
  to EOF (INPUT)." Note: **EOF is true while in write/generation mode** —
  our 8.2 runtime already sets the eof bit on REWRITE; keep that.
  Also: "Calling the EOF (F) function also accesses the buffer variable F^,
  causing a GET if no previous GET was done, because 'lazy evaluation'
  defers the initial GET." This confirms the Phase A1 lazy-fill design is
  manual-required, and that EOF must force the pending fill.
- `FUNCTION EOLN: BOOLEAN` / `(VAR F)` (manual 12-11, txt ~13202): TEXT/ASCII
  structure only; "Calling EOLN (F) when EOF (F) is true is an error in ISO
  Pascal usually caught in IBM Pascal"; "If EOLN (F) is true the value of F^
  is a blank but the file is positioned at a 'line marker.'" EOLN also
  forces buffer access (lazy fill). No parameter ⇒ EOLN(INPUT).
- `EOL`: appears **only** in the predeclared-identifier table (txt 3751);
  no body documentation found by grep. `[VERIFY]` — search harder during
  execution (OCR may mangle it); if no semantics surface, leave `EOL` open
  in the checklist with a note rather than inventing a meaning.

Work items:
B1. Registry/typecheck: add `EOF`, `EOLN` to `BUILTIN_FUNCTIONS`
    (`builtins_registry.py`), returning BOOLEAN, with 0-or-1 file argument
    (default INPUT). TEXT-only check for EOLN. Add `EOL` per verified meaning.
B2. Runtime (`fileops.c`): `int pas_file_eof(fcb)` returns the eof bit
    (after forcing any pending lazy fill — an unfilled INPUT buffer must
    trigger the read so `EOF(INPUT)` is accurate before first GET).
    `int pas_file_eoln(fcb)`: current TEXT component is `'\n'`.
    Decide and document the line-marker model: store `'\n'` in the stream
    (simplest on a Unix host), present `f^ = ' '` when EOLN per standard
    rules if the manual confirms that behavior.
B3. `READLN`'s `pas_readln_skip` and the WRITE `'\n'` path must agree with
    the line-marker model when file-directed (Phase A2).
B4. Codegen: lower `EOF(f)`/`EOLN(f)` in the expression path
    (`codegen/exprs.py` builtin-function dispatch) to the runtime helpers;
    zero-arg forms resolve to the INPUT FCB.
B5. Tests:
    - `WHILE NOT EOF(f) DO ...` loop copying a file's chars; count matches.
    - `EOLN` flips at line boundary; `READLN` consumes the marker.
    - `EOF(INPUT)` end-to-end with piped stdin of known length.
    - Hostile check: stub `pas_file_eof` to return constant 0 locally and
      confirm the loop test hangs/fails (then restore) — i.e., tests must
      actually depend on the predicate.
    - Negative: `EOLN(binfile)` rejected at typecheck.
B6. Checklist: close 8.4 with proof lines; note anything deferred (e.g.,
    DIRECT-mode interaction → 8.6).

## Phase C — 8.5: `ASSIGN`, `CLOSE`, `DISCARD`, `READFN`, `READSET`

Manual-verified semantics `[READ]` (manual 12-28..12-31, txt ~13939–14130):
- `PROCEDURE ASSIGN (VAR F; CONST N: STRING)` — binds a DOS filename in a
  STRING/LSTRING to F; "always truncates any trailing blanks, and overrides
  any filename set previously." A name must be set **before** the first
  RESET/REWRITE; "ASSIGN on an open file (after RESET or REWRITE but before
  CLOSE) produces an error." ASSIGN on INPUT/OUTPUT allowed only after
  CLOSEing them. Special case: `ASSIGN (F, CHR(0))` requests a **temporary
  file** with a system-generated unique name; "Temporary files get deleted
  when they are CLOSEd" (12-30). Host mapping: our existing unbound-tmpfile
  behavior is therefore exactly the CHR(0) case; named ASSIGN maps to
  `fopen` on the host filesystem (drive/8.3-name validation is DOS-specific;
  accept host paths, do not emulate 8.3 limits).
- `PROCEDURE CLOSE (VAR F)` — DOS close; if TEXT being written and last
  non-empty line lacked a line marker, **one is appended**; CLOSE on a
  closed/never-opened file is permitted (no error).
- `PROCEDURE DISCARD (VAR F)` — "closes and deletes an open file. It is
  much like CLOSE, except that the file is deleted."
- `PROCEDURE READSET (VAR F, VAR L: LSTRING, CONST S: SETOFCHAR)` (12-31) —
  "reads characters and puts them into L as long as the characters are in
  the set S and there is room in L. If no file parameter is given INPUT is
  assumed." Leading spaces/tabs/form feeds/line markers are always skipped;
  reading ceases at a line marker. Depends on SET OF CHAR support — check
  the state of sets (`codegen/sets.py`) before scheduling; if SETOFCHAR
  isn't predeclared yet, add it as part of this item.
- `PROCEDURE READFN (VAR F, P1, P2, ...Pn)` (12-31) — "the same as READLN
  with two exceptions: 1) the file parameter F should be present (INPUT is
  assumed but a warning is given), and 2) if a parameter P is of type FILE,
  a sequence of characters forming a valid filename is read from F and
  assigned to P in the same manner as ASSIGN." Unlike READLN it does **not**
  consume the trailing line marker.

Work items:
C1. FCB extension: add a name slot. Options: widen the FCB with an `i8*
    name` field (touch `codegen/base.py` struct + `_init_file_storage`
    zeroing + `fileops.c` struct, all in one commit — they must stay in
    lockstep; see 8.2-remediation §7 risk note), or store the name in a
    runtime-side table keyed by FCB pointer (no codegen change). Prefer the
    FCB field: simpler, consistent with existing design.
C2. Runtime: `pas_file_assign(fcb, char* name, int len)` (LSTRING data —
    note length-prefix convention in `runtime/readq.c::pas_read_lstring`),
    `pas_file_close(fcb)`, `pas_file_discard(fcb)` (close + `remove(name)`).
    `RESET`/`REWRITE` honor a bound name via `fopen`; unbound files and
    `ASSIGN(F, CHR(0))` keep the `tmpfile()` behavior — manual-blessed as
    the temporary-file case (this preserves all 8.2 tests unchanged).
    Manual-mandated checks: ASSIGN on an open file is an error; ASSIGN
    truncates trailing blanks; CLOSE on never-opened/closed file is a
    no-error no-op; CLOSE of a written TEXT file appends a final line
    marker if missing. CLOSE on INPUT/OUTPUT: per manual they may be
    CLOSEd (then reassigned) — do not fclose the real stdin/stdout;
    mark the FCB closed instead.
C3. Registry/typecheck/codegen for the five procedures (same pattern as
    8.2: `BUILTIN_PROCEDURES`, `_check_file_primitive_args`-style checker,
    `_builtin_file_op`-style lowering, `codegen/stmts.py` dispatch).
    READSET requires SET OF CHAR machinery and READFN requires the
    filename-token reader; if either drags in too much, implement
    ASSIGN/CLOSE/DISCARD first and leave READFN/READSET explicitly
    DEFERRED in the checklist with the reason — partial honest closure
    beats invented semantics.
C4. Tests:
    - ASSIGN→REWRITE→PUT→CLOSE, then separately ASSIGN→RESET→GET round-trip
      through a *named* file in a tempdir (proves persistence across
      close/reopen — tmpfile can't fake this).
    - DISCARD: file gone afterward (`os.path.exists` false).
    - ASSIGN-less behavior unchanged (8.2 tests stay green).
    - Typecheck negatives: wrong arity, non-file first arg, non-string name.
    - Note: named-file tests need the test to control the filename — pass
      it via the Pascal source string; tempdir cleanup in the harness.
C5. Checklist: close 8.5 (or close partially, with READFN/READSET deferred
    and tagged honestly).

## Phase D — 8.6: `FILEMODES`, `SEQUENTIAL`, `TERMINAL`, `FCBFQQ`

Manual-verified shape `[READ]` (manual ch. 6 + 12, txt ~7192–7260,
~12946–12990):
- `FILEMODES` is a predeclared enumerated type: "The mode is a value of the
  predeclared enumerated type FILEMODES; the modes are SEQUENTIAL, TERMINAL,
  and DIRECT" (txt 7250). "All files are given SEQUENTIAL mode by default,
  except for INPUT and OUTPUT which are given TERMINAL mode" (txt 7259).
  Mode is set via record-field assignment on the file variable:
  `F.MODE := SEQUENTIAL` (txt 12983). Note `DIRECT` is the third constant —
  the checklist line names only SEQUENTIAL/TERMINAL; register all three but
  DIRECT-mode *behavior* (random access via SEEK etc.) is out of scope.
- `FCBFQQ`: "A file variable is really a record called a file control block
  of type FCBFQQ" (12-32); "a file of any type can be passed to a formal
  parameter of type FCBFQQ and vice versa" (txt ~7192–7218); "Record field
  notation also applies to files" (txt 8576). Documented standard fields:
  `F.MODE: FILEMODES`, `F.TRAP: BOOLEAN`, `F.ERRS: 0..15` (12-32..33).
  Behavioral semantics that matter to us:
  - TERMINAL-mode TEXT input is line-buffered with echo; TERMINAL **binary**
    (FILE OF CHAR) is keystroke I/O with CHR(0)/CHR(255) conventions
    (12-6/12-7) — interactive details beyond "lazy fill + unbuffered"
    are host-dependent; implement state, document behavioral no-ops.
  - SEQUENTIAL/TERMINAL is the access-method axis; structure (ASCII/BINARY)
    is orthogonal and already in our FCB.

Work items:
D1. Re-grep the manual for F.TRAP / F.ERRS / DIRECT specifics as needed;
    record exact quotes in the checklist entry (match `[READ]` style).
D2. Implement the verified subset: predeclared enum type + constants in
    `symbol_table.py`/`builtins_registry.py` registration; semantic effect
    (if any) wired to the runtime mode bits. If a verb/attribute has no
    observable effect in our single-host runtime (e.g., SEQUENTIAL vs
    TERMINAL buffering), implement the *state* (settable, readable) and
    document the no-op behavior explicitly.
D3. Tests: typecheck-level (identifiers predeclared, assignable where the
    manual says) plus whatever runtime behavior was verified.
D4. Checklist: close 8.6 only for what was confirmed; anything still opaque
    stays open with a note. Do not flip the `[INFERRED]` tag without quotes
    from the manual.

---

## Acceptance criteria (whole plan)

- [ ] Phase gates: full suite green after each phase; one commit per phase.
- [ ] INPUT/OUTPUT attached to stdin/stdout without breaking existing
      READ/WRITE end-to-end tests; no implicit-GET-eats-first-char bug
      (explicit regression test for interactive-style read).
- [ ] `EOF`/`EOLN` are real predicates backed by the stream; the
      while-not-EOF copy test demonstrably fails if the predicate is stubbed.
- [ ] ASSIGN-bound files persist across CLOSE/reopen on the real filesystem;
      DISCARD deletes.
- [ ] Every closed checklist item carries Done/Proof/Does-not-cover notes in
      the house style, and no claim exceeds what a test or manual quote
      backs. Items whose manual semantics could not be verified are left
      open or explicitly partially closed — never silently approximated.

## Risks / gotchas

- **FCB layout lockstep:** any struct change touches `codegen/base.py`,
  `codegen/files.py::_init_file_storage`, and `runtime/fileops.c` together.
  GEP indices are positional; this bit us before (see 8.2 remediation).
- **Implicit GET vs. interactive input:** the single most likely source of
  subtle breakage. Lazy fill must be designed in Phase A, not retrofitted.
- **`tmpfile` vs named `fopen` mode strings:** RESET after REWRITE on the
  same open handle needs `"w+b"`-style modes or a close/reopen; the current
  rewind-based RESET works for tmpfile but a named-file RESET-after-CLOSE
  path is a fresh `fopen`.
- **Windows-isms in the manual:** the 1981 dialect may specify CR/LF or
  control-Z EOF conventions for TEXT files. Decide and document the host
  mapping ('\n' line marker, OS EOF) in one place; don't scatter it.
- **stdin in tests:** `build_run_linked` pipes stdin; EOF(INPUT) tests must
  pipe *finite* input — a test that reads an unpiped tty will hang CI.
  Consider a per-test timeout in the harness if not already present.
