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

from typing import Any, Dict, List, Optional, Union

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder

from ast_nodes import *
from parser import parse_file


class CodegenError(Exception):
    pass


class Symbol:
    """A symbol in the current scope."""

    def __init__(self, name: str, llvm_value: Any, type_expr: Type, is_parameter: bool = False):
        self.name = name
        self.llvm_value = llvm_value  # ir.Value or ir.Function or ir.GlobalVariable
        self.type_expr = type_expr
        self.is_parameter = is_parameter  # True if this is a function parameter (passed by value)


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
        self.constants: Dict[str, int] = {}  # compile-time constant values, keyed UPPER
        self.current_interface_decls: Dict[str, Declaration] = {}
        self.verbose = verbose

    def _log(self, msg: str) -> None:
        """Emit a diagnostic line to stderr when verbose mode is on."""
        if self.verbose:
            import sys
            print(f'[codegen] {msg}', file=sys.stderr)

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
            return ir.IntType(32)
        elif isinstance(type_expr, PointerType):
            base_type = self.llvm_type(type_expr.base)
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
        else:
            raise CodegenError(f'Type {type(type_expr).__name__} not yet supported')

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
            for stmt in unit.block.body:
                self.codegen_stmt(stmt)

            # Default return 0
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
                self.codegen_decl(ProcDecl(decl.name, decl.params, getattr(decl, 'attributes', []), body=None) if isinstance(decl, ProcDecl) else FuncDecl(decl.name, decl.params, decl.return_type, getattr(decl, 'attributes', []), body=None))

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

            for stmt in unit.init_body:
                self.codegen_stmt(stmt)

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
            # Type definitions don't generate code in MVP
            pass
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

    def codegen_var_decl(self, decl: VarDecl) -> None:
        """Codegen for VAR declaration."""
        llvm_type = self.llvm_type(decl.type_expr)

        if self.builder:
            # Local variable (inside a function)
            for name in decl.names:
                alloca = self.builder.alloca(llvm_type, name=name)
                self.scope.define(name, alloca, decl.type_expr)
        else:
            # Global variable - define with a zero initializer
            for name in decl.names:
                global_var = ir.GlobalVariable(self.module, llvm_type, name=name)
                global_var.initializer = self.zero_initializer(llvm_type)
                self.scope.define(name, global_var, decl.type_expr)

    def codegen_proc_decl(self, decl: ProcDecl) -> None:
        """Codegen for PROCEDURE declaration."""
        effective_decl = decl
        iface_decl = self.current_interface_decls.get(decl.name.lower()) if decl.name else None
        if iface_decl and not decl.params:
            effective_decl = iface_decl

        # Flatten parameter types: one per name in each Param group
        param_types = []
        for param in effective_decl.params:
            param_type = self.llvm_type(param.type_expr)
            for _ in param.names:
                param_types.append(param_type)
        func_type = ir.FunctionType(ir.IntType(32), param_types)

        # Create function
        func = ir.Function(self.module, func_type, name=decl.name)
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
                self.scope.define(name, arg, param.type_expr, is_parameter=True)

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

        # Flatten parameter types: one per name in each Param group
        param_types = []
        for param in effective_decl.params:
            param_type = self.llvm_type(param.type_expr)
            for _ in param.names:
                param_types.append(param_type)
        return_type = self.llvm_type(decl.return_type)
        func_type = ir.FunctionType(return_type, param_types)

        # Create function
        func = ir.Function(self.module, func_type, name=decl.name)
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
                self.scope.define(name, arg, param.type_expr, is_parameter=True)

        # Allocate space for return value
        return_alloca = self.builder.alloca(return_type, name='return_value')
        self.scope.define(decl.name, return_alloca, decl.return_type)
        self.builder.store(ir.Constant(return_type, 0.0) if isinstance(return_type, ir.DoubleType) else ir.Constant(return_type, 0), return_alloca)

        # Codegen body
        for inner_decl in decl.body.decls:
            self.codegen_decl(inner_decl)

        for stmt in decl.body.body:
            self.codegen_stmt(stmt)

        # Default return / function result
        result = self.builder.load(return_alloca)
        self.builder.ret(result)

        # Restore context
        self.builder = prev_builder
        self.current_function = prev_func
        self.scope = prev_scope

    # ========================================================================
    # Statements
    # ========================================================================

    def codegen_stmt(self, stmt: Statement) -> None:
        """Codegen a statement."""
        self._log(f'stmt  {type(stmt).__name__}')
        if isinstance(stmt, CompoundStmt):
            for s in stmt.stmts:
                self.codegen_stmt(s)
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
            # TODO: handle BREAK
            pass
        elif isinstance(stmt, CycleStmt):
            # TODO: handle CYCLE
            pass
        elif isinstance(stmt, WithStmt):
            # TODO: handle WITH
            pass
        elif isinstance(stmt, LabelStmt):
            self.codegen_stmt(stmt.stmt)
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

        # Resolve the pointer (handles array indexing, etc.)
        ptr = self.resolve_designator_ptr(stmt.target)
        value = self.codegen_expr(stmt.expr)

        # Handle simple type conversions
        if hasattr(ptr.type, 'pointee'):
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
            else:
                raise CodegenError(f'Undefined procedure: {stmt.name}')
        else:
            # User-defined procedure
            fn = symbol.llvm_value
            param_types = fn.function_type.args
            args = []
            for i, arg in enumerate(stmt.args):
                v = self.codegen_expr(arg)
                if i < len(param_types):
                    v = self.coerce_arg(v, param_types[i])
                args.append(v)
            self.builder.call(fn, args)

    def codegen_if_stmt(self, stmt: IfStmt) -> None:
        """Codegen for IF statement."""
        cond = self.codegen_expr(stmt.cond)
        # Reduce to i1 (handles boolean loads as well as integer conditions)
        cond_bit = self.to_bool(cond)

        # Create basic blocks
        then_block = self.current_function.append_basic_block(name='if_then')
        end_block = self.current_function.append_basic_block(name='if_end')

        if stmt.else_branch:
            else_block = self.current_function.append_basic_block(name='if_else')
            self.builder.cbranch(cond_bit, then_block, else_block)

            # Then branch
            self.builder.position_at_end(then_block)
            self.codegen_stmt(stmt.then_branch)
            self.builder.branch(end_block)

            # Else branch
            self.builder.position_at_end(else_block)
            self.codegen_stmt(stmt.else_branch)
            self.builder.branch(end_block)
        else:
            # No else branch
            self.builder.cbranch(cond_bit, then_block, end_block)

            # Then branch
            self.builder.position_at_end(then_block)
            self.codegen_stmt(stmt.then_branch)
            self.builder.branch(end_block)

        # Continue after if
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

        self.builder.branch(loop_block)

        # Loop condition
        self.builder.position_at_end(loop_block)
        current_val = self.builder.load(loop_var)
        end_val = self.codegen_expr(stmt.end)

        if stmt.direction == 'TO':
            cond = self.builder.icmp_signed('<=', current_val, end_val)
        else:  # DOWNTO
            cond = self.builder.icmp_signed('>=', current_val, end_val)

        # Loop body block
        body_block = self.current_function.append_basic_block(name='for_body')
        self.builder.cbranch(cond, body_block, end_block)

        # Loop body
        self.builder.position_at_end(body_block)
        self.codegen_stmt(stmt.body)

        # Increment/decrement
        current_val = self.builder.load(loop_var)
        if stmt.direction == 'TO':
            next_val = self.builder.add(current_val, ir.Constant(ir.IntType(32), 1))
        else:  # DOWNTO
            next_val = self.builder.sub(current_val, ir.Constant(ir.IntType(32), 1))
        self.builder.store(next_val, loop_var)
        self.builder.branch(loop_block)

        # Continue after loop
        self.builder.position_at_end(end_block)

    def codegen_while_stmt(self, stmt: WhileStmt) -> None:
        """Codegen for WHILE loop."""
        loop_block = self.current_function.append_basic_block(name='while_loop')
        body_block = self.current_function.append_basic_block(name='while_body')
        end_block = self.current_function.append_basic_block(name='while_end')

        self.builder.branch(loop_block)

        # Condition check
        self.builder.position_at_end(loop_block)
        cond = self.codegen_expr(stmt.cond)
        cond_bit = self.to_bool(cond)
        self.builder.cbranch(cond_bit, body_block, end_block)

        # Body
        self.builder.position_at_end(body_block)
        self.codegen_stmt(stmt.body)
        self.builder.branch(loop_block)

        # After loop
        self.builder.position_at_end(end_block)

    def codegen_repeat_stmt(self, stmt: RepeatStmt) -> None:
        """Codegen for REPEAT..UNTIL loop."""
        loop_block = self.current_function.append_basic_block(name='repeat_loop')
        end_block = self.current_function.append_basic_block(name='repeat_end')

        self.builder.branch(loop_block)

        # Loop body
        self.builder.position_at_end(loop_block)
        for s in stmt.body:
            self.codegen_stmt(s)

        # Condition (until = exit when true)
        cond = self.codegen_expr(stmt.cond)
        cond_bit = self.to_bool(cond)
        self.builder.cbranch(cond_bit, end_block, loop_block)

        # After loop
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

    def resolve_designator_ptr(self, designator: Designator) -> ir.Value:
        """Resolve a designator to its LLVM pointer (handles arrays/selectors)."""
        symbol = self.scope.lookup(designator.name)
        if not symbol:
            symbol = self.scope.lookup(designator.name.upper())
            if not symbol:
                raise CodegenError(f'Undefined variable: {designator.name}')

        ptr = symbol.llvm_value

        if designator.selectors:
            for selector in designator.selectors:
                if selector.kind == 'INDEX':
                    index = self.codegen_expr(selector.index_or_field)
                    # GEP requires [0, index] for pointers to arrays, or [index] for flat pointers
                    if isinstance(ptr.type.pointee, ir.ArrayType):
                        ptr = self.builder.gep(ptr, [ir.Constant(ir.IntType(32), 0), index])
                    else:
                        ptr = self.builder.gep(ptr, [index])
                elif selector.kind == 'FIELD':
                    # Record field access (simplified)
                    pass
                elif selector.kind == 'DEREF':
                    # Pointer dereference
                    ptr = self.builder.load(ptr)
        return ptr

    # ========================================================================
    # Type-size, argument coercion, and boolean helpers
    # ========================================================================

    def _scalar_size(self, name: str) -> int:
        """Size in bytes of a scalar/built-in type, by name."""
        return _SCALAR_SIZES.get(name.upper(), 4)

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

    def get_type_size(self, t: Type) -> int:
        """Size in bytes of an AST type node (consults constants for bounds)."""
        if isinstance(t, BuiltinType):
            return self._scalar_size(t.name)
        elif isinstance(t, NamedType):
            return self._scalar_size(t.name)
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
        elif isinstance(expr, Identifier):
            # A named constant used as a value (e.g. FOR i := 0 TO size)
            key = expr.name.upper()
            if key in self.constants:
                return ir.Constant(ir.IntType(32), self.constants[key])
            symbol = self.scope.lookup(expr.name) or self.scope.lookup(key)
            if not symbol:
                raise CodegenError(f'Undefined variable: {expr.name}')
            # Parameters are passed by value, don't load them
            if symbol.is_parameter:
                return symbol.llvm_value
            return self.builder.load(symbol.llvm_value)
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
            return self.builder.load(ptr)
        elif isinstance(expr, BinOp):
            return self.codegen_binop(expr)
        elif isinstance(expr, UnaryOp):
            return self.codegen_unaryop(expr)
        elif isinstance(expr, FuncCall):
            return self.codegen_func_call(expr)
        else:
            raise CodegenError(f'Expression type {type(expr).__name__} not yet supported')

    def codegen_binop(self, expr: BinOp) -> ir.Value:
        """Codegen binary operation."""
        if expr.op in {'AND_THEN', 'OR_ELSE'}:
            return self.codegen_short_circuit_binop(expr)

        left = self.codegen_expr(expr.left)
        right = self.codegen_expr(expr.right)

        is_real = isinstance(left.type, ir.DoubleType) or isinstance(right.type, ir.DoubleType)
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
            return self.builder.fdiv(left, right) if is_real or expr.op == 'SLASH' else self.builder.sdiv(left, right)
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
            return self.builder.neg(operand)
        elif expr.op == 'NOT':
            # Logical NOT: invert the boolean
            return self.builder.not_(operand)
        else:
            raise CodegenError(f'Unknown unary operator: {expr.op}')

    def codegen_func_call(self, expr: FuncCall) -> ir.Value:
        """Codegen function call."""
        lookup_name = expr.name.upper()

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
        elif lookup_name == 'SQRT':
            val = self.codegen_expr(expr.args[0])
            if isinstance(val.type, ir.IntType):
                val = self.builder.sitofp(val, ir.DoubleType())
            elif not isinstance(val.type, ir.DoubleType):
                raise CodegenError(f'SQRT not supported for type {val.type}')
            sqrt_fn = self.module.declare_intrinsic('llvm.sqrt', [ir.DoubleType()])
            return self.builder.call(sqrt_fn, [val])

        symbol = self.scope.lookup(lookup_name) or self.scope.lookup(expr.name)
        if not symbol:
            raise CodegenError(f'Undefined function: {expr.name}')

        fn = symbol.llvm_value
        param_types = fn.function_type.args
        args = []
        for i, arg in enumerate(expr.args):
            v = self.codegen_expr(arg)
            if i < len(param_types):
                v = self.coerce_arg(v, param_types[i])
            args.append(v)
        return self.builder.call(fn, args)

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

            prefix = '%'
            if width is not None:
                prefix += '*'
                printf_args.append(self.coerce_printf_int(self.codegen_expr(width)))
            if precision is not None:
                prefix += '.*'
                printf_args.append(self.coerce_printf_int(self.codegen_expr(precision)))

            # Determine format based on LLVM type
            val_type_str = str(val.type)
            if 'i32' in val_type_str:
                suffix = 'd'
            elif 'i16' in val_type_str:
                suffix = 'u'
            elif 'i8*' in val_type_str or '[' in val_type_str:
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

    # ========================================================================
    # Utilities
    # ========================================================================

    def eval_const_expr(self, expr: Expression) -> int:
        """Evaluate a constant expression at compile time."""
        if isinstance(expr, IntLiteral):
            return expr.value
        elif isinstance(expr, BoolLiteral):
            return 1 if expr.value else 0
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
            elif expr.op == 'NOT':
                return 0 if val else 1
        elif isinstance(expr, BinOp):
            left = self.eval_const_expr(expr.left)
            right = self.eval_const_expr(expr.right)
            if expr.op == 'PLUS':
                return left + right
            elif expr.op == 'MINUS':
                return left - right
            elif expr.op == 'MUL':
                return left * right
            elif expr.op == 'DIV' or expr.op == 'SLASH':
                return left // right if right != 0 else 0
            elif expr.op == 'MOD':
                return left % right if right != 0 else 0
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
