"""
EXPRS mixin for Codegen.

Expression code generation

Part of Plan 1 refactoring (mixin-based architecture).
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple, Union

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder

from ast_nodes import *
from type_system import INTEGER32_TYPE, INTEGER64_TYPE, INTEGER_TYPE, WORD_TYPE

from .base import CodegenError
from type_system import LStringType as ResolvedLStringType
from type_system import StringType as ResolvedStringType


class ExprsMixin:
    """Mixin for exprs functionality."""

    def codegen_expr(self, expr: Expression) -> ir.Value:
        """Codegen an expression."""
        if isinstance(expr, IntLiteral):
            resolved = getattr(expr, 'resolved_type', INTEGER_TYPE)
            if resolved == INTEGER64_TYPE:
                return ir.Constant(ir.IntType(64), expr.value)
            if resolved == INTEGER32_TYPE:
                return ir.Constant(ir.IntType(32), expr.value)
            return ir.Constant(ir.IntType(16), expr.value)
        elif isinstance(expr, RealLiteral):
            return ir.Constant(ir.DoubleType(), expr.value)
        elif isinstance(expr, CharLiteral):
            # Convert char to int
            return ir.Constant(ir.IntType(8), ord(expr.value[0]) if expr.value else 0)
        elif isinstance(expr, StringLiteral):
            # Remove single quotes around the Pascal string literal if any
            val_str = expr.value
            if val_str.startswith("'") and val_str.endswith("'"):
                val_str = val_str[1:-1]
            # Replace double single-quotes with single-quote (Pascal escape)
            val_str = val_str.replace("''", "'")

            # Create a global string constant in the module (null-terminated)
            str_bytes = bytearray(val_str.encode('utf-8') + b'\0')
            str_const = ir.Constant(ir.ArrayType(ir.IntType(8), len(str_bytes)), str_bytes)
            str_global = ir.GlobalVariable(self.module, str_const.type, name=self.unique_name('str'))
            str_global.initializer = str_const
            str_global.global_constant = True

            # Return pointer to the first character of the string constant
            zero = ir.Constant(ir.IntType(32), 0)
            return self.builder.gep(str_global, [zero, zero])
        elif isinstance(expr, BoolLiteral):
            return ir.Constant(ir.IntType(1), 1 if expr.value else 0)
        elif isinstance(expr, NilLiteral):
            return ir.Constant(ir.PointerType(ir.IntType(8)), None)
        elif isinstance(expr, AdrExpr):
            # Address-of operator (adr var_name)
            symbol = self.scope.lookup(expr.name)
            if not symbol:
                raise CodegenError(f'Undefined variable: {expr.name}')
            # Local/global variables are represented as pointers in LLVM, so symbol.llvm_value is the address
            return symbol.llvm_value
        elif isinstance(expr, AdsExpr):
            symbol = self.scope.lookup(expr.name)
            if not symbol:
                raise CodegenError(f'Undefined variable: {expr.name}')
            return ir.Constant.literal_struct([symbol.llvm_value, ir.Constant(ir.IntType(16), 0)])
        elif isinstance(expr, SizeofExpr):
            # Sizeof operator (sizeof var_name or sizeof type)
            if isinstance(expr.target, str):
                symbol = self.scope.lookup(expr.target) or self.scope.lookup(expr.target.upper())
                if symbol is not None and symbol.type_expr is not None:
                    size_val = self.get_type_size(symbol.type_expr)
                else:
                    # Not a variable: treat the name as a built-in type name
                    size_val = self._scalar_size(expr.target)
            else:
                # An AST Type node was supplied directly
                size_val = self.get_type_size(expr.target)
            return ir.Constant(ir.IntType(16), size_val)  # WORD is 16-bit
        elif isinstance(expr, UpperExpr) or isinstance(expr, LowerExpr):
            symbol = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
            if not symbol or symbol.type_expr is None:
                raise CodegenError(f'Undefined variable: {expr.name}')
            ty = symbol.type_expr
            if hasattr(ty, 'index_range'):
                lower = self.eval_const_expr(ty.index_range.low)
                upper = None if ty.index_range.high is None else self.eval_const_expr(ty.index_range.high)
            elif hasattr(ty, 'lower_bound') and hasattr(ty, 'upper_bound'):
                lower = ty.lower_bound
                upper = ty.upper_bound
            else:
                raise CodegenError(f"{type(expr).__name__[:-4].upper()} expects an array variable")
            bound = upper if isinstance(expr, UpperExpr) else lower
            if bound is None:
                raise CodegenError(f"{type(expr).__name__[:-4].upper()} could not resolve bound for {expr.name}")
            return ir.Constant(ir.IntType(32), bound)
        elif isinstance(expr, Identifier):
            # A named constant used as a value (e.g. FOR i := 0 TO size)
            key = expr.name.upper()
            if key in self.constants:
                return self._const_ir(key)
            if key == 'NULL':
                return self.null_lstring_ptr()
            if key in {'EOF', 'EOLN'}:
                sym = self.scope.lookup('INPUT')
                out_sym = self.scope.lookup('OUTPUT')
                handle = self.builder.load(sym.llvm_value)
                fcb_ptr = self.builder.bitcast(handle, self.file_fcb_type().as_pointer())
                out_fcb = self.builder.bitcast(self.builder.load(out_sym.llvm_value), self.file_fcb_type().as_pointer())
                self.builder.call(self.scope.lookup('pas_file_attach_std').llvm_value, [fcb_ptr, out_fcb])
                fn = self.scope.lookup('pas_file_eof' if key == 'EOF' else 'pas_file_eoln').llvm_value
                return self.builder.icmp_unsigned('!=', self.builder.call(fn, [fcb_ptr]), ir.Constant(ir.IntType(32), 0))
            symbol = self.scope.lookup(expr.name) or self.scope.lookup(key)
            if not symbol:
                raise CodegenError(f'Undefined variable: {expr.name}')
            if symbol.type_expr is None and getattr(symbol.llvm_value, 'function_type', None) and len(symbol.llvm_value.function_type.args) == 0:
                return self.builder.call(symbol.llvm_value, [])
            # Parameters are passed by value, don't load them
            if symbol.is_parameter:
                return symbol.llvm_value
            # For string/array variables, return pointer without loading (inline aggregates)
            # For scalar variables, load the value
            from ast_nodes import LStringType as ASTLStringType
            if isinstance(symbol.type_expr, (ResolvedLStringType, ResolvedStringType, ASTLStringType, ArrayType)):
                return symbol.llvm_value  # Return pointer to aggregate
            elif isinstance(symbol.type_expr, NamedType) and symbol.type_expr.name.upper() in {'STRING', 'LSTRING'}:
                return symbol.llvm_value  # Return pointer to aggregate
            elif isinstance(symbol.type_expr, NamedType) and isinstance(self.resolve_type_alias(symbol.type_expr), ArrayType):
                return symbol.llvm_value  # Return pointer to aggregate alias
            return self.builder.load(symbol.llvm_value)
        elif isinstance(expr, SetConstructor):
            return self.codegen_set_constructor(expr)
        elif isinstance(expr, Designator):
            symbol = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
            if not symbol:
                raise CodegenError(f'Undefined variable: {expr.name}')
            # Parameters are passed by value, don't load them
            if symbol.is_parameter:
                return symbol.llvm_value

            ptr = self.resolve_designator_ptr(expr)
            # If the designator is a constant, return its value directly (not a pointer)
            if not isinstance(ptr.type, ir.PointerType):
                return ptr
            # Sets are represented as an aggregate too, but participate in
            # value operations (+, -, *, IN), so load them. Strings/arrays and
            # records still travel as pointers for inline aggregate handling.
            if self.is_set_value(ir.Constant(ptr.type.pointee, None)):
                return self.builder.load(ptr)
            # For aggregate designators, return pointer without loading (inline aggregates)
            if isinstance(ptr.type.pointee, (ir.ArrayType, ir.LiteralStructType, ir.IdentifiedStructType)):
                return ptr  # Return pointer to aggregate
            return self.builder.load(ptr)
        elif isinstance(expr, BinOp):
            return self.codegen_binop(expr)
        elif isinstance(expr, UnaryOp):
            return self.codegen_unaryop(expr)
        elif isinstance(expr, FuncCall):
            return self.codegen_func_call(expr)
        elif isinstance(expr, RetypeExpr):
            # 1. Generate code for the inner expression
            val = self.codegen_expr(expr.expr)

            # 2. Get the target LLVM type
            target_llvm_type = self.llvm_type(NamedType(expr.type_id, None))

            # Helper to calculate LLVM type size in bytes
            def llvm_type_size(ty: ir.Type) -> int:
                if isinstance(ty, ir.IntType):
                    return (ty.width + 7) // 8
                elif isinstance(ty, ir.FloatType):
                    return 4
                elif isinstance(ty, ir.DoubleType):
                    return 8
                elif isinstance(ty, ir.PointerType):
                    return 8
                elif isinstance(ty, ir.ArrayType):
                    return ty.count * llvm_type_size(ty.element)
                elif isinstance(ty, (ir.LiteralStructType, ir.IdentifiedStructType)):
                    return sum(llvm_type_size(f) for f in ty.elements)
                return 4

            # 3. Get pointer to the memory representation of target size.
            #
            # An LLVM pointer reaching here is ambiguous (checklist 9.9): it is
            # either the *address of* an aggregate value (STRING/LSTRING/ARRAY/
            # RECORD), in which case RETYPE reinterprets the pointee by loading
            # through the bitcast, OR it is a genuine Pascal pointer *value* (a
            # ``^T`` variable, ADR/ADS, NIL), in which case RETYPE must
            # reinterpret the address bits and must NOT dereference. We split on
            # the Pascal type of the inner expression; only when that is
            # inconclusive do we fall back to the LLVM type (a non-aggregate
            # pointee can only be a scalar pointer value, so it is safe to treat
            # as bits; an aggregate pointee defaults to the legacy load-through).
            is_ptr_value = self.retype_source_is_pointer_value(expr.expr)
            if is_ptr_value is None and isinstance(val.type, ir.PointerType):
                is_ptr_value = not isinstance(val.type.pointee, (ir.ArrayType, ir.LiteralStructType, ir.IdentifiedStructType))

            if isinstance(val.type, ir.PointerType) and not is_ptr_value:
                # Aggregate address: reinterpret the bytes the pointer refers to.
                ptr = val
                casted_ptr = self.builder.bitcast(ptr, ir.PointerType(target_llvm_type))
            elif isinstance(val.type, ir.PointerType):
                # Genuine pointer value: reinterpret the address bits themselves
                # by spilling the pointer to a slot and bitcasting the slot,
                # exactly as a non-pointer scalar is handled below.
                source_size = llvm_type_size(val.type)
                target_size = llvm_type_size(target_llvm_type)
                if source_size >= target_size:
                    ptr = self.builder.alloca(val.type)
                    self.builder.store(val, ptr)
                    casted_ptr = self.builder.bitcast(ptr, ir.PointerType(target_llvm_type))
                else:
                    ptr = self.builder.alloca(target_llvm_type)
                    self.builder.store(self.zero_initializer(target_llvm_type), ptr)
                    source_ptr = self.builder.bitcast(ptr, ir.PointerType(val.type))
                    self.builder.store(val, source_ptr)
                    casted_ptr = ptr
            else:
                source_size = llvm_type_size(val.type)
                target_size = llvm_type_size(target_llvm_type)

                if source_size >= target_size:
                    # Source is larger or equal. Allocate source type.
                    ptr = self.builder.alloca(val.type)
                    self.builder.store(val, ptr)
                    casted_ptr = self.builder.bitcast(ptr, ir.PointerType(target_llvm_type))
                else:
                    # Target is larger. Allocate target type.
                    ptr = self.builder.alloca(target_llvm_type)
                    self.builder.store(self.zero_initializer(target_llvm_type), ptr)
                    # Bitcast ptr to source pointer to store the smaller source value
                    source_ptr = self.builder.bitcast(ptr, ir.PointerType(val.type))
                    self.builder.store(val, source_ptr)
                    casted_ptr = ptr

            # 5. Process any selectors
            if expr.selectors:
                cur_type = self.resolve_type_alias(NamedType(expr.type_id, None))
                for selector in expr.selectors:
                    if selector.kind == 'INDEX':
                        # RETYPE indexing is raw-memory navigation (the index is
                        # a 0-based element offset into the reinterpreted bytes),
                        # so it deliberately does not subtract a lower bound.
                        index = self.codegen_expr(selector.index_or_field)
                        if isinstance(casted_ptr.type.pointee, ir.ArrayType):
                            casted_ptr = self.builder.gep(casted_ptr, [ir.Constant(ir.IntType(32), 0), index])
                        else:
                            casted_ptr = self.builder.gep(casted_ptr, [index])
                        _, cur_type = self.array_lower_bound(cur_type)
                    elif selector.kind == 'FIELD':
                        fidx, ftype = self.record_field_index(cur_type, selector.index_or_field)
                        if fidx is None:
                            raise CodegenError(f"RETYPE: cannot access field '{selector.index_or_field}' on type {cur_type}")
                        casted_ptr = self.builder.gep(casted_ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), fidx)])
                        cur_type = ftype
                    elif selector.kind == 'DEREF':
                        casted_ptr = self.builder.load(casted_ptr)
                        base = self.resolve_type_alias(cur_type) if cur_type is not None else None
                        cur_type = getattr(base, 'base', None) or getattr(base, 'target_type', None)

            # 6. If the resulting type is an aggregate, return the pointer. Otherwise load the value.
            if isinstance(casted_ptr.type.pointee, (ir.ArrayType, ir.LiteralStructType, ir.IdentifiedStructType)):
                return casted_ptr
            return self.builder.load(casted_ptr)
        else:
            raise CodegenError(f'Expression type {type(expr).__name__} not yet supported')

    def _type_expr_name(self, type_expr: Type) -> Optional[str]:
        if isinstance(type_expr, BuiltinType):
            return type_expr.name.upper()
        if isinstance(type_expr, NamedType):
            return type_expr.name.upper()
        return None

    def _expr_is_unsigned_word(self, expr: Expression) -> bool:
        """Best-effort Pascal signedness query for checked integer arithmetic.

        Phase 0 keeps INTEGER at i32, but stops selecting signedness from LLVM
        width. WORD is the only unsigned arithmetic scalar in the vintage core;
        later INTEGER-family i16/i32/i64 values remain signed.
        """
        if isinstance(expr, Identifier):
            sym = self.scope.lookup(expr.name)
            return bool(sym and self._type_expr_name(sym.type_expr) == 'WORD')
        if isinstance(expr, Designator):
            sym = self.scope.lookup(expr.name)
            return bool(sym and self._type_expr_name(sym.type_expr) == 'WORD' and not expr.selectors)
        if isinstance(expr, UnaryOp):
            return self._expr_is_unsigned_word(expr.operand)
        if isinstance(expr, BinOp):
            if expr.op in {'PLUS', 'MINUS', 'MUL', 'DIV', 'MOD', 'AND', 'OR', 'XOR'}:
                return self._expr_is_unsigned_word(expr.left) and self._expr_is_unsigned_word(expr.right)
        if isinstance(expr, FuncCall):
            return expr.name.upper() == 'WRD'
        return False

    def _extend_int_for_pascal_expr(self, value: 'ir.Value', target: ir.IntType, expr: Expression) -> 'ir.Value':
        """Extend an integer value using the Pascal source type's signedness."""
        if self._expr_is_unsigned_word(expr):
            return self.builder.zext(value, target)
        return self.builder.sext(value, target)

    def _mathck_arith(self, op: str, left: 'ir.Value', right: 'ir.Value', signed: bool) -> 'ir.Value':
        """Integer add/sub/mul with $MATHCK overflow detection.

        Manual: "Detects INTEGER and WORD overflow" (default +).  INTEGER
        family values use the signed llvm.s*.with.overflow intrinsics; WORD
        (unsigned) uses the unsigned variants.  The manual's exclusion of
        the exact -MAXINT-1 result (#8000) is a 16-bit two-pass-compiler
        artifact and is not reproduced: here every signed overflow,
        including INT_MIN, is detected.  When $MATHCK is off (or operand
        shapes don't match a plain same-width integer op) this lowers to
        the unchecked instruction, exactly as before.
        """
        plain = getattr(self.builder, op)
        if not self.check_enabled('MATHCK'):
            return plain(left, right)
        if not (isinstance(left.type, ir.IntType) and left.type == right.type
                and left.type.width in (16, 32, 64)):
            return plain(left, right)
        meth = getattr(self.builder, ('s' if signed else 'u') + op + '_with_overflow')
        res = meth(left, right)
        val = self.builder.extract_value(res, 0)
        ovf = self.builder.extract_value(res, 1)
        ok = self.builder.icmp_unsigned('==', ovf, ir.Constant(ir.IntType(1), 0))
        self._emit_runtime_check(ok, 'mathck')
        return val

    def _mathck_div_guard(self, divisor: 'ir.Value') -> None:
        """$MATHCK division-by-zero guard for DIV/MOD."""
        if not self.check_enabled('MATHCK'):
            return
        if not isinstance(divisor.type, ir.IntType):
            return
        ok = self.builder.icmp_signed('!=', divisor, ir.Constant(divisor.type, 0))
        self._emit_runtime_check(ok, 'mathck_div')

    def codegen_binop(self, expr: BinOp) -> ir.Value:
        """Codegen binary operation."""
        if expr.op in {'AND_THEN', 'OR_ELSE'}:
            return self.codegen_short_circuit_binop(expr)

        left = self.codegen_expr(expr.left)
        right = self.codegen_expr(expr.right)

        if self.is_set_value(left) or self.is_set_value(right):
            return self.codegen_set_binop(expr.op, left, right)

        if isinstance(left.type, ir.IntType) and isinstance(right.type, ir.IntType) and left.type.width != right.type.width:
            target = ir.IntType(max(left.type.width, right.type.width))
            if left.type.width < target.width:
                left = self._extend_int_for_pascal_expr(left, target, expr.left)
            if right.type.width < target.width:
                right = self._extend_int_for_pascal_expr(right, target, expr.right)

        # SLASH is always real division in Pascal (7/2 = 3.5), so force double
        # even when both operands are integer-typed.
        is_real = (isinstance(left.type, ir.DoubleType) or isinstance(right.type, ir.DoubleType) or expr.op == 'SLASH')
        if is_real:
            if isinstance(left.type, ir.IntType):
                left = self.builder.sitofp(left, ir.DoubleType())
            if isinstance(right.type, ir.IntType):
                right = self.builder.sitofp(right, ir.DoubleType())

        if expr.op == 'PLUS':
            return self.builder.fadd(left, right) if is_real else self._mathck_arith('add', left, right, signed=not self._expr_is_unsigned_word(expr))
        elif expr.op == 'MINUS':
            return self.builder.fsub(left, right) if is_real else self._mathck_arith('sub', left, right, signed=not self._expr_is_unsigned_word(expr))
        elif expr.op == 'MUL':
            return self.builder.fmul(left, right) if is_real else self._mathck_arith('mul', left, right, signed=not self._expr_is_unsigned_word(expr))
        elif expr.op == 'SLASH' or expr.op == 'DIV':
            if is_real:
                return self.builder.fdiv(left, right)
            self._mathck_div_guard(right)
            return self.builder.sdiv(left, right)
        elif expr.op == 'MOD':
            if is_real:
                return self.builder.frem(left, right)
            self._mathck_div_guard(right)
            return self.builder.srem(left, right)
        elif expr.op == 'AND':
            return self.builder.and_(left, right)
        elif expr.op == 'OR':
            return self.builder.or_(left, right)
        elif expr.op == 'XOR':
            return self.builder.xor(left, right)
        elif expr.op == 'EQ':
            return self.builder.fcmp_ordered('==', left, right) if is_real else self.builder.icmp_signed('==', left, right)
        elif expr.op == 'NEQ':
            return self.builder.fcmp_ordered('!=', left, right) if is_real else self.builder.icmp_signed('!=', left, right)
        elif expr.op == 'LT':
            return self.builder.fcmp_ordered('<', left, right) if is_real else self.builder.icmp_signed('<', left, right)
        elif expr.op == 'LE':
            return self.builder.fcmp_ordered('<=', left, right) if is_real else self.builder.icmp_signed('<=', left, right)
        elif expr.op == 'GT':
            return self.builder.fcmp_ordered('>', left, right) if is_real else self.builder.icmp_signed('>', left, right)
        elif expr.op == 'GE':
            return self.builder.fcmp_ordered('>=', left, right) if is_real else self.builder.icmp_signed('>=', left, right)
        else:
            raise CodegenError(f'Unknown binary operator: {expr.op}')

    def codegen_unaryop(self, expr: UnaryOp) -> ir.Value:
        """Codegen unary operation."""
        operand = self.codegen_expr(expr.operand)

        if expr.op == 'MINUS':
            if isinstance(operand.type, ir.DoubleType):
                return self.builder.fsub(ir.Constant(ir.DoubleType(), 0.0), operand)
            return self.builder.neg(operand)
        elif expr.op == 'NOT':
            # Logical NOT: invert the boolean
            return self.builder.not_(operand)
        else:
            raise CodegenError(f'Unknown unary operator: {expr.op}')

    def codegen_short_circuit_binop(self, expr: BinOp) -> ir.Value:
        """Codegen short-circuit boolean AND THEN / OR ELSE."""
        left = self.to_bool(self.codegen_expr(expr.left))

        rhs_block = self.current_function.append_basic_block(name='sc_rhs')
        merge_block = self.current_function.append_basic_block(name='sc_merge')

        if expr.op == 'AND_THEN':
            self.builder.cbranch(left, rhs_block, merge_block)
            short_value = ir.Constant(ir.IntType(1), 0)
        elif expr.op == 'OR_ELSE':
            self.builder.cbranch(left, merge_block, rhs_block)
            short_value = ir.Constant(ir.IntType(1), 1)
        else:
            raise CodegenError(f'Unknown short-circuit operator: {expr.op}')

        left_block = self.builder.block

        self.builder.position_at_end(rhs_block)
        right = self.to_bool(self.codegen_expr(expr.right))
        right_block = self.builder.block
        self.builder.branch(merge_block)

        self.builder.position_at_end(merge_block)
        result = self.builder.phi(ir.IntType(1), name='sc_result')
        result.add_incoming(short_value, left_block)
        result.add_incoming(right, right_block)
        return result

    def codegen_func_call(self, expr: FuncCall) -> ir.Value:
        """Codegen function call."""
        lookup_name = expr.name.upper()

        if lookup_name in {'EOF', 'EOLN'}:
            if len(expr.args) == 0:
                sym = self.scope.lookup('INPUT')
                slot = sym.llvm_value
            elif len(expr.args) == 1:
                target = expr.args[0] if isinstance(expr.args[0], Designator) else Designator(expr.args[0].name, [])
                slot = self.resolve_designator_ptr(target)
            else:
                raise CodegenError(f'{lookup_name} expects 0 or 1 arguments')
            handle = self.builder.load(slot)
            fcb_ptr = self.builder.bitcast(handle, self.file_fcb_type().as_pointer())
            if (len(expr.args) == 0) or (len(expr.args) == 1 and getattr(expr.args[0], 'name', '').upper() in {'INPUT', 'OUTPUT'}):
                in_sym = self.scope.lookup('INPUT')
                out_sym = self.scope.lookup('OUTPUT')
                in_fcb = self.builder.bitcast(self.builder.load(in_sym.llvm_value), self.file_fcb_type().as_pointer())
                out_fcb = self.builder.bitcast(self.builder.load(out_sym.llvm_value), self.file_fcb_type().as_pointer())
                self.builder.call(self.scope.lookup('pas_file_attach_std').llvm_value, [in_fcb, out_fcb])
            fn = self.scope.lookup('pas_file_eof' if lookup_name == 'EOF' else 'pas_file_eoln').llvm_value
            return self.builder.icmp_unsigned('!=', self.builder.call(fn, [fcb_ptr]), ir.Constant(ir.IntType(32), 0))
        if lookup_name == 'POSITN':
            return self.builtin_positn(expr.args)
        if lookup_name in {'SCANEQ', 'SCANNE'}:
            return self.builtin_scaneq_scanne(lookup_name, expr.args)
        if lookup_name == 'ENCODE':
            return self.builtin_encode(expr.args)
        if lookup_name == 'DECODE':
            return self.builtin_decode(expr.args)

        symbol = self.scope.lookup(lookup_name) or self.scope.lookup(expr.name)

        if symbol:
            fn = symbol.llvm_value
            param_types = fn.function_type.args
            param_modes = self.proc_param_modes.get(expr.name.lower(), [])
            args = []
            for i, arg in enumerate(expr.args):
                mode = param_modes[i] if i < len(param_modes) else None
                v = self.codegen_actual_arg(arg, mode)
                if i < len(param_types):
                    v = self.coerce_arg(v, param_types[i])
                args.append(v)
            return self.builder.call(fn, args)

        # Inline built-in functions
        if lookup_name == 'CHR':
            val = self.codegen_expr(expr.args[0])
            if val.type.width == 8:
                return val
            elif val.type.width > 8:
                return self.builder.trunc(val, ir.IntType(8))
            else:
                return self.builder.zext(val, ir.IntType(8))
        elif lookup_name == 'POSITN':
            return self.builtin_positn(expr.args)
        elif lookup_name == 'ORD':
            val = self.codegen_expr(expr.args[0])
            if val.type.width == 32:
                return val
            return self.builder.zext(val, ir.IntType(32))
        elif lookup_name == 'ODD':
            val = self.codegen_expr(expr.args[0])
            one = ir.Constant(val.type, 1)
            result = self.builder.and_(val, one)
            zero = ir.Constant(val.type, 0)
            return self.builder.icmp_signed('!=', result, zero)
        elif lookup_name == 'SUCC':
            val = self.codegen_expr(expr.args[0])
            one = ir.Constant(val.type, 1)
            return self.builder.add(val, one)
        elif lookup_name == 'PRED':
            val = self.codegen_expr(expr.args[0])
            one = ir.Constant(val.type, 1)
            return self.builder.sub(val, one)
        elif lookup_name == 'ABS':
            val = self.codegen_expr(expr.args[0])
            if isinstance(val.type, ir.DoubleType):
                zero = ir.Constant(ir.DoubleType(), 0.0)
                is_neg = self.builder.fcmp_ordered('<', val, zero)
                neg = self.builder.fsub(zero, val)
                return self.builder.select(is_neg, neg, val)
            if isinstance(val.type, ir.IntType):
                zero = ir.Constant(val.type, 0)
                is_neg = self.builder.icmp_signed('<', val, zero)
                neg = self.builder.sub(zero, val)
                return self.builder.select(is_neg, neg, val)
            raise CodegenError(f'ABS not supported for type {val.type}')
        elif lookup_name == 'SQR':
            val = self.codegen_expr(expr.args[0])
            if isinstance(val.type, ir.DoubleType):
                return self.builder.fmul(val, val)
            if isinstance(val.type, ir.IntType):
                return self.builder.mul(val, val)
            raise CodegenError(f'SQR not supported for type {val.type}')
        elif lookup_name in {'HIBYTE', 'LOBYTE'}:
            val = self.codegen_expr(expr.args[0])
            if not isinstance(val.type, ir.IntType):
                raise CodegenError(f'{lookup_name} not supported for type {val.type}')
            shifted = self.builder.lshr(val, ir.Constant(val.type, 8)) if lookup_name == 'HIBYTE' else val
            return self.builder.trunc(shifted, ir.IntType(8))
        elif lookup_name == 'WRD':
            val = self.codegen_expr(expr.args[0])
            vt = val.type
            if isinstance(vt, ir.DoubleType):
                raise CodegenError('WRD: REAL argument not supported')
            elif isinstance(vt, ir.PointerType):
                # pointer → integer → truncate to 16-bit WORD
                val = self.builder.ptrtoint(val, ir.IntType(32))
                return self.builder.trunc(val, ir.IntType(16))
            elif isinstance(vt, ir.IntType):
                w = vt.width
                if w > 16:
                    # Same 16-bit two's-complement pattern: trunc handles
                    # "add MAXWORD+1 if negative" without a branch
                    return self.builder.trunc(val, ir.IntType(16))
                elif w == 16:
                    return val  # WORD → WORD: identity
                else:
                    # CHAR (i8) / BOOLEAN (i8) / small enum → zero-extend
                    return self.builder.zext(val, ir.IntType(16))
            raise CodegenError(f'WRD: unsupported value type {vt}')
        elif lookup_name == 'BYWORD':
            hi_val = self.codegen_expr(expr.args[0])
            lo_val = self.codegen_expr(expr.args[1])

            def _to_i16(v: ir.Value) -> ir.Value:
                """Widen or narrow any integer value to i16."""
                w = v.type.width
                if w < 16:
                    return self.builder.zext(v, ir.IntType(16))
                if w > 16:
                    return self.builder.trunc(v, ir.IntType(16))
                return v

            hi16 = self.builder.and_(_to_i16(hi_val), ir.Constant(ir.IntType(16), 0x00FF))
            lo16 = self.builder.and_(_to_i16(lo_val), ir.Constant(ir.IntType(16), 0x00FF))
            return self.builder.or_(self.builder.shl(hi16, ir.Constant(ir.IntType(16), 8)), lo16)
        elif lookup_name in {'SQRT', 'SIN', 'COS', 'LN', 'EXP', 'ARCTAN'}:
            val = self.codegen_expr(expr.args[0])
            if isinstance(val.type, ir.IntType):
                val = self.builder.sitofp(val, ir.DoubleType())
            elif not isinstance(val.type, ir.DoubleType):
                raise CodegenError(f'{lookup_name} not supported for type {val.type}')

            libm_names = {'SQRT': 'sqrt', 'SIN': 'sin', 'COS': 'cos', 'LN': 'log', 'EXP': 'exp', 'ARCTAN': 'atan'}
            c_name = libm_names[lookup_name]
            double_ty = ir.DoubleType()
            try:
                fn = self.module.get_global(c_name)
            except KeyError:
                fn = ir.Function(self.module, ir.FunctionType(double_ty, [double_ty]), name=c_name)
            return self.builder.call(fn, [val])
        elif lookup_name == 'TRUNC':
            # REAL -> INTEGER: truncate toward zero (manual 11-7)
            val = self.codegen_expr(expr.args[0])
            if isinstance(val.type, ir.IntType):
                val = self.builder.sitofp(val, ir.DoubleType())
            elif not isinstance(val.type, ir.DoubleType):
                raise CodegenError(f'TRUNC not supported for type {val.type}')
            return self.builder.fptosi(val, ir.IntType(32))
        elif lookup_name == 'ROUND':
            # REAL -> INTEGER: rounds away from zero (manual 11-7).
            # Implemented as: fptosi(x + copysign(0.5, x)), i.e. add +0.5
            # for non-negative inputs and -0.5 for negative inputs, then
            # truncate.  This gives half-away-from-zero without requiring
            # libm.round (llvm.round lowers to a libm call in llvmlite).
            val = self.codegen_expr(expr.args[0])
            if isinstance(val.type, ir.IntType):
                val = self.builder.sitofp(val, ir.DoubleType())
            elif not isinstance(val.type, ir.DoubleType):
                raise CodegenError(f'ROUND not supported for type {val.type}')
            zero = ir.Constant(ir.DoubleType(), 0.0)
            half = ir.Constant(ir.DoubleType(), 0.5)
            neg_half = ir.Constant(ir.DoubleType(), -0.5)
            is_neg = self.builder.fcmp_ordered('<', val, zero)
            adj = self.builder.select(is_neg, neg_half, half)
            rounded = self.builder.fadd(val, adj)
            return self.builder.fptosi(rounded, ir.IntType(32))
        elif lookup_name == 'FLOAT':
            # INTEGER -> REAL: sitofp (manual 11-7)
            val = self.codegen_expr(expr.args[0])
            if not isinstance(val.type, ir.IntType):
                raise CodegenError(f'FLOAT not supported for type {val.type}')
            return self.builder.sitofp(val, ir.DoubleType())

        raise CodegenError(f'Undefined function: {expr.name}')

    # ========================================================================
    # Built-in Functions
    # ========================================================================

    def codegen_actual_arg(self, arg: Expression, mode: Optional[str]) -> ir.Value:
        if mode in {'VAR', 'VARS', 'CONST', 'CONSTS'}:
            if isinstance(arg, Identifier):
                return self.resolve_designator_ptr(Designator(arg.name, []))
            if isinstance(arg, Designator):
                return self.resolve_designator_ptr(arg)
            raise CodegenError(f'{mode} parameter requires a designator argument')
        return self.codegen_expr(arg)

    def array_lower_bound(self, type_expr) -> tuple[Optional[int], Any]:
        """For a (possibly aliased) array type, return ``(lower_bound, element_type)``.

        Returns ``(None, None)`` for anything that is not a genuine indexable
        array. In particular STRING/LSTRING are deliberately excluded: their
        element offsets follow a length-prefix convention (LSTRING reserves
        byte 0 for the length), not array lower-bound subtraction, so they must
        keep their existing indexing behavior.
        """
        t = self.resolve_type_alias(type_expr)
        # AST ArrayType carries an index_range with constant-foldable bounds.
        if hasattr(t, 'index_range') and getattr(t, 'index_range', None) is not None:
            try:
                low = self.eval_const_expr(t.index_range.low)
            except Exception:
                low = None
            return low, getattr(t, 'element_type', None)
        # Resolved type_system.ArrayType carries lower_bound + element_type.
        # (StringType/LStringType expose max_len instead and are excluded.)
        if hasattr(t, 'lower_bound') and hasattr(t, 'element_type') and not hasattr(t, 'max_len'):
            return t.lower_bound, t.element_type
        return None, None

    def record_field_index(self, type_expr, field_name: str) -> tuple[Optional[int], Any]:
        """For a (possibly aliased) record type, return ``(llvm_struct_index,
        field_ast_type)`` for ``field_name``, matching the layout in
        ``llvm_type``. Field lookup is case-insensitive (Pascal identifiers are
        case-insensitive). Returns ``(None, None)`` if not a record / no match.
        """
        t = self.resolve_type_alias(type_expr)
        if not isinstance(t, RecordType):  # AST RecordType
            return None, None
        target = field_name.upper()
        idx = 0
        for names, ftype in t.fields:
            for nm in names:
                if nm.upper() == target:
                    return idx, ftype
                idx += 1
        return None, None

    def get_array_bounds(self, type_expr) -> tuple[int, int]:
        type_expr = self.resolve_type_alias(type_expr)
        if hasattr(type_expr, 'index_range') and type_expr.index_range:
            low = self.eval_const_expr(type_expr.index_range.low)
            high = self.eval_const_expr(type_expr.index_range.high) if type_expr.index_range.high else low
            return low, high
        elif hasattr(type_expr, 'lower_bound') and hasattr(type_expr, 'upper_bound'):
            return type_expr.lower_bound, type_expr.upper_bound
        return 1, 10

    def _designator_array_bounds(self, arg) -> tuple[int, int]:
        """(lower, upper) bounds of the array a designator names; (1, 10) fallback."""
        name = arg.name if isinstance(arg, (Identifier, Designator)) else ""
        sym = self.scope.lookup(name) or self.scope.lookup(name.upper()) if name else None
        if sym and sym.type_expr:
            return self.get_array_bounds(sym.type_expr)
        return 1, 10

    def _designator_array_low(self, arg) -> int:
        """Lower bound of the array a designator names; 0 fallback (no shift)."""
        name = arg.name if isinstance(arg, (Identifier, Designator)) else ""
        sym = self.scope.lookup(name) or self.scope.lookup(name.upper()) if name else None
        if sym and sym.type_expr:
            low, _ = self.array_lower_bound(sym.type_expr)
            if low is not None:
                return low
        return 0

    # ========================================================================
    # Utilities
    # ========================================================================
