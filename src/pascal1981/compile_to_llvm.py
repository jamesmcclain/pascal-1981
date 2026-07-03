#!/usr/bin/env python3
"""
Pascal to LLVM IR compiler driver.

Usage:
    pascal1981 [-v|--verbose] <source.pas> [output.ll]
    pascal1981 --print-runtime-path

If output.ll is not specified, IR is written to stdout.
With -v/--verbose, codegen logs each declaration/statement it processes and
prints a full traceback if compilation fails.
"""

import argparse
import sys
import traceback

from . import runtime_lib_path
from .codegen_llvm import compile_to_llvm
from .features import all_features, resolve_features
from .parser import parse_file
from .type_checker import PascalTypeChecker


def main() -> int:
    parser = argparse.ArgumentParser(description="Pascal to LLVM IR compiler driver.", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('source_file', nargs='?', type=str, help='Source Pascal file (e.g., program.pas)')
    parser.add_argument(
        'output_file',
        nargs='?',  # Optional positional argument
        default=None,
        help='Output LLVM IR file to write to.')
    parser.add_argument('-v', '--verbose', action='store_true', help='Log each declaration/statement and print a full traceback on failure.')
    parser.add_argument('-f', '--feature', action='append', default=[], metavar='NAME', help='Enable extension feature NAME; use no-NAME to disable. Repeatable.')
    parser.add_argument('--dialect',
                        choices=['vintage', 'extended'],
                        default='vintage',
                        help='Feature umbrella: vintage enables no extensions; extended enables all registered features.')
    parser.add_argument('--list-features', action='store_true', help='List registered extension features and exit.')
    parser.add_argument('--print-runtime-path', action='store_true', help='Print the bundled libpascalrt.a path and exit.')
    parser.add_argument('--host-triple', default='x86_64-pc-linux-gnu', metavar='TRIPLE', help='LLVM target triple for host MODULE/PROGRAM units (default: x86_64-pc-linux-gnu).')
    parser.add_argument('--device-triple',
                        default='x86_64-pc-linux-gnu',
                        metavar='TRIPLE',
                        help='LLVM target triple for DEVICE MODULE units; e.g. nvptx64-nvidia-cuda or '
                        'amdgcn-amd-amdhsa. Defaults to the host x86 triple (CPU-device: address '
                        'spaces collapse to addrspace 0).')
    parser.add_argument('--target',
                        choices=['host', 'ptx'],
                        default='host',
                        help='Output target: host LLVM IR (.ll, default) or device NVPTX assembly '
                        '(.ptx). --target ptx selects the NVPTX device triple and honors --sm; it '
                        'is the single-CLI replacement for python -m pascal1981.compile_to_ptx.')
    parser.add_argument('--sm',
                        default='sm_70',
                        metavar='ARCH',
                        help='NVPTX target CPU for --target ptx, e.g. sm_70, sm_86 (default: sm_70).')
    parser.add_argument('--emit-llvm',
                        default=None,
                        metavar='PATH',
                        help='With --target ptx, also write the intermediate NVPTX LLVM IR to PATH.')
    parser.add_argument('--opt-level',
                        type=int,
                        choices=[0, 1, 2, 3],
                        default=0,
                        metavar='N',
                        help='With --target ptx, run LLVM\'s O0-O3 mid-level IR pass pipeline before '
                        'NVPTX codegen (default: 0, i.e. no pipeline). Only meaningful with '
                        '--target ptx; an error with --target host.')
    parser.add_argument('--device-backend',
                        choices=['cpu', 'cuda'],
                        default='cpu',
                        help='Host launch backend for LAUNCH lowering. cpu (default): emit the '
                        'in-process dispatch thunk + registry (CPU-device stand-in). cuda: target '
                        'the CUDA Driver API shim -- the kernel is the loaded PTX module, so no '
                        'thunk/registry and no dead kernel-symbol reference are emitted (no dev.ll '
                        'needed at link).')
    parser.add_argument('--embed-device-ptx',
                        default=None,
                        metavar='PTX_FILE',
                        help='Embed the named device PTX artifact (the companion device unit, '
                        'compiled with python -m pascal1981.compile_to_ptx) into this host '
                        'compiland as the __pas_device_ptx blob, so the launch path is '
                        'self-contained. The CPU device never executes it; the CUDA driver shim '
                        'cuModuleLoadData''s it. Only meaningful for host PROGRAM/MODULE units.')
    # ----------------------------------------------------------------
    # Runtime-check flag overrides
    # Each flag follows the same tri-state convention:
    #   on     — force the check on  (overrides source metacommands)
    #   off    — force the check off (overrides source metacommands)
    #   source — respect what the source says via $METACOMMAND (default)
    #
    # $DEBUG is a master switch: --debug on/off sets all sub-flags
    # (ENTRY INDEXCK INITCK MATHCK NILCK RANGECK STACKCK) to the same
    # value, but individual flags still override the master.
    # ----------------------------------------------------------------
    _flag_help = ('Force {name} check on/off, or use source metacommands (default).')
    # NOTE: STACKCK is accepted as a documented no-op: on this target the
    # OS guard page already faults on stack overflow, and clang owns frame
    # layout, so explicit entry probes would add cost without adding
    # detection.  All other check flags are implemented in codegen.
    _flag_help_noop = ('Force {name} on/off (accepted for source compatibility; no-op on '
                       'this target — the OS guard page already detects stack overflow).')
    parser.add_argument('--debug', choices=['on', 'off', 'source'], default='source', help=_flag_help.format(name='DEBUG (master: sets all sub-flags)'))
    parser.add_argument('--rangeck', choices=['on', 'off', 'source'], default='source', help=_flag_help.format(name='RANGECK (subrange validity)'))
    parser.add_argument('--indexck', choices=['on', 'off', 'source'], default='source', help=_flag_help.format(name='INDEXCK (array index bounds)'))
    parser.add_argument('--mathck', choices=['on', 'off', 'source'], default='source', help=_flag_help.format(name='MATHCK (integer overflow / div-by-zero)'))
    parser.add_argument('--nilck', choices=['on', 'off', 'source'], default='source', help=_flag_help.format(name='NILCK (nil pointer dereference)'))
    parser.add_argument('--stackck', choices=['on', 'off', 'source'], default='source', help=_flag_help_noop.format(name='STACKCK (stack overflow)'))
    parser.add_argument('--initck', choices=['on', 'off', 'source'], default='source', help=_flag_help.format(name='INITCK (uninitialised variable detection)'))
    args = parser.parse_args()

    if args.print_runtime_path:
        print(runtime_lib_path())
        return 0

    if args.target != 'ptx' and args.opt_level:
        parser.error('--opt-level is only meaningful with --target ptx')

    if args.target == 'ptx':
        # Single-CLI device path: parse/check/lower to NVPTX IR, then PTX.
        from .compile_to_ptx import compile_file_to_ptx
        try:
            features = resolve_features(args.dialect, args.feature)
        except ValueError as exc:
            parser.error(str(exc))
        if not args.source_file:
            parser.error('--target ptx requires a source file')
        try:
            device_triple = args.device_triple
            if device_triple == 'x86_64-pc-linux-gnu':
                device_triple = 'nvptx64-nvidia-cuda'
            ptx = compile_file_to_ptx(
                args.source_file,
                host_triple=args.host_triple,
                device_triple=device_triple,
                cpu=args.sm,
                features=features,
                emit_llvm_path=args.emit_llvm,
                opt_level=args.opt_level,
            )
            if args.output_file:
                with open(args.output_file, 'w') as f:
                    f.write(ptx)
                if args.verbose:
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

    if args.list_features:
        for feature in all_features():
            print(f'{feature.name}\tdefault={str(feature.default).lower()}\t{feature.help}')
        return 0

    try:
        features = resolve_features(args.dialect, args.feature)
    except ValueError as exc:
        parser.error(str(exc))

    source_file = args.source_file
    output_file = args.output_file
    verbose = args.verbose

    if not source_file:
        # This path should technically be unreachable if argparse is set up correctly,
        # but we keep the usage message structure for robustness.
        print('Error: Missing source file.', file=sys.stderr)
        parser.print_help(file=sys.stderr)
        return 2

    try:
        # Parse
        if verbose:
            print(f'Parsing {source_file}...', file=sys.stderr)
        ast = parse_file(source_file)

        # Type check
        if verbose:
            print(f'Type checking...', file=sys.stderr)
        type_checker = PascalTypeChecker(source_file=source_file, features=features)
        check_result = type_checker.check(ast)

        if not check_result.success:
            print(f'Type checking failed:', file=sys.stderr)
            for error in check_result.errors:
                print(f'  {error}', file=sys.stderr)
            return 1

        if check_result.warnings:
            for warning in check_result.warnings:
                print(f'Warning: {warning}', file=sys.stderr)

        # Codegen
        if verbose:
            print(f'Generating LLVM IR...', file=sys.stderr)
        # Build force_flags dict from CLI args.  Only flags explicitly set
        # to 'on' or 'off' (not 'source') enter the dict.
        _DEBUG_SUBS = ('ENTRY', 'INDEXCK', 'INITCK', 'MATHCK', 'NILCK', 'RANGECK', 'STACKCK')
        force_flags: dict[str, bool] = {}

        # $DEBUG master: pre-populate sub-flags, then let individual args override.
        if args.debug != 'source':
            debug_val = (args.debug == 'on')
            force_flags['DEBUG'] = debug_val
            for sub in _DEBUG_SUBS:
                force_flags[sub] = debug_val

        # Individual flags (each overrides whatever $DEBUG may have set).
        for flag_name, attr in (
            ('RANGECK', 'rangeck'),
            ('INDEXCK', 'indexck'),
            ('MATHCK', 'mathck'),
            ('NILCK', 'nilck'),
            ('STACKCK', 'stackck'),
            ('INITCK', 'initck'),
        ):
            val = getattr(args, attr)
            if val != 'source':
                force_flags[flag_name] = (val == 'on')

        embed_device_ptx_text = None
        if getattr(args, 'embed_device_ptx', None):
            with open(args.embed_device_ptx, 'r') as ptx_f:
                embed_device_ptx_text = ptx_f.read()

        ir = compile_to_llvm(ast,
                             verbose=verbose,
                             source_file=source_file,
                             force_flags=force_flags or None,
                             features=features,
                             host_triple=args.host_triple,
                             device_triple=args.device_triple,
                             embed_device_ptx_text=embed_device_ptx_text,
                             device_backend=args.device_backend)

        # Output
        if output_file:
            with open(output_file, 'w') as f:
                f.write(ir)
            if verbose:
                print(f'Wrote {output_file}', file=sys.stderr)
        else:
            print(ir)

        return 0

    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        if verbose:
            print('--- traceback ---', file=sys.stderr)
            traceback.print_exc()
        else:
            print('(re-run with -v for a full traceback)', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
