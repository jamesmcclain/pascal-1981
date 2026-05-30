# Pascal-1981 Compiler

A complete Pascal compiler targeting LLVM IR, with full semantic analysis via a separate type-checking phase. Supports the ancient Pascal-1981 dialect with clean architecture and zero regressions through all development phases.

## Quick Start

### Compile a Pascal program to native executable:

```bash
# Compile to LLVM IR
python3 compile_to_llvm.py myprogram.pas myprogram.ll

# Compile LLVM IR to native executable (requires clang)
clang myprogram.ll -o myprogram

# Run it
./myprogram
```

### Or in one line:

```bash
python3 compile_to_llvm.py myprogram.pas /tmp/prog.ll && clang /tmp/prog.ll -o myprogram && ./myprogram
```

## Architecture

The compiler follows a clean **4-phase pipeline**:

```
Pascal Source → Lexer → Parser → Type Checker → Codegen → LLVM IR → clang → Executable
```

### Phase 1: Lexer (`lexer.py`)
Tokenizes Pascal source code. Handles keywords, identifiers, numbers, operators, strings.

### Phase 2: Parser (`parser.py`)
Syntax analysis. Produces an Abstract Syntax Tree (AST) using typed dataclasses.
- Entry point: `parse_file(path) → Union[ProgramUnit, ModuleUnit, ...]`
- Returns structured AST nodes for all language constructs

### Phase 3: Type Checker (`type_system.py`, `symbol_table.py`, `type_checker.py`)
**Semantic analysis.** Validates types, scopes, and control flow before code generation.

**Features:**
- Variable scope tracking with nested scopes
- Type compatibility checking
- Function/procedure validation
- Array indexing type validation
- Return type checking
- Clear error messages with early failure

**Example:** Type errors prevent codegen
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

### Phase 4: Codegen (`codegen_llvm.py`)
**Code generation.** Converts AST to LLVM IR.
- Visitor pattern over AST nodes
- Symbol table from type checker
- Direct LLVM IR generation via `llvmlite`
- Integrates with C stdlib (printf, scanf) for I/O

### Phase 5: Linking
Generated LLVM IR is compiled to native code via `clang`.

## Supported Language Features

### Types
- `INTEGER` (32-bit signed)
- `BOOLEAN` (1-bit)
- `REAL` (64-bit float)
- `WORD` (16-bit unsigned)
- `CHAR` (8-bit character)
- `ARRAY[low..high] OF type`
- `RECORD ... END`
- `SET OF type`
- Pointers (type syntax only)

### Declarations
- `VAR x, y: INTEGER`
- `CONST PI = 3.14159`
- `PROCEDURE name(params); ... END`
- `FUNCTION name(params): type; ... END`
- `TYPE name = type`
- `EXTERN`, `FORWARD`, `EXTERNAL` attributes

### Statements
- `IF cond THEN stmt ELSE stmt END`
- `WHILE cond DO stmt`
- `REPEAT stmt UNTIL cond`
- `FOR var := start TO/DOWNTO end DO stmt`
- `CASE expr OF cases END`
- `BEGIN stmt; stmt; ... END`
- Procedure/function calls

### Expressions
- Arithmetic: `+`, `-`, `*`, `/`, `DIV`, `MOD`
- Logic: `AND`, `OR`, `XOR`, `NOT`
- Comparison: `=`, `<>`, `<`, `<=`, `>`, `>=`
- Function calls: `func(args)`

### Built-in I/O
- `WRITELN(expr)` → outputs integer + newline to stdout
- `READLN(var)` → reads integer from stdin into var
- Uses C `printf` and `scanf` internally

## File Structure

```
pascal-1981/
├── lexer.py              (5 phases, ~300 lines)
├── parser.py             (Syntax analysis, returns AST)
├── ast_nodes.py          (30+ typed dataclass node definitions)
├── type_system.py        (Type hierarchy, operations)
├── symbol_table.py       (Scope management, symbol tracking)
├── type_checker.py       (Semantic analysis visitor)
├── codegen_llvm.py       (LLVM IR generation)
├── compile_to_llvm.py    (Driver: lexer → parser → type check → codegen)
├── test_type_checker.py  (27 comprehensive type checker tests)
├── README.md             (This file)
└── pascal_test_suite/    (25 parser regression tests)
```

## Testing

### Run parser regression tests (25 tests):
```bash
cd pascal_test_suite
bash run_suite.sh /home/ubuntu/pascal-1981
```

### Run type checker tests (27 tests):
```bash
python3 test_type_checker.py
```

## Example Programs

### Hello World (with counting):
```pascal
PROGRAM HELLO;
VAR i: INTEGER;
BEGIN
  FOR i := 1 TO 3 DO
    WRITELN(i)
END.
```

Compile and run:
```bash
$ python3 compile_to_llvm.py hello.pas /tmp/hello.ll && clang /tmp/hello.ll -o /tmp/hello && /tmp/hello
Parsing hello.pas...
Type checking...
Generating LLVM IR...
Wrote /tmp/hello.ll
1
2
3
```

### Factorial Function:
```pascal
PROGRAM FACTORIAL;

FUNCTION FACT(n: INTEGER): INTEGER;
BEGIN
  IF n <= 1 THEN
    FACT := 1
  ELSE
    FACT := n * FACT(n - 1)
END;

VAR result: INTEGER;
BEGIN
  result := FACT(5);
  WRITELN(result)
END.
```

Output: `120`

## Implementation Details

### AST Design
- 30+ typed dataclasses (zero `isinstance` checks in visitor)
- Self-documenting structure
- Exhaustiveness checking via type system

### Type System
- 9 base types (INTEGER, BOOLEAN, REAL, WORD, CHAR, etc.)
- Type operations with Pascal's strict coercion rules
- Composite types (ARRAY, RECORD, SET, POINTER)
- Callable types (PROCEDURE, FUNCTION)

### Symbol Table
- Scope stack with parent chain
- Separate symbol kinds (variable, const, function, procedure, parameter, type)
- Fast lookup with O(1) access to current scope

### Code Generation
- Direct LLVM IR emission (no intermediate representation)
- Global and local variable allocation
- Proper scope management for parameters and locals
- Built-in function mapping (WRITELN → printf, READLN → scanf)

## Development Status

### Completed
- ✓ Phase 1: Lexer (5-pass tokenization)
- ✓ Phase 2: Parser (returns AST, 25/25 regression tests passing)
- ✓ Phase 2.5: LLVM Codegen (9/9 end-to-end tests passing)
- ✓ Phase 3a: Type System Foundation (type_system.py, symbol_table.py)
- ✓ Phase 3b: Basic Type Checking (scope, types, control flow)
- ✓ Phase 3c: Advanced Type Checking (returns, arrays, records)
- ✓ Phase 3d: Integration (4-phase pipeline, 27/27 type tests passing)

### Deferred
- Phase 4: Multi-module support (USES clause, dependencies)
- Phase 5: Optimization (dead code, constant folding, debug symbols)

## Git History

Major commits:
- `a9e8aec` - Add comprehensive type checker test suite
- `bae55cb` - Fix ConstDecl handling in type checker
- `f14da21` - Phase 3d: Type Checker Integration
- `26ffd42` - Phase 3c: Advanced Type Checking
- `47684f4` - Phase 3a: Type System Foundation
- `afe587d` - Phase 2: Real-world compilation fixes
- `e923687` - Phase 2: LLVM IR codegen backend
- `b063b15` - Phase 1: AST construction

## Requirements

- Python 3.6+
- `llvmlite` (for LLVM IR generation)
- `clang` 21.x (for native compilation, `clang --version`)

## License & Attribution

This is a research compiler implementation for the ancient Pascal-1981 dialect. Built with clean architecture principles and comprehensive test coverage.

---

**Usage Example:**

```bash
# 1. Write a Pascal program
cat > prog.pas << 'EOF'
PROGRAM TEST;
VAR x: INTEGER;
BEGIN
  x := 42;
  WRITELN(x)
END.
EOF

# 2. Compile to LLVM IR (includes type checking)
python3 compile_to_llvm.py prog.pas prog.ll

# 3. Compile to native executable
clang prog.ll -o prog

# 4. Run it
./prog
# Output: 42
```

All in one line:
```bash
python3 compile_to_llvm.py prog.pas /tmp/prog.ll && clang /tmp/prog.ll -o prog && ./prog
```
