"""
STRINGS mixin for Codegen.

String operations

"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple, Union

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder

from ..ast_nodes import *
from ..type_system import LStringType as ResolvedLStringType
from ..type_system import StringType as ResolvedStringType
from .base import CodegenError


class StringsMixin:
    """Mixin for strings functionality."""

    def get_string_chars_and_len(self, expr: Expression) -> tuple[ir.Value, ir.Value]:
        """Returns (chars_ptr: ir.Value, length: ir.Value) for any string expression.
        
        The chars_ptr points directly to the first character.
        The length is an i32 representing the dynamic or static length.
        """
        if isinstance(expr, StringLiteral):
            val_str = expr.value
            if val_str.startswith("'") and val_str.endswith("'"):
                val_str = val_str[1:-1]
            val_str = val_str.replace("''", "'")
            lit_len = len(val_str)

            chars_ptr = self.codegen_expr(expr)
            length = ir.Constant(ir.IntType(32), lit_len)
            return chars_ptr, length

        elif isinstance(expr, NilLiteral) or (isinstance(expr, Identifier) and expr.name.upper() == 'NULL'):
            chars_ptr = self.null_lstring_ptr()
            length = ir.Constant(ir.IntType(32), 0)
            return chars_ptr, length

        # val is now a pointer to the inline aggregate [n+1 x i8] or [n x i8]
        val = self.codegen_expr(expr)

        # Determine string type details
        t = None
        if isinstance(expr, Identifier):
            symbol = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
            if symbol:
                t = symbol.type_expr
        elif isinstance(expr, Designator):
            symbol = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
            if symbol:
                t = symbol.type_expr

        is_str, max_len, is_lstring = self.get_string_type_info(t)

        zero = ir.Constant(ir.IntType(32), 0)
        one = ir.Constant(ir.IntType(32), 1)

        if is_lstring:
            # LSTRING [n+1 x i8]: byte [0] = length, bytes [1..n] = chars
            len_ptr = self.builder.gep(val, [zero, zero])
            len_byte = self.builder.load(len_ptr)
            length = self.builder.zext(len_byte, ir.IntType(32))
            chars_ptr = self.builder.gep(val, [zero, one])
        else:
            # STRING [n x i8]: bytes [0..n-1] = chars, no length prefix
            chars_ptr = self.builder.gep(val, [zero, zero])
            length = ir.Constant(ir.IntType(32), max_len)

        return chars_ptr, length

    def _dest_string_max_len(self, arg: Expression) -> int:
        """Resolve the declared capacity (max length) of a string destination."""
        t = None
        if isinstance(arg, (Identifier, Designator)):
            symbol = self.scope.lookup(arg.name) or self.scope.lookup(arg.name.upper())
            if symbol:
                t = symbol.type_expr
        _is_str, max_len, _is_lstring = self.get_string_type_info(t)
        return max_len

    def _guard_string_capacity(self, need_len: ir.Value, max_len: int, label: str, enabled: bool = True):
        """Emit the manual's string range check (errors if upper(D) < need_len).

        If disabled, emit no guard blocks and return None so callers can skip
        the continuation branch entirely.
        """
        if not enabled:
            return None
        cond = self.builder.icmp_signed('<=', need_len, ir.Constant(ir.IntType(32), max_len))
        parent = self.builder.block.parent
        ok_block = parent.append_basic_block(label + '_ok')
        err_block = parent.append_basic_block(label + '_overflow')
        end_block = parent.append_basic_block(label + '_end')
        self.builder.cbranch(cond, ok_block, err_block)
        self.builder.position_at_end(err_block)
        self.emit_runtime_abort()
        self.builder.unreachable()
        self.builder.position_at_end(ok_block)
        return end_block

    def builtin_concat(self, args: List[Expression], enabled: bool = True) -> None:
        """CONCAT(VAR D: LSTRING; CONST S: STRING).

        S is appended to D; D's length grows by length(S). Manual 11-20:
        error if upper(D) < length(D) + upper(S).
        """
        D_arg = args[0]
        if isinstance(D_arg, Identifier):
            D_arg = Designator(D_arg.name, [])
        D_ptr = self.resolve_designator_ptr(D_arg)
        # D_ptr is now directly the aggregate pointer [n+1 x i8]

        src_chars, src_len = self.get_string_chars_and_len(args[1])
        src_len_64 = self.builder.zext(src_len, ir.IntType(64))

        zero = ir.Constant(ir.IntType(32), 0)
        one = ir.Constant(ir.IntType(32), 1)

        # Load current length from byte [0]
        len_ptr = self.builder.gep(D_ptr, [zero, zero])
        dest_len_byte = self.builder.load(len_ptr)
        dest_len = self.builder.zext(dest_len_byte, ir.IntType(32))

        # Range check BEFORE writing: length(D) + length(S) must fit in upper(D).
        new_len = self.builder.add(dest_len, src_len)
        max_len = self._dest_string_max_len(args[0])
        end_block = self._guard_string_capacity(new_len, max_len, 'concat', enabled=enabled)

        # Append S at [1 + dest_len ..]
        dest_chars = self.builder.gep(D_ptr, [zero, one])
        append_ptr = self.builder.gep(dest_chars, [dest_len])
        self.builder.call(self.memcpy_func(), [append_ptr, src_chars, src_len_64])

        # Update length byte [0]. LSTRING is length-prefixed (manual 6-18),
        # not null-terminated.
        new_len_byte = self.builder.trunc(new_len, ir.IntType(8))
        self.builder.store(new_len_byte, len_ptr)

        if end_block is not None:
            self.builder.branch(end_block)
            self.builder.position_at_end(end_block)

    def builtin_copylst(self, args: List[Expression], enabled: bool = True) -> None:
        """COPYLST(CONST S: STRING; VAR D: LSTRING).

        Copies S to D; D's length is set to length(S). Manual 11-20:
        error if upper(D) < upper(S).
        """
        src_chars, src_len = self.get_string_chars_and_len(args[0])
        src_len_64 = self.builder.zext(src_len, ir.IntType(64))

        D_arg = args[1]
        if isinstance(D_arg, Identifier):
            D_arg = Designator(D_arg.name, [])
        D_ptr = self.resolve_designator_ptr(D_arg)
        # D_ptr is now directly the aggregate pointer [n+1 x i8]

        zero = ir.Constant(ir.IntType(32), 0)
        one = ir.Constant(ir.IntType(32), 1)

        # Range check BEFORE writing: length(S) must fit in upper(D).
        max_len = self._dest_string_max_len(args[1])
        end_block = self._guard_string_capacity(src_len, max_len, 'copylst', enabled=enabled)

        # Copy to bytes [1..n]
        dest_chars = self.builder.gep(D_ptr, [zero, one])
        self.builder.call(self.memcpy_func(), [dest_chars, src_chars, src_len_64])

        # Store length in byte [0]. LSTRING is length-prefixed (manual 6-18),
        # not null-terminated.
        len_ptr = self.builder.gep(D_ptr, [zero, zero])
        src_len_byte = self.builder.trunc(src_len, ir.IntType(8))
        self.builder.store(src_len_byte, len_ptr)

        if end_block is not None:
            self.builder.branch(end_block)
            self.builder.position_at_end(end_block)

    def builtin_copystr(self, args: List[Expression], enabled: bool = True) -> None:
        """COPYSTR(CONST S: STRING; VAR D: STRING)"""
        src_chars, src_len = self.get_string_chars_and_len(args[0])
        src_len_64 = self.builder.zext(src_len, ir.IntType(64))

        D_arg = args[1]
        if isinstance(D_arg, Identifier):
            D_arg = Designator(D_arg.name, [])
        D_ptr = self.resolve_designator_ptr(D_arg)
        # D_ptr is now directly the aggregate pointer [n x i8]

        # Get D's maximum length
        t = None
        if isinstance(args[1], Identifier):
            symbol = self.scope.lookup(args[1].name) or self.scope.lookup(args[1].name.upper())
            if symbol:
                t = symbol.type_expr
        elif isinstance(args[1], Designator):
            symbol = self.scope.lookup(args[1].name) or self.scope.lookup(args[1].name.upper())
            if symbol:
                t = symbol.type_expr

        is_str, max_len, is_lstring = self.get_string_type_info(t)

        zero = ir.Constant(ir.IntType(32), 0)

        # Range check BEFORE writing (manual 11-20: error if upper(D) < upper(S)).
        # This also guarantees pad_len below is non-negative.
        end_block = self._guard_string_capacity(src_len, max_len, 'copystr', enabled=enabled)

        # STRING has no length byte; copy to [0]
        dest_chars = self.builder.gep(D_ptr, [zero, zero])
        self.builder.call(self.memcpy_func(), [dest_chars, src_chars, src_len_64])

        # Blank-pad remaining characters from [src_len] to [max_len-1] with 0x20
        pad_ptr = self.builder.gep(D_ptr, [zero, src_len])
        pad_len = self.builder.sub(ir.Constant(ir.IntType(32), max_len), src_len)
        pad_len_64 = self.builder.zext(pad_len, ir.IntType(64))
        self.builder.call(self.memset_func(), [pad_ptr, ir.Constant(ir.IntType(32), 0x20), pad_len_64])

        if end_block is not None:
            self.builder.branch(end_block)
            self.builder.position_at_end(end_block)

    def builtin_insert(self, args: List[Expression], enabled: bool = True) -> None:
        src_chars, src_len = self.get_string_chars_and_len(args[0])
        dst_arg = args[1]
        if isinstance(dst_arg, Identifier):
            dst_arg = Designator(dst_arg.name, [])
        dst_ptr = self.resolve_designator_ptr(dst_arg)
        dst_chars, dst_len = self.get_string_chars_and_len(args[1])
        pos = self.coerce_printf_int(self.codegen_expr(args[2]))
        one = ir.Constant(ir.IntType(32), 1)
        zero = ir.Constant(ir.IntType(32), 0)
        new_len = self.builder.add(dst_len, src_len)
        max_len = self._dest_string_max_len(args[1])
        end_block = self._guard_string_capacity(new_len, max_len, 'insert', enabled=enabled)
        tail_len = self.builder.sub(dst_len, self.builder.sub(pos, one))
        memmove = self.runtime_extern('memmove')
        dst_start = self.builder.gep(dst_chars, [self.builder.sub(pos, one)])
        src_start = self.builder.gep(dst_chars, [self.builder.sub(pos, one)])
        self.builder.call(memmove, [self.builder.gep(dst_chars, [self.builder.add(self.builder.sub(pos, one), src_len)]), dst_start, self.builder.zext(tail_len, ir.IntType(64))])
        self.builder.call(memmove, [dst_start, src_chars, self.builder.zext(src_len, ir.IntType(64))])
        if isinstance(args[1], (Identifier, Designator)) and self.get_string_type_info(getattr(self.scope.lookup(args[1].name), 'type_expr', None))[2]:
            len_ptr = self.builder.gep(dst_ptr, [zero, zero])
            self.builder.store(self.builder.trunc(new_len, ir.IntType(8)), len_ptr)
        # end_block is None when the capacity check is disabled ($RANGECK-);
        # same pattern as the other string intrinsics.
        if end_block is not None:
            self.builder.branch(end_block)
            self.builder.position_at_end(end_block)

    def builtin_delete(self, args: List[Expression]) -> None:
        dst_arg = args[0]
        if isinstance(dst_arg, Identifier):
            dst_arg = Designator(dst_arg.name, [])
        dst_ptr = self.resolve_designator_ptr(dst_arg)
        dst_chars, dst_len = self.get_string_chars_and_len(args[0])
        pos = self.coerce_printf_int(self.codegen_expr(args[1]))
        count = self.coerce_printf_int(self.codegen_expr(args[2]))
        one = ir.Constant(ir.IntType(32), 1)
        zero = ir.Constant(ir.IntType(32), 0)
        start = self.builder.sub(pos, one)
        rem = self.builder.sub(dst_len, self.builder.add(start, count))
        memmove = self.runtime_extern('memmove')
        self.builder.call(memmove, [self.builder.gep(dst_chars, [start]), self.builder.gep(dst_chars, [self.builder.add(start, count)]), self.builder.zext(rem, ir.IntType(64))])
        new_len = self.builder.sub(dst_len, count)
        if isinstance(args[0], (Identifier, Designator)) and self.get_string_type_info(getattr(self.scope.lookup(args[0].name), 'type_expr', None))[2]:
            len_ptr = self.builder.gep(dst_ptr, [zero, zero])
            self.builder.store(self.builder.trunc(new_len, ir.IntType(8)), len_ptr)

    def builtin_positn(self, args: List[Expression]) -> ir.Value:
        hay_chars, hay_len = self.get_string_chars_and_len(args[0])
        needle_chars, needle_len = self.get_string_chars_and_len(args[1])
        return self.builder.call(self.runtime_extern('positn'), [hay_chars, hay_len, needle_chars, needle_len])

    def builtin_scaneq_scanne(self, lookup_name: str, args: List[Expression]) -> ir.Value:
        L = self.coerce_printf_int(self.codegen_expr(args[0]))
        P = self.codegen_expr(args[1])
        S_chars, S_len = self.get_string_chars_and_len(args[2])
        I = self.coerce_printf_int(self.codegen_expr(args[3]))
        stop_flag = ir.Constant(ir.IntType(32), 1 if lookup_name == 'SCANEQ' else 0)
        # Pass the first-character pointer together with the explicit character
        # count. The runtime indexes characters 0-based (Pascal position I maps
        # to chars[I-1]); it must not treat the buffer as length-prefixed, since
        # STRING has no length byte and the LSTRING char pointer already points
        # past its own prefix.
        return self.builder.call(self.runtime_extern(lookup_name.lower()), [L, P, S_chars, S_len, I, stop_flag])

    def builtin_encode(self, args: List[Expression]) -> ir.Value:
        dest = args[0].expr if isinstance(args[0], WriteArg) else args[0]
        if isinstance(dest, Identifier):
            dest = Designator(dest.name, [])
        dest_ptr = self.resolve_designator_ptr(dest)
        dest_chars, _dest_cur_len = self.get_string_chars_and_len(dest)
        # Bound the write by the LSTRING's declared CAPACITY, not its current
        # length. get_string_chars_and_len returns the live length byte, which
        # is 0 for a freshly-initialized LSTRING, so the old code told the
        # runtime it had room for 0 characters and ENCODE silently produced an
        # empty string.
        dest_cap = self._dest_string_max_len(dest)
        value_arg = args[1]
        width = ir.Constant(ir.IntType(32), 0)
        precision = ir.Constant(ir.IntType(32), 0)
        if isinstance(value_arg, WriteArg):
            val = self.codegen_expr(value_arg.expr)
            if isinstance(val.type, ir.IntType) and val.type.width < 32:
                val = self.builder.sext(val, ir.IntType(32))
            if value_arg.width is not None:
                width = self.coerce_arg(self.codegen_expr(value_arg.width), ir.IntType(32))
            if value_arg.precision is not None:
                precision = self.coerce_arg(self.codegen_expr(value_arg.precision), ir.IntType(32))
        else:
            val = self.codegen_expr(value_arg)
            if isinstance(val.type, ir.IntType) and val.type.width < 32:
                val = self.builder.sext(val, ir.IntType(32))
        # encode_value(dest_chars, dest_capacity, dest_raw, value, width, precision, _)
        # dest_raw points at the aggregate base so the runtime can set the
        # LSTRING length-prefix byte after writing the characters.
        return self.builder.call(
            self.runtime_extern('encode_value'),
            [dest_chars,
             ir.Constant(ir.IntType(32), dest_cap),
             self.builder.bitcast(dest_ptr,
                                  ir.IntType(8).as_pointer()), val, width, precision,
             ir.Constant(ir.IntType(32), 0)])

    def builtin_decode(self, args: List[Expression]) -> ir.Value:
        src = args[0].expr if isinstance(args[0], WriteArg) else args[0]
        src_chars, src_len = self.get_string_chars_and_len(src)
        dest = args[1].expr if isinstance(args[1], WriteArg) else args[1]
        if isinstance(dest, Identifier):
            dest = Designator(dest.name, [])
        dest_ptr = self.resolve_designator_ptr(dest)
        # Tell the runtime how many bytes the destination holds so the parsed
        # value is written back at the right width (CHAR=1, WORD=2, INTEGER=4)
        # instead of being discarded. For a plain scalar variable we know the
        # size from its declared type; for a selected component (record field /
        # array element) we conservatively fall back to INTEGER width.
        dest_size = 4
        if isinstance(dest, (Identifier, Designator)) and not getattr(dest, 'selectors', None):
            dsym = self.scope.lookup(dest.name) or self.scope.lookup(dest.name.upper())
            if dsym is not None and dsym.type_expr is not None:
                try:
                    sz = self.get_type_size(self.resolve_type_alias(dsym.type_expr))
                    if sz in (1, 2, 4):
                        dest_size = sz
                except Exception:
                    dest_size = 4
        # decode_value(src_chars, src_len, dest_raw, dest_size, ...)
        return self.builder.call(self.runtime_extern('decode_value'), [
            src_chars, src_len,
            self.builder.bitcast(dest_ptr,
                                 ir.IntType(8).as_pointer()),
            ir.Constant(ir.IntType(32), dest_size),
            ir.Constant(ir.IntType(32), 0),
            ir.Constant(ir.IntType(32), 0),
            ir.Constant(ir.IntType(32), 0)
        ])
