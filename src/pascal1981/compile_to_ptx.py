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


def llvm_ir_to_ptx(ir_text: str, *, triple: str = 'nvptx64-nvidia-cuda', cpu: str = 'sm_70') -> str:
    """Emit PTX assembly from LLVM IR using llvmlite's NVPTX backend."""
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
    return tm.emit_assembly(llvm_mod)


def compile_file_to_ptx(source_file: str, *, host_triple: str = 'x86_64-pc-linux-gnu',
                        device_triple: str = 'nvptx64-nvidia-cuda', cpu: str = 'sm_70',
                        features=None, emit_llvm_path: str | None = None) -> str:
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
    return llvm_ir_to_ptx(ir, triple=device_triple, cpu=cpu)


def main() -> int:
    parser = argparse.ArgumentParser(description='Compile Pascal DEVICE code to PTX assembly.')
    parser.add_argument('source_file', help='Source Pascal DEVICE implementation file')
    parser.add_argument('output_file', nargs='?', default=None, help='Output .ptx file; stdout if omitted')
    parser.add_argument('--host-triple', default='x86_64-pc-linux-gnu')
    parser.add_argument('--device-triple', default='nvptx64-nvidia-cuda')
    parser.add_argument('--cpu', default='sm_70', help='NVPTX target CPU, e.g. sm_70, sm_86 (default: sm_70)')
    parser.add_argument('--emit-llvm', default=None, metavar='PATH', help='Also write the intermediate LLVM IR')
    parser.add_argument('-f', '--feature', action='append', default=[], metavar='NAME', help='Enable extension feature NAME; use no-NAME to disable. Repeatable.')
    parser.add_argument('--dialect', choices=['vintage', 'extended'], default='vintage')
    parser.add_argument('-v', '--verbose', action='store_true', help='Print a full traceback on failure')
    args = parser.parse_args()

    try:
        features = resolve_features(args.dialect, args.feature)
        ptx = compile_file_to_ptx(
            args.source_file,
            host_triple=args.host_triple,
            device_triple=args.device_triple,
            cpu=args.cpu,
            features=features,
            emit_llvm_path=args.emit_llvm,
        )
        if args.output_file:
            with open(args.output_file, 'w') as f:
                f.write(ptx)
            print(f'Wrote {args.output_file}', file=sys.stderr)
        else:
            print(ptx)
        return 0
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        if args.verbose:
            traceback.print_exc()
        else:
            print('(re-run with -v for a full traceback)', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
