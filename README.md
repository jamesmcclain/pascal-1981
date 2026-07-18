# Pascal-1981 Compiler

<img width="1536" height="1024" alt="image" src="https://github.com/user-attachments/assets/42f81123-5db6-4bb0-8661-84339ec218f9" />

A full reimplementation of IBM Pascal 2.0, a compiler targeting LLVM IR with semantic analysis in a dedicated type-checking phase. Built to handle the vintage Pascal-1981 dialect with all its systems-programming extensions (`adr`, `sizeof`, `adrmem`, `word`, `extern`) — the features that made Pascal suitable for low-level operating system and firmware work in the early 1980s.

## Quick Start

There are two supported ways to run the compiler:

1. **Install it with pip** and use the `pascal1981` console script.
2. **Run it directly from a source checkout** with `PYTHONPATH=src`.

Both routes produce LLVM IR. Native executable generation is then handled by
`clang`, linked with the Pascal runtime archive or runtime C sources.

### Install with pip

From a checkout of this repository:

```bash
python3 -m pip install .
```

The pip build compiles the C runtime with `make` and `clang` (both required:
the build fails early with a clear message if either is missing):

```bash
make -C runtime all        # CPU shim + libpascalrt.a alias -- always
make -C runtime cuda       # CUDA shim -- added automatically when the
                           # CUDA toolkit headers are visible to clang
                           # ($CUDA_HOME/include/cuda.h; CUDA_HOME defaults
                           # to /usr/local/cuda)
```

The resulting static archives (`libpascalrt.a`, `libpascalrt_cpu.a`, and
`libpascalrt_cuda.a` when CUDA was found) are bundled inside the installed
Python package, and the wheel carries the PEP 600 perennial tag of the build
machine (e.g. `py3-none-manylinux_2_39_x86_64` when built on Ubuntu 24.04):
the archives are compiled against the build machine's glibc, so pip refuses
the wheel on older-glibc machines instead of letting them fail at link time.  On a machine
with the CUDA toolkit installed (such as the `docker/Dockerfile` image),
`pip install .` or `python -m build` therefore produces a full host+CUDA
wheel with no extra flags.

Compile and link a program after installation:

```bash
# Pascal source -> native executable (compile + assemble + link via clang;
# the bundled libpascalrt.a is added to the link automatically)
pascal1981 myprogram.pas -o myprogram

# Run it
./myprogram
```

The `pascal1981` command line follows gcc conventions. Stage flags select how
far compilation goes: with no stage flag the driver compiles, assembles, and
links an executable (`a.out`, or `-o FILE`); `-S` stops at assembly (host:
LLVM IR, `./<name>.ll`; an nvptx `--device-triple`: `./<name>.ptx`); `-c`
stops at an object file (`./<name>.o`). Assembling and linking run through
clang. `-o FILE` names the output, and with `-S` the extension `-o -` writes
to stdout. `-O0`/`-O1`/`-O2`/`-O3` (or a bare `-O`, meaning `-O1`) selects an
optimization level: with host `-S` it runs LLVM's mid-level IR pipeline, with
`-c`/linking it is forwarded to clang, and with an nvptx `--device-triple` it
runs the pipeline before NVPTX codegen. `-print-file-name=libpascalrt.a`
prints the absolute path of the bundled runtime archive. `-l LIB`, `-L DIR`,
and `-Wl,ARG` pass through to the clang link step, and `-###` prints the
clang commands without executing them (gcc-style dry run).

You can also locate the runtime archive from Python:

```bash
python3 -c 'from pascal1981 import runtime_lib_path; print(runtime_lib_path())'
```

### Build a wheel

On any machine with `make` + `clang`:

```bash
python3 -m pip wheel . --no-deps -w dist
```

The build self-configures: a visible CUDA toolkit produces a full host+CUDA
wheel, otherwise the wheel is CPU-only.  To guarantee the full wheel, build
inside the CUDA development image (no GPU needed for the build itself):

```bash
docker build -t pascal-1981:latest -f docker/Dockerfile .   # once; see docker/README.md
docker run --rm -v "$PWD":/work pascal-1981:latest sh -c "pip wheel . --no-deps -w /work/dist"
```

Either way the wheel lands in `dist/`, tagged with the build machine's glibc
floor (e.g. `pascal1981-1.0.0-py3-none-manylinux_2_39_x86_64.whl` from the
container).  Check its cargo with
`unzip -l dist/*.whl | grep '\.a$'` (three archives with CUDA, two without).

### Run from a source checkout without pip installing

If you do not want to install the package, run the compiler from the checkout by
putting `src/` on `PYTHONPATH`:

```bash
PYTHONPATH=src python3 -m pascal1981 -S myprogram.pas -o myprogram.ll
```

Build the runtime static library manually:

```bash
make -C runtime
```

This produces:

```text
runtime/build/libpascalrt.a
```

Then link against that archive:

```bash
clang myprogram.ll runtime/build/libpascalrt.a -o myprogram
./myprogram
```

After `make -C runtime`, the source-tree CLI can also print that archive path:

```bash
PYTHONPATH=src python3 -m pascal1981 -print-file-name=libpascalrt.a
```

For quick source-tree experiments, you may also link the runtime C files
directly instead of building the archive:

```bash
PYTHONPATH=src python3 -m pascal1981 -S myprogram.pas -o myprogram.ll
clang myprogram.ll runtime/*.c -o myprogram
```

Programs whose output lowers to bare `printf` may link without the runtime, but
anything touching files, `READ`/`READLN`, string intrinsics, `ENCODE`/`DECODE`,
scan/fill/move intrinsics, or other `pas_...` helpers needs the runtime archive
or equivalent runtime objects on the link line. Otherwise the linker will tell
you the truth with `undefined reference to pas_...`. Cold, but fair.

Add `-v` / `--verbose` for detailed output and full Python tracebacks if compilation fails:

```bash
pascal1981 -v -S myprogram.pas -o myprogram.ll
# or, from a source checkout:
PYTHONPATH=src python3 -m pascal1981 -v -S myprogram.pas -o myprogram.ll
```

Optional dialect extensions are controlled with feature flags. The default dialect is vintage IBM Pascal behavior; wider integer types and symbolic enum I/O are off unless explicitly enabled:

```bash
# Show available feature flags
pascal1981 --list-features

# Enable the wide/narrow integer extension family (INTEGER8/32/64,
# WORD8/32/64, MAXINT32/MAXINT64, MAXWORD32/MAXWORD64, WRD8)
pascal1981 -f wide-integers -S myprogram.pas -o myprogram.ll

# Enable name-based user enum WRITE and READ as an extension
pascal1981 -f symbolic-enum-io -S myprogram.pas -o myprogram.ll
```

By default the dialect already enforces the vintage WORD/INTEGER rules: a signed
`INTEGER` variable is not assignment-compatible with `WORD` (convert with
`WRD(...)`; use `ORD(...)` for the reverse), and mixing `WORD` with a
non-constant `INTEGER` in an expression is a warning. The manual's "INTEGER
constants change to WORD" exemption is generalized to the extension family: a
compile-time constant integer expression (literal, named `CONST`, `SIZEOF`, or
foldable expression) whose value fits the target's range may flow into any
`WORD8`/`WORD32`/`WORD64` or `INTEGER8`/`INTEGER32`/`INTEGER64` target; only
non-constant values need explicit conversion. The `strict-word-int`
feature promotes that mix warning to a hard error. It is a policy flag,
orthogonal to `--dialect`: enabling or disabling it never moves a program in or
out of the extended dialect.

```bash
# Make every non-constant WORD/INTEGER expression mix a hard error
pascal1981 -f strict-word-int -S myprogram.pas -o myprogram.ll
```

Without a stage flag the driver links an executable; intermediate artifacts
follow gcc's naming rules (`-S` writes `./<name>.ll`, `-c` writes
`./<name>.o`, linking writes `./a.out`) unless `-o` says otherwise. To stream
LLVM IR to stdout -- e.g. to drive clang yourself in a pipe -- use `-S -o -`:

```bash
pascal1981 -S -o - myprogram.pas | clang -x ir - "$(pascal1981 -print-file-name=libpascalrt.a)" -o myprogram
```

Source-tree equivalent:

```bash
PYTHONPATH=src python3 -m pascal1981 -S -o - myprogram.pas | clang -x ir - runtime/build/libpascalrt.a -o myprogram
```

## Device PTX artifact generation

The compiler also has an early device-only path for Pascal `DEVICE UNIT` /
`DEVICE IMPLEMENTATION` code targeting NVIDIA PTX.  This path is for inspecting
or externally launching GPU kernel artifacts; it does **not** generate Pascal
host-side CUDA orchestration yet.

From a source checkout, compile a device implementation directly to PTX:

```bash
PYTHONPATH=src python3 -m pascal1981.compile_to_ptx \
  examples/device_ptx/fill_indices/fill.pas \
  -o examples/device_ptx/fill_indices/fill.ptx \
  --save-llvm examples/device_ptx/fill_indices/fill.ll \
  --cpu sm_70 \
  -O2
```

The source file is a `DEVICE IMPLEMENTATION OF` whose sibling interface file
contains the `DEVICE INTERFACE`.  By convention in this repository the interface
file carries a `.inc` extension (the compiler does not require it — interface
resolution also accepts an extensionless sibling or a `.pas` file).  Exported
procedures in the device interface are lowered as PTX kernel entries.  For
example:

```pascal
DEVICE INTERFACE;
UNIT FILL (fill_indices);
PROCEDURE fill_indices(outp: ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32; n: INTEGER32);
END;
```

```pascal
(*$INCLUDE:'fill.inc'*)
DEVICE IMPLEMENTATION OF FILL;
PROCEDURE fill_indices(outp: ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32; n: INTEGER32);
VAR i: INTEGER32;
BEGIN
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  IF i < n THEN
    outp^[i] := i
END;
.
```

Inspect the artifact:

```bash
grep '\.visible .entry fill_indices' examples/device_ptx/fill_indices/fill.ptx
grep '%tid.x' examples/device_ptx/fill_indices/fill.ptx
grep 'st.global.u32' examples/device_ptx/fill_indices/fill.ptx
```

This requires `llvmlite`/LLVM with the NVPTX backend.  It does not require an
NVIDIA device, CUDA driver, CUDA runtime, `nvcc`, or the Pascal runtime library.
If NVIDIA tools are available, `ptxas` can provide a stronger validation step:

```bash
ptxas -arch=sm_70 -v -o fill.cubin examples/device_ptx/fill_indices/fill.ptx
```

To actually run the generated `.ptx`, use an external launcher first — PyCUDA or
a small CUDA Driver API program — on a CUDA-capable machine.  See
[`examples/device_ptx/fill_indices/README.md`](examples/device_ptx/fill_indices/README.md)
and
[`examples/device_ptx/fill_indices/RUNNING_PTX.md`](examples/device_ptx/fill_indices/RUNNING_PTX.md)
for the detailed example and runtime test plan.  Pascal host-side operations such
as device allocation, copy, launch, and synchronization are planned separately;
this PTX path is the first artifact-level bridge.

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

- **Lexer (`src/pascal1981/lexer.py`)** — tokenizes Pascal source: keywords, identifiers, numbers, operators, strings.
- **Parser (`src/pascal1981/parser.py`)** — builds an Abstract Syntax Tree (AST) from tokens. Implements the full IBM Pascal 2.0 grammar. Entry point: `parse_file(path)`.
- **Type Checker (`src/pascal1981/type_system.py`, `src/pascal1981/symbol_table.py`, `src/pascal1981/type_checker.py`)** — semantic analysis: validates types, scopes, control flow, and module semantics before code generation. All type violations stop the pipeline with clear error messages.
- **Feature flags (`src/pascal1981/features.py`)** — generic feature-gating machinery for opt-in dialect extensions such as `wide-integers` and `symbolic-enum-io`.
- **Type Checker support (`src/pascal1981/builtins_registry.py`)** — centralized registration of predeclared identifiers (types, constants, intrinsics); user declarations may shadow builtins.
- **Codegen (`src/pascal1981/codegen/` package)** — walks the AST and emits LLVM IR using `llvmlite`. Split by concern: `base`, `decls`, `exprs`, `stmts`, `types_map`, `constfold`, plus feature modules `files` (file-control blocks), `io_write_read`, `strings`, `sets`, and `runtime_builtins`. `codegen_llvm.py` remains as a compatibility shim re-exporting the package.
- **C Runtime (`runtime/`)** — the file I/O subsystem (`fileops.c`: FCB model, RESET/REWRITE/GET/PUT, ASSIGN/CLOSE/DISCARD, READSET/READFN, EOF/EOLN, mode enforcement), stdin readers (`readq.c`), ENCODE/DECODE (`encode_decode.c`), and the move/scan/fill/position intrinsics. `make -C runtime` builds `runtime/build/libpascalrt.a` with `clang`.
- **Linking** — `clang` lowers LLVM IR to native code and links either the installed `libpascalrt.a`, the source-tree `runtime/build/libpascalrt.a`, or `runtime/*.c` during checkout-only development.

### Grammar Reference

The grammar this dialect implements is formally specified in [`docs/ebnf_grammar.md`](docs/ebnf_grammar.md). The parser test suite is graded against this grammar as the source of truth.

## Supported Language Features

This compiler implements the full IBM Pascal 2.0 language, including all semantic rules and dialectal extensions.

### Types
- `INTEGER` (16-bit signed, matching IBM Pascal 2.0; range `-32767..32767`, i.e. `-MAXINT..MAXINT` with `MAXINT = 32767`; per the manual `-32768` is *not* a valid `INTEGER` — that bit pattern belongs to `WORD`)
- `INTEGER32` / `INTEGER64` (opt-in signed extension types enabled with `-f wide-integers`; also enables `MAXINT32` and `MAXINT64`)
- `INTEGER8` (opt-in 8-bit signed extension type, `-f wide-integers`; the Pascal spelling of C `int8_t`. Deliberately *not* a synonym for `CHAR`: `CHAR` is a character type with no arithmetic that `WRITE`s as a glyph, while `INTEGER8` is a true signed integer that does arithmetic and `WRITE`s as a number)
- `BOOLEAN` (one byte; stored as `i8` so address-of / `sizeof` / fills are byte-consistent)
- `REAL` (64-bit float; constants, division, unary minus, and mixed arithmetic are codegen-hardened, and the default `WRITE` format matches the manual's 14-wide exponential, e.g. `WRITE(123.456)` prints ` 1.2345600E+02`)
- `REAL32` / `REAL64` (opt-in extension real types enabled with `-f wide-reals`; `REAL32` is a 32-bit float lowering to LLVM `float`, `REAL64` is a 64-bit synonym for `REAL`. Always available inside `DEVICE` code regardless of the flag — `REAL32` is what gives device kernels true `.f32` parameter ABI.)
- `WORD` (16-bit unsigned)
- `WORD32` / `WORD64` (opt-in *unsigned* extension types enabled with `-f wide-integers`, the unsigned siblings of `INTEGER32`/`INTEGER64`; they zero-extend when widened and `WRITE` them unsigned. `WORD` widens implicitly to `WORD32` and `WORD64`; a signed `INTEGER` does not — convert with `WRD(...)` into `WORD` first)
- `WORD8` (opt-in 8-bit *unsigned* extension type, `-f wide-integers`; the Pascal spelling of C `uint8_t`, for byte buffers and pixel data. Widens implicitly to `WORD`/`WORD32`/`WORD64` (zero-extend) and to the wider signed types (every `WORD8` value fits). Narrowing into `WORD8` is never implicit — `WRD8(x)` is the explicit truncating retype, the 8-bit sibling of `WRD`. Across the `[C]` ABI, `WORD8` parameters/returns carry `zeroext` and `INTEGER8` carry `signext`)
- `WORD16` (= `WORD`) and `INTEGER16` (= `INTEGER`) — width-explicit synonyms enabled with `-f wide-integers` (or inside `DEVICE` code), alongside the other wide integer types
- `ARRAY[low..high] OF type` — bounds may be constant expressions, including named `CONST`s
- `RECORD ... END`
- `SET OF type` — 256-bit bitvector representation; constant constructors fold at compile time
- Enumerated types (`TYPE color = (RED, GREEN, BLUE)`)
- `STRING(n)` (fixed, blank-padded) and `LSTRING(n)` (length-prefixed) string storage, with character indexing (`S[I]` is the Ith character; STRING is 1-based, LSTRING index 0 is the length byte viewed as a CHAR, and `L.LEN` reads the length)
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
- Built-ins: `CHR`, `ORD`, `WRD` (and, under `-f wide-integers`, the truncating `WRD8` retype into `WORD8`), plus the intrinsic families `ENCODE`/`DECODE`, `SCANEQ`/`SCANNE`, `POSITN`, and the move/fill block operations

### Built-in I/O
- `WRITE`/`WRITELN` — mixed integers, characters, booleans, enums, REALs, strings, and string literals, with `:width`/`:width:frac` field formatting; an optional leading `TEXT` file argument selects the output stream (default `OUTPUT`/stdout). User enum values print as ordinals by default, matching IBM Pascal 2.0; `-f symbolic-enum-io` switches user enum output to member names. BOOLEAN always writes `TRUE`/`FALSE`, independent of that flag. The `::N` precision operand on STRING/LSTRING values is ignored by default (matching the vintage compiler, which prints the whole value); `-f string-precision` makes it truncate to `N` characters.
- `READ`/`READLN` — scalar and string targets, with an optional leading `TEXT` file argument (default `INPUT`/stdin). User enum READ accepts numeric ordinals by default; `-f symbolic-enum-io` switches enum READ to symbolic member names, gated together with symbolic enum WRITE so same-mode enum round-trips stay coherent.
- File primitives — `RESET`, `REWRITE`, `GET`, `PUT`, and the buffer variable `F^`, over an inline file-control block with a single fill path shared by `F^`, the predicates, and the formatted readers
- Extended I/O verbs — `ASSIGN` (filename binding; `CHR(0)` spells a temporary file), `CLOSE`, `DISCARD`, `READSET` (scan characters in a `SET OF CHAR`; the delimiter set must be a declared `SET OF CHAR` value, matching the vintage compiler — an inline set-constructor literal such as `['A'..'Z']` is rejected unless `-f readset-set-literal` is enabled), `READFN` (READLN-like dispatcher that binds filenames to file parameters)
- Stream predicates — `EOF` and `EOLN`, with line markers presented as blanks per the manual
- Mode enforcement — writing a file in inspection mode, writing a closed file, or reading a file in generation mode aborts with a runtime error rather than corrupting data

## Systems-Programming Extensions

These are the features that made Pascal suitable for writing operating systems, firmware, and device drivers. They allow direct memory manipulation while maintaining Pascal's type safety where possible:

- **`adr x`** — yields the address of a variable. Lowered to the variable's LLVM pointer, enabling low-level code.
- **`sizeof(x)` / `sizeof(T)`** — compile-time byte size, computed from real array bounds (constants are resolved) and element sizes; returns a `WORD`. Essential for buffer and layout calculations.
- **`adrmem`** — a generic address/pointer parameter type (`i8*` in LLVM). Pointer arguments are automatically bitcast to the parameter's type at the call site, enabling polymorphic low-level functions. Example: `adr flags` (an array pointer) can be passed where an `adrmem` is expected.
- **`extern` procedures** — declared without a body and resolved at link time. Enables linking Pascal code against C runtimes and external libraries.
- **`word` type** — 16-bit unsigned integer for register and hardware register operations.
- **Feature-gated wide integers** — `INTEGER8`/`INTEGER32`/`INTEGER64` and `WORD8`/`WORD32`/`WORD64` are available only with `-f wide-integers`; unflagged builds preserve the vintage 16-bit `INTEGER` surface.

### Foreign buffers: the heap super-array pattern

The sanctioned way for host Pascal to own a large typed buffer that crosses a
foreign boundary (a `[C]` routine or the `DEVCOPYTO`/`DEVCOPYFROM`
orchestration builtins) is a heap **super array** allocated with long-form
`NEW`, not a `malloc` extern returning an untyped `ADRMEM`:

```pascal
TYPE BUF = SUPER ARRAY [0..*] OF INTEGER32;
     PB  = ^BUF;
VAR p: PB;
...
NEW(p, n - 1);          { dynamic bound; i64 bound header + element data }
some_c_function(p);     { the pointer coerces to an ADRMEM / void* param }
DEVCOPYFROM(p, dev, bytes);
x := p^[i];             { typed element access, wide (INTEGER32) index   }
DISPOSE(p)
```

The pointer variable itself is accepted anywhere an `ADRMEM` is expected and
lowers to the raw element pointer (the bound header of
`docs/super-array-bounds-abi.md` sits *before* the data, so C sees a plain
`T*`).  Under `-f wide-integers` the pieces that make this scale past the
16-bit `INTEGER` range are enabled together: the `NEW` bound may be a wide
expression or a literal beyond 32767, arrays may be indexed with `INTEGER32`,
and `FOR` loops may use an `INTEGER32` control variable.  The vintage dialect
keeps its 16-bit rules.

### Record layout across the C boundary

A Pascal `RECORD` whose fields are C-representable scalars, pointers, and
fixed arrays is guaranteed to be laid out exactly like the corresponding C
struct on the host triple — same field offsets (natural alignment, implicit
padding included) and same total size (tail padding included, which `SIZEOF`
reports).  It is therefore sound to declare a third-party C struct as a Pascal
`RECORD` and pass it **by pointer** (`CONST`/`VAR` parameter) to an unmodified
C function through a `[C] EXTERN` declaration.  The guarantee is pinned
differentially against clang `offsetof`/`sizeof` in
`tests/test_c_record_layout.py`; passing or returning aggregates **by value**
is the separate, also-supported `[C]` classifier path (see
[`docs/c-abi-foreign-functions.md`](docs/c-abi-foreign-functions.md)).


## Device Code and Memory Spaces (experimental)

The vintage segmented-address machinery (`ADS`, `ADSMEM`, `FILLSC`/`MOVESL`/`MOVESR`)
is being repurposed into a static **memory-space** system for targeting LLVM GPU
backends. The reference is [`docs/ads-memory-spaces-design.md`](docs/ads-memory-spaces-design.md)
and the build sequence is [`docs/ads-implementation-plan.md`](docs/ads-implementation-plan.md);
This is in-progress work; the surface below is real and tested, but the host
orchestration/launch API and kernel marking are still deferred.

### The two-axis model

- **Module kind picks the language rules.** A regular `MODULE` is host code. A
  `DEVICE MODULE` is device code: the extended dialect, minus a module-scoped
  recission set (recursion, `NEW`/heap, host I/O, `GOTO` and its non-loop labels,
  dynamic set-range construction), plus the address-space surface. The boundary is lexical, so "is
  this device code" needs no reachability analysis.
- **Two target triples pick the lowering**, both defaulting to
  `x86_64-pc-linux-gnu` and independently overridable: `host` for `MODULE` code,
  `device` for `DEVICE MODULE` code. Point `device` at `nvptx64-nvidia-cuda` or
  `amdgcn-amd-amdhsa` for a real GPU; leave it at x86 to run device-dialect code
  on the CPU (every space collapses to addrspace 0 — the OpenCL-on-CPU case).

### Memory spaces

A predeclared enum `SPACE = (HOST, GLOBAL, SHARED, CONSTANT, LOCAL)` supplies the
space tags (meaningful only inside a `DEVICE MODULE`). Each `ADS` pointer carries
two independent spaces:

- **pointer space** — where the pointer variable itself lives, set by a
  `[SPACE(s)]` residence attribute: `VAR [SPACE(GLOBAL)] g: ARRAY[0..255] OF REAL;`
- **pointee space** — what it addresses, set on the type: `TYPE p = ADS(GLOBAL) OF REAL;`

Space is part of pointer-type identity: **static only, no mixing, fully explicit.**
A *dereferenceability invariant* is enforced by the type checker — `HOST` pointers
are dereferenceable only in host modules, the four device spaces only in device
modules. Crossing spaces is never a pointer cast (there is no `RESPACE`); it is
always a **data copy** via the `FILLSC`/`MOVESL`/`MOVESR` bridge (on-device) or a
host-orchestrated transfer (across the host/device line). Inside a `DEVICE MODULE`
those three builtins accept operands in *different* concrete spaces and lower to an
addrspace-aware byte loop (`ld.global`/`st.shared`-class on NVPTX); on the device
triple the spaces map `GLOBAL→1, SHARED→3, CONSTANT→4, LOCAL→5`.

### How to build device code

Two CLI flags select the target triples, independently:

- `--host-triple TRIPLE` — the triple for host `MODULE`/`PROGRAM` units
  (default `x86_64-pc-linux-gnu`).
- `--device-triple TRIPLE` — the triple for `DEVICE MODULE` units; set it to
  `nvptx64-nvidia-cuda` or `amdgcn-amd-amdhsa` for a real GPU. It defaults to the
  host x86 triple (the CPU-device case, where address spaces collapse to
  addrspace 0). An nvptx-family device triple makes `-S` emit PTX device
  assembly instead of host LLVM IR.

```bash
# CPU device (runnable here): spaces collapse to addrspace 0
pascal1981 -S kernel.pas -o kernel.ll

# GPU device: an nvptx --device-triple makes -S emit PTX device assembly;
# --save-llvm keeps the NVPTX IR (addrspace(1)/addrspace(3)/...) alongside
pascal1981 -S --device-triple nvptx64-nvidia-cuda kernel.pas -o kernel.ptx --save-llvm kernel.ll

# Cross-compile the host side too (triples are independent)
pascal1981 -S --host-triple aarch64-unknown-linux-gnu kernel.pas -o kernel.ll
```

The same triples are available on the `compile_to_llvm` package API:

```python
from pascal1981.codegen import compile_to_llvm
from pascal1981.type_checker import PascalTypeChecker
from pascal1981.parser import parse_file

ast = parse_file("kernel.pas")
assert PascalTypeChecker().check(ast).success

ir_cpu = compile_to_llvm(ast)                                    # CPU device (x86)
ir_gpu = compile_to_llvm(ast, device_triple="nvptx64-nvidia-cuda")
```

The **CPU-device** case produces runnable artifacts. A `DEVICE MODULE` has no
`main`, so link its IR against a host driver — e.g. a small C harness that
declares the module's globals and entry routine — with `clang`:

```bash
clang kernel.ll host_driver.c -o demo && ./demo
```

The **GPU-device** case (`nvptx64`/`amdgcn`) emits correct addrspace-qualified
LLVM IR, but producing and running a real GPU artifact needs an NVIDIA/AMD
toolchain and runtime that this project does not bundle — so on a host without a
GPU runtime that path is code-generation-complete but not executable.

**Future.** The host launch/allocate/transfer API, kind-aware `uses`, and
`KERNEL` marking are planned but not yet implemented; see the design record's
*Out of Scope* section and the implementation plan.


## Project Scope

This is a **full reimplementation** of IBM Pascal 2.0. The goal is not a subset or tutorial language, but complete dialect coverage as specified in the original IBM Pascal 2.0 manual. 

**Reference:** The original compiler manual is [here](https://archive.org/details/ibm-pascal-compiler-aug-81) — this is the source of truth for dialect semantics and feature completeness.

Dialect coverage is complete: the planned feature checklist has been worked through, and the remaining differential questions against the genuine 1981 compiler have been settled and archived under [`docs/old/`](docs/old/). Open follow-up seams are tracked in [`docs/followups.md`](docs/followups.md). Behaviors that the vintage compiler does not have, but that this implementation offers as deliberate extensions, are gated behind opt-in feature flags (see `features.py` / `--list-features`) so the default build stays faithful to IBM Pascal 2.0. The formal grammar lives in [`docs/ebnf_grammar.md`](docs/ebnf_grammar.md).

The test suite is organized to run independently at each layer, so development can proceed without the full LLVM toolchain.

## File Structure

```
pascal-1981/
├─ Python package
│  └── src/pascal1981/
│      ├── __init__.py             # public package API; runtime_lib_path()
│      ├── __main__.py             # python -m pascal1981 entry point
│      ├── compile_to_llvm.py      # Driver (parse → type-check → codegen)
│      ├── lexer.py                # Tokenizer
│      ├── parser.py               # Syntax analysis; builds AST via recursive descent
│      ├── ast_nodes.py            # AST node definitions
│      ├── type_system.py          # Type hierarchy and compatibility rules
│      ├── symbol_table.py         # Scope management and symbol lookup
│      ├── type_checker.py         # Semantic analysis
│      ├── builtins_registry.py    # Predeclared identifiers
│      ├── features.py             # Opt-in dialect feature flags
│      ├── codegen_llvm.py         # Compatibility shim re-exporting codegen/
│      └── codegen/                # LLVM IR generation package
│          ├── base.py, decls.py, exprs.py, stmts.py, types_map.py, constfold.py
│          ├── files.py            # File-control blocks (FCB layout, F^, file ops)
│          ├── io_write_read.py    # WRITE/READ lowering, field widths, file selectors
│          ├── strings.py, sets.py # STRING/LSTRING and SET lowering
│          └── runtime_builtins.py # Extern seams to the C runtime
│
├─ Runtime
│  └── runtime/
│      ├── Makefile                # clang build of build/libpascalrt.a
│      ├── pascalrt.h              # shared runtime declarations/layout
│      ├── fileops.c               # FCB model, files, ASSIGN/CLOSE/DISCARD, predicates
│      ├── readq.c                 # stdin READ/READLN readers
│      ├── encode_decode.c         # ENCODE/DECODE intrinsics
│      ├── mover.c, movel.c, movesl.c, movesr.c
│      ├── scaneq.c, positn.c
│      ├── fillc.c, fillsc.c
│      └── pabort.c
│
├─ Packaging
│  ├── pyproject.toml              # setuptools metadata and console script
│  ├── setup.py                    # custom build_py hook for libpascalrt.a
│  └── MANIFEST.in                 # sdist inputs for runtime/docs/tests
│
├─ Tests
│  └── tests/
│      ├── support.py              # Test helpers and dependency probes
│      ├── test_parser.py          # Parser accept/reject corpus
│      ├── test_typecheck.py       # Type rules and semantics
│      ├── test_codegen.py         # IR generation and build/run
│      ├── test_codegen_strings_bounds.py
│      ├── test_read_end_to_end.py
│      ├── test_runtime_fixes.py
│      ├── test_c_ffi.py               # [C] attribute, aliases, SysV classifier, variadics
│      ├── test_c_record_layout.py     # record layout differential vs clang offsetof/sizeof
│      ├── test_byte_types.py          # WORD8/INTEGER8 and WRD8
│      ├── test_super_array_host_buffer.py  # heap super-array foreign-buffer pattern
│      ├── ... (one focused suite per feature area; see tests/)
│      └── fixtures/parser/
│
└─ Documentation
   └── docs/
       ├── ebnf_grammar.md              # formal grammar (source of truth for the parser suite)
       ├── c-abi-foreign-functions.md   # [C] FFI: scalar map, record layout guarantee, host-buffer pattern
       ├── ads-memory-spaces-design.md  # ADS memory-space reference (enum, mapping, grammar rails, type rules)
       ├── device-kernel-orientation.md
       ├── super-array-bounds-abi.md    # heap super-array bound-header ABI
       ├── tuning-hints.md
       ├── command-line-support.md
       ├── followups.md                 # tracked tech-debt
       └── old/                         # archived plans, design rationale, and settled differential questions
```

## Testing

One unified test suite built on `pytest`, with automatic detection of optional dependencies. Tests are organized by **pipeline layer**, so you can run the subset relevant to your changes without requiring the full LLVM toolchain.

### Run the entire test suite

```bash
# All tests from a source checkout; codegen tests auto-skip if llvmlite/clang are unavailable
PYTHONPATH=src python3 -m pytest tests/ -q
```

The integration/link tests link against `runtime/build/libpascalrt.a`. On import,
`tests/support.py` builds that archive automatically (once per session, via
`make -C runtime`) if it is missing and `clang` is available, so a fresh
checkout does not need a manual build step first. If the automatic build itself
fails, `tests/support.py` raises a clear error naming `make -C runtime` as the
fix instead of letting each test fail later with an opaque clang link error. You
can still run `make -C runtime` yourself beforehand (e.g. to see build output,
or after touching the C sources) — it is idempotent. Parser/typecheck tests
need no dependencies at all; codegen IR-only tests need `llvmlite` but not the
archive.

If you installed the package into the active environment, `PYTHONPATH=src` is not
needed.

### Run by layer

```bash
# Parser accept/reject corpus + type rules (no llvmlite needed)
PYTHONPATH=src python3 -m pytest tests/test_parser.py tests/test_typecheck.py -q

# Codegen only (requires llvmlite + clang)
PYTHONPATH=src python3 -m pytest tests/test_codegen.py -q

# Multi-file integration tests (real files on disk, separate compile/link/run)
PYTHONPATH=src python3 -m pytest tests/integration/ -q
```

For one integration fixture at a time:

```bash
PYTHONPATH=src python3 -m pytest tests/integration/test_device_primes.py -q
PYTHONPATH=src python3 -m pytest tests/integration/test_host_uses.py -q
PYTHONPATH=src python3 -m pytest tests/integration/test_uses_graphics.py -q
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

- **`tests/integration/`** — Multi-file integration tier. These tests materialize
  real on-disk projects and exercise interface resolution, `USES` binding,
  separate IR generation, `clang` linking, and native execution. The suite under
  this directory is the living specification of the tier; see the individual
  `test_*.py` files there.

- **`tests/test_integration.py`** — Legacy integration corpus (currently removed from supported test suite).

### Dependency Isolation

The front end (lexer, parser, type checker) is pure Python with **no `llvmlite` dependency**. This means:
- `test_parser.py` and `test_typecheck.py` run on any Python 3.10+ system with no third-party packages
- `test_codegen.py` and `tests/integration/` require `llvmlite` and `clang`
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
- Python 3.10+ (the packaging floor set by `llvmlite`; see `pyproject.toml`)
- No external dependencies (pure Python implementation)

**For code generation (Pascal → LLVM IR):**
- Python 3.10+
- `llvmlite` (for LLVM IR generation via Python)

**For native executables and runtime builds:**
- `clang` (required to lower/link LLVM IR and to build the C runtime)
  - A harmless target-triple override warning from LLVM is expected and safe to ignore
- `make` and `ar` (used by `runtime/Makefile` to build `libpascalrt.a`)

**For pip installation from this repository:**
- Python 3.10+
- `pip`
- `clang`, `make`, and `ar` available on `PATH`, because installation builds and bundles the C runtime archive

**Note:** If `llvmlite` or `clang` are unavailable, the parser and type checker still work fully; only codegen/native tests are skipped.
