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
    """The REAL type (floating point)."""

    def __str__(self) -> str:
        return "REAL"

    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, RealType)


class WordType(Type):
    """The WORD type (16-bit unsigned)."""

    def __str__(self) -> str:
        return "WORD"

    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, WordType)


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

    def __str__(self) -> str:
        prefix = {'ADR': 'ADR OF ', 'ADS': 'ADS OF '}.get(self.flavor, '^')
        return f"{prefix}{self.target_type}"

    def equivalent_to(self, other: Type) -> bool:
        if not isinstance(other, PointerType):
            return False
        return self.flavor == other.flavor or self.flavor == 'POINTER' or other.flavor == 'POINTER'


@dataclass
class ProcedureType(Type):
    """Procedure type (has parameters, no return value)."""

    name: str
    params: List[Tuple[str, Type]]  # List of (param_name, param_type)

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
WORD_TYPE = WordType()
CHAR_TYPE = CharType()


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
    if isinstance(from_type, IntegerType) and isinstance(to_type, WordType):
        return True
    if isinstance(from_type, IntegerType) and isinstance(to_type, (Integer32Type, Integer64Type)):
        return True
    if isinstance(from_type, Integer32Type) and isinstance(to_type, Integer64Type):
        return True
    if isinstance(from_type, WordType) and isinstance(to_type, (Integer32Type, Integer64Type)):
        return True
    if isinstance(from_type, SetType) and isinstance(to_type, SetType):
        return from_type.element_type.equivalent_to(to_type.element_type)
    if isinstance(from_type, (StringType, LStringType)) and isinstance(to_type, (StringType, LStringType)):
        return from_type.max_len <= to_type.max_len
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

    # Wide signed integer extension family.
    int_rank = {IntegerType: 0, WordType: 0, Integer32Type: 1, Integer64Type: 2}
    if type(left_type) in int_rank and type(right_type) in int_rank:
        if op == 'SLASH':
            return REAL_TYPE
        if op in ARITH or op in BITWISE:
            rank = max(int_rank[type(left_type)], int_rank[type(right_type)])
            if rank == 2:
                return INTEGER64_TYPE
            if rank == 1:
                return INTEGER32_TYPE
            if isinstance(left_type, WordType) and isinstance(right_type, WordType):
                return WORD_TYPE
            return INTEGER_TYPE
        if op in COMPARE:
            return BOOLEAN_TYPE

    # Boolean logic
    if isinstance(left_type, BooleanType) and isinstance(right_type, BooleanType):
        if op in BITWISE or op in SHORT_CIRCUIT:
            return BOOLEAN_TYPE
        if op in ('EQ', 'NEQ'):
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
        if isinstance(operand_type, WordType):
            return WORD_TYPE

    return None
