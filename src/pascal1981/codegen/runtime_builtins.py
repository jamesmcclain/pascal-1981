"""
RUNTIME_BUILTINS mixin for Codegen.

C runtime wrappers and memory builtins

"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple, Union

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder

from ..ast_nodes import *
from .base import CodegenError


class RuntimeBuiltinsMixin:
    """Mixin for runtime_builtins functionality."""

    def memcpy_func(self) -> ir.Function:
        return self.runtime_extern('memcpy')

    def memset_func(self) -> ir.Function:
        return self.runtime_extern('memset')

    def runtime_error_func(self) -> ir.Function:
        """Declare or fetch a runtime error handler (calls abort)."""
        return self.runtime_extern('abort')

    def emit_runtime_abort(self) -> None:
        """Flush host stdio, then call the runtime error handler.

        Vintage abort-path probes preserve output printed before the trap.  The
        modern backend uses libc abort() for generated runtime checks, so flush
        all C streams first to avoid losing buffered stdout on captured runs.
        """
        fflush = self.runtime_extern('fflush')
        self.builder.call(fflush, [ir.Constant(ir.IntType(8).as_pointer(), None)])
        self.builder.call(self.runtime_error_func(), [])

    def pascal_abort_func(self) -> ir.Function:
        """Declare or fetch the ABORT runtime: void pabort(i8* msg, i32 len, i16 code, i16 status)."""
        return self.runtime_extern('pabort')

    def builtin_fillc(self, args: List[Expression]) -> None:
        self._runtime_fillmove('FILLC', args)

    def builtin_fillsc(self, args: List[Expression]) -> None:
        self._runtime_fillmove('FILLSC', args)

    def builtin_movel(self, args: List[Expression]) -> None:
        self._runtime_fillmove('MOVEL', args)

    def builtin_mover(self, args: List[Expression]) -> None:
        self._runtime_fillmove('MOVER', args)

    def builtin_movesl(self, args: List[Expression]) -> None:
        self._runtime_fillmove('MOVESL', args)

    def builtin_movesr(self, args: List[Expression]) -> None:
        self._runtime_fillmove('MOVESR', args)

    def builtin_new(self, args: List[Expression]) -> None:
        if len(args) < 1:
            raise CodegenError(f'NEW expects at least 1 argument, got {len(args)}')
        target = args[0]
        if isinstance(target, Identifier):
            target = Designator(target.name, [])
        ptr_addr = self.resolve_designator_ptr(target)
        sym = self.scope.lookup(target.name) if isinstance(target, (Identifier, Designator)) else None
        # Unwrap named aliases (VAR p: np; TYPE np = ^node) so a pointer
        # declared via a type alias is accepted, not just an inline ^T.
        ptr_type = self.resolve_type_alias(sym.type_expr) if sym else None
        if not sym or not isinstance(ptr_type, PointerType):
            raise CodegenError('NEW requires a pointer variable')
        pointee = getattr(ptr_type, 'target_type', None) or getattr(ptr_type, 'base', None)
        resolved_pointee = self.resolve_type_alias(pointee)
        alloc_ty = self.llvm_type(pointee)

        if len(args) > 1:
            if not (isinstance(resolved_pointee, ArrayType) and getattr(resolved_pointee, 'super', False)):
                raise CodegenError(f'NEW expects 1 argument, got {len(args)}')
            if len(args) != 2:
                raise CodegenError(f'NEW super array allocation expects 1 upper bound, got {len(args) - 1}')
            lower = self.eval_const_expr(resolved_pointee.index_range.low)
            elem_size = self.get_type_size(resolved_pointee.element_type)
            upper_val = self.codegen_expr(args[1])
            if isinstance(upper_val.type, ir.IntType) and upper_val.type.width < 64:
                upper64 = self.builder.sext(upper_val, ir.IntType(64))
            elif isinstance(upper_val.type, ir.IntType) and upper_val.type.width > 64:
                upper64 = self.builder.trunc(upper_val, ir.IntType(64))
            else:
                upper64 = upper_val
            count = self.builder.sub(upper64, ir.Constant(ir.IntType(64), lower - 1))
            alloc_size = self.builder.mul(count, ir.Constant(ir.IntType(64), elem_size))
        else:
            # Size the heap block from the pointee's real byte size. The module
            # carries an empty target datalayout, so DataLayout.get_type_alloc_size()
            # silently fell back to a hard-coded 8 here and under-allocated for any
            # pointee larger than 8 bytes (e.g. a multi-field RECORD), corrupting the
            # heap on the first full write. get_type_size() resolves array bounds and
            # record layouts the same way SIZEOF does.
            alloc_size_int = 0
            try:
                alloc_size_int = self.get_type_size(resolved_pointee)
            except Exception:
                alloc_size_int = 0
            if alloc_size_int <= 0 and getattr(self.module, 'data_layout', None):
                try:
                    alloc_size_int = self.module.data_layout.get_type_alloc_size(alloc_ty)
                except Exception:
                    alloc_size_int = 0
            if alloc_size_int <= 0:
                alloc_size_int = 8
            alloc_size = ir.Constant(ir.IntType(64), alloc_size_int)
        raw = self.builder.call(self.runtime_extern('malloc'), [alloc_size])
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
        ptr_type = self.resolve_type_alias(sym.type_expr) if sym else None
        if not sym or not isinstance(ptr_type, PointerType):
            raise CodegenError('DISPOSE requires a pointer variable')
        ptr_val = self.builder.load(ptr_addr)
        raw = self.builder.bitcast(ptr_val, ir.IntType(8).as_pointer())
        self.builder.call(self.runtime_extern('free'), [raw])
        self.builder.store(ir.Constant(self.llvm_type(sym.type_expr), None), ptr_addr)

    def _file_helper(self, name: str) -> ir.Function:
        return self.runtime_extern(name)

    def _builtin_file_op(self, pas_name: str, helper_name: str, args: List[Expression]) -> None:
        if len(args) != 1:
            raise CodegenError(f'{pas_name} expects 1 argument, got {len(args)}')
        target = args[0] if isinstance(args[0], Designator) else Designator(args[0].name, [])
        ptr = self.resolve_designator_ptr(target)
        handle = self.builder.load(ptr)
        fcb_ptr = self.builder.bitcast(handle, self.file_fcb_type().as_pointer())
        if getattr(target, 'name', '').upper() in {'INPUT', 'OUTPUT'}:
            in_sym = self.scope.lookup('INPUT')
            out_sym = self.scope.lookup('OUTPUT')
            in_fcb = self.builder.bitcast(self.builder.load(in_sym.llvm_value), self.file_fcb_type().as_pointer())
            out_fcb = self.builder.bitcast(self.builder.load(out_sym.llvm_value), self.file_fcb_type().as_pointer())
            self.builder.call(self._file_helper('pas_file_attach_std'), [in_fcb, out_fcb])
        self.builder.call(self._file_helper(helper_name), [fcb_ptr])

    def builtin_reset(self, args: List[Expression]) -> None:
        self._builtin_file_op('RESET', 'pas_file_reset', args)

    def builtin_rewrite(self, args: List[Expression]) -> None:
        self._builtin_file_op('REWRITE', 'pas_file_rewrite', args)

    def builtin_get(self, args: List[Expression]) -> None:
        self._builtin_file_op('GET', 'pas_file_get', args)

    def builtin_put(self, args: List[Expression]) -> None:
        self._builtin_file_op('PUT', 'pas_file_put', args)

    def builtin_close(self, args: List[Expression]) -> None:
        self._builtin_file_op('CLOSE', 'pas_file_close', args)

    def builtin_discard(self, args: List[Expression]) -> None:
        self._builtin_file_op('DISCARD', 'pas_file_discard', args)

    def builtin_assign(self, args: List[Expression]) -> None:
        if len(args) != 2:
            raise CodegenError(f'ASSIGN expects 2 arguments, got {len(args)}')
        target = args[0] if isinstance(args[0], Designator) else Designator(args[0].name, [])
        ptr = self.resolve_designator_ptr(target)
        handle = self.builder.load(ptr)
        fcb_ptr = self.builder.bitcast(handle, self.file_fcb_type().as_pointer())
        name_arg = args[1]
        try:
            chars, length = self.get_string_chars_and_len(name_arg)
        except Exception:
            # ASSIGN accepts a single CHAR too; in particular ASSIGN(F, CHR(0))
            # requests an anonymous temporary file per the manual.
            val = self.codegen_expr(name_arg)
            if val.type != ir.IntType(8):
                raise
            tmp = self.builder.alloca(ir.IntType(8), name='assign_char')
            self.builder.store(val, tmp)
            chars, length = tmp, ir.Constant(ir.IntType(32), 1)
        self.builder.call(self._file_helper('pas_file_assign'), [fcb_ptr, chars, length])

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
        i_val = self.coerce_printf_int(self.codegen_expr(i_arg))

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
        i_val = self.coerce_printf_int(self.codegen_expr(i_arg))

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
        fn = self.runtime_extern(name.lower())
        values = [self.codegen_expr(arg) for arg in args]
        coerced = [self.coerce_arg(value, target) for value, target in zip(values, fn.function_type.args)]
        self.builder.call(fn, coerced)

    def _as_i8_space_ptr(self, ptr: ir.Value) -> ir.Value:
        """Reinterpret a (possibly addrspace-qualified) pointer as i8* in the
        same address space, for byte-granular block copy/fill."""
        addrspace = getattr(ptr.type, 'addrspace', 0)
        want = ir.IntType(8).as_pointer(addrspace)
        if ptr.type != want:
            ptr = self.builder.bitcast(ptr, want)
        return ptr

    def _to_i64(self, val: ir.Value) -> ir.Value:
        i64 = ir.IntType(64)
        if isinstance(val.type, ir.IntType):
            if val.type.width < 64:
                return self.builder.zext(val, i64)
            if val.type.width > 64:
                return self.builder.trunc(val, i64)
        return val

    def _device_seg_bridge(self, name: str, args: List[Expression]) -> None:
        """Lower FILLSC/MOVESL/MOVESR inside a DEVICE MODULE (design S5.4).

        Emits an explicit byte loop that loads from the source address space and
        stores to the destination address space -- the one sanctioned cross-space
        bridge.  On the CPU device (device=x86) every space collapses to
        addrspace 0, so the loop is an ordinary, runnable byte copy/fill; on a GPU
        triple the loads/stores carry the operands' addrspace(k) and are emitted
        for the device backend (not executed on this host).

        MOVESL copies forward (low->high), MOVESR backward (high->low); FILLSC
        writes a constant byte.  Both moves are cross-space and never overlap.
        """
        i8 = ir.IntType(8)
        i64 = ir.IntType(64)
        if name == 'FILLSC':
            dst = self._as_i8_space_ptr(self.codegen_expr(args[0]))
            length = self._to_i64(self.codegen_expr(args[1]))
            fill = self.codegen_expr(args[2])
            if isinstance(fill.type, ir.IntType) and fill.type.width != 8:
                fill = (self.builder.trunc(fill, i8) if fill.type.width > 8 else self.builder.zext(fill, i8))
            src = None
            reverse = False
        else:
            src = self._as_i8_space_ptr(self.codegen_expr(args[0]))
            dst = self._as_i8_space_ptr(self.codegen_expr(args[1]))
            length = self._to_i64(self.codegen_expr(args[2]))
            fill = None
            reverse = (name == 'MOVESR')

        zero = ir.Constant(i64, 0)
        one = ir.Constant(i64, 1)
        nm1 = self.builder.sub(length, one) if reverse else None

        parent = self.builder.block.parent
        cond_bb = parent.append_basic_block(name.lower() + '_cond')
        body_bb = parent.append_basic_block(name.lower() + '_body')
        end_bb = parent.append_basic_block(name.lower() + '_end')

        entry_bb = self.builder.block
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        idx = self.builder.phi(i64, name='i')
        idx.add_incoming(zero, entry_bb)
        cond = self.builder.icmp_unsigned('<', idx, length)
        self.builder.cbranch(cond, body_bb, end_bb)

        self.builder.position_at_end(body_bb)
        off = self.builder.sub(nm1, idx) if reverse else idx
        if name == 'FILLSC':
            self.builder.store(fill, self.builder.gep(dst, [off]))
        else:
            byte = self.builder.load(self.builder.gep(src, [off]))
            self.builder.store(byte, self.builder.gep(dst, [off]))
        nxt = self.builder.add(idx, one)
        idx.add_incoming(nxt, body_bb)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(end_bb)

    def _coerce_to_word(self, val: ir.Value) -> ir.Value:
        """Coerce an integer value to i16 (WORD) for a runtime call."""
        if isinstance(val.type, ir.IntType):
            if val.type.width > 16:
                return self.builder.trunc(val, ir.IntType(16))
            if val.type.width < 16:
                return self.builder.zext(val, ir.IntType(16))
        return val
