"""
AST node definitions for Pascal parser.
Each dataclass represents a construct in the Pascal grammar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Union


# Base class for all AST nodes
@dataclass
class ASTNode:
    """Base class for all AST nodes."""
    pass


# NOTE: Source location tracking (filename, line, column) will be added later
# when we update the lexer/parser to track positions. For now, we use None.

# ============================================================================
# Program/Module Units
# ============================================================================


@dataclass
class ProgramUnit(ASTNode):
    name: str
    params: List[str]  # parameter names
    uses: List[UseClause]
    block: Block


@dataclass
class ModuleUnit(ASTNode):
    name: str
    uses: List[UseClause]
    decls: List[Declaration]


@dataclass
class InterfaceUnit(ASTNode):
    name: str
    params: List[str]
    uses: List[UseClause]
    decls: List[Declaration]


@dataclass
class ImplementationUnit(ASTNode):
    name: str
    uses: List[UseClause]
    decls: List[Declaration]
    init_body: Optional[List[Statement]]  # None if no BEGIN..END
    interface: Optional[InterfaceUnit] = None


# ============================================================================
# Blocks and Declarations
# ============================================================================


@dataclass
class Block(ASTNode):
    decls: List[Declaration]
    body: List[Statement]


Declaration = Union['ConstDecl', 'TypeDecl', 'VarDecl', 'ValueDecl', 'LabelDecl', 'ProcDecl', 'FuncDecl']


@dataclass
class ConstDecl(ASTNode):
    name: str
    value: Expression


@dataclass
class TypeDecl(ASTNode):
    name: str
    type_expr: Type


@dataclass
class VarDecl(ASTNode):
    names: List[str]
    type_expr: Type
    attributes: List[str]  # e.g., ['READONLY', 'STATIC']


@dataclass
class ValueDecl(ASTNode):
    name: str
    value: Expression


@dataclass
class LabelDecl(ASTNode):
    labels: List[Union[int, str]]  # label ids (int or identifier name)


@dataclass
class ProcDecl(ASTNode):
    name: str
    params: List[Param]
    attributes: List[str]
    body: Optional[Block]  # None if EXTERN/FORWARD/EXTERNAL


@dataclass
class FuncDecl(ASTNode):
    name: str
    params: List[Param]
    return_type: Type
    attributes: List[str]
    body: Optional[Block]  # None if EXTERN/FORWARD/EXTERNAL


@dataclass
class Param(ASTNode):
    mode: Optional[str]  # 'VAR', 'CONST', 'VARS', 'CONSTS', or None
    names: List[str]
    type_expr: Type


# ============================================================================
# Statements
# ============================================================================

Statement = Union['CompoundStmt', 'AssignStmt', 'ProcCallStmt', 'IfStmt', 'ForStmt', 'WhileStmt', 'RepeatStmt', 'CaseStmt', 'WithStmt', 'GotoStmt', 'ReturnStmt', 'BreakStmt',
                  'CycleStmt', 'LabelStmt', 'EmptyStmt']


@dataclass
class CompoundStmt(ASTNode):
    stmts: List[Statement]


@dataclass
class AssignStmt(ASTNode):
    target: Designator
    expr: Expression


@dataclass
class WriteArg(ASTNode):
    expr: Expression
    width: Optional[Expression] = None
    precision: Optional[Expression] = None


@dataclass
class ProcCallStmt(ASTNode):
    name: str
    args: List[Union[Expression, WriteArg]]


@dataclass
class IfStmt(ASTNode):
    cond: Expression
    then_branch: Statement
    else_branch: Optional[Statement]


@dataclass
class ForStmt(ASTNode):
    var: str
    start: Expression
    end: Expression
    direction: str  # 'TO' or 'DOWNTO'
    body: Statement


@dataclass
class WhileStmt(ASTNode):
    cond: Expression
    body: Statement


@dataclass
class RepeatStmt(ASTNode):
    body: List[Statement]
    cond: Expression


@dataclass
class CaseStmt(ASTNode):
    expr: Expression
    elements: List[CaseElement]
    otherwise: Optional[Statement]


@dataclass
class CaseElement(ASTNode):
    constants: List[Expression]  # can be ranges via RangeExpr
    stmt: Statement


@dataclass
class WithStmt(ASTNode):
    targets: List[Designator]
    body: Statement


@dataclass
class GotoStmt(ASTNode):
    label: Union[int, str]


@dataclass
class ReturnStmt(ASTNode):
    pass


@dataclass
class BreakStmt(ASTNode):
    pass


@dataclass
class CycleStmt(ASTNode):
    pass


@dataclass
class LabelStmt(ASTNode):
    label: Union[int, str]
    stmt: Statement


@dataclass
class EmptyStmt(ASTNode):
    pass


# ============================================================================
# Expressions
# ============================================================================

Expression = Union['BinOp', 'UnaryOp', 'IntLiteral', 'RealLiteral', 'CharLiteral', 'StringLiteral', 'BoolLiteral', 'NilLiteral', 'Identifier', 'Designator', 'FuncCall', 'SetConstructor',
                   'AdrExpr', 'SizeofExpr', 'UpperExpr', 'RangeExpr']


@dataclass
class BinOp(ASTNode):
    op: str
    left: Expression
    right: Expression


@dataclass
class UnaryOp(ASTNode):
    op: str
    operand: Expression


@dataclass
class IntLiteral(ASTNode):
    value: int


@dataclass
class RealLiteral(ASTNode):
    value: float


@dataclass
class CharLiteral(ASTNode):
    value: str


@dataclass
class StringLiteral(ASTNode):
    value: str


@dataclass
class BoolLiteral(ASTNode):
    value: bool


@dataclass
class NilLiteral(ASTNode):
    pass


@dataclass
class Identifier(ASTNode):
    name: str


@dataclass
class Designator(ASTNode):
    name: str
    selectors: List[Selector]


@dataclass
class FuncCall(ASTNode):
    name: str
    args: List[Expression]


@dataclass
class SetConstructor(ASTNode):
    elements: List[Expression]  # can include RangeExpr for ranges


@dataclass
class AdrExpr(ASTNode):
    name: str


@dataclass
class SizeofExpr(ASTNode):
    target: Union[str, Type]  # identifier name or type


@dataclass
class UpperExpr(ASTNode):
    name: str


@dataclass
class RangeExpr(ASTNode):
    low: Expression
    high: Expression


# ============================================================================
# Types
# ============================================================================

Type = Union['NamedType', 'ArrayType', 'RecordType', 'SetType', 'FileType', 'EnumType', 'PointerType', 'LStringType', 'BuiltinType', 'SubrangeType']


@dataclass
class NamedType(ASTNode):
    name: str
    param: Optional[Union[int, str]]  # e.g., IDENTIFIER(limit)


@dataclass
class ArrayType(ASTNode):
    index_range: IndexRange
    element_type: Type
    packed: bool
    super: bool


@dataclass
class RecordType(ASTNode):
    fields: List[tuple[List[str], Type]]  # list of (name_list, type) pairs
    packed: bool


@dataclass
class SetType(ASTNode):
    base: Type


@dataclass
class FileType(ASTNode):
    element_type: Type


@dataclass
class EnumType(ASTNode):
    values: List[str]


@dataclass
class PointerType(ASTNode):
    base: Type


@dataclass
class LStringType(ASTNode):
    max_len: int


@dataclass
class BuiltinType(ASTNode):
    name: str  # INTEGER, REAL, BOOLEAN, CHAR, WORD, ADRMEM


@dataclass
class IndexRange(ASTNode):
    low: Expression
    high: Optional[Expression]  # None for super arrays (star)


@dataclass
class SubrangeType(ASTNode):
    """An ordinal subrange type, e.g. `1..10` or `'A'..'Z'`. Used as a set base
    (`SET OF 1..10`) and anywhere a subrange may appear. Preserves both bounds;
    `host` records the underlying ordinal type the bounds belong to
    ('INTEGER', 'CHAR', 'BOOLEAN', or a named/enum type) when it can be
    determined from the bound literals, else None."""
    low: Expression
    high: Expression
    host: Optional[str] = None


@dataclass
class Selector(ASTNode):
    kind: str  # 'INDEX', 'FIELD', 'DEREF'
    index_or_field: Optional[Union[Expression, str]]  # expr for INDEX, str for FIELD, None for DEREF


@dataclass
class UseClause(ASTNode):
    name: str
    imports: Optional[List[str]]  # None if no import list, else list of imported names
