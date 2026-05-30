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
        self.symbols[name] = Symbol(name, llvm_value, type_expr, is_parameter)

    def lookup(self, name: str) -> Optional[Symbol]:
        """Look up a symbol, checking parent scopes."""
        if name in self.symbols:
            return self.symbols[name]
        if self.parent:
            return self.parent.lookup(name)
        return None


class Codegen:
    """LLVM IR code generator."""

    def __init__(self):
        self.module = ir.Module(name="pascal_program")
        self.builder: Optional[IRBuilder] = None
        self.scope = Scope()  # global scope
        self.current_function: Optional[ir.Function] = None
        self.current_return_block: Optional[ir.BasicBlock] = None

    # ========================================================================
    # Type System
    # ========================================================================

    def llvm_type(self, type_expr: Type) -> ir.Type:
        """Convert a Pascal type to LLVM type."""
        if isinstance(type_expr, BuiltinType):
            if type_expr.name == 'INTEGER':
                return ir.IntType(32)
            elif type_expr.name == 'BOOLEAN':
                return ir.IntType(1)
            elif type_expr.name == 'WORD':
                return ir.IntType(16)
            elif type_expr.name == 'CHAR':
                return ir.IntType(8)
            elif type_expr.name == 'REAL':
                raise CodegenError('REAL type not yet supported')
            elif type_expr.name == 'ADRMEM':
                return ir.IntType(32)  # address/memory word
            else:
                raise CodegenError(f'Unknown built-in type: {type_expr.name}')
        elif isinstance(type_expr, NamedType):
            # For now, treat as INTEGER (would need type table lookup)
            return ir.IntType(32)
        elif isinstance(type_expr, PointerType):
            base_type = self.llvm_type(type_expr.base)
            return ir.PointerType(base_type)
        elif isinstance(type_expr, ArrayType):
            elem_type = self.llvm_type(type_expr.element_type)
            # For now, fixed-size arrays
            size = 100  # placeholder
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

    def codegen_interface(self, unit: InterfaceUnit) -> ir.Module:
        """Codegen for INTERFACE unit (declarations only)."""
        for decl in unit.decls:
            self.codegen_decl(decl)
        return self.module

    def codegen_implementation(self, unit: ImplementationUnit) -> ir.Module:
        """Codegen for IMPLEMENTATION unit."""
        for decl in unit.decls:
            self.codegen_decl(decl)
        
        # Codegen init body if present
        if unit.init_body:
            init_type = ir.FunctionType(ir.IntType(32), [])
            init_func = ir.Function(self.module, init_type, name='_init')
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
        # Evaluate constant at compile time
        value = self.eval_const_expr(decl.value)
        # Store in scope for later use (const folding)
        # For now, we just evaluate and discard

    def codegen_var_decl(self, decl: VarDecl) -> None:
        """Codegen for VAR declaration."""
        llvm_type = self.llvm_type(decl.type_expr)
        
        if self.builder:
            # Local variable (inside a function)
            for name in decl.names:
                alloca = self.builder.alloca(llvm_type, name=name)
                self.scope.define(name, alloca, decl.type_expr)
        else:
            # Global variable
            global_var = ir.GlobalVariable(self.module, llvm_type, name=decl.names[0])
            for name in decl.names:
                self.scope.define(name, global_var, decl.type_expr)

    def codegen_proc_decl(self, decl: ProcDecl) -> None:
        """Codegen for PROCEDURE declaration."""
        # Build parameter types
        param_types = [self.llvm_type(param.type_expr) for param in decl.params]
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
        for param, arg in zip(decl.params, func.args):
            arg.name = param.names[0]
            for name in param.names:
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
        # Build parameter types
        param_types = [self.llvm_type(param.type_expr) for param in decl.params]
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
        for param, arg in zip(decl.params, func.args):
            arg.name = param.names[0]
            for name in param.names:
                self.scope.define(name, arg, param.type_expr, is_parameter=True)

        # Allocate space for return value
        return_alloca = self.builder.alloca(return_type, name='return_value')
        self.scope.define(decl.name, return_alloca, decl.return_type)

        # Codegen body
        for inner_decl in decl.body.decls:
            self.codegen_decl(inner_decl)

        for stmt in decl.body.body:
            self.codegen_stmt(stmt)

        # Default return
        self.builder.ret(ir.Constant(return_type, 0))

        # Restore context
        self.builder = prev_builder
        self.current_function = prev_func
        self.scope = prev_scope

    # ========================================================================
    # Statements
    # ========================================================================

    def codegen_stmt(self, stmt: Statement) -> None:
        """Codegen a statement."""
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
        symbol = self.scope.lookup(target_name)
        if not symbol:
            raise CodegenError(f'Undefined variable: {target_name}')
        
        # Can't assign to parameters (passed by value)
        if symbol.is_parameter:
            raise CodegenError(f'Cannot assign to parameter: {target_name}')

        value = self.codegen_expr(stmt.expr)
        self.builder.store(value, symbol.llvm_value)

    def codegen_proc_call_stmt(self, stmt: ProcCallStmt) -> None:
        """Codegen for procedure call statement."""
        symbol = self.scope.lookup(stmt.name)
        if not symbol:
            # Try built-in procedures
            if stmt.name == 'WRITELN':
                self.builtin_writeln(stmt.args)
            elif stmt.name == 'READLN':
                self.builtin_readln(stmt.args)
            else:
                raise CodegenError(f'Undefined procedure: {stmt.name}')
        else:
            # User-defined procedure
            args = [self.codegen_expr(arg) for arg in stmt.args]
            self.builder.call(symbol.llvm_value, args)

    def codegen_if_stmt(self, stmt: IfStmt) -> None:
        """Codegen for IF statement."""
        cond = self.codegen_expr(stmt.cond)
        # Convert to 1-bit for branch
        cond_bit = self.builder.icmp_signed('!=', cond, ir.Constant(ir.IntType(32), 0))

        with self.builder.if_else(cond_bit) as (then_bb, else_bb):
            with then_bb:
                self.codegen_stmt(stmt.then_branch)
            if stmt.else_branch:
                with else_bb:
                    self.codegen_stmt(stmt.else_branch)

    def codegen_for_stmt(self, stmt: ForStmt) -> None:
        """Codegen for FOR loop."""
        # Allocate loop variable (or reuse if already exists)
        symbol = self.scope.lookup(stmt.var)
        if not symbol:
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
        cond_bit = self.builder.icmp_signed('!=', cond, ir.Constant(ir.IntType(32), 0))
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
        cond_bit = self.builder.icmp_signed('!=', cond, ir.Constant(ir.IntType(32), 0))
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

    # ========================================================================
    # Expressions
    # ========================================================================

    def codegen_expr(self, expr: Expression) -> ir.Value:
        """Codegen an expression."""
        if isinstance(expr, IntLiteral):
            return ir.Constant(ir.IntType(32), expr.value)
        elif isinstance(expr, RealLiteral):
            raise CodegenError('REAL literals not yet supported')
        elif isinstance(expr, CharLiteral):
            # Convert char to int
            return ir.Constant(ir.IntType(8), ord(expr.value[0]) if expr.value else 0)
        elif isinstance(expr, StringLiteral):
            raise CodegenError('String literals not yet supported')
        elif isinstance(expr, BoolLiteral):
            return ir.Constant(ir.IntType(1), 1 if expr.value else 0)
        elif isinstance(expr, Identifier):
            symbol = self.scope.lookup(expr.name)
            if not symbol:
                raise CodegenError(f'Undefined variable: {expr.name}')
            # Parameters are passed by value, don't load them
            if symbol.is_parameter:
                return symbol.llvm_value
            return self.builder.load(symbol.llvm_value)
        elif isinstance(expr, Designator):
            # For MVP, just load the base variable
            symbol = self.scope.lookup(expr.name)
            if not symbol:
                raise CodegenError(f'Undefined variable: {expr.name}')
            # Parameters are passed by value, don't load them
            if symbol.is_parameter:
                return symbol.llvm_value
            return self.builder.load(symbol.llvm_value)
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
        left = self.codegen_expr(expr.left)
        right = self.codegen_expr(expr.right)

        if expr.op == 'PLUS':
            return self.builder.add(left, right)
        elif expr.op == 'MINUS':
            return self.builder.sub(left, right)
        elif expr.op == 'MUL':
            return self.builder.mul(left, right)
        elif expr.op == 'SLASH' or expr.op == 'DIV':
            return self.builder.sdiv(left, right)
        elif expr.op == 'MOD':
            return self.builder.srem(left, right)
        elif expr.op == 'AND':
            return self.builder.and_(left, right)
        elif expr.op == 'OR':
            return self.builder.or_(left, right)
        elif expr.op == 'XOR':
            return self.builder.xor(left, right)
        elif expr.op == 'EQ':
            return self.builder.icmp_signed('==', left, right)
        elif expr.op == 'NEQ':
            return self.builder.icmp_signed('!=', left, right)
        elif expr.op == 'LT':
            return self.builder.icmp_signed('<', left, right)
        elif expr.op == 'LE':
            return self.builder.icmp_signed('<=', left, right)
        elif expr.op == 'GT':
            return self.builder.icmp_signed('>', left, right)
        elif expr.op == 'GE':
            return self.builder.icmp_signed('>=', left, right)
        else:
            raise CodegenError(f'Unknown binary operator: {expr.op}')

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
        symbol = self.scope.lookup(expr.name)
        if not symbol:
            raise CodegenError(f'Undefined function: {expr.name}')

        args = [self.codegen_expr(arg) for arg in expr.args]
        return self.builder.call(symbol.llvm_value, args)

    # ========================================================================
    # Built-in Functions
    # ========================================================================

    def builtin_writeln(self, args: List[Expression]) -> None:
        """Implement WRITELN for integers."""
        # Declare printf if not already declared
        if 'printf' not in [f.name for f in self.module.functions]:
            printf_type = ir.FunctionType(ir.IntType(32), [ir.PointerType(ir.IntType(8))], var_arg=True)
            ir.Function(self.module, printf_type, name='printf')

        # Get printf function
        printf_func = None
        for func in self.module.functions:
            if func.name == 'printf':
                printf_func = func
                break

        # Build format string and args
        if args:
            for arg in args:
                val = self.codegen_expr(arg)
                # Create format string "%d\n" for integers
                fmt_str = "%d\n"
                fmt_const = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_str) + 1),
                                       bytearray(fmt_str.encode('utf-8') + b'\0'))
                fmt_global = ir.GlobalVariable(self.module, fmt_const.type, name=self.unique_name('fmt'))
                fmt_global.initializer = fmt_const
                fmt_ptr = self.builder.bitcast(fmt_global, ir.PointerType(ir.IntType(8)))

                self.builder.call(printf_func, [fmt_ptr, val])
        else:
            # Just print newline
            fmt_str = "\n"
            fmt_const = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_str) + 1),
                                   bytearray(fmt_str.encode('utf-8') + b'\0'))
            fmt_global = ir.GlobalVariable(self.module, fmt_const.type, name=self.unique_name('fmt'))
            fmt_global.initializer = fmt_const
            fmt_ptr = self.builder.bitcast(fmt_global, ir.PointerType(ir.IntType(8)))
            self.builder.call(printf_func, [fmt_ptr])

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
                fmt_const = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt_str) + 1),
                                       bytearray(fmt_str.encode('utf-8') + b'\0'))
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


def compile_to_llvm(ast: Union[ProgramUnit, ModuleUnit, InterfaceUnit, ImplementationUnit]) -> str:
    """Compile AST to LLVM IR string."""
    codegen = Codegen()
    module = codegen.codegen(ast)
    return str(module)
