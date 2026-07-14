"""Statement checking: assignment, control flow (IF/FOR/WHILE/REPEAT/CASE/
WITH/RETURN), and procedure-call statements.

Mixin for PascalTypeChecker, split out of type_checker.py as pure code
movement: methods are unchanged and still reach each other through self.
"""


from ..ast_nodes import (
    AssignStmt,
    CaseStmt,
    Designator,
    ForStmt,
    GotoStmt,
    Identifier,
    IfStmt,
    LabelStmt,
    ProcCallStmt,
    RangeExpr,
    RepeatStmt,
    ReturnStmt,
    Statement,
    WhileStmt,
    WithStmt,
    WriteArg,
)
from ..builtins_registry import DEVICE_SYNC_BUILTIN_PROCEDURES
from ..symbol_table import Symbol
from ..type_system import (
    BOOLEAN_TYPE,
    CHAR_TYPE,
    INTEGER32_TYPE,
    INTEGER64_TYPE,
    INTEGER_TYPE,
    WORD_TYPE,
    EnumType,
    FileType,
    ProcedureType,
    RecordType,
    can_assign,
)


class StmtsMixin:

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
            # A labeled statement is GOTO machinery unless the label sits
            # directly on a loop, where it instead names a BREAK/CYCLE target
            # (structured, reducible, GPU-friendly). In device code GOTO is
            # rescinded, so a label on anything *other* than a loop can only be
            # a dead GOTO landing pad -- reject it for consistency with the GOTO
            # ban below. Loop labels stay legal so labeled BREAK/CYCLE keeps
            # working on device. (Matches codegen_label_stmt's loop predicate.)
            if self.in_device_module and not isinstance(stmt.stmt, (ForStmt, WhileStmt, RepeatStmt)):
                self.error("a labeled statement is not available in device code "
                           "unless the label is on a loop (for BREAK/CYCLE)", stmt)
            self.check_statement(stmt.stmt)
        elif isinstance(stmt, WithStmt):
            self.check_with_stmt(stmt)
        elif isinstance(stmt, GotoStmt):
            # Device-code recission: GOTO is rejected in any DEVICE compiland
            # (DEVICE MODULE / DEVICE INTERFACE / DEVICE IMPLEMENTATION -- all
            # set in_device_module via _device_context). SIMT loop-structurizer
            # backends need structured, reducible control flow. This bans ALL
            # goto -- a conservative superset of the stated "nonlocal/irreducible"
            # intent, since the checker has no label-scope table or CFG
            # reducibility analysis to draw a finer line. The companion LabelStmt
            # rule above rejects the non-loop labels such a goto would target, so
            # the two together leave no orphan GOTO machinery on device. Outside
            # a device compiland GOTO keeps its existing (unchecked) behavior.
            if self.in_device_module:
                self.error("GOTO is not available in device code", stmt)

    def check_with_stmt(self, stmt: WithStmt) -> None:
        """Type check a WITH statement.

        Each target must designate a record. Inside the body the record's
        field names become directly visible as bare identifiers, shadowing
        any outer symbol of the same name. With several comma-separated
        targets the rightmost wins on a field-name clash, matching the
        nested-WITH equivalence in the grammar (``WITH a, b DO s`` ==
        ``WITH a DO WITH b DO s``); the later targets are therefore resolved
        with the earlier targets' fields already in scope. One scope is
        pushed per target so the aliases vanish when the WITH ends.
        """
        pushed = 0
        for target in stmt.targets:
            rec_type = self.infer_designator_type(target)
            if not isinstance(rec_type, RecordType):
                if rec_type is not None:
                    self.error(f"WITH target must be a record, got {rec_type}", stmt)
                continue
            base_sym = self.symbol_table.lookup(target.name)
            target_mutable = base_sym.is_mutable if base_sym else True
            self.symbol_table.enter_scope()
            pushed += 1
            for fname, ftype in rec_type.fields.items():
                self.symbol_table.define(
                    fname,
                    Symbol(name=fname, type=ftype, kind='var', is_mutable=target_mutable),
                )
        self.check_statement(stmt.body)
        for _ in range(pushed):
            self.symbol_table.exit_scope()

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

    def _is_ordinal_type(self, t) -> bool:
        """Ordinal types are the ones valid as FOR control variables, CASE
        selectors, and SUCC/PRED/ORD operands: INTEGER, WORD, CHAR, BOOLEAN and
        any enumerated type."""
        return isinstance(t, EnumType) or t in (INTEGER_TYPE, WORD_TYPE, CHAR_TYPE, BOOLEAN_TYPE)

    def _is_integer_type(self, t) -> bool:
        """True for the integer family (INTEGER/WORD and, where enabled, the
        wide INTEGER32/INTEGER64). Used to validate byte-count arguments."""
        return t in (INTEGER_TYPE, WORD_TYPE, INTEGER32_TYPE, INTEGER64_TYPE)

    def _check_unroll_hint(self, stmt, loop_name: str) -> None:
        """Gate and validate a {$UNROLL n} hint attached to a loop statement.

        The hint is extension surface (tuning-hints feature): rejected under the
        faithful vintage dialect, on by default inside DEVICE code (the device
        feature baseline is the extended umbrella), enabled in host code with
        -f tuning-hints.
        """
        unroll = getattr(stmt, 'unroll', None)
        if unroll is None:
            return
        if not self.feature_enabled('tuning-hints'):
            self.error(f"{{$UNROLL}} on {loop_name} is an extension; enable it with -f tuning-hints", stmt)
            return
        if not isinstance(unroll, int) or unroll < 1:
            self.error(f"{{$UNROLL}} count must be a positive integer, got {unroll}", stmt)

    def check_for_stmt(self, stmt: ForStmt) -> None:
        """Type check a FOR statement."""
        self._check_unroll_hint(stmt, 'FOR')
        # The control variable must be an ordinal type (INTEGER, CHAR, BOOLEAN,
        # WORD, or an enum). Enum-controlled loops are valid: enum ordinals are
        # contiguous, so `FOR c := Red TO Blue` iterates the members in order.
        var_type = None
        if stmt.var:
            sym = self.symbol_table.lookup(stmt.var)
            if not sym:
                self.error(f"Undefined variable: {stmt.var}", stmt)
            else:
                var_type = sym.type
                wide_for_ok = (var_type == INTEGER32_TYPE and (self.feature_enabled('wide-integers') or self.in_device_module))
                if not self._is_ordinal_type(var_type) and not wide_for_ok:
                    self.error(f"FOR loop variable must be an ordinal type, got {var_type}", stmt)
                    var_type = None

        # Each bound must be assignment-compatible with the control variable
        # (e.g. enum bounds for an enum control variable).
        for bound, which in ((stmt.start, 'start'), (stmt.end, 'end')):
            if bound is None:
                continue
            # The control variable's type is the literal context, so a bound
            # like 99999 in a FOR over an INTEGER32 variable adopts (and is
            # range-checked against) INTEGER32 rather than defaulting to
            # INTEGER and failing its 16-bit range check.
            bound_type = self.infer_expression_type(bound, var_type)
            if bound_type is None:
                continue
            if var_type is not None:
                if not (can_assign(bound_type, var_type) or can_assign(var_type, bound_type)):
                    self.error(f"FOR {which} bound type {bound_type} is incompatible with loop variable type {var_type}", stmt)
            elif stmt.var is None and not self._is_ordinal_type(bound_type):
                self.error(f"FOR {which} bound must be an ordinal type, got {bound_type}", stmt)

        # Check loop body
        if stmt.body:
            self.check_statement(stmt.body)

    def check_while_stmt(self, stmt: WhileStmt) -> None:
        """Type check a WHILE statement."""
        self._check_unroll_hint(stmt, 'WHILE')
        # Condition must be BOOLEAN
        cond_type = self.infer_expression_type(stmt.cond)
        if cond_type and not cond_type.equivalent_to(BOOLEAN_TYPE):
            self.error(f"WHILE condition must be BOOLEAN, got {cond_type}", stmt)

        # Check body
        if stmt.body:
            self.check_statement(stmt.body)

    def check_repeat_stmt(self, stmt: RepeatStmt) -> None:
        """Type check a REPEAT statement."""
        self._check_unroll_hint(stmt, 'REPEAT')
        # Condition must be BOOLEAN
        cond_type = self.infer_expression_type(stmt.cond)
        if cond_type and not cond_type.equivalent_to(BOOLEAN_TYPE):
            self.error(f"REPEAT..UNTIL condition must be BOOLEAN, got {cond_type}", stmt)

        # Check body
        if stmt.body:
            for s in stmt.body:
                self.check_statement(s)

    def check_case_stmt(self, stmt: CaseStmt) -> None:
        """Type check a CASE statement.

        The selector must be an ordinal value and each case label (or range
        endpoint) must be compatible with it — this is what makes `CASE c OF
        Red: ...` over an enum a checked construct. The check is
        deliberately lenient: it stays silent when a type can't be inferred, and
        accepts compatibility in either direction so INTEGER/WORD/CHAR literal
        labels are not falsely rejected against a related selector type.
        """
        selector_type = self.infer_expression_type(stmt.expr)
        for element in stmt.elements:
            for label in element.constants:
                endpoints = (label.low, label.high) if isinstance(label, RangeExpr) else (label, )
                for endpoint in endpoints:
                    label_type = self.infer_expression_type(endpoint)
                    if selector_type and label_type and not (can_assign(label_type, selector_type) or can_assign(selector_type, label_type)):
                        self.error(f"CASE label type {label_type} is incompatible with selector type {selector_type}", stmt)
            if element.stmt:
                self.check_statement(element.stmt)
        if stmt.otherwise:
            self.check_statement(stmt.otherwise)

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
            elif value_type:
                self._check_word_int_assign(value_type, self.current_function_return_type, stmt.value, stmt)

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
                value_type = self.infer_expression_type(stmt.expr, self.current_function_return_type)
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
                if isinstance(target_type, FileType) and not stmt.target.selectors:
                    self.error("Cannot assign whole file variables; use the file buffer variable (F^) or file I/O procedures", stmt)
                    return
                # Type check successful - target_type is now the element/field type
                value_type = self.infer_expression_type(stmt.expr, target_type)
                if value_type and not can_assign(value_type, target_type) \
                        and not self._const_adapts_to_int_target(value_type, target_type, stmt.expr):
                    self.error(f"Cannot assign {value_type} to {target_type}", stmt)
                elif value_type:
                    self._check_word_int_assign(value_type, target_type, stmt.expr, stmt)
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

        if isinstance(target_type, FileType):
            self.error("Cannot assign whole file variables; use the file buffer variable (F^) or file I/O procedures", stmt)
            return

        # Check type compatibility
        value_type = self.infer_expression_type(stmt.expr, target_type)
        if value_type:
            if not can_assign(value_type, target_type) \
                    and not self._const_adapts_to_int_target(value_type, target_type, stmt.expr):
                self.error(f"Cannot assign {value_type} to {target_type}", stmt)
            else:
                self._check_word_int_assign(value_type, target_type, stmt.expr, stmt)

    def check_proc_call_stmt(self, stmt: ProcCallStmt) -> None:
        """Type check a procedure call statement."""
        if not stmt.name:
            return

        # Look up the procedure (Pascal is case-insensitive)
        lookup_name = stmt.name.upper()
        self._check_device_recission(lookup_name, stmt)
        sym = self.symbol_table.lookup(lookup_name) or self.symbol_table.lookup(stmt.name)
        is_builtin = sym is None or getattr(sym, 'is_builtin', False)

        if is_builtin:
            if lookup_name in DEVICE_SYNC_BUILTIN_PROCEDURES:
                argc = len(stmt.args) if stmt.args else 0
                if not self.in_device_module:
                    self.error(f"{lookup_name} is only available in DEVICE code", stmt)
                    return
                if argc != 0:
                    self.error(f"Procedure '{lookup_name}' expects 0 arguments, got {argc}", stmt)
                    return
                return
            if lookup_name in {'DEVCOPYTO', 'DEVCOPYFROM', 'DEVFREE', 'LAUNCH'}:
                self._check_device_orchestration_args(lookup_name, stmt)
                return
            if lookup_name == 'PACK':
                self._check_pack_args(stmt)
                return
            elif lookup_name == 'UNPACK':
                self._check_unpack_args(stmt)
                return
            elif lookup_name in {'WRITE', 'WRITELN'}:
                self._check_write_args(stmt)
                return
            elif lookup_name in {'READ', 'READLN'}:
                self._check_read_args(stmt, is_readln=(lookup_name == 'READLN'))
                return
            elif lookup_name in {'RESET', 'REWRITE', 'GET', 'PUT', 'CLOSE', 'DISCARD'}:
                self._check_file_primitive_args(stmt, lookup_name)
                return
            elif lookup_name == 'ASSIGN':
                self._check_assign_file_args(stmt)
                return
            elif lookup_name == 'READFN':
                self._check_readfn_args(stmt)
                return
            elif lookup_name == 'READSET':
                self._check_readset_args(stmt)
                return
            elif lookup_name in {'FILLSC', 'MOVESL', 'MOVESR'} and self.in_device_module:
                # Inside a DEVICE MODULE the segmented bridge builtins are
                # the one sanctioned cross-space op (design S5.4) -- their two
                # ADSMEM params may carry *different* concrete spaces, so the
                # equal-space identity rule (which the generic arg check would
                # enforce against the space-less ADSMEM formal) is relaxed here.
                self._check_seg_bridge_args(stmt, lookup_name)
                return

        if not sym:
            self.error(f"Undefined procedure: {stmt.name}", stmt)
            return

        if not isinstance(sym.type, ProcedureType):
            self.error(f"'{stmt.name}' is not a procedure", stmt)
            return

        # Check argument types (including built-in procedures)
        if stmt.args:
            # Special handling for string procedures (section 7.2)
            if is_builtin and stmt.name.upper() == 'CONCAT':
                self._check_concat_args(stmt)
                return
            elif is_builtin and stmt.name.upper() == 'COPYLST':
                self._check_copylst_args(stmt)
                return
            elif is_builtin and stmt.name.upper() == 'COPYSTR':
                self._check_copystr_args(stmt)
                return
            elif is_builtin and stmt.name.upper() == 'INSERT':
                self._check_insert_args(stmt)
                return
            elif is_builtin and stmt.name.upper() == 'DELETE':
                self._check_delete_args(stmt)
                return
            elif is_builtin and stmt.name.upper() == 'POSITN':
                self._check_positn_args(stmt)
                return
            elif is_builtin and stmt.name.upper() == 'NEW':
                self._check_new_args(stmt)
                return
            elif is_builtin and stmt.name.upper() == 'DISPOSE':
                self._check_dispose_args(stmt)
                return

            # For built-in procedures like WRITELN/WRITE/READLN/NEW/DISPOSE,
            # skip count/type checks but still check that the arguments are valid
            # expressions (e.g., defined variables)
            if not is_builtin or stmt.name.upper() not in ['WRITELN', 'WRITE', 'READLN', 'NEW', 'DISPOSE']:
                # For user-defined procedures, check argument count.
                # Variadic ([VARARGS]) routines accept any number of args >= the
                # fixed parameter count.
                expected_args = len(sym.type.params)
                actual_args = len(stmt.args)
                _is_variadic_proc = getattr(sym.type, 'is_variadic', False)
                if _is_variadic_proc:
                    if actual_args < expected_args:
                        self.error(f"Procedure '{stmt.name}' expects at least {expected_args} arguments, got {actual_args}", stmt)
                        return
                elif actual_args != expected_args:
                    self.error(f"Procedure '{stmt.name}' expects {expected_args} arguments, got {actual_args}", stmt)
                    return

            # Check that all arguments are well-formed (this will catch undefined variables)
            for i, arg in enumerate(stmt.args):
                value_arg = arg.expr if isinstance(arg, WriteArg) else arg
                # Give integer literals the parameter's type as context, so a
                # constant argument to a narrow (WORD8/INTEGER8) or wide
                # parameter adopts that type directly (with its range check)
                # instead of defaulting to INTEGER and failing compatibility.
                _param_ctx = None
                if (stmt.name.upper() not in ['WRITELN', 'WRITE', 'READLN'] and hasattr(sym.type, 'params') and i < len(sym.type.params)):
                    _param_ctx = sym.type.params[i][1]
                arg_type = self.infer_expression_type(value_arg, _param_ctx)
                if isinstance(arg, WriteArg):
                    if arg.width is not None:
                        self.infer_expression_type(arg.width)
                    if arg.precision is not None:
                        self.infer_expression_type(arg.precision)
                # If it's a user-defined procedure, also check type compatibility
                if stmt.name.upper() not in ['WRITELN', 'WRITE', 'READLN'] and arg_type:
                    if i < len(sym.type.params):
                        _, param_type = sym.type.params[i]
                        if not self._can_pass_value_argument(arg_type, param_type) \
                                and not self._const_adapts_to_int_target(arg_type, param_type, value_arg):
                            self.error(f"Argument {i+1} type mismatch: expected {param_type}, got {arg_type}", stmt)
                        else:
                            self._check_word_int_assign(arg_type, param_type, value_arg, stmt)
            return
