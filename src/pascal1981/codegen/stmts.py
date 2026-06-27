"""
STMTS mixin for Codegen.

Statement code generation

"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple, Union

import llvmlite.ir as ir
from llvmlite.ir import IRBuilder

from ..ast_nodes import *
from ..builtins_registry import DEVICE_SYNC_BUILTIN_PROCEDURES, DEVICE_INDEX_BUILTIN_FUNCTIONS
from .base import CodegenError, LoopContext, Scope


class StmtsMixin:
    """Mixin for stmts functionality."""

    def codegen_stmt_list(self, stmts: List[Statement]) -> None:
        for stmt in stmts:
            if self.builder.block.is_terminated:
                # The previous statement ended the block (RETURN/BREAK/CYCLE/
                # GOTO).  With no labels in scope nothing downstream can be a
                # jump target, so the rest of the list is unreachable and we
                # stop -- preserving the original fast path.  When labels do
                # exist, a following labeled statement may still be reached by
                # a GOTO, so we open a fresh (possibly dead) block for it to
                # land in; LLVM discards it if nothing branches there.
                if not self.label_blocks:
                    break
                cont = self.current_function.append_basic_block(self.unique_name('cont'))
                self.builder.position_at_end(cont)
            self.codegen_stmt(stmt)

    def effective_flag(self, flag: str, stmt: Statement) -> bool:
        """Return the effective boolean value of a runtime-check flag.

        Priority order:
          0. device-code suppression of host-trapping checks (the RANGECK
             CASE-no-match trap and string-capacity guard lower to a host
             fflush+abort that does not exist on device).
          1. CLI force_flags (explicit --flag on/off override),
          2. the full metacommand state stamped onto the AST node by the
             parser (stmt.meta_flags),
          3. the legacy per-flag attribute (e.g. stmt.rangeck),
          4. the manual's documented default for the flag (NOT a blanket
             True — e.g. ENTRY and INITCK default off).
        """
        if self._device_checks_suppressed(flag):
            return False
        if flag in self.force_flags:
            return self.force_flags[flag]
        meta = getattr(stmt, 'meta_flags', None)
        if meta is not None and flag in meta:
            return meta[flag]
        attr = getattr(stmt, flag.lower(), None)
        if attr is not None:
            return attr
        from ..lexer import _ON_OFF_FLAGS
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
            self.codegen_goto_stmt(stmt)
        elif isinstance(stmt, ReturnStmt):
            self.codegen_return_stmt(stmt)
        elif isinstance(stmt, BreakStmt):
            self.codegen_break_stmt(stmt)
        elif isinstance(stmt, CycleStmt):
            self.codegen_cycle_stmt(stmt)
        elif isinstance(stmt, WithStmt):
            self.codegen_with_stmt(stmt)
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

        # Can't assign to parameters themselves (passed by value), but assigning
        # through a pointer parameter designator such as p^[i] is a store to the
        # pointee, not a rebinding of the parameter value.
        if symbol.is_parameter and not stmt.target.selectors:
            raise CodegenError(f'Cannot assign to parameter: {target_name}')

        # Check if the target is a string type
        is_str, max_len, is_dest_lstring = self.get_string_type_info(symbol.type_expr)

        # Resolve the pointer (handles array indexing, etc.)
        ptr = self.resolve_designator_ptr(stmt.target)
        value = self.codegen_expr(stmt.expr)

        # Handle simple type conversions
        if not is_str and hasattr(ptr.type, 'pointee'):
            value = self._coerce_assign_value(value, ptr.type.pointee, stmt.expr)

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
            pointee = getattr(ptr.type, 'pointee', None)
            if pointee is not None and value.type != pointee \
                    and isinstance(value.type, ir.BaseStructType):
                # Whole-record copy where the source and destination structs are
                # the same layout but not the same LLVM type identity -- e.g. two
                # distinct named records that are structurally equivalent, now
                # lowered as separate identified structs. Copy by layout via a
                # destination-pointer bitcast rather than by nominal type.
                ptr = self.builder.bitcast(ptr, value.type.as_pointer())
            self.builder.store(value, ptr)

    def _coerce_assign_value(self, value: ir.Value, target_type: ir.Type, expr: Expression) -> ir.Value:
        """Coerce a lowered RHS value to a scalar/pointer assignment target.

        Shared by :meth:`codegen_assign_stmt` and the IF/ELSE-of-assignment
        `select` peephole so the two arms of a select are coerced through the
        same int/float/pointer widening/narrowing path before they are merged.
        Returns ``value`` unchanged for types with no applicable conversion.
        """
        if isinstance(target_type, ir.IntType) and isinstance(value.type, ir.IntType):
            if target_type.width < value.type.width:
                return self.builder.trunc(value, target_type)
            elif target_type.width > value.type.width:
                return self._extend_int_for_pascal_expr(value, target_type, expr)
        elif isinstance(target_type, (ir.FloatType, ir.DoubleType)) and isinstance(value.type, ir.IntType):
            return self.builder.sitofp(value, target_type)
        elif isinstance(target_type, ir.IntType) and isinstance(value.type, (ir.FloatType, ir.DoubleType)):
            return self.builder.fptosi(value, target_type)
        elif isinstance(target_type, ir.DoubleType) and isinstance(value.type, ir.FloatType):
            # REAL32 value into a REAL slot: widen f32 -> f64.
            return self.builder.fpext(value, target_type)
        elif isinstance(target_type, ir.FloatType) and isinstance(value.type, ir.DoubleType):
            # REAL value into a REAL32 slot: narrow f64 -> f32 (e.g. a bare
            # ``0.0`` literal stored into a REAL32 variable).
            return self.builder.fptrunc(value, target_type)
        elif isinstance(target_type, ir.PointerType) and isinstance(value.type, ir.PointerType):
            if isinstance(expr, NilLiteral):
                return ir.Constant(target_type, None)
            elif value.type != target_type:
                return self.builder.bitcast(value, target_type)
        return value

    def _unwrap_single(self, stmt: Statement) -> Statement:
        """Unwrap a single-statement BEGIN/END (CompoundStmt of length 1)."""
        if isinstance(stmt, CompoundStmt) and len(stmt.stmts) == 1:
            return stmt.stmts[0]
        return stmt

    def _select_rhs_is_safe(self, expr: Expression) -> bool:
        """Whether an RHS expression is safe to evaluate *unconditionally*.

        The IF/ELSE-of-assignment select peephole evaluates both arms' RHS
        regardless of the condition, so each RHS must be free of side effects
        (function calls) and free of operations that could trap or fire a
        runtime check on the not-taken arm: division (``$MATHCK`` divide-by-zero
        or an illegal quotient), integer ``+``/``-``/``*`` (``$MATHCK`` overflow
        guard), array indexing (``$INDEXCK``), and pointer dereference
        (``$NILCK``).  Pure literal/variable reads are always safe.

        Checks that lower to a host trap are suppressed in device code, so in
        the device-only context where this peephole actually runs the integer
        ``$MATHCK`` arm is moot; the explicit ``check_enabled`` test keeps the
        predicate correct on its own terms, so the transform stays sound even if
        it is ever re-enabled on a path where those checks are live.
        """
        if expr is None:
            return True
        if isinstance(expr, FuncCall):
            return False
        if isinstance(expr, BinOp):
            if expr.op in ('SLASH', 'DIV', 'MOD'):
                return False
            if expr.op in ('PLUS', 'MINUS', 'MUL') and self.check_enabled('MATHCK'):
                # Integer add/sub/mul lower through the $MATHCK overflow guard,
                # which traps on the not-taken arm if speculatively evaluated.
                # (Float arithmetic does not trap, but the AST does not carry a
                # lowered type here, so bail conservatively when the check is
                # live; device code, where the peephole runs, has it suppressed.)
                return False
            return self._select_rhs_is_safe(expr.left) and self._select_rhs_is_safe(expr.right)
        if isinstance(expr, UnaryOp):
            return self._select_rhs_is_safe(expr.operand)
        if isinstance(expr, Designator):
            # Bare variable read only; indexed/dereferenced reads could fire
            # INDEXCK/NILCK or read out-of-bounds on the not-taken arm.
            return len(expr.selectors) == 0
        if isinstance(expr, Identifier):
            key = expr.name.upper()
            if key in self.constants:
                return True
            if key in DEVICE_INDEX_BUILTIN_FUNCTIONS:
                return True  # pure special-register read
            if key in {'EOF', 'EOLN', 'NULL'}:
                return False  # EOF/EOLN call runtime; NULL is handled via type
            sym = self.scope.lookup(expr.name) or self.scope.lookup(key)
            if sym is not None and isinstance(getattr(sym, 'llvm_value', None), ir.Function):
                return False  # parameterless function call (may have side effects)
            return True  # plain variable / parameter read
        # literals (Int/Real/Char/String/Bool/Nil) are safe
        return True

    def _try_select_if(self, stmt: IfStmt) -> bool:
        """Lower ``IF c THEN x:=a ELSE x:=b`` to a branchless ``select``.

        Returns True if lowered, False to fall back to branch lowering.
        """
        if not stmt.else_branch:
            return False
        then_s = self._unwrap_single(stmt.then_branch)
        else_s = self._unwrap_single(stmt.else_branch)
        if not (isinstance(then_s, AssignStmt) and isinstance(else_s, AssignStmt)):
            return False
        # Same simple-variable target (no selectors): the designator must
        # resolve to a pure pointer with no index/deref checks of its own.
        if then_s.target != else_s.target or then_s.target.selectors:
            return False
        if not (self._select_rhs_is_safe(then_s.expr) and self._select_rhs_is_safe(else_s.expr)):
            return False
        symbol = self.scope.lookup(then_s.target.name) or self.scope.lookup(then_s.target.name.upper())
        if not symbol:
            return False
        is_str, _max_len, _is_lstr = self.get_string_type_info(symbol.type_expr)
        if is_str:
            return False
        ptr = self.resolve_designator_ptr(then_s.target)
        if not hasattr(ptr.type, 'pointee'):
            return False
        target_type = ptr.type.pointee
        # Scalar/pointer targets only: merging aggregates via select is unusual
        # and the aggregate assign path does whole-record bitcasts we skip here.
        if not isinstance(target_type, (ir.IntType, ir.FloatType, ir.DoubleType, ir.PointerType)):
            return False
        # Evaluate condition first (source order), then both arms' RHS, coerce
        # each to the target type, merge via select, and store once.  Both RHS
        # are pure (verified above), so unconditional evaluation is equivalent.
        cond = self.codegen_expr(stmt.cond)
        cond_bit = self.to_bool(cond)
        then_val = self._coerce_assign_value(self.codegen_expr(then_s.expr), target_type, then_s.expr)
        else_val = self._coerce_assign_value(self.codegen_expr(else_s.expr), target_type, else_s.expr)
        if then_val.type != else_val.type:
            return False  # defensive: coercion should already align these
        merged = self.builder.select(cond_bit, then_val, else_val)
        self.builder.store(merged, ptr)
        return True

    def codegen_device_sync_builtin(self, name: str) -> None:
        """Lower DEVICE synchronization builtins.

        For CPU-device execution the host is the device and execution is serial,
        so SYNCTHREADS is a real host implementation: a no-op because there are
        no sibling lanes to wait for.  GPU targets lower to backend barriers.
        """
        upper = name.upper()
        if upper != 'SYNCTHREADS':
            raise CodegenError(f'Unknown device synchronization builtin: {name}')
        triple = self.device_triple or ''
        if not (triple.startswith('nvptx') or triple.startswith('amdgcn')):
            return
        intrinsic_name = 'llvm.nvvm.barrier0' if triple.startswith('nvptx') else 'llvm.amdgcn.s.barrier'
        try:
            fn = self.module.get_global(intrinsic_name)
        except KeyError:
            fn = ir.Function(self.module, ir.FunctionType(ir.VoidType(), []), name=intrinsic_name)
        self.builder.call(fn, [])

    def _orch_i8ptr(self, arg: Expression) -> ir.Value:
        """Lower an orchestration address argument to a flat ``i8*``.

        Handles a held ADRMEM handle (a loaded ``i8*``), an ``ADR buf`` address
        (a typed pointer to host storage), or any other pointer/segmented-address
        value -- ``coerce_arg`` performs the pointer bitcast (and seg->flat
        collapse) into ``i8*``.
        """
        i8p = ir.IntType(8).as_pointer()
        return self.coerce_arg(self.codegen_expr(arg), i8p)

    def _kernel_cstring(self, text: str) -> ir.Value:
        """A module-global, NUL-terminated C string; returns an ``i8*`` to it."""
        data = bytearray(text.encode('utf-8') + b'\0')
        const = ir.Constant(ir.ArrayType(ir.IntType(8), len(data)), data)
        gv = ir.GlobalVariable(self.module, const.type, name=self.unique_name('kname'))
        gv.global_constant = True
        gv.initializer = const
        zero = ir.Constant(ir.IntType(32), 0)
        return self.builder.gep(gv, [zero, zero])

    def _kernel_launch_thunk(self, fn: ir.Function) -> ir.Function:
        """Return (creating once per kernel) the host dispatch thunk.

        The thunk ``void __pas_klaunch_<name>(i8** argv)`` unpacks ``argv`` into
        ``fn``'s parameter types and calls ``fn``.  This is the CPU-device launch
        dispatch: ``pas_dev_launch`` invokes it as a single-thread grid, so a
        grid-stride kernel still covers the whole buffer.  On a GPU the shim
        dispatches the kernel by name out of the loaded module and the thunk is
        never called -- but it is harmless to emit, and LAUNCH only ever appears
        in host code (never a device compiland), so the thunk never collides with
        a ``ptx_kernel`` calling convention.

        ``argv`` mirrors ``cuLaunchKernel``'s ``void**``: each slot points at a
        storage cell holding one argument value (a scalar by value, or a device
        handle by its opaque pointer value).
        """
        thunk_name = '__pas_klaunch_' + fn.name
        cache = self._launch_thunks
        existing = cache.get(thunk_name)
        if existing is not None:
            return existing
        i8p = ir.IntType(8).as_pointer()
        i8pp = i8p.as_pointer()
        thunk = ir.Function(self.module, ir.FunctionType(ir.VoidType(), [i8pp]), name=thunk_name)
        thunk.linkage = 'internal'
        argv = thunk.args[0]
        argv.name = 'argv'
        b = ir.IRBuilder(thunk.append_basic_block('entry'))
        call_args = []
        for i, pty in enumerate(fn.function_type.args):
            slot_pp = b.gep(argv, [ir.Constant(ir.IntType(32), i)], inbounds=True)
            slot = b.load(slot_pp)                       # i8* -> the cell holding arg i
            typed = b.bitcast(slot, pty.as_pointer())
            call_args.append(b.load(typed))
        b.call(fn, call_args)
        b.ret_void()
        cache[thunk_name] = thunk
        return thunk

    def _codegen_device_orchestration(self, name: str, args: list) -> None:
        """Lower DEVCOPYTO / DEVCOPYFROM / DEVFREE / LAUNCH (Milestone D).

        DEV* lower to the orchestration-shim externs (``pas_dev_copy_to`` /
        ``pas_dev_copy_from`` / ``pas_dev_free``).  LAUNCH lowers to a real
        launch ABI: it marshals the kernel arguments into a ``void**`` array (the
        shape ``cuLaunchKernel`` consumes) and calls ``pas_dev_launch`` with the
        kernel-name string, a per-kernel dispatch thunk, the six geometry values,
        and that array.  On the CPU device ``pas_dev_launch`` runs the thunk
        (single-thread grid); swapping the shim for the CUDA driver path reuses
        this exact call site -- it dispatches by name and ignores the thunk -- so
        no codegen change is needed to run the same program on a GPU (§5.2/§5.4).

        Launch geometry is 2 values (grid, block -> a 1-D launch) or 6 values
        (gx,gy,gz, bx,by,bz); the count is implied by the kernel's arity.
        """
        if name == 'DEVCOPYTO':
            dev = self._orch_i8ptr(args[0])
            src = self._orch_i8ptr(args[1])
            nbytes = self._to_i64(self.codegen_expr(args[2]))
            self.builder.call(self.runtime_extern('pas_dev_copy_to'), [dev, src, nbytes])
            return
        if name == 'DEVCOPYFROM':
            dst = self._orch_i8ptr(args[0])
            dev = self._orch_i8ptr(args[1])
            nbytes = self._to_i64(self.codegen_expr(args[2]))
            self.builder.call(self.runtime_extern('pas_dev_copy_from'), [dst, dev, nbytes])
            return
        if name == 'DEVFREE':
            self.builder.call(self.runtime_extern('pas_dev_free'), [self._orch_i8ptr(args[0])])
            return

        # LAUNCH(kernel, <geometry>, kernel actuals...)
        if len(args) < 1:
            raise CodegenError('LAUNCH expects at least a kernel name')
        kernel = args[0]
        kernel_name = getattr(kernel, 'name', None)
        if kernel_name is None:
            raise CodegenError('LAUNCH first argument must name a kernel')
        symbol = self.scope.lookup(kernel_name) or self.scope.lookup(kernel_name.upper())
        if not symbol or not isinstance(getattr(symbol, 'llvm_value', None), ir.Function):
            raise CodegenError(f"LAUNCH cannot resolve kernel '{kernel_name}'")
        fn = symbol.llvm_value
        param_types = fn.function_type.args
        expected = len(param_types)

        # Split the flat argument list using the kernel's arity: the trailing
        # `expected` args are the kernel actuals; everything between the kernel
        # name and them is launch geometry (2 or 6 integer values).
        split = len(args) - expected
        geometry = args[1:split]
        kernel_actuals = args[split:] if expected else []
        gvals = [self._to_i64(self.codegen_expr(g)) for g in geometry]
        i64 = ir.IntType(64)
        one = ir.Constant(i64, 1)
        if len(gvals) == 2:           # 1-D: grid, block
            gx, bx = gvals
            geom6 = [gx, one, one, bx, one, one]
        elif len(gvals) == 6:         # gx,gy,gz, bx,by,bz
            geom6 = gvals
        else:
            raise CodegenError(
                f"LAUNCH of '{kernel_name}' expects 2 (grid, block) or 6 geometry "
                f"values before its {expected} argument(s), got {len(geometry)}")

        # Marshal the kernel actuals into a void** argument array: argv[i] points
        # at a storage cell holding actual i, coerced to the kernel's parameter
        # ABI (the same coercion the former direct call used).
        i8p = ir.IntType(8).as_pointer()
        i32 = ir.IntType(32)
        if kernel_actuals:
            argv = self.builder.alloca(ir.ArrayType(i8p, len(kernel_actuals)), name='launch_argv')
            for i, actual in enumerate(kernel_actuals):
                v = self.coerce_arg(self.codegen_expr(actual), param_types[i])
                cell = self.builder.alloca(param_types[i], name=f'launch_arg{i}')
                self.builder.store(v, cell)
                slot = self.builder.gep(argv, [i32(0), i32(i)], inbounds=True)
                self.builder.store(self.builder.bitcast(cell, i8p), slot)
            argv_ptr = self.builder.gep(argv, [i32(0), i32(0)], inbounds=True)
        else:
            argv_ptr = ir.Constant(i8p.as_pointer(), None)

        name_str = self._kernel_cstring(fn.name)
        # Record this kernel in the per-compiland registry and emit its dispatch
        # thunk (the CPU-device "entry").  Resolve the entry by name through the
        # module, mirroring cuModuleLoadData + cuModuleGetFunction, then launch
        # the resolved entry.  On the CPU device load returns the registry,
        # get_function returns the thunk, and launch calls it; on the GPU the
        # same three calls become cuModuleLoadData(ptx) / cuModuleGetFunction /
        # cuLaunchKernel, with no change here.
        # On the CPU-device backend the launch is dispatched in-process through a
        # per-kernel thunk recorded in this compiland's registry; that thunk
        # statically references the kernel symbol, which is what forces the
        # separate host-ABI device compile (dev.ll) at link time.  On the CUDA
        # backend the kernel is the loaded PTX module and the shim dispatches it
        # by name, so we emit neither thunk nor registry -- the host .ll then has
        # no undefined kernel symbol and needs no dev.ll.
        if self.device_backend != 'cuda':
            self._record_launched_kernel(fn.name, self._kernel_launch_thunk(fn))
        module = self.builder.call(
            self.runtime_extern('pas_dev_module_load'),
            [self._launch_registry_ptr(), self._device_ptx_ptr()])
        entry = self.builder.call(
            self.runtime_extern('pas_dev_module_get_function'), [module, name_str])
        self.builder.call(self.runtime_extern('pas_dev_launch'),
                          [entry] + geom6 + [argv_ptr])

    # ---- launch registry (CPU stand-in for a loaded CUDA module) -----------

    def _record_launched_kernel(self, name: str, thunk: ir.Function) -> None:
        """Note a kernel launched by this compiland (deduped by name)."""
        if not any(n == name for n, _ in self._launched_kernels):
            self._launched_kernels.append((name, thunk))

    def _launch_registry_ptr(self) -> ir.Value:
        """An ``i8*`` to this compiland's kernel registry global.

        The global is created (shell, no initializer) on first reference and
        filled in by ``_emit_launch_registry`` at finalize, once every LAUNCH
        has recorded its kernel.
        """
        i8p = ir.IntType(8).as_pointer()
        i64 = ir.IntType(64)
        # CUDA backend: there is no in-process registry (the kernel is the loaded
        # PTX module and the shim ignores this argument), so pass a null pointer
        # rather than referencing an external registry global that nothing
        # defines -- which would otherwise be an undefined symbol at link.
        if self.device_backend == 'cuda':
            return ir.Constant(i8p, None)
        if self._launch_registry_gv is None:
            reg_ty = ir.LiteralStructType([i8p.as_pointer(), i8p.as_pointer(), i64])
            self._launch_registry_gv = ir.GlobalVariable(
                self.module, reg_ty, name='__pas_klaunch_registry')
            self._launch_registry_gv.global_constant = True
        return self.builder.bitcast(self._launch_registry_gv, i8p)

    def _device_ptx_ptr(self) -> ir.Value:
        """An ``i8*`` to the embedded device-PTX blob (NUL-terminated).

        The blob is the companion device unit's emitted PTX, embedded so the
        launch path is self-contained and the GPU swap is a pure runtime change
        (the CUDA shim ``cuModuleLoadData``s this blob).  When no PTX was
        supplied at compile time, an empty blob is embedded -- the mechanism is
        always present; the CPU device never executes it.
        """
        i8 = ir.IntType(8)
        i8p = i8.as_pointer()
        zero = ir.Constant(ir.IntType(32), 0)
        if self._device_ptx_gv is None:
            if self.device_backend == 'cuda' and not self._embed_device_ptx_text:
                # CUDA backend, decoupled packaging: the PTX blob is its own
                # object (built from the .ptx at link time), referenced here as
                # an external `const char __pas_device_ptx[]`.  The host .ll no
                # longer needs the kernel text baked in, so host compile does not
                # depend on the device artifact.
                gv = ir.GlobalVariable(self.module, ir.ArrayType(i8, 0),
                                       name='__pas_device_ptx')
                gv.global_constant = True
                gv.linkage = 'external'
                self._device_ptx_gv = gv
                return self.builder.bitcast(gv, i8p)
            text = self._embed_device_ptx_text or ''
            data = bytearray(text.encode('utf-8') + b'\0')
            const = ir.Constant(ir.ArrayType(i8, len(data)), data)
            gv = ir.GlobalVariable(self.module, const.type, name='__pas_device_ptx')
            gv.global_constant = True
            gv.initializer = const
            self._device_ptx_gv = gv
        if isinstance(self._device_ptx_gv.type.pointee, ir.ArrayType) and \
                self._device_ptx_gv.type.pointee.count == 0:
            return self.builder.bitcast(self._device_ptx_gv, i8p)
        return self.builder.gep(self._device_ptx_gv, [zero, zero])

    def _emit_launch_registry(self) -> None:
        """Fill the kernel registry global from the launched-kernel list.

        Builds a names table and an entries (thunk) table and points the
        registry struct ``{ i8** names; i8** entries; i64 count }`` at them.
        Called at the end of host PROGRAM/MODULE codegen.  A no-op when the
        compiland performed no launches, so launch-free host IR is byte-identical
        to before.
        """
        if self._launch_registry_gv is None or not self._launched_kernels:
            return
        i8 = ir.IntType(8)
        i8p = i8.as_pointer()
        i64 = ir.IntType(64)
        i32 = ir.IntType(32)
        zero = ir.Constant(i32, 0)
        name_ptrs = []
        entry_ptrs = []
        for kname, thunk in self._launched_kernels:
            data = bytearray(kname.encode('utf-8') + b'\0')
            nm = ir.GlobalVariable(self.module, ir.ArrayType(i8, len(data)),
                                   name=self.unique_name('kregname'))
            nm.global_constant = True
            nm.initializer = ir.Constant(ir.ArrayType(i8, len(data)), data)
            name_ptrs.append(nm.gep([zero, zero]))
            entry_ptrs.append(thunk.bitcast(i8p))
        count = len(self._launched_kernels)
        names_arr = ir.GlobalVariable(self.module, ir.ArrayType(i8p, count),
                                      name=self.unique_name('kregnames'))
        names_arr.global_constant = True
        names_arr.initializer = ir.Constant(ir.ArrayType(i8p, count), name_ptrs)
        ents_arr = ir.GlobalVariable(self.module, ir.ArrayType(i8p, count),
                                     name=self.unique_name('kregentries'))
        ents_arr.global_constant = True
        ents_arr.initializer = ir.Constant(ir.ArrayType(i8p, count), entry_ptrs)
        reg_ty = self._launch_registry_gv.type.pointee
        self._launch_registry_gv.initializer = ir.Constant(reg_ty, [
            names_arr.gep([zero, zero]),
            ents_arr.gep([zero, zero]),
            ir.Constant(i64, count),
        ])

    def codegen_proc_call_stmt(self, stmt: ProcCallStmt) -> None:
        """Codegen for procedure call statement."""
        lookup_name = stmt.name.upper()
        if self.is_device_module and lookup_name in DEVICE_SYNC_BUILTIN_PROCEDURES:
            if stmt.args:
                raise CodegenError(f'{lookup_name} expects 0 arguments')
            self.codegen_device_sync_builtin(lookup_name)
            return
        if self.is_device_module and lookup_name in {'FILLSC', 'MOVESL', 'MOVESR'}:
            # Inside a DEVICE MODULE these lower to addrspace-aware copy/
            # fill loops across the operands' concrete spaces -- not the vintage
            # extern call with the {ptr, i16} segmented ABI (which host code keeps).
            self._device_seg_bridge(lookup_name, stmt.args)
            return
        if lookup_name in {'DEVCOPYTO', 'DEVCOPYFROM', 'DEVFREE', 'LAUNCH'}:
            # Host device-orchestration (Milestone D). Host-only; the type
            # checker has already rejected any use inside DEVICE code.
            self._codegen_device_orchestration(lookup_name, stmt.args)
            return
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
            elif lookup_name == 'FILLC':
                self.builtin_fillc(stmt.args)
            elif lookup_name == 'FILLSC':
                self.builtin_fillsc(stmt.args)
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
        """Codegen for IF statement.

        A narrow ``IF c THEN x := a ELSE x := b`` on a *scalar* ``x`` with pure,
        side-effect-free, non-faulting RHS lowers to a branchless LLVM
        ``select`` (PTX ``selp``) instead of a real branch diamond.  This is the
        preferred GPU idiom because it avoids warp divergence at image edges;
        `nvcc` predicates the same pattern.  The peephole is conservative: it
        bails to the branch lowering on anything ambiguous (aggregate/string
        targets, function calls in the RHS, dividing ops that could trap,
        indexed/dereferenced reads that could fault or fire INDEXCK/NILCK, or
        targets whose designator itself carries selectors).  (followups.md
        item 2: branch vs predication on the bounds guard.)

        The transform is **device-only**.  Its safety argument ("both arms are
        pure, so evaluating the not-taken arm unconditionally is equivalent")
        holds only where the host-trapping runtime checks are suppressed -- and
        that is exactly device code (see ``_HOST_TRAPPING_CHECKS``).  On the
        host path, integer ``+``/``-``/``*`` lower through the ``$MATHCK``
        overflow guard (on by default), so speculatively evaluating an
        overflowing not-taken arm would fire an abort the source-level branch
        never reaches.  Gating on ``is_device_module`` keeps host lowering
        byte-for-byte unchanged; ``_select_rhs_is_safe`` additionally refuses
        any arm whose evaluation could trap under a live check, as defense in
        depth should an on-device trap ever be introduced.
        """
        if self.is_device_module and self._try_select_if(stmt):
            return
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
            start_val = self.builder.trunc(start_val, loop_var.type.pointee) if start_val.type.width > loop_var.type.pointee.width else self._extend_int_for_pascal_expr(
                start_val, loop_var.type.pointee, stmt.start)
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
            end_val = self.builder.trunc(end_val, current_val.type) if end_val.type.width > current_val.type.width else self._extend_int_for_pascal_expr(
                end_val, current_val.type, stmt.end)
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
        ret_t = self.current_function.function_type.return_type
        if isinstance(ret_t, ir.VoidType):
            self.builder.ret_void()
        else:
            self.builder.ret(ir.Constant(ir.IntType(32), 0))

    def codegen_break_stmt(self, stmt: BreakStmt) -> None:
        ctx = self.resolve_loop_context(stmt.label)
        self.builder.branch(ctx.break_block)

    def codegen_cycle_stmt(self, stmt: CycleStmt) -> None:
        ctx = self.resolve_loop_context(stmt.label)
        self.builder.branch(ctx.cycle_block)

    def codegen_goto_stmt(self, stmt: GotoStmt) -> None:
        """Lower a GOTO: an unconditional branch to the block that begins the
        target label.  All label blocks in the routine were pre-created before
        the body was lowered, so forward and backward targets resolve alike.

        The branch terminates the current block.  Statements that textually
        follow are only reachable via another label; codegen_stmt_list opens a
        fresh block for them as needed."""
        target = self.normalize_label(stmt.label)
        block = self.label_blocks.get(target)
        if block is None:
            raise CodegenError(f'GOTO to undefined label: {stmt.label}')
        self.builder.branch(block)

    def codegen_label_stmt(self, stmt: LabelStmt) -> None:
        target = self.normalize_label(stmt.label)
        block = self.label_blocks.get(target)
        if block is not None:
            # Fall through into the label block from the preceding straight-line
            # code, then continue lowering there.  This makes the label a join
            # point for both ordinary fall-through and any GOTO that targets it.
            if not self.builder.block.is_terminated:
                self.builder.branch(block)
            self.builder.position_at_end(block)
        inner = stmt.stmt
        if isinstance(inner, (WhileStmt, ForStmt, RepeatStmt)):
            # A labeled loop is also a BREAK/CYCLE target; keep that wiring.
            setattr(inner, 'label', target)
        self.codegen_stmt(inner)

    def _collect_labels(self, node: Any) -> List[Union[int, str]]:
        """Return the (normalized) ids of every label defined via a labeled
        statement reachable within ``node`` (a statement or list of them),
        recursing through the compound/control-flow statements that can nest
        further statements.  Used to pre-create label blocks for a routine."""
        found: List[Union[int, str]] = []

        def walk(s: Any) -> None:
            if s is None:
                return
            if isinstance(s, LabelStmt):
                found.append(self.normalize_label(s.label))
                walk(s.stmt)
            elif isinstance(s, CompoundStmt):
                for x in s.stmts:
                    walk(x)
            elif isinstance(s, IfStmt):
                walk(s.then_branch)
                walk(s.else_branch)
            elif isinstance(s, (ForStmt, WhileStmt, WithStmt)):
                walk(s.body)
            elif isinstance(s, RepeatStmt):
                for x in s.body:
                    walk(x)
            elif isinstance(s, CaseStmt):
                for el in s.elements:
                    walk(el.stmt)
                walk(s.otherwise)

        if isinstance(node, list):
            for s in node:
                walk(s)
        else:
            walk(node)
        return found

    def setup_function_labels(self, body: Any) -> Dict[Union[int, str], ir.Block]:
        """Install a fresh per-routine label scope for ``self.current_function``,
        pre-creating one LLVM block per label declared in ``body``.  Labels are
        block-local in Pascal, so each routine starts with an empty map (no
        non-local GOTO into an enclosing routine).  Returns the previous map so
        the caller can restore it once the body is lowered."""
        prev = self.label_blocks
        self.label_blocks = {}
        for lid in self._collect_labels(body):
            if lid not in self.label_blocks:
                self.label_blocks[lid] = self.current_function.append_basic_block(name=self.unique_name(f'label_{lid}'))
        return prev

    def normalize_label(self, label: Optional[Union[int, str]]) -> Optional[Union[int, str]]:
        if isinstance(label, str):
            return label.lower()
        return label

    def codegen_with_stmt(self, stmt: WithStmt) -> None:
        """Lower a WITH statement.

        Each target record is evaluated to a pointer exactly once, then its
        fields are bound as bare names (one GEP per field) in a freshly pushed
        scope so the body can reference them without qualification. Multiple
        comma-separated targets nest left-to-right, so on a field-name clash
        the rightmost target shadows -- matching the nested-WITH equivalence
        in the grammar. Scopes are restored on exit (including on error).
        """
        prev_scope = self.scope
        try:
            for target in stmt.targets:
                base_ptr, base_type = self.resolve_designator_ptr_typed(target)
                rec = self.resolve_type_alias(base_type) if base_type is not None else None
                if not isinstance(rec, RecordType):
                    raise CodegenError(f"WITH target is not a record: {target.name}")
                self.scope = Scope(parent=self.scope)
                for names, _ftype in rec.fields:
                    for nm in names:
                        fidx, fast = self.record_field_index(base_type, nm)
                        if fidx is None:
                            continue
                        fptr = self.builder.gep(
                            base_ptr,
                            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), fidx)],
                        )
                        self.scope.define(nm, fptr, fast)
            self.codegen_stmt(stmt.body)
        finally:
            self.scope = prev_scope

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
