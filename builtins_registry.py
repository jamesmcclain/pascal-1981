"""
Centralized registry of predeclared/built-in identifiers in IBM Pascal 2.0.

This is shared between the type checker and code generator to prevent
"registered but no codegen" traps.
"""

from symbol_table import Symbol
from type_system import (BOOLEAN_TYPE, CHAR_TYPE, INTEGER_TYPE, REAL_TYPE, WORD_TYPE, FileType, FunctionType, LStringType, PointerType, ProcedureType, StringType)

# Lists of all built-in function and procedure names
BUILTIN_FUNCTIONS = {'ABS', 'SQR', 'SQRT', 'SIN', 'COS', 'LN', 'EXP', 'ARCTAN', 'CHR', 'ORD', 'ODD', 'SUCC', 'PRED', 'HIBYTE', 'LOBYTE', 'WRD', 'BYWORD', 'TRUNC', 'ROUND', 'FLOAT', 'SCANEQ', 'SCANNE', 'ENCODE', 'DECODE'}

BUILTIN_PROCEDURES = {'WRITE', 'WRITELN', 'READLN', 'CONCAT', 'COPYLST', 'COPYSTR', 'INSERT', 'DELETE', 'POSITN', 'PACK', 'UNPACK', 'NEW', 'DISPOSE', 'FILLC', 'FILLSC', 'MOVEL', 'MOVER', 'MOVESL', 'MOVESR', 'ABORT'}


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
    define_builtin('INSERT', ProcedureType('INSERT', []), 'procedure')
    define_builtin('DELETE', ProcedureType('DELETE', []), 'procedure')
    define_builtin('POSITN', FunctionType('POSITN', [], INTEGER_TYPE), 'function')
    define_builtin('SCANEQ', FunctionType('SCANEQ', [], INTEGER_TYPE), 'function')
    define_builtin('SCANNE', FunctionType('SCANNE', [], INTEGER_TYPE), 'function')
    define_builtin('ENCODE', FunctionType('ENCODE', [], BOOLEAN_TYPE), 'function')
    define_builtin('DECODE', FunctionType('DECODE', [], BOOLEAN_TYPE), 'function')
    define_builtin('PACK', ProcedureType('PACK', []), 'procedure')
    define_builtin('UNPACK', ProcedureType('UNPACK', []), 'procedure')
    define_builtin('NEW', ProcedureType('NEW', []), 'procedure')
    define_builtin('DISPOSE', ProcedureType('DISPOSE', []), 'procedure')
    fill_proc = ProcedureType('FILLC', [('loc', PointerType(CHAR_TYPE)), ('len', WORD_TYPE), ('val', CHAR_TYPE)])
    define_builtin('FILLC', fill_proc, 'procedure')
    # FILLSC/MOVESL/MOVESR are the SEGMENTED-address siblings of FILLC/MOVEL/
    # MOVER (manual: "the corresponding segmented address versions ... declared
    # with ADSMEM instead of ADRMEM parameters"), NOT short-count variants.
    ADSMEM = PointerType(CHAR_TYPE, flavor='ADS')
    define_builtin('FILLSC', ProcedureType('FILLSC', [('loc', ADSMEM), ('len', WORD_TYPE), ('val', CHAR_TYPE)]), 'procedure')
    define_builtin('MOVEL', ProcedureType('MOVEL', [('src', PointerType(CHAR_TYPE)), ('dst', PointerType(CHAR_TYPE)), ('len', WORD_TYPE)]), 'procedure')
    define_builtin('MOVER', ProcedureType('MOVER', [('src', PointerType(CHAR_TYPE)), ('dst', PointerType(CHAR_TYPE)), ('len', WORD_TYPE)]), 'procedure')
    define_builtin('MOVESL', ProcedureType('MOVESL', [('src', ADSMEM), ('dst', ADSMEM), ('len', WORD_TYPE)]), 'procedure')
    define_builtin('MOVESR', ProcedureType('MOVESR', [('src', ADSMEM), ('dst', ADSMEM), ('len', WORD_TYPE)]), 'procedure')
    # ABORT(CONST STRING, WORD, WORD): error message, error code, STATUS word.
    define_builtin('ABORT', ProcedureType('ABORT', [('msg', StringType(255)), ('code', WORD_TYPE), ('status', WORD_TYPE)]), 'procedure')

    # Constants
    define_builtin('MAXINT', INTEGER_TYPE, 'const')
    define_builtin('MAXWORD', WORD_TYPE, 'const')
    define_builtin('NULL', LStringType(0), 'const')

    # Types
    text_type = FileType(CHAR_TYPE, structure='ASCII')
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
