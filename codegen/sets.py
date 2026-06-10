"""
SETS mixin for Codegen.

Pascal set operations

Part of Plan 1 refactoring (mixin-based architecture).
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple, Union

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder

from ast_nodes import *


class SetsMixin:
    """Mixin for sets functionality."""

    def set_llvm_type(self) -> ir.Type:
        """LLVM representation for all Pascal sets: 256 bits as four i64 words."""
        return ir.ArrayType(ir.IntType(64), 4)

    def codegen_set_constructor(self, expr: SetConstructor) -> ir.Value:
        """Codegen a set constructor as a 256-bit bitvector.

        Constant elements and ranges fold into a compile-time set constant;
        non-constant elements and range bounds are set at runtime (single bits
        inline, ranges via a small loop). Reversed ranges are treated as empty.
        """
        # First fold every element we can evaluate at compile time. Collect the
        # remaining (dynamic) elements for runtime bit-setting.
        words = [0, 0, 0, 0]
        dynamic: List[Union[Expression, RangeExpr]] = []
        for element in expr.elements:
            if isinstance(element, RangeExpr):
                low = self._try_const(element.low)
                high = self._try_const(element.high)
                if low is not None and high is not None:
                    if low <= high:
                        for value in range(low, high + 1):
                            self._set_constant_bit(words, value)
                    continue
                dynamic.append(element)
            else:
                value = self._try_const(element)
                if value is not None:
                    self._set_constant_bit(words, value)
                else:
                    dynamic.append(element)

        const_set = ir.Constant(self.set_llvm_type(), [ir.Constant(ir.IntType(64), word) for word in words])
        if not dynamic:
            return const_set

        # Runtime path: materialize the constant part in a temporary and OR in
        # the dynamic elements bit by bit.
        slot = self.builder.alloca(self.set_llvm_type(), name='settmp')
        self.builder.store(const_set, slot)
        for element in dynamic:
            if isinstance(element, RangeExpr):
                self._set_runtime_range(slot, element.low, element.high)
            else:
                self._set_runtime_bit(slot, self.codegen_expr(element))
        return self.builder.load(slot)

    def codegen_set_binop(self, op: str, left: ir.Value, right: ir.Value) -> ir.Value:
        """Lower Pascal set operators over the fixed [4 x i64] representation."""
        if op == 'IN':
            if not self.is_set_value(right):
                raise CodegenError('Right operand of IN must be a set')
            return self.codegen_set_member(left, right)

        if not self.is_set_value(left) or not self.is_set_value(right):
            raise CodegenError(f'Operator {op} requires set operands')

        if op == 'PLUS':
            return self.set_from_words([self.builder.or_(self.set_word(left, i), self.set_word(right, i)) for i in range(4)])
        if op == 'MUL':
            return self.set_from_words([self.builder.and_(self.set_word(left, i), self.set_word(right, i)) for i in range(4)])
        if op == 'MINUS':
            all_ones = ir.Constant(ir.IntType(64), (1 << 64) - 1)
            return self.set_from_words([self.builder.and_(self.set_word(left, i), self.builder.xor(self.set_word(right, i), all_ones)) for i in range(4)])
        if op in {'EQ', 'NEQ'}:
            eq = self.codegen_set_equal(left, right)
            return eq if op == 'EQ' else self.builder.not_(eq)
        if op in {'LE', 'GE', 'LT', 'GT'}:
            subset = self.codegen_set_subset(left, right) if op in {'LE', 'LT'} else self.codegen_set_subset(right, left)
            if op in {'LE', 'GE'}:
                return subset
            return self.builder.and_(subset, self.builder.not_(self.codegen_set_equal(left, right)))
        raise CodegenError(f'Unknown set operator: {op}')

    def codegen_set_member(self, ordinal: ir.Value, set_value: ir.Value) -> ir.Value:
        """Lower ordinal IN set to a bit test."""
        if isinstance(ordinal.type, ir.IntType) and ordinal.type.width < 32:
            ordinal = self.builder.zext(ordinal, ir.IntType(32))
        elif isinstance(ordinal.type, ir.IntType) and ordinal.type.width > 32:
            ordinal = self.builder.trunc(ordinal, ir.IntType(32))
        word_index = self.builder.udiv(ordinal, ir.Constant(ir.IntType(32), 64))
        bit_index = self.builder.urem(ordinal, ir.Constant(ir.IntType(32), 64))
        words_ptr = self.builder.alloca(self.set_llvm_type(), name='settmp')
        self.builder.store(set_value, words_ptr)
        word_ptr = self.builder.gep(words_ptr, [ir.Constant(ir.IntType(32), 0), word_index])
        word = self.builder.load(word_ptr)
        bit_index64 = self.builder.zext(bit_index, ir.IntType(64))
        mask = self.builder.shl(ir.Constant(ir.IntType(64), 1), bit_index64)
        masked = self.builder.and_(word, mask)
        return self.builder.icmp_unsigned('!=', masked, ir.Constant(ir.IntType(64), 0))

    def codegen_set_equal(self, left: ir.Value, right: ir.Value) -> ir.Value:
        result = self.builder.icmp_unsigned('==', self.set_word(left, 0), self.set_word(right, 0))
        for i in range(1, 4):
            eq = self.builder.icmp_unsigned('==', self.set_word(left, i), self.set_word(right, i))
            result = self.builder.and_(result, eq)
        return result

    def codegen_set_subset(self, left: ir.Value, right: ir.Value) -> ir.Value:
        """Return left <= right for sets: every bit in left is also in right."""
        result: Optional[ir.Value] = None
        for i in range(4):
            left_word = self.set_word(left, i)
            right_word = self.set_word(right, i)
            included = self.builder.icmp_unsigned('==', self.builder.and_(left_word, right_word), left_word)
            result = included if result is None else self.builder.and_(result, included)
        return result if result is not None else ir.Constant(ir.IntType(1), 1)

    def _set_runtime_bit(self, slot: ir.Value, ordinal: ir.Value) -> None:
        """OR one runtime ordinal bit into the set stored at ``slot``."""
        ordinal = self._normalize_ordinal(ordinal)
        word_index = self.builder.udiv(ordinal, ir.Constant(ir.IntType(32), 64))
        bit_index = self.builder.urem(ordinal, ir.Constant(ir.IntType(32), 64))
        word_ptr = self.builder.gep(slot, [ir.Constant(ir.IntType(32), 0), word_index])
        word = self.builder.load(word_ptr)
        bit_index64 = self.builder.zext(bit_index, ir.IntType(64))
        mask = self.builder.shl(ir.Constant(ir.IntType(64), 1), bit_index64)
        self.builder.store(self.builder.or_(word, mask), word_ptr)

    def _set_runtime_range(self, slot: ir.Value, low_expr: Expression, high_expr: Expression) -> None:
        """Set every bit in [low, high] at runtime via a counted loop."""
        low = self._normalize_ordinal(self.codegen_expr(low_expr))
        high = self._normalize_ordinal(self.codegen_expr(high_expr))
        counter = self.builder.alloca(ir.IntType(32), name='setrange')
        self.builder.store(low, counter)
        cond_block = self.builder.append_basic_block('setrange.cond')
        body_block = self.builder.append_basic_block('setrange.body')
        end_block = self.builder.append_basic_block('setrange.end')
        self.builder.branch(cond_block)

        self.builder.position_at_end(cond_block)
        cur = self.builder.load(counter)
        self.builder.cbranch(self.builder.icmp_signed('<=', cur, high), body_block, end_block)

        self.builder.position_at_end(body_block)
        self._set_runtime_bit(slot, self.builder.load(counter))
        nxt = self.builder.add(self.builder.load(counter), ir.Constant(ir.IntType(32), 1))
        self.builder.store(nxt, counter)
        self.builder.branch(cond_block)

        self.builder.position_at_end(end_block)

    def _set_constant_bit(self, words: List[int], value: int) -> None:
        """Set one ordinal bit in a four-word set constant."""
        if value < 0 or value > 255:
            raise CodegenError(f'Set element ordinal out of range 0..255: {value}')
        words[value // 64] |= 1 << (value % 64)

    def _normalize_ordinal(self, value: ir.Value) -> ir.Value:
        """Coerce a set element/ordinal value to i32."""
        if isinstance(value.type, ir.IntType):
            if value.type.width < 32:
                return self.builder.zext(value, ir.IntType(32))
            if value.type.width > 32:
                return self.builder.trunc(value, ir.IntType(32))
        return value

    def is_set_value(self, value: ir.Value) -> bool:
        """Return True for the fixed Pascal set aggregate representation."""
        typ = value.type
        return isinstance(typ, ir.ArrayType) and typ.count == 4 and isinstance(typ.element, ir.IntType) and typ.element.width == 64

    def set_word(self, value: ir.Value, index: int) -> ir.Value:
        return self.builder.extract_value(value, index)

    def set_from_words(self, words: List[ir.Value]) -> ir.Value:
        result: ir.Value = ir.Constant(self.set_llvm_type(), None)
        for index, word in enumerate(words):
            result = self.builder.insert_value(result, word, index)
        return result
