"""
STMTS mixin for Codegen.

Statement code generation

Part of Plan 1 refactoring (mixin-based architecture).
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple, Union

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder

from ast_nodes import *

from .base import CodegenError, LoopContext


class StmtsMixin:
    """Mixin for stmts functionality."""

    def codegen_stmt_list(self, stmts: List[Statement]) -> None:
        for stmt in stmts:
            if self.builder.block.is_terminated:
                break
            self.codegen_stmt(stmt)

    def effective_flag(self, flag: str, stmt: Statement) -> bool:
        """Return the effective boolean value of a runtime-check flag.

        Priority order:
          1. CLI force_flags (explicit --flag on/off override),
          2. the full metacommand state stamped onto the AST node by the
             parser (stmt.meta_flags),
          3. the legacy per-flag attribute (e.g. stmt.rangeck),
          4. the manual's documented default for the flag (NOT a blanket
             True — e.g. ENTRY and INITCK default off).
        """
        if flag in self.force_flags:
            return self.force_flags[flag]
        meta = getattr(stmt, 'meta_flags', None)
        if meta is not None and flag in meta:
            return meta[flag]
        attr = getattr(stmt, flag.lower(), None)
        if attr is not None:
            return attr
        from lexer import _ON_OFF_FLAGS
        return _ON_OFF_FLAGS.get(flag, True)

    def effective_rangeck(self, stmt: Statement) -> bool:
        """Convenience wrapper for the RANGECK flag."""
        return self.effective_flag('RANGECK', stmt)

    def codegen_stmt(self, stmt: Statement) -> None:
        """Codegen a statement."""
        # Track the metacommand flag state for expression-level checks
        # (INDEXCK, MATHCK, NILCK).  Statements that don't carry meta_flags
        # (compound/control-flow wrappers) inherit the last state seen, which
        # matches lexical flag scoping for straight-line code.
        meta = getattr(stmt, 'meta_flags', None)
        if meta is not None:
            self._stmt_meta = meta
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
                    value = self._extend_int_for_pascal_expr(value, target_type, stmt.expr)
            elif isinstance(target_type, ir.DoubleType) and isinstance(value.type, ir.IntType):
                value = self.builder.sitofp(value, target_type)
            elif isinstance(target_type, ir.IntType) and isinstance(value.type, ir.DoubleType):
                value = self.builder.fptosi(value, target_type)
            elif isinstance(target_type, ir.PointerType) and isinstance(value.type, ir.PointerType):
                if isinstance(stmt.expr, NilLiteral):
                    value = ir.Constant(target_type, None)
                elif value.type != target_type:
                    value = self.builder.bitcast(value, target_type)

        rangeck_enabled = self.effective_rangeck(stmt)

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

                end_block = self._guard_string_capacity(src_len, max_len, 'str_assign', enabled=rangeck_enabled)
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

                if end_block is not None:
                    self.builder.branch(end_block)
                    self.builder.position_at_end(end_block)
        else:
            self.builder.store(value, ptr)

    def codegen_proc_call_stmt(self, stmt: ProcCallStmt) -> None:
        """Codegen for procedure call statement."""
        lookup_name = stmt.name.upper()
        symbol = self.scope.lookup(lookup_name) or self.scope.lookup(stmt.name)
        if not symbol or symbol.llvm_value is None:
            # Try built-in procedures
            if lookup_name == 'WRITELN':
                self.builtin_writeln(stmt.args)
            elif lookup_name == 'WRITE':
                self.builtin_write(stmt.args)
            elif lookup_name == 'READ':
                self.builtin_read(stmt.args)
            elif lookup_name == 'READLN':
                self.builtin_readln(stmt.args)
            elif lookup_name == 'READSET':
                self.builtin_readset(stmt.args)
            elif lookup_name == 'READFN':
                self.builtin_readfn(stmt.args)
            elif lookup_name == 'CONCAT':
                self.builtin_concat(stmt.args, enabled=self.effective_rangeck(stmt))
            elif lookup_name == 'COPYLST':
                self.builtin_copylst(stmt.args, enabled=self.effective_rangeck(stmt))
            elif lookup_name == 'COPYSTR':
                self.builtin_copystr(stmt.args, enabled=self.effective_rangeck(stmt))
            elif lookup_name == 'INSERT':
                self.builtin_insert(stmt.args, enabled=self.effective_rangeck(stmt))
            elif lookup_name == 'DELETE':
                self.builtin_delete(stmt.args)
            elif lookup_name == 'POSITN':
                self.codegen_expr_func_call(stmt)  # function call path
                return
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
            elif lookup_name == 'RESET':
                self.builtin_reset(stmt.args)
            elif lookup_name == 'REWRITE':
                self.builtin_rewrite(stmt.args)
            elif lookup_name == 'GET':
                self.builtin_get(stmt.args)
            elif lookup_name == 'PUT':
                self.builtin_put(stmt.args)
            elif lookup_name == 'ASSIGN':
                self.builtin_assign(stmt.args)
            elif lookup_name == 'CLOSE':
                self.builtin_close(stmt.args)
            elif lookup_name == 'DISCARD':
                self.builtin_discard(stmt.args)
            elif lookup_name == 'NEW':
                self.builtin_new(stmt.args)
            elif lookup_name == 'DISPOSE':
                self.builtin_dispose(stmt.args)
            elif lookup_name == 'ABORT':
                self.builtin_abort(stmt.args)
            else:
                raise CodegenError(f'Undefined procedure: {stmt.name}')
        else:
            # User-defined procedure, or a predeclared built-in with a
            # placeholder symbol entry but no llvm_value.
            if symbol.llvm_value is None:
                raise CodegenError(f'Undefined procedure: {stmt.name}')
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
            loop_var = self.builder.alloca(ir.IntType(16), name=stmt.var)
            self.scope.define(stmt.var, loop_var, BuiltinType('INTEGER'))
        else:
            loop_var = symbol.llvm_value

        # Initialize loop variable
        start_val = self.codegen_expr(stmt.start)
        if isinstance(start_val.type, ir.IntType) and start_val.type != loop_var.type.pointee:
            start_val = self.builder.trunc(start_val, loop_var.type.pointee) if start_val.type.width > loop_var.type.pointee.width else self._extend_int_for_pascal_expr(start_val, loop_var.type.pointee, stmt.start)
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
        if isinstance(end_val.type, ir.IntType) and end_val.type != current_val.type:
            end_val = self.builder.trunc(end_val, current_val.type) if end_val.type.width > current_val.type.width else self._extend_int_for_pascal_expr(end_val, current_val.type, stmt.end)
        cond = self.builder.icmp_signed('<=', current_val, end_val) if stmt.direction == 'TO' else self.builder.icmp_signed('>=', current_val, end_val)
        self.builder.cbranch(cond, body_block, end_block)

        self.builder.position_at_end(body_block)
        self.codegen_stmt(stmt.body)
        if not self.builder.block.is_terminated:
            self.builder.branch(step_block)

        self.builder.position_at_end(step_block)
        current_val = self.builder.load(loop_var)
        one = ir.Constant(current_val.type, 1)
        next_val = self.builder.add(current_val, one) if stmt.direction == 'TO' else self.builder.sub(current_val, one)
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

    def _emit_case_no_match_trap(self) -> None:
        """Abort the current path for a checked CASE no-match."""
        self.emit_runtime_abort()
        self.builder.unreachable()

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

        # Otherwise / no-match branch.  IBM Pascal traps on a no-match CASE
        # with no OTHERWISE when RANGECK is enabled; with RANGECK disabled the
        # historical behavior is unchecked, so preserve silent fall-through.
        if stmt.otherwise:
            self.codegen_stmt(stmt.otherwise)
            if not self.builder.block.is_terminated:
                self.builder.branch(end_block)
        elif self.effective_rangeck(stmt):
            self._emit_case_no_match_trap()
        else:
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
