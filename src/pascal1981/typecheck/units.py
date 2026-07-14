"""Compiland-level checking: PROGRAM/MODULE/INTERFACE/IMPLEMENTATION units,
USES resolution and symbol import, and interface/implementation
signature matching.

Mixin for PascalTypeChecker, split out of type_checker.py as pure code
movement: methods are unchanged and still reach each other through self.
"""

from pathlib import Path
from typing import Any, Optional

from ..ast_nodes import (
    BoolLiteral,
    ConstDecl,
    FuncDecl,
    ImplementationUnit,
    InterfaceUnit,
    IntLiteral,
    ModuleUnit,
    NamedType,
    ProcDecl,
    ProgramUnit,
    RealLiteral,
    StringLiteral,
    TypeDecl,
    UseClause,
    VarDecl,
)
from ..parser import parse_file
from ..symbol_table import Symbol
from ..type_system import (
    BOOLEAN_TYPE,
    CHAR_TYPE,
    INTEGER_TYPE,
    REAL_TYPE,
    ProcedureType,
    Type,
)


class UnitsMixin:

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
            all_decls = list(interface.decls)

            # A DEVICE INTERFACE was written in the device dialect: its types
            # (INTEGER32, ADS(GLOBAL), etc.) must be resolved in device context.
            # We enter _device_context for the duration of the import so that
            # resolve_type and check_declaration see the same extended feature
            # set and address-space rules that the interface was compiled under.
            # The resulting symbols land in the *host* symbol table after the
            # context exits, so the host caller can reference them by name.
            is_device_iface = getattr(interface, 'is_device', False)

            with self._device_context(is_device_iface):
                # Build the exported-routine list by matching names from the
                # export list against the declarations.  Non-routine decls
                # (TYPE, CONST, VAR) are excluded from this list; they are
                # imported separately below so their presence does not inflate
                # the export count.
                export_name_set = {n.lower() for n in export_names}
                routine_decls = [d for d in all_decls if isinstance(d, (ProcDecl, FuncDecl)) and getattr(d, 'name', '').lower() in export_name_set]

                # Validate: every name in the export list must have a matching decl.
                declared_names = {getattr(d, 'name', '').lower() for d in routine_decls}
                missing = [n for n in export_names if n.lower() not in declared_names]
                if missing:
                    self.error(
                        f"Interface '{interface.name}' export list names not found "
                        f"in declarations: {missing}",
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
                    # Positional rename: pair each local alias with the nth export
                    # name, then find the matching decl by that export name.
                    pairs = []
                    for alias, ename in zip(imported_aliases, export_names):
                        decl = next((d for d in routine_decls if getattr(d, 'name', '').lower() == ename.lower()), None)
                        if decl:
                            pairs.append((alias, ename, decl))
                else:
                    pairs = [(n, n, next(d for d in routine_decls if getattr(d, 'name', '').lower() == n.lower())) for n in export_names]

                # Import non-exported TYPE/CONST decls so the importing scope
                # can reference shared buffer type names (e.g. PIXELS).  These
                # are checked under the same device context so INTEGER32,
                # ADS(GLOBAL), etc. resolve without errors.
                for decl in all_decls:
                    if isinstance(decl, (TypeDecl, ConstDecl)) and getattr(decl, 'name', None):
                        if not self.symbol_table.lookup_local(decl.name):
                            self.check_declaration(decl)

                # Build the routine symbols under device context so parameter
                # types involving INTEGER32 / ADS(s) / etc. resolve correctly.
                routine_symbols = []
                for local_name, _exported_name, decl in pairs:
                    sym = Symbol(
                        name=local_name,
                        type=self._get_declaration_type(decl),
                        kind=self._get_declaration_kind(decl),
                        is_mutable=isinstance(decl, VarDecl),
                    )
                    routine_symbols.append((local_name, sym))

            # Register the resolved symbols in the host scope (outside the
            # device context so no device restrictions apply to the host body).
            for local_name, sym in routine_symbols:
                if self.symbol_table.lookup_local(local_name):
                    self.error(f"Symbol '{local_name}' from module {uses.name} conflicts with existing definition", None)
                    continue
                self.symbol_table.define(local_name, sym)
            return
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

        for local_name, _exported_name, decl in pairs:
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
        if type(iface_decl) is not type(impl_decl):
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
        if prog.uses:
            for use_clause in prog.uses:
                spliced = next(
                    (i for i in getattr(prog, 'local_interfaces', []) if i.name.upper() == use_clause.name.upper()),
                    None,
                )
                if spliced is None:
                    self.error(f"Module '{use_clause.name}' must be provided by a spliced INTERFACE header in the source file", None)
                    continue
                self.import_symbols(spliced, use_clause)

        # Now type-check the program block
        self.check_block(prog.block)

    def check_module_unit(self, mod: ModuleUnit) -> None:
        """Type check a module unit."""
        if mod.uses:
            for use_clause in mod.uses:
                spliced = next(
                    (i for i in getattr(mod, 'local_interfaces', []) if i.name.upper() == use_clause.name.upper()),
                    None,
                )
                if spliced is None:
                    self.error(f"Module '{use_clause.name}' must be provided by a spliced INTERFACE header in the source file", None)
                    continue
                self.import_symbols(spliced, use_clause)

        # A DEVICE MODULE switches into the device dialect (extended minus the
        # recission set, plus the address-space surface) and the two-worlds
        # dereferenceability scope for the duration of its body (design S1.2/S3.3).
        with self._device_context(getattr(mod, 'is_device', False)):
            # Check declarations
            if mod.decls:
                for decl in mod.decls:
                    self.check_declaration(decl)

    def check_interface_unit(self, iface: InterfaceUnit) -> None:
        """Type check an interface unit."""
        if iface.uses:
            for use_clause in iface.uses:
                spliced = next(
                    (i for i in getattr(iface, 'local_interfaces', []) if i.name.upper() == use_clause.name.upper()),
                    None,
                )
                if spliced is None:
                    self.error(f"Module '{use_clause.name}' must be provided by a spliced INTERFACE header in the source file", None)
                    continue
                self.import_symbols(spliced, use_clause)

        with self._device_context(getattr(iface, 'is_device', False)):
            if getattr(iface, 'is_device', False) and getattr(iface, 'has_init', False):
                self.error("initializer code is not available in a DEVICE UNIT", None)
            # Check declarations
            if iface.decls:
                for decl in iface.decls:
                    self.check_declaration(decl)

    def _mark_exported_entries(self, impl: ImplementationUnit, iface: Any) -> None:
        """Flag each device-implementation routine whose name the interface
        exports.  In a DEVICE UNIT the interface's export
        list (`InterfaceUnit.params`) *is* the set of launchable kernel entries;
        everything else stays a device-internal routine.

        Marking happens here, on the implementation AST, precisely because the
        checker loads the interface from disk (`load_interface`) even under
        separate compilation, whereas codegen never sees it.
        Codegen then reads the flag rather than re-deriving the export list.
        """
        export_names = {n.lower() for n in getattr(iface, 'params', []) or []}
        if not export_names:
            return
        for decl in impl.decls or []:
            name = getattr(decl, 'name', None)
            if name and name.lower() in export_names and isinstance(decl, (ProcDecl, FuncDecl)):
                decl.is_exported_entry = True

    def check_implementation_unit(self, impl: ImplementationUnit) -> None:
        """Type check an implementation unit and validate against its interface."""
        iface = impl.interface
        if iface is None:
            self.error(f"IMPLEMENTATION OF {impl.name} must include its matching INTERFACE header before the implementation", None)
        if iface:
            if getattr(impl, 'is_device', False) != getattr(iface, 'is_device', False):
                self.error("device-ness of implementation must match its interface", None)
            self.validate_implementation_against_interface(impl, iface)
            if getattr(impl, 'is_device', False):
                self._mark_exported_entries(impl, iface)

        if impl.uses:
            for use_clause in impl.uses:
                spliced = next(
                    (i for i in getattr(impl, 'local_interfaces', []) if i.name.upper() == use_clause.name.upper()),
                    None,
                )
                if spliced is None:
                    self.error(f"Module '{use_clause.name}' must be provided by a spliced INTERFACE header in the source file", None)
                    continue
                self.import_symbols(spliced, use_clause)

        old_iface = self.current_interface_decls
        self.current_interface_decls = {getattr(decl, 'name', '').lower(): decl for decl in (iface.decls if iface else []) if getattr(decl, 'name', None)}
        try:
            with self._device_context(getattr(impl, 'is_device', False)):
                if getattr(impl, 'is_device', False) and impl.init_body is not None:
                    self.error("initializer code is not available in a DEVICE UNIT", None)

                # Seed TYPE and CONST aliases from the interface so the
                # implementation can reference them without restating.  Only
                # seed names that the implementation does not itself declare
                # (impl wins when both define the same name).
                if iface:
                    impl_type_names = {getattr(d, 'name', '').upper() for d in (impl.decls or []) if isinstance(d, TypeDecl)}
                    impl_const_names = {getattr(d, 'name', '').upper() for d in (impl.decls or []) if isinstance(d, ConstDecl)}
                    for decl in iface.decls:
                        name = getattr(decl, 'name', '') or ''
                        if isinstance(decl, TypeDecl) and name.upper() not in impl_type_names:
                            self.check_declaration(decl)
                        elif isinstance(decl, ConstDecl) and name.upper() not in impl_const_names:
                            self.check_declaration(decl)

                if impl.decls:
                    for decl in impl.decls:
                        self.check_declaration(decl)
        finally:
            self.current_interface_decls = old_iface
