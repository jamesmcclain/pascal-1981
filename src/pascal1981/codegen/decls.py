"""
DECLS mixin for Codegen.

Declaration code generation

"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, List, Optional, Tuple, Union

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder

from ..ast_nodes import *
from ..parser import parse_file
from .base import CodegenError, Scope, _is_gpu_triple


class DeclsMixin:
    """Mixin for decls functionality."""

    @contextmanager
    def _device_codegen_context(self, active: bool):
        """Temporarily switch lowering into device-code mode."""
        prev_is_device = self.is_device_module
        if active:
            self.is_device_module = True
            self.module.triple = self.device_triple
        try:
            yield
        finally:
            self.is_device_module = prev_is_device

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
            self.codegen_use_clause(use_clause, local_interfaces=getattr(unit, 'local_interfaces', []))

        # Codegen all declarations
        for decl in unit.block.decls:
            self.codegen_decl(decl)

        # Create main function if not already defined
        if 'main' not in [f.name for f in self.module.functions]:
            # main takes (argc, argv) so program-heading parameters can be bound
            # from the command line (vintage program-parameter model); ordinary
            # programs that ignore them are unaffected (C main(void) vs
            # main(int,char**) are link-compatible).
            i8pp = ir.IntType(8).as_pointer().as_pointer()
            main_type = ir.FunctionType(ir.IntType(32), [ir.IntType(32), i8pp])
            main_func = ir.Function(self.module, main_type, name='main')
            main_func.args[0].name = 'argc'
            main_func.args[1].name = 'argv'
            entry_block = main_func.append_basic_block(name='entry')
            self.builder = IRBuilder(entry_block)
            self.current_function = main_func

            # Predeclared TEXT files exist even when not listed in the PROGRAM
            # heading. IBM Pascal automatically initializes INPUT/OUTPUT; the
            # later I/O primitives will attach devices, while 8.1 establishes
            # the concrete file-control block and buffer model.
            for sym in list(self.scope.symbols.values()):
                if isinstance(self.resolve_type_alias(sym.type_expr), FileType):
                    self._init_file_storage(sym.llvm_value, sym.type_expr)
            # Bind program-heading parameters from the command line.
            self._codegen_program_parameters(unit)
            # Execute the program body
            prev_labels = self.setup_function_labels(unit.block.body)
            self.codegen_stmt_list(unit.block.body)
            self.label_blocks = prev_labels

            # Default return 0
            if not self.builder.block.is_terminated:
                self.builder.ret(ir.Constant(ir.IntType(32), 0))

        self._emit_launch_registry()
        return self.module

    def _emit_cstring_ptr(self, text: str) -> ir.Value:
        """Create a NUL-terminated global C string and return an i8* to it."""
        data = bytearray(text.encode('utf-8') + b'\0')
        const = ir.Constant(ir.ArrayType(ir.IntType(8), len(data)), data)
        gv = ir.GlobalVariable(self.module, const.type, name=self.unique_name('argname'))
        gv.initializer = const
        gv.global_constant = True
        zero = ir.Constant(ir.IntType(32), 0)
        return self.builder.gep(gv, [zero, zero])

    def _codegen_program_parameters(self, unit) -> None:
        """Populate program-heading parameters from the command line.

        Faithful to the vintage model (IBM Pascal manual 13-5..13-7): each
        heading parameter other than INPUT/OUTPUT is read, in heading order,
        from successive command-line tokens, prompting at the keyboard when a
        token is absent.  Reading reuses the ordinary READ parsers via stdin
        redirection (see runtime/cmdline.c), so a parameter parses exactly as it
        would interactively.  INPUT/OUTPUT are bound to the keyboard/display and
        occupy no command-line position.
        """
        from ..ast_nodes import Identifier
        params = list(getattr(unit, 'params', None) or [])
        # INPUT/OUTPUT are bound to the keyboard/display and occupy no
        # command-line position; if every heading parameter is one of those (or
        # there are none), emit nothing -- programs that take no command-line
        # input keep their previous, runtime-free main.
        bindable = [p for p in params if p.upper() not in {'INPUT', 'OUTPUT'}]
        if not bindable:
            return
        i32 = ir.IntType(32)
        argc, argv = self.current_function.args[0], self.current_function.args[1]
        self.builder.call(self.runtime_extern('pas_args_init'), [argc, argv])

        position = 0  # command-line position among bindable parameters
        for pname in params:
            if pname.upper() in {'INPUT', 'OUTPUT'}:
                continue  # not set from the command line; not positional
            sym = self.scope.lookup(pname) or self.scope.lookup(pname.upper())
            if sym is not None and getattr(sym, 'llvm_value', None) is not None:
                name_ptr = self._emit_cstring_ptr(pname)
                self.builder.call(self.runtime_extern('pas_arg_begin'),
                                  [ir.Constant(i32, position), name_ptr])
                resolved = self.resolve_type_alias(sym.type_expr)
                if isinstance(resolved, FileType):
                    self._bind_file_parameter(sym)
                else:
                    self._emit_read_target(Identifier(pname), None)
                # Consume the rest of the line. On the command-line token stream
                # this is harmless (the stream is discarded next); on the
                # keyboard-prompt fallback it advances past the just-typed line
                # so the next parameter reads cleanly.
                self.builder.call(self._read_helper('pas_readln_skip', ir.VoidType()), [])
                self.builder.call(self.runtime_extern('pas_arg_end'), [])
            position += 1

    def _bind_file_parameter(self, sym) -> None:
        """Bind a FILE program parameter's filename from the command line.

        Reads the filename token as an LSTRING (reusing the ordinary reader
        under the active stdin redirect), then ASSIGNs it to the file's control
        block so a later RESET/REWRITE opens it -- the canonical vintage use of
        a file program parameter.
        """
        i8 = ir.IntType(8)
        i32 = ir.IntType(32)
        zero = ir.Constant(i32, 0)
        cap = 255
        buf = self.builder.alloca(ir.ArrayType(i8, cap + 1), name='arg_filename')
        buf_i8 = self.builder.bitcast(buf, i8.as_pointer())
        self.builder.call(self._read_helper('pas_read_lstring', i8.as_pointer(), [i32]),
                          [buf_i8, ir.Constant(i32, cap)])
        # LSTRING layout: byte 0 is the length, bytes 1.. are the characters.
        length = self.builder.zext(self.builder.load(buf_i8), i32)
        name_ptr = self.builder.bitcast(
            self.builder.gep(buf, [zero, ir.Constant(i32, 1)]), i8.as_pointer())
        handle = self.builder.load(sym.llvm_value)
        fcb = self.builder.bitcast(handle, self.file_fcb_type().as_pointer())
        self.builder.call(self.runtime_extern('pas_file_assign'), [fcb, name_ptr, length])

    def codegen_module(self, unit: ModuleUnit) -> ir.Module:
        """Codegen for MODULE unit."""
        # A DEVICE MODULE lowers against the device triple, with address spaces
        # live; a plain MODULE keeps the host triple and is byte-identical to
        # before (ads-memory-spaces-design.md S1.2).
        with self._device_codegen_context(getattr(unit, 'is_device', False)):
            for decl in unit.decls:
                self.codegen_decl(decl)
        self._emit_launch_registry()
        return self.module

    def codegen_interface(self, unit: InterfaceUnit) -> ir.Module:
        """Codegen for INTERFACE unit (declarations only)."""
        with self._device_codegen_context(getattr(unit, 'is_device', False)):
            for decl in unit.decls:
                self.codegen_decl(decl)
        return self.module

    def codegen_implementation(self, unit: ImplementationUnit) -> ir.Module:
        """Codegen for IMPLEMENTATION unit."""
        old_iface = self.current_interface_decls
        self.current_interface_decls = {getattr(decl, 'name', '').lower(): decl for decl in (unit.interface.decls if unit.interface else []) if getattr(decl, 'name', None)}
        try:
            with self._device_codegen_context(getattr(unit, 'is_device', False)):
                # Seed TYPE and CONST aliases from the interface so the
                # implementation can reference them without restating.
                # Only seed names the implementation does not itself declare
                # (impl wins when both define the same name), mirroring the
                # identical logic in type_checker.py::check_implementation_unit.
                if unit.interface:
                    impl_type_names  = {getattr(d, 'name', '').upper() for d in (unit.decls or []) if isinstance(d, TypeDecl)}
                    impl_const_names = {getattr(d, 'name', '').upper() for d in (unit.decls or []) if isinstance(d, ConstDecl)}
                    for decl in unit.interface.decls:
                        name = getattr(decl, 'name', '') or ''
                        if isinstance(decl, TypeDecl) and name.upper() not in impl_type_names:
                            self.codegen_type_decl(decl)
                        elif isinstance(decl, ConstDecl) and name.upper() not in impl_const_names:
                            self.codegen_const_decl(decl)

                for decl in unit.decls:
                    self.codegen_decl(decl)

                # Codegen init body if present
                if unit.init_body:
                    init_type = ir.FunctionType(ir.IntType(32), [])
                    init_name = f'pascal_init_{unit.name.lower()}'
                    init_func = ir.Function(self.module, init_type, name=init_name)
                    entry_block = init_func.append_basic_block(name='entry')
                    self.builder = IRBuilder(entry_block)
                    self.current_function = init_func

                    prev_labels = self.setup_function_labels(unit.init_body)
                    self.codegen_stmt_list(unit.init_body)
                    self.label_blocks = prev_labels

                    if not self.builder.block.is_terminated:
                        self.builder.ret(ir.Constant(ir.IntType(32), 0))
        finally:
            self.current_interface_decls = old_iface

        return self.module

    # ========================================================================
    # Declarations
    # ========================================================================

    def codegen_use_clause(self, use_clause: UseClause, local_interfaces=None) -> None:
        """Import declarations from a USES module as external symbols."""
        ast = None
        if local_interfaces:
            ast = next(
                (i for i in local_interfaces if i.name.upper() == use_clause.name.upper()),
                None,
            )
        if ast is None:
            raise CodegenError(f"Module '{use_clause.name}' must be provided by a spliced INTERFACE header in the source file")

        # Build the exported routines in export order. For an INTERFACE UNIT the
        # export order is the unit's export list (UNIT G (BJUMP, WJUMP)); for a
        # MODULE/IMPLEMENTATION it is declaration order. This mirrors the type
        # checker's import_symbols pairing so a renaming USES binds the local
        # alias to the right exported symbol.
        #
        # For InterfaceUnit we match by name rather than by positional zip so
        # that non-routine decls (TYPE, CONST) in the interface body do not
        # corrupt the pairing when they precede the exported procedures.
        all_iface_decls = list(getattr(ast, 'decls', []))
        if isinstance(ast, InterfaceUnit):
            export_name_list = list(getattr(ast, 'params', []))
            routine_by_name  = {getattr(d, 'name', '').lower(): d
                                for d in all_iface_decls
                                if isinstance(d, (ProcDecl, FuncDecl))}
            export_routines  = [(n, routine_by_name[n.lower()])
                                for n in export_name_list
                                if n.lower() in routine_by_name]
            # Also seed TYPE/CONST decls into the importing module's type_aliases
            # so the caller can reference shared buffer types by name.
            for decl in all_iface_decls:
                if isinstance(decl, TypeDecl) and getattr(decl, 'name', None):
                    if decl.name.upper() not in self.type_aliases:
                        self.codegen_type_decl(decl)
                elif isinstance(decl, ConstDecl) and getattr(decl, 'name', None):
                    if decl.name.upper() not in self.constants:
                        self.codegen_const_decl(decl)
        else:
            export_routines = [(d.name, d) for d in all_iface_decls if isinstance(d, (ProcDecl, FuncDecl)) and getattr(d, 'name', None)]

        # A renaming USES (e.g. `USES GRAPHICS (MOVE, PLOT)`) binds the imports
        # positionally onto the exports; a plain USES imports each under its own
        # name.
        if use_clause.imports:
            aliases = list(use_clause.imports)
            pairs = [(aliases[i], export_routines[i][1]) for i in range(min(len(aliases), len(export_routines)))]
        else:
            pairs = list(export_routines)

        is_device_iface = bool(getattr(ast, 'is_device', False))
        for alias, decl in pairs:
            exported = decl.name
            # The external LLVM function keeps the REAL exported name so it
            # resolves against the separately-compiled IMPLEMENTATION's symbol.
            #
            # For a DEVICE unit the declaration must be lowered in *device*
            # context so its parameter ABI matches the kernel definition (which
            # was compiled as device code).  Otherwise an `ADS(GLOBAL) OF T`
            # parameter would lower here to the host segmented `{ptr, i16}` pair
            # while the kernel itself takes a flat/addrspace pointer -- a silent
            # ABI mismatch that hands the kernel a garbage buffer pointer.  On
            # the CPU device (device=x86) this yields the flat addrspace(0)
            # pointer the kernel expects; a direct LAUNCH call then matches.
            with self._device_codegen_context(is_device_iface):
                if isinstance(decl, ProcDecl):
                    self.codegen_decl(ProcDecl(exported, decl.params, getattr(decl, 'attributes', []), body=None))
                else:
                    self.codegen_decl(FuncDecl(exported, decl.params, decl.return_type, getattr(decl, 'attributes', []), body=None))
            # Bind the call-site alias (MOVE) to that same function (@BJUMP).
            if alias and alias.lower() != exported.lower():
                sym = self.scope.lookup(exported)
                if sym is not None:
                    self.scope.define(alias, sym.llvm_value, None)
                    self.proc_param_modes[alias.lower()] = self.proc_param_modes.get(exported.lower(), [])

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
            self.codegen_value_decl(decl)
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

    def codegen_type_decl(self, decl: TypeDecl) -> None:
        """Record a type declaration for later codegen lookups."""
        self.type_aliases[decl.name.upper()] = decl.type_expr
        # Enum members become ordinal compile-time constants (0, 1, 2, ...).
        if isinstance(decl.type_expr, EnumType):
            for ordinal, member in enumerate(decl.type_expr.values):
                self.constants[member.upper()] = ordinal
                # Remember which enum each member belongs to so WRITE can print
                # the symbolic name of a bare member literal.
                self.enum_member_names[member.upper()] = list(decl.type_expr.values)

    def _decode_string_literal(self, expr: StringLiteral) -> str:
        text = expr.value
        if text.startswith("'") and text.endswith("'"):
            text = text[1:-1]
        return text.replace("''", "'")

    def _value_initializer_constant(self, expr: Expression, type_expr: Type) -> ir.Constant:
        """Build an LLVM constant for a VALUE-section initializer."""
        llvm_type = self.llvm_type(type_expr)
        is_str, max_len, is_lstring = self.get_string_type_info(type_expr)
        if isinstance(self.resolve_type_alias(type_expr), SetType) and isinstance(expr, SetConstructor):
            init = self.codegen_set_constructor(expr)
            if isinstance(init, ir.Constant):
                return init
            raise CodegenError('VALUE set initializer must be constant')
        if is_str and isinstance(expr, StringLiteral):
            data = self._decode_string_literal(expr).encode('latin1')
            if is_lstring:
                raw = bytearray(max_len + 1)
                raw[0] = len(data)
                raw[1:1 + len(data)] = data
                return ir.Constant(llvm_type, raw)
            raw = bytearray(data)
            if len(raw) < max_len:
                raw.extend(b' ' * (max_len - len(raw)))
            return ir.Constant(llvm_type, raw[:max_len])

        value = self.eval_const_expr(expr)
        if isinstance(llvm_type, ir.IntType):
            return ir.Constant(llvm_type, int(value))
        if isinstance(llvm_type, (ir.FloatType, ir.DoubleType)):
            return ir.Constant(llvm_type, float(value))
        raise CodegenError(f'Unsupported VALUE initializer for {type(type_expr).__name__}')

    def _explicit_zero_initializer(self, type_expr: Type) -> ir.Constant:
        """Produce an inspectable zero initializer for aggregate patching."""
        resolved = self.resolve_type_alias(type_expr)
        # Use the original type expression's LLVM type, not the alias-unwrapped
        # one: a named record lowers to an identified struct, and its
        # initializer constant must carry that identified type, not the literal
        # struct that the unwrapped AST record would produce.
        llvm_type = self.llvm_type(type_expr)
        if isinstance(resolved, RecordType):
            parts: List[ir.Constant] = []
            for names, ftype in resolved.fields:
                for _ in names:
                    parts.append(self._explicit_zero_initializer(ftype))
            return ir.Constant(llvm_type, parts)
        if isinstance(resolved, ArrayType):
            elem = self._explicit_zero_initializer(resolved.element_type)
            return ir.Constant(llvm_type, [elem for _ in range(llvm_type.count)])
        return self.zero_initializer(llvm_type)

    def _replace_record_initializer_field(self, aggregate: ir.Constant, type_expr: Type, selectors: List[Selector], value: ir.Constant) -> ir.Constant:
        """Return ``aggregate`` with a record FIELD selector path replaced."""
        if not selectors:
            return value
        selector = selectors[0]
        if selector.kind != 'FIELD':
            raise CodegenError('VALUE section supports only variables and record-field selectors')
        fidx, ftype = self.record_field_index(type_expr, str(selector.index_or_field))
        if fidx is None or ftype is None:
            raise CodegenError(f"Record has no field '{selector.index_or_field}'")
        llvm_type = self.llvm_type(type_expr)
        parts = list(getattr(aggregate, 'constant', []) or [])
        if not parts:
            parts = list(self._explicit_zero_initializer(type_expr).constant)
        if fidx >= len(parts):
            raise CodegenError(f"Record field '{selector.index_or_field}' has invalid initializer index")
        if len(selectors) == 1:
            parts[fidx] = value
        else:
            parts[fidx] = self._replace_record_initializer_field(parts[fidx], ftype, selectors[1:], value)
        return ir.Constant(llvm_type, parts)

    def codegen_value_decl(self, decl: ValueDecl) -> None:
        """Apply a VALUE-section initializer to static/global storage."""
        sym = self.scope.lookup(decl.name)
        if not sym:
            raise CodegenError(f'Undefined variable in VALUE section: {decl.name}')
        slot = sym.llvm_value
        if not isinstance(slot, ir.GlobalVariable):
            raise CodegenError(f'VALUE initializer for non-static variable not supported: {decl.name}')

        target_type_expr = sym.type_expr
        for selector in decl.target.selectors:
            if selector.kind != 'FIELD':
                raise CodegenError('VALUE section supports only variables and record-field selectors')
            _fidx, target_type_expr = self.record_field_index(target_type_expr, str(selector.index_or_field))
            if target_type_expr is None:
                raise CodegenError(f"Record has no field '{selector.index_or_field}'")

        init = self._value_initializer_constant(decl.value, target_type_expr)
        if not decl.target.selectors:
            slot.initializer = init
            return

        current = slot.initializer if slot.initializer is not None else self._explicit_zero_initializer(sym.type_expr)
        slot.initializer = self._replace_record_initializer_field(current, sym.type_expr, decl.target.selectors, init)

    def _initck_sentinel(self, decl: VarDecl, llvm_type) -> Optional[ir.Constant]:
        """$INITCK sentinel constant for a scalar variable, or None.

        Manual: "set the value of all uninitialized integers to -32768 and
        uninitialized pointers to 1 (if $NILCK is on)" (default -).  Use the
        signed minimum for each INTEGER-family width.  Per the manual,
        VALUE-section variables, record variant fields, and super-array
        components are not covered; this implementation initializes scalar
        INTEGERs and pointers (the two classes the manual names) and leaves
        aggregates to their existing zero/blank initialization.
        """

        def flag(name: str) -> bool:
            if name in self.force_flags:
                return self.force_flags[name]
            meta = getattr(decl, 'meta_flags', None)
            if meta is not None and name in meta:
                return meta[name]
            from ..lexer import _ON_OFF_FLAGS
            return _ON_OFF_FLAGS.get(name, True)

        if not flag('INITCK'):
            return None
        resolved = self.resolve_type_alias(decl.type_expr)
        if isinstance(llvm_type, ir.IntType) and llvm_type.width in (16, 32, 64) \
                and not isinstance(resolved, EnumType):
            return ir.Constant(llvm_type, -(1 << (llvm_type.width - 1)))
        if isinstance(llvm_type, ir.PointerType) and flag('NILCK'):
            return ir.Constant(ir.IntType(64), 1).inttoptr(llvm_type)
        return None

    def codegen_var_decl(self, decl: VarDecl) -> None:
        """Codegen for VAR declaration."""
        llvm_type = self.llvm_type(decl.type_expr)
        attrs = {attr.name.upper() for attr in getattr(decl, 'attributes', [])}
        is_static = 'STATIC' in attrs

        # Residence address space (DEVICE MODULE [SPACE(s)]). A non-HOST device
        # space makes the variable statically-allocated storage in that space
        # (like CUDA __shared__/__constant__/__device__) -- NOT a stack alloca,
        # even inside a routine. HOST/default and the x86 CPU-device => 0.
        residence_as = 0
        if self.is_device_module:
            for attr in getattr(decl, 'attributes', []):
                if attr.name.upper() == 'SPACE' and getattr(attr, 'arg', None) is not None:
                    residence_as = self._space_addrspace(self.eval_const_expr(attr.arg))

        # Check if the type is a string type
        is_str, max_len, is_lstring = self.get_string_type_info(decl.type_expr)
        initck_const = self._initck_sentinel(decl, llvm_type)

        if self.builder and not is_static and residence_as == 0:
            # Local variable (inside a function) — allocate the aggregate inline
            for name in decl.names:
                alloca = self.builder.alloca(llvm_type, name=name)
                self.scope.define(name, alloca, decl.type_expr)
                if initck_const is not None:
                    self.builder.store(initck_const, alloca)
                if isinstance(decl.type_expr, FileType) or (isinstance(decl.type_expr, NamedType) and decl.type_expr.name.upper() == 'TEXT'):
                    self._init_file_storage(alloca, decl.type_expr)

                if is_str:
                    # Initialize length byte to 0 for LSTRING
                    if is_lstring:
                        zero = ir.Constant(ir.IntType(32), 0)
                        len_ptr = self.builder.gep(alloca, [zero, zero])
                        self.builder.store(ir.Constant(ir.IntType(8), 0), len_ptr)
                    # STRING: no initialization needed (chars are undefined until assigned)
        else:
            # Static / global / device-residence storage. A [SPACE(s)] variable is
            # allocated in its address space (residence_as); this also covers an
            # in-routine device-space local routed here from the branch above.
            prefix = self.current_function.name if self.current_function else 'global'
            for name in decl.names:
                gv_name = name if not self.builder else f'{prefix}.{name}'

                # Create global variable with the aggregate type
                global_var = ir.GlobalVariable(self.module, llvm_type, name=gv_name, addrspace=residence_as)

                if is_str:
                    if is_lstring:
                        # Initialize with zero (length 0, rest undefined)
                        global_var.initializer = ir.Constant(llvm_type, bytearray(llvm_type.count))
                    else:
                        # STRING: initialize with blanks (0x20)
                        init_bytes = bytearray([0x20] * llvm_type.count)
                        global_var.initializer = ir.Constant(llvm_type, init_bytes)
                else:
                    global_var.initializer = initck_const if initck_const is not None else self.zero_initializer(llvm_type)

                self.scope.define(name, global_var, decl.type_expr)

    def _param_device_passable(self, param: Param) -> bool:
        """Whether a kernel-entry parameter can be passed to a device launch
       .  Reference-mode params and host-space pointers lower
        to addrspace-0 pointers a device entry cannot dereference; device data
        must arrive by value (scalars) or as a non-HOST `ADS(space) OF T`.
        """
        if param.mode in {'VAR', 'VARS', 'CONST', 'CONSTS'}:
            return False  # reference params are host-space (addrspace 0) pointers
        t = param.type_expr
        if isinstance(t, PointerType):
            if t.flavor != 'ADS':
                return False  # plain ^T heap / ADR: host-space pointer
            if t.space is None:
                return False  # ADS with unspecified space == HOST
            try:
                space_ord = self.eval_const_expr(t.space)
            except Exception:
                return True  # unfoldable: don't block a compile the checker passed
            return bool(space_ord)  # 0 (HOST) -> not passable; GLOBAL/CONSTANT/... -> ok
        return True  # value scalar / array / record

    def _is_kernel_entry(self, decl: Union[ProcDecl, FuncDecl]) -> bool:
        """True when `decl` lowers to a real launchable GPU `.entry`.

        Matches the gate in `_apply_kernel_entry`: a device compiland, a GPU
        device triple, and a routine the interface exports. On x86 (CPU-device)
        this is False, so the serial parity path keeps the vintage
        i32-returning procedure shape byte-identical.
        """
        return (self.is_device_module and _is_gpu_triple(self.device_triple) and getattr(decl, 'is_exported_entry', False) and isinstance(decl, ProcDecl))

    def _apply_kernel_entry(self, decl: Union[ProcDecl, FuncDecl], func: ir.Function) -> None:
        """Make an exported device routine a launchable kernel entry
       .

        Fires only when lowering device code to a real GPU triple and the
        routine is flagged `is_exported_entry` by the checker.  In that case the
        entry-shape rules bite (here, where a true `.entry` is formed and the
        triple is known -- the triple-blind checker cannot enforce them without
        rejecting the x86 CPU-device parity ports) and the kernel calling
        convention is set, which is what turns a PTX `.func` into a `.visible
        .entry`.  Inert on host, on x86 CPU-device, and for non-exported
        routines -- so those stay byte-identical and `DEVICE MODULE` (no
        interface, nothing exported) keeps emitting plain device functions.
        """
        if not (self.is_device_module and _is_gpu_triple(self.device_triple)):
            return
        if not getattr(decl, 'is_exported_entry', False):
            return
        # A GPU entry cannot return a value -- it must be a PROCEDURE.
        if isinstance(decl, FuncDecl):
            raise CodegenError(f"exported device routine '{decl.name}' must be a PROCEDURE to be a kernel entry: "
                               f"a GPU entry cannot return a value (return results via an ADS(GLOBAL) parameter)")
        for param in decl.params:
            if not self._param_device_passable(param):
                raise CodegenError(f"kernel entry '{decl.name}' has a non-device-passable parameter: pass device "
                                   f"data by value or as ADS(GLOBAL)/ADS(CONSTANT) OF T, not a host-space pointer")
        func.calling_convention = 'amdgpu_kernel' if self.device_triple.startswith('amdgcn') else 'ptx_kernel'
        # Tighten pointer-parameter alignment to the element type's natural
        # alignment.  Without this the NVPTX backend annotates every pointer
        # param `.ptr .global .align 1`; the element type is known (e.g. an
        # `int*` into `INTEGER32` data is genuinely 4-byte aligned), so the
        # tighter hint is both correct and what `nvcc` emits.  Only the LLVM
        # pointee type is consulted, so this works uniformly for `ADS(s) OF T`
        # pointers regardless of address space.  Inert for scalar params.
        # (followups.md item 2: conservative pointer alignment.)
        for arg in func.args:
            if isinstance(arg.type, ir.PointerType):
                arg.attributes.align = self.natural_alignment(arg.type.pointee)

    @staticmethod
    def _c_abi_sign_attr(type_expr) -> Optional[str]:
        """Return 'signext' or 'zeroext' for sub-32-bit Pascal scalar types (Phase 4).

        Only the directly named Pascal built-in and C-alias types are recognised;
        user-defined aliases that happen to resolve to a narrow type get no
        attribute (safe: the caller will either sign- or zero-extend anyway, and
        the worst case is a latent bug only with negative values on sub-32-bit
        returns, the same status quo as before Phase 4).

        Signed narrow types (C char / short):  'signext'
          INTEGER (i16), CHAR (i8), CCHAR (i8 alias), CSHORT (i16 alias)
        Unsigned/boolean narrow types:         'zeroext'
          WORD (i16), BOOLEAN (i8)
        All 32-bit-and-wider types:            None  (no attribute needed)
        """
        if type_expr is None:
            return None
        name = getattr(type_expr, 'name', None)
        if name is None:
            return None
        n = name.upper()
        if n in {'INTEGER', 'INTEGER16', 'CHAR', 'CCHAR', 'CSHORT'}:
            return 'signext'
        if n in {'WORD', 'WORD16', 'BOOLEAN'}:
            return 'zeroext'
        return None

    def _codegen_c_abi_decl(self, decl, return_llvm) -> None:
        """Lower a foreign ``[C]`` routine declaration with C-ABI-correct
        signature (byval/sret/register coercion/signext/zeroext/void). Phases 2-4.

        ``return_llvm`` is the LLVM return type for functions, or None for
        procedures.  The routine is always body-less (EXTERN), so this only emits
        the `declare`, records the call plan, and registers the symbol/modes the
        call sites need.

        Phase 4 additions:
        - Sub-32-bit scalar parameters carry signext/zeroext on the declare and
          the call site, closing the latent dirty-bit gap for i8/i16 types.
        - [C] EXTERN procedures are declared as void-returning rather than the
          internal i32 convention, so the declaration exactly matches a C `void`.
        - BOOLEAN (i8) is tagged zeroext; CHAR/INTEGER get signext.
        """
        flat_param_types = []
        flat_modes = []
        flat_sign_attrs = []
        for param in decl.params:
            pt = self.param_llvm_type(param)
            sa = self._c_abi_sign_attr(param.type_expr)
            for _ in param.names:
                flat_param_types.append(pt)
                flat_modes.append(param.mode)
                flat_sign_attrs.append(sa)

        decl_attrs = {a.name.upper() for a in getattr(decl, 'attributes', [])}
        is_variadic = 'VARARGS' in decl_attrs

        # Phase 4: determine sign attr for the return type.
        ret_type_expr = getattr(decl, 'return_type', None)
        ret_sign_attr = self._c_abi_sign_attr(ret_type_expr)

        ir_args, ir_ret, _sret, arg_attrs, plan = self.build_c_abi_plan(
            decl, flat_param_types, flat_modes, return_llvm, is_variadic=is_variadic,
            flat_sign_attrs=flat_sign_attrs, ret_sign_attr=ret_sign_attr)

        func_type = ir.FunctionType(ir_ret, ir_args, var_arg=is_variadic)
        func = ir.Function(self.module, func_type, name=decl.name)
        func.linkage = 'external'
        for idx, (names, align) in arg_attrs.items():
            dst = func.args[idx].attributes
            for a in names:
                dst.add(a)
            if align is not None:
                dst.align = align

        # Phase 4: attach the return sign/zero-extension attribute on the declare.
        if plan.ret_sign_attr:
            func.return_value.attributes.add(plan.ret_sign_attr)

        self.proc_param_modes[decl.name.lower()] = flat_modes
        self.c_abi_plans[decl.name.lower()] = plan
        self.scope.define(decl.name, func, getattr(decl, 'return_type', None))

    def codegen_proc_decl(self, decl: ProcDecl) -> None:
        """Codegen for PROCEDURE declaration."""
        if self.is_c_abi_foreign(decl):
            self._codegen_c_abi_decl(decl, None)
            return
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
        # A launchable GPU kernel entry must return void: the host launcher
        # (cuLaunchKernel) provides no return slot, so an i32-returning entry is
        # an ABI mismatch. Everywhere else, procedures keep the vintage
        # i32-returning shape (a harmless internal convention).
        kernel_entry = self._is_kernel_entry(decl)
        ret_ll = ir.VoidType() if kernel_entry else ir.IntType(32)
        func_type = ir.FunctionType(ret_ll, param_types)

        attrs = {attr.name.upper() for attr in getattr(decl, 'attributes', [])}
        existing = self.scope.lookup(decl.name)
        if existing and isinstance(existing.llvm_value, ir.Function):
            func = existing.llvm_value
            if func.function_type != func_type:
                raise CodegenError(f"Procedure '{decl.name}' already declared with a different signature")
        else:
            # Create function
            func = ir.Function(self.module, func_type, name=decl.name)
        # Directive ('extern') and attributes ([PUBLIC]) both request external linkage.
        # Previously the eager extern dump masked a missing `directive` check here:
        # pre-registered externs already had linkage='external', so the condition
        # being False was harmless.  Fixed now so source-level `; extern;` declarations
        # always emit `declare external` IR regardless of pre-registration.
        _directive = getattr(decl, 'directive', '') or ''
        if attrs.intersection({'PUBLIC', 'EXTERN', 'EXTERNAL'}) or _directive.upper() in ('EXTERN', 'EXTERNAL', 'PUBLIC'):
            func.linkage = 'external'
        self._apply_kernel_entry(decl, func)
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

        prev_labels = self.setup_function_labels(decl.body.body)
        self.codegen_stmt_list(decl.body.body)
        self.label_blocks = prev_labels

        # Default return
        if not self.builder.block.is_terminated:
            if isinstance(ret_ll, ir.VoidType):
                self.builder.ret_void()
            else:
                self.builder.ret(ir.Constant(ir.IntType(32), 0))

        # Restore context
        self.builder = prev_builder
        self.current_function = prev_func
        self.scope = prev_scope

    def codegen_func_decl(self, decl: FuncDecl) -> None:
        """Codegen for FUNCTION declaration."""
        if self.is_c_abi_foreign(decl):
            self._codegen_c_abi_decl(decl, self.llvm_type(decl.return_type))
            return
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
        attrs = {attr.name.upper() for attr in getattr(decl, 'attributes', [])}
        _directive = getattr(decl, 'directive', '') or ''
        if attrs.intersection({'PUBLIC', 'EXTERN', 'EXTERNAL'}) or _directive.upper() in ('EXTERN', 'EXTERNAL', 'PUBLIC'):
            func.linkage = 'external'
        self._apply_kernel_entry(decl, func)
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
        self.builder.store(ir.Constant(return_type, 0.0) if isinstance(return_type, (ir.FloatType, ir.DoubleType)) else ir.Constant(return_type, 0), return_alloca)

        # Codegen body
        for inner_decl in decl.body.decls:
            self.codegen_decl(inner_decl)

        prev_labels = self.setup_function_labels(decl.body.body)
        self.codegen_stmt_list(decl.body.body)
        self.label_blocks = prev_labels

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
