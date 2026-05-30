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
├── type_system.py        # type hierarchy and operations
├── symbol_table.py       # scope management
├── type_checker.py       # semantic analysis
├── codegen_llvm.py       # LLVM IR generation
├── compile_to_llvm.py    # driver (parse -> type-check -> codegen), supports -v
├── test_type_checker.py  # type checker tests
├── runtime/              # C runtime
│   └── fillc.c
└── README.md             # this file
```

## Testing

```bash
# Type checker tests
python3 test_type_checker.py
```

```bash
# Lexing/parsing tests
bash pascal_test_suite/run_suite.sh
```

The front end (lexer, parser, type checker) is pure Python with no `llvmlite` dependency, so it can be exercised without an LLVM toolchain installed.

## Implementation Notes

- **AST** — typed dataclasses, one per construct, with selectors for array / record / pointer access.
- **Type system** — base scalar types plus composite (ARRAY, RECORD, SET, POINTER) and callable (PROCEDURE, FUNCTION) types, with Pascal's strict assignment rules.
- **Symbol table** — scope stack with a parent chain; symbols tagged by kind (var, const, function, procedure, parameter, type).
- **Codegen** — direct LLVM IR emission. Globals get proper zero initializers; named constants are folded; call arguments are coerced (pointer bitcasts, integer width adjustment) to match callee signatures; boolean conditions reduce correctly regardless of integer width.

## Requirements

- Python 3.8+
- `llvmlite` (for LLVM IR generation)
- `clang` (for native compilation and linking; recent versions work — a harmless target-triple override warning is expected)
