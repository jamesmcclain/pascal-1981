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

from ast_nodes import *


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


_SCALAR_SIZES = {
    'INTEGER': 4,
    'REAL': 8,
    'WORD': 2,
    'CHAR': 1,
    'BOOLEAN': 1,  # vintage Pascal BOOLEAN is one byte
    'ADRMEM': 8,  # 64-bit pointer
}


class CodegenBase:
    """Base infrastructure for the LLVM IR code generator.
    
    Initializes module state, builder, scope, and shared constants/tables.
    """

    def __init__(self, verbose: bool = False, source_file: Optional[str] = None, force_rangeck: Optional[bool] = None):
        self.module = ir.Module(name="pascal_program")
        self.source_file = source_file
        self.module.triple = "x86_64-pc-linux-gnu"  # Standard Linux target
        self.builder: Optional[IRBuilder] = None
        self.scope = Scope()  # global scope
        self.current_function: Optional[ir.Function] = None
        self.current_return_block: Optional[ir.BasicBlock] = None
        # Compile-time constants keyed UPPER.  Values are int for INTEGER/BOOL/CHAR
        # constants, or float for REAL constants.  Use _const_ir() to emit the
        # appropriate LLVM constant at reference sites.
        self.constants: Dict[str, object] = {
            'MAXINT': 2147483647,
            'MAXWORD': 65535,
        }
        self.type_aliases: Dict[str, Type] = {}  # compile-time type aliases, keyed UPPER
        self.current_interface_decls: Dict[str, Declaration] = {}
        self.proc_param_modes: Dict[str, List[Optional[str]]] = {}
        self.loop_stack: List[LoopContext] = []
        # Enum support (checklist 9.8): map each enum member (UPPER) to the full
        # ordered member-name list of its enum so WRITE can print the symbolic
        # name of a bare member literal; cache the per-enum `[n x i8*]` name
        # tables so each enum emits its name strings only once.
        self.enum_member_names: Dict[str, List[str]] = {}
        self._enum_name_tables: Dict[str, ir.GlobalVariable] = {}
        self.verbose = verbose
        self.force_rangeck = force_rangeck
        self._register_predeclared_externs()
        self._register_predeclared_files()

    def _log(self, msg: str) -> None:
        """Emit a diagnostic line to stderr when verbose mode is on."""
        if self.verbose:
            import sys
            print(f'[codegen] {msg}', file=sys.stderr)

    def _register_predeclared_files(self) -> None:
        """Declare INPUT and OUTPUT as real predeclared TEXT file handles."""
        text_type = FileType(NamedType('CHAR', None), structure='ASCII')
        for name in ('INPUT', 'OUTPUT'):
            gv = ir.GlobalVariable(self.module, ir.IntType(8).as_pointer(), name=name.lower())
            gv.initializer = ir.Constant(ir.IntType(8).as_pointer(), None)
            self.scope.define(name, gv, text_type)

    def _register_predeclared_externs(self) -> None:
        """Predeclare runtime externs that behave like builtins.

        The flat variants (fillc/movel/mover) take ADRMEM (i8*) addresses; the
        segmented variants (fillsc/movesl/movesr) take ADSMEM addresses, modeled
        as a {flat pointer, segment word} pair to match ADS pointers."""
        ads_ty = ir.LiteralStructType([ir.IntType(8).as_pointer(), ir.IntType(16)])
        fill_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer(), ir.IntType(16), ir.IntType(8)])
        seg_fill_ty = ir.FunctionType(ir.IntType(32), [ads_ty, ir.IntType(16), ir.IntType(8)])
        fillc = ir.Function(self.module, fill_ty, name='fillc')
        fillc.linkage = 'external'
        self.scope.define('fillc', fillc, None)
        fillsc = ir.Function(self.module, seg_fill_ty, name='fillsc')
        fillsc.linkage = 'external'
        self.scope.define('fillsc', fillsc, None)
        mov_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer(), ir.IntType(16)])
        seg_mov_ty = ir.FunctionType(ir.IntType(32), [ads_ty, ads_ty, ir.IntType(16)])
        movel = ir.Function(self.module, mov_ty, name='movel')
        movel.linkage = 'external'
        self.scope.define('movel', movel, None)
        mover = ir.Function(self.module, mov_ty, name='mover')
        mover.linkage = 'external'
        self.scope.define('mover', mover, None)
        movesl = ir.Function(self.module, seg_mov_ty, name='movesl')
        movesl.linkage = 'external'
        self.scope.define('movesl', movesl, None)
        movesr = ir.Function(self.module, seg_mov_ty, name='movesr')
        movesr.linkage = 'external'
        self.scope.define('movesr', movesr, None)
        memmove_ty = ir.FunctionType(ir.VoidType(), [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer(), ir.IntType(64)])
        memmove_fn = ir.Function(self.module, memmove_ty, name='memmove')
        memmove_fn.linkage = 'external'
        self.scope.define('memmove', memmove_fn, None)
        read_int_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(32).as_pointer()])
        read_word_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(16).as_pointer()])
        read_real_ty = ir.FunctionType(ir.IntType(32), [ir.DoubleType().as_pointer()])
        read_char_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer()])
        read_lstr_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer(), ir.IntType(32)])
        read_skip_ty = ir.FunctionType(ir.VoidType(), [])
        for name, ty in [('pas_read_int', read_int_ty), ('pas_read_word', read_word_ty), ('pas_read_real', read_real_ty), ('pas_read_char', read_char_ty),
                         ('pas_read_lstring', read_lstr_ty), ('pas_readln_skip', read_skip_ty)]:
            fn = ir.Function(self.module, ty, name=name)
            fn.linkage = 'external'
            self.scope.define(name, fn, None)
        positn_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer(), ir.IntType(32), ir.IntType(8).as_pointer(), ir.IntType(32)])
        positn_fn = ir.Function(self.module, positn_ty, name='positn')
        positn_fn.linkage = 'external'
        self.scope.define('positn', positn_fn, None)
        scaneq_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(32), ir.IntType(8), ir.IntType(8).as_pointer(), ir.IntType(32), ir.IntType(32), ir.IntType(32)])
        scaneq_fn = ir.Function(self.module, scaneq_ty, name='scaneq')
        scaneq_fn.linkage = 'external'
        self.scope.define('scaneq', scaneq_fn, None)
        scanne_fn = ir.Function(self.module, scaneq_ty, name='scanne')
        scanne_fn.linkage = 'external'
        self.scope.define('scanne', scanne_fn, None)
        encode_bool_ty = ir.FunctionType(
            ir.IntType(32),
            [ir.IntType(8).as_pointer(), ir.IntType(32),
             ir.IntType(8).as_pointer(), ir.IntType(32),
             ir.IntType(32), ir.IntType(32), ir.IntType(32)])
        encode_fn = ir.Function(self.module, encode_bool_ty, name='encode_value')
        encode_fn.linkage = 'external'
        self.scope.define('encode_value', encode_fn, None)
        decode_fn = ir.Function(self.module, encode_bool_ty, name='decode_value')
        decode_fn.linkage = 'external'
        self.scope.define('decode_value', decode_fn, None)
        malloc_ty = ir.FunctionType(ir.IntType(8).as_pointer(), [ir.IntType(64)])
        free_ty = ir.FunctionType(ir.VoidType(), [ir.IntType(8).as_pointer()])
        malloc_fn = ir.Function(self.module, malloc_ty, name='malloc')
        malloc_fn.linkage = 'external'
        self.scope.define('malloc', malloc_fn, None)
        free_fn = ir.Function(self.module, free_ty, name='free')
        free_fn.linkage = 'external'
        self.scope.define('free', free_fn, None)
        # File-control block, one fixed layout for every file type:
        #   {i32 element-size, i32 structure (0=binary FILE OF T, 1=ASCII/TEXT),
        #    i32 touched (buffer-accessed flag), i32 mode/eof bookkeeping,
        #    i8* current-component buffer, i8* runtime handle, i8* bound name}.
        fcb_ty = self.file_fcb_type()
        fcb_ptr = fcb_ty.as_pointer()
        file_buffer_ty = ir.FunctionType(ir.IntType(8).as_pointer(), [fcb_ptr])
        file_touch_ty = ir.FunctionType(ir.VoidType(), [fcb_ptr])
        i32 = ir.IntType(32)
        file_buffer = ir.Function(self.module, file_buffer_ty, name='pas_file_buffer')
        file_buffer.linkage = 'external'
        self.scope.define('pas_file_buffer', file_buffer, None)
        file_touch = ir.Function(self.module, file_touch_ty, name='pas_file_touch_buffer')
        file_touch.linkage = 'external'
        self.scope.define('pas_file_touch_buffer', file_touch, None)
        for name, ty in [('pas_file_reset', ir.FunctionType(ir.VoidType(), [fcb_ptr])), ('pas_file_rewrite', ir.FunctionType(ir.VoidType(), [fcb_ptr])), ('pas_file_get', ir.FunctionType(ir.VoidType(), [fcb_ptr])), ('pas_file_put', ir.FunctionType(ir.VoidType(), [fcb_ptr])), ('pas_file_close', ir.FunctionType(ir.VoidType(), [fcb_ptr])), ('pas_file_discard', ir.FunctionType(ir.VoidType(), [fcb_ptr])), ('pas_file_assign', ir.FunctionType(ir.VoidType(), [fcb_ptr, ir.IntType(8).as_pointer(), ir.IntType(32)])), ('pas_file_attach_std', ir.FunctionType(ir.VoidType(), [fcb_ptr, fcb_ptr])), ('pas_file_eof', ir.FunctionType(ir.IntType(32), [fcb_ptr])), ('pas_file_eoln', ir.FunctionType(ir.IntType(32), [fcb_ptr]))]:
            fn = ir.Function(self.module, ty, name=name)
            fn.linkage = 'external'
            self.scope.define(name, fn, None)
        write_fmt = ir.Function(self.module, ir.FunctionType(ir.IntType(32), [fcb_ptr, ir.IntType(8).as_pointer()], var_arg=True), name='pas_write_fmt')
        write_fmt.linkage = 'external'
        self.scope.define('pas_write_fmt', write_fmt, None)
        for name, ptr_ty in [('pas_fread_int', ir.IntType(32).as_pointer()), ('pas_fread_word', ir.IntType(16).as_pointer()), ('pas_fread_real', ir.DoubleType().as_pointer()), ('pas_fread_char', ir.IntType(8).as_pointer())]:
            fn = ir.Function(self.module, ir.FunctionType(ir.IntType(32), [fcb_ptr, ptr_ty]), name=name)
            fn.linkage = 'external'
            self.scope.define(name, fn, None)
        fread_lstr = ir.Function(self.module, ir.FunctionType(ir.IntType(32), [fcb_ptr, ir.IntType(8).as_pointer(), ir.IntType(32)]), name='pas_fread_lstring')
        fread_lstr.linkage = 'external'
        self.scope.define('pas_fread_lstring', fread_lstr, None)
        fread_skip = ir.Function(self.module, ir.FunctionType(ir.VoidType(), [fcb_ptr]), name='pas_freadln_skip')
        fread_skip.linkage = 'external'
        self.scope.define('pas_freadln_skip', fread_skip, None)

    def file_fcb_type(self) -> ir.Type:
        """The file-control-block layout: [i32 element-size, i32 structure,
        i32 touched, i32 mode/eof, i8* buffer, i8* handle, i8* bound name]."""
        if not hasattr(self, '_fcb_ty'):
            i32 = ir.IntType(32)
            self._fcb_ty = ir.LiteralStructType([i32, i32, i32, i32, ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer()])
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
