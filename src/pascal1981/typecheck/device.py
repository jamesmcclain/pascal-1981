"""Device-compiland checks: ADS address spaces, device recissions,
recursion detection, launch-bound attributes, segment bridges, and the
host-side device-orchestration builtins (DEVALLOC/DEVCOPYTO/LAUNCH/...).

Mixin for PascalTypeChecker, split out of type_checker.py as pure code
movement: methods are unchanged and still reach each other through self.
"""

from contextlib import contextmanager
from typing import Optional

from ..ast_nodes import AdrExpr, AdsExpr, Designator, Identifier, ProcCallStmt
from ..device_limits import NVVM_AXIS_MAX, NVVM_MAX_THREADS_PER_BLOCK
from ..type_system import (
    CHAR_TYPE,
    ArrayType,
    EnumType,
    LStringType,
    PointerType,
    ProcedureType,
    RecordType,
    StringType,
)


class DeviceCheckMixin:


    # ---- ADS address-space helpers (ads-memory-spaces-design.md S3-S5) ----

    def _fold_space(self, expr) -> Optional[int]:
        """Fold a SPACE attribute/operand expression to its enum ordinal.

        Accepts a bare member name (e.g. GLOBAL). Returns the ordinal
        (HOST=0..LOCAL=4), or None if it is not a SPACE member.
        """
        name = getattr(expr, 'name', None)
        if name is None:
            return None
        sym = self.symbol_table.lookup(name)
        if sym is None or not isinstance(sym.type, EnumType) or sym.type.name != 'SPACE':
            return None
        try:
            return sym.type.members.index(name.upper())
        except ValueError:
            return None

    def _check_deref_space(self, ptr_type, node) -> None:
        """Enforce the dereferenceability invariant for spaced ADS pointers.

        HOST-space (or unspecified) pointers may be dereferenced only in host
        code; the four device spaces only inside a DEVICE MODULE. Gated to the
        ADS flavor so plain ^/ADR heap pointers are untouched (design S3.3).
        """
        if not isinstance(ptr_type, PointerType) or ptr_type.flavor != 'ADS':
            return
        space = ptr_type.space if ptr_type.space is not None else 0  # default HOST
        if space == 0 and self.in_device_module:
            self.error("cannot dereference a HOST-space pointer inside device code", node)
        elif space != 0 and not self.in_device_module:
            self.error("cannot dereference a device-space pointer outside device code", node)

    # Device-code recissions enforced as module-scoped checker bans inside a
    # DEVICE compiland (design S1.2/S9): constructs that cannot lower to
    # structured, allocation-free, host-I/O-free SIMT code.
    _DEVICE_BANNED_HEAP = {'NEW', 'DISPOSE'}
    _DEVICE_BANNED_IO = {
        'WRITE',
        'WRITELN',
        'READ',
        'READLN',
        'PAGE',
        'RESET',
        'REWRITE',
        'GET',
        'PUT',
        'CLOSE',
        'DISCARD',
        'ASSIGN',
        'READFN',
        'READSET',
    }

    def _check_device_recission(self, name: Optional[str], node) -> None:
        """Reject device-hostile constructs inside device code.

        Checker-enforced recissions include dynamic allocation (NEW/DISPOSE),
        host I/O, recursion (recorded here and flagged at device-compiland end
        by _detect_device_recursion), GOTO, dynamic set ranges, and the DEVICE
        UNIT initializer-block ban handled at unit scope. The construct-shaped
        bans live here/in the checker rather than in feature flags.
        """
        if not self.in_device_module or not name:
            return
        up = name.upper()
        if up in self._DEVICE_BANNED_HEAP:
            self.error(f"dynamic allocation ('{up}') is not available in device code", node)
            return
        if up in self._DEVICE_BANNED_IO:
            self.error(f"host I/O ('{up}') is not available in device code", node)
            return
        current = None
        if self.current_procedure is not None and self.current_procedure.name:
            current = self.current_procedure.name
        elif self.current_function is not None and self.current_function.name:
            current = self.current_function.name
        if current:
            self._device_callgraph.setdefault(current.upper(), []).append((up, node))

    def _detect_device_recursion(self) -> None:
        """Flag direct and mutual recursion among DEVICE MODULE routines.

        Recursion has no place on a device (tiny/absent call stack). Using the
        call graph collected during the body check, report any routine that can
        reach itself through one or more calls.
        """
        graph = self._device_callgraph
        adj = {caller: {callee for callee, _ in edges} for caller, edges in graph.items()}

        def reaches_self(start: str) -> bool:
            seen = set()
            stack = list(adj.get(start, ()))
            while stack:
                n = stack.pop()
                if n == start:
                    return True
                if n not in seen:
                    seen.add(n)
                    stack.extend(adj.get(n, ()))
            return False

        for caller, edges in graph.items():
            if reaches_self(caller):
                node = edges[0][1] if edges else None
                self.error(f"recursion is not available in device code "
                           f"(routine '{caller}' is part of a call cycle)", node)

    @contextmanager
    def _device_context(self, active: bool):
        """Temporarily switch the checker into the device dialect/context."""
        prev_in_device = self.in_device_module
        prev_features = self.features
        prev_callgraph = self._device_callgraph
        if active:
            from ..features import device_features
            self.in_device_module = True
            self.features = device_features()
            self._device_callgraph = {}
        try:
            yield
            if active:
                self._detect_device_recursion()
        finally:
            self.in_device_module = prev_in_device
            self.features = prev_features
            self._device_callgraph = prev_callgraph

    def _check_launch_bound_attrs(self, decl, is_function: bool) -> None:
        """Validate MAXNTID/REQNTID/MINCTASM launch-bound attributes.

        These are extension surface (tuning-hints feature), meaningful only on
        exported device kernel PROCEDUREs; codegen lowers them to NVVM
        annotations. Dimensions must be positive integer literals so the
        annotation values are compile-time facts.
        """
        # The PTX ISA (.reqntid, Performance-Tuning Directives) states that
        # .reqntid cannot be used in conjunction with .maxntid on the same
        # entry, so reject the pair up front rather than emitting PTX that
        # ptxas will refuse. (Follow-up item 12; see docs/old/old-followups.md.)
        present = {a.name.upper() for a in getattr(decl, 'attributes', []) or []}
        if ('MAXNTID' in present and 'REQNTID' in present and self.feature_enabled('tuning-hints') and self.in_device_module):
            self.error("[MAXNTID] and [REQNTID] cannot be used together on the same kernel: the PTX ISA forbids combining .maxntid with .reqntid", decl)
            return
        for attr in getattr(decl, 'attributes', []) or []:
            name = attr.name.upper()
            if name not in {'MAXNTID', 'REQNTID', 'MINCTASM'}:
                continue
            if not self.feature_enabled('tuning-hints'):
                self.error(f"[{name}] is an extension attribute; enable it with -f tuning-hints", decl)
                continue
            if not self.in_device_module:
                self.error(f"[{name}] is only valid in device code", decl)
                continue
            if is_function:
                self.error(f"[{name}] is only valid on PROCEDUREs: a kernel entry cannot be a FUNCTION", decl)
                continue
            if not getattr(decl, 'is_exported_entry', False):
                self.error(f"[{name}] is only meaningful on an exported device kernel procedure; '{decl.name}' is not in the interface export list", decl)
                continue
            args = attr.arg if isinstance(attr.arg, list) else ([attr.arg] if attr.arg is not None else [])
            max_args = 1 if name == 'MINCTASM' else 3
            if not (1 <= len(args) <= max_args):
                self.error(f"[{name}] expects 1{'' if max_args == 1 else '-3'} dimension argument(s), got {len(args)}", decl)
                continue
            values = []
            bad = False
            for arg in args:
                value = self._fold_int_literal_value(arg)
                if value is None or value < 1:
                    self.error(f"[{name}] dimensions must be positive integer literals", decl)
                    bad = True
                    break
                values.append(value)
            if bad:
                continue
            # MAXNTID/REQNTID declare thread-block (CTA) geometry, so their
            # dimensions must fit the CUDA architectural ceilings; otherwise
            # ptxas/the driver rejects the kernel at load time (or, worse, it
            # is silently mis-scheduled). MINCTASM is a *minimum CTAs-per-SM*
            # occupancy hint with no fixed numeric ceiling -- the PTX ISA says
            # an infeasible value is silently ignored by ptxas, not rejected,
            # so we deliberately bound-check only positivity for it (above)
            # and invent no upper limit here.
            if name in {'MAXNTID', 'REQNTID'}:
                axes = ('X', 'Y', 'Z')
                for value, axis in zip(values, axes):
                    axis_max = NVVM_AXIS_MAX[axis]
                    if value > axis_max:
                        self.error(f"[{name}] {axis.lower()}-dimension {value} exceeds the CUDA architectural maximum of {axis_max} threads for that axis", decl)
                        bad = True
                if bad:
                    continue
                total = 1
                for value in values:
                    total *= value
                if total > NVVM_MAX_THREADS_PER_BLOCK:
                    self.error(f"[{name}] total threads per block {total} exceeds the CUDA architectural maximum of {NVVM_MAX_THREADS_PER_BLOCK}", decl)
                    continue

    def _check_seg_bridge_args(self, stmt: ProcCallStmt, name: str) -> None:
        """Type-check FILLSC/MOVESL/MOVESR inside a DEVICE MODULE.

        FILLSC(loc: ADSMEM; len: WORD; val: CHAR);
        MOVESL/MOVESR(src, dst: ADSMEM; len: WORD).

        The pointer operands must be ADS-flavor (segmented) pointers, but they
        may name *different* concrete spaces -- this is the sanctioned on-device
        cross-space data-movement bridge (design S5.4), so no equal-space rule
        applies.  The length must be an ordinal and FILLSC's fill value a CHAR.
        """
        if len(stmt.args) != 3:
            self.error(f"Procedure '{stmt.name}' expects 3 arguments, got {len(stmt.args)}", stmt)
            return
        ptr_positions = (0, ) if name == 'FILLSC' else (0, 1)
        for i in ptr_positions:
            arg_type = self.infer_expression_type(stmt.args[i])
            if not isinstance(arg_type, PointerType) or arg_type.flavor != 'ADS':
                self.error(f"{name} argument {i + 1} must be a segmented (ADS) pointer, "
                           f"got {arg_type}", stmt)
        len_pos = 1 if name == 'FILLSC' else 2
        len_type = self.infer_expression_type(stmt.args[len_pos])
        if len_type is not None and not self._is_ordinal_type(len_type):
            self.error(f"{name} length argument must be an integer, got {len_type}", stmt)
        if name == 'FILLSC':
            val_type = self.infer_expression_type(stmt.args[2])
            if val_type is not None and not val_type.equivalent_to(CHAR_TYPE):
                self.error(f"FILLSC fill value must be a CHAR, got {val_type}", stmt)

    def _check_device_orchestration_args(self, name: str, stmt: ProcCallStmt) -> None:
        """Type-check the host device-orchestration procedures (Milestone D).

        DEVCOPYTO(dev, src, nbytes), DEVCOPYFROM(dst, dev, nbytes),
        DEVFREE(dev), LAUNCH(kernel, grid, block, args...).

        All are host-only: orchestration has no meaning inside DEVICE code.
        Address slots accept an ADRMEM handle, an ``ADR``/``ADS`` address, or an
        addressable aggregate/variable; byte counts and launch geometry must be
        integers.  LAUNCH's first argument must name an (imported) kernel; the
        remaining arguments are the kernel's actuals and are checked against its
        parameter count.
        """
        args = stmt.args or []
        if self.in_device_module:
            self.error(f"{name} is host-only and cannot appear in DEVICE code", stmt)
            return

        def _is_address_like(expr) -> bool:
            if isinstance(expr, (AdrExpr, AdsExpr, Designator)):
                return True
            t = self.infer_expression_type(expr)
            return isinstance(t, (PointerType, ArrayType, RecordType, StringType, LStringType))

        if name in {'DEVCOPYTO', 'DEVCOPYFROM'}:
            if len(args) != 3:
                self.error(f"{name} expects 3 arguments (two addresses and a byte count), got {len(args)}", stmt)
                return
            for i in (0, 1):
                if not _is_address_like(args[i]):
                    self.error(f"{name} argument {i + 1} must be a device handle or buffer address", stmt)
            n_type = self.infer_expression_type(args[2])
            if n_type is not None and not self._is_integer_type(n_type):
                self.error(f"{name} byte count must be an integer, got {n_type}", stmt)
            return

        if name == 'DEVFREE':
            if len(args) != 1:
                self.error(f"DEVFREE expects 1 argument (a device handle), got {len(args)}", stmt)
                return
            if not _is_address_like(args[0]):
                self.error("DEVFREE argument must be a device handle", stmt)
            return

        # LAUNCH(kernel, <geometry>, kernel actuals...)
        if len(args) < 1:
            self.error("LAUNCH expects at least a kernel name", stmt)
            return
        kernel = args[0]
        if not isinstance(kernel, (Identifier, Designator)) or getattr(kernel, 'selectors', None):
            self.error("LAUNCH first argument must name a kernel", stmt)
            return
        ksym = self.symbol_table.lookup(kernel.name) or self.symbol_table.lookup(kernel.name.upper())
        if not ksym or not isinstance(ksym.type, ProcedureType):
            self.error(f"LAUNCH cannot resolve kernel procedure '{kernel.name}'", stmt)
            return
        # Geometry is 2 (grid, block -> 1-D) or 6 (gx,gy,gz, bx,by,bz) integer
        # values; the count is implied by the kernel's arity (the trailing
        # `expected` args are the kernel actuals).
        expected = len(ksym.type.params)
        split = len(args) - expected
        geometry = args[1:split] if split >= 1 else []
        kernel_actuals = args[split:] if expected else []
        if len(geometry) not in (2, 6):
            self.error(
                f"LAUNCH of '{kernel.name}' (a {expected}-parameter kernel) expects "
                f"2 (grid, block) or 6 (gx,gy,gz, bx,by,bz) geometry values plus "
                f"{expected} kernel argument(s); got {max(len(args) - 1, 0)} argument(s) "
                f"after the kernel name", stmt)
        else:
            for gexpr in geometry:
                g_type = self.infer_expression_type(gexpr)
                if g_type is not None and not self._is_integer_type(g_type):
                    self.error(f"LAUNCH geometry dimension must be an integer, got {g_type}", stmt)
        # Visit each actual so undefined identifiers are still reported. Exact
        # type compatibility (an ADRMEM handle into an ADS(GLOBAL) buffer
        # parameter) is intentionally lenient -- the codegen coerces the handle
        # to the kernel's pointer parameter type.
        for actual in kernel_actuals:
            self.infer_expression_type(actual)
