"""
Type System for Pascal-1981 Compiler

Defines a type hierarchy for representing Pascal types at compile time.
This is used by both the type checker and code generator.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


class Type(ABC):
    """Base class for all Pascal types."""

    @abstractmethod
    def __str__(self) -> str:
        """Return string representation of type."""
        pass

    @abstractmethod
    def equivalent_to(self, other: 'Type') -> bool:
        """Check if this type is equivalent to another."""
        pass


class IntegerType(Type):
    """The INTEGER type (16-bit signed)."""

    def __str__(self) -> str:
        return "INTEGER"

    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, IntegerType)


class Integer32Type(Type):
    """The INTEGER32 extension type (32-bit signed)."""

    def __str__(self) -> str:
        return "INTEGER32"

    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, Integer32Type)


class Integer64Type(Type):
    """The INTEGER64 extension type (64-bit signed)."""

    def __str__(self) -> str:
        return "INTEGER64"

    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, Integer64Type)


class BooleanType(Type):
    """The BOOLEAN type (TRUE/FALSE)."""

    def __str__(self) -> str:
        return "BOOLEAN"

    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, BooleanType)


class RealType(Type):
    """The REAL type (64-bit floating point).

    ``REAL64`` is an accepted spelling of this same type (a synonym), so there
    is no separate Real64Type: REAL64 resolves to this singleton.
    """

    def __str__(self) -> str:
        return "REAL"

    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, RealType)


class Real32Type(Type):
    """The REAL32 extension type (32-bit floating point, LLVM ``float``).

    Distinct from ``REAL``/``REAL64`` (64-bit). REAL32 is the single-precision
    type needed for true ``mandelbrot_f32``-style device kernels whose PTX
    parameters must be ``.f32``. Mixed REAL32/REAL arithmetic widens to REAL,
    matching the C rule that ``float op double`` promotes to ``double``.
    """

    def __str__(self) -> str:
        return "REAL32"

    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, Real32Type)


class WordType(Type):
    """The WORD type (16-bit unsigned).

    ``WORD16`` is an accepted spelling of this same type (a synonym), gated on
    the same mode that enables ``REAL64``; like ``REAL64`` it resolves to this
    singleton, so there is no separate Word16Type.
    """

    def __str__(self) -> str:
        return "WORD"

    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, WordType)


class Word32Type(Type):
    """The WORD32 extension type (32-bit unsigned).

    The unsigned sibling of INTEGER32, gated on ``wide-integers``.  Widens to
    WORD64 and zero-extends (never sign-extends) when widened.
    """

    def __str__(self) -> str:
        return "WORD32"

    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, Word32Type)


class Word64Type(Type):
    """The WORD64 extension type (64-bit unsigned).

    The unsigned sibling of INTEGER64, gated on ``wide-integers``.
    """

    def __str__(self) -> str:
        return "WORD64"

    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, Word64Type)


class CharType(Type):
    """The CHAR type (single character)."""

    def __str__(self) -> str:
        return "CHAR"

    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, CharType)


@dataclass
class StringType(Type):
    """The STRING(n) type: fixed-length string storage."""

    max_len: int

    def __str__(self) -> str:
        return f"STRING({self.max_len})"

    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, StringType) and self.max_len == other.max_len


@dataclass
class LStringType(Type):
    """The LSTRING(n) type: length-prefixed string storage."""

    max_len: int

    def __str__(self) -> str:
        return f"LSTRING({self.max_len})"

    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, LStringType) and self.max_len == other.max_len


@dataclass
class ArrayType(Type):
    """Array type: ARRAY[lower..upper] OF element_type.

    ``lower_bound``/``upper_bound`` are the *ordinal* values of the index range
    (for a CHAR index these are the ORD of the bound characters; for an enum
    index, the member positions). ``index_type`` records the ordinal type a
    subscript must have. It defaults to ``None``, which is treated as INTEGER so
    arrays built the old way (integer subscripts) keep their exact behavior.
    """

    element_type: Type
    lower_bound: int
    upper_bound: int
    packed: bool = False
    index_type: Optional[Type] = None

    @property
    def effective_index_type(self) -> Type:
        """The subscript type, defaulting to INTEGER when unspecified."""
        return self.index_type if self.index_type is not None else INTEGER_TYPE

    def __str__(self) -> str:
        prefix = "PACKED " if self.packed else ""
        return f"{prefix}ARRAY[{self.lower_bound}..{self.upper_bound}] OF {self.element_type}"

    def equivalent_to(self, other: Type) -> bool:
        if not isinstance(other, ArrayType):
            return False
        return (self.element_type.equivalent_to(other.element_type) and self.lower_bound == other.lower_bound and self.upper_bound == other.upper_bound
                and self.packed == other.packed and self.effective_index_type.equivalent_to(other.effective_index_type))


@dataclass
class RecordType(Type):
    """Record type: RECORD field1: type1; field2: type2; END."""

    name: Optional[str]  # Optional record name
    fields: Dict[str, Type]  # field_name -> type

    def __str__(self) -> str:
        if self.name:
            return f"RECORD {self.name}"
        return "RECORD"

    def equivalent_to(self, other: Type) -> bool:
        if not isinstance(other, RecordType):
            return False
        if len(self.fields) != len(other.fields):
            return False
        # Structural equivalence is ORDER-SENSITIVE: a record's field order
        # determines its physical layout, and whole-record assignment copies
        # by position, so two records are interchangeable only if they list the
        # same fields, in the same order, with equivalent types. (Without this,
        # `RECORD a,b: INTEGER` and `RECORD b,a: INTEGER` would be deemed equal
        # and a positional copy would silently swap the fields.) Field names
        # compare case-insensitively, since Pascal identifiers ignore case.
        for (self_name, self_type), (other_name, other_type) in zip(self.fields.items(), other.fields.items()):
            if self_name.upper() != other_name.upper():
                return False
            if not self_type.equivalent_to(other_type):
                return False
        return True

    def has_field(self, field_name: str) -> bool:
        """Case-insensitive membership test (Pascal identifiers ignore case)."""
        return self.get_field_type(field_name) is not None

    def get_field_type(self, field_name: str) -> Optional[Type]:
        """Get the type of a field by name, case-insensitively."""
        if field_name in self.fields:
            return self.fields[field_name]
        target = field_name.upper()
        for name, ftype in self.fields.items():
            if name.upper() == target:
                return ftype
        return None


@dataclass
class EnumType(Type):
    """An enumerated ordinal type, e.g. (Red, Green, Blue).

    Members carry their declaration order as ordinal values (0, 1, 2, ...).
    ``name`` is the declared type name when known (for readable diagnostics and
    nominal equivalence); anonymous enums compare by member list.
    """

    members: List[str]
    name: Optional[str] = None

    def __str__(self) -> str:
        return self.name or f"({', '.join(self.members)})"

    def equivalent_to(self, other: Type) -> bool:
        if not isinstance(other, EnumType):
            return False
        return self.members == other.members


@dataclass
class SetType(Type):
    """Set type: SET OF element_type."""

    element_type: Type

    def __str__(self) -> str:
        return f"SET OF {self.element_type}"

    def equivalent_to(self, other: Type) -> bool:
        if not isinstance(other, SetType):
            return False
        return self.element_type.equivalent_to(other.element_type)


@dataclass
class FileType(Type):
    """File type: FILE OF element_type, with TEXT marked as ASCII."""

    element_type: Type
    structure: str = 'BINARY'

    def __str__(self) -> str:
        return "TEXT" if self.structure == 'ASCII' and self.element_type.equivalent_to(CHAR_TYPE) else f"FILE OF {self.element_type}"

    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, FileType) and self.structure == other.structure and self.element_type.equivalent_to(other.element_type)


@dataclass
class PointerType(Type):
    """Pointer/address type."""

    target_type: Type
    flavor: str = 'POINTER'  # POINTER, ADR, ADS
    # Pointee address space for ADS pointers: a SPACE ordinal (HOST=0..LOCAL=4)
    # or None for an unspecified/plain pointer (implicitly HOST). Part of type
    # identity for ADS pointers (ads-memory-spaces-design.md S5.1).
    space: Optional[int] = None

    def __str__(self) -> str:
        prefix = {'ADR': 'ADR OF ', 'ADS': 'ADS OF '}.get(self.flavor, '^')
        if self.flavor == 'ADS' and self.space is not None:
            prefix = f'ADS({self.space}) OF '
        return f"{prefix}{self.target_type}"

    def equivalent_to(self, other: Type) -> bool:
        if not isinstance(other, PointerType):
            return False
        # Plain '^T' heap pointers remain a wildcard, matching any flavor.
        if self.flavor == 'POINTER' or other.flavor == 'POINTER':
            return True
        if self.flavor != other.flavor:
            return False
        # When both sides are ADS, the pointee space is part of identity:
        # ADS(GLOBAL) OF T and ADS(SHARED) OF T are distinct, incompatible types.
        if self.flavor == 'ADS' and self.space != other.space:
            return False
        return True


@dataclass
class ProcedureType(Type):
    """Procedure type (has parameters, no return value)."""

    name: str
    params: List[Tuple[str, Type]]  # List of (param_name, param_type)
    is_variadic: bool = False  # True for [VARARGS] C-ABI foreign procedures

    def __str__(self) -> str:
        param_str = ", ".join(f"{name}: {typ}" for name, typ in self.params)
        return f"PROCEDURE {self.name}({param_str})"

    def equivalent_to(self, other: Type) -> bool:
        if not isinstance(other, ProcedureType):
            return False
        if len(self.params) != len(other.params):
            return False
        for (_, type1), (_, type2) in zip(self.params, other.params):
            if not type1.equivalent_to(type2):
                return False
        return True


@dataclass
class FunctionType(Type):
    """Function type (has parameters and return value)."""

    name: str
    params: List[Tuple[str, Type]]  # List of (param_name, param_type)
    return_type: Type
    is_variadic: bool = False  # True for [VARARGS] C-ABI foreign functions

    def __str__(self) -> str:
        param_str = ", ".join(f"{name}: {typ}" for name, typ in self.params)
        return f"FUNCTION {self.name}({param_str}): {self.return_type}"

    def equivalent_to(self, other: Type) -> bool:
        if not isinstance(other, FunctionType):
            return False
        if len(self.params) != len(other.params):
            return False
        for (_, type1), (_, type2) in zip(self.params, other.params):
            if not type1.equivalent_to(type2):
                return False
        return self.return_type.equivalent_to(other.return_type)


# Built-in type instances (singletons)
INTEGER_TYPE = IntegerType()
INTEGER32_TYPE = Integer32Type()
INTEGER64_TYPE = Integer64Type()
BOOLEAN_TYPE = BooleanType()
REAL_TYPE = RealType()
REAL32_TYPE = Real32Type()
WORD_TYPE = WordType()
WORD32_TYPE = Word32Type()
WORD64_TYPE = Word64Type()
CHAR_TYPE = CharType()


def fixed_char_array_len(t: Type) -> Optional[int]:
    """Return length for PACKED ARRAY[..] OF CHAR, else None.

    Differential evidence currently covers PACKED fixed character arrays as
    vintage string-compatible storage; keep the predicate narrow.
    """
    if (isinstance(t, ArrayType) and t.packed and t.element_type.equivalent_to(CHAR_TYPE) and isinstance(t.lower_bound, int) and isinstance(t.upper_bound, int)):
        return t.upper_bound - t.lower_bound + 1
    return None


def is_fixed_char_array(t: Type) -> bool:
    return fixed_char_array_len(t) is not None


# Type coercion rules
def can_assign(from_type: Type, to_type: Type) -> bool:
    """
    Check if a value of from_type can be assigned to a variable of to_type.

    We keep the rule narrow: exact matches are allowed, plus the common
    INTEGER-to-REAL widening used by assignments, parameters, and returns.
    Sets follow exact element-type equivalence, with the empty set represented
    by a compatible SetType. String values may flow between STRING(n) and
    LSTRING(n) when the source fits in the destination capacity.
    """
    if from_type.equivalent_to(to_type):
        return True
    if isinstance(from_type, IntegerType) and isinstance(to_type, RealType):
        return True
    # Integer family widens into REAL32 (single-precision), mirroring the
    # INTEGER->REAL widening above. REAL32 in turn widens into REAL (f32->f64),
    # but the reverse (REAL->REAL32) is a narrowing and is NOT implicit; write
    # REAL32 literals/values in a REAL32 context instead.
    if isinstance(from_type, (IntegerType, Integer32Type, Integer64Type, WordType, Word32Type, Word64Type)) and isinstance(to_type, Real32Type):
        return True
    if isinstance(from_type, Real32Type) and isinstance(to_type, RealType):
        return True
    if isinstance(from_type, IntegerType) and isinstance(to_type, WordType):
        return True
    if isinstance(from_type, IntegerType) and isinstance(to_type, (Integer32Type, Integer64Type)):
        return True
    if isinstance(from_type, Integer32Type) and isinstance(to_type, Integer64Type):
        return True
    if isinstance(from_type, WordType) and isinstance(to_type, (Integer32Type, Integer64Type)):
        return True
    # Unsigned widening: WORD -> WORD32 -> WORD64 (value-preserving zero-extend).
    # Narrowing the other way is NOT implicit, exactly like INTEGER32 -> INTEGER.
    # A signed INTEGER does not implicitly become an unsigned WORD32/WORD64
    # either (the WORD/INTEGER signedness wall extends to the wide unsigned
    # types); convert via WRD(...) into WORD first, then widen.
    if isinstance(from_type, WordType) and isinstance(to_type, (Word32Type, Word64Type)):
        return True
    if isinstance(from_type, Word32Type) and isinstance(to_type, Word64Type):
        return True
    if isinstance(from_type, SetType) and isinstance(to_type, SetType):
        return from_type.element_type.equivalent_to(to_type.element_type)
    if isinstance(from_type, (StringType, LStringType)) and isinstance(to_type, StringType):
        return from_type.max_len == to_type.max_len
    if isinstance(from_type, (StringType, LStringType)) and isinstance(to_type, LStringType):
        return from_type.max_len <= to_type.max_len
    char_array_len = fixed_char_array_len(to_type)
    if isinstance(from_type, (StringType, LStringType)) and char_array_len is not None:
        return from_type.max_len == char_array_len
    return False


def binary_op_result_type(left_type: Type, op: str, right_type: Type) -> Optional[Type]:
    """
    Determine the result type of a binary operation.

    `op` is the AST/token-kind operator name produced by the parser:
      additive/mul  : PLUS MINUS MUL SLASH DIV MOD
      bitwise/logic : AND OR XOR
      short-circuit : AND_THEN OR_ELSE
      comparison    : EQ NEQ LT LE GT GE
    Returns None if the operation is invalid for these types.
    """
    ARITH = {'PLUS', 'MINUS', 'MUL', 'DIV', 'MOD'}  # integer-preserving arithmetic
    BITWISE = {'AND', 'OR', 'XOR'}
    SHORT_CIRCUIT = {'AND_THEN', 'OR_ELSE'}
    COMPARE = {'EQ', 'NEQ', 'LT', 'LE', 'GT', 'GE'}

    # Integer arithmetic
    if isinstance(left_type, IntegerType) and isinstance(right_type, IntegerType):
        if op == 'SLASH':
            return REAL_TYPE  # real division
        if op in ARITH or op in BITWISE:
            return INTEGER_TYPE
        if op in COMPARE:
            return BOOLEAN_TYPE

    # Wide integer extension family (signed INTEGER/INTEGER32/INTEGER64 and
    # unsigned WORD/WORD32/WORD64).  The result width is the wider of the two
    # operands.  Signedness: when the operands differ in width, the wider
    # operand's signedness wins (so WORD + INTEGER32 -> INTEGER32 and the WORD
    # value zero-extends into the i32); when they are the SAME width, an unsigned
    # operand makes the result unsigned (WORD + INTEGER -> WORD, WORD32 +
    # INTEGER32 -> WORD32), consistent with the vintage rank-0 WORD/INTEGER rule.
    # The vintage WORD/INTEGER (16-bit) mix is additionally diagnosed in the type
    # checker; the wide-type mixes are extension territory and are not diagnosed.
    int_rank = {IntegerType: 0, WordType: 0,
                Integer32Type: 1, Word32Type: 1,
                Integer64Type: 2, Word64Type: 2}
    _signed_by_rank = {0: INTEGER_TYPE, 1: INTEGER32_TYPE, 2: INTEGER64_TYPE}
    _unsigned_by_rank = {0: WORD_TYPE, 1: WORD32_TYPE, 2: WORD64_TYPE}
    _unsigned_types = (WordType, Word32Type, Word64Type)
    if type(left_type) in int_rank and type(right_type) in int_rank:
        if op == 'SLASH':
            return REAL_TYPE
        if op in ARITH or op in BITWISE:
            lr, rr = int_rank[type(left_type)], int_rank[type(right_type)]
            rank = max(lr, rr)
            if lr == rr:
                unsigned = isinstance(left_type, _unsigned_types) or isinstance(right_type, _unsigned_types)
            else:
                higher = left_type if lr > rr else right_type
                unsigned = isinstance(higher, _unsigned_types)
            return (_unsigned_by_rank if unsigned else _signed_by_rank)[rank]
        if op in COMPARE:
            return BOOLEAN_TYPE

    # Boolean logic
    if isinstance(left_type, BooleanType) and isinstance(right_type, BooleanType):
        if op in BITWISE or op in SHORT_CIRCUIT:
            return BOOLEAN_TYPE
        if op in ('EQ', 'NEQ'):
            return BOOLEAN_TYPE

    # REAL32 (single-precision) arithmetic and mixing.
    #   REAL32 op REAL32        -> REAL32
    #   REAL32 op {int family}  -> REAL32   (the integer widens to f32)
    #   REAL32 op REAL/REAL64   -> REAL     (f32 widens to f64, C-like)
    REAL_ARITH = ('PLUS', 'MINUS', 'MUL', 'SLASH')
    if isinstance(left_type, Real32Type) or isinstance(right_type, Real32Type):
        other_ok_int = (IntegerType, Integer32Type, Integer64Type, WordType)
        l_is32, r_is32 = isinstance(left_type, Real32Type), isinstance(right_type, Real32Type)
        l_is64, r_is64 = isinstance(left_type, RealType), isinstance(right_type, RealType)
        l_int, r_int = isinstance(left_type, other_ok_int), isinstance(right_type, other_ok_int)
        # Determine if this is a valid REAL32-involving real operation.
        both_real_ish = (l_is32 or l_is64 or l_int) and (r_is32 or r_is64 or r_int)
        if both_real_ish:
            widen_to_64 = l_is64 or r_is64
            if op in REAL_ARITH:
                return REAL_TYPE if widen_to_64 else REAL32_TYPE
            if op in COMPARE:
                return BOOLEAN_TYPE

    # Real arithmetic
    if isinstance(left_type, RealType) and isinstance(right_type, RealType):
        if op in ('PLUS', 'MINUS', 'MUL', 'SLASH'):
            return REAL_TYPE
        if op in COMPARE:
            return BOOLEAN_TYPE

    # INTEGER op REAL (mixed arithmetic widens to REAL)
    if (isinstance(left_type, IntegerType) and isinstance(right_type, RealType)) or \
       (isinstance(left_type, RealType) and isinstance(right_type, IntegerType)):
        if op in ('PLUS', 'MINUS', 'MUL', 'SLASH'):
            return REAL_TYPE
        if op in COMPARE:
            return BOOLEAN_TYPE

    # WORD arithmetic / bitwise / comparison (16-bit unsigned)
    if isinstance(left_type, WordType) and isinstance(right_type, WordType):
        if op in ARITH or op in BITWISE:
            return WORD_TYPE
        if op in COMPARE:
            return BOOLEAN_TYPE

    # WORD mixed with INTEGER -> widens to INTEGER
    if (isinstance(left_type, WordType) and isinstance(right_type, IntegerType)) or \
       (isinstance(left_type, IntegerType) and isinstance(right_type, WordType)):
        if op in ARITH or op in BITWISE:
            return INTEGER_TYPE
        if op in COMPARE:
            return BOOLEAN_TYPE

    # Character comparison
    if isinstance(left_type, CharType) and isinstance(right_type, CharType):
        if op in COMPARE:
            return BOOLEAN_TYPE

    # Enum comparison (same enum type only)
    if isinstance(left_type, EnumType) and isinstance(right_type, EnumType):
        if left_type.equivalent_to(right_type) and op in COMPARE:
            return BOOLEAN_TYPE

    # Set operators
    if isinstance(left_type, SetType) and isinstance(right_type, SetType):
        if left_type.element_type.equivalent_to(right_type.element_type):
            if op in {'PLUS', 'MINUS', 'MUL'}:
                return left_type
            if op in {'EQ', 'NEQ', 'LE', 'GE', 'LT', 'GT'}:
                return BOOLEAN_TYPE

    # Membership: ordinal IN set
    if op == 'IN' and isinstance(right_type, SetType):
        if can_assign(left_type, right_type.element_type) or can_assign(right_type.element_type, left_type):
            return BOOLEAN_TYPE

    # Pointer identity: two pointers (including NIL, which carries a pointer
    # type) may be compared for equality. PointerType.equivalent_to already
    # treats the generic POINTER flavor as compatible with any pointer, so
    # `p = NIL`, `p <> NIL`, and `p = q` all type-check to BOOLEAN.
    if isinstance(left_type, PointerType) and isinstance(right_type, PointerType):
        if op in ('EQ', 'NEQ') and left_type.equivalent_to(right_type):
            return BOOLEAN_TYPE

    return None


def unary_op_result_type(operand_type: Type, op: str) -> Optional[Type]:
    """
    Determine the result type of a unary operation.

    `op` is the AST/token-kind name: NOT, MINUS, PLUS.
    Returns None if the operation is invalid for this type.
    """
    if op == 'NOT':
        if isinstance(operand_type, BooleanType):
            return BOOLEAN_TYPE
        if isinstance(operand_type, (IntegerType, Integer32Type, Integer64Type, WordType)):
            return operand_type  # bitwise complement

    if op in ('PLUS', 'MINUS'):
        if isinstance(operand_type, (IntegerType, Integer32Type, Integer64Type)):
            return operand_type
        if isinstance(operand_type, RealType):
            return REAL_TYPE
        if isinstance(operand_type, Real32Type):
            return REAL32_TYPE
        if isinstance(operand_type, WordType):
            return WORD_TYPE

    return None
