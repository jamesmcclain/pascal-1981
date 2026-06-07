"""
Static Type Checker for Pascal-1981 Compiler

Performs semantic analysis on the AST:
- Builds symbol tables
- Resolves identifier references
- Checks type compatibility
- Validates control flow types
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from parser import parse_file
from pathlib import Path
from typing import Any, Dict, List, Optional

from ast_nodes import AdrExpr, AdsExpr
from ast_nodes import ArrayType as ASTArrayType
from ast_nodes import (AssignStmt, ASTNode, BinOp, Block, BoolLiteral, CaseStmt, CharLiteral, ConstDecl, Designator, Expression, ForStmt, FuncCall, FuncDecl, Identifier, IfStmt,
                       ImplementationUnit, InterfaceUnit, IntLiteral, LabelStmt, LowerExpr, ModuleUnit, NamedType, NilLiteral, PointerType as ASTPointerType, ProcCallStmt, ProcDecl, ProgramUnit, RealLiteral, UpperExpr, WriteArg)
from ast_nodes import LStringType as ASTLStringType, RecordType as ASTRecordType, SetType as ASTSetType, SubrangeType as ASTSubrangeType, EnumType as ASTEnumType
from ast_nodes import (RangeExpr, RepeatStmt, ReturnStmt, Selector, SetConstructor, SizeofExpr, Statement, StringLiteral, TypeDecl, UnaryOp, UseClause, VarDecl, WhileStmt)
from symbol_table import SourceLocation, Symbol, SymbolTable
from type_system import (BOOLEAN_TYPE, CHAR_TYPE, INTEGER_TYPE, REAL_TYPE, WORD_TYPE, ArrayType, FileType, FunctionType, LStringType, PointerType, ProcedureType, RecordType, SetType, StringType, Type,
                         EnumType, binary_op_result_type, can_assign, unary_op_result_type)


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

    def __init__(self, source_file: Optional[str] = None):
        self.symbol_table = SymbolTable()
        self.errors: List[TypeCheckError] = []
        self.warnings: List[TypeCheckError] = []
        self.current_function: Optional[FuncDecl] = None
        self.current_function_return_type: Optional[Type] = None
        self.current_procedure: Optional[ProcDecl] = None
        self.current_interface_decls: Dict[str, Any] = {}
        self.source_file = source_file  # Path to the source file being compiled
        self._setup_builtins()

    def _setup_builtins(self) -> None:
        """Define built-in procedures and functions in the global scope."""
        # WRITELN - variable argument procedure
        writeln_type = ProcedureType('WRITELN', [])
        self.symbol_table.define('WRITELN', Symbol(name='WRITELN', type=writeln_type, kind='procedure', is_mutable=False))

        # WRITE - variable argument procedure (no newline)
        write_type = ProcedureType('WRITE', [])
        self.symbol_table.define('WRITE', Symbol(name='WRITE', type=write_type, kind='procedure', is_mutable=False))

        # READLN - variable argument procedure
        readln_type = ProcedureType('READLN', [])
        self.symbol_table.define('READLN', Symbol(name='READLN', type=readln_type, kind='procedure', is_mutable=False))

        # String manipulation procedures (section 7.2 of manual)
        concat_type = ProcedureType('CONCAT', [])
        self.symbol_table.define('CONCAT', Symbol(name='CONCAT', type=concat_type, kind='procedure', is_mutable=False))

        copylst_type = ProcedureType('COPYLST', [])
        self.symbol_table.define('COPYLST', Symbol(name='COPYLST', type=copylst_type, kind='procedure', is_mutable=False))

        copystr_type = ProcedureType('COPYSTR', [])
        self.symbol_table.define('COPYSTR', Symbol(name='COPYSTR', type=copystr_type, kind='procedure', is_mutable=False))

        # Predeclared constants.
        self.symbol_table.define('MAXINT', Symbol(name='MAXINT', type=INTEGER_TYPE, kind='const', is_mutable=False))
        self.symbol_table.define('MAXWORD', Symbol(name='MAXWORD', type=WORD_TYPE, kind='const', is_mutable=False))
        self.symbol_table.define('NULL', Symbol(name='NULL', type=LStringType(0), kind='const', is_mutable=False))

        # Predeclared file/type names.
        text_type = FileType(CHAR_TYPE)
        self.symbol_table.define('TEXT', Symbol(name='TEXT', type=text_type, kind='type', is_mutable=False))
        self.symbol_table.define('INPUT', Symbol(name='INPUT', type=text_type, kind='var', is_mutable=False))
        self.symbol_table.define('OUTPUT', Symbol(name='OUTPUT', type=text_type, kind='var', is_mutable=False))
        self.symbol_table.define('STRING', Symbol(name='STRING', type=StringType(256), kind='type', is_mutable=False))

        # ABS and SQRT are handled as special built-ins in type inference/codegen.
        # LENGTH is not part of the manual's predeclared list; keep it out until
        # a dialect decision puts it back in.

        # CHR function (returns CHAR)
        chr_type = FunctionType('CHR', [('n', INTEGER_TYPE)], CHAR_TYPE)
        self.symbol_table.define('CHR', Symbol(name='CHR', type=chr_type, kind='function', is_mutable=False))

        # ORD function (returns INTEGER)
        ord_type = FunctionType('ORD', [('c', CHAR_TYPE)], INTEGER_TYPE)
        self.symbol_table.define('ORD', Symbol(name='ORD', type=ord_type, kind='function', is_mutable=False))

        # ODD function (returns BOOLEAN)
        odd_type = FunctionType('ODD', [('n', INTEGER_TYPE)], BOOLEAN_TYPE)
        self.symbol_table.define('ODD', Symbol(name='ODD', type=odd_type, kind='function', is_mutable=False))

        # SUCC / PRED functions (returns INTEGER)
        succ_type = FunctionType('SUCC', [('n', INTEGER_TYPE)], INTEGER_TYPE)
        self.symbol_table.define('SUCC', Symbol(name='SUCC', type=succ_type, kind='function', is_mutable=False))
        pred_type = FunctionType('PRED', [('n', INTEGER_TYPE)], INTEGER_TYPE)
        self.symbol_table.define('PRED', Symbol(name='PRED', type=pred_type, kind='function', is_mutable=False))

        # HIBYTE / LOBYTE functions (return CHAR)
        hibyte_type = FunctionType('HIBYTE', [('n', INTEGER_TYPE)], CHAR_TYPE)
        self.symbol_table.define('HIBYTE', Symbol(name='HIBYTE', type=hibyte_type, kind='function', is_mutable=False))
        lobyte_type = FunctionType('LOBYTE', [('n', INTEGER_TYPE)], CHAR_TYPE)
        self.symbol_table.define('LOBYTE', Symbol(name='LOBYTE', type=lobyte_type, kind='function', is_mutable=False))

        # TRUNC / ROUND functions (REAL -> INTEGER)
        trunc_type = FunctionType('TRUNC', [('x', REAL_TYPE)], INTEGER_TYPE)
        self.symbol_table.define('TRUNC', Symbol(name='TRUNC', type=trunc_type, kind='function', is_mutable=False))
        round_type = FunctionType('ROUND', [('x', REAL_TYPE)], INTEGER_TYPE)
        self.symbol_table.define('ROUND', Symbol(name='ROUND', type=round_type, kind='function', is_mutable=False))

        # FLOAT function (INTEGER -> REAL)
        float_type = FunctionType('FLOAT', [('x', INTEGER_TYPE)], REAL_TYPE)
        self.symbol_table.define('FLOAT', Symbol(name='FLOAT', type=float_type, kind='function', is_mutable=False))

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
            elif isinstance(ast, InterfaceUnit):
                self.check_interface_unit(ast)
            elif isinstance(ast, ImplementationUnit):
                self.check_implementation_unit(ast)
            elif isinstance(ast, ModuleUnit):
                self.check_module_unit(ast)
            else:
                self.error(f"Unknown root node type: {type(ast).__name__}", ast.location)
        except Exception as e:
            self.error(f"Internal error during type checking: {e}", None)

        return TypeCheckResult(success=len(self.errors) == 0, symbol_table=self.symbol_table, errors=self.errors, warnings=self.warnings, annotated_ast=ast)

    def resolve_module_path(self, module_name: str, search_dir: Optional[str]) -> Optional[str]:
        """Resolve a module name to a source filename.

        We first try the literal basename used by the source text, then the same
        basename with common Pascal-era source suffixes (.inc / .pas) and simple
        case variants.
        """
        if search_dir is None:
            search_dir = '.'

        search_path = Path(search_dir)
        stems = [module_name, module_name.lower(), module_name.upper()]
        suffixes = ['', '.inc', '.pas']

        for stem in stems:
            for suffix in suffixes:
                candidate = search_path / f"{stem}{suffix}"
                if candidate.exists():
                    return str(candidate.resolve())

        return None

    def load_interface(self, path: str) -> Optional[Any]:
        """Load a module source file for symbol import.

        Historically, the source that feeds USES may be an INTERFACE unit or a
        module/implementation file that carries the exported declarations.
        """
        try:
            ast = parse_file(path)
            if not isinstance(ast, (InterfaceUnit, ImplementationUnit, ModuleUnit)):
                self.error(f"Expected module/interface file, got {type(ast).__name__} in {path}", None)
                return None
            return ast
        except Exception as e:
            self.error(f"Failed to load interface from {path}: {e}", None)
            return None

    def import_symbols(self, interface: Any, uses: UseClause) -> None:
        """Import symbols from a loaded module/interface into the current scope."""
        if isinstance(interface, InterfaceUnit):
            export_names = list(interface.params)
            export_decls = list(interface.decls)
            if len(export_names) != len(export_decls):
                self.error(
                    f"Interface '{interface.name}' export list does not match its declarations",
                    None,
                )
                return
            if uses.imports:
                imported_aliases = list(uses.imports)
                if len(imported_aliases) > len(export_names):
                    self.error(
                        f"Module {uses.name} imports {len(imported_aliases)} name(s) but only exports {len(export_names)}",
                        None,
                    )
                    return
                pairs = list(zip(imported_aliases, export_names[:len(imported_aliases)], export_decls[:len(imported_aliases)]))
            else:
                pairs = list(zip(export_names, export_names, export_decls))
        else:
            export_decls = [decl for decl in getattr(interface, 'decls', []) if getattr(decl, 'name', None)]
            export_names = [decl.name for decl in export_decls]
            if uses.imports:
                imported_aliases = list(uses.imports)
                if len(imported_aliases) > len(export_names):
                    self.error(
                        f"Module {uses.name} imports {len(imported_aliases)} name(s) but only exports {len(export_names)}",
                        None,
                    )
                    return
                wanted = []
                for alias in imported_aliases:
                    try:
                        idx = [name.lower() for name in export_names].index(alias.lower())
                    except ValueError:
                        self.error(f"Module {uses.name} does not export '{alias}'", None)
                        continue
                    wanted.append((alias, export_names[idx], export_decls[idx]))
                pairs = wanted
            else:
                pairs = list(zip(export_names, export_names, export_decls))

        for local_name, exported_name, decl in pairs:
            symbol = Symbol(
                name=local_name,
                type=self._get_declaration_type(decl),
                kind=self._get_declaration_kind(decl),
                is_mutable=isinstance(decl, VarDecl),
            )
            if self.symbol_table.lookup_local(local_name):
                self.error(f"Symbol '{local_name}' from module {uses.name} conflicts with existing definition", None)
                continue
            self.symbol_table.define(local_name, symbol)

    def _get_declaration_kind(self, decl: Any) -> str:
        """Get the kind of a declaration (procedure, function, const, type, var)."""
        if isinstance(decl, ProcDecl):
            return 'procedure'
        elif isinstance(decl, FuncDecl):
            return 'function'
        elif isinstance(decl, ConstDecl):
            return 'const'
        elif isinstance(decl, TypeDecl):
            return 'type'
        elif isinstance(decl, VarDecl):
            return 'var'
        else:
            return 'unknown'

    def _get_declaration_type(self, decl: Any) -> Type:
        """Get the Type object for a declaration."""
        if isinstance(decl, FuncDecl):
            # For functions, use the resolved return type
            return_type = self.resolve_type(decl.return_type) if decl.return_type else INTEGER_TYPE
            return return_type if return_type else INTEGER_TYPE
        elif isinstance(decl, ProcDecl):
            # For procedures, create a ProcedureType with resolved parameter types
            param_list = []
            for p in decl.params:
                param_type = self.resolve_type(p.type_expr) if hasattr(p, 'type_expr') else INTEGER_TYPE
                if not param_type:
                    param_type = INTEGER_TYPE
                for name in getattr(p, 'names', []):
                    param_list.append((name, param_type))
            return ProcedureType(decl.name, param_list)
        elif isinstance(decl, ConstDecl):
            # For constants, try to infer type from value
            if isinstance(decl.value, IntLiteral):
                return INTEGER_TYPE
            elif isinstance(decl.value, RealLiteral):
                return REAL_TYPE
            elif isinstance(decl.value, StringLiteral):
                return CHAR_TYPE
            elif isinstance(decl.value, BoolLiteral):
                return BOOLEAN_TYPE
            else:
                return INTEGER_TYPE
        elif isinstance(decl, TypeDecl):
            # For type declarations, use the type itself
            return decl.type if hasattr(decl, 'type') else INTEGER_TYPE
        elif isinstance(decl, VarDecl):
            # For variables, use their declared type
            return decl.type if hasattr(decl, 'type') else INTEGER_TYPE
        else:
            return INTEGER_TYPE

    def validate_implementation_against_interface(self, impl: ImplementationUnit, iface: InterfaceUnit) -> None:
        """Validate that implementation matches its interface.

        The implementation may omit parameter lists in routine bodies and inherit
        the interface signature, but the underlying routine kinds, parameter
        counts, and return types must still agree.
        """
        impl_decls = {getattr(decl, 'name', '').lower(): decl for decl in impl.decls if getattr(decl, 'name', None)}

        for export_name in iface.params:
            iface_decl = next((decl for decl in iface.decls if getattr(decl, 'name', '').lower() == export_name.lower()), None)
            if not iface_decl:
                continue

            impl_decl = impl_decls.get(export_name.lower())
            if not impl_decl:
                kind = 'procedure' if isinstance(iface_decl, ProcDecl) else 'function'
                self.error(f"Missing implementation for exported {kind} '{export_name}'", None)
                continue

            if not self.match_signatures(iface_decl, impl_decl):
                self.error(self._signature_mismatch_message(iface_decl, impl_decl), None)

    def match_signatures(self, iface_decl: Any, impl_decl: Any) -> bool:
        """Check if implementation signature matches interface declaration."""
        if type(iface_decl) != type(impl_decl):
            return False

        if isinstance(iface_decl, FuncDecl):
            if not self._types_equal(iface_decl.return_type, impl_decl.return_type):
                return False

        iface_params = iface_decl.params if hasattr(iface_decl, 'params') else []
        impl_params = impl_decl.params if hasattr(impl_decl, 'params') else []
        if impl_params and len(iface_params) != len(impl_params):
            return False
        if not impl_params:
            impl_params = iface_params

        for iface_param, impl_param in zip(iface_params, impl_params):
            iface_type = getattr(iface_param, 'type_expr', None)
            impl_type = getattr(impl_param, 'type_expr', None)
            if not self._types_equal(iface_type, impl_type):
                return False
            if getattr(iface_param, 'mode', None) != getattr(impl_param, 'mode', None):
                return False

        return True

    def _types_equal(self, type1: Any, type2: Any) -> bool:
        """Check if two types are equal."""
        if type1 is None and type2 is None:
            return True
        if type1 is None or type2 is None:
            return False

        # NamedType comparison
        if isinstance(type1, NamedType) and isinstance(type2, NamedType):
            return type1.name.lower() == type2.name.lower()

        # Direct type comparison (INTEGER_TYPE, etc.)
        if isinstance(type1, type(type2)):
            if hasattr(type1, 'name') and hasattr(type2, 'name'):
                return type1.name.lower() == type2.name.lower()
            return type1 == type2

        return False

    def _signature_mismatch_message(self, iface_decl: Any, impl_decl: Any) -> str:
        """Generate a detailed error message for signature mismatch."""
        name = getattr(iface_decl, 'name', 'Unknown')
        kind = 'FUNCTION' if isinstance(iface_decl, FuncDecl) else 'PROCEDURE'

        iface_params = iface_decl.params if hasattr(iface_decl, 'params') else []
        impl_params = impl_decl.params if hasattr(impl_decl, 'params') else []

        # Check what kind of mismatch
        if len(iface_params) != len(impl_params):
            return (f"{kind} '{name}' signature mismatch: "
                    f"expected {len(iface_params)} parameter(s), got {len(impl_params)}")

        # Check parameter details
        for i, (iface_param, impl_param) in enumerate(zip(iface_params, impl_params)):
            iface_type = getattr(iface_param, 'type_expr', None)
            impl_type = getattr(impl_param, 'type_expr', None)
            if not self._types_equal(iface_type, impl_type):
                iface_type_str = self._type_to_string(iface_type)
                impl_type_str = self._type_to_string(impl_type)
                # Get parameter name from names list
                iface_names = getattr(iface_param, 'names', [])
                param_name = iface_names[0] if iface_names else f'param{i}'
                return (f"{kind} '{name}' parameter '{param_name}' type mismatch: "
                        f"expected {iface_type_str}, got {impl_type_str}")

            # Check mode (VAR/CONST) mismatch
            iface_mode = getattr(iface_param, 'mode', None)
            impl_mode = getattr(impl_param, 'mode', None)
            if iface_mode != impl_mode:
                iface_names = getattr(iface_param, 'names', [])
                param_name = iface_names[0] if iface_names else f'param{i}'
                iface_mode_str = iface_mode if iface_mode else 'value'
                impl_mode_str = impl_mode if impl_mode else 'value'
                return (f"{kind} '{name}' parameter '{param_name}' mode mismatch: "
                        f"expected {iface_mode_str}, got {impl_mode_str}")

        # Check return type for functions
        if isinstance(iface_decl, FuncDecl):
            iface_ret = iface_decl.return_type
            impl_ret = impl_decl.return_type
            if not self._types_equal(iface_ret, impl_ret):
                iface_ret_str = self._type_to_string(iface_ret)
                impl_ret_str = self._type_to_string(impl_ret)
                return (f"FUNCTION '{name}' return type mismatch: "
                        f"expected {iface_ret_str}, got {impl_ret_str}")

        # Fallback
        return f"{kind} '{name}' signature mismatch"

    def _type_to_string(self, typ: Any) -> str:
        """Convert a type to a string representation."""
        if typ is None:
            return "(unknown)"
        if isinstance(typ, NamedType):
            return typ.name
        if hasattr(typ, 'name'):
            return typ.name
        return str(typ)

    def check_program_unit(self, prog: ProgramUnit) -> None:
        """Type check a program unit."""
        # Process USES clauses first
        if prog.uses:
            # Get directory of source file for module resolution
            source_dir = str(Path(self.source_file).parent) if self.source_file else None
            for use_clause in prog.uses:
                # Resolve module path
                module_path = self.resolve_module_path(use_clause.name, source_dir)
                if module_path is None:
                    self.error(f"Module '{use_clause.name}' not found", None)
                    continue

                # Load interface
                interface = self.load_interface(module_path)
                if interface is None:
                    continue

                # Import symbols
                self.import_symbols(interface, use_clause)

        # Now type-check the program block
        self.check_block(prog.block)

    def check_module_unit(self, mod: ModuleUnit) -> None:
        """Type check a module unit."""
        # Process USES clauses first
        if mod.uses:
            # Get directory of source file for module resolution
            source_dir = str(Path(self.source_file).parent) if self.source_file else None
            for use_clause in mod.uses:
                # Resolve module path
                module_path = self.resolve_module_path(use_clause.name, source_dir)
                if module_path is None:
                    self.error(f"Module '{use_clause.name}' not found", None)
                    continue

                # Load interface
                interface = self.load_interface(module_path)
                if interface is None:
                    continue

                # Import symbols
                self.import_symbols(interface, use_clause)

        # Check declarations
        if mod.decls:
            for decl in mod.decls:
                self.check_declaration(decl)

    def check_interface_unit(self, iface: InterfaceUnit) -> None:
        """Type check an interface unit."""
        # Process USES clauses first
        if iface.uses:
            source_dir = str(Path(self.source_file).parent) if self.source_file else None
            for use_clause in iface.uses:
                module_path = self.resolve_module_path(use_clause.name, source_dir)
                if module_path is None:
                    self.error(f"Module '{use_clause.name}' not found", None)
                    continue
                interface = self.load_interface(module_path)
                if interface is None:
                    continue
                self.import_symbols(interface, use_clause)

        # Check declarations
        if iface.decls:
            for decl in iface.decls:
                self.check_declaration(decl)

    def check_implementation_unit(self, impl: ImplementationUnit) -> None:
        """Type check an implementation unit and validate against its interface."""
        source_dir = str(Path(self.source_file).parent) if self.source_file else None
        iface = impl.interface
        if iface is None:
            iface_path = self.resolve_module_path(impl.name, source_dir)
            if iface_path:
                iface = self.load_interface(iface_path)
            else:
                self.error(f"Interface file for module '{impl.name}' not found", None)
        if iface:
            self.validate_implementation_against_interface(impl, iface)

        if impl.uses:
            for use_clause in impl.uses:
                module_path = self.resolve_module_path(use_clause.name, source_dir)
                if module_path is None:
                    self.error(f"Module '{use_clause.name}' not found", None)
                    continue
                loaded_iface = self.load_interface(module_path)
                if loaded_iface is None:
                    continue
                self.import_symbols(loaded_iface, use_clause)

        old_iface = self.current_interface_decls
        self.current_interface_decls = {getattr(decl, 'name', '').lower(): decl for decl in (iface.decls if iface else []) if getattr(decl, 'name', None)}
        try:
            if impl.decls:
                for decl in impl.decls:
                    self.check_declaration(decl)
        finally:
            self.current_interface_decls = old_iface

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

        readonly = 'READONLY' in {attr.upper() for attr in getattr(decl, 'attributes', [])}

        # Add each variable to the symbol table
        for name in decl.names:
            # Check for redeclaration
            existing = self.symbol_table.lookup_local(name)
            if existing:
                self.error(f"Variable '{name}' already declared at {existing.location}", decl)
                continue

            # Create symbol
            symbol = Symbol(name=name, type=var_type, kind='var', location=self.get_node_location(decl), is_mutable=not readonly)
            self.symbol_table.define(name, symbol)

    def check_const_decl(self, decl: ConstDecl) -> None:
        """Type check a constant declaration."""
        if not decl.name or not decl.value:
            return

        # Evaluate the constant value and infer type
        value_type = self.infer_expression_type(decl.value)
        if not value_type:
            self.error(f"Cannot infer type of constant", decl)
            return

        # Add constant to the symbol table
        existing = self.symbol_table.lookup_local(decl.name)
        if existing:
            self.error(f"Constant '{decl.name}' already declared at {existing.location}", decl)
            return

        symbol = Symbol(name=decl.name, type=value_type, kind='const', location=self.make_location(decl), is_mutable=False)
        self.symbol_table.define(decl.name, symbol)

    def check_type_decl(self, decl: TypeDecl) -> None:
        """Type check a type declaration."""
        if not decl.name or not decl.type_expr:
            return

        existing = self.symbol_table.lookup_local(decl.name)
        if existing:
            self.error(f"Type '{decl.name}' already declared at {existing.location}", decl)
            return

        resolved_type = self.resolve_type(decl.type_expr)
        if not resolved_type:
            self.error(f"Unknown type: {decl.type_expr}", decl)
            return

        # Tag anonymous enums with their declared name and register each member
        # as an ordinal constant so they can be used as values and set elements.
        if isinstance(resolved_type, EnumType):
            resolved_type.name = decl.name
            for member in resolved_type.members:
                self.symbol_table.define(
                    member,
                    Symbol(name=member, type=resolved_type, kind='const',
                           location=self.get_node_location(decl), is_mutable=False))

        self.symbol_table.define(decl.name, Symbol(name=decl.name, type=resolved_type, kind='type', location=self.get_node_location(decl), is_mutable=False))

    def check_func_decl(self, decl: FuncDecl) -> None:
        """Type check a function declaration."""
        if not decl.name:
            return

        attrs = {attr.upper() for attr in getattr(decl, 'attributes', [])}
        if 'PURE' in attrs:
            for param in getattr(decl, 'params', []):
                if getattr(param, 'mode', None) in {'VAR', 'VARS'}:
                    self.error(f"PURE function '{decl.name}' cannot have VAR/VARS parameters", decl)

        effective_decl = decl
        iface_decl = self.current_interface_decls.get(decl.name.lower()) if decl.name else None
        if iface_decl and not decl.params:
            effective_decl = iface_decl

        # Resolve parameter types
        param_types = []
        if effective_decl.params:
            for param in effective_decl.params:
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
            self.error(f"Function '{decl.name}' already declared at {existing.location}", decl)
            return

        # Add to symbol table
        symbol = Symbol(name=decl.name, type=func_type, kind='function', location=self.make_location(decl))
        self.symbol_table.define(decl.name, symbol)

        # Check function body
        old_func = self.current_function
        old_return_type = self.current_function_return_type
        self.current_function = decl
        self.current_function_return_type = return_type
        self.symbol_table.enter_scope()

        # Add parameters to scope
        for param in effective_decl.params:
            param_type = self.resolve_type(param.type_expr)
            if param_type:
                for name in param.names:
                    param_symbol = Symbol(name=name, type=param_type, kind='parameter', location=self.make_location(param), is_mutable=param.mode not in {'CONST', 'CONSTS'})
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

        attrs = {attr.upper() for attr in getattr(decl, 'attributes', [])}
        if 'PURE' in attrs:
            self.error(f"PURE is only valid on functions, not procedure '{decl.name}'", decl)

        effective_decl = decl
        iface_decl = self.current_interface_decls.get(decl.name.lower()) if decl.name else None
        if iface_decl and not decl.params:
            effective_decl = iface_decl

        # Resolve parameter types
        param_types = []
        if effective_decl.params:
            for param in effective_decl.params:
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
            self.error(f"Procedure '{decl.name}' already declared at {existing.location}", decl)
            return

        # Add to symbol table
        symbol = Symbol(name=decl.name, type=proc_type, kind='procedure', location=self.make_location(decl))
        self.symbol_table.define(decl.name, symbol)

        # Check procedure body
        old_proc = self.current_procedure
        self.current_procedure = decl
        self.symbol_table.enter_scope()

        # Add parameters to scope
        for param in effective_decl.params:
            param_type = self.resolve_type(param.type_expr)
            if param_type:
                for name in param.names:
                    param_symbol = Symbol(name=name, type=param_type, kind='parameter', location=self.make_location(param), is_mutable=param.mode not in {'CONST', 'CONSTS'})
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
        elif isinstance(stmt, LabelStmt):
            self.check_statement(stmt.stmt)

    def check_if_stmt(self, stmt: IfStmt) -> None:
        """Type check an IF statement."""
        # Condition must be BOOLEAN
        cond_type = self.infer_expression_type(stmt.cond)
        if cond_type and not cond_type.equivalent_to(BOOLEAN_TYPE):
            self.error(f"IF condition must be BOOLEAN, got {cond_type}", stmt)

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
                self.error(f"FOR loop variable must be INTEGER, got {sym.type}", stmt)

        # Loop bounds must be INTEGER
        if stmt.start:
            start_type = self.infer_expression_type(stmt.start)
            if start_type and not start_type.equivalent_to(INTEGER_TYPE):
                self.error(f"FOR start bound must be INTEGER, got {start_type}", stmt)

        if stmt.end:
            end_type = self.infer_expression_type(stmt.end)
            if end_type and not end_type.equivalent_to(INTEGER_TYPE):
                self.error(f"FOR end bound must be INTEGER, got {end_type}", stmt)

        # Check loop body
        if stmt.body:
            self.check_statement(stmt.body)

    def check_while_stmt(self, stmt: WhileStmt) -> None:
        """Type check a WHILE statement."""
        # Condition must be BOOLEAN
        cond_type = self.infer_expression_type(stmt.cond)
        if cond_type and not cond_type.equivalent_to(BOOLEAN_TYPE):
            self.error(f"WHILE condition must be BOOLEAN, got {cond_type}", stmt)

        # Check body
        if stmt.body:
            self.check_statement(stmt.body)

    def check_repeat_stmt(self, stmt: RepeatStmt) -> None:
        """Type check a REPEAT statement."""
        # Condition must be BOOLEAN
        cond_type = self.infer_expression_type(stmt.cond)
        if cond_type and not cond_type.equivalent_to(BOOLEAN_TYPE):
            self.error(f"REPEAT..UNTIL condition must be BOOLEAN, got {cond_type}", stmt)

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
            self.error("RETURN statement outside of function", stmt)
            return

        # If function has return type, RETURN value must match
        if self.current_function_return_type and hasattr(stmt, 'value') and stmt.value:
            value_type = self.infer_expression_type(stmt.value)
            if value_type and not can_assign(value_type, self.current_function_return_type):
                self.error(f"RETURN type mismatch: expected {self.current_function_return_type}, got {value_type}", stmt)

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
                        self.error(f"Cannot assign {value_type} to function return type {self.current_function_return_type}", stmt)
                return
            # Regular variable assignment
            sym = self.symbol_table.lookup(target_name)
            if sym:
                target_type = sym.type
        elif isinstance(stmt.target, Designator):
            # Designator with selectors (array/record/pointer access)
            sym = self.symbol_table.lookup(stmt.target.name)
            if sym and not sym.is_mutable:
                self.error(f"Cannot assign to immutable {sym.kind}: {stmt.target.name}", stmt)
            target_type = self.infer_designator_type(stmt.target)
            if target_type:
                # Type check successful - target_type is now the element/field type
                value_type = self.infer_expression_type(stmt.expr)
                if value_type and not can_assign(value_type, target_type):
                    self.error(f"Cannot assign {value_type} to {target_type}", stmt)
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
            self.error(f"Cannot assign to immutable {sym.kind}: {target_name}", stmt)

        # Check type compatibility
        value_type = self.infer_expression_type(stmt.expr)
        if value_type:
            if not can_assign(value_type, target_type):
                self.error(f"Cannot assign {value_type} to {target_type}", stmt)

    def check_proc_call_stmt(self, stmt: ProcCallStmt) -> None:
        """Type check a procedure call statement."""
        if not stmt.name:
            return

        # Look up the procedure (Pascal is case-insensitive)
        lookup_name = stmt.name.upper()
        sym = self.symbol_table.lookup(lookup_name) or self.symbol_table.lookup(stmt.name)
        if not sym:
            self.error(f"Undefined procedure: {stmt.name}", stmt)
            return

        if not isinstance(sym.type, ProcedureType):
            self.error(f"'{stmt.name}' is not a procedure", stmt)
            return

        # Check argument types (including built-in procedures)
        if stmt.args:
            # Special handling for string procedures (section 7.2)
            if stmt.name.upper() == 'CONCAT':
                self._check_concat_args(stmt)
                return
            elif stmt.name.upper() == 'COPYLST':
                self._check_copylst_args(stmt)
                return
            elif stmt.name.upper() == 'COPYSTR':
                self._check_copystr_args(stmt)
                return
            
            # For built-in procedures like WRITELN/WRITE/READLN, skip count/type checks but still
            # check that the arguments are valid expressions (e.g., defined variables)
            if stmt.name.upper() not in ['WRITELN', 'WRITE', 'READLN']:
                # For user-defined procedures, check argument count
                expected_args = len(sym.type.params)
                actual_args = len(stmt.args)
                if actual_args != expected_args:
                    self.error(f"Procedure '{stmt.name}' expects {expected_args} arguments, got {actual_args}", stmt)
                    return

            # Check that all arguments are well-formed (this will catch undefined variables)
            for i, arg in enumerate(stmt.args):
                value_arg = arg.expr if isinstance(arg, WriteArg) else arg
                arg_type = self.infer_expression_type(value_arg)
                if isinstance(arg, WriteArg):
                    if arg.width is not None:
                        self.infer_expression_type(arg.width)
                    if arg.precision is not None:
                        self.infer_expression_type(arg.precision)
                # If it's a user-defined procedure, also check type compatibility
                if stmt.name.upper() not in ['WRITELN', 'WRITE', 'READLN'] and arg_type:
                    if i < len(sym.type.params):
                        _, param_type = sym.type.params[i]
                        if not can_assign(arg_type, param_type):
                            self.error(f"Argument {i+1} type mismatch: expected {param_type}, got {arg_type}", stmt)
            return

    def _check_concat_args(self, stmt: ProcCallStmt) -> None:
        """Type check CONCAT(VAR D: LSTRING; CONST S: STRING).
        
        D (destination) must be an LSTRING variable (mutable).
        S (source) must be a STRING or LSTRING (readable).
        Error if upper(D) < length(D) + upper(S) (capacity check).
        """
        if len(stmt.args) != 2:
            self.error(f"CONCAT expects 2 arguments, got {len(stmt.args)}", stmt)
            return
        
        # Argument 1: destination (VAR LSTRING)
        dest_arg = stmt.args[0]
        if not isinstance(dest_arg, Identifier) and not isinstance(dest_arg, Designator):
            self.error("CONCAT: first argument must be a designator (variable)", stmt)
            return
        
        dest_name = dest_arg.name if isinstance(dest_arg, Identifier) else dest_arg.name
        dest_sym = self.symbol_table.lookup(dest_name) or self.symbol_table.lookup(dest_name.upper())
        if not dest_sym:
            self.error(f"CONCAT: undefined variable '{dest_name}'", stmt)
            return
        
        dest_type = dest_sym.type if dest_sym else None
        if not isinstance(dest_type, LStringType):
            self.error(f"CONCAT: first argument must be LSTRING, got {dest_type}", stmt)
            return
        
        if not dest_sym.is_mutable:
            self.error(f"CONCAT: first argument must be mutable (VAR parameter)", stmt)
            return
        
        # Argument 2: source (CONST STRING or LSTRING)
        src_arg = stmt.args[1]
        src_type = self.infer_expression_type(src_arg)
        if not isinstance(src_type, (StringType, LStringType)):
            self.error(f"CONCAT: second argument must be STRING or LSTRING, got {src_type}", stmt)
            return

    def _check_copylst_args(self, stmt: ProcCallStmt) -> None:
        """Type check COPYLST(CONST S: STRING; VAR D: LSTRING).
        
        S (source) must be a STRING or LSTRING (readable).
        D (destination) must be an LSTRING variable (mutable).
        Error if upper(D) < upper(S) (capacity check).
        """
        if len(stmt.args) != 2:
            self.error(f"COPYLST expects 2 arguments, got {len(stmt.args)}", stmt)
            return
        
        # Argument 1: source (CONST STRING or LSTRING)
        src_arg = stmt.args[0]
        src_type = self.infer_expression_type(src_arg)
        if not isinstance(src_type, (StringType, LStringType)):
            self.error(f"COPYLST: first argument must be STRING or LSTRING, got {src_type}", stmt)
            return
        
        # Argument 2: destination (VAR LSTRING)
        dest_arg = stmt.args[1]
        if not isinstance(dest_arg, Identifier) and not isinstance(dest_arg, Designator):
            self.error("COPYLST: second argument must be a designator (variable)", stmt)
            return
        
        dest_name = dest_arg.name if isinstance(dest_arg, Identifier) else dest_arg.name
        dest_sym = self.symbol_table.lookup(dest_name) or self.symbol_table.lookup(dest_name.upper())
        if not dest_sym:
            self.error(f"COPYLST: undefined variable '{dest_name}'", stmt)
            return
        
        dest_type = dest_sym.type if dest_sym else None
        if not isinstance(dest_type, LStringType):
            self.error(f"COPYLST: second argument must be LSTRING, got {dest_type}", stmt)
            return
        
        if not dest_sym.is_mutable:
            self.error(f"COPYLST: second argument must be mutable (VAR parameter)", stmt)
            return

    def _check_copystr_args(self, stmt: ProcCallStmt) -> None:
        """Type check COPYSTR(CONST S: STRING; VAR D: STRING).
        
        S (source) must be a STRING or LSTRING (readable).
        D (destination) must be a STRING variable (mutable).
        Error if upper(D) < upper(S) (capacity check).
        """
        if len(stmt.args) != 2:
            self.error(f"COPYSTR expects 2 arguments, got {len(stmt.args)}", stmt)
            return
        
        # Argument 1: source (CONST STRING or LSTRING)
        src_arg = stmt.args[0]
        src_type = self.infer_expression_type(src_arg)
        if not isinstance(src_type, (StringType, LStringType)):
            self.error(f"COPYSTR: first argument must be STRING or LSTRING, got {src_type}", stmt)
            return
        
        # Argument 2: destination (VAR STRING)
        dest_arg = stmt.args[1]
        if not isinstance(dest_arg, Identifier) and not isinstance(dest_arg, Designator):
            self.error("COPYSTR: second argument must be a designator (variable)", stmt)
            return
        
        dest_name = dest_arg.name if isinstance(dest_arg, Identifier) else dest_arg.name
        dest_sym = self.symbol_table.lookup(dest_name) or self.symbol_table.lookup(dest_name.upper())
        if not dest_sym:
            self.error(f"COPYSTR: undefined variable '{dest_name}'", stmt)
            return
        
        dest_type = dest_sym.type if dest_sym else None
        if not isinstance(dest_type, StringType):
            self.error(f"COPYSTR: second argument must be STRING, got {dest_type}", stmt)
            return
        
        if not dest_sym.is_mutable:
            self.error(f"COPYSTR: second argument must be mutable (VAR parameter)", stmt)
            return

    def _decode_pascal_string(self, value: str) -> str:
        """Return the runtime contents represented by a Pascal string token."""
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        return value.replace("''", "'")

    def infer_expression_type(self, expr: Expression) -> Optional[Type]:
        """Infer the type of an expression."""
        if isinstance(expr, IntLiteral):
            return INTEGER_TYPE
        elif isinstance(expr, RealLiteral):
            return REAL_TYPE
        elif isinstance(expr, BoolLiteral):
            return BOOLEAN_TYPE
        elif isinstance(expr, CharLiteral):
            return CHAR_TYPE
        elif isinstance(expr, NilLiteral):
            return PointerType(CHAR_TYPE)
        elif isinstance(expr, StringLiteral):
            return LStringType(len(self._decode_pascal_string(expr.value)))
        elif isinstance(expr, SetConstructor):
            declared_set_type: Optional[SetType] = None
            if expr.type_name:
                sym = self.symbol_table.lookup(expr.type_name)
                if not sym or sym.kind != 'type':
                    self.error(f"Unknown set type: {expr.type_name}", expr)
                    return None
                if not isinstance(sym.type, SetType):
                    self.error(f"Typed set constructor prefix must name a set type, got {sym.type}", expr)
                    return None
                declared_set_type = sym.type
                if not all(self.is_constant_set_element(el) for el in expr.elements):
                    self.error("Typed set constructors require constant elements", expr)
                    return None
            if not expr.elements:
                return declared_set_type or SetType(INTEGER_TYPE)
            element_type: Optional[Type] = None
            for el in expr.elements:
                if isinstance(el, RangeExpr):
                    low_type = self.infer_expression_type(el.low)
                    high_type = self.infer_expression_type(el.high)
                    if not low_type or not high_type:
                        return None
                    if not low_type.equivalent_to(high_type):
                        self.error(f"Set range bounds must have the same ordinal type, got {low_type} and {high_type}", el)
                        return None
                    cur_type = low_type
                else:
                    cur_type = self.infer_expression_type(el)
                if not cur_type:
                    return None
                if declared_set_type and not can_assign(cur_type, declared_set_type.element_type) and not can_assign(declared_set_type.element_type, cur_type):
                    self.error(f"Set element type mismatch: expected {declared_set_type.element_type}, got {cur_type}", el)
                    return None
                if element_type is None:
                    element_type = cur_type
                elif not cur_type.equivalent_to(element_type):
                    self.error(f"Set element type mismatch: expected {element_type}, got {cur_type}", el)
                    return None
            return declared_set_type or SetType(element_type or INTEGER_TYPE)
        elif isinstance(expr, AdrExpr):
            # Address-of operator (adr var_name)
            sym = self.symbol_table.lookup(expr.name)
            if not sym:
                self.error(f"Undefined variable: {expr.name}", expr)
                return None
            return PointerType(sym.type, flavor='ADR')
        elif isinstance(expr, AdsExpr):
            # Segmented address-of operator (ads var_name)
            sym = self.symbol_table.lookup(expr.name)
            if not sym:
                self.error(f"Undefined variable: {expr.name}", expr)
                return None
            return PointerType(sym.type, flavor='ADS')
        elif isinstance(expr, SizeofExpr):
            # Sizeof operator (sizeof var_name or type)
            return INTEGER_TYPE
        elif isinstance(expr, UpperExpr) or isinstance(expr, LowerExpr):
            sym = self.symbol_table.lookup(expr.name)
            if not sym:
                self.error(f"Undefined variable: {expr.name}", expr)
                return None
            ty = sym.type
            if isinstance(ty, ArrayType):
                return INTEGER_TYPE
            self.error(f"Function '{type(expr).__name__[:-4].upper()}' expects an array variable", expr)
            return None
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
                result = binary_op_result_type(left_type, expr.op, right_type)
                if result is None:
                    self.error(f"Operator '{expr.op}' cannot be applied to operands of type {left_type} and {right_type}", expr)
                return result
            return None
        elif isinstance(expr, UnaryOp):
            operand_type = self.infer_expression_type(expr.operand)
            if operand_type:
                result = unary_op_result_type(operand_type, expr.op)
                if result is None:
                    self.error(f"Operator '{expr.op}' cannot be applied to operand of type {operand_type}", expr)
                return result
            return None
        elif isinstance(expr, FuncCall):
            lookup_name = expr.name.upper()
            if lookup_name == 'ABS':
                if len(expr.args) != 1:
                    self.error(f"Function 'ABS' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type in (INTEGER_TYPE, REAL_TYPE):
                    return arg_type
                if arg_type:
                    self.error(f"Argument 1 type mismatch: expected INTEGER or REAL, got {arg_type}", expr)
                return None
            if lookup_name == 'SQRT':
                if len(expr.args) != 1:
                    self.error(f"Function 'SQRT' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type in (INTEGER_TYPE, REAL_TYPE):
                    return REAL_TYPE
                if arg_type:
                    self.error(f"Argument 1 type mismatch: expected INTEGER or REAL, got {arg_type}", expr)
                return None
            if lookup_name == 'SQR':
                if len(expr.args) != 1:
                    self.error(f"Function 'SQR' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type in (INTEGER_TYPE, REAL_TYPE):
                    return arg_type
                if arg_type:
                    self.error(f"Argument 1 type mismatch: expected INTEGER or REAL, got {arg_type}", expr)
                return None
            if lookup_name in {'SUCC', 'PRED'}:
                if len(expr.args) != 1:
                    self.error(f"Function '{lookup_name}' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type == INTEGER_TYPE:
                    return INTEGER_TYPE
                if arg_type:
                    self.error(f"Argument 1 type mismatch: expected INTEGER, got {arg_type}", expr)
                return None
            if lookup_name in {'HIBYTE', 'LOBYTE'}:
                if len(expr.args) != 1:
                    self.error(f"Function '{lookup_name}' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type == INTEGER_TYPE:
                    return CHAR_TYPE
                if arg_type:
                    self.error(f"Argument 1 type mismatch: expected INTEGER, got {arg_type}", expr)
                return None
            if lookup_name in {'TRUNC', 'ROUND'}:
                if len(expr.args) != 1:
                    self.error(f"Function '{lookup_name}' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type == REAL_TYPE:
                    return INTEGER_TYPE
                if arg_type:
                    self.error(f"Argument 1 type mismatch: expected REAL, got {arg_type}", expr)
                return None
            if lookup_name == 'FLOAT':
                if len(expr.args) != 1:
                    self.error(f"Function 'FLOAT' expects 1 argument, got {len(expr.args)}", expr)
                    return None
                arg_type = self.infer_expression_type(expr.args[0])
                if arg_type == INTEGER_TYPE:
                    return REAL_TYPE
                if arg_type:
                    self.error(f"Argument 1 type mismatch: expected INTEGER, got {arg_type}", expr)
                return None
            sym = self.symbol_table.lookup(lookup_name) or self.symbol_table.lookup(expr.name)
            if not sym:
                self.error(f"Undefined function: {expr.name}", expr)
                return None
            if isinstance(sym.type, FunctionType):
                # Check argument count
                expected_args = len(sym.type.params)
                actual_args = len(expr.args) if expr.args else 0
                if actual_args != expected_args:
                    self.error(f"Function '{expr.name}' expects {expected_args} arguments, got {actual_args}", expr)
                # Check argument types
                if expr.args:
                    for i, (arg, (param_name, param_type)) in enumerate(zip(expr.args, sym.type.params)):
                        arg_type = self.infer_expression_type(arg)
                        if arg_type and not can_assign(arg_type, param_type):
                            self.error(f"Argument {i+1} type mismatch: expected {param_type}, got {arg_type}", expr)
                return sym.type.return_type
            return None
        elif isinstance(expr, Designator):
            return self.infer_designator_type(expr)
        else:
            # Unknown expression type
            return None

    def is_constant_set_element(self, expr: Expression) -> bool:
        """Return True when a set element/range endpoint is compile-time constant."""
        if isinstance(expr, RangeExpr):
            return self.is_constant_set_element(expr.low) and self.is_constant_set_element(expr.high)
        if isinstance(expr, (IntLiteral, RealLiteral, BoolLiteral, CharLiteral, StringLiteral)):
            return True
        if isinstance(expr, Identifier):
            sym = self.symbol_table.lookup(expr.name)
            return bool(sym and sym.kind == 'const')
        if isinstance(expr, UnaryOp):
            return self.is_constant_set_element(expr.operand)
        if isinstance(expr, BinOp):
            return self.is_constant_set_element(expr.left) and self.is_constant_set_element(expr.right)
        return False

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
                        self.error(f"Cannot index non-array type {current_type}", designator)
                        return None
                    # Check that index is INTEGER
                    if selector.index_or_field:
                        index_type = self.infer_expression_type(selector.index_or_field)
                        if index_type and not index_type.equivalent_to(INTEGER_TYPE):
                            self.error(f"Array index must be INTEGER, got {index_type}", designator)
                    current_type = current_type.element_type

                elif selector.kind == 'FIELD':
                    # Record field access
                    if not isinstance(current_type, RecordType):
                        self.error(f"Cannot access field on non-record type {current_type}", designator)
                        return None
                    field_name = selector.index_or_field
                    field_type = current_type.get_field_type(field_name)
                    if not field_type:
                        self.error(f"Record has no field '{field_name}'", designator)
                        return None
                    current_type = field_type

                elif selector.kind == 'DEREF':
                    # Pointer dereference
                    if not isinstance(current_type, PointerType):
                        self.error(f"Cannot dereference non-pointer type {current_type}", designator)
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
            elif name == 'ADRMEM':
                return PointerType(CHAR_TYPE)
            elif name == 'STRING':
                max_len = int(type_expr.param) if isinstance(type_expr.param, int) else 256
                return StringType(max_len)
            elif name == 'LSTRING':
                max_len = int(type_expr.param) if isinstance(type_expr.param, int) else 256
                return LStringType(max_len)
            else:
                sym = self.symbol_table.lookup(type_expr.name)
                if sym and sym.kind == 'type':
                    return sym.type
                return None
        elif isinstance(type_expr, ASTLStringType):
            return LStringType(type_expr.max_len)
        elif isinstance(type_expr, ASTEnumType):
            return EnumType(list(type_expr.values))
        elif isinstance(type_expr, ASTSetType):
            base_type = self.resolve_type(type_expr.base)
            return SetType(base_type) if base_type else None
        elif isinstance(type_expr, ASTSubrangeType):
            if type_expr.host:
                host = self.resolve_type(NamedType(type_expr.host, None))
                if host:
                    return host
            low_type = self.infer_expression_type(type_expr.low)
            high_type = self.infer_expression_type(type_expr.high)
            if low_type and high_type and low_type.equivalent_to(high_type):
                return low_type
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
        elif isinstance(type_expr, ASTPointerType):
            base_type = self.resolve_type(type_expr.base)
            flavor = getattr(type_expr, 'flavor', 'POINTER')
            return PointerType(base_type, flavor=flavor) if base_type else PointerType(CHAR_TYPE, flavor=flavor)
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
        self.errors.append(TypeCheckError(message=message, location=loc, severity='error'))

    def warning(self, message: str, location=None) -> None:
        """Record a type checking warning."""
        # Handle node objects or tuple locations
        loc = None
        if location is not None:
            if isinstance(location, ASTNode):
                loc = self.get_node_location(location)
            else:
                loc = self.make_location(location)
        self.warnings.append(TypeCheckError(message=message, location=loc, severity='warning'))
