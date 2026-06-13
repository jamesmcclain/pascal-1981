# Pascal-1981 Compiler

<img width="1402" height="1122" alt="ChatGPT Image Jun 3, 2026, 08_20_36 PM" src="https://github.com/user-attachments/assets/55ba0872-c8fa-4fc3-ab82-db076194d4f3" />

A full reimplementation of IBM Pascal 2.0, a compiler targeting LLVM IR with semantic analysis in a dedicated type-checking phase. Built to handle the vintage Pascal-1981 dialect with all its systems-programming extensions (`adr`, `sizeof`, `adrmem`, `word`, `extern`) — the features that made Pascal suitable for low-level operating system and firmware work in the early 1980s.

## Quick Start

Compile a Pascal program to a native executable:

```bash
# Pascal source -> LLVM IR  (parse + type-check + codegen)
python3 compile_to_llvm.py myprogram.pas myprogram.ll

# LLVM IR -> native executable (requires clang).
# Link the C runtime: file I/O, READ/READLN, string intrinsics,
# ENCODE/DECODE, and friends resolve against runtime/*.c.
clang myprogram.ll runtime/*.c -o myprogram

# Run it
./myprogram
```

Programs whose output lowers to bare `printf` (e.g. `WRITELN` of integers)
link without the runtime, but anything touching files, READ, or the string
intrinsics will fail with `undefined reference to pas_...` unless
`runtime/*.c` is on the link line.

Add `-v` / `--verbose` for detailed output and full Python tracebacks if compilation fails:

```bash
python3 compile_to_llvm.py -v myprogram.pas myprogram.ll
```

Optional dialect extensions are controlled with feature flags. The default dialect is vintage IBM Pascal behavior; wider integer types and symbolic enum I/O are off unless explicitly enabled:

```bash
# Show available feature flags
python3 compile_to_llvm.py --list-features

# Enable INTEGER32 / INTEGER64 and MAXINT32 / MAXINT64
python3 compile_to_llvm.py -f wide-integers myprogram.pas myprogram.ll

# Enable name-based user enum WRITE and READ as an extension
python3 compile_to_llvm.py -f symbolic-enum-io myprogram.pas myprogram.ll
```

If no output file is specified, LLVM IR is written to stdout:

```bash
python3 compile_to_llvm.py myprogram.pas | clang -x ir - runtime/*.c -o myprogram
```

## Architecture

A clean, layered pipeline with clear separation of concerns:

```
Pascal Source -> Lexer -> Parser -> Type Checker -> Codegen -> LLVM IR -> clang -> Executable
```

### Design Philosophy

Each phase is independent and focused:
- **Front end** (lexer, parser, type checker) is pure Python with no LLVM dependency
- **Errors stop the pipeline early** — type errors are reported before any IR is generated
- **No surprise failures** — if compilation succeeds, the generated code will link and run

### Components

- **Lexer (`lexer.py`)** — tokenizes Pascal source: keywords, identifiers, numbers, operators, strings.
- **Parser (`parser.py`)** — builds an Abstract Syntax Tree (AST) from tokens. Implements the full IBM Pascal 2.0 grammar. Entry point: `parse_file(path)`.
- **Type Checker (`type_system.py`, `symbol_table.py`, `type_checker.py`)** — semantic analysis: validates types, scopes, control flow, and module semantics before code generation. All type violations stop the pipeline with clear error messages.
- **Feature flags (`features.py`)** — generic feature-gating machinery for opt-in dialect extensions such as `wide-integers` and `symbolic-enum-io`.
- **Type Checker support (`builtins_registry.py`)** — centralized registration of predeclared identifiers (types, constants, intrinsics); user declarations may shadow builtins.
- **Codegen (`codegen/` package)** — walks the AST and emits LLVM IR using `llvmlite`. Split by concern: `base`, `decls`, `exprs`, `stmts`, `types_map`, `constfold`, plus feature modules `files` (file-control blocks), `io_write_read`, `strings`, `sets`, and `runtime_builtins`. `codegen_llvm.py` remains as a compatibility shim re-exporting the package.
- **C Runtime (`runtime/`)** — the file I/O subsystem (`fileops.c`: FCB model, RESET/REWRITE/GET/PUT, ASSIGN/CLOSE/DISCARD, READSET/READFN, EOF/EOLN, mode enforcement), stdin readers (`readq.c`), ENCODE/DECODE (`encode_decode.c`), and the move/scan/fill/position intrinsics.
- **Linking** — `clang` lowers LLVM IR to native code and links `runtime/*.c`.

### Grammar Reference

The grammar this dialect implements is formally specified in [`docs/ebnf_grammar.md`](docs/ebnf_grammar.md). The parser test suite is graded against this grammar as the source of truth.

## Supported Language Features

This compiler implements the full IBM Pascal 2.0 language, including all semantic rules and dialectal extensions. The checklist of features and gaps is tracked in [`docs/Grand_Unified_Checklist.md`](docs/Grand_Unified_Checklist.md).

### Types
- `INTEGER` (16-bit signed, matching IBM Pascal 2.0; range `-32768..32767`; `MAXINT = 32767`)
- `INTEGER32` / `INTEGER64` (opt-in extension types enabled with `-f wide-integers`; also enables `MAXINT32` and `MAXINT64`)
- `BOOLEAN` (one byte; stored as `i8` so address-of / `sizeof` / fills are byte-consistent)
- `REAL` (64-bit float; constants, division, unary minus, and mixed arithmetic are codegen-hardened — see checklist 9.1 — and the default `WRITE` format matches the manual's 14-wide exponential, e.g. `WRITE(123.456)` prints ` 1.2345600E+02`)
- `WORD` (16-bit unsigned)
- `CHAR` (8-bit)
- `ARRAY[low..high] OF type` — bounds may be constant expressions, including named `CONST`s
- `RECORD ... END`
- `SET OF type` — 256-bit bitvector representation; constant constructors fold at compile time
- Enumerated types (`TYPE color = (RED, GREEN, BLUE)`)
- `STRING(n)` (fixed, blank-padded) and `LSTRING(n)` (length-prefixed) string storage
- `TEXT` and binary `FILE OF T` file types, with the buffer variable `F^` backed by an inline file-control block
- Predeclared `FILEMODES` enum (`SEQUENTIAL`, `TERMINAL`, `DIRECT`) and `FCBFQQ` record; `F.MODE` is readable and assignable on file variables
- Pointers, plus the `adrmem` (generic address) parameter type

### Declarations
- `VAR x, y: INTEGER`
- `CONST size = 8190` — constant values are folded and usable in array bounds, `sizeof`, and expressions
- `PROCEDURE name(params); ... END`
- `FUNCTION name(params): type; ... END`
- `TYPE name = type`
- `EXTERN` / `FORWARD` / `EXTERNAL` procedures (link against external/C objects)

### Statements
- `IF cond THEN stmt ELSE stmt`
- `WHILE cond DO stmt`
- `REPEAT stmt UNTIL cond`
- `FOR var := start TO/DOWNTO end DO stmt`
- `CASE expr OF cases END`
- `BEGIN stmt; stmt; ... END`
- procedure / function calls

### Expressions
- Arithmetic: `+`, `-`, `*`, `/`, `DIV`, `MOD`
- Logic: `AND`, `OR`, `XOR`, `NOT`
- Comparison: `=`, `<>`, `<`, `<=`, `>`, `>=`
- Calls: `func(args)`
- Systems-programming operators: `adr x` (address-of), `sizeof(x)` / `sizeof(type)`
- Built-ins: `CHR`, `ORD`, plus the intrinsic families `ENCODE`/`DECODE`, `SCANEQ`/`SCANNE`, `POSITN`, and the move/fill block operations

### Built-in I/O
- `WRITE`/`WRITELN` — mixed integers, characters, booleans, enums, REALs, strings, and string literals, with `:width`/`:width:frac` field formatting; an optional leading `TEXT` file argument selects the output stream (default `OUTPUT`/stdout). User enum values print as ordinals by default, matching IBM Pascal 2.0; `-f symbolic-enum-io` switches user enum output to member names. BOOLEAN always writes `TRUE`/`FALSE`, independent of that flag.
- `READ`/`READLN` — scalar and string targets, with an optional leading `TEXT` file argument (default `INPUT`/stdin). User enum READ accepts numeric ordinals by default; `-f symbolic-enum-io` switches enum READ to symbolic member names, gated together with symbolic enum WRITE so same-mode enum round-trips stay coherent.
- File primitives — `RESET`, `REWRITE`, `GET`, `PUT`, and the buffer variable `F^`, over an inline file-control block with a single fill path shared by `F^`, the predicates, and the formatted readers
- Extended I/O verbs — `ASSIGN` (filename binding; `CHR(0)` spells a temporary file), `CLOSE`, `DISCARD`, `READSET` (scan characters in a `SET OF CHAR`), `READFN` (READLN-like dispatcher that binds filenames to file parameters)
- Stream predicates — `EOF` and `EOLN`, with line markers presented as blanks per the manual
- Mode enforcement — writing a file in inspection mode, writing a closed file, or reading a file in generation mode aborts with a runtime error rather than corrupting data

Coverage and known gaps for the file subsystem are tracked in checklist Section 8.

## Systems-Programming Extensions

These are the features that made Pascal suitable for writing operating systems, firmware, and device drivers. They allow direct memory manipulation while maintaining Pascal's type safety where possible:

- **`adr x`** — yields the address of a variable. Lowered to the variable's LLVM pointer, enabling low-level code.
- **`sizeof(x)` / `sizeof(T)`** — compile-time byte size, computed from real array bounds (constants are resolved) and element sizes; returns a `WORD`. Essential for buffer and layout calculations.
- **`adrmem`** — a generic address/pointer parameter type (`i8*` in LLVM). Pointer arguments are automatically bitcast to the parameter's type at the call site, enabling polymorphic low-level functions. Example: `adr flags` (an array pointer) can be passed where an `adrmem` is expected.
- **`extern` procedures** — declared without a body and resolved at link time. Enables linking Pascal code against C runtimes and external libraries.
- **`word` type** — 16-bit unsigned integer for register and hardware register operations.
- **Feature-gated wide integers** — `INTEGER32` and `INTEGER64` are available only with `-f wide-integers`; unflagged builds preserve the vintage 16-bit `INTEGER` surface.


## Project Scope

This is a **full reimplementation** of IBM Pascal 2.0. The goal is not a subset or tutorial language, but complete dialect coverage as specified in the original IBM Pascal 2.0 manual. 

**Reference:** The original compiler manual is [here](https://archive.org/details/ibm-pascal-compiler-aug-81) — this is the source of truth for dialect semantics and feature completeness.

Progress toward full coverage is tracked in [`docs/Grand_Unified_Checklist.md`](docs/Grand_Unified_Checklist.md), which lists:

- ✅ Completed features with test evidence
- 🚧 In-progress and planned work
- 📋 Known gaps with effort estimates

Features are prioritized by impact (correctness traps first, then missing grammar, then semantic edge cases) and effort. The test suite is organized to run independently at each layer, so development can proceed without the full LLVM toolchain.

## File Structure

```
pascal-1981/
├─ Core Compiler
│  ├── lexer.py                  # Tokenizer (keywords, identifiers, numbers, strings, operators)
│  ├── parser.py                 # Syntax analysis; builds AST via recursive descent
│  ├── ast_nodes.py              # AST node definitions (typed dataclasses)
│  ├── type_system.py            # Type hierarchy and compatibility rules
│  ├── symbol_table.py           # Scope management and symbol lookup
│  ├── type_checker.py           # Semantic analysis (types, scopes, control flow)
│  ├── builtins_registry.py      # Centralized predeclared-identifier registration
│  ├── features.py               # Generic opt-in dialect feature flags
│  ├── codegen/                  # LLVM IR generation package
│  │  ├── base.py, decls.py, exprs.py, stmts.py, types_map.py, constfold.py
│  │  ├── files.py               # File-control blocks (FCB layout, F^, file ops)
│  │  ├── io_write_read.py       # WRITE/READ lowering, field widths, file selectors
│  │  ├── strings.py, sets.py    # STRING/LSTRING and SET lowering
│  │  └── runtime_builtins.py    # Extern seams to the C runtime
│  ├── codegen_llvm.py           # Compatibility shim re-exporting codegen/
│  └── compile_to_llvm.py        # Driver (parse → type-check → codegen)
│
├─ Tests (organized by pipeline layer)
│  ├── tests/
│  │  ├── __init__.py
│  │  ├── support.py             # Test helpers and dependency probes
│  │  ├── test_parser.py         # Parser accept/reject corpus (pure Python)
│  │  ├── test_typecheck.py      # Type rules and semantics (pure Python)
│  │  ├── test_codegen.py        # IR generation and build/run (requires llvmlite + clang)
│  │  ├── test_codegen_strings_bounds.py  # String intrinsics, capacities, READ dispatch
│  │  ├── test_read_end_to_end.py         # Piped-stdin READ/READLN run tests
│  │  ├── test_runtime_fixes.py           # Hostile run tests for runtime behaviors (file subsystem, intrinsics)
│  │  ├── test_integration.py    # Legacy integration corpus (removed)
│  │  └── fixtures/parser/
│  │      ├── should_pass/       # Programs that MUST parse
│  │      ├── should_fail/       # Programs that MUST be rejected
│  │      └── judgment_calls/    # Edge cases per dialect spec
│
├─ Documentation
│  ├── docs/
│  │  ├── ebnf_grammar.md        # Formal grammar specification (reference document)
│  │  ├── Grand_Unified_Checklist.md  # Feature completeness tracker (priorities, effort, gaps)
│  │  └── plans/                 # Remediation and completion plans (executed plans kept for the record)
│
├─ Runtime & Build
│  ├── runtime/
│  │  ├── fileops.c              # File subsystem: FCB model, RESET/REWRITE/GET/PUT,
│  │  │                          #   ASSIGN/CLOSE/DISCARD, READSET/READFN, EOF/EOLN, mode enforcement
│  │  ├── readq.c                # stdin READ/READLN readers
│  │  ├── encode_decode.c        # ENCODE/DECODE intrinsics
│  │  ├── mover.c, movel.c, movesl.c, movesr.c   # Block-move intrinsics
│  │  ├── scaneq.c, positn.c     # Scan/position intrinsics
│  │  ├── fillc.c, fillsc.c      # Fill intrinsics
│  │  └── pabort.c               # Runtime abort reporting
│  ├── scripts/
│  │  └── beautify.sh            # Code formatter (isort + yapf)
│  ├── .gitignore
│  ├── .style.yapf               # Code style config
│  └── README.md                 # This file
```

## Testing

One unified test suite built on Python's stdlib `unittest`, with automatic detection of optional dependencies. Tests are organized by **pipeline layer**, so you can run the subset relevant to your changes without requiring the full LLVM toolchain.

### Run the entire test suite

```bash
# All tests; codegen tests auto-skip if llvmlite/clang are unavailable
python3 -m unittest discover -s tests -v
```

### Run by layer

```bash
# Parser accept/reject corpus + type rules (no llvmlite needed)
python3 -m unittest tests.test_parser tests.test_typecheck

# Codegen only (requires llvmlite + clang)
python3 -m unittest tests.test_codegen
```

### Test Organization

- **`tests/test_parser.py`** — Parser accept/reject verdicts over a fixture corpus:
  - `should_pass/` — programs that conform to the grammar and MUST parse
  - `should_fail/` — programs that violate the grammar and MUST be rejected
  - `judgment_calls/` — edge cases where the dialect spec allows discretion
  
  No subprocess or stdout grepping; verdicts come from catching `(ParserError, LexerError)`. Each fixture runs in a `subTest` for isolated failure reporting.

- **`tests/test_typecheck.py`** — Type rules, scope, compatibility, control flow, and module semantics. Organized by topic into `TestCase` classes (`TestVariableScope`, `TestTypeCompatibility`, `TestModuleSemantics`, etc.). In-process; no subprocess or `llvmlite` dependency.

- **`tests/test_codegen.py`** — LLVM IR generation and native build/run tests. Decorated with `@requires_llvm` (IR tests) and `@requires_exe` (build/run tests). Automatically skipped if the toolchain is unavailable; the suite still exits 0.

- **`tests/test_codegen_strings_bounds.py`** — string-intrinsic capacity semantics, WRITE field-width ordering, and READ dispatch guards at the IR and run level.

- **`tests/test_read_end_to_end.py`** — piped-stdin READ/READLN run tests across scalar and string types.

- **`tests/test_runtime_fixes.py`** — hostile run tests pinning previously-wrong runtime behaviors: NEW sizing, ENCODE/DECODE, SCANNE, and the file subsystem (buffer-variable model, RESET/GET interleaves, mode-enforcement aborts, ASSIGN/CLOSE/DISCARD/READSET/READFN).

- **`tests/test_integration.py`** — Legacy integration corpus (currently removed from supported test suite).

### Dependency Isolation

The front end (lexer, parser, type checker) is pure Python with **no `llvmlite` dependency**. This means:
- `test_parser.py` and `test_typecheck.py` run on any Python 3.8+ system
- `test_codegen.py` requires `llvmlite` and `clang` but is the only place that imports them
- If codegen dependencies are missing, the suite auto-skips those tests without failure

## Implementation Notes

### Data Structures

- **AST** — typed dataclasses defined in `ast_nodes.py`, one per language construct. The parser builds the tree bottom-up using recursive descent. Array, record, and pointer access use selector nodes for uniform representation.
- **Type System** — modular type hierarchy: base scalar types (`INTEGER`, `REAL`, `BOOLEAN`, `CHAR`, `WORD`, plus feature-gated `INTEGER32`/`INTEGER64`) plus composite types (ARRAY, RECORD, SET, POINTER) and callable types (PROCEDURE, FUNCTION). Implements Pascal's strict assignment rules with explicit type compatibility checks.
- **Symbol Table** — scope stack with parent chain for lexical scoping. Symbols are tagged by kind (var, const, function, procedure, parameter, type) to support scope-aware lookups and proper shadowing rules.
- **Codegen** — direct LLVM IR emission using `llvmlite`. No intermediate IR; the AST walks directly to LLVM instructions. Globals receive proper zero initializers; named constants are folded at compile time; function arguments are coerced (pointer bitcasts, integer width adjustments) to match callee signatures.

### Key Design Decisions

- **Type checking before codegen** — all type errors are caught and reported before any IR is generated, guaranteeing that successful type checking implies compilable output.
- **Minimal operator overloading** — each operator works on specific types with explicit type rules, avoiding the ambiguity that makes compiled languages harder to reason about.
- **Array bounds at compile time** — constant expressions in array declarations enable `sizeof` and layout calculations to be resolved during parsing, essential for systems programming.
- **Vintage integer width by default** — `INTEGER` lowers to signed 16-bit LLVM IR. Wider signed integers are extension-only (`INTEGER32`, `INTEGER64`) and must be enabled deliberately with `-f wide-integers`; there is no compatibility flag that makes default `INTEGER` 32-bit.

## Requirements

**For parsing and type checking:**
- Python 3.8+
- No external dependencies (pure Python implementation)

**For code generation (LLVM IR → native executable):**
- Python 3.8+
- `llvmlite` (for LLVM IR generation via Python)
- `clang` (recent versions; needed for native compilation and linking)
  - A harmless target-triple override warning from LLVM is expected and safe to ignore

**Note:** If `llvmlite` or `clang` are unavailable, the parser and type checker still work fully; only codegen tests are skipped.
