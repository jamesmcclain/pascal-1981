"""
CodegenBase: Core infrastructure for the LLVM IR code generator.

Contains:
- CodegenError exception
- Symbol, LoopContext, Scope support classes
- CodegenBase with __init__, logging, predeclared registration, unique_name
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder

from ..ast_nodes import *


class CodegenError(Exception):
    pass


class Symbol:
    """A symbol in the current scope."""

    def __init__(self, name: str, llvm_value: Any, type_expr: Type, is_parameter: bool = False):
        self.name = name
        self.llvm_value = llvm_value  # ir.Value or ir.Function or ir.GlobalVariable
        self.type_expr = type_expr
        self.is_parameter = is_parameter  # True if this is a function parameter (passed by value)


@dataclass
class LoopContext:
    label: Optional[Union[int, str]]
    break_block: ir.Block
    cycle_block: ir.Block


class Scope:
    """A scope (function or block) with symbol table."""

    def __init__(self, parent: Optional[Scope] = None):
        self.parent = parent
        self.symbols: Dict[str, Symbol] = {}

    def define(self, name: str, llvm_value: Any, type_expr: Type, is_parameter: bool = False) -> None:
        """Define a symbol in this scope."""
        self.symbols[name.lower()] = Symbol(name, llvm_value, type_expr, is_parameter)

    def lookup(self, name: str) -> Optional[Symbol]:
        """Look up a symbol, checking parent scopes."""
        key = name.lower()
        if key in self.symbols:
            return self.symbols[key]
        if self.parent:
            return self.parent.lookup(name)
        return None


def _is_gpu_triple(triple: str) -> bool:
    """True for real GPU target triples (NVPTX / AMDGPU).

    Single source of truth shared by addrspace lowering (_space_addrspace) and
    the device host-runtime-extern skip (checklist S2.2.1).  The x86 CPU-device
    triple is deliberately *not* a GPU triple: there address spaces collapse to
    0 and device code still links the host runtime, so its externs stay (the
    green-safe boundary)."""
    return triple.startswith('nvptx') or triple.startswith('amdgcn')


_SCALAR_SIZES = {
    'INTEGER': 2,
    'INTEGER32': 4,
    'INTEGER64': 8,
    'REAL': 8,
    'REAL64': 8,
    'REAL32': 4,
    'WORD': 2,
    'CHAR': 1,
    'BOOLEAN': 1,  # vintage Pascal BOOLEAN is one byte
    'ADRMEM': 8,  # 64-bit pointer
}


class CodegenBase:
    """Base infrastructure for the LLVM IR code generator.
    
    Initializes module state, builder, scope, and shared constants/tables.
    """

    def __init__(self, verbose: bool = False, source_file: Optional[str] = None, force_flags: Optional[Dict[str, bool]] = None, features: Optional[Dict[str, bool]] = None, device_triple: str = "x86_64-pc-linux-gnu", host_triple: str = "x86_64-pc-linux-gnu", is_root_compiland: bool = True):
        # Each compilation gets its own LLVM context. Identified struct types
        # (used for named records, so self-referential linked-list nodes can
        # build) are interned by name *within a context*; the default global
        # context would leak a record named e.g. `node` across separate
        # compilations in one process, so distinct programs that reuse a type
        # name would collide. A fresh context per module keeps them isolated.
        self.module = ir.Module(name="pascal_program", context=ir.Context())
        self.source_file = source_file
        # Lowering target for host MODULE / PROGRAM units. Defaults to x86 Linux;
        # override with --host-triple for cross-compilation of the host side.
        self.host_triple = host_triple
        self.module.triple = host_triple
        # Lowering target for device compilands (historical name kept for the
        # existing DEVICE MODULE path; DEVICE UNIT lowering reuses it later).
        # Defaults to x86 (CPU-device), which collapses every address space to
        # addrspace 0; override with a GPU triple (nvptx64.../amdgcn...) to get
        # space-specific lowering (ads-memory-spaces-design.md S1.2/S3.2).
        self.device_triple = device_triple
        # Historical name: now means "currently lowering device code" once
        # DEVICE UNIT codegen is wired. Host/vintage codegen stays unchanged.
        self.is_device_module = False
        self.builder: Optional[IRBuilder] = None
        self.scope = Scope()  # global scope
        self._root_scope = self.scope  # fixed reference; self.scope moves during function lowering
        # Cache of LLVM identified struct types for named records, keyed by type
        # name. Lets self-referential records (linked-list nodes) build without
        # infinite recursion: the handle is cached before its body is set.
        self._identified_records: Dict[str, ir.Type] = {}
        self.current_function: Optional[ir.Function] = None
        self.current_return_block: Optional[ir.BasicBlock] = None
        self.features: Dict[str, bool] = features if features is not None else {}
        # Compile-time constants keyed UPPER.  Values are int for INTEGER/BOOL/CHAR
        # constants, or float for REAL constants.  Use _const_ir() to emit the
        # appropriate LLVM constant at reference sites.
        self.constants: Dict[str, object] = {
            'MAXINT': 32767,
            'MAXWORD': 65535,
            'SEQUENTIAL': 0,
            'TERMINAL': 1,
            'DIRECT': 2,
            # SPACE enum ordinals. Builtin enums do not auto-seed codegen's
            # constants (only the checker symbol table gets them), so they are
            # hand-seeded here alongside MAXINT (plan Step 0/Step 1), otherwise
            # ADS(GLOBAL) type-checks but fails to fold. Inert outside device code.
            'HOST': 0,
            'GLOBAL': 1,
            'SHARED': 2,
            'CONSTANT': 3,
            'LOCAL': 4,
        }
        if self.feature_enabled('wide-integers'):
            self.constants['MAXINT32'] = 2147483647
            self.constants['MAXINT64'] = 9223372036854775807
        self.type_aliases: Dict[str, Type] = {}  # compile-time type aliases, keyed UPPER
        self.current_interface_decls: Dict[str, Declaration] = {}
        self.proc_param_modes: Dict[str, List[Optional[str]]] = {}
        self.loop_stack: List[LoopContext] = []
        # Per-routine map of (normalized) label id -> the LLVM block that the
        # corresponding labeled statement begins.  Blocks are pre-created for
        # every label in a routine body before its statements are lowered, so a
        # GOTO can target a label that appears either earlier (backward) or
        # later (forward) in the source.  Reset/restored per function body.
        self.label_blocks: Dict[Union[int, str], ir.Block] = {}
        # Enum support (checklist 9.8): map each enum member (UPPER) to the full
        # ordered member-name list of its enum so WRITE can print the symbolic
        # name of a bare member literal; cache the per-enum `[n x i8*]` name
        # tables so each enum emits its name strings only once.
        self.enum_member_names: Dict[str, List[str]] = {}
        self._enum_name_tables: Dict[str, ir.GlobalVariable] = {}
        self.verbose = verbose
        # CLI flag overrides: maps flag name (upper) → forced bool.
        # A key absent from this dict means "use whatever the source says".
        self.force_flags: Dict[str, bool] = force_flags if force_flags is not None else {}
        # Metacommand flag state of the innermost statement currently being
        # lowered (set by codegen_stmt).  Expression-level runtime checks
        # (INDEXCK, MATHCK, NILCK) read it via check_enabled().
        self._stmt_meta: Optional[Dict[str, bool]] = None
        # Lazy extern registration (checklist S2.2.1 full/lazy form): build a
        # private cache/registry now (cheap — no IR emitted), materialise each extern
        # the first time codegen actually references it via runtime_extern().
        # Dead externs (never referenced) never appear in the module IR at all,
        # making "no dead host-runtime declare" hold for every triple — not just
        # GPU device targets.  The old gated-skip scaffolding is removed.
        self._runtime_extern_cache: Dict[str, ir.Function] = {}
        self._build_extern_factories()
        # INPUT/OUTPUT: only PROGRAM owns the strong definition; MODULE and
        # UNIT compilands emit declare-only (external global) so the linker
        # resolves to the single copy in the program root (S4.1).
        self._register_predeclared_files(is_root_compiland)

    def _log(self, msg: str) -> None:
        """Emit a diagnostic line to stderr when verbose mode is on."""
        if self.verbose:
            import sys
            print(f'[codegen] {msg}', file=sys.stderr)

    def feature_enabled(self, name: str) -> bool:
        """Return whether a named compile-time extension feature is enabled."""
        return self.features.get(name, False)

    def _space_addrspace(self, space_ord: Optional[int]) -> int:
        """Map a SPACE ordinal to its LLVM addrspace for the device triple.

        GPU triples use the validated S3.2 table (GLOBAL=1, SHARED=3,
        CONSTANT=4, LOCAL=5); HOST and the x86 CPU-device collapse to 0.
        """
        if not space_ord:  # None or 0 (HOST)
            return 0
        if _is_gpu_triple(self.device_triple):
            return {1: 1, 2: 3, 3: 4, 4: 5}.get(space_ord, 0)
        return 0  # device=x86 (CPU-device): spaces collapse to addrspace 0

    # Runtime-check flags whose failure path lowers to a *host* trap
    # (fflush+abort, via emit_runtime_abort / _emit_case_no_match_trap /
    # _guard_string_capacity).  In device code those host symbols don't exist,
    # so the checks are suppressed wholesale (checklist S2.1.1; prescription
    # S2.3.A1).  INITCK is deliberately excluded: it zero-initializes rather
    # than trapping, so it is harmless — and arguably desirable — on device.
    _HOST_TRAPPING_CHECKS = frozenset({'MATHCK', 'RANGECK', 'INDEXCK', 'NILCK', 'STACKCK'})

    def _device_checks_suppressed(self, flag: str) -> bool:
        """True when a host-trapping runtime check must be elided because we
        are currently lowering device code.

        Single chokepoint shared by the two flag-evaluation paths
        (check_enabled for expression-level MATHCK/INDEXCK/NILCK, and
        effective_flag for statement-level RANGECK).  Fires only under
        is_device_module, so host/vintage and DEVICE-MODULE-on-host lowering
        stay byte-identical; on a GPU triple it is what makes device IR carry
        zero abort/fflush (checklist S2.1 green gate).  A future S2.1.2 could
        swap the elision here for an on-device llvm.trap() instead.
        """
        return self.is_device_module and flag in self._HOST_TRAPPING_CHECKS

    def check_enabled(self, flag: str) -> bool:
        """Effective value of a runtime-check flag at the current lowering
        point.  Priority: device-code suppression > CLI force override >
        metacommand state of the innermost enclosing statement > the manual's
        documented default.
        """
        if self._device_checks_suppressed(flag):
            return False
        if flag in self.force_flags:
            return self.force_flags[flag]
        if self._stmt_meta is not None and flag in self._stmt_meta:
            return self._stmt_meta[flag]
        from ..lexer import _ON_OFF_FLAGS
        return _ON_OFF_FLAGS.get(flag, True)

    def _emit_runtime_check(self, ok_cond: 'ir.Value', label: str) -> None:
        """Emit a guarded runtime check: if ok_cond is false, call the
        runtime error handler (abort) — same shape as the string capacity
        guards.  The builder is left positioned in the ok block.
        """
        parent = self.builder.block.parent
        ok_block = parent.append_basic_block(label + '_ok')
        err_block = parent.append_basic_block(label + '_fail')
        self.builder.cbranch(ok_cond, ok_block, err_block)
        self.builder.position_at_end(err_block)
        self.emit_runtime_abort()
        self.builder.unreachable()
        self.builder.position_at_end(ok_block)

    def _register_predeclared_files(self, is_root_compiland: bool = True) -> None:
        """Declare INPUT and OUTPUT as predeclared TEXT file handles.

        PROGRAM compilands emit a strong global definition — the single owner
        of these program-wide singletons. MODULE and UNIT compilands emit an
        external declaration only; the linker resolves their reference to the
        PROGRAM definition. This prevents multiple-definition collisions when
        linking a host program with one or more compiled library objects
        (checklist S4.1).
        """
        text_type = FileType(NamedType('CHAR', None), structure='ASCII')
        for name in ('INPUT', 'OUTPUT'):
            gv = ir.GlobalVariable(self.module, ir.IntType(8).as_pointer(), name=name.lower())
            if is_root_compiland:
                gv.initializer = ir.Constant(ir.IntType(8).as_pointer(), None)  # strong definition
            else:
                gv.linkage = 'external'  # declare-only; definition lives in root compiland
            self.scope.define(name, gv, text_type)

    def _build_extern_factories(self) -> None:
        """Build a registry of zero-arg factory callables for every host-runtime extern.

        Calling a factory creates the corresponding ir.Function in self.module
        with external linkage.  Nothing is added to the module until the first
        call to runtime_extern(name).  Types are computed once here (cheap;
        no IR emitted) and captured in closures.
        """
        m = self.module  # captured by all factories
        # Shared type shorthands.
        i8p  = ir.IntType(8).as_pointer()
        i8   = ir.IntType(8)
        i16  = ir.IntType(16)
        i32  = ir.IntType(32)
        i64  = ir.IntType(64)
        f64  = ir.DoubleType()
        void = ir.VoidType()
        ads_ty  = ir.LiteralStructType([i8p, i16])
        fcb_ty  = self.file_fcb_type()
        fcb_ptr = fcb_ty.as_pointer()
        set_ptr = ir.ArrayType(i64, 4).as_pointer()

        def _f(name: str, ty: ir.FunctionType):
            """Create one external ir.Function in the module."""
            fn = ir.Function(m, ty, name=name)
            fn.linkage = 'external'
            return fn

        def _mk(name: str, ty: ir.FunctionType):
            """Return a zero-arg factory that creates the function on first call."""
            return lambda: _f(name, ty)

        self._extern_factories: Dict[str, Any] = {
            # ---- fill family -----------------------------------------------
            'fillc':  _mk('fillc',  ir.FunctionType(i32, [i8p, i16, i8])),
            'fillsc': _mk('fillsc', ir.FunctionType(i32, [ads_ty, i16, i8])),
            # ---- move family -----------------------------------------------
            'movel':  _mk('movel',  ir.FunctionType(i32, [i8p, i8p, i16])),
            'mover':  _mk('mover',  ir.FunctionType(i32, [i8p, i8p, i16])),
            'movesl': _mk('movesl', ir.FunctionType(i32, [ads_ty, ads_ty, i16])),
            'movesr': _mk('movesr', ir.FunctionType(i32, [ads_ty, ads_ty, i16])),
            # ---- libc / libm -----------------------------------------------
            'printf': _mk('printf', ir.FunctionType(i32, [i8p], var_arg=True)),
            'memcpy':  _mk('memcpy',  ir.FunctionType(i8p,  [i8p, i8p, i64])),
            'memset':  _mk('memset',  ir.FunctionType(i8p,  [i8p, i32, i64])),
            'memmove': _mk('memmove', ir.FunctionType(void, [i8p, i8p, i64])),
            'malloc':  _mk('malloc',  ir.FunctionType(i8p,  [i64])),
            'free':    _mk('free',    ir.FunctionType(void, [i8p])),
            'abort':   _mk('abort',   ir.FunctionType(void, [])),
            'fflush':  _mk('fflush',  ir.FunctionType(i32, [i8p])),
            'sqrt':    _mk('sqrt',    ir.FunctionType(f64, [f64])),
            'sin':     _mk('sin',     ir.FunctionType(f64, [f64])),
            'cos':     _mk('cos',     ir.FunctionType(f64, [f64])),
            'log':     _mk('log',     ir.FunctionType(f64, [f64])),
            'exp':     _mk('exp',     ir.FunctionType(f64, [f64])),
            'atan':    _mk('atan',    ir.FunctionType(f64, [f64])),
            # ---- string helpers --------------------------------------------
            'positn':       _mk('positn',       ir.FunctionType(i32, [i8p, i32, i8p, i32])),
            'scaneq':       _mk('scaneq',       ir.FunctionType(i32, [i32, i8, i8p, i32, i32, i32])),
            'scanne':       _mk('scanne',       ir.FunctionType(i32, [i32, i8, i8p, i32, i32, i32])),
            'encode_value': _mk('encode_value', ir.FunctionType(i32, [i8p, i32, i8p, i32, i32, i32, i32])),
            'decode_value': _mk('decode_value', ir.FunctionType(i32, [i8p, i32, i8p, i32, i32, i32, i32])),
            # ---- stdin read family -----------------------------------------
            'pas_read_int':    _mk('pas_read_int',    ir.FunctionType(i32,  [i32.as_pointer()])),
            'pas_read_word':   _mk('pas_read_word',   ir.FunctionType(i32,  [i16.as_pointer()])),
            'pas_read_real':   _mk('pas_read_real',   ir.FunctionType(i32,  [f64.as_pointer()])),
            'pas_read_char':   _mk('pas_read_char',   ir.FunctionType(i32,  [i8p])),
            'pas_read_lstring':_mk('pas_read_lstring',ir.FunctionType(i32,  [i8p, i32])),
            'pas_read_string': _mk('pas_read_string', ir.FunctionType(i32,  [i8p, i32])),
            'pas_readln_skip': _mk('pas_readln_skip', ir.FunctionType(void, [])),
            # ---- file-based read family ------------------------------------
            'pas_fread_int':    _mk('pas_fread_int',    ir.FunctionType(i32,  [fcb_ptr, i32.as_pointer()])),
            'pas_fread_word':   _mk('pas_fread_word',   ir.FunctionType(i32,  [fcb_ptr, i16.as_pointer()])),
            'pas_fread_real':   _mk('pas_fread_real',   ir.FunctionType(i32,  [fcb_ptr, f64.as_pointer()])),
            'pas_fread_char':   _mk('pas_fread_char',   ir.FunctionType(i32,  [fcb_ptr, i8p])),
            'pas_fread_lstring':_mk('pas_fread_lstring',ir.FunctionType(i32,  [fcb_ptr, i8p, i32])),
            'pas_fread_string': _mk('pas_fread_string', ir.FunctionType(i32,  [fcb_ptr, i8p, i32])),
            'pas_freadln_skip': _mk('pas_freadln_skip', ir.FunctionType(void, [fcb_ptr])),
            'pas_freadset':     _mk('pas_freadset',     ir.FunctionType(void, [fcb_ptr, i8p, i32, set_ptr])),
            'pas_fread_filename':_mk('pas_fread_filename', ir.FunctionType(void, [fcb_ptr, fcb_ptr])),
            # ---- file control ----------------------------------------------
            'pas_file_buffer':       _mk('pas_file_buffer',       ir.FunctionType(i8p,  [fcb_ptr])),
            'pas_file_touch_buffer': _mk('pas_file_touch_buffer', ir.FunctionType(void, [fcb_ptr])),
            'pas_file_reset':        _mk('pas_file_reset',        ir.FunctionType(void, [fcb_ptr])),
            'pas_file_rewrite':      _mk('pas_file_rewrite',      ir.FunctionType(void, [fcb_ptr])),
            'pas_file_get':          _mk('pas_file_get',          ir.FunctionType(void, [fcb_ptr])),
            'pas_file_put':          _mk('pas_file_put',          ir.FunctionType(void, [fcb_ptr])),
            'pas_file_close':        _mk('pas_file_close',        ir.FunctionType(void, [fcb_ptr])),
            'pas_file_discard':      _mk('pas_file_discard',      ir.FunctionType(void, [fcb_ptr])),
            'pas_file_assign':       _mk('pas_file_assign',       ir.FunctionType(void, [fcb_ptr, i8p, i32])),
            'pas_file_attach_std':   _mk('pas_file_attach_std',   ir.FunctionType(void, [fcb_ptr, fcb_ptr])),
            'pas_file_eof':          _mk('pas_file_eof',          ir.FunctionType(i32,  [fcb_ptr])),
            'pas_file_eoln':         _mk('pas_file_eoln',         ir.FunctionType(i32,  [fcb_ptr])),
            # ---- write / enum helpers -------------------------------------
            'pas_write_fmt': _mk('pas_write_fmt', ir.FunctionType(i32, [fcb_ptr, i8p], var_arg=True)),
            'pas_enum_write_token': _mk('pas_enum_write_token', ir.FunctionType(i8p, [i32, i8p.as_pointer(), i32])),
            'pas_read_enum_name': _mk('pas_read_enum_name', ir.FunctionType(i32, [i32.as_pointer(), i8p.as_pointer(), i32])),
            'pas_fread_enum_name': _mk('pas_fread_enum_name', ir.FunctionType(i32, [fcb_ptr, i32.as_pointer(), i8p.as_pointer(), i32])),
            'pabort': _mk('pabort', ir.FunctionType(void, [i8p, i32, i16, i16])),
        }

    def runtime_extern(self, name: str) -> ir.Function:
        """Return the ir.Function for a named host-runtime extern, materialising
        it on first reference (lazy registration).

        - Checks a private runtime cache first (covers the common second-reference case).
        - Creates from the factory registry on the first true reference, then
          caches the result outside the Pascal symbol table so runtime names
          cannot collide with predeclared identifiers such as ABORT.
        - Unknown extern names fail clearly instead of silently inventing a
          second declaration path.
        """
        cached = self._runtime_extern_cache.get(name)
        if cached is not None:
            return cached
        try:
            factory = self._extern_factories[name]
        except KeyError as exc:
            raise CodegenError(f"unknown runtime extern '{name}'") from exc
        fn = factory()
        self._runtime_extern_cache[name] = fn
        return fn

    def file_fcb_type(self) -> ir.Type:
        """The file-control-block layout: [i32 element-size, i32 structure,
        i32 touched, i32 mode/eof, i8* buffer, i8* handle, i8* bound name,
        i32 FILEMODES user mode, i8 TRAP, i32 ERRS].  TRAP/ERRS are the
        manual ch.12 trapped-I/O fields; the i8 TRAP slot matches this
        compiler's one-byte BOOLEAN and C's `unsigned char trap` (natural
        alignment pads identically on both sides)."""
        if not hasattr(self, '_fcb_ty'):
            i32 = ir.IntType(32)
            self._fcb_ty = ir.LiteralStructType([i32, i32, i32, i32, ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer(), i32, ir.IntType(8), i32])
        return self._fcb_ty

    def _scalar_size(self, name: str) -> int:
        """Size in bytes of a scalar/built-in type, by name."""
        return _SCALAR_SIZES.get(name.upper(), 4)

    def unique_name(self, prefix: str) -> str:
        """Generate a unique name."""
        if not hasattr(self, '_name_counter'):
            self._name_counter = {}
        if prefix not in self._name_counter:
            self._name_counter[prefix] = 0
        self._name_counter[prefix] += 1
        return f'{prefix}_{self._name_counter[prefix]}'
