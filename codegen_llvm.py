"""
LLVM IR code generator for Pascal AST.

Walks the AST and emits LLVM IR. Supports:
- Integer and boolean variables
- Procedure and function declarations
- All statement types (IF, FOR, WHILE, REPEAT, CASE, etc.)
- Integer expressions and operators
- Built-in I/O: WRITELN(integer), READLN(var integer)
"""

from __future__ import annotations

from dataclasses import dataclass
from parser import parse_file
from typing import Any, Dict, List, Optional, Union

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder

from ast_nodes import *
from builtins_registry import register_builtins
from type_system import LStringType as ResolvedLStringType
from type_system import StringType as ResolvedStringType


class CodegenError(Exception):
    pass


class Symbol:
    """A symbol in the current scope."""

    def __init__(self, name: str, llvm_value: Any, type_expr: Type, is_parameter: bool = False):
        self.name = name
        self.llvm_value = llvm_value  # ir.Value or ir.Function or ir.GlobalVariable
        self.type_expr = type_expr
        self.is_parameter = is_parameter  # True if this is a function parameter (passed by value)


@dataclass
class LoopContext:
    label: Optional[Union[int, str]]
    break_block: ir.Block
    cycle_block: ir.Block


class Scope:
    """A scope (function or block) with symbol table."""

    def __init__(self, parent: Optional[Scope] = None):
        self.parent = parent
        self.symbols: Dict[str, Symbol] = {}

    def define(self, name: str, llvm_value: Any, type_expr: Type, is_parameter: bool = False) -> None:
        """Define a symbol in this scope."""
        self.symbols[name.lower()] = Symbol(name, llvm_value, type_expr, is_parameter)

    def lookup(self, name: str) -> Optional[Symbol]:
        """Look up a symbol, checking parent scopes."""
        key = name.lower()
        if key in self.symbols:
            return self.symbols[key]
        if self.parent:
            return self.parent.lookup(name)
        return None


_SCALAR_SIZES = {
    'INTEGER': 4,
    'REAL': 8,
    'WORD': 2,
    'CHAR': 1,
    'BOOLEAN': 1,  # vintage Pascal BOOLEAN is one byte
    'ADRMEM': 8,  # 64-bit pointer
}


class Codegen:
    """LLVM IR code generator."""

    def __init__(self, verbose: bool = False, source_file: Optional[str] = None):
        self.module = ir.Module(name="pascal_program")
        self.source_file = source_file
        self.module.triple = "x86_64-pc-linux-gnu"  # Standard Linux target
        self.builder: Optional[IRBuilder] = None
        self.scope = Scope()  # global scope
        self.current_function: Optional[ir.Function] = None
        self.current_return_block: Optional[ir.BasicBlock] = None
        # Compile-time constants keyed UPPER.  Values are int for INTEGER/BOOL/CHAR
        # constants, or float for REAL constants.  Use _const_ir() to emit the
        # appropriate LLVM constant at reference sites.
        self.constants: Dict[str, object] = {
            'MAXINT': 2147483647,
            'MAXWORD': 65535,
        }
        self.type_aliases: Dict[str, Type] = {}  # compile-time type aliases, keyed UPPER
        self.current_interface_decls: Dict[str, Declaration] = {}
        self.proc_param_modes: Dict[str, List[Optional[str]]] = {}
        self.loop_stack: List[LoopContext] = []
        # Enum support (checklist 9.8): map each enum member (UPPER) to the full
        # ordered member-name list of its enum so WRITE can print the symbolic
        # name of a bare member literal; cache the per-enum `[n x i8*]` name
        # tables so each enum emits its name strings only once.
        self.enum_member_names: Dict[str, List[str]] = {}
        self._enum_name_tables: Dict[str, ir.GlobalVariable] = {}
        self.verbose = verbose
        self._register_predeclared_externs()

    def _log(self, msg: str) -> None:
        """Emit a diagnostic line to stderr when verbose mode is on."""
        if self.verbose:
            import sys
            print(f'[codegen] {msg}', file=sys.stderr)

    def _register_predeclared_externs(self) -> None:
        """Predeclare runtime externs that behave like builtins."""
        fill_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer(), ir.IntType(16), ir.IntType(8)])
        fillc = ir.Function(self.module, fill_ty, name='fillc')
        fillc.linkage = 'external'
        self.scope.define('fillc', fillc, None)
        fillsc = ir.Function(self.module, fill_ty, name='fillsc')
        fillsc.linkage = 'external'
        self.scope.define('fillsc', fillsc, None)
        mov_ty = ir.FunctionType(ir.IntType(32), [ir.IntType(8).as_pointer(), ir.IntType(8).as_pointer(), ir.IntType(16)])
        movel = ir.Function(self.module, mov_ty, name='movel')
        movel.linkage = 'external'
        self.scope.define('movel', movel, None)
        mover = ir.Function(self.module, mov_ty, name='mover')
        mover.linkage = 'external'
        self.scope.define('mover', mover, None)
        movesl = ir.Function(self.module, mov_ty, name='movesl')
        movesl.linkage = 'external'
        self.scope.define('movesl', movesl, None)
        movesr = ir.Function(self.module, mov_ty, name='movesr')
        movesr.linkage = 'external'
        self.scope.define('movesr', movesr, None)

    # ========================================================================
    # Type System
    # ========================================================================

    def llvm_type(self, type_expr: Type) -> ir.Type:
        """Convert a Pascal type to LLVM type."""
        if isinstance(type_expr, BuiltinType):
            if type_expr.name == 'INTEGER':
                return ir.IntType(32)
            elif type_expr.name == 'BOOLEAN':
                return ir.IntType(8)  # one byte, so adr/sizeof/fillc agree on layout
            elif type_expr.name == 'WORD':
                return ir.IntType(16)
            elif type_expr.name == 'CHAR':
                return ir.IntType(8)
            elif type_expr.name == 'REAL':
                return ir.DoubleType()
            elif type_expr.name == 'ADRMEM':
                return ir.PointerType(ir.IntType(8))  # pointer/address
            else:
                raise CodegenError(f'Unknown built-in type: {type_expr.name}')
        elif isinstance(type_expr, NamedType):
            name_up = type_expr.name.upper()
            if name_up == 'LSTRING':
                # LSTRING without explicit param: use default 256
                param_val = int(type_expr.param) if type_expr.param else 256
                return ir.ArrayType(ir.IntType(8), param_val + 1)
            elif name_up == 'STRING':
                # STRING without explicit param: use default 256
                param_val = int(type_expr.param) if type_expr.param else 256
                return ir.ArrayType(ir.IntType(8), param_val)
            if name_up == 'ADRMEM':
                return ir.PointerType(ir.IntType(8))
            elif name_up == 'INTEGER':
                return ir.IntType(32)
            elif name_up == 'BOOLEAN':
                return ir.IntType(8)
            elif name_up == 'WORD':
                return ir.IntType(16)
            elif name_up == 'REAL':
                return ir.DoubleType()
            elif name_up == 'CHAR':
                return ir.IntType(8)
            if name_up in self.type_aliases:
                return self.llvm_type(self.type_aliases[name_up])
            return ir.IntType(32)
        elif isinstance(type_expr, EnumType):
            return ir.IntType(32)
        elif isinstance(type_expr, SetType):
            return self.set_llvm_type()
        elif isinstance(type_expr, LStringType):
            # LSTRING(n) is PACKED ARRAY [0..n] OF CHAR = [n+1 x i8]
            return ir.ArrayType(ir.IntType(8), type_expr.max_len + 1)
        elif isinstance(type_expr, ResolvedLStringType):
            # LSTRING(n) is PACKED ARRAY [0..n] OF CHAR = [n+1 x i8]
            return ir.ArrayType(ir.IntType(8), type_expr.max_len + 1)
        elif isinstance(type_expr, ResolvedStringType):
            # STRING(n) is PACKED ARRAY [1..n] OF CHAR = [n x i8]
            return ir.ArrayType(ir.IntType(8), type_expr.max_len)
        elif isinstance(type_expr, SubrangeType):
            if type_expr.host:
                return self.llvm_type(NamedType(type_expr.host, None))
            return ir.IntType(32)
        elif isinstance(type_expr, PointerType):
            base_type = self.llvm_type(type_expr.base)
            if getattr(type_expr, 'flavor', 'POINTER') == 'ADS':
                return ir.LiteralStructType([ir.PointerType(base_type), ir.IntType(16)])
            return ir.PointerType(base_type)
        elif isinstance(type_expr, ArrayType):
            elem_type = self.llvm_type(type_expr.element_type)
            # Compute actual array size
            try:
                low_val = self.eval_const_expr(type_expr.index_range.low)
                high_val = self.eval_const_expr(type_expr.index_range.high) if type_expr.index_range.high else low_val + 99
                size = high_val - low_val + 1
            except Exception:
                size = 100
            return ir.ArrayType(elem_type, size)
        elif isinstance(type_expr, RecordType):
            # AST RecordType.fields is a list of (name_list, field_type) pairs.
            # Lay the record out as an LLVM struct in declaration order, one
            # struct element per field name (so `x, y: INTEGER` -> two i32s).
            # record_field_index() uses this same ordering to address fields.
            elem_types: List[ir.Type] = []
            for names, ftype in type_expr.fields:
                lt = self.llvm_type(ftype)
                for _ in names:
                    elem_types.append(lt)
            return ir.LiteralStructType(elem_types)
        else:
            raise CodegenError(f'Type {type(type_expr).__name__} not yet supported')

    def param_llvm_type(self, param: Param) -> ir.Type:
        base = self.llvm_type(param.type_expr)
        if param.mode in {'VAR', 'VARS', 'CONST', 'CONSTS'}:
            # LLVM lowering: near and far reference parameters both use ordinary
            # pointers on this target. Far modes preserve source-level mode
            # metadata; the segment component is degenerate, as with ADS.
            return ir.PointerType(base)
        return base

    # ========================================================================
    # Main Entry Point
    # ========================================================================

    def codegen(self, unit: Union[ProgramUnit, ModuleUnit, InterfaceUnit, ImplementationUnit]) -> ir.Module:
        """Generate LLVM IR from AST root."""
        if isinstance(unit, ProgramUnit):
            return self.codegen_program(unit)
        elif isinstance(unit, ModuleUnit):
            return self.codegen_module(unit)
        elif isinstance(unit, InterfaceUnit):
            return self.codegen_interface(unit)
        elif isinstance(unit, ImplementationUnit):
            return self.codegen_implementation(unit)
        else:
            raise CodegenError(f'Unknown unit type: {type(unit).__name__}')

    def codegen_program(self, unit: ProgramUnit) -> ir.Module:
        """Codegen for PROGRAM unit."""
        # Import any modules referenced by USES before we codegen the body.
        for use_clause in unit.uses:
            self.codegen_use_clause(use_clause)

        # Codegen all declarations
        for decl in unit.block.decls:
            self.codegen_decl(decl)

        # Create main function if not already defined
        if 'main' not in [f.name for f in self.module.functions]:
            main_type = ir.FunctionType(ir.IntType(32), [])
            main_func = ir.Function(self.module, main_type, name='main')
            entry_block = main_func.append_basic_block(name='entry')
            self.builder = IRBuilder(entry_block)
            self.current_function = main_func

            # Execute the program body
            self.codegen_stmt_list(unit.block.body)

            # Default return 0
            if not self.builder.block.is_terminated:
                self.builder.ret(ir.Constant(ir.IntType(32), 0))

        return self.module

    def codegen_module(self, unit: ModuleUnit) -> ir.Module:
        """Codegen for MODULE unit."""
        for decl in unit.decls:
            self.codegen_decl(decl)
        return self.module

    def codegen_use_clause(self, use_clause: UseClause) -> None:
        """Import declarations from a USES module as external symbols."""
        module_path = None
        import os
        from pathlib import Path
        search_dir = Path(self.source_file).parent if self.source_file else Path('.')
        for candidate in (use_clause.name, use_clause.name.lower(), use_clause.name.upper()):
            for suffix in ('', '.inc', '.pas'):
                path = search_dir / f'{candidate}{suffix}'
                if path.exists():
                    module_path = str(path)
                    break
            if module_path:
                break
        if not module_path:
            return
        ast = parse_file(module_path)
        decls = getattr(ast, 'decls', [])
        for decl in decls:
            if isinstance(decl, (ProcDecl, FuncDecl)) and getattr(decl, 'name', None):
                self.codegen_decl(
                    ProcDecl(decl.name, decl.params, getattr(decl, 'attributes', []), body=None
                             ) if isinstance(decl, ProcDecl) else FuncDecl(decl.name, decl.params, decl.return_type, getattr(decl, 'attributes', []), body=None))

    def codegen_interface(self, unit: InterfaceUnit) -> ir.Module:
        """Codegen for INTERFACE unit (declarations only)."""
        for decl in unit.decls:
            self.codegen_decl(decl)
        return self.module

    def codegen_implementation(self, unit: ImplementationUnit) -> ir.Module:
        """Codegen for IMPLEMENTATION unit."""
        old_iface = self.current_interface_decls
        self.current_interface_decls = {getattr(decl, 'name', '').lower(): decl for decl in (unit.interface.decls if unit.interface else []) if getattr(decl, 'name', None)}
        try:
            for decl in unit.decls:
                self.codegen_decl(decl)
        finally:
            self.current_interface_decls = old_iface

        # Codegen init body if present
        if unit.init_body:
            init_type = ir.FunctionType(ir.IntType(32), [])
            init_name = f'pascal_init_{unit.name.lower()}'
            init_func = ir.Function(self.module, init_type, name=init_name)
            entry_block = init_func.append_basic_block(name='entry')
            self.builder = IRBuilder(entry_block)
            self.current_function = init_func

            self.codegen_stmt_list(unit.init_body)

            if not self.builder.block.is_terminated:
                self.builder.ret(ir.Constant(ir.IntType(32), 0))

        return self.module

    # ========================================================================
    # Declarations
    # ========================================================================

    def codegen_decl(self, decl: Declaration) -> None:
        """Codegen a declaration."""
        names = getattr(decl, 'names', None) or getattr(decl, 'name', '')
        self._log(f'decl  {type(decl).__name__} {names}')
        if isinstance(decl, ConstDecl):
            self.codegen_const_decl(decl)
        elif isinstance(decl, VarDecl):
            self.codegen_var_decl(decl)
        elif isinstance(decl, TypeDecl):
            self.codegen_type_decl(decl)
        elif isinstance(decl, ValueDecl):
            # VALUE declarations don't generate code
            pass
        elif isinstance(decl, LabelDecl):
            # Label declarations don't generate code
            pass
        elif isinstance(decl, ProcDecl):
            self.codegen_proc_decl(decl)
        elif isinstance(decl, FuncDecl):
            self.codegen_func_decl(decl)
        else:
            raise CodegenError(f'Unknown declaration: {type(decl).__name__}')

    def codegen_const_decl(self, decl: ConstDecl) -> None:
        """Codegen for CONST declaration."""
        # Evaluate constant at compile time and remember it so that later
        # uses (array bounds, sizeof, and plain value references) can resolve it.
        value = self.eval_const_expr(decl.value)
        self.constants[decl.name.upper()] = value

    def _const_ir(self, name_upper: str) -> ir.Constant:
        """Emit the appropriate LLVM constant for a named compile-time constant."""
        v = self.constants[name_upper]
        if isinstance(v, float):
            return ir.Constant(ir.DoubleType(), v)
        return ir.Constant(ir.IntType(32), int(v))

    def codegen_type_decl(self, decl: TypeDecl) -> None:
        """Record a type declaration for later codegen lookups."""
        self.type_aliases[decl.name.upper()] = decl.type_expr
        # Enum members become ordinal compile-time constants (0, 1, 2, ...).
        if isinstance(decl.type_expr, EnumType):
            for ordinal, member in enumerate(decl.type_expr.values):
                self.constants[member.upper()] = ordinal
                # Remember which enum each member belongs to so WRITE can print
                # the symbolic name of a bare member literal (checklist 9.8).
                self.enum_member_names[member.upper()] = list(decl.type_expr.values)

    def get_string_type_info(self, t: Type) -> tuple[bool, int, bool]:
        """Returns (is_str, max_len, is_lstring) for any AST Type or Resolved Type."""
        from type_system import LStringType as ResolvedLStringType
        from type_system import StringType as ResolvedStringType

        if isinstance(t, (ResolvedLStringType, ResolvedStringType)):
            return True, t.max_len, isinstance(t, ResolvedLStringType)

        # Check AST LStringType
        if isinstance(t, LStringType):
            return True, t.max_len, True

        # Check NamedType
        if isinstance(t, NamedType):
            name_up = t.name.upper()
            if name_up == 'LSTRING':
                return True, (int(t.param) if t.param is not None else 256), True
            elif name_up == 'STRING':
                return True, (int(t.param) if t.param is not None else 256), False
            elif name_up in self.type_aliases:
                return self.get_string_type_info(self.type_aliases[name_up])

        return False, 256, False

    def codegen_var_decl(self, decl: VarDecl) -> None:
        """Codegen for VAR declaration."""
        llvm_type = self.llvm_type(decl.type_expr)
        attrs = {attr.upper() for attr in getattr(decl, 'attributes', [])}
        is_static = 'STATIC' in attrs

        # Check if the type is a string type
        is_str, max_len, is_lstring = self.get_string_type_info(decl.type_expr)

        if self.builder and not is_static:
            # Local variable (inside a function) — allocate the aggregate inline
            for name in decl.names:
                alloca = self.builder.alloca(llvm_type, name=name)
                self.scope.define(name, alloca, decl.type_expr)

                if is_str:
                    # Initialize length byte to 0 for LSTRING
                    if is_lstring:
                        zero = ir.Constant(ir.IntType(32), 0)
                        len_ptr = self.builder.gep(alloca, [zero, zero])
                        self.builder.store(ir.Constant(ir.IntType(8), 0), len_ptr)
                    # STRING: no initialization needed (chars are undefined until assigned)
        else:
            # Static or global variable — allocate aggregate with zero init
            prefix = self.current_function.name if self.current_function else 'global'
            for name in decl.names:
                gv_name = name if not self.builder else f'{prefix}.{name}'

                # Create global variable with the aggregate type
                global_var = ir.GlobalVariable(self.module, llvm_type, name=gv_name)

                if is_str:
                    if is_lstring:
                        # Initialize with zero (length 0, rest undefined)
                        global_var.initializer = ir.Constant(llvm_type, bytearray(llvm_type.count))
                    else:
                        # STRING: initialize with blanks (0x20)
                        init_bytes = bytearray([0x20] * llvm_type.count)
                        global_var.initializer = ir.Constant(llvm_type, init_bytes)
                else:
                    global_var.initializer = self.zero_initializer(llvm_type)

                self.scope.define(name, global_var, decl.type_expr)

    def codegen_proc_decl(self, decl: ProcDecl) -> None:
        """Codegen for PROCEDURE declaration."""
        effective_decl = decl
        iface_decl = self.current_interface_decls.get(decl.name.lower()) if decl.name else None
        if iface_decl and not decl.params:
            effective_decl = iface_decl

        # Flatten parameter types: reference modes are passed as LLVM pointers.
        param_types = []
        flat_modes = []
        for param in effective_decl.params:
            param_type = self.param_llvm_type(param)
            for _ in param.names:
                param_types.append(param_type)
                flat_modes.append(param.mode)
        func_type = ir.FunctionType(ir.IntType(32), param_types)

        attrs = {attr.upper() for attr in getattr(decl, 'attributes', [])}
        existing = self.scope.lookup(decl.name)
        if existing and isinstance(existing.llvm_value, ir.Function):
            func = existing.llvm_value
            if func.function_type != func_type:
                raise CodegenError(f"Procedure '{decl.name}' already declared with a different signature")
        else:
            # Create function
            func = ir.Function(self.module, func_type, name=decl.name)
        if attrs.intersection({'PUBLIC', 'EXTERN', 'EXTERNAL'}):
            func.linkage = 'external'
        self.proc_param_modes[decl.name.lower()] = flat_modes
        self.scope.define(decl.name, func, None)

        # If no body, it's extern/forward
        if not decl.body:
            return

        # Create entry block
        entry_block = func.append_basic_block(name='entry')
        prev_builder = self.builder
        prev_func = self.current_function
        prev_scope = self.scope

        self.builder = IRBuilder(entry_block)
        self.current_function = func
        self.scope = Scope(parent=prev_scope)

        # Bind parameters to the scope
        args_iter = iter(func.args)
        for param in effective_decl.params:
            for name in param.names:
                arg = next(args_iter)
                arg.name = name
                self.scope.define(name, arg, param.type_expr, is_parameter=param.mode not in {'VAR', 'VARS', 'CONST', 'CONSTS'})

        # Codegen body
        for inner_decl in decl.body.decls:
            self.codegen_decl(inner_decl)

        for stmt in decl.body.body:
            self.codegen_stmt(stmt)

        # Default return
        self.builder.ret(ir.Constant(ir.IntType(32), 0))

        # Restore context
        self.builder = prev_builder
        self.current_function = prev_func
        self.scope = prev_scope

    def codegen_func_decl(self, decl: FuncDecl) -> None:
        """Codegen for FUNCTION declaration."""
        effective_decl = decl
        iface_decl = self.current_interface_decls.get(decl.name.lower()) if decl.name else None
        if iface_decl and not decl.params:
            effective_decl = iface_decl

        # Flatten parameter types: reference modes are passed as LLVM pointers.
        param_types = []
        flat_modes = []
        for param in effective_decl.params:
            param_type = self.param_llvm_type(param)
            for _ in param.names:
                param_types.append(param_type)
                flat_modes.append(param.mode)
        return_type = self.llvm_type(decl.return_type)
        func_type = ir.FunctionType(return_type, param_types)

        # Create function
        func = ir.Function(self.module, func_type, name=decl.name)
        attrs = {attr.upper() for attr in getattr(decl, 'attributes', [])}
        if attrs.intersection({'PUBLIC', 'EXTERN', 'EXTERNAL'}):
            func.linkage = 'external'
        self.proc_param_modes[decl.name.lower()] = flat_modes
        self.scope.define(decl.name, func, decl.return_type)

        # If no body, it's extern/forward
        if not decl.body:
            return

        # Create entry block
        entry_block = func.append_basic_block(name='entry')
        prev_builder = self.builder
        prev_func = self.current_function
        prev_scope = self.scope

        self.builder = IRBuilder(entry_block)
        self.current_function = func
        self.scope = Scope(parent=prev_scope)

        # Bind parameters
        args_iter = iter(func.args)
        for param in effective_decl.params:
            for name in param.names:
                arg = next(args_iter)
                arg.name = name
                self.scope.define(name, arg, param.type_expr, is_parameter=param.mode not in {'VAR', 'VARS', 'CONST', 'CONSTS'})

        # Allocate space for return value
        return_alloca = self.builder.alloca(return_type, name='return_value')
        self.scope.define(decl.name, return_alloca, decl.return_type)
        self.builder.store(ir.Constant(return_type, 0.0) if isinstance(return_type, ir.DoubleType) else ir.Constant(return_type, 0), return_alloca)

        # Codegen body
        for inner_decl in decl.body.decls:
            self.codegen_decl(inner_decl)

        self.codegen_stmt_list(decl.body.body)

        # Default return / function result
        if not self.builder.block.is_terminated:
            result = self.builder.load(return_alloca)
            self.builder.ret(result)

        # Restore context
        self.builder = prev_builder
        self.current_function = prev_func
        self.scope = prev_scope

    # ========================================================================
    # Statements
    # ========================================================================

    def codegen_stmt_list(self, stmts: List[Statement]) -> None:
        for stmt in stmts:
            if self.builder.block.is_terminated:
                break
            self.codegen_stmt(stmt)

    def codegen_stmt(self, stmt: Statement) -> None:
        """Codegen a statement."""
        self._log(f'stmt  {type(stmt).__name__}')
        if isinstance(stmt, CompoundStmt):
            self.codegen_stmt_list(stmt.stmts)
        elif isinstance(stmt, AssignStmt):
            self.codegen_assign_stmt(stmt)
        elif isinstance(stmt, ProcCallStmt):
            self.codegen_proc_call_stmt(stmt)
        elif isinstance(stmt, IfStmt):
            self.codegen_if_stmt(stmt)
        elif isinstance(stmt, ForStmt):
            self.codegen_for_stmt(stmt)
        elif isinstance(stmt, WhileStmt):
            self.codegen_while_stmt(stmt)
        elif isinstance(stmt, RepeatStmt):
            self.codegen_repeat_stmt(stmt)
        elif isinstance(stmt, CaseStmt):
            self.codegen_case_stmt(stmt)
        elif isinstance(stmt, GotoStmt):
            # TODO: handle GOTO
            pass
        elif isinstance(stmt, ReturnStmt):
            self.codegen_return_stmt(stmt)
        elif isinstance(stmt, BreakStmt):
            self.codegen_break_stmt(stmt)
        elif isinstance(stmt, CycleStmt):
            self.codegen_cycle_stmt(stmt)
        elif isinstance(stmt, WithStmt):
            # TODO: handle WITH
            pass
        elif isinstance(stmt, LabelStmt):
            self.codegen_label_stmt(stmt)
        elif isinstance(stmt, EmptyStmt):
            pass
        else:
            raise CodegenError(f'Unknown statement: {type(stmt).__name__}')

    def codegen_assign_stmt(self, stmt: AssignStmt) -> None:
        """Codegen for assignment statement."""
        target_name = stmt.target.name
        symbol = self.scope.lookup(target_name) or self.scope.lookup(target_name.upper())
        if not symbol:
            raise CodegenError(f'Undefined variable: {target_name}')

        # Can't assign to parameters (passed by value)
        if symbol.is_parameter:
            raise CodegenError(f'Cannot assign to parameter: {target_name}')

        # Check if the target is a string type
        is_str, max_len, is_dest_lstring = self.get_string_type_info(symbol.type_expr)

        # Resolve the pointer (handles array indexing, etc.)
        ptr = self.resolve_designator_ptr(stmt.target)
        value = self.codegen_expr(stmt.expr)

        # Handle simple type conversions
        if not is_str and hasattr(ptr.type, 'pointee'):
            target_type = ptr.type.pointee
            if isinstance(target_type, ir.IntType) and isinstance(value.type, ir.IntType):
                if target_type.width < value.type.width:
                    value = self.builder.trunc(value, target_type)
                elif target_type.width > value.type.width:
                    value = self.builder.zext(value, target_type)
            elif isinstance(target_type, ir.DoubleType) and isinstance(value.type, ir.IntType):
                value = self.builder.sitofp(value, target_type)
            elif isinstance(target_type, ir.IntType) and isinstance(value.type, ir.DoubleType):
                value = self.builder.fptosi(value, target_type)
            elif isinstance(target_type, ir.PointerType) and isinstance(value.type, ir.PointerType):
                if isinstance(stmt.expr, NilLiteral):
                    value = ir.Constant(target_type, None)
                elif value.type != target_type:
                    value = self.builder.bitcast(value, target_type)

        if is_str:
            # ptr is now directly the aggregate pointer [n+1 x i8] or [n x i8]
            if isinstance(stmt.expr, NilLiteral) or (isinstance(stmt.expr, Identifier) and stmt.expr.name.upper() == 'NULL'):
                if is_dest_lstring:
                    # LSTRING: set length to 0
                    zero = ir.Constant(ir.IntType(32), 0)
                    len_ptr = self.builder.gep(ptr, [zero, zero])
                    self.builder.store(ir.Constant(ir.IntType(8), 0), len_ptr)
                else:
                    # STRING: fill with blanks (0x20)
                    zero = ir.Constant(ir.IntType(32), 0)
                    chars_ptr = self.builder.gep(ptr, [zero, zero])
                    size_64 = self.builder.zext(ir.Constant(ir.IntType(32), max_len), ir.IntType(64))
                    self.builder.call(self.memset_func(), [chars_ptr, ir.Constant(ir.IntType(32), 0x20), size_64])
            else:
                src_chars, src_len = self.get_string_chars_and_len(stmt.expr)

                # Range check: src_len <= max_len
                cond = self.builder.icmp_signed('<=', src_len, ir.Constant(ir.IntType(32), max_len))
                success_block = self.builder.block.parent.append_basic_block('str_assign_ok')
                error_block = self.builder.block.parent.append_basic_block('str_assign_overflow')
                end_block = self.builder.block.parent.append_basic_block('str_assign_end')
                self.builder.cbranch(cond, success_block, error_block)

                # Error block: emit range-check failure
                self.builder.position_at_end(error_block)
                self.builder.call(self.runtime_error_func(), [])
                self.builder.unreachable()

                # Success block: perform assignment
                self.builder.position_at_end(success_block)
                zero = ir.Constant(ir.IntType(32), 0)
                one = ir.Constant(ir.IntType(32), 1)
                src_len_64 = self.builder.zext(src_len, ir.IntType(64))

                if is_dest_lstring:
                    # LSTRING(n) is PACKED ARRAY [0..n] OF CHAR (manual 6-18):
                    # byte [0] = current length (0..n), bytes [1..n] = chars.
                    # It is length-prefixed, NOT null-terminated, so the whole
                    # [n+1 x i8] is usable at full capacity (src_len == n).
                    # Copy characters to [1..]
                    dest_chars = self.builder.gep(ptr, [zero, one])
                    self.builder.call(self.memcpy_func(), [dest_chars, src_chars, src_len_64])

                    # Store length in byte [0]
                    len_ptr = self.builder.gep(ptr, [zero, zero])
                    src_len_8 = self.builder.trunc(src_len, ir.IntType(8))
                    self.builder.store(src_len_8, len_ptr)
                else:
                    # STRING [n x i8]: bytes [0..n-1] = chars, blank-padded
                    # Copy characters to [0,0]
                    dest_chars = self.builder.gep(ptr, [zero, zero])
                    self.builder.call(self.memcpy_func(), [dest_chars, src_chars, src_len_64])

                    # Blank-pad from [src_len] to [max_len-1] with 0x20
                    pad_start = self.builder.gep(ptr, [zero, src_len])
                    pad_len = self.builder.sub(ir.Constant(ir.IntType(32), max_len), src_len)
                    pad_len_64 = self.builder.zext(pad_len, ir.IntType(64))
                    self.builder.call(self.memset_func(), [pad_start, ir.Constant(ir.IntType(32), 0x20), pad_len_64])

                self.builder.branch(end_block)
                self.builder.position_at_end(end_block)
        else:
            self.builder.store(value, ptr)

    def codegen_proc_call_stmt(self, stmt: ProcCallStmt) -> None:
        """Codegen for procedure call statement."""
        lookup_name = stmt.name.upper()
        symbol = self.scope.lookup(lookup_name) or self.scope.lookup(stmt.name)
        if not symbol:
            # Try built-in procedures
            if lookup_name == 'WRITELN':
                self.builtin_writeln(stmt.args)
            elif lookup_name == 'WRITE':
                self.builtin_write(stmt.args)
            elif lookup_name == 'READLN':
                self.builtin_readln(stmt.args)
            elif lookup_name == 'CONCAT':
                self.builtin_concat(stmt.args)
            elif lookup_name == 'COPYLST':
                self.builtin_copylst(stmt.args)
            elif lookup_name == 'COPYSTR':
                self.builtin_copystr(stmt.args)
            elif lookup_name == 'PACK':
                self.builtin_pack(stmt.args)
            elif lookup_name == 'UNPACK':
                self.builtin_unpack(stmt.args)
            elif lookup_name == 'MOVEL':
                self.builtin_movel(stmt.args)
            elif lookup_name == 'MOVER':
                self.builtin_mover(stmt.args)
            elif lookup_name == 'MOVESL':
                self.builtin_movesl(stmt.args)
            elif lookup_name == 'MOVESR':
                self.builtin_movesr(stmt.args)
            elif lookup_name == 'ABORT':
                self.builtin_abort(stmt.args)
            else:
                raise CodegenError(f'Undefined procedure: {stmt.name}')
        else:
            # User-defined procedure
            fn = symbol.llvm_value
            param_types = fn.function_type.args
            param_modes = self.proc_param_modes.get(stmt.name.lower(), [])
            args = []
            for i, arg in enumerate(stmt.args):
                mode = param_modes[i] if i < len(param_modes) else None
                v = self.codegen_actual_arg(arg, mode)
                if i < len(param_types):
                    v = self.coerce_arg(v, param_types[i])
                args.append(v)
            self.builder.call(fn, args)

    def codegen_actual_arg(self, arg: Expression, mode: Optional[str]) -> ir.Value:
        if mode in {'VAR', 'VARS', 'CONST', 'CONSTS'}:
            if isinstance(arg, Identifier):
                return self.resolve_designator_ptr(Designator(arg.name, []))
            if isinstance(arg, Designator):
                return self.resolve_designator_ptr(arg)
            raise CodegenError(f'{mode} parameter requires a designator argument')
        return self.codegen_expr(arg)

    def codegen_if_stmt(self, stmt: IfStmt) -> None:
        """Codegen for IF statement."""
        cond = self.codegen_expr(stmt.cond)
        cond_bit = self.to_bool(cond)

        then_block = self.current_function.append_basic_block(name='if_then')
        end_block = self.current_function.append_basic_block(name='if_end')

        if stmt.else_branch:
            else_block = self.current_function.append_basic_block(name='if_else')
            self.builder.cbranch(cond_bit, then_block, else_block)

            self.builder.position_at_end(then_block)
            self.codegen_stmt(stmt.then_branch)
            if not self.builder.block.is_terminated:
                self.builder.branch(end_block)

            self.builder.position_at_end(else_block)
            self.codegen_stmt(stmt.else_branch)
            if not self.builder.block.is_terminated:
                self.builder.branch(end_block)
        else:
            self.builder.cbranch(cond_bit, then_block, end_block)

            self.builder.position_at_end(then_block)
            self.codegen_stmt(stmt.then_branch)
            if not self.builder.block.is_terminated:
                self.builder.branch(end_block)

        self.builder.position_at_end(end_block)

    def codegen_for_stmt(self, stmt: ForStmt) -> None:
        """Codegen for FOR loop."""
        # Allocate loop variable (or reuse if already exists).  IBM Pascal's
        # ``FOR STATIC i := ...`` treats the control variable as STATIC: it has
        # fixed storage instead of normal stack storage.
        symbol = self.scope.lookup(stmt.var)
        if stmt.static:
            loop_type = self.llvm_type(symbol.type_expr) if symbol else ir.IntType(32)
            owner = self.current_function.name if self.current_function else 'global'
            global_name = f"__for_static_{owner}_{stmt.var}"
            if global_name in self.module.globals:
                loop_var = self.module.globals[global_name]
            else:
                loop_var = ir.GlobalVariable(self.module, loop_type, name=global_name)
                loop_var.linkage = 'internal'
                loop_var.initializer = self.zero_initializer(loop_type)
            self.scope.define(stmt.var, loop_var, symbol.type_expr if symbol else BuiltinType('INTEGER'))
        elif not symbol:
            loop_var = self.builder.alloca(ir.IntType(32), name=stmt.var)
            self.scope.define(stmt.var, loop_var, BuiltinType('INTEGER'))
        else:
            loop_var = symbol.llvm_value

        # Initialize loop variable
        start_val = self.codegen_expr(stmt.start)
        self.builder.store(start_val, loop_var)

        # Create loop blocks
        loop_block = self.current_function.append_basic_block(name='for_loop')
        end_block = self.current_function.append_basic_block(name='for_end')
        step_block = self.current_function.append_basic_block(name='for_step')
        body_block = self.current_function.append_basic_block(name='for_body')
        self.loop_stack.append(LoopContext(self.normalize_label(getattr(stmt, 'label', None)), end_block, step_block))

        self.builder.branch(loop_block)
        self.builder.position_at_end(loop_block)
        current_val = self.builder.load(loop_var)
        end_val = self.codegen_expr(stmt.end)
        cond = self.builder.icmp_signed('<=', current_val, end_val) if stmt.direction == 'TO' else self.builder.icmp_signed('>=', current_val, end_val)
        self.builder.cbranch(cond, body_block, end_block)

        self.builder.position_at_end(body_block)
        self.codegen_stmt(stmt.body)
        if not self.builder.block.is_terminated:
            self.builder.branch(step_block)

        self.builder.position_at_end(step_block)
        current_val = self.builder.load(loop_var)
        next_val = self.builder.add(current_val, ir.Constant(ir.IntType(32), 1)) if stmt.direction == 'TO' else self.builder.sub(current_val, ir.Constant(ir.IntType(32), 1))
        self.builder.store(next_val, loop_var)
        self.builder.branch(loop_block)

        self.loop_stack.pop()
        self.builder.position_at_end(end_block)

    def codegen_while_stmt(self, stmt: WhileStmt) -> None:
        """Codegen for WHILE loop."""
        loop_block = self.current_function.append_basic_block(name='while_loop')
        body_block = self.current_function.append_basic_block(name='while_body')
        end_block = self.current_function.append_basic_block(name='while_end')
        self.loop_stack.append(LoopContext(self.normalize_label(getattr(stmt, 'label', None)), end_block, loop_block))

        self.builder.branch(loop_block)
        self.builder.position_at_end(loop_block)
        cond = self.codegen_expr(stmt.cond)
        self.builder.cbranch(self.to_bool(cond), body_block, end_block)

        self.builder.position_at_end(body_block)
        self.codegen_stmt(stmt.body)
        if not self.builder.block.is_terminated:
            self.builder.branch(loop_block)
        self.loop_stack.pop()
        self.builder.position_at_end(end_block)

    def codegen_repeat_stmt(self, stmt: RepeatStmt) -> None:
        """Codegen for REPEAT..UNTIL loop."""
        loop_block = self.current_function.append_basic_block(name='repeat_loop')
        end_block = self.current_function.append_basic_block(name='repeat_end')
        self.loop_stack.append(LoopContext(self.normalize_label(getattr(stmt, 'label', None)), end_block, loop_block))

        self.builder.branch(loop_block)
        self.builder.position_at_end(loop_block)
        self.codegen_stmt_list(stmt.body)
        if not self.builder.block.is_terminated:
            cond = self.codegen_expr(stmt.cond)
            self.builder.cbranch(self.to_bool(cond), end_block, loop_block)
        self.loop_stack.pop()
        self.builder.position_at_end(end_block)

    def codegen_case_stmt(self, stmt: CaseStmt) -> None:
        """Codegen for CASE statement."""
        expr = self.codegen_expr(stmt.expr)

        end_block = self.current_function.append_basic_block(name='case_end')

        # For simplicity, use if-else chain
        for element in stmt.elements:
            case_block = self.current_function.append_basic_block(name='case_block')
            next_check = self.current_function.append_basic_block(name='case_next')

            # Check if expression matches any constant
            any_match = None
            for const_expr in element.constants:
                const_val = self.codegen_expr(const_expr)
                match = self.builder.icmp_signed('==', expr, const_val)
                if any_match is None:
                    any_match = match
                else:
                    any_match = self.builder.or_(any_match, match)

            self.builder.cbranch(any_match, case_block, next_check)

            # Execute case body
            self.builder.position_at_end(case_block)
            self.codegen_stmt(element.stmt)
            self.builder.branch(end_block)

            # Continue to next case
            self.builder.position_at_end(next_check)

        # Otherwise branch
        if stmt.otherwise:
            self.codegen_stmt(stmt.otherwise)

        self.builder.branch(end_block)
        self.builder.position_at_end(end_block)

    def codegen_return_stmt(self, stmt: ReturnStmt) -> None:
        """Codegen for RETURN statement."""
        self.builder.ret(ir.Constant(ir.IntType(32), 0))

    def codegen_break_stmt(self, stmt: BreakStmt) -> None:
        ctx = self.resolve_loop_context(stmt.label)
        self.builder.branch(ctx.break_block)

    def codegen_cycle_stmt(self, stmt: CycleStmt) -> None:
        ctx = self.resolve_loop_context(stmt.label)
        self.builder.branch(ctx.cycle_block)

    def codegen_label_stmt(self, stmt: LabelStmt) -> None:
        inner = stmt.stmt
        if isinstance(inner, (WhileStmt, ForStmt, RepeatStmt)):
            setattr(inner, 'label', self.normalize_label(stmt.label))
        self.codegen_stmt(inner)

    def normalize_label(self, label: Optional[Union[int, str]]) -> Optional[Union[int, str]]:
        if isinstance(label, str):
            return label.lower()
        return label

    def resolve_loop_context(self, label: Optional[Union[int, str]]) -> LoopContext:
        if not self.loop_stack:
            raise CodegenError('BREAK/CYCLE outside of loop')
        label = self.normalize_label(label)
        if label is None:
            return self.loop_stack[-1]
        for ctx in reversed(self.loop_stack):
            if ctx.label == label:
                return ctx
        raise CodegenError(f'Unknown loop label: {label}')

    def resolve_designator_ptr(self, designator: Designator) -> ir.Value:
        """Resolve a designator to its LLVM pointer (handles arrays/selectors)."""
        symbol = self.scope.lookup(designator.name)
        if not symbol:
            symbol = self.scope.lookup(designator.name.upper())
            if not symbol:
                raise CodegenError(f'Undefined variable: {designator.name}')

        ptr = symbol.llvm_value
        cur_type = symbol.type_expr

        if designator.selectors:
            for selector in designator.selectors:
                if selector.kind == 'INDEX':
                    index = self.codegen_expr(selector.index_or_field)
                    # Pascal array indices are relative to the declared lower
                    # bound, but storage is allocated as [high-low+1 x elem]
                    # (0-based). Translate the index to a 0-based slot so that
                    # e.g. ARRAY[5..7] indexed by 5 lands on slot 0, not slot 5
                    # (which would read/write outside the allocation).
                    low, elem_type = self.array_lower_bound(cur_type)
                    if low is not None and low != 0 and isinstance(index.type, ir.IntType):
                        index = self.builder.sub(index, ir.Constant(index.type, low))
                    # GEP requires [0, index] for pointers to arrays, or [index] for flat pointers
                    if isinstance(ptr.type.pointee, ir.ArrayType):
                        ptr = self.builder.gep(ptr, [ir.Constant(ir.IntType(32), 0), index])
                    else:
                        ptr = self.builder.gep(ptr, [index])
                    cur_type = elem_type
                elif selector.kind == 'FIELD':
                    # Record field access: GEP to the field's struct slot.
                    fidx, ftype = self.record_field_index(cur_type, selector.index_or_field)
                    if fidx is None:
                        raise CodegenError(f"Cannot access field '{selector.index_or_field}' on type {cur_type}")
                    ptr = self.builder.gep(ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), fidx)])
                    cur_type = ftype
                elif selector.kind == 'DEREF':
                    # Pointer dereference
                    ptr = self.builder.load(ptr)
                    base = self.resolve_type_alias(cur_type) if cur_type is not None else None
                    cur_type = getattr(base, 'base', None) or getattr(base, 'target_type', None)
        return ptr

    # ========================================================================
    # Type-size, argument coercion, and boolean helpers
    # ========================================================================

    def _scalar_size(self, name: str) -> int:
        """Size in bytes of a scalar/built-in type, by name."""
        return _SCALAR_SIZES.get(name.upper(), 4)

    def set_llvm_type(self) -> ir.Type:
        """LLVM representation for all Pascal sets: 256 bits as four i64 words."""
        return ir.ArrayType(ir.IntType(64), 4)

    def zero_initializer(self, llvm_type: ir.Type) -> ir.Value:
        """Produce a valid zero initializer for any LLVM type.

        Aggregates (arrays/structs) and pointers cannot be initialized with a
        scalar 0 -- llvmlite would try to iterate the int. ``None`` renders as
        ``zeroinitializer`` (and ``null`` for pointers), which is valid for any
        type; scalars keep an explicit 0 for readable IR.
        """
        if isinstance(llvm_type, ir.IntType):
            return ir.Constant(llvm_type, 0)
        return ir.Constant(llvm_type, None)

    def null_lstring_ptr(self) -> ir.Value:
        """Return a pointer to the empty LSTRING constant."""
        if not hasattr(self, '_null_lstring_global'):
            empty = ir.Constant(ir.ArrayType(ir.IntType(8), 1), bytearray(b'\0'))
            self._null_lstring_global = ir.GlobalVariable(self.module, empty.type, name=self.unique_name('nullstr'))
            self._null_lstring_global.initializer = empty
            self._null_lstring_global.global_constant = True
        zero = ir.Constant(ir.IntType(32), 0)
        return self.builder.gep(self._null_lstring_global, [zero, zero])

    def get_type_size(self, t: Type) -> int:
        """Size in bytes of an AST type node (consults constants for bounds)."""
        if isinstance(t, BuiltinType):
            return self._scalar_size(t.name)
        elif isinstance(t, NamedType):
            if t.name.upper() in {'STRING', 'LSTRING'}:
                return (int(t.param) if isinstance(t.param, int) else 256) + 1
            return self._scalar_size(t.name)
        elif isinstance(t, SetType):
            return 32
        elif isinstance(t, (LStringType, ResolvedStringType, ResolvedLStringType)):
            return max(1, getattr(t, 'max_len', 256)) + 1
        elif isinstance(t, SubrangeType):
            return self._scalar_size(t.host) if t.host else 4
        elif isinstance(t, ArrayType):
            low = self.eval_const_expr(t.index_range.low)
            high = self.eval_const_expr(t.index_range.high) if t.index_range.high else low
            count = high - low + 1
            return count * self.get_type_size(t.element_type)
        elif isinstance(t, PointerType):
            return 8  # 64-bit pointer
        elif isinstance(t, RecordType):
            # AST RecordType.fields is a list of (name_list, type) pairs
            total = 0
            for names, ftype in t.fields:
                total += len(names) * self.get_type_size(ftype)
            return total
        else:
            return 4  # fallback

    def coerce_arg(self, value: ir.Value, target_type: ir.Type) -> ir.Value:
        """Coerce a call argument to the callee's declared parameter type.

        Handles the two cases the vintage benchmark needs: any-pointer-to-any
        -pointer (adrmem) via bitcast, and integer width adjustment (e.g. an
        i32 expression into a WORD/i16 parameter).
        """
        vt = value.type
        if vt == target_type:
            return value
        if isinstance(target_type, ir.PointerType) and isinstance(vt, ir.PointerType):
            return self.builder.bitcast(value, target_type)
        if isinstance(target_type, ir.IntType) and isinstance(vt, ir.IntType):
            if vt.width > target_type.width:
                return self.builder.trunc(value, target_type)
            elif vt.width < target_type.width:
                return self.builder.zext(value, target_type)
        if isinstance(target_type, ir.DoubleType) and isinstance(vt, ir.IntType):
            return self.builder.sitofp(value, target_type)
        if isinstance(target_type, ir.IntType) and isinstance(vt, ir.DoubleType):
            return self.builder.fptosi(value, target_type)
        return value

    def to_bool(self, cond: ir.Value) -> ir.Value:
        """Reduce a condition value to an i1 for a branch.

        An already-i1 value is used directly; wider integers (e.g. an i8
        BOOLEAN load or an i32) are compared against zero.
        """
        if isinstance(cond.type, ir.IntType):
            if cond.type.width == 1:
                return cond
            return self.builder.icmp_signed('!=', cond, ir.Constant(cond.type, 0))
        return cond

    # ========================================================================
    # Expressions
    # ========================================================================

    def codegen_expr(self, expr: Expression) -> ir.Value:
        """Codegen an expression."""
        if isinstance(expr, IntLiteral):
            return ir.Constant(ir.IntType(32), expr.value)
        elif isinstance(expr, RealLiteral):
            return ir.Constant(ir.DoubleType(), expr.value)
        elif isinstance(expr, CharLiteral):
            # Convert char to int
            return ir.Constant(ir.IntType(8), ord(expr.value[0]) if expr.value else 0)
        elif isinstance(expr, StringLiteral):
            # Remove single quotes around the Pascal string literal if any
            val_str = expr.value
            if val_str.startswith("'") and val_str.endswith("'"):
                val_str = val_str[1:-1]
            # Replace double single-quotes with single-quote (Pascal escape)
            val_str = val_str.replace("''", "'")

            # Create a global string constant in the module (null-terminated)
            str_bytes = bytearray(val_str.encode('utf-8') + b'\0')
            str_const = ir.Constant(ir.ArrayType(ir.IntType(8), len(str_bytes)), str_bytes)
            str_global = ir.GlobalVariable(self.module, str_const.type, name=self.unique_name('str'))
            str_global.initializer = str_const
            str_global.global_constant = True

            # Return pointer to the first character of the string constant
            zero = ir.Constant(ir.IntType(32), 0)
            return self.builder.gep(str_global, [zero, zero])
        elif isinstance(expr, BoolLiteral):
            return ir.Constant(ir.IntType(1), 1 if expr.value else 0)
        elif isinstance(expr, NilLiteral):
            return ir.Constant(ir.PointerType(ir.IntType(8)), None)
        elif isinstance(expr, AdrExpr):
            # Address-of operator (adr var_name)
            symbol = self.scope.lookup(expr.name)
            if not symbol:
                raise CodegenError(f'Undefined variable: {expr.name}')
            # Local/global variables are represented as pointers in LLVM, so symbol.llvm_value is the address
            return symbol.llvm_value
        elif isinstance(expr, AdsExpr):
            symbol = self.scope.lookup(expr.name)
            if not symbol:
                raise CodegenError(f'Undefined variable: {expr.name}')
            return ir.Constant.literal_struct([symbol.llvm_value, ir.Constant(ir.IntType(16), 0)])
        elif isinstance(expr, SizeofExpr):
            # Sizeof operator (sizeof var_name or sizeof type)
            if isinstance(expr.target, str):
                symbol = self.scope.lookup(expr.target) or self.scope.lookup(expr.target.upper())
                if symbol is not None and symbol.type_expr is not None:
                    size_val = self.get_type_size(symbol.type_expr)
                else:
                    # Not a variable: treat the name as a built-in type name
                    size_val = self._scalar_size(expr.target)
            else:
                # An AST Type node was supplied directly
                size_val = self.get_type_size(expr.target)
            return ir.Constant(ir.IntType(16), size_val)  # WORD is 16-bit
        elif isinstance(expr, UpperExpr) or isinstance(expr, LowerExpr):
            symbol = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
            if not symbol or symbol.type_expr is None:
                raise CodegenError(f'Undefined variable: {expr.name}')
            ty = symbol.type_expr
            if hasattr(ty, 'index_range'):
                lower = self.eval_const_expr(ty.index_range.low)
                upper = None if ty.index_range.high is None else self.eval_const_expr(ty.index_range.high)
            elif hasattr(ty, 'lower_bound') and hasattr(ty, 'upper_bound'):
                lower = ty.lower_bound
                upper = ty.upper_bound
            else:
                raise CodegenError(f"{type(expr).__name__[:-4].upper()} expects an array variable")
            bound = upper if isinstance(expr, UpperExpr) else lower
            if bound is None:
                raise CodegenError(f"{type(expr).__name__[:-4].upper()} could not resolve bound for {expr.name}")
            return ir.Constant(ir.IntType(32), bound)
        elif isinstance(expr, Identifier):
            # A named constant used as a value (e.g. FOR i := 0 TO size)
            key = expr.name.upper()
            if key in self.constants:
                return self._const_ir(key)
            if key == 'NULL':
                return self.null_lstring_ptr()
            symbol = self.scope.lookup(expr.name) or self.scope.lookup(key)
            if not symbol:
                raise CodegenError(f'Undefined variable: {expr.name}')
            if symbol.type_expr is None and getattr(symbol.llvm_value, 'function_type', None) and len(symbol.llvm_value.function_type.args) == 0:
                return self.builder.call(symbol.llvm_value, [])
            # Parameters are passed by value, don't load them
            if symbol.is_parameter:
                return symbol.llvm_value
            # For string/array variables, return pointer without loading (inline aggregates)
            # For scalar variables, load the value
            from ast_nodes import LStringType as ASTLStringType
            if isinstance(symbol.type_expr, (ResolvedLStringType, ResolvedStringType, ASTLStringType, ArrayType)):
                return symbol.llvm_value  # Return pointer to aggregate
            elif isinstance(symbol.type_expr, NamedType) and symbol.type_expr.name.upper() in {'STRING', 'LSTRING'}:
                return symbol.llvm_value  # Return pointer to aggregate
            return self.builder.load(symbol.llvm_value)
        elif isinstance(expr, SetConstructor):
            return self.codegen_set_constructor(expr)
        elif isinstance(expr, Designator):
            symbol = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
            if not symbol:
                raise CodegenError(f'Undefined variable: {expr.name}')
            # Parameters are passed by value, don't load them
            if symbol.is_parameter:
                return symbol.llvm_value

            ptr = self.resolve_designator_ptr(expr)
            # If the designator is a constant, return its value directly (not a pointer)
            if not isinstance(ptr.type, ir.PointerType):
                return ptr
            # For aggregate designators, return pointer without loading (inline aggregates)
            if isinstance(ptr.type.pointee, (ir.ArrayType, ir.LiteralStructType, ir.IdentifiedStructType)):
                return ptr  # Return pointer to aggregate
            return self.builder.load(ptr)
        elif isinstance(expr, BinOp):
            return self.codegen_binop(expr)
        elif isinstance(expr, UnaryOp):
            return self.codegen_unaryop(expr)
        elif isinstance(expr, FuncCall):
            return self.codegen_func_call(expr)
        elif isinstance(expr, RetypeExpr):
            # 1. Generate code for the inner expression
            val = self.codegen_expr(expr.expr)

            # 2. Get the target LLVM type
            target_llvm_type = self.llvm_type(NamedType(expr.type_id, None))

            # Helper to calculate LLVM type size in bytes
            def llvm_type_size(ty: ir.Type) -> int:
                if isinstance(ty, ir.IntType):
                    return (ty.width + 7) // 8
                elif isinstance(ty, ir.FloatType):
                    return 4
                elif isinstance(ty, ir.DoubleType):
                    return 8
                elif isinstance(ty, ir.PointerType):
                    return 8
                elif isinstance(ty, ir.ArrayType):
                    return ty.count * llvm_type_size(ty.element)
                elif isinstance(ty, (ir.LiteralStructType, ir.IdentifiedStructType)):
                    return sum(llvm_type_size(f) for f in ty.elements)
                return 4

            # 3. Get pointer to the memory representation of target size.
            #
            # An LLVM pointer reaching here is ambiguous (checklist 9.9): it is
            # either the *address of* an aggregate value (STRING/LSTRING/ARRAY/
            # RECORD), in which case RETYPE reinterprets the pointee by loading
            # through the bitcast, OR it is a genuine Pascal pointer *value* (a
            # ``^T`` variable, ADR/ADS, NIL), in which case RETYPE must
            # reinterpret the address bits and must NOT dereference. We split on
            # the Pascal type of the inner expression; only when that is
            # inconclusive do we fall back to the LLVM type (a non-aggregate
            # pointee can only be a scalar pointer value, so it is safe to treat
            # as bits; an aggregate pointee defaults to the legacy load-through).
            is_ptr_value = self.retype_source_is_pointer_value(expr.expr)
            if is_ptr_value is None and isinstance(val.type, ir.PointerType):
                is_ptr_value = not isinstance(
                    val.type.pointee,
                    (ir.ArrayType, ir.LiteralStructType, ir.IdentifiedStructType))

            if isinstance(val.type, ir.PointerType) and not is_ptr_value:
                # Aggregate address: reinterpret the bytes the pointer refers to.
                ptr = val
                casted_ptr = self.builder.bitcast(ptr, ir.PointerType(target_llvm_type))
            elif isinstance(val.type, ir.PointerType):
                # Genuine pointer value: reinterpret the address bits themselves
                # by spilling the pointer to a slot and bitcasting the slot,
                # exactly as a non-pointer scalar is handled below.
                source_size = llvm_type_size(val.type)
                target_size = llvm_type_size(target_llvm_type)
                if source_size >= target_size:
                    ptr = self.builder.alloca(val.type)
                    self.builder.store(val, ptr)
                    casted_ptr = self.builder.bitcast(ptr, ir.PointerType(target_llvm_type))
                else:
                    ptr = self.builder.alloca(target_llvm_type)
                    self.builder.store(self.zero_initializer(target_llvm_type), ptr)
                    source_ptr = self.builder.bitcast(ptr, ir.PointerType(val.type))
                    self.builder.store(val, source_ptr)
                    casted_ptr = ptr
            else:
                source_size = llvm_type_size(val.type)
                target_size = llvm_type_size(target_llvm_type)

                if source_size >= target_size:
                    # Source is larger or equal. Allocate source type.
                    ptr = self.builder.alloca(val.type)
                    self.builder.store(val, ptr)
                    casted_ptr = self.builder.bitcast(ptr, ir.PointerType(target_llvm_type))
                else:
                    # Target is larger. Allocate target type.
                    ptr = self.builder.alloca(target_llvm_type)
                    self.builder.store(self.zero_initializer(target_llvm_type), ptr)
                    # Bitcast ptr to source pointer to store the smaller source value
                    source_ptr = self.builder.bitcast(ptr, ir.PointerType(val.type))
                    self.builder.store(val, source_ptr)
                    casted_ptr = ptr

            # 5. Process any selectors
            if expr.selectors:
                cur_type = self.resolve_type_alias(NamedType(expr.type_id, None))
                for selector in expr.selectors:
                    if selector.kind == 'INDEX':
                        # RETYPE indexing is raw-memory navigation (the index is
                        # a 0-based element offset into the reinterpreted bytes),
                        # so it deliberately does not subtract a lower bound.
                        index = self.codegen_expr(selector.index_or_field)
                        if isinstance(casted_ptr.type.pointee, ir.ArrayType):
                            casted_ptr = self.builder.gep(casted_ptr, [ir.Constant(ir.IntType(32), 0), index])
                        else:
                            casted_ptr = self.builder.gep(casted_ptr, [index])
                        _, cur_type = self.array_lower_bound(cur_type)
                    elif selector.kind == 'FIELD':
                        fidx, ftype = self.record_field_index(cur_type, selector.index_or_field)
                        if fidx is None:
                            raise CodegenError(f"RETYPE: cannot access field '{selector.index_or_field}' on type {cur_type}")
                        casted_ptr = self.builder.gep(casted_ptr, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), fidx)])
                        cur_type = ftype
                    elif selector.kind == 'DEREF':
                        casted_ptr = self.builder.load(casted_ptr)
                        base = self.resolve_type_alias(cur_type) if cur_type is not None else None
                        cur_type = getattr(base, 'base', None) or getattr(base, 'target_type', None)

            # 6. If the resulting type is an aggregate, return the pointer. Otherwise load the value.
            if isinstance(casted_ptr.type.pointee, (ir.ArrayType, ir.LiteralStructType, ir.IdentifiedStructType)):
                return casted_ptr
            return self.builder.load(casted_ptr)
        else:
            raise CodegenError(f'Expression type {type(expr).__name__} not yet supported')

    def codegen_set_constructor(self, expr: SetConstructor) -> ir.Value:
        """Codegen a set constructor as a 256-bit bitvector.

        Constant elements and ranges fold into a compile-time set constant;
        non-constant elements and range bounds are set at runtime (single bits
        inline, ranges via a small loop). Reversed ranges are treated as empty.
        """
        # First fold every element we can evaluate at compile time. Collect the
        # remaining (dynamic) elements for runtime bit-setting.
        words = [0, 0, 0, 0]
        dynamic: List[Union[Expression, RangeExpr]] = []
        for element in expr.elements:
            if isinstance(element, RangeExpr):
                low = self._try_const(element.low)
                high = self._try_const(element.high)
                if low is not None and high is not None:
                    if low <= high:
                        for value in range(low, high + 1):
                            self._set_constant_bit(words, value)
                    continue
                dynamic.append(element)
            else:
                value = self._try_const(element)
                if value is not None:
                    self._set_constant_bit(words, value)
                else:
                    dynamic.append(element)

        const_set = ir.Constant(self.set_llvm_type(), [ir.Constant(ir.IntType(64), word) for word in words])
        if not dynamic:
            return const_set

        # Runtime path: materialize the constant part in a temporary and OR in
        # the dynamic elements bit by bit.
        slot = self.builder.alloca(self.set_llvm_type(), name='settmp')
        self.builder.store(const_set, slot)
        for element in dynamic:
            if isinstance(element, RangeExpr):
                self._set_runtime_range(slot, element.low, element.high)
            else:
                self._set_runtime_bit(slot, self.codegen_expr(element))
        return self.builder.load(slot)

    def _try_const(self, expr: Expression) -> Optional[int]:
        """Evaluate an expression as a constant ordinal, or None if not constant."""
        try:
            return self.eval_const_expr(expr)
        except CodegenError:
            return None

    def _normalize_ordinal(self, value: ir.Value) -> ir.Value:
        """Coerce a set element/ordinal value to i32."""
        if isinstance(value.type, ir.IntType):
            if value.type.width < 32:
                return self.builder.zext(value, ir.IntType(32))
            if value.type.width > 32:
                return self.builder.trunc(value, ir.IntType(32))
        return value

    def _set_runtime_bit(self, slot: ir.Value, ordinal: ir.Value) -> None:
        """OR one runtime ordinal bit into the set stored at ``slot``."""
        ordinal = self._normalize_ordinal(ordinal)
        word_index = self.builder.udiv(ordinal, ir.Constant(ir.IntType(32), 64))
        bit_index = self.builder.urem(ordinal, ir.Constant(ir.IntType(32), 64))
        word_ptr = self.builder.gep(slot, [ir.Constant(ir.IntType(32), 0), word_index])
        word = self.builder.load(word_ptr)
        bit_index64 = self.builder.zext(bit_index, ir.IntType(64))
        mask = self.builder.shl(ir.Constant(ir.IntType(64), 1), bit_index64)
        self.builder.store(self.builder.or_(word, mask), word_ptr)

    def _set_runtime_range(self, slot: ir.Value, low_expr: Expression, high_expr: Expression) -> None:
        """Set every bit in [low, high] at runtime via a counted loop."""
        low = self._normalize_ordinal(self.codegen_expr(low_expr))
        high = self._normalize_ordinal(self.codegen_expr(high_expr))
        counter = self.builder.alloca(ir.IntType(32), name='setrange')
        self.builder.store(low, counter)
        cond_block = self.builder.append_basic_block('setrange.cond')
        body_block = self.builder.append_basic_block('setrange.body')
        end_block = self.builder.append_basic_block('setrange.end')
        self.builder.branch(cond_block)

        self.builder.position_at_end(cond_block)
        cur = self.builder.load(counter)
        self.builder.cbranch(self.builder.icmp_signed('<=', cur, high), body_block, end_block)

        self.builder.position_at_end(body_block)
        self._set_runtime_bit(slot, self.builder.load(counter))
        nxt = self.builder.add(self.builder.load(counter), ir.Constant(ir.IntType(32), 1))
        self.builder.store(nxt, counter)
        self.builder.branch(cond_block)

        self.builder.position_at_end(end_block)

    def _set_constant_bit(self, words: List[int], value: int) -> None:
        """Set one ordinal bit in a four-word set constant."""
        if value < 0 or value > 255:
            raise CodegenError(f'Set element ordinal out of range 0..255: {value}')
        words[value // 64] |= 1 << (value % 64)

    def codegen_binop(self, expr: BinOp) -> ir.Value:
        """Codegen binary operation."""
        if expr.op in {'AND_THEN', 'OR_ELSE'}:
            return self.codegen_short_circuit_binop(expr)

        left = self.codegen_expr(expr.left)
        right = self.codegen_expr(expr.right)

        if self.is_set_value(left) or self.is_set_value(right):
            return self.codegen_set_binop(expr.op, left, right)

        # SLASH is always real division in Pascal (7/2 = 3.5), so force double
        # even when both operands are integer-typed.
        is_real = (isinstance(left.type, ir.DoubleType) or isinstance(right.type, ir.DoubleType) or expr.op == 'SLASH')
        if is_real:
            if isinstance(left.type, ir.IntType):
                left = self.builder.sitofp(left, ir.DoubleType())
            if isinstance(right.type, ir.IntType):
                right = self.builder.sitofp(right, ir.DoubleType())

        if expr.op == 'PLUS':
            return self.builder.fadd(left, right) if is_real else self.builder.add(left, right)
        elif expr.op == 'MINUS':
            return self.builder.fsub(left, right) if is_real else self.builder.sub(left, right)
        elif expr.op == 'MUL':
            return self.builder.fmul(left, right) if is_real else self.builder.mul(left, right)
        elif expr.op == 'SLASH' or expr.op == 'DIV':
            return self.builder.fdiv(left, right) if is_real else self.builder.sdiv(left, right)
        elif expr.op == 'MOD':
            return self.builder.frem(left, right) if is_real else self.builder.srem(left, right)
        elif expr.op == 'AND':
            return self.builder.and_(left, right)
        elif expr.op == 'OR':
            return self.builder.or_(left, right)
        elif expr.op == 'XOR':
            return self.builder.xor(left, right)
        elif expr.op == 'EQ':
            return self.builder.fcmp_ordered('==', left, right) if is_real else self.builder.icmp_signed('==', left, right)
        elif expr.op == 'NEQ':
            return self.builder.fcmp_ordered('!=', left, right) if is_real else self.builder.icmp_signed('!=', left, right)
        elif expr.op == 'LT':
            return self.builder.fcmp_ordered('<', left, right) if is_real else self.builder.icmp_signed('<', left, right)
        elif expr.op == 'LE':
            return self.builder.fcmp_ordered('<=', left, right) if is_real else self.builder.icmp_signed('<=', left, right)
        elif expr.op == 'GT':
            return self.builder.fcmp_ordered('>', left, right) if is_real else self.builder.icmp_signed('>', left, right)
        elif expr.op == 'GE':
            return self.builder.fcmp_ordered('>=', left, right) if is_real else self.builder.icmp_signed('>=', left, right)
        else:
            raise CodegenError(f'Unknown binary operator: {expr.op}')

    def is_set_value(self, value: ir.Value) -> bool:
        """Return True for the fixed Pascal set aggregate representation."""
        typ = value.type
        return isinstance(typ, ir.ArrayType) and typ.count == 4 and isinstance(typ.element, ir.IntType) and typ.element.width == 64

    def set_word(self, value: ir.Value, index: int) -> ir.Value:
        return self.builder.extract_value(value, index)

    def set_from_words(self, words: List[ir.Value]) -> ir.Value:
        result: ir.Value = ir.Constant(self.set_llvm_type(), None)
        for index, word in enumerate(words):
            result = self.builder.insert_value(result, word, index)
        return result

    def codegen_set_binop(self, op: str, left: ir.Value, right: ir.Value) -> ir.Value:
        """Lower Pascal set operators over the fixed [4 x i64] representation."""
        if op == 'IN':
            if not self.is_set_value(right):
                raise CodegenError('Right operand of IN must be a set')
            return self.codegen_set_member(left, right)

        if not self.is_set_value(left) or not self.is_set_value(right):
            raise CodegenError(f'Operator {op} requires set operands')

        if op == 'PLUS':
            return self.set_from_words([self.builder.or_(self.set_word(left, i), self.set_word(right, i)) for i in range(4)])
        if op == 'MUL':
            return self.set_from_words([self.builder.and_(self.set_word(left, i), self.set_word(right, i)) for i in range(4)])
        if op == 'MINUS':
            all_ones = ir.Constant(ir.IntType(64), (1 << 64) - 1)
            return self.set_from_words([self.builder.and_(self.set_word(left, i), self.builder.xor(self.set_word(right, i), all_ones)) for i in range(4)])
        if op in {'EQ', 'NEQ'}:
            eq = self.codegen_set_equal(left, right)
            return eq if op == 'EQ' else self.builder.not_(eq)
        if op in {'LE', 'GE', 'LT', 'GT'}:
            subset = self.codegen_set_subset(left, right) if op in {'LE', 'LT'} else self.codegen_set_subset(right, left)
            if op in {'LE', 'GE'}:
                return subset
            return self.builder.and_(subset, self.builder.not_(self.codegen_set_equal(left, right)))
        raise CodegenError(f'Unknown set operator: {op}')

    def codegen_set_member(self, ordinal: ir.Value, set_value: ir.Value) -> ir.Value:
        """Lower ordinal IN set to a bit test."""
        if isinstance(ordinal.type, ir.IntType) and ordinal.type.width < 32:
            ordinal = self.builder.zext(ordinal, ir.IntType(32))
        elif isinstance(ordinal.type, ir.IntType) and ordinal.type.width > 32:
            ordinal = self.builder.trunc(ordinal, ir.IntType(32))
        word_index = self.builder.udiv(ordinal, ir.Constant(ir.IntType(32), 64))
        bit_index = self.builder.urem(ordinal, ir.Constant(ir.IntType(32), 64))
        words_ptr = self.builder.alloca(self.set_llvm_type(), name='settmp')
        self.builder.store(set_value, words_ptr)
        word_ptr = self.builder.gep(words_ptr, [ir.Constant(ir.IntType(32), 0), word_index])
        word = self.builder.load(word_ptr)
        bit_index64 = self.builder.zext(bit_index, ir.IntType(64))
        mask = self.builder.shl(ir.Constant(ir.IntType(64), 1), bit_index64)
        masked = self.builder.and_(word, mask)
        return self.builder.icmp_unsigned('!=', masked, ir.Constant(ir.IntType(64), 0))

    def codegen_set_equal(self, left: ir.Value, right: ir.Value) -> ir.Value:
        result = self.builder.icmp_unsigned('==', self.set_word(left, 0), self.set_word(right, 0))
        for i in range(1, 4):
            eq = self.builder.icmp_unsigned('==', self.set_word(left, i), self.set_word(right, i))
            result = self.builder.and_(result, eq)
        return result

    def codegen_set_subset(self, left: ir.Value, right: ir.Value) -> ir.Value:
        """Return left <= right for sets: every bit in left is also in right."""
        result: Optional[ir.Value] = None
        for i in range(4):
            left_word = self.set_word(left, i)
            right_word = self.set_word(right, i)
            included = self.builder.icmp_unsigned('==', self.builder.and_(left_word, right_word), left_word)
            result = included if result is None else self.builder.and_(result, included)
        return result if result is not None else ir.Constant(ir.IntType(1), 1)

    def codegen_short_circuit_binop(self, expr: BinOp) -> ir.Value:
        """Codegen short-circuit boolean AND THEN / OR ELSE."""
        left = self.to_bool(self.codegen_expr(expr.left))

        rhs_block = self.current_function.append_basic_block(name='sc_rhs')
        merge_block = self.current_function.append_basic_block(name='sc_merge')

        if expr.op == 'AND_THEN':
            self.builder.cbranch(left, rhs_block, merge_block)
            short_value = ir.Constant(ir.IntType(1), 0)
        elif expr.op == 'OR_ELSE':
            self.builder.cbranch(left, merge_block, rhs_block)
            short_value = ir.Constant(ir.IntType(1), 1)
        else:
            raise CodegenError(f'Unknown short-circuit operator: {expr.op}')

        left_block = self.builder.block

        self.builder.position_at_end(rhs_block)
        right = self.to_bool(self.codegen_expr(expr.right))
        right_block = self.builder.block
        self.builder.branch(merge_block)

        self.builder.position_at_end(merge_block)
        result = self.builder.phi(ir.IntType(1), name='sc_result')
        result.add_incoming(short_value, left_block)
        result.add_incoming(right, right_block)
        return result

    def codegen_unaryop(self, expr: UnaryOp) -> ir.Value:
        """Codegen unary operation."""
        operand = self.codegen_expr(expr.operand)

        if expr.op == 'MINUS':
            if isinstance(operand.type, ir.DoubleType):
                return self.builder.fsub(ir.Constant(ir.DoubleType(), 0.0), operand)
            return self.builder.neg(operand)
        elif expr.op == 'NOT':
            # Logical NOT: invert the boolean
            return self.builder.not_(operand)
        else:
            raise CodegenError(f'Unknown unary operator: {expr.op}')

    def codegen_func_call(self, expr: FuncCall) -> ir.Value:
        """Codegen function call."""
        lookup_name = expr.name.upper()
        symbol = self.scope.lookup(lookup_name) or self.scope.lookup(expr.name)

        if symbol:
            fn = symbol.llvm_value
            param_types = fn.function_type.args
            param_modes = self.proc_param_modes.get(expr.name.lower(), [])
            args = []
            for i, arg in enumerate(expr.args):
                mode = param_modes[i] if i < len(param_modes) else None
                v = self.codegen_actual_arg(arg, mode)
                if i < len(param_types):
                    v = self.coerce_arg(v, param_types[i])
                args.append(v)
            return self.builder.call(fn, args)

        # Inline built-in functions
        if lookup_name == 'CHR':
            val = self.codegen_expr(expr.args[0])
            if val.type.width == 8:
                return val
            elif val.type.width > 8:
                return self.builder.trunc(val, ir.IntType(8))
            else:
                return self.builder.zext(val, ir.IntType(8))
        elif lookup_name == 'ORD':
            val = self.codegen_expr(expr.args[0])
            if val.type.width == 32:
                return val
            return self.builder.zext(val, ir.IntType(32))
        elif lookup_name == 'ODD':
            val = self.codegen_expr(expr.args[0])
            one = ir.Constant(ir.IntType(32), 1)
            result = self.builder.and_(val, one)
            zero = ir.Constant(ir.IntType(32), 0)
            return self.builder.icmp_signed('!=', result, zero)
        elif lookup_name == 'SUCC':
            val = self.codegen_expr(expr.args[0])
            one = ir.Constant(ir.IntType(32), 1)
            return self.builder.add(val, one)
        elif lookup_name == 'PRED':
            val = self.codegen_expr(expr.args[0])
            one = ir.Constant(ir.IntType(32), 1)
            return self.builder.sub(val, one)
        elif lookup_name == 'ABS':
            val = self.codegen_expr(expr.args[0])
            if isinstance(val.type, ir.DoubleType):
                zero = ir.Constant(ir.DoubleType(), 0.0)
                is_neg = self.builder.fcmp_ordered('<', val, zero)
                neg = self.builder.fsub(zero, val)
                return self.builder.select(is_neg, neg, val)
            if isinstance(val.type, ir.IntType):
                zero = ir.Constant(val.type, 0)
                is_neg = self.builder.icmp_signed('<', val, zero)
                neg = self.builder.sub(zero, val)
                return self.builder.select(is_neg, neg, val)
            raise CodegenError(f'ABS not supported for type {val.type}')
        elif lookup_name == 'SQR':
            val = self.codegen_expr(expr.args[0])
            if isinstance(val.type, ir.DoubleType):
                return self.builder.fmul(val, val)
            if isinstance(val.type, ir.IntType):
                return self.builder.mul(val, val)
            raise CodegenError(f'SQR not supported for type {val.type}')
        elif lookup_name in {'HIBYTE', 'LOBYTE'}:
            val = self.codegen_expr(expr.args[0])
            if not isinstance(val.type, ir.IntType):
                raise CodegenError(f'{lookup_name} not supported for type {val.type}')
            shifted = self.builder.lshr(val, ir.Constant(val.type, 8)) if lookup_name == 'HIBYTE' else val
            return self.builder.trunc(shifted, ir.IntType(8))
        elif lookup_name == 'WRD':
            val = self.codegen_expr(expr.args[0])
            vt = val.type
            if isinstance(vt, ir.DoubleType):
                raise CodegenError('WRD: REAL argument not supported')
            elif isinstance(vt, ir.PointerType):
                # pointer → integer → truncate to 16-bit WORD
                val = self.builder.ptrtoint(val, ir.IntType(32))
                return self.builder.trunc(val, ir.IntType(16))
            elif isinstance(vt, ir.IntType):
                w = vt.width
                if w > 16:
                    # Same 16-bit two's-complement pattern: trunc handles
                    # "add MAXWORD+1 if negative" without a branch
                    return self.builder.trunc(val, ir.IntType(16))
                elif w == 16:
                    return val  # WORD → WORD: identity
                else:
                    # CHAR (i8) / BOOLEAN (i8) / small enum → zero-extend
                    return self.builder.zext(val, ir.IntType(16))
            raise CodegenError(f'WRD: unsupported value type {vt}')
        elif lookup_name == 'BYWORD':
            hi_val = self.codegen_expr(expr.args[0])
            lo_val = self.codegen_expr(expr.args[1])

            def _to_i16(v: ir.Value) -> ir.Value:
                """Widen or narrow any integer value to i16."""
                w = v.type.width
                if w < 16:
                    return self.builder.zext(v, ir.IntType(16))
                if w > 16:
                    return self.builder.trunc(v, ir.IntType(16))
                return v

            hi16 = self.builder.and_(_to_i16(hi_val), ir.Constant(ir.IntType(16), 0x00FF))
            lo16 = self.builder.and_(_to_i16(lo_val), ir.Constant(ir.IntType(16), 0x00FF))
            return self.builder.or_(self.builder.shl(hi16, ir.Constant(ir.IntType(16), 8)), lo16)
        elif lookup_name in {'SQRT', 'SIN', 'COS', 'LN', 'EXP', 'ARCTAN'}:
            val = self.codegen_expr(expr.args[0])
            if isinstance(val.type, ir.IntType):
                val = self.builder.sitofp(val, ir.DoubleType())
            elif not isinstance(val.type, ir.DoubleType):
                raise CodegenError(f'{lookup_name} not supported for type {val.type}')

            libm_names = {'SQRT': 'sqrt', 'SIN': 'sin', 'COS': 'cos', 'LN': 'log', 'EXP': 'exp', 'ARCTAN': 'atan'}
            c_name = libm_names[lookup_name]
            double_ty = ir.DoubleType()
            try:
                fn = self.module.get_global(c_name)
            except KeyError:
                fn = ir.Function(self.module, ir.FunctionType(double_ty, [double_ty]), name=c_name)
            return self.builder.call(fn, [val])
        elif lookup_name == 'TRUNC':
            # REAL -> INTEGER: truncate toward zero (manual 11-7)
            val = self.codegen_expr(expr.args[0])
            if isinstance(val.type, ir.IntType):
                val = self.builder.sitofp(val, ir.DoubleType())
            elif not isinstance(val.type, ir.DoubleType):
                raise CodegenError(f'TRUNC not supported for type {val.type}')
            return self.builder.fptosi(val, ir.IntType(32))
        elif lookup_name == 'ROUND':
            # REAL -> INTEGER: rounds away from zero (manual 11-7).
            # Implemented as: fptosi(x + copysign(0.5, x)), i.e. add +0.5
            # for non-negative inputs and -0.5 for negative inputs, then
            # truncate.  This gives half-away-from-zero without requiring
            # libm.round (llvm.round lowers to a libm call in llvmlite).
            val = self.codegen_expr(expr.args[0])
            if isinstance(val.type, ir.IntType):
                val = self.builder.sitofp(val, ir.DoubleType())
            elif not isinstance(val.type, ir.DoubleType):
                raise CodegenError(f'ROUND not supported for type {val.type}')
            zero = ir.Constant(ir.DoubleType(), 0.0)
            half = ir.Constant(ir.DoubleType(), 0.5)
            neg_half = ir.Constant(ir.DoubleType(), -0.5)
            is_neg = self.builder.fcmp_ordered('<', val, zero)
            adj = self.builder.select(is_neg, neg_half, half)
            rounded = self.builder.fadd(val, adj)
            return self.builder.fptosi(rounded, ir.IntType(32))
        elif lookup_name == 'FLOAT':
            # INTEGER -> REAL: sitofp (manual 11-7)
            val = self.codegen_expr(expr.args[0])
            if not isinstance(val.type, ir.IntType):
                raise CodegenError(f'FLOAT not supported for type {val.type}')
            return self.builder.sitofp(val, ir.DoubleType())

        raise CodegenError(f'Undefined function: {expr.name}')

    # ========================================================================
    # Built-in Functions
    # ========================================================================

    def _declare_libm_func(self, name: str, ret_type: ir.Type, arg_types: List[ir.Type]) -> ir.Function:
        if name not in [f.name for f in self.module.functions]:
            fn_type = ir.FunctionType(ret_type, arg_types)
            ir.Function(self.module, fn_type, name=name)
        return next(f for f in self.module.functions if f.name == name)

    def printf_func(self) -> ir.Function:
        """Declare or fetch printf."""
        if 'printf' not in [f.name for f in self.module.functions]:
            printf_type = ir.FunctionType(ir.IntType(32), [ir.PointerType(ir.IntType(8))], var_arg=True)
            ir.Function(self.module, printf_type, name='printf')
        return next(f for f in self.module.functions if f.name == 'printf')

    def coerce_printf_int(self, val: ir.Value) -> ir.Value:
        """printf dynamic width/precision arguments must be C int-sized."""
        if isinstance(val.type, ir.IntType):
            if val.type.width < 32:
                return self.builder.zext(val, ir.IntType(32))
            if val.type.width > 32:
                return self.builder.trunc(val, ir.IntType(32))
        return val

    def build_write_format_and_args(self, args: List[Union[Expression, WriteArg]]) -> tuple[str, List[ir.Value]]:
        fmt_parts = []
        printf_args = []
        for arg in args:
            if isinstance(arg, WriteArg):
                expr = arg.expr
                width = arg.width
                precision = arg.precision
            else:
                expr = arg
                width = None
                precision = None

            val = self.codegen_expr(expr)

            # Enum value printed by symbolic name (checklist 9.8): index the
            # per-enum name table by the runtime ordinal and print the resulting
            # i8* with %s. The i8* flows through the format logic below, which
            # already maps a char pointer to %s.
            enum_names = self.write_enum_names(expr)
            if enum_names is not None:
                table = self.enum_name_table(enum_names)
                zero = ir.Constant(ir.IntType(32), 0)
                name_ptr = self.builder.gep(table, [zero, val])
                val = self.builder.load(name_ptr)

            # Check if it is a string variable (non-literal)
            is_string_var = False
            is_lstring_var = False
            lstring_len = None
            string_max_len = 0
            from ast_nodes import LStringType as ASTLStringType
            from type_system import LStringType, StringType
            if not isinstance(expr, StringLiteral) and not isinstance(expr, NilLiteral) and not (isinstance(expr, Identifier) and expr.name.upper() == 'NULL'):
                t = None
                if isinstance(expr, Identifier):
                    symbol = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
                    if symbol:
                        t = symbol.type_expr
                elif isinstance(expr, Designator):
                    symbol = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
                    if symbol:
                        t = symbol.type_expr

                if isinstance(t, (LStringType, ASTLStringType)):
                    is_string_var = True
                    is_lstring_var = True
                    string_max_len = t.max_len if hasattr(t, 'max_len') else 256
                elif isinstance(t, StringType):
                    is_string_var = True
                    is_lstring_var = False
                    string_max_len = t.max_len if hasattr(t, 'max_len') else 256
                elif isinstance(t, NamedType):
                    name_up = t.name.upper()
                    if name_up == 'LSTRING':
                        is_string_var = True
                        is_lstring_var = True
                        string_max_len = int(t.param) if t.param else 256
                    elif name_up == 'STRING':
                        is_string_var = True
                        is_lstring_var = False
                        string_max_len = int(t.param) if t.param else 256
                    elif name_up in self.type_aliases:
                        aliased = self.type_aliases[name_up]
                        if isinstance(aliased, (LStringType, ASTLStringType)):
                            is_string_var = True
                            is_lstring_var = True
                            string_max_len = aliased.max_len if hasattr(aliased, 'max_len') else 256
                        elif isinstance(aliased, StringType):
                            is_string_var = True
                            is_lstring_var = False
                            string_max_len = aliased.max_len if hasattr(aliased, 'max_len') else 256

            if is_string_var:
                # val is aggregate pointer [n+1 x i8]* or [n x i8]*
                # Extract chars pointer: skip length byte for LSTRING, start at 0 for STRING
                zero = ir.Constant(ir.IntType(32), 0)
                one = ir.Constant(ir.IntType(32), 1)
                if is_lstring_var:
                    # LSTRING (manual 6-19): WRITE emits the *current length*
                    # string. Length is byte [0]; characters are [1..]. No null
                    # terminator exists, so use %.*s with the runtime length.
                    len_byte = self.builder.load(self.builder.gep(val, [zero, zero]))
                    lstring_len = self.builder.zext(len_byte, ir.IntType(32))
                    val = self.builder.gep(val, [zero, one])
                else:
                    # STRING: chars at [0, 0], not null-terminated, use %.*s with length
                    val = self.builder.gep(val, [zero, zero])

            prefix = '%'
            if width is not None:
                prefix += '*'
                printf_args.append(self.coerce_printf_int(self.codegen_expr(width)))
            if precision is not None:
                prefix += '.*'
                printf_args.append(self.coerce_printf_int(self.codegen_expr(precision)))

            # Determine format based on LLVM type
            val_type_str = str(val.type)
            if is_string_var:
                # Both STRING and LSTRING write with %.*s. STRING uses its
                # (blank-padded) compile-time max length; LSTRING uses the
                # runtime length read from byte [0] above.
                length_arg = (lstring_len if is_lstring_var else ir.Constant(ir.IntType(32), string_max_len))
                if not precision:  # Only add length if not already in precision
                    prefix += '.*'
                    # printf consumes dynamic args as width, then precision,
                    # then value. The implicit length IS the precision, so it
                    # must follow any width arg already appended for this item
                    # (appending puts it immediately before the value below).
                    printf_args.append(length_arg)
                suffix = 's'
            elif 'i32' in val_type_str:
                suffix = 'd'
            elif 'i16' in val_type_str:
                suffix = 'u'
            elif 'i8*' in val_type_str:
                suffix = 's'
            elif 'i8' in val_type_str:
                suffix = 'c'
            elif 'i1' in val_type_str:
                # Booleans as integers for printf
                suffix = 'd'
            elif 'double' in val_type_str or 'float' in val_type_str:
                suffix = 'f'
            else:
                suffix = 's'

            fmt_parts.append(prefix + suffix)
            printf_args.append(val)

        return "".join(fmt_parts), printf_args

    def builtin_writeln(self, args: List[Union[Expression, WriteArg]]) -> None:
        """Implement WRITELN for any combination of string/integer/boolean types."""
        printf_func = self.printf_func()
        fmt_str, printf_args = self.build_write_format_and_args(args)
        fmt_str += "\n"
        fmt_const = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_str) + 1), bytearray(fmt_str.encode('utf-8') + b'\0'))
        fmt_global = ir.GlobalVariable(self.module, fmt_const.type, name=self.unique_name('fmt'))
        fmt_global.initializer = fmt_const
        fmt_global.global_constant = True
        fmt_ptr = self.builder.gep(fmt_global, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])

        self.builder.call(printf_func, [fmt_ptr] + printf_args)

    def builtin_write(self, args: List[Union[Expression, WriteArg]]) -> None:
        """Implement WRITE for any combination of string/integer/boolean types (no newline)."""
        printf_func = self.printf_func()
        fmt_str, printf_args = self.build_write_format_and_args(args)
        fmt_const = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_str) + 1), bytearray(fmt_str.encode('utf-8') + b'\0'))
        fmt_global = ir.GlobalVariable(self.module, fmt_const.type, name=self.unique_name('fmt'))
        fmt_global.initializer = fmt_const
        fmt_global.global_constant = True
        fmt_ptr = self.builder.gep(fmt_global, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])

        self.builder.call(printf_func, [fmt_ptr] + printf_args)

    def builtin_readln(self, args: List[Expression]) -> None:
        """Implement READLN for integers."""
        # Declare scanf if not already declared
        if 'scanf' not in [f.name for f in self.module.functions]:
            scanf_type = ir.FunctionType(ir.IntType(32), [ir.PointerType(ir.IntType(8))], var_arg=True)
            ir.Function(self.module, scanf_type, name='scanf')

        # Get scanf function
        scanf_func = None
        for func in self.module.functions:
            if func.name == 'scanf':
                scanf_func = func
                break

        for arg in args:
            if isinstance(arg, Identifier):
                symbol = self.scope.lookup(arg.name)
                if not symbol:
                    raise CodegenError(f'Undefined variable: {arg.name}')

                # Can't read into parameters
                if symbol.is_parameter:
                    raise CodegenError(f'Cannot read into parameter: {arg.name}')

                fmt_str = "%d"
                fmt_const = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_str) + 1), bytearray(fmt_str.encode('utf-8') + b'\0'))
                fmt_global = ir.GlobalVariable(self.module, fmt_const.type, name=self.unique_name('fmt'))
                fmt_global.initializer = fmt_const
                fmt_ptr = self.builder.bitcast(fmt_global, ir.PointerType(ir.IntType(8)))

                self.builder.call(scanf_func, [fmt_ptr, symbol.llvm_value])

    def memcpy_func(self) -> ir.Function:
        for func in self.module.functions:
            if func.name == 'memcpy':
                return func
        memcpy_type = ir.FunctionType(ir.PointerType(ir.IntType(8)), [ir.PointerType(ir.IntType(8)), ir.PointerType(ir.IntType(8)), ir.IntType(64)])
        return ir.Function(self.module, memcpy_type, name='memcpy')

    def memset_func(self) -> ir.Function:
        for func in self.module.functions:
            if func.name == 'memset':
                return func
        memset_type = ir.FunctionType(ir.PointerType(ir.IntType(8)), [ir.PointerType(ir.IntType(8)), ir.IntType(32), ir.IntType(64)])
        return ir.Function(self.module, memset_type, name='memset')

    def runtime_error_func(self) -> ir.Function:
        """Declare or fetch a runtime error handler (calls abort)."""
        for func in self.module.functions:
            if func.name == 'abort':
                return func
        # abort() takes no arguments and returns never (noreturn), but we declare void
        abort_type = ir.FunctionType(ir.VoidType(), [])
        return ir.Function(self.module, abort_type, name='abort')

    def get_string_chars_and_len(self, expr: Expression) -> tuple[ir.Value, ir.Value]:
        """Returns (chars_ptr: ir.Value, length: ir.Value) for any string expression.
        
        The chars_ptr points directly to the first character.
        The length is an i32 representing the dynamic or static length.
        """
        if isinstance(expr, StringLiteral):
            val_str = expr.value
            if val_str.startswith("'") and val_str.endswith("'"):
                val_str = val_str[1:-1]
            val_str = val_str.replace("''", "'")
            lit_len = len(val_str)

            chars_ptr = self.codegen_expr(expr)
            length = ir.Constant(ir.IntType(32), lit_len)
            return chars_ptr, length

        elif isinstance(expr, NilLiteral) or (isinstance(expr, Identifier) and expr.name.upper() == 'NULL'):
            chars_ptr = self.null_lstring_ptr()
            length = ir.Constant(ir.IntType(32), 0)
            return chars_ptr, length

        # val is now a pointer to the inline aggregate [n+1 x i8] or [n x i8]
        val = self.codegen_expr(expr)

        # Determine string type details
        t = None
        if isinstance(expr, Identifier):
            symbol = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
            if symbol:
                t = symbol.type_expr
        elif isinstance(expr, Designator):
            symbol = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
            if symbol:
                t = symbol.type_expr

        is_str, max_len, is_lstring = self.get_string_type_info(t)

        zero = ir.Constant(ir.IntType(32), 0)
        one = ir.Constant(ir.IntType(32), 1)

        if is_lstring:
            # LSTRING [n+1 x i8]: byte [0] = length, bytes [1..n] = chars
            len_ptr = self.builder.gep(val, [zero, zero])
            len_byte = self.builder.load(len_ptr)
            length = self.builder.zext(len_byte, ir.IntType(32))
            chars_ptr = self.builder.gep(val, [zero, one])
        else:
            # STRING [n x i8]: bytes [0..n-1] = chars, no length prefix
            chars_ptr = self.builder.gep(val, [zero, zero])
            length = ir.Constant(ir.IntType(32), max_len)

        return chars_ptr, length

    def _dest_string_max_len(self, arg: Expression) -> int:
        """Resolve the declared capacity (max length) of a string destination."""
        t = None
        if isinstance(arg, (Identifier, Designator)):
            symbol = self.scope.lookup(arg.name) or self.scope.lookup(arg.name.upper())
            if symbol:
                t = symbol.type_expr
        _is_str, max_len, _is_lstring = self.get_string_type_info(t)
        return max_len

    def _guard_string_capacity(self, need_len: ir.Value, max_len: int, label: str):
        """Emit the manual's string range check (errors if upper(D) < need_len).

        If `need_len` exceeds `max_len`, call the runtime error handler (abort)
        and mark the block unreachable; otherwise fall through. Mirrors the
        LSTRING assignment path. Returns the post-check block, which the caller
        must branch to once the guarded work is done.
        """
        cond = self.builder.icmp_signed('<=', need_len, ir.Constant(ir.IntType(32), max_len))
        parent = self.builder.block.parent
        ok_block = parent.append_basic_block(label + '_ok')
        err_block = parent.append_basic_block(label + '_overflow')
        end_block = parent.append_basic_block(label + '_end')
        self.builder.cbranch(cond, ok_block, err_block)
        self.builder.position_at_end(err_block)
        self.builder.call(self.runtime_error_func(), [])
        self.builder.unreachable()
        self.builder.position_at_end(ok_block)
        return end_block

    def builtin_concat(self, args: List[Expression]) -> None:
        """CONCAT(VAR D: LSTRING; CONST S: STRING).

        S is appended to D; D's length grows by length(S). Manual 11-20:
        error if upper(D) < length(D) + upper(S).
        """
        D_arg = args[0]
        if isinstance(D_arg, Identifier):
            D_arg = Designator(D_arg.name, [])
        D_ptr = self.resolve_designator_ptr(D_arg)
        # D_ptr is now directly the aggregate pointer [n+1 x i8]

        src_chars, src_len = self.get_string_chars_and_len(args[1])
        src_len_64 = self.builder.zext(src_len, ir.IntType(64))

        zero = ir.Constant(ir.IntType(32), 0)
        one = ir.Constant(ir.IntType(32), 1)

        # Load current length from byte [0]
        len_ptr = self.builder.gep(D_ptr, [zero, zero])
        dest_len_byte = self.builder.load(len_ptr)
        dest_len = self.builder.zext(dest_len_byte, ir.IntType(32))

        # Range check BEFORE writing: length(D) + length(S) must fit in upper(D).
        new_len = self.builder.add(dest_len, src_len)
        max_len = self._dest_string_max_len(args[0])
        end_block = self._guard_string_capacity(new_len, max_len, 'concat')

        # Append S at [1 + dest_len ..]
        dest_chars = self.builder.gep(D_ptr, [zero, one])
        append_ptr = self.builder.gep(dest_chars, [dest_len])
        self.builder.call(self.memcpy_func(), [append_ptr, src_chars, src_len_64])

        # Update length byte [0]. LSTRING is length-prefixed (manual 6-18),
        # not null-terminated.
        new_len_byte = self.builder.trunc(new_len, ir.IntType(8))
        self.builder.store(new_len_byte, len_ptr)

        self.builder.branch(end_block)
        self.builder.position_at_end(end_block)

    def builtin_copylst(self, args: List[Expression]) -> None:
        """COPYLST(CONST S: STRING; VAR D: LSTRING).

        Copies S to D; D's length is set to length(S). Manual 11-20:
        error if upper(D) < upper(S).
        """
        src_chars, src_len = self.get_string_chars_and_len(args[0])
        src_len_64 = self.builder.zext(src_len, ir.IntType(64))

        D_arg = args[1]
        if isinstance(D_arg, Identifier):
            D_arg = Designator(D_arg.name, [])
        D_ptr = self.resolve_designator_ptr(D_arg)
        # D_ptr is now directly the aggregate pointer [n+1 x i8]

        zero = ir.Constant(ir.IntType(32), 0)
        one = ir.Constant(ir.IntType(32), 1)

        # Range check BEFORE writing: length(S) must fit in upper(D).
        max_len = self._dest_string_max_len(args[1])
        end_block = self._guard_string_capacity(src_len, max_len, 'copylst')

        # Copy to bytes [1..n]
        dest_chars = self.builder.gep(D_ptr, [zero, one])
        self.builder.call(self.memcpy_func(), [dest_chars, src_chars, src_len_64])

        # Store length in byte [0]. LSTRING is length-prefixed (manual 6-18),
        # not null-terminated.
        len_ptr = self.builder.gep(D_ptr, [zero, zero])
        src_len_byte = self.builder.trunc(src_len, ir.IntType(8))
        self.builder.store(src_len_byte, len_ptr)

        self.builder.branch(end_block)
        self.builder.position_at_end(end_block)

    def builtin_copystr(self, args: List[Expression]) -> None:
        """COPYSTR(CONST S: STRING; VAR D: STRING)"""
        src_chars, src_len = self.get_string_chars_and_len(args[0])
        src_len_64 = self.builder.zext(src_len, ir.IntType(64))

        D_arg = args[1]
        if isinstance(D_arg, Identifier):
            D_arg = Designator(D_arg.name, [])
        D_ptr = self.resolve_designator_ptr(D_arg)
        # D_ptr is now directly the aggregate pointer [n x i8]

        # Get D's maximum length
        t = None
        if isinstance(args[1], Identifier):
            symbol = self.scope.lookup(args[1].name) or self.scope.lookup(args[1].name.upper())
            if symbol:
                t = symbol.type_expr
        elif isinstance(args[1], Designator):
            symbol = self.scope.lookup(args[1].name) or self.scope.lookup(args[1].name.upper())
            if symbol:
                t = symbol.type_expr

        is_str, max_len, is_lstring = self.get_string_type_info(t)

        zero = ir.Constant(ir.IntType(32), 0)

        # Range check BEFORE writing (manual 11-20: error if upper(D) < upper(S)).
        # This also guarantees pad_len below is non-negative.
        end_block = self._guard_string_capacity(src_len, max_len, 'copystr')

        # STRING has no length byte; copy to [0]
        dest_chars = self.builder.gep(D_ptr, [zero, zero])
        self.builder.call(self.memcpy_func(), [dest_chars, src_chars, src_len_64])

        # Blank-pad remaining characters from [src_len] to [max_len-1] with 0x20
        pad_ptr = self.builder.gep(D_ptr, [zero, src_len])
        pad_len = self.builder.sub(ir.Constant(ir.IntType(32), max_len), src_len)
        pad_len_64 = self.builder.zext(pad_len, ir.IntType(64))
        self.builder.call(self.memset_func(), [pad_ptr, ir.Constant(ir.IntType(32), 0x20), pad_len_64])

        self.builder.branch(end_block)
        self.builder.position_at_end(end_block)

    def retype_source_is_pointer_value(self, expr) -> Optional[bool]:
        """Classify the inner expression of a RETYPE for the pointer-vs-aggregate
        conflation documented in checklist item 9.9.

        ``codegen_expr`` returns an LLVM pointer for two unrelated reasons:

        * the value *is* an aggregate (STRING/LSTRING/ARRAY/RECORD) and the
          pointer is merely the address of those bytes — RETYPE should
          reinterpret the *pointee* (load through the bitcast);
        * the value is a genuine Pascal pointer scalar (a ``^T`` variable, an
          ``ADR``/``ADS`` factor, ``NIL``) — RETYPE should reinterpret the
          *address bits*, not dereference them.

        Returns ``True`` if the inner expression is a genuine pointer value,
        ``False`` if it is an aggregate address, and ``None`` if it cannot be
        classified from the AST alone (caller falls back to the LLVM type).
        """
        # ADR/ADS factors and NIL are always pointer *values*.
        if isinstance(expr, (AdrExpr, AdsExpr, NilLiteral)):
            return True
        # A nested RETYPE's value type is its declared target type.
        if isinstance(expr, RetypeExpr):
            t = self.resolve_type_alias(NamedType(expr.type_id, None))
            return isinstance(t, PointerType)
        # Named variables/designators: consult the declared Pascal type.
        if isinstance(expr, (Identifier, Designator)):
            sym = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
            if sym is None or sym.type_expr is None:
                return None
            t = self.resolve_type_alias(sym.type_expr)
            # A selector chain (field/index/deref) yields whatever the selected
            # component is; that is not necessarily a pointer, so do not claim
            # to know — let the caller fall back to the LLVM-type heuristic.
            if getattr(expr, 'selectors', None):
                return None
            return isinstance(t, PointerType)
        return None

    def resolve_type_alias(self, type_expr):
        """Unwrap NamedType aliases (e.g. ``TYPE arr = ARRAY[..]``) to the
        underlying declared type. Built-in names and unknown names are returned
        unchanged. Cycle-safe."""
        seen = set()
        while isinstance(type_expr, NamedType):
            key = type_expr.name.upper()
            if key in seen or key not in self.type_aliases:
                break
            seen.add(key)
            type_expr = self.type_aliases[key]
        return type_expr

    # ------------------------------------------------------------------
    # Enum support (checklist 9.8)
    # ------------------------------------------------------------------
    def enum_value_list(self, type_expr) -> Optional[List[str]]:
        """Return the ordered member names if ``type_expr`` is (or aliases) an
        enum type, else ``None``. Enums lower to i32 ordinals; this recovers the
        symbolic names for WRITE."""
        t = self.resolve_type_alias(type_expr)
        if isinstance(t, EnumType):  # AST EnumType carries `.values`
            return list(t.values)
        return None

    def write_enum_names(self, expr) -> Optional[List[str]]:
        """If a WRITE argument denotes an enum value (an enum variable, an enum
        designator without further selectors, or a bare enum member literal),
        return the member-name list so it can be printed by name. Returns
        ``None`` for anything else — notably arbitrary enum-typed *expressions*
        (e.g. ``SUCC(c)``), which still print as their ordinal because codegen
        does not carry per-expression Pascal types."""
        if isinstance(expr, (Identifier, Designator)) and not getattr(expr, 'selectors', None):
            sym = self.scope.lookup(expr.name) or self.scope.lookup(expr.name.upper())
            if sym is not None and sym.type_expr is not None:
                names = self.enum_value_list(sym.type_expr)
                if names:
                    return names
            # Bare enum member literal, e.g. WRITE(Red).
            return self.enum_member_names.get(expr.name.upper())
        return None

    def enum_name_table(self, names: List[str]) -> ir.GlobalVariable:
        """Get (or build once) a constant ``[n x i8*]`` table of pointers to the
        null-terminated member-name strings for an enum, indexable by ordinal."""
        key = '\x00'.join(names)
        cached = self._enum_name_tables.get(key)
        if cached is not None:
            return cached
        i8 = ir.IntType(8)
        i8p = ir.PointerType(i8)
        zero = ir.Constant(ir.IntType(32), 0)
        ptrs = []
        for nm in names:
            data = bytearray(nm.encode('utf-8') + b'\0')
            const = ir.Constant(ir.ArrayType(i8, len(data)), data)
            g = ir.GlobalVariable(self.module, const.type, name=self.unique_name('enumname'))
            g.initializer = const
            g.global_constant = True
            ptrs.append(g.gep([zero, zero]))
        table_type = ir.ArrayType(i8p, len(names))
        table = ir.GlobalVariable(self.module, table_type, name=self.unique_name('enumtab'))
        table.global_constant = True
        table.initializer = ir.Constant(table_type, ptrs)
        self._enum_name_tables[key] = table
        return table

    def array_lower_bound(self, type_expr) -> tuple[Optional[int], Any]:
        """For a (possibly aliased) array type, return ``(lower_bound, element_type)``.

        Returns ``(None, None)`` for anything that is not a genuine indexable
        array. In particular STRING/LSTRING are deliberately excluded: their
        element offsets follow a length-prefix convention (LSTRING reserves
        byte 0 for the length), not array lower-bound subtraction, so they must
        keep their existing indexing behavior.
        """
        t = self.resolve_type_alias(type_expr)
        # AST ArrayType carries an index_range with constant-foldable bounds.
        if hasattr(t, 'index_range') and getattr(t, 'index_range', None) is not None:
            try:
                low = self.eval_const_expr(t.index_range.low)
            except Exception:
                low = None
            return low, getattr(t, 'element_type', None)
        # Resolved type_system.ArrayType carries lower_bound + element_type.
        # (StringType/LStringType expose max_len instead and are excluded.)
        if hasattr(t, 'lower_bound') and hasattr(t, 'element_type') and not hasattr(t, 'max_len'):
            return t.lower_bound, t.element_type
        return None, None

    def record_field_index(self, type_expr, field_name: str) -> tuple[Optional[int], Any]:
        """For a (possibly aliased) record type, return ``(llvm_struct_index,
        field_ast_type)`` for ``field_name``, matching the layout in
        ``llvm_type``. Field lookup is case-insensitive (Pascal identifiers are
        case-insensitive). Returns ``(None, None)`` if not a record / no match.
        """
        t = self.resolve_type_alias(type_expr)
        if not isinstance(t, RecordType):  # AST RecordType
            return None, None
        target = field_name.upper()
        idx = 0
        for names, ftype in t.fields:
            for nm in names:
                if nm.upper() == target:
                    return idx, ftype
                idx += 1
        return None, None

    def get_array_bounds(self, type_expr) -> tuple[int, int]:
        type_expr = self.resolve_type_alias(type_expr)
        if hasattr(type_expr, 'index_range') and type_expr.index_range:
            low = self.eval_const_expr(type_expr.index_range.low)
            high = self.eval_const_expr(type_expr.index_range.high) if type_expr.index_range.high else low
            return low, high
        elif hasattr(type_expr, 'lower_bound') and hasattr(type_expr, 'upper_bound'):
            return type_expr.lower_bound, type_expr.upper_bound
        return 1, 10

    def builtin_pack(self, args: List[Expression]) -> None:
        """PACK(CONST A: unpacked-array; I: index; VAR Z: packed-array)

        Semantics (manual / ISO): for j := low(Z) to high(Z),
        Z[j] := A[I + (j - low(Z))]. Storage for both arrays is 0-based
        ([high-low+1 x elem]), so every Pascal index is translated to a slot
        by subtracting that array's lower bound.
        """
        a_arg, i_arg, z_arg = args[0], args[1], args[2]

        a_ptr = self.codegen_expr(a_arg)
        z_ptr = self.codegen_expr(z_arg)
        i_val = self.codegen_expr(i_arg)

        a_low = self._designator_array_low(a_arg)
        z_low, z_high = self._designator_array_bounds(z_arg)

        j_var = self.builder.alloca(ir.IntType(32), name='pack_j')
        self.builder.store(ir.Constant(ir.IntType(32), z_low), j_var)

        loop_block = self.current_function.append_basic_block(name='pack_loop')
        body_block = self.current_function.append_basic_block(name='pack_body')
        end_block = self.current_function.append_basic_block(name='pack_end')

        self.builder.branch(loop_block)
        self.builder.position_at_end(loop_block)

        j_val = self.builder.load(j_var)
        cond = self.builder.icmp_signed('<=', j_val, ir.Constant(ir.IntType(32), z_high))
        self.builder.cbranch(cond, body_block, end_block)

        self.builder.position_at_end(body_block)

        # offset = j - low(Z): 0-based position, which is also Z's storage slot.
        offset = self.builder.sub(j_val, ir.Constant(ir.IntType(32), z_low))
        # A storage slot = (I + offset) - low(A).
        a_pascal = self.builder.add(offset, i_val)
        a_slot = self.builder.sub(a_pascal, ir.Constant(ir.IntType(32), a_low))

        a_elem_ptr = self.builder.gep(a_ptr, [ir.Constant(ir.IntType(32), 0), a_slot])
        elem_val = self.builder.load(a_elem_ptr)

        z_elem_ptr = self.builder.gep(z_ptr, [ir.Constant(ir.IntType(32), 0), offset])
        self.builder.store(elem_val, z_elem_ptr)

        next_j = self.builder.add(j_val, ir.Constant(ir.IntType(32), 1))
        self.builder.store(next_j, j_var)
        self.builder.branch(loop_block)

        self.builder.position_at_end(end_block)

    def builtin_unpack(self, args: List[Expression]) -> None:
        """UNPACK(CONST Z: packed-array; VAR A: unpacked-array; I: index)

        Semantics (manual / ISO): for j := low(Z) to high(Z),
        A[I + (j - low(Z))] := Z[j]. As in PACK, every Pascal index is
        translated to a 0-based storage slot.
        """
        z_arg, a_arg, i_arg = args[0], args[1], args[2]

        z_ptr = self.codegen_expr(z_arg)
        a_ptr = self.codegen_expr(a_arg)
        i_val = self.codegen_expr(i_arg)

        a_low = self._designator_array_low(a_arg)
        z_low, z_high = self._designator_array_bounds(z_arg)

        j_var = self.builder.alloca(ir.IntType(32), name='unpack_j')
        self.builder.store(ir.Constant(ir.IntType(32), z_low), j_var)

        loop_block = self.current_function.append_basic_block(name='unpack_loop')
        body_block = self.current_function.append_basic_block(name='unpack_body')
        end_block = self.current_function.append_basic_block(name='unpack_end')

        self.builder.branch(loop_block)
        self.builder.position_at_end(loop_block)

        j_val = self.builder.load(j_var)
        cond = self.builder.icmp_signed('<=', j_val, ir.Constant(ir.IntType(32), z_high))
        self.builder.cbranch(cond, body_block, end_block)

        self.builder.position_at_end(body_block)

        offset = self.builder.sub(j_val, ir.Constant(ir.IntType(32), z_low))
        a_pascal = self.builder.add(offset, i_val)
        a_slot = self.builder.sub(a_pascal, ir.Constant(ir.IntType(32), a_low))

        z_elem_ptr = self.builder.gep(z_ptr, [ir.Constant(ir.IntType(32), 0), offset])
        elem_val = self.builder.load(z_elem_ptr)

        a_elem_ptr = self.builder.gep(a_ptr, [ir.Constant(ir.IntType(32), 0), a_slot])
        self.builder.store(elem_val, a_elem_ptr)

        next_j = self.builder.add(j_val, ir.Constant(ir.IntType(32), 1))
        self.builder.store(next_j, j_var)
        self.builder.branch(loop_block)

        self.builder.position_at_end(end_block)

    def _runtime_fillmove(self, name: str, args: List[Expression]) -> None:
        src = self.codegen_expr(args[0])
        dst = self.codegen_expr(args[1])
        length = self.codegen_expr(args[2])
        fn = self.scope.lookup(name)
        if not fn:
            raise CodegenError(f'Undefined procedure: {name}')
        self.builder.call(fn.llvm_value, [src, dst, length])

    def builtin_movel(self, args: List[Expression]) -> None:
        self._runtime_fillmove('MOVEL', args)

    def builtin_mover(self, args: List[Expression]) -> None:
        self._runtime_fillmove('MOVER', args)

    def builtin_movesl(self, args: List[Expression]) -> None:
        self._runtime_fillmove('MOVESL', args)

    def builtin_movesr(self, args: List[Expression]) -> None:
        self._runtime_fillmove('MOVESR', args)

    def builtin_abort(self, args: List[Expression]) -> None:
        abort_fn = self.runtime_error_func()
        self.builder.call(abort_fn, [])
        self.builder.unreachable()

    def _designator_array_bounds(self, arg) -> tuple[int, int]:
        """(lower, upper) bounds of the array a designator names; (1, 10) fallback."""
        name = arg.name if isinstance(arg, (Identifier, Designator)) else ""
        sym = self.scope.lookup(name) or self.scope.lookup(name.upper()) if name else None
        if sym and sym.type_expr:
            return self.get_array_bounds(sym.type_expr)
        return 1, 10

    def _designator_array_low(self, arg) -> int:
        """Lower bound of the array a designator names; 0 fallback (no shift)."""
        name = arg.name if isinstance(arg, (Identifier, Designator)) else ""
        sym = self.scope.lookup(name) or self.scope.lookup(name.upper()) if name else None
        if sym and sym.type_expr:
            low, _ = self.array_lower_bound(sym.type_expr)
            if low is not None:
                return low
        return 0

    # ========================================================================
    # Utilities
    # ========================================================================

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

    def unique_name(self, prefix: str) -> str:
        """Generate a unique name."""
        if not hasattr(self, '_name_counter'):
            self._name_counter = {}
        if prefix not in self._name_counter:
            self._name_counter[prefix] = 0
        self._name_counter[prefix] += 1
        return f'{prefix}_{self._name_counter[prefix]}'


def compile_to_llvm(ast: Union[ProgramUnit, ModuleUnit, InterfaceUnit, ImplementationUnit], verbose: bool = False, source_file: Optional[str] = None) -> str:
    """Compile AST to LLVM IR string."""
    codegen = Codegen(verbose=verbose, source_file=source_file)
    module = codegen.codegen(ast)
    return str(module)
