"""
RUNTIME_BUILTINS mixin for Codegen.

C runtime wrappers and memory builtins

Part of Plan 1 refactoring (mixin-based architecture).
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple, Union

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder

from ast_nodes import *
from codegen.base import CodegenError


class RuntimeBuiltinsMixin:
    """Mixin for runtime_builtins functionality."""

    def memcpy_func(self) -> ir.Function:
        for func in self.module.functions:
            if func.name == 'memcpy':
                return func
        memcpy_type = ir.FunctionType(ir.PointerType(ir.IntType(8)), [ir.PointerType(ir.IntType(8)), ir.PointerType(ir.IntType(8)), ir.IntType(64)])
        return ir.Function(self.module, memcpy_type, name='memcpy')

    def memset_func(self) -> ir.Function:
        for func in self.module.functions:
            if func.name == 'memset':
                return func
        memset_type = ir.FunctionType(ir.PointerType(ir.IntType(8)), [ir.PointerType(ir.IntType(8)), ir.IntType(32), ir.IntType(64)])
        return ir.Function(self.module, memset_type, name='memset')

    def runtime_error_func(self) -> ir.Function:
        """Declare or fetch a runtime error handler (calls abort)."""
        for func in self.module.functions:
            if func.name == 'abort':
                return func
        # abort() takes no arguments and returns never (noreturn), but we declare void
        abort_type = ir.FunctionType(ir.VoidType(), [])
        return ir.Function(self.module, abort_type, name='abort')

    def pascal_abort_func(self) -> ir.Function:
        """Declare or fetch the ABORT runtime: void pabort(i8* msg, i32 len, i16 code, i16 status)."""
        for func in self.module.functions:
            if func.name == 'pabort':
                return func
        fn_type = ir.FunctionType(ir.VoidType(), [ir.PointerType(ir.IntType(8)), ir.IntType(32), ir.IntType(16), ir.IntType(16)])
        fn = ir.Function(self.module, fn_type, name='pabort')
        fn.linkage = 'external'
        return fn

    def builtin_movel(self, args: List[Expression]) -> None:
        self._runtime_fillmove('MOVEL', args)

    def builtin_mover(self, args: List[Expression]) -> None:
        self._runtime_fillmove('MOVER', args)

    def builtin_movesl(self, args: List[Expression]) -> None:
        self._runtime_fillmove('MOVESL', args)

    def builtin_movesr(self, args: List[Expression]) -> None:
        self._runtime_fillmove('MOVESR', args)

    def builtin_new(self, args: List[Expression]) -> None:
        if len(args) != 1:
            raise CodegenError(f'NEW expects 1 argument, got {len(args)}')
        target = args[0]
        if isinstance(target, Identifier):
            target = Designator(target.name, [])
        ptr_addr = self.resolve_designator_ptr(target)
        sym = self.scope.lookup(target.name) if isinstance(target, (Identifier, Designator)) else None
        if not sym or not isinstance(sym.type_expr, PointerType):
            raise CodegenError('NEW requires a pointer variable')
        pointee = getattr(sym.type_expr, 'target_type', None) or getattr(sym.type_expr, 'base', None)
        alloc_ty = self.llvm_type(pointee)
        # Size the heap block from the pointee's real byte size. The module
        # carries an empty target datalayout, so DataLayout.get_type_alloc_size()
        # silently fell back to a hard-coded 8 here and under-allocated for any
        # pointee larger than 8 bytes (e.g. a multi-field RECORD), corrupting the
        # heap on the first full write. get_type_size() resolves array bounds and
        # record layouts the same way SIZEOF does.
        alloc_size = 0
        try:
            alloc_size = self.get_type_size(self.resolve_type_alias(pointee))
        except Exception:
            alloc_size = 0
        if alloc_size <= 0 and getattr(self.module, 'data_layout', None):
            try:
                alloc_size = self.module.data_layout.get_type_alloc_size(alloc_ty)
            except Exception:
                alloc_size = 0
        if alloc_size <= 0:
            alloc_size = 8
        raw = self.builder.call(self.scope.lookup('malloc').llvm_value, [ir.Constant(ir.IntType(64), alloc_size)])
        casted = self.builder.bitcast(raw, self.llvm_type(sym.type_expr))
        self.builder.store(casted, ptr_addr)

    def builtin_dispose(self, args: List[Expression]) -> None:
        if len(args) != 1:
            raise CodegenError(f'DISPOSE expects 1 argument, got {len(args)}')
        target = args[0]
        if isinstance(target, Identifier):
            target = Designator(target.name, [])
        ptr_addr = self.resolve_designator_ptr(target)
        sym = self.scope.lookup(target.name) if isinstance(target, (Identifier, Designator)) else None
        if not sym or not isinstance(sym.type_expr, PointerType):
            raise CodegenError('DISPOSE requires a pointer variable')
        ptr_val = self.builder.load(ptr_addr)
        raw = self.builder.bitcast(ptr_val, ir.IntType(8).as_pointer())
        self.builder.call(self.scope.lookup('free').llvm_value, [raw])
        self.builder.store(ir.Constant(self.llvm_type(sym.type_expr), None), ptr_addr)

    def _file_helper(self, name: str) -> ir.Function:
        fn = self.scope.lookup(name)
        if not fn:
            raise CodegenError(f'Undefined runtime helper: {name}')
        return fn.llvm_value

    def builtin_reset(self, args: List[Expression]) -> None:
        if len(args) != 1:
            raise CodegenError(f'RESET expects 1 argument, got {len(args)}')
        target = args[0] if isinstance(args[0], Designator) else Designator(args[0].name, [])
        ptr = self.resolve_designator_ptr(target)
        self.builder.call(self._file_helper('pas_file_reset'), [self.builder.bitcast(ptr, self.file_fcb_type().as_pointer())])

    def builtin_rewrite(self, args: List[Expression]) -> None:
        if len(args) != 1:
            raise CodegenError(f'REWRITE expects 1 argument, got {len(args)}')
        target = args[0] if isinstance(args[0], Designator) else Designator(args[0].name, [])
        ptr = self.resolve_designator_ptr(target)
        self.builder.call(self._file_helper('pas_file_rewrite'), [self.builder.bitcast(ptr, self.file_fcb_type().as_pointer())])

    def builtin_get(self, args: List[Expression]) -> None:
        if len(args) != 1:
            raise CodegenError(f'GET expects 1 argument, got {len(args)}')
        target = args[0] if isinstance(args[0], Designator) else Designator(args[0].name, [])
        ptr = self.resolve_designator_ptr(target)
        self.builder.call(self._file_helper('pas_file_get'), [self.builder.bitcast(ptr, self.file_fcb_type().as_pointer())])

    def builtin_put(self, args: List[Expression]) -> None:
        if len(args) != 1:
            raise CodegenError(f'PUT expects 1 argument, got {len(args)}')
        target = args[0] if isinstance(args[0], Designator) else Designator(args[0].name, [])
        ptr = self.resolve_designator_ptr(target)
        self.builder.call(self._file_helper('pas_file_put'), [self.builder.bitcast(ptr, self.file_fcb_type().as_pointer())])

    def builtin_abort(self, args: List[Expression]) -> None:
        # ABORT(CONST STRING, WORD, WORD): surface the message, error code, and
        # STATUS word through the runtime rather than dropping them (manual:
        # stops execution like an internal runtime error).
        chars, length = self.get_string_chars_and_len(args[0])
        code = self._coerce_to_word(self.codegen_expr(args[1]))
        status = self._coerce_to_word(self.codegen_expr(args[2]))
        self.builder.call(self.pascal_abort_func(), [chars, length, code, status])
        self.builder.unreachable()

    def builtin_pack(self, args: List[Expression]) -> None:
        """PACK(CONST A: unpacked-array; I: index; VAR Z: packed-array)

        Semantics (manual / ISO): for j := low(Z) to high(Z),
        Z[j] := A[I + (j - low(Z))]. Storage for both arrays is 0-based
        ([high-low+1 x elem]), so every Pascal index is translated to a slot
        by subtracting that array's lower bound.
        """
        a_arg, i_arg, z_arg = args[0], args[1], args[2]

        a_ptr = self.codegen_expr(a_arg)
        z_ptr = self.codegen_expr(z_arg)
        i_val = self.codegen_expr(i_arg)

        a_low = self._designator_array_low(a_arg)
        z_low, z_high = self._designator_array_bounds(z_arg)

        j_var = self.builder.alloca(ir.IntType(32), name='pack_j')
        self.builder.store(ir.Constant(ir.IntType(32), z_low), j_var)

        loop_block = self.current_function.append_basic_block(name='pack_loop')
        body_block = self.current_function.append_basic_block(name='pack_body')
        end_block = self.current_function.append_basic_block(name='pack_end')

        self.builder.branch(loop_block)
        self.builder.position_at_end(loop_block)

        j_val = self.builder.load(j_var)
        cond = self.builder.icmp_signed('<=', j_val, ir.Constant(ir.IntType(32), z_high))
        self.builder.cbranch(cond, body_block, end_block)

        self.builder.position_at_end(body_block)

        # offset = j - low(Z): 0-based position, which is also Z's storage slot.
        offset = self.builder.sub(j_val, ir.Constant(ir.IntType(32), z_low))
        # A storage slot = (I + offset) - low(A).
        a_pascal = self.builder.add(offset, i_val)
        a_slot = self.builder.sub(a_pascal, ir.Constant(ir.IntType(32), a_low))

        a_elem_ptr = self.builder.gep(a_ptr, [ir.Constant(ir.IntType(32), 0), a_slot])
        elem_val = self.builder.load(a_elem_ptr)

        z_elem_ptr = self.builder.gep(z_ptr, [ir.Constant(ir.IntType(32), 0), offset])
        self.builder.store(elem_val, z_elem_ptr)

        next_j = self.builder.add(j_val, ir.Constant(ir.IntType(32), 1))
        self.builder.store(next_j, j_var)
        self.builder.branch(loop_block)

        self.builder.position_at_end(end_block)

    def builtin_unpack(self, args: List[Expression]) -> None:
        """UNPACK(CONST Z: packed-array; VAR A: unpacked-array; I: index)

        Semantics (manual / ISO): for j := low(Z) to high(Z),
        A[I + (j - low(Z))] := Z[j]. As in PACK, every Pascal index is
        translated to a 0-based storage slot.
        """
        z_arg, a_arg, i_arg = args[0], args[1], args[2]

        z_ptr = self.codegen_expr(z_arg)
        a_ptr = self.codegen_expr(a_arg)
        i_val = self.codegen_expr(i_arg)

        a_low = self._designator_array_low(a_arg)
        z_low, z_high = self._designator_array_bounds(z_arg)

        j_var = self.builder.alloca(ir.IntType(32), name='unpack_j')
        self.builder.store(ir.Constant(ir.IntType(32), z_low), j_var)

        loop_block = self.current_function.append_basic_block(name='unpack_loop')
        body_block = self.current_function.append_basic_block(name='unpack_body')
        end_block = self.current_function.append_basic_block(name='unpack_end')

        self.builder.branch(loop_block)
        self.builder.position_at_end(loop_block)

        j_val = self.builder.load(j_var)
        cond = self.builder.icmp_signed('<=', j_val, ir.Constant(ir.IntType(32), z_high))
        self.builder.cbranch(cond, body_block, end_block)

        self.builder.position_at_end(body_block)

        offset = self.builder.sub(j_val, ir.Constant(ir.IntType(32), z_low))
        a_pascal = self.builder.add(offset, i_val)
        a_slot = self.builder.sub(a_pascal, ir.Constant(ir.IntType(32), a_low))

        z_elem_ptr = self.builder.gep(z_ptr, [ir.Constant(ir.IntType(32), 0), offset])
        elem_val = self.builder.load(z_elem_ptr)

        a_elem_ptr = self.builder.gep(a_ptr, [ir.Constant(ir.IntType(32), 0), a_slot])
        self.builder.store(elem_val, a_elem_ptr)

        next_j = self.builder.add(j_val, ir.Constant(ir.IntType(32), 1))
        self.builder.store(next_j, j_var)
        self.builder.branch(loop_block)

        self.builder.position_at_end(end_block)

    def _runtime_fillmove(self, name: str, args: List[Expression]) -> None:
        src = self.codegen_expr(args[0])
        dst = self.codegen_expr(args[1])
        length = self.codegen_expr(args[2])
        fn = self.scope.lookup(name)
        if not fn:
            raise CodegenError(f'Undefined procedure: {name}')
        self.builder.call(fn.llvm_value, [src, dst, length])

    def _coerce_to_word(self, val: ir.Value) -> ir.Value:
        """Coerce an integer value to i16 (WORD) for a runtime call."""
        if isinstance(val.type, ir.IntType):
            if val.type.width > 16:
                return self.builder.trunc(val, ir.IntType(16))
            if val.type.width < 16:
                return self.builder.zext(val, ir.IntType(16))
        return val
