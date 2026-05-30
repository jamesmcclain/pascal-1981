# Recognizer → Parser + Code Generation: Design Notes

## Current State

`parser.py` is a **recognizer**: it validates syntax and rejects bad input, but
every `parse_*` method returns `None`. The `Node` dataclass exists but is never
instantiated. There is no intermediate representation for a backend to consume.

---

## Phase 1: AST Construction

### Strategy: Typed AST Nodes over Generic Nodes

The current generic `Node(kind, children)` is fine for prototyping, but for
multiple backends with different concerns (LLVM IR, interpreter, 16-bit real
mode), **typed dataclasses** will pay for themselves quickly. They give you:

- Exhaustiveness checking (mypy, pattern matching via `match/case`)
- Self-documenting field names (`ForStmt.var`, `ForStmt.start`, `ForStmt.end`)
  instead of positional `children[0]`, `children[1]`
- Cleaner backend code—each visitor method knows exactly what it's looking at

### Proposed AST Hierarchy

A single `ast_nodes.py` module (keeps `parser.py` from bloating):

```
ASTNode (base)
├── ProgramUnit(name, params, uses, block)
├── ModuleUnit(name, uses, decls)
├── InterfaceUnit(name, params, uses, decls)
├── ImplementationUnit(name, uses, decls, init_body)
├── Block(decls, body)
├── Declarations
│   ├── ConstDecl(name, value)
│   ├── TypeDecl(name, type_expr)
│   ├── VarDecl(names, type_expr, attributes)
│   ├── ValueDecl(name, value)
│   ├── LabelDecl(labels)
│   ├── ProcDecl(name, params, attributes, body)  # body=None if EXTERN/FORWARD
│   └── FuncDecl(name, params, return_type, attributes, body)
├── Statements
│   ├── CompoundStmt(stmts)
│   ├── AssignStmt(target, expr)
│   ├── ProcCallStmt(name, args)
│   ├── IfStmt(cond, then_branch, else_branch)
│   ├── ForStmt(var, start, end, direction, body)
│   ├── WhileStmt(cond, body)
│   ├── RepeatStmt(body, cond)
│   ├── CaseStmt(expr, elements, otherwise)
│   ├── WithStmt(targets, body)
│   ├── GotoStmt(label)
│   ├── ReturnStmt()
│   ├── BreakStmt() / CycleStmt()
│   ├── LabelStmt(label, stmt)
│   └── EmptyStmt()
├── Expressions
│   ├── BinOp(op, left, right)
│   ├── UnaryOp(op, operand)
│   ├── IntLiteral(value) / RealLiteral(value) / CharLiteral(value)
│   ├── StringLiteral(value) / BoolLiteral(value)
│   ├── Identifier(name)
│   ├── Designator(name, selectors)
│   ├── FuncCall(name, args)
│   ├── SetConstructor(elements)
│   ├── AdrExpr(name)
│   ├── SizeofExpr(target)
│   └── UpperExpr(name)
├── Types
│   ├── NamedType(name, param)
│   ├── ArrayType(index_range, element_type, packed, super)
│   ├── RecordType(fields, packed)
│   ├── SetType(base)
│   ├── FileType(element_type)
│   ├── EnumType(values)
│   ├── PointerType(base)
│   ├── LStringType(max_len)
│   └── BuiltinType(name)  # INTEGER, REAL, BOOLEAN, CHAR, WORD, ADRMEM
└── Support
    ├── Param(mode, names, type_expr)  # mode: VAR/CONST/VARS/CONSTS/None
    ├── CaseElement(constants, stmt)
    ├── IndexRange(low, high)          # high=None for super arrays (star)
    ├── Selector(kind, index_or_field)
    └── UseClause(name, imports)
```

### Transformation Approach

The refactor is mechanical. For each `parse_*` method:

1. Change return type from `None` to the appropriate AST node
2. Capture tokens and child-parse results into local variables
3. Return a constructed node

Example—current:
```python
def parse_for_statement(self) -> None:
    self.expect('FOR')
    self.expect('IDENTIFIER')
    self.expect('ASSIGN')
    self.parse_expression()
    if self.current().kind in {'TO', 'DOWNTO'}:
        self.pos += 1
    else:
        self.error('expected TO or DOWNTO')
    self.parse_expression()
    self.expect('DO')
    self.parse_statement()
```

After:
```python
def parse_for_statement(self) -> ForStmt:
    self.expect('FOR')
    var = self.expect('IDENTIFIER').lexeme
    self.expect('ASSIGN')
    start = self.parse_expression()
    if self.current().kind in {'TO', 'DOWNTO'}:
        direction = self.current().kind
        self.pos += 1
    else:
        self.error('expected TO or DOWNTO')
    end = self.parse_expression()
    self.expect('DO')
    body = self.parse_statement()
    return ForStmt(var, start, end, direction, body)
```

Every method follows this same pattern. No structural changes to the grammar,
no new control flow—just capturing what was previously discarded.

### Test Suite Impact

The test suite calls the parser and checks accept/reject. As long as
`parse_file()` still raises on bad input and doesn't raise on good input,
the suite keeps working. The AST is a new output; it doesn't break the
existing contract.

---

## Phase 2: LLVM IR Generation via `llvmlite`

### Architecture

```
lexer.py → parser.py → ast_nodes.py (data)
                              ↓
                        codegen_llvm.py  (backend #1)
                              ↓
                         output.ll → llc / opt / clang
```

`codegen_llvm.py` imports `parser.py`, gets an AST, walks it, and emits IR
using `llvmlite.ir`. The IR gets written to a `.ll` file. From there, any
LLVM toolchain works: `llc` for native code, `opt` for optimization passes,
`clang` to link against a C runtime.

### Key `llvmlite` Considerations

- **`llvmlite` generates IR in-memory, dumps to text.** This is exactly what
  you want. Call `module.as_llvm_ir()` → write to `.ll` file.

- **Type mapping.** This Pascal dialect has `INTEGER`, `REAL`, `BOOLEAN`,
  `CHAR`, `WORD`, `ADRMEM`, pointers, arrays, records, sets. Most map
  directly to LLVM types. Sets and `LSTRING` will need runtime support
  (small struct + helper functions, or inline bit manipulation for small sets).

- **`SUPER ARRAY`** (conformant arrays with `*` upper bound) maps to a
  pointer + length pair—essentially a fat pointer / slice. This will need a
  calling convention decision.

- **`EXTERN` / `EXTERNAL`** procedures map directly to LLVM `declare`
  statements. This is the easy part.

- **Nested procedures** (Pascal allows them, and your grammar supports them):
  LLVM IR has no nested functions. You'll need lambda lifting—passing the
  enclosing frame as an explicit pointer parameter. Doable but non-trivial.

- **Runtime library.** `WRITELN`, `READLN`, file I/O, `NEW`/`DISPOSE`—these
  need implementations. Options: (a) emit calls to C `printf`/`scanf`/`malloc`
  and link with libc, or (b) write a small Pascal runtime in C. Option (a) is
  the fast path for getting something running.

### What's Feasible First

A reasonable initial target: compile a program that does integer arithmetic,
`IF`/`FOR`/`WHILE`, procedure calls, and `WRITELN` of integers. That covers
`ProgramUnit`, `Block`, `VarDecl`, `ConstDecl`, `ProcDecl`, all statement
types, `BinOp`/`UnaryOp`/literals, and basic type mapping. Defer sets,
records, files, super arrays, and modules to later iterations.

---

## Phase 3: Multiple Backends

Once the AST exists, adding backends is just writing new tree walkers:

- `codegen_llvm.py` — LLVM IR (Phase 2)
- `interpreter.py` — direct AST interpretation
- `codegen_x86_16.py` — 16-bit real mode (COM/MZ output)

Each imports `parser.py`, calls `parse_file()`, gets an AST, does its thing.
The parser doesn't know or care what happens downstream. Clean separation.

For the 16-bit backend, you'd skip `llvmlite` entirely and emit machine code
or NASM-syntax assembly directly from the AST. Different concerns (segmented
memory, far/near calls, DOS interrupts) but same input structure.

---

## Execution Order

1. **Create `ast_nodes.py`** — define all node dataclasses
2. **Refactor `parser.py`** — return AST nodes instead of `None`
3. **Verify test suite still passes** (it will; same accept/reject behavior)
4. **Write `codegen_llvm.py`** — minimal backend targeting integer programs
5. **Iterate** — add type support, runtime hooks, more complex constructs

Step 1 and 2 are the foundation. Everything else builds on having a real AST.

---

## Risks and Sharp Edges

- **Semicolon as separator vs. terminator.** Your grammar uses semicolons in
  both roles depending on context. The AST doesn't care, but backends that
  emit code need to understand statement boundaries. The AST structure
  (lists of statements) handles this naturally.

- **Include directives.** Currently skipped. If included files contain
  declarations that affect type checking or code generation, you'll
  eventually need to process them. For now, skipping is fine—the parser
  already handles it.

- **No symbol table.** The parser doesn't do semantic analysis. For LLVM
  codegen you'll need at minimum a scope stack mapping identifiers to their
  LLVM values and types. This lives in the backend, not the parser. Build
  it in `codegen_llvm.py`.

- **`llvmlite` maintenance status.** The library works, it's stable, but it's
  not heavily developed anymore. It tracks a specific LLVM version. For your
  use case (emit IR to disk, use external LLVM tools) this is a non-issue—
  IR text format is highly stable across LLVM versions.
