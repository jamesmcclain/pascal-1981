# Pascal-1981 Compiler

<img width="1536" height="1024" alt="image" src="https://github.com/user-attachments/assets/42f81123-5db6-4bb0-8661-84339ec218f9" />

This project reimplements IBM Pascal 2.0. The compiler targets LLVM IR. The semantic analysis phase runs in a dedicated type checker. The compiler handles the Pascal-1981 dialect. It supports all systems-programming extensions (`adr`, `sizeof`, `adrmem`, `word`, `extern`). These features made Pascal suitable for low-level operating system and firmware work in the early 1980s.

## Quick Start

You can run the compiler in two ways:

1. Install the package with pip and use the `pascal1981` console script.
2. Run the compiler from a source checkout with `PYTHONPATH=src`.

Both methods produce LLVM IR. Then `clang` generates the native executable. `clang` links the Pascal runtime archive or the runtime C sources.

### Install with pip

Install the package from a checkout of this repository:

```bash
python3 -m pip install .
```

The pip build compiles the C runtime with `make` and `clang`. The build fails early if either tool is missing.

```bash
make -C runtime all        # CPU shim + libpascalrt.a alias -- always
make -C runtime cuda       # CUDA shim -- added automatically when the
                           # CUDA toolkit headers are visible to clang
                           # ($CUDA_HOME/include/cuda.h; CUDA_HOME defaults
                           # to /usr/local/cuda)
```

The static archives (`libpascalrt.a`, `libpascalrt_cpu.a`, and `libpascalrt_cuda.a` if CUDA was found) are inside the installed Python package. The wheel carries the PEP 600 perennial tag of the build machine. For example, a wheel built on Ubuntu 24.04 carries tag `py3-none-manylinux_2_39_x86_64`. The archives use the glibc of the build machine. pip refuses the wheel on older-glibc machines. This prevents link failures at runtime.

Compile and link a program after installation:

```bash
# Pascal source -> native executable (compile + assemble + link via clang;
# the bundled libpascalrt.a is added to the link automatically)
pascal1981 myprogram.pas -o myprogram

# Run it
./myprogram
```

The `pascal1981` command line follows gcc conventions. Stage flags select how far compilation goes. The driver compiles, assembles, and links an executable (`a.out`, or `-o FILE`) when no stage flag is present. `-S` stops at assembly. LLVM IR goes to `./<name>.ll` for host. PTX goes to `./<name>.ptx` for nvptx `--device-triple`. `-c` stops at an object file (`./<name>.o`). `clang` handles assembling and linking. `-o FILE` names the output. The flag `-S -o -` writes to stdout. Flags `-O0`, `-O1`, `-O2`, `-O3` (or `-O`, meaning `-O1`) select the optimization level. The flag runs the mid-level IR pipeline of LLVM for host `-S`. The flag forwards to `clang` for `-c` and linking. The flag runs the pipeline before NVPTX codegen for an nvptx `--device-triple`. The command `-print-file-name=libpascalrt.a` prints the absolute path of the bundled runtime archive. Flags `-l LIB`, `-L DIR`, and `-Wl,ARG` pass through to the `clang` link step. Flag `-###` prints the `clang` commands without executing them.

You can also locate the runtime archive from Python:

```bash
python3 -c 'from pascal1981 import runtime_lib_path; print(runtime_lib_path())'
```

### Build a wheel

Build the wheel on any machine with `make` and `clang`:

```bash
python3 -m pip wheel . --no-deps -w dist
```

The build self-configures. A visible CUDA toolkit produces a full host+CUDA wheel. Otherwise the wheel is CPU-only. Build inside the CUDA development image to guarantee the full wheel. You do not need a GPU for the build:

```bash
docker build -t pascal-1981:latest -f docker/Dockerfile .   # once; see docker/README.md
docker run --rm -v "$PWD":/work pascal-1981:latest sh -c "pip wheel . --no-deps -w /work/dist"
```

The wheel lands in `dist/`. It carries the glibc floor tag of the build machine. For example, the container produces `pascal1981-1.0.0-py3-none-manylinux_2_39_x86_64.whl`. Run `unzip -l dist/*.whl | grep '\.a$'` to check the archive contents. The output shows three archives with CUDA, two without.

### Run from a source checkout without pip

Put `src/` on `PYTHONPATH` to run the compiler from the checkout:

```bash
PYTHONPATH=src python3 -m pascal1981 -S myprogram.pas -o myprogram.ll
```

Build the runtime static library manually:

```bash
make -C runtime
```

This command produces:

```text
runtime/build/libpascalrt.a
```

Then link against the archive:

```bash
clang myprogram.ll runtime/build/libpascalrt.a -o myprogram
./myprogram
```

After `make -C runtime`, the source-tree CLI can print the archive path:

```bash
PYTHONPATH=src python3 -m pascal1981 -print-file-name=libpascalrt.a
```

You can also link the runtime C files directly for quick source-tree experiments:

```bash
PYTHONPATH=src python3 -m pascal1981 -S myprogram.pas -o myprogram.ll
clang myprogram.ll runtime/*.c -o myprogram
```

Programs whose output lowers to bare `printf` can link without the runtime. Programs that touch files, `READ`/`READLN`, string intrinsics, `ENCODE`/`DECODE`, scan/fill/move intrinsics, or other `pas_...` helpers need the runtime archive on the link line. Otherwise the linker reports `undefined reference to pas_...`.

Add `-v` or `--verbose` for detailed output and full Python tracebacks:

```bash
pascal1981 -v -S myprogram.pas -o myprogram.ll
# or, from a source checkout:
PYTHONPATH=src python3 -m pascal1981 -v -S myprogram.pas -o myprogram.ll
```

Optional dialect extensions are controlled with feature flags. The default dialect is vintage IBM Pascal behavior. Wider integer types and symbolic enum I/O are off unless you enable them:

```bash
# Show available feature flags
pascal1981 --list-features

# Enable the wide/narrow integer extension family (INTEGER8/32/64,
# WORD8/32/64, MAXINT32/MAXINT64, MAXWORD32/MAXWORD64, WRD8)
pascal1981 -f wide-integers -S myprogram.pas -o myprogram.ll

# Enable name-based user enum WRITE and READ as an extension
pascal1981 -f symbolic-enum-io -S myprogram.pas -o myprogram.ll
```

The default dialect enforces the vintage WORD/INTEGER rules. A signed `INTEGER` variable is not assignment-compatible with `WORD`. Convert with `WRD(...)`. Use `ORD(...)` for the reverse. Mixing `WORD` with a non-constant `INTEGER` in an expression produces a warning. The exemption in the manual for INTEGER constants is generalized to the extension family. A compile-time constant integer expression can flow into any `WORD8`/`WORD32`/`WORD64` or `INTEGER8`/`INTEGER32`/`INTEGER64` target. The expression can be a literal, a named `CONST`, `SIZEOF`, or a foldable expression. Its value must fit the target range. Only non-constant values need explicit conversion. Flag `strict-word-int` promotes the mix warning to a hard error. It is a policy flag. It is orthogonal to `--dialect`. Enabling or disabling it never moves a program in or out of the extended dialect.

```bash
# Make every non-constant WORD/INTEGER expression mix a hard error
pascal1981 -f strict-word-int -S myprogram.pas -o myprogram.ll
```

The driver links an executable without a stage flag. Intermediate artifacts follow gcc naming rules (`-S` writes `./<name>.ll`, `-c` writes `./<name>.o`, linking writes `./a.out`) unless `-o` says otherwise. Use `-S -o -` to stream LLVM IR to stdout:

```bash
pascal1981 -S -o - myprogram.pas | clang -x ir - "$(pascal1981 -print-file-name=libpascalrt.a)" -o myprogram
```

Source-tree equivalent:

```bash
PYTHONPATH=src python3 -m pascal1981 -S -o - myprogram.pas | clang -x ir - runtime/build/libpascalrt.a -o myprogram
```

## Device PTX artifact generation

The compiler has an early device-only path for Pascal `DEVICE UNIT` / `DEVICE IMPLEMENTATION` code. The target is NVIDIA PTX. This path is for inspecting or externally launching GPU kernel artifacts. The path does not generate Pascal host-side CUDA orchestration.

Compile a device implementation directly to PTX from a source checkout:

```bash
PYTHONPATH=src python3 -m pascal1981.compile_to_ptx \
  examples/device_ptx/fill_indices/fill.pas \
  -o examples/device_ptx/fill_indices/fill.ptx \
  --save-llvm examples/device_ptx/fill_indices/fill.ll \
  --cpu sm_70 \
  -O2
```

The source file is a `DEVICE IMPLEMENTATION OF`. The sibling interface file contains the `DEVICE INTERFACE`. The interface file carries a `.inc` extension by convention in this repository. The compiler does not require this extension. Interface resolution also accepts an extensionless sibling or a `.pas` file. Exported procedures in the device interface lower as PTX kernel entries. For example:

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

This path requires `llvmlite`/LLVM with the NVPTX backend. It does not require an NVIDIA device, CUDA driver, CUDA runtime, `nvcc`, or the Pascal runtime library. Tool `ptxas` can provide a stronger validation step if NVIDIA tools are available:

```bash
ptxas -arch=sm_70 -v -o fill.cubin examples/device_ptx/fill_indices/fill.ptx
```

Run the generated `.ptx` with an external launcher first. Use PyCUDA or a small CUDA Driver API program on a CUDA-capable machine. See [`examples/device_ptx/fill_indices/README.md`](examples/device_ptx/fill_indices/README.md) and [`examples/device_ptx/fill_indices/RUNNING_PTX.md`](examples/device_ptx/fill_indices/RUNNING_PTX.md) for the detailed example and runtime test plan. Pascal host-side operations such as device allocation, copy, launch, and synchronization are planned separately. This PTX path is the first artifact-level bridge.

## Architecture

The pipeline uses a clean, layered design with clear separation of concerns:

```
Pascal Source -> Lexer -> Parser -> Type Checker -> Codegen -> LLVM IR -> clang -> Executable
```

### Design Philosophy

Each phase is independent and focused:
- The front end (lexer, parser, type checker) is pure Python with no LLVM dependency.
- Type errors stop the pipeline before any IR is generated.
- If compilation succeeds, the generated code will link and run.

### Components

- **Lexer (`src/pascal1981/lexer.py`)** — tokenizes Pascal source: keywords, identifiers, numbers, operators, strings.
- **Parser (`src/pascal1981/parser.py`)** — builds an Abstract Syntax Tree (AST) from tokens. Implements the full IBM Pascal 2.0 grammar. Entry point: `parse_file(path)`.
- **Type Checker (`src/pascal1981/type_system.py`, `src/pascal1981/symbol_table.py`, `src/pascal1981/type_checker.py`)** — semantic analysis. Validates types, scopes, control flow, and module semantics before code generation. All type violations stop the pipeline with clear error messages.
- **Feature flags (`src/pascal1981/features.py`)** — generic feature-gating machinery for opt-in dialect extensions such as `wide-integers` and `symbolic-enum-io`.
- **Type Checker support (`src/pascal1981/builtins_registry.py`)** — centralized registration of predeclared identifiers (types, constants, intrinsics). User declarations can shadow builtins.
- **Codegen (`src/pascal1981/codegen/` package)** — walks the AST and emits LLVM IR using `llvmlite`. Split by concern: `base`, `decls`, `exprs`, `stmts`, `types_map`, `constfold`, plus feature modules `files` (file-control blocks), `io_write_read`, `strings`, `sets`, and `runtime_builtins`. `codegen_llvm.py` remains as a compatibility shim.
- **C Runtime (`runtime/`)** — file I/O subsystem (`fileops.c`: FCB model, RESET/REWRITE/GET/PUT, ASSIGN/CLOSE/DISCARD, READSET/READFN, EOF/EOLN, mode enforcement), stdin readers (`readq.c`), ENCODE/DECODE (`encode_decode.c`), and move/scan/fill/position intrinsics. Command `make -C runtime` builds `runtime/build/libpascalrt.a` with `clang`.
- **Linking** — `clang` lowers LLVM IR to native code and links either the installed `libpascalrt.a`, the source-tree `runtime/build/libpascalrt.a`, or `runtime/*.c` during checkout-only development.

### Grammar Reference

The grammar for this dialect is specified in [`docs/ebnf_grammar.md`](docs/ebnf_grammar.md). The parser test suite is graded against this grammar.

## Supported Language Features

This compiler implements the full IBM Pascal 2.0 language. It includes all semantic rules and dialectal extensions.

### Types
- `INTEGER` (16-bit signed. Matches IBM Pascal 2.0. Range: `-32767..32767`. Per the manual, `-32768` is not a valid `INTEGER`. That bit pattern belongs to `WORD`.)
- `INTEGER32` / `INTEGER64` (opt-in signed extension types. Enable with `-f wide-integers`. Also enables `MAXINT32` and `MAXINT64`.)
- `INTEGER8` (opt-in 8-bit signed extension type. Enable with `-f wide-integers`. This is the Pascal spelling of C `int8_t`. It is not a synonym for `CHAR`. Type `CHAR` is a character type with no arithmetic. `WRITE` prints `CHAR` as a glyph. Type `INTEGER8` is a true signed integer. It does arithmetic. `WRITE` prints `INTEGER8` as a number.)
- `BOOLEAN` (one byte. Stored as `i8` for byte-consistent address-of, `sizeof`, and fills)
- `REAL` (64-bit float. Constants, division, unary minus, and mixed arithmetic are codegen-hardened. The default `WRITE` format matches the 14-wide exponential format of the manual. For example, `WRITE(123.456)` prints ` 1.2345600E+02`.)
- `REAL32` / `REAL64` (opt-in extension real types. Enable with `-f wide-reals`. `REAL32` is a 32-bit float that lowers to LLVM `float`. `REAL64` is a 64-bit synonym for `REAL`. Both are always available inside `DEVICE` code regardless of the flag. `REAL32` gives device kernels true `.f32` parameter ABI.)
- `WORD` (16-bit unsigned)
- `WORD32` / `WORD64` (opt-in unsigned extension types. Enable with `-f wide-integers`. These are the unsigned siblings of `INTEGER32`/`INTEGER64`. They zero-extend when widened. `WRITE` prints them unsigned. `WORD` widens implicitly to `WORD32` and `WORD64`. A signed `INTEGER` does not. Convert with `WRD(...)` into `WORD` first.)
- `WORD8` (opt-in 8-bit unsigned extension type. Enable with `-f wide-integers`. This is the Pascal spelling of C `uint8_t`. Use it for byte buffers and pixel data. It widens implicitly to `WORD`/`WORD32`/`WORD64` (zero-extend) and to the wider signed types. Narrowing into `WORD8` is never implicit. Use `WRD8(x)` for the explicit truncating retype. This is the 8-bit sibling of `WRD`. Parameters and returns for `WORD8` carry `zeroext` across the `[C]` ABI. Parameters and returns for `INTEGER8` carry `signext`.)
- `WORD16` (= `WORD`) and `INTEGER16` (= `INTEGER`) — width-explicit synonyms. Enable with `-f wide-integers` or use inside `DEVICE` code.
- `ARRAY[low..high] OF type` — bounds can be constant expressions, including named `CONST`s.
- `RECORD ... END`
- `SET OF type` — 256-bit bitvector representation. Constant constructors fold at compile time.
- Enumerated types (`TYPE color = (RED, GREEN, BLUE)`)
- `STRING(n)` (fixed, blank-padded) and `LSTRING(n)` (length-prefixed) string storage. Character indexing is supported (`S[I]` is the Ith character). STRING is 1-based. LSTRING index 0 is the length byte viewed as a CHAR. `L.LEN` reads the length.
- `TEXT` and binary `FILE OF T` file types. The buffer variable `F^` is backed by an inline file-control block.
- Predeclared `FILEMODES` enum (`SEQUENTIAL`, `TERMINAL`, `DIRECT`) and `FCBFQQ` record. `F.MODE` is readable and assignable on file variables.
- Pointers, plus the `adrmem` (generic address) parameter type.

### Declarations
- `VAR x, y: INTEGER`
- `CONST size = 8190` — constant values fold and are usable in array bounds, `sizeof`, and expressions.
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
- Built-ins: `CHR`, `ORD`, `WRD` (and `WRD8` under `-f wide-integers`), plus the intrinsic families `ENCODE`/`DECODE`, `SCANEQ`/`SCANNE`, `POSITN`, and move/fill block operations.

### Built-in I/O
- `WRITE`/`WRITELN` — mixed integers, characters, booleans, enums, REALs, strings, and string literals. Supports `:width`/`:width:frac` field formatting. An optional leading `TEXT` file argument selects the output stream. Default is `OUTPUT`/stdout. User enum values print as ordinals by default. This matches IBM Pascal 2.0. Flag `-f symbolic-enum-io` switches user enum output to member names. BOOLEAN always writes `TRUE`/`FALSE`. This behavior is independent of that flag. The `::N` precision operand on STRING/LSTRING values is ignored by default. This matches the vintage compiler, which prints the whole value. Flag `-f string-precision` truncates to `N` characters.
- `READ`/`READLN` — scalar and string targets. An optional leading `TEXT` file argument selects the input stream. Default is `INPUT`/stdin. User enum READ accepts numeric ordinals by default. Flag `-f symbolic-enum-io` switches enum READ to symbolic member names. This flag is gated together with symbolic enum WRITE. Same-mode enum round-trips stay coherent.
- File primitives — `RESET`, `REWRITE`, `GET`, `PUT`, and the buffer variable `F^`. These use an inline file-control block with a single fill path shared by `F^`, the predicates, and the formatted readers.
- Extended I/O verbs — `ASSIGN` (filename binding. `CHR(0)` spells a temporary file), `CLOSE`, `DISCARD`, `READSET` (scan characters in a `SET OF CHAR`. The delimiter set must be a declared `SET OF CHAR` value. This matches the vintage compiler. An inline set-constructor literal such as `['A'..'Z']` is rejected unless `-f readset-set-literal` is enabled.), `READFN` (READLN-like dispatcher that binds filenames to file parameters).
- Stream predicates — `EOF` and `EOLN`. Line markers are presented as blanks per the manual.
- Mode enforcement — writing a file in inspection mode, writing a closed file, or reading a file in generation mode aborts with a runtime error. Data corruption does not occur.

## Systems-Programming Extensions

These features made Pascal suitable for writing operating systems, firmware, and device drivers. They allow direct memory manipulation. They maintain Pascal type safety where possible:

- **`adr x`** — yields the address of a variable. Lowers to the LLVM pointer of the variable. Enables low-level code.
- **`sizeof(x)` / `sizeof(T)`** — compile-time byte size. Computed from real array bounds (constants are resolved) and element sizes. Returns a `WORD`. Essential for buffer and layout calculations.
- **`adrmem`** — a generic address/pointer parameter type (`i8*` in LLVM). Pointer arguments are automatically bitcast to the parameter type at the call site. Enables polymorphic low-level functions. Example: `adr flags` (an array pointer) can be passed where an `adrmem` is expected.
- **`extern` procedures** — declared without a body and resolved at link time. Enables linking Pascal code against C runtimes and external libraries.
- **`word` type** — 16-bit unsigned integer for register and hardware register operations.
- **Feature-gated wide integers** — `INTEGER8`/`INTEGER32`/`INTEGER64` and `WORD8`/`WORD32`/`WORD64` are available only with `-f wide-integers`. Unflagged builds preserve the vintage 16-bit `INTEGER` surface.

### Foreign buffers: the heap super-array pattern

Use a heap **super array** for host Pascal to own a large typed buffer that crosses a foreign boundary. Allocate it with long-form `NEW`. Do not use a `malloc` extern that returns an untyped `ADRMEM`:

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

The pointer variable is accepted where an `ADRMEM` is expected. It lowers to the raw element pointer. The bound header (see `docs/super-array-bounds-abi.md`) sits before the data. C sees a plain `T*`. Flag `-f wide-integers` enables the pieces that scale past the 16-bit `INTEGER` range together. The `NEW` bound can be a wide expression or a literal beyond 32767. Arrays can be indexed with `INTEGER32`. `FOR` loops can use an `INTEGER32` control variable. The vintage dialect keeps its 16-bit rules.

### Record layout across the C boundary

A Pascal `RECORD` whose fields are C-representable scalars, pointers, and fixed arrays has a guaranteed layout. The layout matches the corresponding C struct on the host triple. The field offsets are the same (natural alignment, implicit padding included). The total size is the same (tail padding included, which `SIZEOF` reports). You can declare a third-party C struct as a Pascal `RECORD` and pass it by pointer (`CONST`/`VAR` parameter) to an unmodified C function. Use a `[C] EXTERN` declaration for this. The guarantee is pinned differentially against `clang` `offsetof`/`sizeof` in `tests/test_c_record_layout.py`. Passing or returning aggregates by value is the separate, also-supported `[C]` classifier path. See [`docs/c-abi-foreign-functions.md`](docs/c-abi-foreign-functions.md).

## Device Code and Memory Spaces (experimental)

The vintage segmented-address machinery (`ADS`, `ADSMEM`, `FILLSC`/`MOVESL`/`MOVESR`) is repurposed. The target is a static memory-space system for LLVM GPU backends. See [`docs/ads-memory-spaces-design.md`](docs/ads-memory-spaces-design.md) for the reference. See [`docs/ads-implementation-plan.md`](docs/ads-implementation-plan.md) for the build sequence. This work is in progress. The surface below is real and tested. The host orchestration/launch API and kernel marking are still deferred.

### The two-axis model

- **Module kind picks the language rules.** A regular `MODULE` is host code. A `DEVICE MODULE` is device code. It uses the extended dialect minus a module-scoped recission set (recursion, `NEW`/heap, host I/O, `GOTO` and its non-loop labels, dynamic set-range construction). It adds the address-space surface. The boundary is lexical. You do not need reachability analysis to determine if the code is device code.
- **Two target triples pick the lowering.** Both default to `x86_64-pc-linux-gnu` and are independently overridable. Use `host` for `MODULE` code. Use `device` for `DEVICE MODULE` code. Point `device` at `nvptx64-nvidia-cuda` or `amdgcn-amd-amdhsa` for a real GPU. Leave it at x86 to run device-dialect code on the CPU. Every space collapses to addrspace 0 (the OpenCL-on-CPU case).

### Memory spaces

A predeclared enum `SPACE = (HOST, GLOBAL, SHARED, CONSTANT, LOCAL)` supplies the space tags. The tags are meaningful only inside a `DEVICE MODULE`. Each `ADS` pointer carries two independent spaces:

- **pointer space** — where the pointer variable itself lives. Set it with a `[SPACE(s)]` residence attribute: `VAR [SPACE(GLOBAL)] g: ARRAY[0..255] OF REAL;`
- **pointee space** — what the pointer addresses. Set it on the type. For example: `TYPE p = ADS(GLOBAL) OF REAL;`

Space is part of pointer-type identity. It is static only. Mixing is not allowed. Everything is explicit. The type checker enforces a dereferenceability invariant. `HOST` pointers are dereferenceable only in host modules. The four device spaces are dereferenceable only in device modules. Crossing spaces is never a pointer cast. There is no `RESPACE`. Crossing is always a data copy. Use the `FILLSC`/`MOVESL`/`MOVESR` bridge (on-device) or a host-orchestrated transfer (across the host/device line). The three builtins accept operands in different concrete spaces inside a `DEVICE MODULE`. They lower to an addrspace-aware byte loop (`ld.global`/`st.shared`-class on NVPTX). The spaces map `GLOBAL→1, SHARED→3, CONSTANT→4, LOCAL→5` on the device triple.

### How to build device code

Two CLI flags select the target triples. They are independent:

- `--host-triple TRIPLE` — the triple for host `MODULE`/`PROGRAM` units (default `x86_64-pc-linux-gnu`).
- `--device-triple TRIPLE` — the triple for `DEVICE MODULE` units. Set it to `nvptx64-nvidia-cuda` or `amdgcn-amd-amdhsa` for a real GPU. It defaults to the host x86 triple (the CPU-device case where address spaces collapse to addrspace 0). An nvptx-family device triple makes `-S` emit PTX device assembly instead of host LLVM IR.

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

The **CPU-device** case produces runnable artifacts. A `DEVICE MODULE` has no `main`. Link its IR against a host driver. Use a small C harness that declares the globals of the module and entry routine. Link with `clang`:

```bash
clang kernel.ll host_driver.c -o demo && ./demo
```

The **GPU-device** case (`nvptx64`/`amdgcn`) emits correct addrspace-qualified LLVM IR. An NVIDIA/AMD toolchain and runtime are needed to produce and run a real GPU artifact. This project does not bundle those tools. The path is code-generation-complete but not executable on a host without a GPU runtime.

The host launch/allocate/transfer API, kind-aware `uses`, and `KERNEL` marking are planned but not yet implemented. See the *Out of Scope* section of the design record and the implementation plan.

## Project Scope

This project is a **full reimplementation** of IBM Pascal 2.0. The goal is complete dialect coverage. It is not a subset or tutorial language. The original IBM Pascal 2.0 manual specifies the dialect.

**Reference:** The original compiler manual is [here](https://archive.org/details/ibm-pascal-compiler-aug-81). This manual is the source of truth for dialect semantics and feature completeness.

Dialect coverage is complete. The planned feature checklist is worked through. The remaining differential questions against the genuine 1981 compiler are settled and archived under [`docs/old/`](docs/old/). Open follow-up seams are tracked in [`docs/followups.md`](docs/followups.md). Behaviors that the vintage compiler does not have are gated behind opt-in feature flags. See `features.py` and `--list-features`. The default build stays faithful to IBM Pascal 2.0. The formal grammar is in [`docs/ebnf_grammar.md`](docs/ebnf_grammar.md).

The test suite runs independently at each layer. Development can proceed without the full LLVM toolchain.

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

One unified test suite uses `pytest`. It detects optional dependencies automatically. Tests are organized by pipeline layer. Run the subset relevant to your changes. The full LLVM toolchain is not required.

### Run the entire test suite

```bash
# All tests from a source checkout; codegen tests auto-skip if llvmlite/clang are unavailable
PYTHONPATH=src python3 -m pytest tests/ -q
```

The integration/link tests link against `runtime/build/libpascalrt.a`. On import, `tests/support.py` builds that archive automatically. The build runs once per session via `make -C runtime`. The build runs if the archive is missing and `clang` is available. A fresh checkout does not need a manual build step first. If the automatic build itself fails, `tests/support.py` raises a clear error. The error names `make -C runtime` as the fix. Each test does not fail later with an opaque `clang` link error. You can also run `make -C runtime` yourself beforehand. Run it to see build output or after touching the C sources. The build is idempotent. Parser and typecheck tests need no dependencies at all. Codegen IR-only tests need `llvmlite` but not the archive.

If you installed the package into the active environment, `PYTHONPATH=src` is not needed.

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

  The tests do not use subprocess or stdout grepping. Verdicts come from catching `(ParserError, LexerError)`. Each fixture runs in a `subTest` for isolated failure reporting.

- **`tests/test_typecheck.py`** — Type rules, scope, compatibility, control flow, and module semantics. The tests are organized by topic into `TestCase` classes (`TestVariableScope`, `TestTypeCompatibility`, `TestModuleSemantics`, etc.). The tests run in-process. They do not use subprocess or `llvmlite`.

- **`tests/test_codegen.py`** — LLVM IR generation and native build/run tests. Decorated with `@requires_llvm` (IR tests) and `@requires_exe` (build/run tests). The tests skip automatically if the toolchain is unavailable. The suite still exits 0.

- **`tests/test_codegen_strings_bounds.py`** — string-intrinsic capacity semantics, WRITE field-width ordering, and READ dispatch guards at the IR and run level.

- **`tests/test_read_end_to_end.py`** — piped-stdin READ/READLN run tests across scalar and string types.

- **`tests/test_runtime_fixes.py`** — hostile run tests pinning previously-wrong runtime behaviors. Tests cover NEW sizing, ENCODE/DECODE, SCANNE, and the file subsystem (buffer-variable model, RESET/GET interleaves, mode-enforcement aborts, ASSIGN/CLOSE/DISCARD/READSET/READFN).

- **`tests/integration/`** — Multi-file integration tier. These tests materialize real on-disk projects. They exercise interface resolution, `USES` binding, separate IR generation, `clang` linking, and native execution. The suite under this directory is the living specification of the tier. See the individual `test_*.py` files there.

- **`tests/test_integration.py`** — Legacy integration corpus (currently removed from supported test suite).

### Dependency Isolation

The front end (lexer, parser, type checker) is pure Python. It has no `llvmlite` dependency:
- `test_parser.py` and `test_typecheck.py` run on any Python 3.10+ system with no third-party packages
- `test_codegen.py` and `tests/integration/` require `llvmlite` and `clang`
- The suite auto-skips tests without failure if codegen dependencies are missing

## Implementation Notes

### Data Structures

- **AST** — typed dataclasses defined in `ast_nodes.py`. One class per language construct. The parser builds the tree bottom-up using recursive descent. Array, record, and pointer access use selector nodes for uniform representation.
- **Type System** — modular type hierarchy. Base scalar types (`INTEGER`, `REAL`, `BOOLEAN`, `CHAR`, `WORD`, plus feature-gated `INTEGER32`/`INTEGER64`) and composite types (ARRAY, RECORD, SET, POINTER) and callable types (PROCEDURE, FUNCTION). Implements the strict assignment rules of Pascal with explicit type compatibility checks.
- **Symbol Table** — scope stack with parent chain for lexical scoping. Symbols are tagged by kind (var, const, function, procedure, parameter, type). Scope-aware lookups and proper shadowing rules are supported.
- **Codegen** — direct LLVM IR emission using `llvmlite`. No intermediate IR. The AST walks directly to LLVM instructions. Globals receive proper zero initializers. Named constants fold at compile time. Function arguments are coerced (pointer bitcasts, integer width adjustments) to match callee signatures.

### Key Design Decisions

- **Type checking before codegen** — all type errors are caught and reported before any IR is generated. Successful type checking implies compilable output.
- **Minimal operator overloading** — each operator works on specific types with explicit type rules. The ambiguity that makes compiled languages harder to reason about is avoided.
- **Array bounds at compile time** — constant expressions in array declarations enable `sizeof` and layout calculations. The resolution runs during parsing. This is essential for systems programming.
- **Vintage integer width by default** — `INTEGER` lowers to signed 16-bit LLVM IR. Wider signed integers are extension-only (`INTEGER32`, `INTEGER64`). You must enable them deliberately with `-f wide-integers`. No compatibility flag makes default `INTEGER` 32-bit.

## Requirements

**For parsing and type checking:**
- Python 3.10+ (the packaging floor set by `llvmlite`. See `pyproject.toml`)
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
- `clang`, `make`, and `ar` available on `PATH` (installation builds and bundles the C runtime archive)

**Note:** The parser and type checker still work fully if `llvmlite` or `clang` are unavailable. Only codegen and native tests are skipped.
