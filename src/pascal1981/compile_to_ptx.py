#!/usr/bin/env python3
"""Pascal DEVICE compiler driver that emits NVPTX assembly (.ptx).

This is intentionally device-artifact-only: it parses/type-checks Pascal source,
lowers DEVICE code to NVPTX LLVM IR, then asks LLVM's NVPTX backend to emit PTX.
It does not require a CUDA runtime or an NVIDIA device.
"""

from __future__ import annotations

import argparse
import sys
import traceback

from .codegen_llvm import compile_to_llvm
from .features import resolve_features
from .parser import parse_file
from .type_checker import PascalTypeChecker


def llvm_ir_to_ptx(ir_text: str, *, triple: str = 'nvptx64-nvidia-cuda', cpu: str = 'sm_70', opt_level: int = 0) -> str:
    """Emit PTX assembly from LLVM IR using llvmlite's NVPTX backend.

    ``opt_level`` (0-3) runs LLVM's optional mid-level O1/O2/O3 pass
    pipeline over the module before handing it to the NVPTX backend.
    Production-quality PTX should use ``opt_level=2``; level 0 is retained
    as the compatibility/debugging default. 0 (default) is a no-op: the module goes straight
    from `verify()` to `emit_assembly()`, exactly as before this flag existed,
    so no caller's output changes unless it opts in. This matters because the
    NVPTX backend's own instruction-selection/scheduling is a separate,
    unconditional layer -- `opt_level` only controls whether a mid-level IR
    pipeline (inlining, GVN, LICM, instcombine, vectorization, ...) runs first;
    `ptxas` still performs final scheduling/register allocation downstream of
    everything here regardless of this flag.

    The pass-manager API used here (`create_pipeline_tuning_options` /
    `create_pass_builder` / `ModulePassManager.run`) is llvmlite's "new pass
    manager" binding, confirmed present under the repo's pinned
    `llvmlite>=0.47.0`; it is the same API already exercised by
    `tests/test_tuning_hints.py::test_unroll_hint_fires_under_o2`.
    """
    try:
        import llvmlite.binding as llvm
    except Exception as exc:  # pragma: no cover - exercised only without llvmlite
        raise RuntimeError('PTX emission requires llvmlite') from exc

    try:
        llvm.initialize_all_targets()
        llvm.initialize_all_asmprinters()
    except Exception:
        # llvmlite permits repeated initialization in some builds and rejects it
        # in others.  Either way, continue to target lookup.
        pass

    llvm_mod = llvm.parse_assembly(ir_text)
    llvm_mod.verify()
    target = llvm.Target.from_triple(triple)
    tm = target.create_target_machine(cpu=cpu)
    if opt_level:
        from .codegen.llvmlite_compat import create_pipeline_tuning_options
        pto = create_pipeline_tuning_options(llvm, speed_level=opt_level)
        pb = llvm.create_pass_builder(tm, pto)
        pb.getModulePassManager().run(llvm_mod, pb)
        llvm_mod.verify()
    return tm.emit_assembly(llvm_mod)


def compile_file_to_ptx(source_file: str,
                        *,
                        host_triple: str = 'x86_64-pc-linux-gnu',
                        device_triple: str = 'nvptx64-nvidia-cuda',
                        cpu: str = 'sm_70',
                        features=None,
                        emit_llvm_path: str | None = None,
                        opt_level: int = 0) -> str:
    """Compile one Pascal source file to PTX text."""
    ast = parse_file(source_file)
    result = PascalTypeChecker(source_file=source_file, features=features).check(ast)
    if not result.success:
        lines = ['Type checking failed:'] + [f'  {err}' for err in result.errors]
        raise RuntimeError('\n'.join(lines))

    ir = compile_to_llvm(
        ast,
        source_file=source_file,
        features=features,
        host_triple=host_triple,
        device_triple=device_triple,
    )
    if emit_llvm_path:
        with open(emit_llvm_path, 'w') as f:
            f.write(ir)
    return llvm_ir_to_ptx(ir, triple=device_triple, cpu=cpu, opt_level=opt_level)


def run_ptx_cli(source_file: str,
                output_file: str | None,
                *,
                host_triple: str,
                device_triple: str,
                cpu: str,
                features,
                emit_llvm_path: str | None,
                opt_level: int,
                verbose: bool) -> int:
    """Shared CLI tail for PTX emission: compile, write-or-print, report.

    Used by both this module's `main` and `compile_to_llvm.main`'s
    `--target ptx` branch, which previously duplicated this block verbatim.
    Returns a process exit code.
    """
    try:
        ptx = compile_file_to_ptx(
            source_file,
            host_triple=host_triple,
            device_triple=device_triple,
            cpu=cpu,
            features=features,
            emit_llvm_path=emit_llvm_path,
            opt_level=opt_level,
        )
        if output_file:
            with open(output_file, 'w') as f:
                f.write(ptx)
            if verbose:
                print(f'Wrote {output_file}', file=sys.stderr)
        else:
            print(ptx)
        return 0
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        if verbose:
            traceback.print_exc()
        else:
            print('(re-run with -v for a full traceback)', file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description='Compile Pascal DEVICE code to PTX assembly.')
    parser.add_argument('source_file', help='Source Pascal DEVICE implementation file')
    parser.add_argument('output_file', nargs='?', default=None, help='Output .ptx file; stdout if omitted')
    parser.add_argument('--host-triple', default='x86_64-pc-linux-gnu')
    parser.add_argument('--device-triple', default='nvptx64-nvidia-cuda')
    parser.add_argument('--cpu', default='sm_70', help='NVPTX target CPU, e.g. sm_70, sm_86 (default: sm_70)')
    parser.add_argument('--emit-llvm', default=None, metavar='PATH', help='Also write the intermediate LLVM IR')
    parser.add_argument('--opt-level',
                        type=int,
                        choices=[0, 1, 2, 3],
                        default=0,
                        metavar='N',
                        help='Run LLVM\'s O0-O3 mid-level IR pass pipeline before NVPTX codegen (default: 0 for compatibility/debugging; use 2 for quality PTX).')
    parser.add_argument('-f', '--feature', action='append', default=[], metavar='NAME', help='Enable extension feature NAME; use no-NAME to disable. Repeatable.')
    parser.add_argument('--dialect', choices=['vintage', 'extended'], default='vintage')
    parser.add_argument('-v', '--verbose', action='store_true', help='Print a full traceback on failure')
    args = parser.parse_args()

    try:
        features = resolve_features(args.dialect, args.feature)
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        else:
            print('(re-run with -v for a full traceback)', file=sys.stderr)
        return 1
    return run_ptx_cli(
        args.source_file,
        args.output_file,
        host_triple=args.host_triple,
        device_triple=args.device_triple,
        cpu=args.cpu,
        features=features,
        emit_llvm_path=args.emit_llvm,
        opt_level=args.opt_level,
        verbose=args.verbose,
    )


if __name__ == '__main__':
    raise SystemExit(main())
