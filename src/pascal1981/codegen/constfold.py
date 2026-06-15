"""
Const Fold mixin for Codegen.

Compile-time constant folding and evaluation.
Evaluates constant expressions at compile time for array bounds, sizeof, etc.

Part of Plan 1 refactoring (mixin-based architecture).
Checklist: 4.2 (constant folding)
"""

from __future__ import annotations

from typing import Optional

import llvmlite.ir as ir

from ..ast_nodes import *

from .base import CodegenError


class ConstFoldMixin:
    """Mixin for compile-time constant evaluation."""

    def _const_ir(self, name_upper: str) -> ir.Constant:
        """Emit the appropriate LLVM constant for a named compile-time constant."""
        v = self.constants[name_upper]
        if isinstance(v, float):
            return ir.Constant(ir.DoubleType(), v)
        if name_upper == 'MAXINT':
            return ir.Constant(ir.IntType(16), int(v))
        if name_upper == 'MAXINT64':
            return ir.Constant(ir.IntType(64), int(v))
        return ir.Constant(ir.IntType(32), int(v))

    def _try_const(self, expr: Expression) -> Optional[int]:
        """Evaluate an expression as a constant ordinal, or None if not constant."""
        try:
            return self.eval_const_expr(expr)
        except CodegenError:
            return None

    def eval_const_expr(self, expr: Expression):
        """Evaluate a constant expression at compile time.

        Returns int for INTEGER/BOOLEAN/CHAR constants and float for REAL
        constants.  Arithmetic automatically promotes to float when either
        operand is real (mirrors type_system.binary_op_result_type).
        """
        if isinstance(expr, IntLiteral):
            return expr.value
        elif isinstance(expr, RealLiteral):
            return float(expr.value)
        elif isinstance(expr, BoolLiteral):
            return 1 if expr.value else 0
        elif isinstance(expr, CharLiteral):
            return ord(expr.value) if len(expr.value) == 1 else 0
        elif isinstance(expr, RetypeExpr):
            return self.eval_const_expr(expr.expr)
        elif isinstance(expr, Identifier):
            key = expr.name.upper()
            if key in self.constants:
                return self.constants[key]
            raise CodegenError(f'Unknown constant: {expr.name}')
        elif isinstance(expr, Designator) and not expr.selectors:
            key = expr.name.upper()
            if key in self.constants:
                return self.constants[key]
            raise CodegenError(f'Unknown constant: {expr.name}')
        elif isinstance(expr, UnaryOp):
            val = self.eval_const_expr(expr.operand)
            if expr.op == 'MINUS':
                return -val
            elif expr.op == 'PLUS':
                return val
            elif expr.op == 'NOT':
                return 0 if val else 1
        elif isinstance(expr, BinOp):
            left = self.eval_const_expr(expr.left)
            right = self.eval_const_expr(expr.right)
            # SLASH always produces float; any float operand widens the result
            if expr.op == 'SLASH' or isinstance(left, float) or isinstance(right, float):
                lf, rf = float(left), float(right)
                if expr.op in ('PLUS', 'SLASH'):
                    return lf + rf if expr.op == 'PLUS' else (lf / rf if rf != 0.0 else 0.0)
                elif expr.op == 'MINUS':
                    return lf - rf
                elif expr.op == 'MUL':
                    return lf * rf
                elif expr.op == 'DIV':
                    return float(int(lf) // int(rf)) if rf != 0.0 else 0.0
                elif expr.op == 'MOD':
                    return float(int(lf) % int(rf)) if rf != 0.0 else 0.0
            else:
                if expr.op == 'PLUS':
                    return left + right
                elif expr.op == 'MINUS':
                    return left - right
                elif expr.op == 'MUL':
                    return left * right
                elif expr.op == 'DIV':
                    return left // right if right != 0 else 0
                elif expr.op == 'MOD':
                    return left % right if right != 0 else 0
        elif isinstance(expr, FuncCall):
            func_name = expr.name.upper() if hasattr(expr, 'name') else ''
            if func_name == 'WRD':
                raw = self.eval_const_expr(expr.args[0])
                return int(raw) & 0xFFFF
            elif func_name == 'BYWORD':
                hi = int(self.eval_const_expr(expr.args[0])) & 0xFF
                lo = int(self.eval_const_expr(expr.args[1])) & 0xFF
                return (hi << 8) | lo
            elif func_name == 'ORD':
                return int(self.eval_const_expr(expr.args[0]))
            elif func_name == 'CHR':
                return int(self.eval_const_expr(expr.args[0])) & 0xFF
        raise CodegenError(f'Cannot evaluate constant expression: {type(expr).__name__}')
