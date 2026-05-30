"""
Static Type Checker for Pascal-1981 Compiler

Performs semantic analysis on the AST:
- Builds symbol tables
- Resolves identifier references
- Checks type compatibility
- Validates control flow types
"""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from abc import ABC, abstractmethod

from ast_nodes import (
    ASTNode, ProgramUnit, ModuleUnit, Block, Statement, Expression,
    VarDecl, ConstDecl, TypeDecl, FuncDecl, ProcDecl,
    NamedType, ArrayType as ASTArrayType, RecordType as ASTRecordType,
    Identifier, BinOp, UnaryOp, IntLiteral, RealLiteral, BoolLiteral,
    IfStmt, ForStmt, WhileStmt, RepeatStmt, CaseStmt, AssignStmt, 
    ProcCallStmt, FuncCall, Designator, ReturnStmt, Selector
)

from type_system import (
    Type, INTEGER_TYPE, BOOLEAN_TYPE, REAL_TYPE, WORD_TYPE, CHAR_TYPE,
    ArrayType, RecordType, FunctionType, ProcedureType, PointerType, SetType,
    can_assign, binary_op_result_type, unary_op_result_type
)

from symbol_table import SymbolTable, Symbol, SourceLocation


@dataclass
class TypeCheckError:
    """A type checking error or warning."""
    
    message: str
    location: Optional[SourceLocation] = None
    severity: str = 'error'  # 'error' or 'warning'
    
    def __str__(self) -> str:
        if self.location:
            return f"{self.severity.upper()} at {self.location}: {self.message}"
        return f"{self.severity.upper()}: {self.message}"


@dataclass
class TypeCheckResult:
    """Result of type checking."""
    
    success: bool
    symbol_table: SymbolTable
    errors: List[TypeCheckError]
    warnings: List[TypeCheckError]
    annotated_ast: Optional[ASTNode] = None


class TypeChecker(ABC):
    """Abstract base class for type checkers."""
    
    @abstractmethod
    def check(self, ast: ASTNode) -> TypeCheckResult:
        pass


class PascalTypeChecker(TypeChecker):
    """Type checker for Pascal-1981."""
    
    def __init__(self):
        self.symbol_table = SymbolTable()
        self.errors: List[TypeCheckError] = []
        self.warnings: List[TypeCheckError] = []
        self.current_function: Optional[FuncDecl] = None
        self.current_function_return_type: Optional[Type] = None
        self.current_procedure: Optional[ProcDecl] = None
        self._setup_builtins()
    
    def _setup_builtins(self) -> None:
        """Define built-in procedures and functions in the global scope."""
        # WRITELN - variable argument procedure
        writeln_type = ProcedureType('WRITELN', [])
        self.symbol_table.define('WRITELN', Symbol(
            name='WRITELN',
            type=writeln_type,
            kind='procedure',
            is_mutable=False
        ))
        
        # READLN - variable argument procedure
        readln_type = ProcedureType('READLN', [])
        self.symbol_table.define('READLN', Symbol(
            name='READLN',
            type=readln_type,
            kind='procedure',
            is_mutable=False
        ))
        
        # ABS function (argument can be INTEGER or REAL)
        abs_type = FunctionType('ABS', [('n', INTEGER_TYPE)], INTEGER_TYPE)
        self.symbol_table.define('ABS', Symbol(
            name='ABS',
            type=abs_type,
            kind='function',
            is_mutable=False
        ))
        
        # SQRT function (returns REAL)
        sqrt_type = FunctionType('SQRT', [('n', REAL_TYPE)], REAL_TYPE)
        self.symbol_table.define('SQRT', Symbol(
            name='SQRT',
            type=sqrt_type,
            kind='function',
            is_mutable=False
        ))
        
        # LENGTH function (for strings/arrays - returns INTEGER)
        length_type = FunctionType('LENGTH', [('s', CHAR_TYPE)], INTEGER_TYPE)
        self.symbol_table.define('LENGTH', Symbol(
            name='LENGTH',
            type=length_type,
            kind='function',
            is_mutable=False
        ))
    
    def check(self, ast: ASTNode) -> TypeCheckResult:
        """Main entry point for type checking."""
        self.errors = []
        self.warnings = []
        # Reset symbol table but keep builtins
        self.symbol_table = SymbolTable()
        self._setup_builtins()
        
        try:
            if isinstance(ast, ProgramUnit):
                self.check_program_unit(ast)
            elif isinstance(ast, ModuleUnit):
                self.check_module_unit(ast)
            else:
                self.error(f"Unknown root node type: {type(ast).__name__}", ast.location)
        except Exception as e:
            self.error(f"Internal error during type checking: {e}", None)
        
        return TypeCheckResult(
            success=len(self.errors) == 0,
            symbol_table=self.symbol_table,
            errors=self.errors,
            warnings=self.warnings,
            annotated_ast=ast
        )
    
    def check_program_unit(self, prog: ProgramUnit) -> None:
        """Type check a program unit."""
        # Program name is implicit
        self.check_block(prog.block)
    
    def check_module_unit(self, mod: ModuleUnit) -> None:
        """Type check a module unit."""
        # TODO: Handle module imports and exports
        self.check_block(mod.impl_block)
    
    def check_block(self, block: Block) -> None:
        """Type check a block (declarations + statements)."""
        if not block:
            return
        
        # Process declarations first
        if block.decls:
            for decl in block.decls:
                self.check_declaration(decl)
        
        # Then check statements
        if block.body:
            for stmt in block.body:
                self.check_statement(stmt)
    
    def check_declaration(self, decl) -> None:
        """Type check a declaration."""
        if isinstance(decl, VarDecl):
            self.check_var_decl(decl)
        elif isinstance(decl, ConstDecl):
            self.check_const_decl(decl)
        elif isinstance(decl, TypeDecl):
            self.check_type_decl(decl)
        elif isinstance(decl, FuncDecl):
            self.check_func_decl(decl)
        elif isinstance(decl, ProcDecl):
            self.check_proc_decl(decl)
    
    def check_var_decl(self, decl: VarDecl) -> None:
        """Type check a variable declaration."""
        if not decl.names or not decl.type_expr:
            return
        
        # Resolve the type
        var_type = self.resolve_type(decl.type_expr)
        if not var_type:
            self.error(f"Unknown type: {decl.type_expr}", decl)
            return
        
        # Add each variable to the symbol table
        for name in decl.names:
            # Check for redeclaration
            existing = self.symbol_table.lookup_local(name)
            if existing:
                self.error(
                    f"Variable '{name}' already declared at {existing.location}",
                    decl
                )
                continue
            
            # Create symbol
            symbol = Symbol(
                name=name,
                type=var_type,
                kind='var',
                location=self.get_node_location(decl),
                is_mutable=True
            )
            self.symbol_table.define(name, symbol)
    
    def check_const_decl(self, decl: ConstDecl) -> None:
        """Type check a constant declaration."""
        if not decl.names or not decl.value:
            return
        
        # Evaluate the constant value and infer type
        # For now, just check it's valid
        if not decl.value:
            return
        
        value_type = self.infer_expression_type(decl.value)
        if not value_type:
            self.error(f"Cannot infer type of constant", decl)
            return
        
        # Add each constant to the symbol table
        for name in decl.names:
            existing = self.symbol_table.lookup_local(name)
            if existing:
                self.error(
                    f"Constant '{name}' already declared at {existing.location}",
                    decl
                )
                continue
            
            symbol = Symbol(
                name=name,
                type=value_type,
                kind='const',
                location=self.make_location(decl),
                is_mutable=False
            )
            self.symbol_table.define(name, symbol)
    
    def check_type_decl(self, decl: TypeDecl) -> None:
        """Type check a type declaration."""
        # TODO: Handle type aliases
        pass
    
    def check_func_decl(self, decl: FuncDecl) -> None:
        """Type check a function declaration."""
        if not decl.name:
            return
        
        # Resolve parameter types
        param_types = []
        if decl.params:
            for param in decl.params:
                if param.type_expr:
                    param_type = self.resolve_type(param.type_expr)
                    if param_type:
                        for name in param.names:
                            param_types.append((name, param_type))
        
        # Resolve return type
        return_type = INTEGER_TYPE
        if decl.return_type:
            return_type = self.resolve_type(decl.return_type)
            if not return_type:
                self.error(f"Unknown return type", decl)
                return_type = INTEGER_TYPE
        
        # Create function type
        func_type = FunctionType(decl.name, param_types, return_type)
        
        # Check for redeclaration
        existing = self.symbol_table.lookup_local(decl.name)
        if existing:
            self.error(
                f"Function '{decl.name}' already declared at {existing.location}",
                decl
            )
            return
        
        # Add to symbol table
        symbol = Symbol(
            name=decl.name,
            type=func_type,
            kind='function',
            location=self.make_location(decl)
        )
        self.symbol_table.define(decl.name, symbol)
        
        # Check function body
        old_func = self.current_function
        old_return_type = self.current_function_return_type
        self.current_function = decl
        self.current_function_return_type = return_type
        self.symbol_table.enter_scope()
        
        # Add parameters to scope
        if decl.params:
            for param in decl.params:
                param_type = self.resolve_type(param.type_expr)
                if param_type:
                    for name in param.names:
                        param_symbol = Symbol(
                            name=name,
                            type=param_type,
                            kind='parameter',
                            location=self.make_location(param),
                            is_mutable=False
                        )
                        self.symbol_table.define(name, param_symbol)
        
        # Check body
        self.check_block(decl.body)
        
        self.symbol_table.exit_scope()
        self.current_function = old_func
        self.current_function_return_type = old_return_type
    
    def check_proc_decl(self, decl: ProcDecl) -> None:
        """Type check a procedure declaration."""
        if not decl.name:
            return
        
        # Resolve parameter types
        param_types = []
        if decl.params:
            for param in decl.params:
                if param.type_expr:
                    param_type = self.resolve_type(param.type_expr)
                    if param_type:
                        for name in param.names:
                            param_types.append((name, param_type))
        
        # Create procedure type
        proc_type = ProcedureType(decl.name, param_types)
        
        # Check for redeclaration
        existing = self.symbol_table.lookup_local(decl.name)
        if existing:
            self.error(
                f"Procedure '{decl.name}' already declared at {existing.location}",
                decl
            )
            return
        
        # Add to symbol table
        symbol = Symbol(
            name=decl.name,
            type=proc_type,
            kind='procedure',
            location=self.make_location(decl)
        )
        self.symbol_table.define(decl.name, symbol)
        
        # Check procedure body
        old_proc = self.current_procedure
        self.current_procedure = decl
        self.symbol_table.enter_scope()
        
        # Add parameters to scope
        if decl.params:
            for param in decl.params:
                param_type = self.resolve_type(param.type_expr)
                if param_type:
                    for name in param.names:
                        param_symbol = Symbol(
                            name=name,
                            type=param_type,
                            kind='parameter',
                            location=self.make_location(param),
                            is_mutable=False
                        )
                        self.symbol_table.define(name, param_symbol)
        
        # Check body
        self.check_block(decl.body)
        
        self.symbol_table.exit_scope()
        self.current_procedure = old_proc
    
    def check_statement(self, stmt: Statement) -> None:
        """Type check a statement."""
        if isinstance(stmt, IfStmt):
            self.check_if_stmt(stmt)
        elif isinstance(stmt, ForStmt):
            self.check_for_stmt(stmt)
        elif isinstance(stmt, WhileStmt):
            self.check_while_stmt(stmt)
        elif isinstance(stmt, RepeatStmt):
            self.check_repeat_stmt(stmt)
        elif isinstance(stmt, CaseStmt):
            self.check_case_stmt(stmt)
        elif isinstance(stmt, AssignStmt):
            self.check_assign_stmt(stmt)
        elif isinstance(stmt, ProcCallStmt):
            self.check_proc_call_stmt(stmt)
        elif isinstance(stmt, ReturnStmt):
            self.check_return_stmt(stmt)
    
    def check_if_stmt(self, stmt: IfStmt) -> None:
        """Type check an IF statement."""
        # Condition must be BOOLEAN
        cond_type = self.infer_expression_type(stmt.cond)
        if cond_type and not cond_type.equivalent_to(BOOLEAN_TYPE):
            self.error(
                f"IF condition must be BOOLEAN, got {cond_type}",
                stmt
            )
        
        # Check branches
        if stmt.then_branch:
            self.check_statement(stmt.then_branch)
        if stmt.else_branch:
            self.check_statement(stmt.else_branch)
    
    def check_for_stmt(self, stmt: ForStmt) -> None:
        """Type check a FOR statement."""
        # Loop variable must be INTEGER
        if stmt.var:
            sym = self.symbol_table.lookup(stmt.var)
            if not sym:
                self.error(f"Undefined variable: {stmt.var}", stmt)
            elif not sym.type.equivalent_to(INTEGER_TYPE):
                self.error(
                    f"FOR loop variable must be INTEGER, got {sym.type}",
                    stmt
                )
        
        # Loop bounds must be INTEGER
        if stmt.start:
            start_type = self.infer_expression_type(stmt.start)
            if start_type and not start_type.equivalent_to(INTEGER_TYPE):
                self.error(
                    f"FOR start bound must be INTEGER, got {start_type}",
                    stmt
                )
        
        if stmt.end:
            end_type = self.infer_expression_type(stmt.end)
            if end_type and not end_type.equivalent_to(INTEGER_TYPE):
                self.error(
                    f"FOR end bound must be INTEGER, got {end_type}",
                    stmt
                )
        
        # Check loop body
        if stmt.body:
            self.check_statement(stmt.body)
    
    def check_while_stmt(self, stmt: WhileStmt) -> None:
        """Type check a WHILE statement."""
        # Condition must be BOOLEAN
        cond_type = self.infer_expression_type(stmt.cond)
        if cond_type and not cond_type.equivalent_to(BOOLEAN_TYPE):
            self.error(
                f"WHILE condition must be BOOLEAN, got {cond_type}",
                stmt
            )
        
        # Check body
        if stmt.body:
            self.check_statement(stmt.body)
    
    def check_repeat_stmt(self, stmt: RepeatStmt) -> None:
        """Type check a REPEAT statement."""
        # Condition must be BOOLEAN
        cond_type = self.infer_expression_type(stmt.cond)
        if cond_type and not cond_type.equivalent_to(BOOLEAN_TYPE):
            self.error(
                f"REPEAT..UNTIL condition must be BOOLEAN, got {cond_type}",
                stmt
            )
        
        # Check body
        if stmt.body:
            for s in stmt.body:
                self.check_statement(s)
    
    def check_case_stmt(self, stmt: CaseStmt) -> None:
        """Type check a CASE statement."""
        # TODO: Check selector type and case value types
        pass
    
    def check_return_stmt(self, stmt: ReturnStmt) -> None:
        """Type check a RETURN statement."""
        # RETURN is only valid inside a function
        if not self.current_function:
            self.error(
                "RETURN statement outside of function",
                stmt
            )
            return
        
        # If function has return type, RETURN value must match
        if self.current_function_return_type and hasattr(stmt, 'value') and stmt.value:
            value_type = self.infer_expression_type(stmt.value)
            if value_type and not can_assign(value_type, self.current_function_return_type):
                self.error(
                    f"RETURN type mismatch: expected {self.current_function_return_type}, got {value_type}",
                    stmt
                )
    
    def check_assign_stmt(self, stmt: AssignStmt) -> None:
        """Type check an assignment statement."""
        if not stmt.target or not stmt.expr:
            return
        
        # Get the target variable name and type
        target_name = None
        target_type = None
        
        if isinstance(stmt.target, Identifier):
            target_name = stmt.target.name
            # Special case: assigning to function name inside function body (sets return value)
            if self.current_function and target_name == self.current_function.name:
                value_type = self.infer_expression_type(stmt.expr)
                if value_type and self.current_function_return_type:
                    if not can_assign(value_type, self.current_function_return_type):
                        self.error(
                            f"Cannot assign {value_type} to function return type {self.current_function_return_type}",
                            stmt
                        )
                return
            # Regular variable assignment
            sym = self.symbol_table.lookup(target_name)
            if sym:
                target_type = sym.type
        elif isinstance(stmt.target, Designator):
            # Designator with selectors (array/record/pointer access)
            target_type = self.infer_designator_type(stmt.target)
            if target_type:
                # Type check successful - target_type is now the element/field type
                value_type = self.infer_expression_type(stmt.expr)
                if value_type and not can_assign(value_type, target_type):
                    self.error(
                        f"Cannot assign {value_type} to {target_type}",
                        stmt
                    )
                return
            else:
                # Error already reported by infer_designator_type
                return
        else:
            return
        
        if not target_name:
            return
        
        # Look up the variable (already done above for Identifier case in special function case)
        if not target_type:
            sym = self.symbol_table.lookup(target_name)
            if not sym:
                self.error(f"Undefined variable: {target_name}", stmt)
                return
            target_type = sym.type
        else:
            sym = None
        
        # Check mutability (only for variables, not designators)
        if sym and not sym.is_mutable:
            self.error(
                f"Cannot assign to immutable {sym.kind}: {target_name}",
                stmt
            )
        
        # Check type compatibility
        value_type = self.infer_expression_type(stmt.expr)
        if value_type:
            if not can_assign(value_type, target_type):
                self.error(
                    f"Cannot assign {value_type} to {target_type}",
                    stmt
                )
    
    def check_proc_call_stmt(self, stmt: ProcCallStmt) -> None:
        """Type check a procedure call statement."""
        if not stmt.name:
            return
        
        # Look up the procedure
        sym = self.symbol_table.lookup(stmt.name)
        if not sym:
            self.error(f"Undefined procedure: {stmt.name}", stmt)
            return
        
        if not isinstance(sym.type, ProcedureType):
            self.error(
                f"'{stmt.name}' is not a procedure",
                stmt
            )
            return
        
        # Check argument types (including built-in procedures)
        if stmt.args:
            # For built-in procedures like WRITELN/READLN, skip count/type checks but still
            # check that the arguments are valid expressions (e.g., defined variables)
            if stmt.name.upper() not in ['WRITELN', 'READLN']:
                # For user-defined procedures, check argument count
                expected_args = len(sym.type.params)
                actual_args = len(stmt.args)
                if actual_args != expected_args:
                    self.error(
                        f"Procedure '{stmt.name}' expects {expected_args} arguments, got {actual_args}",
                        stmt
                    )
                    return
            
            # Check that all arguments are well-formed (this will catch undefined variables)
            for i, arg in enumerate(stmt.args):
                arg_type = self.infer_expression_type(arg)
                # If it's a user-defined procedure, also check type compatibility
                if stmt.name.upper() not in ['WRITELN', 'READLN'] and arg_type:
                    if i < len(sym.type.params):
                        _, param_type = sym.type.params[i]
                        if not can_assign(arg_type, param_type):
                            self.error(
                                f"Argument {i+1} type mismatch: expected {param_type}, got {arg_type}",
                                stmt
                            )
            return
    
    def infer_expression_type(self, expr: Expression) -> Optional[Type]:
        """Infer the type of an expression."""
        if isinstance(expr, IntLiteral):
            return INTEGER_TYPE
        elif isinstance(expr, RealLiteral):
            return REAL_TYPE
        elif isinstance(expr, BoolLiteral):
            return BOOLEAN_TYPE
        elif isinstance(expr, Identifier):
            sym = self.symbol_table.lookup(expr.name)
            if not sym:
                self.error(f"Undefined variable: {expr.name}", expr)
                return None
            return sym.type
        elif isinstance(expr, BinOp):
            left_type = self.infer_expression_type(expr.left)
            right_type = self.infer_expression_type(expr.right)
            if left_type and right_type:
                return binary_op_result_type(left_type, expr.op, right_type)
            return None
        elif isinstance(expr, UnaryOp):
            operand_type = self.infer_expression_type(expr.operand)
            if operand_type:
                return unary_op_result_type(operand_type, expr.op)
            return None
        elif isinstance(expr, FuncCall):
            sym = self.symbol_table.lookup(expr.name)
            if not sym:
                self.error(f"Undefined function: {expr.name}", expr)
                return None
            if isinstance(sym.type, FunctionType):
                # Check argument count
                expected_args = len(sym.type.params)
                actual_args = len(expr.args) if expr.args else 0
                if actual_args != expected_args:
                    self.error(
                        f"Function '{expr.name}' expects {expected_args} arguments, got {actual_args}",
                        expr
                    )
                # Check argument types
                if expr.args:
                    for i, (arg, (param_name, param_type)) in enumerate(zip(expr.args, sym.type.params)):
                        arg_type = self.infer_expression_type(arg)
                        if arg_type and not can_assign(arg_type, param_type):
                            self.error(
                                f"Argument {i+1} type mismatch: expected {param_type}, got {arg_type}",
                                expr
                            )
                return sym.type.return_type
            return None
        elif isinstance(expr, Designator):
            return self.infer_designator_type(expr)
        else:
            # Unknown expression type
            return None
    
    def infer_designator_type(self, designator: Designator) -> Optional[Type]:
        """Infer the type of a designator (with selectors for array/record access)."""
        # Special case: inside a function, referencing the function name gets the return type
        if self.current_function and designator.name == self.current_function.name:
            current_type = self.current_function_return_type
            if not current_type:
                return None
        else:
            # Look up the base name
            sym = self.symbol_table.lookup(designator.name)
            if not sym:
                self.error(f"Undefined variable: {designator.name}", designator)
                return None
            current_type = sym.type
        
        # Process selectors (array indexing, field access, pointer dereference)
        if designator.selectors:
            for selector in designator.selectors:
                if selector.kind == 'INDEX':
                    # Array indexing
                    if not isinstance(current_type, ArrayType):
                        self.error(
                            f"Cannot index non-array type {current_type}",
                            designator
                        )
                        return None
                    # Check that index is INTEGER
                    if selector.index_or_field:
                        index_type = self.infer_expression_type(selector.index_or_field)
                        if index_type and not index_type.equivalent_to(INTEGER_TYPE):
                            self.error(
                                f"Array index must be INTEGER, got {index_type}",
                                designator
                            )
                    current_type = current_type.element_type
                
                elif selector.kind == 'FIELD':
                    # Record field access
                    if not isinstance(current_type, RecordType):
                        self.error(
                            f"Cannot access field on non-record type {current_type}",
                            designator
                        )
                        return None
                    field_name = selector.index_or_field
                    field_type = current_type.get_field_type(field_name)
                    if not field_type:
                        self.error(
                            f"Record has no field '{field_name}'",
                            designator
                        )
                        return None
                    current_type = field_type
                
                elif selector.kind == 'DEREF':
                    # Pointer dereference
                    if not isinstance(current_type, PointerType):
                        self.error(
                            f"Cannot dereference non-pointer type {current_type}",
                            designator
                        )
                        return None
                    current_type = current_type.target_type
        
        return current_type
    
    def resolve_type(self, type_expr) -> Optional[Type]:
        """Resolve a type expression to a Type object."""
        if isinstance(type_expr, NamedType):
            name = type_expr.name.upper()
            if name == 'INTEGER':
                return INTEGER_TYPE
            elif name == 'BOOLEAN':
                return BOOLEAN_TYPE
            elif name == 'REAL':
                return REAL_TYPE
            elif name == 'WORD':
                return WORD_TYPE
            elif name == 'CHAR':
                return CHAR_TYPE
            else:
                # Could be a user-defined type
                return None
        elif isinstance(type_expr, ASTArrayType):
            # Resolve the element type
            if isinstance(type_expr.element_type, Type):
                # Already a Type object (from AST)
                element_type = type_expr.element_type
            else:
                # Resolve as type expression
                element_type = self.resolve_type(type_expr.element_type)
            
            if element_type and type_expr.index_range:
                # Extract bounds - these are expressions, evaluate them as constants
                try:
                    if isinstance(type_expr.index_range.low, IntLiteral):
                        lower = type_expr.index_range.low.value
                    else:
                        lower = 1  # Default if not a constant
                    
                    if type_expr.index_range.high and isinstance(type_expr.index_range.high, IntLiteral):
                        upper = type_expr.index_range.high.value
                    else:
                        upper = 10  # Default if not a constant
                    
                    return ArrayType(element_type, lower, upper)
                except:
                    return None
            return None
        elif isinstance(type_expr, ASTRecordType):
            fields = {}
            if type_expr.fields:
                for field_name, field_type_expr in type_expr.fields.items():
                    field_type = self.resolve_type(field_type_expr)
                    if field_type:
                        fields[field_name] = field_type
            return RecordType(type_expr.name, fields)
        else:
            return None
    
    def make_location(self, location) -> Optional[SourceLocation]:
        """Convert AST location tuple to SourceLocation."""
        # Handle None and missing location attributes gracefully
        if location is None:
            return None
        if isinstance(location, tuple) and len(location) >= 3:
            return SourceLocation(location[0], location[1], location[2])
        return None
    
    def get_node_location(self, node: Optional[ASTNode]) -> Optional[SourceLocation]:
        """Get location from a node, handling missing attributes gracefully."""
        if not node:
            return None
        if hasattr(node, 'location'):
            return self.make_location(node.location)
        return None
    
    def error(self, message: str, location=None) -> None:
        """Record a type checking error."""
        # Handle node objects or tuple locations
        loc = None
        if location is not None:
            if isinstance(location, ASTNode):
                loc = self.get_node_location(location)
            else:
                loc = self.make_location(location)
        self.errors.append(TypeCheckError(
            message=message,
            location=loc,
            severity='error'
        ))
    
    def warning(self, message: str, location=None) -> None:
        """Record a type checking warning."""
        # Handle node objects or tuple locations
        loc = None
        if location is not None:
            if isinstance(location, ASTNode):
                loc = self.get_node_location(location)
            else:
                loc = self.make_location(location)
        self.warnings.append(TypeCheckError(
            message=message,
            location=loc,
            severity='warning'
        ))
