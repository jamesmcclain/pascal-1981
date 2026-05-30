"""
Type System for Pascal-1981 Compiler

Defines a type hierarchy for representing Pascal types at compile time.
This is used by both the type checker and code generator.
"""

from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from abc import ABC, abstractmethod


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
    """The INTEGER type (32-bit signed)."""
    
    def __str__(self) -> str:
        return "INTEGER"
    
    def equivalent_to(self, other: Type) -> bool:
        return isinstance(other, IntegerType)


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
class ArrayType(Type):
    """Array type: ARRAY[lower..upper] OF element_type."""
    
    element_type: Type
    lower_bound: int
    upper_bound: int
    
    def __str__(self) -> str:
        return f"ARRAY[{self.lower_bound}..{self.upper_bound}] OF {self.element_type}"
    
    def equivalent_to(self, other: Type) -> bool:
        if not isinstance(other, ArrayType):
            return False
        return (self.element_type.equivalent_to(other.element_type) and
                self.lower_bound == other.lower_bound and
                self.upper_bound == other.upper_bound)


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
        if set(self.fields.keys()) != set(other.fields.keys()):
            return False
        for field_name in self.fields:
            if not self.fields[field_name].equivalent_to(other.fields[field_name]):
                return False
        return True
    
    def get_field_type(self, field_name: str) -> Optional[Type]:
        """Get the type of a field by name."""
        return self.fields.get(field_name)


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
class PointerType(Type):
    """Pointer type: ^target_type."""
    
    target_type: Type
    
    def __str__(self) -> str:
        return f"^{self.target_type}"
    
    def equivalent_to(self, other: Type) -> bool:
        if not isinstance(other, PointerType):
            return False
        return self.target_type.equivalent_to(other.target_type)


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
BOOLEAN_TYPE = BooleanType()
REAL_TYPE = RealType()
WORD_TYPE = WordType()
CHAR_TYPE = CharType()


# Type coercion rules
def can_assign(from_type: Type, to_type: Type) -> bool:
    """
    Check if a value of from_type can be assigned to a variable of to_type.
    
    Pascal has strict typing: no implicit coercion.
    Both types must be equivalent.
    """
    return from_type.equivalent_to(to_type)


def binary_op_result_type(left_type: Type, op: str, right_type: Type) -> Optional[Type]:
    """
    Determine the result type of a binary operation.
    
    Returns None if the operation is invalid for these types.
    """
    # Integer arithmetic
    if isinstance(left_type, IntegerType) and isinstance(right_type, IntegerType):
        if op in ['+', '-', '*', '/', 'DIV', 'MOD']:
            # DIV and MOD return INTEGER, / returns REAL (in some Pascal dialects)
            if op == '/':
                return REAL_TYPE
            return INTEGER_TYPE
        if op in ['=', '<>', '<', '<=', '>', '>=']:
            return BOOLEAN_TYPE
    
    # Boolean logic
    if isinstance(left_type, BooleanType) and isinstance(right_type, BooleanType):
        if op in ['AND', 'OR', 'XOR']:
            return BOOLEAN_TYPE
        if op in ['=', '<>']:
            return BOOLEAN_TYPE
    
    # Real arithmetic
    if isinstance(left_type, RealType) and isinstance(right_type, RealType):
        if op in ['+', '-', '*', '/']:
            return REAL_TYPE
        if op in ['=', '<>', '<', '<=', '>', '>=']:
            return BOOLEAN_TYPE
    
    # INTEGER op REAL (some dialects allow this)
    if (isinstance(left_type, IntegerType) and isinstance(right_type, RealType)) or \
       (isinstance(left_type, RealType) and isinstance(right_type, IntegerType)):
        if op in ['+', '-', '*', '/']:
            return REAL_TYPE
        if op in ['=', '<>', '<', '<=', '>', '>=']:
            return BOOLEAN_TYPE
    
    # String/character operations
    if isinstance(left_type, CharType) and isinstance(right_type, CharType):
        if op in ['=', '<>', '<', '<=', '>', '>=']:
            return BOOLEAN_TYPE
    
    return None


def unary_op_result_type(operand_type: Type, op: str) -> Optional[Type]:
    """
    Determine the result type of a unary operation.
    
    Returns None if the operation is invalid for this type.
    """
    if op == 'NOT':
        if isinstance(operand_type, BooleanType):
            return BOOLEAN_TYPE
    
    if op in ['+', '-']:
        if isinstance(operand_type, IntegerType):
            return INTEGER_TYPE
        if isinstance(operand_type, RealType):
            return REAL_TYPE
    
    return None
