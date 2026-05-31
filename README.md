# Pascal-1981 Compiler

A Pascal compiler targeting LLVM IR, with semantic analysis in a separate type-checking phase. It handles the vintage Pascal-1981 dialect, including the systems-programming extensions (`adr`, `sizeof`, `adrmem`, `word`, `extern`) needed to compile period software.

## Quick Start

Compile a Pascal program to a native executable:

```bash
# Pascal source -> LLVM IR  (parse + type-check + codegen)
python3 compile_to_llvm.py myprogram.pas myprogram.ll

# LLVM IR -> native executable (requires clang)
clang myprogram.ll -o myprogram

# Run it
./myprogram
```

Add `-v` / `--verbose` to trace codegen and get a full Python traceback if compilation fails:

```bash
python3 compile_to_llvm.py -v myprogram.pas myprogram.ll
```

## Architecture

A clean pipeline:

```
Pascal Source -> Lexer -> Parser -> Type Checker -> Codegen -> LLVM IR -> clang -> Executable
```

- **Lexer (`lexer.py`)** — tokenizes keywords, identifiers, numbers, operators, strings.
- **Parser (`parser.py`)** — builds an AST of typed dataclasses. Entry point: `parse_file(path)`.
- **Type Checker (`type_system.py`, `symbol_table.py`, `type_checker.py`)** — validates types, scopes, and control flow before any code is generated. Errors stop the pipeline before codegen.
- **Codegen (`codegen_llvm.py`)** — walks the AST and emits LLVM IR via `llvmlite`, wiring built-in I/O to the C runtime (`printf`/`scanf`).
- **Linking** — `clang` lowers the IR and links any required runtime objects.

The grammar this dialect is checked against lives in [`docs/ebnf_grammar.md`](docs/ebnf_grammar.md); it is the reference the parser test suite is graded against.

Type errors are reported before codegen runs:

```pascal
VAR x: INTEGER;
BEGIN
  x := 3.14    (* Type error: REAL to INTEGER *)
END.
```

```
$ python3 compile_to_llvm.py bad.pas output.ll
Parsing bad.pas...
Type checking...
Type checking failed:
  ERROR: Cannot assign REAL to INTEGER
```

## Supported Language Features

### Types
- `INTEGER` (32-bit signed)
- `BOOLEAN` (one byte; stored as `i8` so address-of / `sizeof` / fills are byte-consistent)
- `REAL` (64-bit float; limited codegen support)
- `WORD` (16-bit unsigned)
- `CHAR` (8-bit)
- `ARRAY[low..high] OF type` — bounds may be constant expressions, including named `CONST`s
- `RECORD ... END`
- `SET OF type`
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
- Built-ins: `CHR`, `ORD`

### Built-in I/O
- `WRITELN(...)` — accepts a mix of integers, characters, booleans, and string literals (mapped to `printf`)
- `READLN(var)` — reads an integer (mapped to `scanf`)

## Systems-Programming Extensions

These are what let the compiler build period code that talks to memory directly:

- **`adr x`** — yields the address of a variable. Lowered to the variable's LLVM pointer.
- **`sizeof(x)` / `sizeof(T)`** — compile-time byte size, computed from real array bounds (constants resolved) and element sizes; returns a `WORD`.
- **`adrmem`** — a generic address/pointer parameter type (`i8*`). Pointer arguments are automatically bitcast to the parameter's type at the call site, so e.g. `adr flags` (an array pointer) can be passed where an `adrmem` is expected.
- **`extern` procedures** — declared without a body and resolved at link time against a separately compiled object (e.g. a C runtime).


## File Structure

```
pascal-1981/
├── lexer.py              # tokenizer
├── parser.py             # syntax analysis -> AST
├── ast_nodes.py          # typed dataclass node definitions
├── type_system.py        # type hierarchy and type-rule operations
├── symbol_table.py       # scope management
├── type_checker.py       # semantic analysis
├── codegen_llvm.py       # LLVM IR generation
├── compile_to_llvm.py    # driver (parse -> type-check -> codegen), supports -v
├── test_type_checker.py  # type checker tests (full pipeline, via the driver)
├── test_semantic.py      # interface / implementation / module semantics tests
├── runtime/              # C runtime
│   └── fillc.c
├── scripts/
│   └── beautify.sh       # isort + yapf over the Python sources
├── pascal_test_suite/    # parser accept/reject corpus
│   ├── run_suite.sh
│   ├── should_pass/      # programs a conforming parser MUST accept
│   ├── should_fail/      # programs a conforming parser MUST reject
│   └── judgment_calls/   # cases whose verdict depends on dialect decisions
├── docs/
│   └── ebnf_grammar.md   # the grammar this dialect is checked against
└── README.md             # this file
```

## Testing

Three independent checks, in increasing order of what they pull in:

```bash
# 1. Parser accept/reject corpus — lexer + parser only, no llvmlite.
bash pascal_test_suite/run_suite.sh
```

The corpus is organized by what the grammar (`docs/ebnf_grammar.md`) dictates,
not by what the parser happens to do: `should_pass/` programs must be accepted,
`should_fail/` programs must be rejected, and `judgment_calls/` collects cases
whose verdict depends on dialect decisions you may not have settled. Any line
the runner marks `BUG` is a divergence from the grammar.

```bash
# 2. Interface / implementation / module semantics — imports the type
#    checker directly, no llvmlite.
python3 test_semantic.py
```

```bash
# 3. Type checker, end to end — runs each program through the driver
#    (parse -> type-check -> codegen), so this one needs llvmlite + clang.
python3 test_type_checker.py
```

The front end (lexer, parser, type checker) is pure Python with no `llvmlite`
dependency, so the first two checks run without an LLVM toolchain installed.
`test_type_checker.py` drives the whole pipeline through `compile_to_llvm.py`,
so its valid-program cases need `llvmlite` to reach a successful exit.

## Implementation Notes

- **AST** — typed dataclasses, one per construct, with selectors for array / record / pointer access.
- **Type system** — base scalar types plus composite (ARRAY, RECORD, SET, POINTER) and callable (PROCEDURE, FUNCTION) types, with Pascal's strict assignment rules.
- **Symbol table** — scope stack with a parent chain; symbols tagged by kind (var, const, function, procedure, parameter, type).
- **Codegen** — direct LLVM IR emission. Globals get proper zero initializers; named constants are folded; call arguments are coerced (pointer bitcasts, integer width adjustment) to match callee signatures; boolean conditions reduce correctly regardless of integer width.

## Requirements

- Python 3.8+
- `llvmlite` (for LLVM IR generation)
- `clang` (for native compilation and linking; recent versions work — a harmless target-triple override warning is expected)
