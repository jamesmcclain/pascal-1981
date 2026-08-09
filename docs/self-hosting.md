# Self-hosting Pascal-1981: feasibility and strategy

## Context

`pascal-1981` is a ~15,169-line Python implementation of the IBM Pascal 2.0
dialect, targeting LLVM IR text and delegating assembly/linking to `clang`.
The question on the table is whether the compiler can be rewritten in its own
dialect, bootstrapped by the existing Python compiler.

Nothing is being implemented. This document is the analysis and a recommended
entry point.

**Verdict: technically feasible, and the dialect is unusually well-equipped for
it — but it is a ~2-year project whose payoff is symbolic. There is a
high-value prefix worth doing regardless of whether the full port ever
happens.**

---

## What makes this more tractable than expected

Four facts, verified in the source, shrink the problem substantially:

1. **llvmlite is not on the critical path.** `codegen/__init__.py:118` ends
   with `return _selfref_loop_metadata(str(module))` — the compiler already
   produces IR *text*. `llvmlite.binding` appears only in `_optimize_ir_text`
   (`compile_to_llvm.py:44`) and `llvm_ir_to_ptx` (`compile_to_ptx.py:22`),
   both text-in/text-out and both replaceable by shelling out to `opt`/`llc`.
   The feared "rewrite the backend to emit text" is replacing one text printer
   with another.

2. **There is no SSA construction to reimplement.** Codegen is
   alloca-per-variable (21 `alloca`, 48 `load`, 55 `store`), and there are
   exactly **two** `builder.phi` sites, both at `codegen/exprs.py:557` for the
   short-circuit `AND`/`OR` merge. Value numbering in a Pascal text backend is
   a monotonic counter. No dominance analysis, no phi placement.

3. **The parser has exactly one throw site** (`parser.py:49`, inside
   `Parser.error`) and performs no error recovery — it bails on first error.
   The type checker never raises at all; `TypeCheckError`
   (`typecheck/result.py:16`) is an accumulated dataclass. "Pascal has no
   exceptions" is, for this codebase specifically, close to a non-problem.

4. **The `[C]` FFI with a working SysV AMD64 classifier
   (`codegen/c_abi.py`) means the OS is already reachable** — `malloc`,
   `realloc`, `fopen`, `system`, and custom shims in `runtime/`. Every escape
   hatch the port needs already exists.

---

## What will actually hurt

### 1. stage0 miscompiling the compiler — the top risk

Not hypothetical. `codegen/decls.py:1055` sets `self.scope =
Scope(parent=prev_scope)` while emitting into a freshly created `ir.Function`.
An inner routine referencing an enclosing routine's local resolves to *that
other function's* alloca SSA value and emits a cross-function reference. There
is no static link or display anywhere in the package. **Nested-procedure
uplevel access is broken, and 1,121 tests never noticed.**

There will be more. A ~50k-line Pascal program is, by an order of magnitude,
the largest thing this compiler has ever compiled — deep recursion, ~15-unit
separate compilation, thousands of globals, LSTRING super-array formals at
scale are all currently exercised only at toy scale.

### 2. The AST representation, chosen once, chosen wrong

`ast_nodes.py` has 64 node types over ~70 distinct field names — but the
pipeline *dynamically extends* nodes via **226 `getattr`/`setattr`/`hasattr`
sites** (`typecheck/units.py` 46, `codegen/decls.py` 41), stapling on
`resolved_type`, `is_device`, `directive`, `llvm_value` and friends. In Pascal
every annotation must be a field declared months before you discover you need
another one.

### 3. Attrition

The middle ~12 months (parser + type checker, validated only against dumps)
produce no runnable compiler and zero user-visible value, while every Python-side
improvement must be ported twice. This kills more self-hosting projects than any
technical issue.

---

## Recommended design decisions

| Question | Recommendation |
|---|---|
| **AST representation** | Common header (`kind`, `line`, `col`) as the **first** field of each of 64 per-kind records, reached by `RETYPE` to typed pointers. C-compatible record layout makes this legal. ~40–80 bytes/node vs ~720 for a flat union record. Dispatch is `CASE n^.kind OF`. *Verify first that `RETYPE` (`codegen/exprs.py:232`) accepts named pointer types.* |
| **Variant records** | Do **not** put on the critical path. Adding them (~800–1,500 Python LOC, 3–6 weeks, touching `parser.py`, `type_system.RecordType`, `codegen/types_map.py`) is worthwhile independently — it restores a real IBM Pascal 2.0 feature the dialect claims fidelity to — but it feels like progress while deferring the actual representation decision. |
| **Error handling** | Parser: **panic-abort** — a behaviourally exact port of the single throw site, zero machinery. Type checker: **port as-is** (accumulate into a vector, poison `ERROR_TYPE`). Codegen: panic-abort. Reject error-code threading (+1,500 LOC of noise) and `GOTO` handlers (needs the same broken static-link machinery). |
| **argv** | `[C] EXTERN` shims. `runtime/cmdline.c:31` already stashes `g_argc`/`g_argv`; adding `pas_argc()` and `pas_argv_into(i, buf, cap)` is ~20 additive lines. Reject a PPM/argc builtin — grammar+typecheck+codegen surface for no gain. |
| **Memory** | Arena allocator (chunked bump over `malloc`, never frees). This is what makes panic-abort safe — no frame owes cleanup. |
| **Backend** | LLVM IR text emission. *Easier* than the llvmlite version for the reasons above; only `gep` (74 uses) is fiddly. Emit `REAL` constants as `0x` + 16 hex digits via `RETYPE(WORD64, r)`, dodging decimal round-trip entirely — this is what llvmlite already does. |
| **`-O`, PTX** | Drop both from the Pascal compiler; shell out to `opt -O<n>` and `llc -mtriple=nvptx64-nvidia-cuda` via a `[C] EXTERN int pas_system(const char*)` plus ~60 Pascal LOC of shell quoting. |
| **Fixed-point gate** | **stage2 == stage3**, on *normalized* `-O0` IR. Do **not** chase stage0 == stage1 byte-identity — reproducing llvmlite's implicit `%0`/`.1` naming is a fool's errand. Agreement with stage0 is a separate concern, handled by the differential corpus. |

### The subset: write the compiler in "PSUB", not the full dialect

Enforce with a `--psub` lint mode in stage0 so the source cannot drift.

- **Ban nesting outright.** "Nesting without uplevel refs" is not mechanically
  checkable, and uplevel is broken. All routines top-level.
- **Exclude:** DEVICE/PTX/ADS/SPACE/tuning-hints (~1,000–1,500 Python LOC
  cuttable wholesale — the compiler source uses no DEVICE construct);
  `FILE OF T` and buffer variables; REAL arithmetic; `WITH`; `GOTO`;
  `ORIGIN`; `RETYPE` outside the one pointer-downcast idiom.
- **Cannot cut:** `-f wide-integers` is **mandatory** — default `INTEGER` is
  `i16` (`codegen/types_map.py:32`), useless for node indices. The
  self-hosted compiler cannot compile itself in `--dialect vintage`, so
  `features.py` shrinks to ~2 features but not to zero. Also mandatory:
  INTERFACE/IMPLEMENTATION separate compilation, `$INCLUDE`, sets, TEXT
  files, LSTRING super-array formals, `[C] EXTERN`.
- **C ABI classifier:** cutting it *would* break self-hosting, but every shim
  the compiler needs passes only scalars and pointers — never structs by
  value. stage1 needs a ~60-LOC subset, not the 434-line `byval`/`sret`
  machinery. Keeping that true is a real constraint on `runtime/pascalrt.h`.

### Standard library that must be built first (~1,800–2,200 Pascal LOC, ~120 C LOC)

Arena (150) · growable vectors over `SUPER ARRAY` + long-form `NEW` (400) ·
interned string table + FNV-1a hash map, **insertion-ordered where iteration
feeds emission** (350) · `TStrBuf` dynamic string over C `realloc`, since
`LSTRING` caps at 255 and a `.ll` runs to megabytes (250 Pascal + 40 C) ·
integer formatting (120) · diagnostics (200) · whole-file slurp (30 C) · argv
(250 Pascal + 20 C) · process spawn (60 Pascal + 30 C).

**Do not build printf-style formatting** — no source-level varargs. The 116
f-string sites become ~600 explicit `Emit`/`EmitInt` calls. This is the single
largest driver of code expansion.

**Purity line:** C shims only for what the OS owns (memory, files, argv,
spawn), never for compiler logic — the line gcc and Go drew.

### Definition of self-hosting for this project

> The compiler binary contains no Python. Every line of compiler logic — lexing,
> parsing, type checking, IR emission, option parsing — is Pascal-1981 source
> compiled by this compiler. It links `libpascalrt.a` for OS services.

Shelling out to `clang`/`opt`/`llc` is **not** a violation (gcc shells out to
`as` and `ld`). Keeping `argparse`, or keeping llvmlite anywhere, **is**.

---

## Where to start — and where not to

**Do not start with the backend**, despite it being the biggest and most
interesting piece: it is the *last* thing that can be differentially
validated, and until goldens and a normalizer exist it cannot be validated at
all.

**Do not start the port at all until the golden `.ll` suite exists.** Without
it you are porting 5,000 lines of codegen against a spec you can read but not
test.

### Phase 0 — the high-value prefix (worth doing regardless)

1. **Golden `.ll` snapshot suite.** ~870 programs already exist — 56 `.pas`
   files plus ~815 inline `PROGRAM` sources embedded in `tests/*.py`. An
   autouse hook in `tests/support.py` that snapshots normalized `-O0` IR on
   every compile, plus `--update-goldens`: **~100 Python LOC yields ~870
   goldens for free.** Its absence is precisely why any codegen refactor
   currently looks frightening.
2. **IR normalizer** (~150 LOC): strip comments, renumber `%N`/`!N`
   monotonically, sort declaration blocks. This is what makes cross-compiler
   comparison tractable.
3. **The IR-text facade** (~1,500 Python LOC): a `codegen/irtext.py`
   implementing the same duck-typed surface as `ir.Module`/`ir.IRBuilder`
   /`ir.Type` (45 methods, top 10 covering ~80% of calls), behind a flag,
   validated by running the full suite both ways. Removes llvmlite from the
   critical path, kills the version-skew tax visible in
   `codegen/llvmlite_compat.py`, and produces an **executable specification**
   of what the Pascal backend must emit.
4. `--dump-tokens` / `--dump-ast` in stage0, in a canonical round-trippable
   format, plus readers on the Python side (~300 LOC).
5. `--psub` lint mode; PSUB conformance suite (~250 native-run programs at
   *compiler* scale — pointer graphs, `CASE` over 64 labels, recursion depth
   ≥5,000, ~15-unit separate compilation, every planned `[C]` shim signature).

### Phase 1 — the proof of concept

**The lexer in Pascal (~2,500 LOC, ~6 weeks).** It exercises every piece of
new infrastructure (arena, vectors, interning, `TStrBuf`, argv shim, file
slurp) and is validated byte-identically against `--dump-tokens` over the
~870-program corpus. Because the token dump round-trips, the *Pascal* lexer's
output can be fed to the *Python* parser and run against the entire 1,121-test
suite.

This either validates the whole plan or tells you within two months that it
won't work.

### Subsequent phases, for scale

Parser (validated on `--dump-ast`) → type checker (annotated-AST dump +
normalized diagnostics; this is where the 226-getattr census bites) → codegen
in tranches against the goldens → driver → stage2/stage3 fixed-point chase.

**Estimated total: ~50,000 Pascal LOC** (3.3× expansion on 15,169 Python
lines, plus ~2,000 of new stdlib), **≈23 months solo, ≈14 months with two.**
Expansion drivers: f-strings →emit chains (~5×), comprehensions →loops (~4×),
`Dict[str,…]` →explicit map handles (~2×), 700 `isinstance` →`CASE` + `RETYPE`
(~1.5×), no dataclass `__init__`/`__repr__` (+~2,000 LOC), Pascal `VAR`
sections (+~15%).

**One accepted semantic loss:** Python's arbitrary-precision ints make
constant folding free (`typecheck/consts.py`, `codegen/constfold.py`). Pascal
gets `INTEGER64` plus overflow diagnostics. A const expression exceeding i64
folds in stage0 and will not in stage1. Document and move on.

---

## Verification

Since nothing is being built yet, the verification story *is* the plan: the
Phase 0 deliverables are exactly the harness that makes every later phase
checkable.

- Goldens + normalizer: `PYTHONPATH=src python3 -m pytest tests/ -q` must pass
  identically with the llvmlite backend and the text facade.
- PSUB conformance: ~250 programs built and **run** natively via the existing
  `build_and_run_pascal_project` helper in `tests/support.py`.
- Phase L gate: Pascal lexer token dump byte-identical to `--dump-tokens` on
  all ~870 corpus programs, *and* the full Python test suite green when driven
  from Pascal-produced tokens.
- Bootstrap gate: stage2 and stage3 `.ll` byte-identical after normalization.

## Critical files

- `src/pascal1981/codegen/__init__.py:118` — the `str(module)` seam where the
  text facade is inserted; `_selfref_loop_metadata` must be reproduced exactly
- `src/pascal1981/codegen/decls.py:1055` — the broken nested-procedure scope
  chain; diagnose before relying on any nesting
- `src/pascal1981/ast_nodes.py` — the 64 node types the Pascal records mirror
- `src/pascal1981/codegen/types_map.py:32` — the `i16` INTEGER default that
  forces `-f wide-integers`
- `src/pascal1981/codegen/exprs.py:232` — `RETYPE` lowering; confirm it admits
  named pointer types
- `tests/support.py` — where the golden hook and normalizer belong
- `runtime/cmdline.c:31` — already holds `argc`/`argv`; the shim is ~20 lines
