"""Compile-time integer facts: literal/CONST folding, integer ranges, and the
WORD/INTEGER strictness rules (manual p.6-5).

Mixin for PascalTypeChecker, split out of type_checker.py as pure code
movement: methods are unchanged and still reach each other through self.
"""

from typing import Optional

from ..ast_nodes import (
    BinOp,
    Designator,
    Expression,
    FuncCall,
    Identifier,
    IntLiteral,
    SizeofExpr,
    UnaryOp,
)
from ..type_system import (
    INTEGER8_TYPE,
    INTEGER32_TYPE,
    INTEGER64_TYPE,
    INTEGER_TYPE,
    WORD8_TYPE,
    WORD32_TYPE,
    WORD64_TYPE,
    WORD_TYPE,
    Type,
)


class ConstFoldMixin:

    def _integer_range_for_type(self, t: Type) -> Optional[tuple[int, int]]:
        if t == INTEGER_TYPE:
            # IBM Pascal 2.0 manual (Elementary Types, p.6-5): INTEGER ranges
            # -MAXINT..MAXINT with MAXINT = 32767, and "-32768 is not a valid
            # INTEGER".  The two's-complement bit pattern 0x8000 belongs to WORD,
            # not INTEGER -- one of the motivations the manual gives for having a
            # separate WORD type at all.
            return (-32767, 32767)
        if t == WORD_TYPE:
            return (0, 65535)
        if t == WORD8_TYPE:
            return (0, 255)
        if t == INTEGER8_TYPE:
            return (-128, 127)
        if t == WORD32_TYPE:
            return (0, 4294967295)
        if t == WORD64_TYPE:
            return (0, 18446744073709551615)
        if t == INTEGER32_TYPE:
            return (-2147483648, 2147483647)
        if t == INTEGER64_TYPE:
            return (-9223372036854775808, 9223372036854775807)
        return None

    def _fold_int_literal_value(self, expr: Expression) -> Optional[int]:
        if isinstance(expr, IntLiteral):
            return expr.value
        if isinstance(expr, UnaryOp) and expr.op in ('PLUS', 'MINUS'):
            inner = self._fold_int_literal_value(expr.operand)
            if inner is not None:
                return -inner if expr.op == 'MINUS' else inner
        return None

    # ------------------------------------------------------------------
    # WORD / INTEGER strictness (IBM Pascal 2.0 manual, Elementary Types, p.6-5)
    #
    #   "INTEGER type constants change to WORD type if necessary, but not
    #    INTEGER variables."
    #   "WORD and INTEGER values cannot be mixed in an expression (unless one is
    #    a constant INTEGER), and are not assignment compatible.  Mixing INTEGER
    #    and WORD values results in a warning instead of an error ..."
    #
    # So a signed INTEGER *variable/expression* is NOT assignment compatible with
    # WORD (and vice versa: WORD->INTEGER is already rejected by can_assign, which
    # forces ORD(...)).  Here we additionally reject a non-constant INTEGER value
    # flowing into a WORD target (forcing WRD(...)), and we warn (or, under
    # -f strict-word-int, error) on a WORD/INTEGER expression mix.  In every case
    # an INTEGER *constant* is exempt -- it "changes to WORD" per the manual.
    #
    # Constant-expression folding is implemented via `_fold_const_int` (below):
    # `_is_constant_integer_expr` keeps its literal + named-CONST fast paths and
    # then falls through to the fold for composite expressions such as `k + 1`,
    # `2 * SIZE`, or `SUCC(k)`.  The named-CONST branch stays as-is: a CONST is
    # exempt even if its value cannot be folded.
    # ------------------------------------------------------------------
    def _fold_const_int(self, expr: Expression) -> Optional[int]:
        """Fold a constant INTEGER *expression* to its compile-time value.

        Returns the integer value, or None when the expression is not a
        compile-time INTEGER constant.  Never raises.  Used by
        `_is_constant_integer_expr` to widen the manual's INTEGER-constant
        WORD exemption from literals/named-CONSTs to constant *expressions*.

        Integer-scoped on purpose: literals, unary +/-, arithmetic BinOp
        (+, -, *, DIV, MOD) over foldable operands, identifiers naming an
        integer-family CONST whose folded value was stashed in
        `check_const_decl`, and ORD/SUCC/PRED of a foldable operand.  REAL
        operands, SLASH, comparisons, and set ops return None.  DIV/MOD by
        zero returns None (the codegen folder would emit 0, but the type
        checker declines to claim a value for a program that is dubious
        anyway).
        """
        ARITH = {'PLUS', 'MINUS', 'MUL', 'DIV', 'MOD'}
        if isinstance(expr, IntLiteral):
            return expr.value
        if isinstance(expr, UnaryOp) and expr.op in ('PLUS', 'MINUS'):
            inner = self._fold_const_int(expr.operand)
            if inner is None:
                return None
            return -inner if expr.op == 'MINUS' else inner
        if isinstance(expr, BinOp) and expr.op in ARITH:
            left = self._fold_const_int(expr.left)
            right = self._fold_const_int(expr.right)
            if left is None or right is None:
                return None
            if expr.op == 'PLUS':
                return left + right
            if expr.op == 'MINUS':
                return left - right
            if expr.op == 'MUL':
                return left * right
            if expr.op == 'DIV':
                return None if right == 0 else left // right
            if expr.op == 'MOD':
                return None if right == 0 else left % right
        # Identifier or bare Designator naming an integer-family CONST whose
        # folded value was stashed at declaration time.
        name = None
        if isinstance(expr, Identifier):
            name = expr.name
        elif isinstance(expr, Designator) and not expr.selectors:
            name = expr.name
        if name is not None:
            sym = self.symbol_table.lookup(name)
            if (sym and getattr(sym, 'kind', None) == 'const' and sym.type in (INTEGER_TYPE, INTEGER32_TYPE, INTEGER64_TYPE)):
                folded = getattr(sym, 'const_int', None)
                if folded is not None:
                    return folded
        if isinstance(expr, FuncCall):
            fn = expr.name.upper()
            if fn in ('ORD', 'SUCC', 'PRED') and expr.args:
                val = self._fold_const_int(expr.args[0])
                if val is None:
                    return None
                if fn == 'ORD':
                    return val
                if fn == 'SUCC':
                    return val + 1
                if fn == 'PRED':
                    return val - 1
        return None

    def _is_constant_integer_expr(self, expr: Expression) -> bool:
        """True if expr is a *constant* INTEGER for the manual's WORD exemption.

        Recognizes integer literals (incl. unary +/-), named integer CONSTs
        (exempt even when their value is not foldable), and -- via
        `_fold_const_int` -- constant *expressions* such as `k + 1`,
        `2 * SIZE`, or `SUCC(k)`.
        """
        if self._fold_int_literal_value(expr) is not None:
            return True
        # SIZEOF is a compile-time constant by construction; like a named
        # CONST it is exempt even without folding its value here, so
        # FILLC(adr x, SIZEOF(x), ...) passes the WORD 'len' parameter.
        if isinstance(expr, SizeofExpr):
            return True
        if isinstance(expr, Identifier):
            sym = self.symbol_table.lookup(expr.name)
            if (sym and getattr(sym, 'kind', None) == 'const' and sym.type in (INTEGER_TYPE, INTEGER32_TYPE, INTEGER64_TYPE)):
                return True
        return self._fold_const_int(expr) is not None

    def _const_adapts_to_int_target(self, value_type, target_type, value_expr) -> bool:
        """Vintage constant-adaptation rule, generalized to the extension family.

        The manual's "INTEGER type constants change to WORD type" rule lets a
        compile-time INTEGER constant flow into a WORD target.  The extension
        types inherit the same convenience: a constant integer expression
        (literal, named CONST, or foldable expression) whose value fits the
        target's range may be assigned/passed to any WORD8/WORD/WORD32/WORD64
        or INTEGER8/INTEGER32/INTEGER64 target.  Non-constant values keep the
        strict rules (explicit WRD/WRD8, or widening only)."""
        if value_type not in (INTEGER_TYPE, INTEGER32_TYPE, INTEGER64_TYPE):
            return False
        rng = self._integer_range_for_type(target_type)
        if rng is None:
            return False
        val = self._fold_const_int(value_expr) if value_expr is not None else None
        if val is None:
            return False
        lo, hi = rng
        return lo <= val <= hi

    def _check_word_int_assign(self, value_type, target_type, value_expr, node) -> None:
        """Reject a non-constant INTEGER value assigned/passed into a WORD target.

        WORD->INTEGER is already rejected upstream by can_assign (use ORD); this
        adds the reciprocal vintage rule for INTEGER->WORD (use WRD), with the
        INTEGER-constant exemption.  Applies to assignment, value-argument
        passing, and function return -- every assignment-compatibility context.
        """
        if value_type == INTEGER_TYPE and target_type == WORD_TYPE:
            if value_expr is None or not self._is_constant_integer_expr(value_expr):
                self.error("INTEGER is not assignment compatible with WORD: only INTEGER "
                           "constants change to WORD; convert a signed INTEGER value with "
                           "WRD(...)", node)

    # Equal-rank unsigned/signed integer pairs that carry the WORD/INTEGER
    # signedness ambiguity.  Rank == index into each tuple: rank 0 is the vintage
    # 16-bit WORD/INTEGER pair the manual actually rules on; ranks 1 and 2 are the
    # wide extension types (WORD32/INTEGER32, WORD64/INTEGER64), which the manual
    # does not cover but which inherit the same "which signedness does the
    # arithmetic use?" hazard.  `binary_op_result_type` resolves every one of
    # these same-width mixes to the unsigned member, so the diagnostic below is
    # what makes that silent choice visible (and, under -f strict-word-int,
    # refusable).  Membership is tested with ``==`` rather than a dict keyed by
    # type instance, because some operand types (e.g. SetType) are unhashable.
    _WORD_FAMILY_BY_RANK = (WORD8_TYPE, WORD_TYPE, WORD32_TYPE, WORD64_TYPE)
    _INT_FAMILY_BY_RANK = (INTEGER8_TYPE, INTEGER_TYPE, INTEGER32_TYPE, INTEGER64_TYPE)

    @staticmethod
    def _family_rank(t, family) -> Optional[int]:
        """Rank (0/1/2) of ``t`` within ``family``, or None if it is not a member.

        Uses equality, not a hash lookup, so unhashable operand types (SetType,
        ArrayType, ...) simply compare unequal instead of raising.
        """
        for rank, member in enumerate(family):
            if t == member:
                return rank
        return None

    def _check_word_int_mix(self, left_type, right_type, left_expr, right_expr, op, node) -> None:
        """Diagnose an unsigned/signed (WORD-family/INTEGER-family) mix at equal
        width in an arithmetic or bitwise expression.

        Covers the vintage 16-bit WORD/INTEGER pair *and* the equal-width wide
        extension pairs WORD32/INTEGER32 and WORD64/INTEGER64.  A mix at unequal
        width is not flagged here: there the wider operand's signedness
        unambiguously wins (see `binary_op_result_type`), so there is no
        signedness coin-flip to warn about.

        Allowed when the signed (INTEGER-family) operand is a constant (it
        changes to the unsigned type).  Otherwise a warning by default (the
        vintage compiler arbitrarily picks signed or unsigned arithmetic),
        promoted to a hard error under -f strict-word-int.
        """
        ARITH_BITWISE = {'PLUS', 'MINUS', 'MUL', 'DIV', 'MOD', 'AND', 'OR', 'XOR'}
        if op not in ARITH_BITWISE:
            return
        for a_t, b_t, b_e in ((left_type, right_type, right_expr), (right_type, left_type, left_expr)):
            # a_t is the unsigned (WORD-family) operand, b_t the signed
            # (INTEGER-family) operand; only flag them at equal width/rank.
            a_rank = self._family_rank(a_t, self._WORD_FAMILY_BY_RANK)
            b_rank = self._family_rank(b_t, self._INT_FAMILY_BY_RANK)
            if a_rank is not None and a_rank == b_rank:
                if self._is_constant_integer_expr(b_e):
                    return  # constant INTEGER changes to the WORD type: clean
                msg = ("WORD and INTEGER values cannot be mixed in an expression "
                       "unless the INTEGER operand is a constant; convert "
                       "explicitly with WRD(...) or ORD(...)")
                if self.feature_enabled('strict-word-int'):
                    self.error(msg, node)
                else:
                    self.warning(msg + " (the compiler would arbitrarily use "
                                 "signed or unsigned arithmetic)", node)
                return

    def _check_integer_literal_range(self, expr: Expression, context_type: Optional[Type]) -> None:
        value = self._fold_int_literal_value(expr)
        if value is None:
            return
        target = context_type if context_type is not None else INTEGER_TYPE
        rng = self._integer_range_for_type(target)
        if rng is None:
            return
        lo, hi = rng
        if value < lo or value > hi:
            self.error(f"Integer literal {value} out of range for {target} ({lo}..{hi})", expr)
