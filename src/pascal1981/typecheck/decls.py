"""Declaration checking: blocks, VAR/CONST/TYPE/VALUE declarations,
foreign ([C]) routine ABI validation, and procedure/function declarations.

Mixin for PascalTypeChecker, split out of type_checker.py as pure code
movement: methods are unchanged and still reach each other through self.
"""


from ..ast_nodes import (
    Block,
    ConstDecl,
    FuncDecl,
    NamedType,
    ProcDecl,
    TypeDecl,
    ValueDecl,
    VarDecl,
)
from ..ast_nodes import RecordType as ASTRecordType
from ..symbol_table import Symbol
from ..type_system import (
    INTEGER_TYPE,
    ArrayType,
    EnumType,
    FunctionType,
    LStringType,
    ProcedureType,
    RecordType,
    SetType,
    StringType,
    Type,
    can_assign,
)


class DeclsMixin:

    def check_block(self, block: Block) -> None:
        """Type check a block (declarations + statements)."""
        if not block:
            return

        # Process declarations first
        if block.decls:
            self._predeclare_record_types(block.decls)
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
        elif isinstance(decl, ValueDecl):
            self.check_value_decl(decl)

    def check_value_decl(self, decl: ValueDecl) -> None:
        """Type check a VALUE-section initializer."""
        sym = self.symbol_table.lookup(decl.name)
        if not sym:
            self.error(f"Undefined variable in VALUE section: {decl.name}", decl)
            return
        if sym.kind != 'var':
            self.error(f"VALUE target '{decl.name}' is not a variable", decl)
            return
        for selector in decl.target.selectors:
            if selector.kind != 'FIELD':
                self.error("VALUE section supports only variables and record-field selectors", decl)
                return
        if not self.is_constant_set_element(decl.value):
            self.error("VALUE initializer must be constant", decl)
            return
        target_type = self.infer_designator_type(decl.target)
        if target_type is None:
            return
        value_type = self.infer_expression_type(decl.value, target_type)
        if value_type and not can_assign(value_type, target_type):
            self.error(f"Cannot initialize {target_type} with {value_type} in VALUE section", decl)

    def _can_pass_value_argument(self, arg_type: Type, param_type: Type) -> bool:
        """Assignment compatibility for by-value parameters.

        STRING without an explicit bound is represented in the builtin table as
        STRING(255); use capacity semantics for that super-array-like formal,
        while ordinary assignment to STRING(n) remains exact-length.
        """
        if isinstance(param_type, StringType) and param_type.max_len == 255 and isinstance(arg_type, (StringType, LStringType)):
            return arg_type.max_len <= param_type.max_len
        return can_assign(arg_type, param_type)

    def check_var_decl(self, decl: VarDecl) -> None:
        """Type check a variable declaration."""
        if not decl.names or not decl.type_expr:
            return

        # Resolve the type
        var_type = self.resolve_type(decl.type_expr)
        if not var_type:
            self.error(f"Unknown type: {decl.type_expr}", decl)
            return

        readonly = 'READONLY' in {attr.name.upper() for attr in getattr(decl, 'attributes', [])}
        # [SPACE(s)] residence attribute: gated on DEVICE MODULE, folded to an
        # ordinal carried on each variable's Symbol (design S4.4).
        residence = None
        for attr in getattr(decl, 'attributes', []):
            if attr.name.upper() == 'SPACE':
                residence = self._fold_space(attr.arg)
                if residence is None:
                    self.error("invalid address space in [SPACE(...)] attribute", decl)
                elif not self.in_device_module:
                    self.error("address spaces require device code", decl)

        # Add each variable to the symbol table
        for name in decl.names:
            # Check for redeclaration
            existing = self.symbol_table.lookup_local(name)
            if existing and not getattr(existing, 'is_builtin', False):
                self.error(f"Variable '{name}' already declared at {existing.location}", decl)
                continue

            # Create symbol
            symbol = Symbol(name=name, type=var_type, kind='var', location=self.get_node_location(decl), is_mutable=not readonly, space=residence)
            setattr(symbol, 'type_expr', decl.type_expr)
            self.symbol_table.define(name, symbol)

    def check_const_decl(self, decl: ConstDecl) -> None:
        """Type check a constant declaration."""
        if not decl.name or not decl.value:
            return

        # Evaluate the constant value and infer type
        value_type = self.infer_expression_type(decl.value)
        if not value_type:
            self.error("Cannot infer type of constant", decl)
            return

        # Add constant to the symbol table
        existing = self.symbol_table.lookup_local(decl.name)
        if existing and not getattr(existing, 'is_builtin', False):
            self.error(f"Constant '{decl.name}' already declared at {existing.location}", decl)
            return

        symbol = Symbol(name=decl.name, type=value_type, kind='const', location=self.make_location(decl), is_mutable=False)
        # Stash the folded integer value (if any) so that constant
        # *expressions* referencing this CONST (e.g. `k + 1`, `2 * SIZE`) can be
        # recognized as compile-time INTEGER constants for the manual's
        # WORD/INTEGER exemption.  Stored under a dedicated attribute -- NOT
        # Symbol.value, which is the codegen LLVM value.  Decl-order checking
        # guarantees earlier CONSTs are already folded when a later CONST
        # references them.
        setattr(symbol, 'const_int', self._fold_const_int(decl.value))
        self.symbol_table.define(decl.name, symbol)

    def _predeclare_record_types(self, decls) -> None:
        """Register an empty placeholder for every named RECORD type in a
        declaration block before any bodies are resolved.

        Pascal lets a pointer type forward-reference a record declared later in
        the same TYPE section (``np = ^node; node = RECORD ... next: np END``),
        and a record reference itself through a pointer field. Resolving bases
        eagerly turned both into ^CHAR. Pre-creating a stable (initially empty)
        RecordType per name and filling it in place when its body is reached
        means such references resolve to the real record object.
        """
        for decl in decls:
            if not isinstance(decl, TypeDecl) or not decl.name:
                continue
            if not isinstance(decl.type_expr, ASTRecordType):
                continue
            if self.symbol_table.lookup_local(decl.name):
                continue
            placeholder = RecordType(decl.name, {})
            self.symbol_table.define(
                decl.name,
                Symbol(name=decl.name, type=placeholder, kind='type', location=self.get_node_location(decl), is_mutable=False),
            )
            self._predeclared_types.add(decl.name.upper())

    def check_type_decl(self, decl: TypeDecl) -> None:
        """Type check a type declaration."""
        if not decl.name or not decl.type_expr:
            return

        existing = self.symbol_table.lookup_local(decl.name)

        # A record name pre-declared by _predeclare_record_types: fill the
        # existing placeholder in place so any forward/self pointer references
        # that already captured it now see the real fields.
        if decl.name.upper() in self._predeclared_types and existing is not None \
                and isinstance(existing.type, RecordType) and isinstance(decl.type_expr, ASTRecordType):
            placeholder = existing.type
            for names, field_type_expr in (decl.type_expr.fields or []):
                field_type = self.resolve_type(field_type_expr)
                if field_type:
                    for field_name in names:
                        placeholder.fields[field_name] = field_type
            self._predeclared_types.discard(decl.name.upper())
            return

        if existing and not getattr(existing, 'is_builtin', False):
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
                self.symbol_table.define(member, Symbol(name=member, type=resolved_type, kind='const', location=self.get_node_location(decl), is_mutable=False))

        symbol = Symbol(name=decl.name, type=resolved_type, kind='type', location=self.get_node_location(decl), is_mutable=False)
        setattr(symbol, 'type_expr', decl.type_expr)
        self.symbol_table.define(decl.name, symbol)

    # Aggregate Pascal types that cannot cross the C ABI by value with the
    # current lowering (no per-target aggregate classifier exists yet).  Passed
    # or returned by value, they are silently mislowered, so a foreign routine
    # using them is rejected at type-check time.  See Phase 0 of
    # docs/c-abi-foreign-functions.md.
    _C_ABI_AGGREGATE_TYPES = (RecordType, ArrayType, SetType, StringType, LStringType)

    @staticmethod
    def _is_foreign_routine(decl) -> bool:
        """True if a routine is an EXTERN/EXTERNAL (foreign) import.

        Recognizes both the directive form (`; EXTERN;`) and the attribute form
        (`[EXTERN]`).  PUBLIC exports are intentionally out of scope: they are
        defined here, and the cross-ABI concern for them runs the other
        direction (C calling Pascal).
        """
        directive = (getattr(decl, 'directive', None) or '').upper()
        attrs = {a.name.upper() for a in getattr(decl, 'attributes', [])}
        return directive in {'EXTERN', 'EXTERNAL'} or bool(attrs & {'EXTERN', 'EXTERNAL'})

    def _check_foreign_abi(self, decl, return_type) -> None:
        """C-FFI guard: gate the [C] surface and reject ABI-incompatible signatures.

        First, the [C]/[CDECL] foreign-ABI marker is part of the C-FFI surface,
        which is available only under the extended dialect (the wide widths it
        implies are themselves extended types); it is rejected in the faithful
        1981 dialect, so a vintage program cannot opt into C-ABI lowering.

        Then, for foreign routines: by-value aggregate parameters and aggregate
        return types are rejected on plain EXTERN routines (no [C]) instead of
        being silently mislowered; with [C] they are lowered correctly by the
        Phase 2 classifier.  By-reference (CONST/VAR/CONSTS/VARS) aggregates are
        fine -- they lower to a pointer, which is ABI-safe as long as the C side
        also takes a pointer.

        Also emits a non-fatal warning when a bare 16-bit INTEGER is used in a
        foreign signature, since C `int` is 32-bit; CINT/INTEGER32 (or CSHORT for
        a genuine C `short`) is almost always what was meant.
        """
        from ..features import is_extended
        attr_names = {a.name.upper() for a in getattr(decl, 'attributes', [])}
        has_c = 'C' in attr_names
        has_varargs = 'VARARGS' in attr_names
        if has_c and not is_extended(self.features):
            self.error(f"routine '{decl.name}': the [C] (C-ABI) attribute requires the "
                       f"extended dialect and is not available in the faithful 1981 dialect.", decl)
        if has_varargs and not is_extended(self.features):
            self.error(f"routine '{decl.name}': the [VARARGS] attribute requires the "
                       f"extended dialect and is not available in the faithful 1981 dialect.", decl)
        if has_varargs and not has_c:
            self.error(f"routine '{decl.name}': [VARARGS] requires the [C] attribute; "
                       f"variadic calls are only supported on C-ABI foreign routines.", decl)
        if has_varargs and getattr(self, 'in_device_module', False):
            self.error(f"routine '{decl.name}': [VARARGS] is not permitted in DEVICE code.", decl)
        if not self._is_foreign_routine(decl):
            return
        # The [C]/[CDECL] marker opts into C-ABI-correct lowering of by-value
        # aggregates (Phase 2 classifier). Plain EXTERN routines still reject
        # them, since without [C] the aggregate is passed as a raw LLVM value.
        for param in getattr(decl, 'params', []):
            by_reference = getattr(param, 'mode', None) in {'VAR', 'VARS', 'CONST', 'CONSTS'}
            param_type = self.resolve_type(param.type_expr) if param.type_expr else None
            names = ', '.join(param.names)
            if not by_reference and not has_c and isinstance(param_type, self._C_ABI_AGGREGATE_TYPES):
                self.error(
                    f"foreign routine '{decl.name}': by-value aggregate parameter '{names}' "
                    f"is not C-ABI compatible; pass it by CONST or VAR and declare the C side "
                    f"to take a pointer, or mark the routine [C] to pass it by value under the "
                    f"C ABI.", decl)
            elif not by_reference and isinstance(param.type_expr, NamedType) and param.type_expr.name.upper() == 'INTEGER':
                self.warning(
                    f"foreign routine '{decl.name}': parameter '{names}' is a 16-bit INTEGER, "
                    f"but C 'int' is 32-bit; use CINT/INTEGER32 (or CSHORT for a C 'short') to "
                    f"match the intended C width.", decl)
        if not has_c and isinstance(return_type, self._C_ABI_AGGREGATE_TYPES):
            self.error(
                f"foreign function '{decl.name}': by-value aggregate return is not C-ABI "
                f"compatible; return it through a CONST/VAR pointer parameter instead, or mark "
                f"the routine [C] to return it by value under the C ABI.", decl)

    def check_func_decl(self, decl: FuncDecl) -> None:
        """Type check a function declaration."""
        if not decl.name:
            return

        self._check_launch_bound_attrs(decl, is_function=True)
        attrs = {attr.name.upper() for attr in getattr(decl, 'attributes', [])}
        if 'PURE' in attrs:
            for param in getattr(decl, 'params', []):
                if getattr(param, 'mode', None) in {'VAR', 'VARS'}:
                    self.error(f"PURE function '{decl.name}' cannot have VAR/VARS parameters", decl)

        self._check_routine_decl(decl, is_function=True)

    def _check_routine_decl(self, decl, *, is_function: bool) -> None:
        """Shared FUNCTION/PROCEDURE declaration checking.

        Everything after the kind-specific attribute prelude (PURE and
        launch-bound validation, which `check_func_decl`/`check_proc_decl`
        keep so their error ordering is unchanged) is identical for the two
        routine kinds except for: the return type (functions only), the
        signature type constructed (FunctionType vs ProcedureType), the
        symbol kind / redeclaration-message noun, and which "current routine"
        context is saved around the body walk.
        """
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

        # Create the signature type.  [VARARGS] makes a [C] foreign routine
        # variadic; the flag must be threaded onto the type because the
        # call-site arity check reads sym.type.is_variadic.  (Finding 3.)
        _decl_attrs = {a.name.upper() for a in getattr(decl, 'attributes', [])}
        is_variadic = 'VARARGS' in _decl_attrs
        return_type = None
        if is_function:
            # Resolve return type
            return_type = INTEGER_TYPE
            if decl.return_type:
                return_type = self.resolve_type(decl.return_type)
                if not return_type:
                    self.error("Unknown return type", decl)
                    return_type = INTEGER_TYPE
            routine_type = FunctionType(decl.name, param_types, return_type, is_variadic=is_variadic)
            kind, noun = 'function', 'Function'
        else:
            routine_type = ProcedureType(decl.name, param_types, is_variadic=is_variadic)
            kind, noun = 'procedure', 'Procedure'

        # Phase 0 C-FFI guard: reject ABI-incompatible foreign signatures
        # (procedures have no return type, so only by-value aggregate params).
        self._check_foreign_abi(decl, return_type)

        # Check for redeclaration. A FORWARD declaration is completed (not
        # redeclared) by a later body definition -- this is what makes forward
        # references and mutual recursion expressible.
        existing = self.symbol_table.lookup_local(decl.name)
        if existing and not getattr(existing, 'is_builtin', False):
            if getattr(existing, 'is_forward', False) and decl.body is not None:
                existing.is_forward = False  # completing the forward declaration
            else:
                self.error(f"{noun} '{decl.name}' already declared at {existing.location}", decl)
                return
        else:
            # Add to symbol table (mark a FORWARD declaration awaiting completion)
            symbol = Symbol(name=decl.name, type=routine_type, kind=kind, location=self.make_location(decl))
            if decl.body is None and getattr(decl, 'directive', None) == 'FORWARD':
                symbol.is_forward = True
            self.symbol_table.define(decl.name, symbol)

        # Check the routine body
        if is_function:
            old_func = self.current_function
            old_return_type = self.current_function_return_type
            self.current_function = decl
            self.current_function_return_type = return_type
        else:
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
        if is_function:
            self.current_function = old_func
            self.current_function_return_type = old_return_type
        else:
            self.current_procedure = old_proc

    def check_proc_decl(self, decl: ProcDecl) -> None:
        """Type check a procedure declaration."""
        if not decl.name:
            return

        attrs = {attr.name.upper() for attr in getattr(decl, 'attributes', [])}
        if 'PURE' in attrs:
            self.error(f"PURE is only valid on functions, not procedure '{decl.name}'", decl)
        self._check_launch_bound_attrs(decl, is_function=False)

        self._check_routine_decl(decl, is_function=False)
