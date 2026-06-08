"""
Centralized registry of predeclared/built-in identifiers in IBM Pascal 2.0.

This is shared between the type checker and code generator to prevent
"registered but no codegen" traps.
"""

from symbol_table import Symbol
from type_system import (
    INTEGER_TYPE, REAL_TYPE, CHAR_TYPE, BOOLEAN_TYPE, WORD_TYPE,
    ProcedureType, FunctionType, FileType, StringType, LStringType, PointerType
)

# Lists of all built-in function and procedure names
BUILTIN_FUNCTIONS = {
    'ABS', 'SQR', 'SQRT', 'SIN', 'COS', 'LN', 'EXP', 'ARCTAN',
    'CHR', 'ORD', 'ODD', 'SUCC', 'PRED', 'HIBYTE', 'LOBYTE',
    'WRD', 'BYWORD', 'TRUNC', 'ROUND', 'FLOAT'
}

BUILTIN_PROCEDURES = {
    'WRITE', 'WRITELN', 'READLN',
    'CONCAT', 'COPYLST', 'COPYSTR',
    'PACK', 'UNPACK', 'FILLC', 'FILLSC', 'MOVEL', 'MOVER', 'MOVESL', 'MOVESR'
}

def register_builtins(symbol_table) -> None:
    """Define built-in procedures, functions, constants, and types in the global scope."""
    def define_builtin(name: str, symbol_type, kind: str):
        symbol_table.define(name, Symbol(name=name, type=symbol_type, kind=kind, is_mutable=False, is_builtin=True))

    # Procedures
    define_builtin('WRITELN', ProcedureType('WRITELN', []), 'procedure')
    define_builtin('WRITE', ProcedureType('WRITE', []), 'procedure')
    define_builtin('READLN', ProcedureType('READLN', []), 'procedure')
    define_builtin('CONCAT', ProcedureType('CONCAT', []), 'procedure')
    define_builtin('COPYLST', ProcedureType('COPYLST', []), 'procedure')
    define_builtin('COPYSTR', ProcedureType('COPYSTR', []), 'procedure')
    define_builtin('PACK', ProcedureType('PACK', []), 'procedure')
    define_builtin('UNPACK', ProcedureType('UNPACK', []), 'procedure')
    fill_proc = ProcedureType('FILLC', [('loc', PointerType(CHAR_TYPE)), ('len', WORD_TYPE), ('val', CHAR_TYPE)])
    define_builtin('FILLC', fill_proc, 'procedure')
    define_builtin('FILLSC', ProcedureType('FILLSC', [('loc', PointerType(CHAR_TYPE)), ('len', WORD_TYPE), ('val', CHAR_TYPE)]), 'procedure')
    define_builtin('MOVEL', ProcedureType('MOVEL', [('src', PointerType(CHAR_TYPE)), ('dst', PointerType(CHAR_TYPE)), ('len', WORD_TYPE)]), 'procedure')
    define_builtin('MOVER', ProcedureType('MOVER', [('src', PointerType(CHAR_TYPE)), ('dst', PointerType(CHAR_TYPE)), ('len', WORD_TYPE)]), 'procedure')
    define_builtin('MOVESL', ProcedureType('MOVESL', [('src', PointerType(CHAR_TYPE)), ('dst', PointerType(CHAR_TYPE)), ('len', WORD_TYPE)]), 'procedure')
    define_builtin('MOVESR', ProcedureType('MOVESR', [('src', PointerType(CHAR_TYPE)), ('dst', PointerType(CHAR_TYPE)), ('len', WORD_TYPE)]), 'procedure')

    # Constants
    define_builtin('MAXINT', INTEGER_TYPE, 'const')
    define_builtin('MAXWORD', WORD_TYPE, 'const')
    define_builtin('NULL', LStringType(0), 'const')

    # Types
    text_type = FileType(CHAR_TYPE)
    define_builtin('TEXT', text_type, 'type')
    define_builtin('STRING', StringType(256), 'type')

    # Variables/Files
    define_builtin('INPUT', text_type, 'var')
    define_builtin('OUTPUT', text_type, 'var')

    # Mathematical functions (registered!)
    define_builtin('ABS', FunctionType('ABS', [('x', REAL_TYPE)], REAL_TYPE), 'function')
    define_builtin('SQR', FunctionType('SQR', [('x', REAL_TYPE)], REAL_TYPE), 'function')
    for math_fn in ('SQRT', 'SIN', 'COS', 'LN', 'EXP', 'ARCTAN'):
        define_builtin(math_fn, FunctionType(math_fn, [('x', REAL_TYPE)], REAL_TYPE), 'function')

    # Ordinal functions
    define_builtin('CHR', FunctionType('CHR', [('n', INTEGER_TYPE)], CHAR_TYPE), 'function')
    define_builtin('ORD', FunctionType('ORD', [('c', CHAR_TYPE)], INTEGER_TYPE), 'function')
    define_builtin('ODD', FunctionType('ODD', [('n', INTEGER_TYPE)], BOOLEAN_TYPE), 'function')
    define_builtin('SUCC', FunctionType('SUCC', [('n', INTEGER_TYPE)], INTEGER_TYPE), 'function')
    define_builtin('PRED', FunctionType('PRED', [('n', INTEGER_TYPE)], INTEGER_TYPE), 'function')

    # Word/Byte functions
    define_builtin('HIBYTE', FunctionType('HIBYTE', [('n', INTEGER_TYPE)], CHAR_TYPE), 'function')
    define_builtin('LOBYTE', FunctionType('LOBYTE', [('n', INTEGER_TYPE)], CHAR_TYPE), 'function')
    define_builtin('WRD', FunctionType('WRD', [('x', INTEGER_TYPE)], WORD_TYPE), 'function')
    define_builtin('BYWORD', FunctionType('BYWORD', [('hi', CHAR_TYPE), ('lo', CHAR_TYPE)], WORD_TYPE), 'function')

    # Conversion functions
    define_builtin('TRUNC', FunctionType('TRUNC', [('x', REAL_TYPE)], INTEGER_TYPE), 'function')
    define_builtin('ROUND', FunctionType('ROUND', [('x', REAL_TYPE)], INTEGER_TYPE), 'function')
    define_builtin('FLOAT', FunctionType('FLOAT', [('x', INTEGER_TYPE)], REAL_TYPE), 'function')
