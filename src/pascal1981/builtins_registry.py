"""
Centralized registry of predeclared/built-in identifiers in IBM Pascal 2.0.

This is shared between the type checker and code generator to prevent
"registered but no codegen" traps.
"""

from .symbol_table import Symbol
from .features import is_extended
from .type_system import (BOOLEAN_TYPE, CHAR_TYPE, INTEGER32_TYPE, INTEGER64_TYPE, INTEGER_TYPE, REAL_TYPE, WORD_TYPE, EnumType, FileType, FunctionType, LStringType, PointerType,
                          ProcedureType, RecordType, StringType)

# Lists of all built-in function and procedure names
DEVICE_INDEX_BUILTIN_FUNCTIONS = {
    'THREADIDX_X',
    'THREADIDX_Y',
    'THREADIDX_Z',
    'BLOCKIDX_X',
    'BLOCKIDX_Y',
    'BLOCKIDX_Z',
    'BLOCKDIM_X',
    'BLOCKDIM_Y',
    'BLOCKDIM_Z',
    'GRIDDIM_X',
    'GRIDDIM_Y',
    'GRIDDIM_Z',
}

# Host-side device orchestration (Milestone D): allocate / copy / launch / free.
# These are HOST-only -- the type checker rejects them inside DEVICE code, where
# orchestration has no meaning. DEVALLOC is a function (returns an opaque ADRMEM
# handle); the rest are procedures. LAUNCH is variadic and checked specially.
DEVICE_ORCHESTRATION_BUILTIN_FUNCTIONS = {'DEVALLOC'}
DEVICE_ORCHESTRATION_BUILTIN_PROCEDURES = {'DEVCOPYTO', 'DEVCOPYFROM', 'DEVFREE', 'LAUNCH'}

BUILTIN_FUNCTIONS = {
    'ABS', 'SQR', 'SQRT', 'SIN', 'COS', 'LN', 'EXP', 'ARCTAN', 'CHR', 'ORD', 'ODD', 'SUCC', 'PRED', 'HIBYTE', 'LOBYTE', 'WRD', 'BYWORD', 'TRUNC', 'ROUND', 'FLOAT', 'SCANEQ',
    'SCANNE', 'ENCODE', 'DECODE', 'EOF', 'EOLN', *DEVICE_INDEX_BUILTIN_FUNCTIONS, *DEVICE_ORCHESTRATION_BUILTIN_FUNCTIONS
}

DEVICE_SYNC_BUILTIN_PROCEDURES = {'SYNCTHREADS'}

# C-ABI fixed-width type aliases (Phase 1 of the C-FFI plan,
# docs/c-abi-foreign-functions.md).  These give foreign `[C]` declarations exact
# C widths independent of the vintage 16-bit INTEGER, so a programmer can spell
# `CINT` for C `int` instead of mis-mapping it to INTEGER.  The widths follow the
# LP64 / System V AMD64 model -- the only host target exercised today.  On a
# future LLP64 target (Windows AMD64) CLONG would need to remain 32-bit; that is
# deliberately a per-target concern, tracked with the rest of the ABI work.
#
# Each alias maps to an underlying built-in *type name* that both the type
# checker (via the resolved-type table below) and codegen (via NamedType
# seeding) already understand, so no new lowering is required: the aliases reuse
# the existing INTEGER32/INTEGER64/CHAR/REAL/ADRMEM machinery.  Registered as
# predeclared TYPE symbols; a user TYPE/VAR of the same name still shadows them.
C_ABI_TYPE_ALIASES = {
    'CCHAR': 'CHAR',         # C char        -> i8
    'CSHORT': 'INTEGER',     # C short       -> i16
    'CINT': 'INTEGER32',     # C int         -> i32
    'CLONG': 'INTEGER64',    # C long (LP64) -> i64
    'CSIZE_T': 'INTEGER64',  # C size_t      -> i64
    'CDOUBLE': 'REAL',       # C double      -> f64
    'CPTR': 'ADRMEM',        # C void*       -> i8*
}

BUILTIN_PROCEDURES = {
    'WRITE', 'WRITELN', 'READ', 'READLN', 'RESET', 'REWRITE', 'GET', 'PUT', 'ASSIGN', 'CLOSE', 'DISCARD', 'READFN', 'READSET', 'CONCAT', 'COPYLST', 'COPYSTR', 'INSERT', 'DELETE',
    'POSITN', 'PACK', 'UNPACK', 'NEW', 'DISPOSE', 'FILLC', 'FILLSC', 'MOVEL', 'MOVER', 'MOVESL', 'MOVESR', 'ABORT', *DEVICE_SYNC_BUILTIN_PROCEDURES, *DEVICE_ORCHESTRATION_BUILTIN_PROCEDURES
}


def register_builtins(symbol_table, features=None) -> None:
    """Define built-in procedures, functions, constants, and types in the global scope."""

    def define_builtin(name: str, symbol_type, kind: str):
        symbol_table.define(name, Symbol(name=name, type=symbol_type, kind=kind, is_mutable=False, is_builtin=True))

    # Procedures
    define_builtin('WRITELN', ProcedureType('WRITELN', []), 'procedure')
    define_builtin('WRITE', ProcedureType('WRITE', []), 'procedure')
    text_file_param = [('f', FileType(CHAR_TYPE, structure='ASCII'))]
    define_builtin('READ', ProcedureType('READ', []), 'procedure')
    define_builtin('READLN', ProcedureType('READLN', []), 'procedure')
    define_builtin('RESET', ProcedureType('RESET', text_file_param), 'procedure')
    define_builtin('REWRITE', ProcedureType('REWRITE', text_file_param), 'procedure')
    define_builtin('GET', ProcedureType('GET', text_file_param), 'procedure')
    define_builtin('PUT', ProcedureType('PUT', text_file_param), 'procedure')
    define_builtin('ASSIGN', ProcedureType('ASSIGN', []), 'procedure')
    define_builtin('CLOSE', ProcedureType('CLOSE', text_file_param), 'procedure')
    define_builtin('DISCARD', ProcedureType('DISCARD', text_file_param), 'procedure')
    define_builtin('READFN', ProcedureType('READFN', []), 'procedure')
    define_builtin('READSET', ProcedureType('READSET', []), 'procedure')
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
    define_builtin('EOF', FunctionType('EOF', [], BOOLEAN_TYPE), 'function')
    define_builtin('EOLN', FunctionType('EOLN', [], BOOLEAN_TYPE), 'function')
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

    # Device host-orchestration surface (Milestone D, cuda-kernel-prescription
    # §5/§7).  Registered globally so they are recognized as builtins; the type
    # checker rejects their use *inside* DEVICE code and validates argument
    # shapes specially (LAUNCH is variadic).  The opaque device handle is an
    # ADRMEM (generic address); the host holds it but never dereferences it.
    ADRMEM = PointerType(CHAR_TYPE)
    define_builtin('DEVALLOC', FunctionType('DEVALLOC', [('nbytes', INTEGER_TYPE)], ADRMEM), 'function')
    define_builtin('DEVCOPYTO', ProcedureType('DEVCOPYTO', [('dev', ADRMEM), ('src', ADRMEM), ('nbytes', INTEGER_TYPE)]), 'procedure')
    define_builtin('DEVCOPYFROM', ProcedureType('DEVCOPYFROM', [('dst', ADRMEM), ('dev', ADRMEM), ('nbytes', INTEGER_TYPE)]), 'procedure')
    define_builtin('DEVFREE', ProcedureType('DEVFREE', [('dev', ADRMEM)]), 'procedure')
    define_builtin('LAUNCH', ProcedureType('LAUNCH', []), 'procedure')

    # Device synchronization procedures.  Registered globally because the registry
    # runs before compiland device-ness is known; the type checker rejects use
    # outside DEVICE source code.
    for _name in sorted(DEVICE_SYNC_BUILTIN_PROCEDURES):
        define_builtin(_name, ProcedureType(_name, []), 'procedure')

    # Device parallel-index functions.  Registered globally because the registry
    # runs before compiland device-ness is known; the type checker rejects use
    # outside DEVICE source code.
    for _name in sorted(DEVICE_INDEX_BUILTIN_FUNCTIONS):
        define_builtin(_name, FunctionType(_name, [], INTEGER32_TYPE), 'function')

    # Constants
    define_builtin('MAXINT', INTEGER_TYPE, 'const')
    define_builtin('MAXWORD', WORD_TYPE, 'const')
    if features and features.get('wide-integers', False):
        define_builtin('MAXINT32', INTEGER32_TYPE, 'const')
        define_builtin('MAXINT64', INTEGER64_TYPE, 'const')
    define_builtin('NULL', LStringType(0), 'const')
    filemodes_type = EnumType(['SEQUENTIAL', 'TERMINAL', 'DIRECT'], name='FILEMODES')
    define_builtin('SEQUENTIAL', filemodes_type, 'const')
    define_builtin('TERMINAL', filemodes_type, 'const')
    define_builtin('DIRECT', filemodes_type, 'const')
    # Predeclared SPACE enum (address-space constants). Registered
    # unconditionally; its *meaning* is gated by module kind in the checker
    # (ads-memory-spaces-design.md S3.1), so it is inert outside DEVICE MODULEs.
    # Ordinals follow list order: HOST=0, GLOBAL=1, SHARED=2, CONSTANT=3, LOCAL=4.
    space_type = EnumType(['HOST', 'GLOBAL', 'SHARED', 'CONSTANT', 'LOCAL'], name='SPACE')
    for _space_member in space_type.members:
        define_builtin(_space_member, space_type, 'const')

    # Types
    text_type = FileType(CHAR_TYPE, structure='ASCII')
    define_builtin('TEXT', text_type, 'type')
    define_builtin('STRING', StringType(256), 'type')
    define_builtin('FILEMODES', filemodes_type, 'type')
    define_builtin('SPACE', space_type, 'type')
    define_builtin('FCBFQQ', RecordType('FCBFQQ', {'MODE': filemodes_type, 'TRAP': BOOLEAN_TYPE, 'ERRS': INTEGER_TYPE}), 'type')

    # C-ABI fixed-width type aliases (Phase 1 of the C-FFI plan).  These are part
    # of the C-FFI surface and are therefore registered only under the extended
    # dialect (see features.is_extended): the wide widths they name -- INTEGER32,
    # INTEGER64 -- are themselves extended types, so the interface and the widths
    # it needs become available together.  Under the faithful 1981 dialect these
    # identifiers are simply undeclared, so a vintage program cannot reach a wide
    # type through a C alias.  Under extended, wide-integers is on, so resolving
    # CINT -> INTEGER32 is legitimate rather than a gate bypass.
    if is_extended(features):
        _c_alias_resolved = {
            'CHAR': CHAR_TYPE,
            'INTEGER': INTEGER_TYPE,
            'INTEGER32': INTEGER32_TYPE,
            'INTEGER64': INTEGER64_TYPE,
            'REAL': REAL_TYPE,
            'ADRMEM': PointerType(CHAR_TYPE),
        }
        for _alias, _base in C_ABI_TYPE_ALIASES.items():
            define_builtin(_alias, _c_alias_resolved[_base], 'type')

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
