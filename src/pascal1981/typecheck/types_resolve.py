"""Type resolution: AST type expressions to resolved type_system types,
array index-bound evaluation, and resolved-type size estimation.

Mixin for PascalTypeChecker, split out of type_checker.py as pure code
movement: methods are unchanged and still reach each other through self.
"""

from typing import Optional

from ..ast_nodes import ArrayType as ASTArrayType
from ..ast_nodes import (
    BoolLiteral,
    CharLiteral,
    Designator,
    Identifier,
    IntLiteral,
    NamedType,
    UnaryOp,
)
from ..ast_nodes import EnumType as ASTEnumType
from ..ast_nodes import FileType as ASTFileType
from ..ast_nodes import LStringType as ASTLStringType
from ..ast_nodes import PointerType as ASTPointerType
from ..ast_nodes import RecordType as ASTRecordType
from ..ast_nodes import SetType as ASTSetType
from ..ast_nodes import SubrangeType as ASTSubrangeType
from ..type_system import (
    BOOLEAN_TYPE,
    CHAR_TYPE,
    INTEGER8_TYPE,
    INTEGER32_TYPE,
    INTEGER64_TYPE,
    INTEGER_TYPE,
    REAL32_TYPE,
    REAL_TYPE,
    WORD8_TYPE,
    WORD32_TYPE,
    WORD64_TYPE,
    WORD_TYPE,
    ArrayType,
    BooleanType,
    CharType,
    EnumType,
    FileType,
    IntegerType,
    LStringType,
    PointerType,
    RealType,
    RecordType,
    SetType,
    StringType,
    Type,
    WordType,
)


class TypeResolveMixin:

    def _eval_index_bound(self, expr) -> Optional[tuple]:
        """Best-effort evaluate an array index-range endpoint.

        Returns ``(ordinal_value, Type)`` for a compile-time ordinal constant,
        where ``ordinal_value`` is the value used for storage bounds (ORD for
        chars, member position for enums) and ``Type`` is the index type the
        endpoint implies. Returns ``None`` when the endpoint isn't a recognized
        ordinal constant (e.g. a named INTEGER constant), letting the caller
        fall back to INTEGER indexing.
        """
        if isinstance(expr, IntLiteral):
            return expr.value, INTEGER_TYPE
        if isinstance(expr, CharLiteral):
            return (ord(expr.value[0]) if expr.value else 0), CHAR_TYPE
        if isinstance(expr, BoolLiteral):
            return (1 if expr.value else 0), BOOLEAN_TYPE
        if isinstance(expr, UnaryOp) and expr.op in ('PLUS', 'MINUS'):
            inner = self._eval_index_bound(expr.operand)
            if inner is None:
                return None
            val, ty = inner
            return (-val if expr.op == 'MINUS' else val), ty
        # A bare identifier may name an enum member (its ordinal is its
        # declaration position) used as an index bound, e.g. ARRAY[Red..Blue].
        name = None
        if isinstance(expr, Identifier):
            name = expr.name
        elif isinstance(expr, Designator) and not expr.selectors:
            name = expr.name
        if name is not None:
            sym = self.symbol_table.lookup(name)
            if sym and sym.kind == 'const' and isinstance(sym.type, EnumType):
                target = name.upper()
                for i, member in enumerate(sym.type.members):
                    if member.upper() == target:
                        return i, sym.type
        return None

    def resolve_type(self, type_expr) -> Optional[Type]:
        """Resolve a type expression to a Type object."""
        if isinstance(type_expr, NamedType):
            name = type_expr.name.upper()
            if name == 'INTEGER':
                return INTEGER_TYPE
            elif name == 'INTEGER8' and (self.feature_enabled('wide-integers') or self.in_device_module):
                # INTEGER8 is the 8-bit signed extension type (C int8_t), the
                # narrow sibling of INTEGER32/INTEGER64.  It is NOT a synonym
                # for CHAR: CHAR is a character type with no arithmetic.
                return INTEGER8_TYPE
            elif name == 'INTEGER16' and (self.feature_enabled('wide-integers') or self.in_device_module):
                # INTEGER16 is a synonym for INTEGER, gated on the wide-integer
                # surface (like INTEGER32), so it is available exactly when the
                # other wide integer types are.
                return INTEGER_TYPE
            elif name == 'INTEGER32' and (self.feature_enabled('wide-integers') or self.in_device_module):
                return INTEGER32_TYPE
            elif name == 'INTEGER64' and self.feature_enabled('wide-integers'):
                return INTEGER64_TYPE
            elif name == 'BOOLEAN':
                return BOOLEAN_TYPE
            elif name == 'REAL':
                return REAL_TYPE
            elif name == 'REAL64' and (self.feature_enabled('wide-reals') or self.in_device_module):
                # REAL64 is a 64-bit synonym for REAL.
                return REAL_TYPE
            elif name == 'REAL32' and (self.feature_enabled('wide-reals') or self.in_device_module):
                return REAL32_TYPE
            elif name == 'WORD':
                return WORD_TYPE
            elif name == 'WORD8' and (self.feature_enabled('wide-integers') or self.in_device_module):
                # WORD8 is the 8-bit unsigned extension type (C uint8_t), the
                # narrow sibling of WORD32/WORD64.
                return WORD8_TYPE
            elif name == 'WORD16' and (self.feature_enabled('wide-integers') or self.in_device_module):
                # WORD16 is a synonym for WORD, gated on the wide-integer surface
                # (like WORD32), so it is available exactly when the other wide
                # integer types are.
                return WORD_TYPE
            elif name == 'WORD32' and (self.feature_enabled('wide-integers') or self.in_device_module):
                # WORD32 is the unsigned sibling of INTEGER32 (32-bit unsigned).
                return WORD32_TYPE
            elif name == 'WORD64' and self.feature_enabled('wide-integers'):
                # WORD64 is the unsigned sibling of INTEGER64 (64-bit unsigned).
                return WORD64_TYPE
            elif name == 'CHAR':
                return CHAR_TYPE
            elif name == 'ADRMEM':
                return PointerType(CHAR_TYPE)
            elif name == 'ADSMEM':
                # Segmented address type: the ADS sibling of ADRMEM. Distinct
                # from ADRMEM (flavor 'ADS' vs 'POINTER'), so the segmented
                # runtime builtins require ADS-style addresses.
                return PointerType(CHAR_TYPE, flavor='ADS')
            elif name == 'STRING':
                max_len = int(type_expr.param) if isinstance(type_expr.param, int) else 256
                return StringType(max_len)
            elif name == 'LSTRING':
                max_len = int(type_expr.param) if isinstance(type_expr.param, int) else 256
                return LStringType(max_len)
            else:
                sym = self.symbol_table.lookup(type_expr.name)
                if sym and sym.kind == 'type':
                    return sym.type
                return None
        elif isinstance(type_expr, ASTLStringType):
            return LStringType(type_expr.max_len)
        elif isinstance(type_expr, ASTEnumType):
            return EnumType(list(type_expr.values))
        elif isinstance(type_expr, ASTSetType):
            base_type = self.resolve_type(type_expr.base)
            return SetType(base_type) if base_type else None
        elif isinstance(type_expr, ASTFileType):
            element_type = self.resolve_type(type_expr.element_type)
            return FileType(element_type, structure=getattr(type_expr, 'structure', 'BINARY')) if element_type else None
        elif isinstance(type_expr, ASTSubrangeType):
            if type_expr.host:
                host = self.resolve_type(NamedType(type_expr.host, None))
                if host:
                    return host
            low_type = self.infer_expression_type(type_expr.low)
            high_type = self.infer_expression_type(type_expr.high)
            if low_type and high_type and low_type.equivalent_to(high_type):
                return low_type
            return None
        elif isinstance(type_expr, ASTArrayType):
            # Resolve the element type
            if isinstance(type_expr.element_type, Type):
                # Already a Type object (from AST)
                element_type = type_expr.element_type
            else:
                # Resolve as type expression
                element_type = self.resolve_type(type_expr.element_type)

            if element_type and type_expr.index_range:
                # The index range fixes both the storage bounds and the ordinal
                # type a subscript must have. Pascal index types are ordinal
                # (INTEGER, CHAR, BOOLEAN, enum, ...), not just INTEGER, so we
                # evaluate each endpoint to (ordinal_value, type) rather than
                # assuming integer literals.
                try:
                    low_eval = self._eval_index_bound(type_expr.index_range.low)
                    high_node = type_expr.index_range.high
                    high_eval = self._eval_index_bound(high_node) if high_node else None

                    # Index type comes from whichever endpoint we could resolve
                    # (they should agree); default to INTEGER when neither is a
                    # recognizable ordinal constant (e.g. named-constant bounds).
                    index_type = None
                    if low_eval is not None:
                        index_type = low_eval[1]
                    elif high_eval is not None:
                        index_type = high_eval[1]

                    lower = low_eval[0] if low_eval is not None else 1
                    if high_eval is not None:
                        upper = high_eval[0]
                    elif high_node is None:
                        # Super array (ARRAY[lo..*]): upper bound is open.
                        upper = lower
                    else:
                        upper = 10

                    return ArrayType(element_type, lower, upper, packed=getattr(type_expr, 'packed', False), index_type=index_type)
                except Exception:
                    return None
            return None
        elif isinstance(type_expr, ASTRecordType):
            # AST RecordType.fields is a list of (name_list, type) pairs, e.g.
            # `x, y: INTEGER` parses to (['x', 'y'], INTEGER). Expand each name
            # into the name->type dict that type_system.RecordType expects.
            # Insertion order is preserved (declaration order), matching the
            # struct layout codegen builds.
            fields = {}
            for names, field_type_expr in (type_expr.fields or []):
                field_type = self.resolve_type(field_type_expr)
                if field_type:
                    for field_name in names:
                        fields[field_name] = field_type
            return RecordType(getattr(type_expr, 'name', None), fields)
        elif isinstance(type_expr, ASTPointerType):
            base_type = self.resolve_type(type_expr.base)
            flavor = getattr(type_expr, 'flavor', 'POINTER')
            # ADS(s) OF T: fold the pointee space and gate it on DEVICE MODULE.
            space_ord = None
            space_expr = getattr(type_expr, 'space', None)
            if space_expr is not None:
                space_ord = self._fold_space(space_expr)
                if space_ord is None:
                    self.error(f"invalid address space in {flavor} type", type_expr)
                elif not self.in_device_module:
                    self.error("address spaces require device code", type_expr)
            target = base_type if base_type else CHAR_TYPE
            return PointerType(target, flavor=flavor, space=space_ord)
        else:
            return None

    def get_resolved_type_size(self, t: Type) -> int:
        """Estimate the size of a resolved Type in bytes."""
        if isinstance(t, IntegerType):
            return 4
        elif isinstance(t, RealType):
            return 8
        elif isinstance(t, BooleanType):
            return 1
        elif isinstance(t, WordType):
            return 2
        elif isinstance(t, CharType):
            return 1
        elif isinstance(t, EnumType):
            return 4
        elif isinstance(t, SetType):
            return 32
        elif isinstance(t, StringType):
            return t.max_len
        elif isinstance(t, LStringType):
            return t.max_len + 1
        elif isinstance(t, PointerType):
            return 8
        elif isinstance(t, ArrayType):
            elem_size = self.get_resolved_type_size(t.element_type)
            count = max(0, t.upper_bound - t.lower_bound + 1)
            return count * elem_size
        elif isinstance(t, RecordType):
            total = 0
            for ftype in t.fields.values():
                total += self.get_resolved_type_size(ftype)
            return total
        return 4
