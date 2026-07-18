#!/usr/bin/env python3
"""
Pascal to LLVM IR compiler driver.

Usage:
    pascal1981 [options] <source.pas>     compile, assemble, and link (a.out, or -o FILE)
    pascal1981 -S <source.pas>            compile only: write assembly (host: ./<name>.ll;
                                          --target ptx: ./<name>.ptx). -o - writes to stdout.
    pascal1981 -c <source.pas>            compile and assemble to ./<name>.o (or -o FILE)
    pascal1981 -print-file-name=libpascalrt.a

Assembling and linking run through clang; the bundled libpascalrt.a is added
to the link automatically.  With -v/--verbose, codegen logs each
declaration/statement it processes, echoes the clang commands it runs, and
prints a full traceback if compilation fails.
"""

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import traceback

from . import runtime_lib_path
from .codegen_llvm import compile_to_llvm
from .features import all_features, resolve_features
from .parser import parse_file
from .type_checker import PascalTypeChecker


def _default_output(source_file: str, suffix: str) -> str:
    """gcc-style default output name: the source basename with its last
    extension swapped for ``suffix``, in the current working directory."""
    return os.path.splitext(os.path.basename(source_file))[0] + suffix


def _optimize_ir_text(ir_text: str, opt_level: int) -> str:
    """Run LLVM's O1-O3 mid-level pass pipeline over host IR and return the
    optimized module as text (llvmlite new-PM bindings; mirrors the pipeline
    compile_to_ptx.llvm_ir_to_ptx runs for --target ptx)."""
    import llvmlite.binding as llvm

    try:
        llvm.initialize_all_targets()
        llvm.initialize_all_asmprinters()
    except Exception:
        # llvmlite permits repeated initialization in some builds and rejects
        # it in others; either way, continue to target lookup.
        pass

    m = re.search(r'^target triple = "([^"]+)"', ir_text, re.MULTILINE)
    triple = m.group(1) if m else llvm.get_default_triple()
    llvm_mod = llvm.parse_assembly(ir_text)
    llvm_mod.verify()
    tm = llvm.Target.from_triple(triple).create_target_machine()
    from .codegen.llvmlite_compat import create_pipeline_tuning_options
    pto = create_pipeline_tuning_options(llvm, speed_level=opt_level)
    pb = llvm.create_pass_builder(tm, pto)
    pb.getModulePassManager().run(llvm_mod, pb)
    llvm_mod.verify()
    return str(llvm_mod)


def _run_clang(cmd: list, verbose: bool) -> int:
    """Run one clang command line, echoing it under -v; return its exit code."""
    if verbose:
        print('+ ' + ' '.join(shlex.quote(a) for a in cmd), file=sys.stderr)
    try:
        return subprocess.run(cmd).returncode
    except OSError as exc:
        print(f'Error: failed to execute clang: {exc}', file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Pascal to LLVM IR compiler driver.", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('source_file', nargs='?', type=str, help='Source Pascal file (e.g., program.pas)')
    parser.add_argument('-o', '--output', dest='output_file', default=None, metavar='FILE', help='Write output to FILE (default: a.out when linking, ./<basename>.ll or .ptx with -S, ./<basename>.o with -c). With -S, -o - writes to stdout.')
    parser.add_argument('-v', '--verbose', action='store_true', help='Log each declaration/statement, echo clang command lines, and print a full traceback on failure.')
    _stages = parser.add_mutually_exclusive_group()
    _stages.add_argument('-S', dest='stage_s', action='store_true', help='Compile only: emit assembly and stop (host: LLVM IR; --target ptx: PTX). Default output ./<basename>.ll (or .ptx); -o - writes to stdout.')
    _stages.add_argument('-c', dest='stage_c', action='store_true', help='Compile and assemble to an object file via clang (default ./<basename>.o); do not link.')
    parser.add_argument('-f', '--feature', action='append', default=[], metavar='NAME', help='Enable extension feature NAME; use no-NAME to disable. Repeatable.')
    parser.add_argument('--dialect',
                        choices=['vintage', 'extended'],
                        default='vintage',
                        help='Feature umbrella: vintage enables no extensions; extended enables all registered features.')
    parser.add_argument('--list-features', action='store_true', help='List registered extension features and exit.')
    parser.add_argument('-print-file-name', dest='print_file_name', default=None, metavar='LIB', help='Print the absolute path of the named runtime archive and exit (gcc-style; e.g. -print-file-name=libpascalrt.a). As with gcc, an unrecognized LIB is echoed back unchanged.')
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
    parser.add_argument('--sm', default='sm_70', metavar='ARCH', help='NVPTX target CPU for --target ptx, e.g. sm_70, sm_86 (default: sm_70).')
    parser.add_argument('--save-llvm', default=None, metavar='PATH', help='With --target ptx, also write the intermediate NVPTX LLVM IR to PATH (gcc -save-temps style).')
    parser.add_argument('-O',
                        dest='opt_level',
                        type=int,
                        choices=[0, 1, 2, 3],
                        nargs='?',
                        const=1,
                        default=None,
                        metavar='N',
                        help='Optimization level 0-3 (gcc-style; a bare -O means -O1). Host: with -S, '
                        'runs LLVM\'s mid-level IR pipeline before writing IR; with -c or when linking, '
                        'forwarded to clang. With --target ptx, runs the mid-level pipeline before NVPTX '
                        'codegen (default: 0 for compatibility/debugging; use 2 for quality PTX).')
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
                        'cuModuleLoadData'
                        's it. Only meaningful for host PROGRAM/MODULE units.')
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

    if args.print_file_name is not None:
        # gcc semantics: print the located path, or echo the name back when it
        # is not a file this driver knows how to locate.
        if args.print_file_name == 'libpascalrt.a':
            print(runtime_lib_path())
        else:
            print(args.print_file_name)
        return 0

    opt_level = args.opt_level if args.opt_level is not None else 0

    if args.target == 'ptx':
        # Single-CLI device path: parse/check/lower to NVPTX IR, then PTX.
        # PTX is an assembly-level artifact, so only -S applies: it cannot be
        # assembled further (-c needs ptxas) or linked into a host executable.
        # The compile/emit/report tail is shared with compile_to_ptx.main.
        from .compile_to_ptx import run_ptx_cli
        if args.stage_c:
            parser.error('-c is not available with --target ptx (assembling PTX requires ptxas; emit PTX with -S)')
        if not args.stage_s:
            parser.error('--target ptx requires -S (device assembly cannot be assembled or linked into a host executable)')
        try:
            features = resolve_features(args.dialect, args.feature)
        except ValueError as exc:
            parser.error(str(exc))
        if not args.source_file:
            parser.error('--target ptx requires a source file')
        device_triple = args.device_triple
        if device_triple == 'x86_64-pc-linux-gnu':
            device_triple = 'nvptx64-nvidia-cuda'
        out = args.output_file or _default_output(args.source_file, '.ptx')
        return run_ptx_cli(
            args.source_file,
            None if out == '-' else out,
            host_triple=args.host_triple,
            device_triple=device_triple,
            cpu=args.sm,
            features=features,
            emit_llvm_path=args.save_llvm,
            opt_level=opt_level,
            verbose=args.verbose,
        )

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

    if not args.stage_s:
        if args.output_file == '-':
            parser.error('-o - (stdout) is only meaningful with -S')
        if shutil.which('clang') is None:
            parser.error('clang not found: -c and linking run through clang '
                         '(use -S to emit LLVM IR and drive clang manually)')
        if not args.stage_c and not os.path.exists(runtime_lib_path()):
            parser.error(f'runtime archive not found: {runtime_lib_path()} '
                         '(build it first: make -C runtime)')

    try:
        # Parse
        if verbose:
            print(f'Parsing {source_file}...', file=sys.stderr)
        ast = parse_file(source_file)

        # Type check
        if verbose:
            print('Type checking...', file=sys.stderr)
        type_checker = PascalTypeChecker(source_file=source_file, features=features)
        check_result = type_checker.check(ast)

        if not check_result.success:
            print('Type checking failed:', file=sys.stderr)
            for error in check_result.errors:
                print(f'  {error}', file=sys.stderr)
            return 1

        if check_result.warnings:
            for warning in check_result.warnings:
                print(f'Warning: {warning}', file=sys.stderr)

        # Codegen
        if verbose:
            print('Generating LLVM IR...', file=sys.stderr)
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

        # Stage dispatch
        if args.stage_s:
            if opt_level:
                ir = _optimize_ir_text(ir, opt_level)
            out = output_file or _default_output(source_file, '.ll')
            if out == '-':
                sys.stdout.write(ir if ir.endswith('\n') else ir + '\n')
            else:
                with open(out, 'w') as f:
                    f.write(ir)
                if verbose:
                    print(f'Wrote {out}', file=sys.stderr)
            return 0

        # -c / link: hand the IR to clang (linking adds the bundled runtime).
        out = output_file or (_default_output(source_file, '.o') if args.stage_c else 'a.out')
        with tempfile.TemporaryDirectory(prefix='pascal1981-') as tmp_dir:
            ll_path = os.path.join(tmp_dir, 'unit.ll')
            with open(ll_path, 'w') as f:
                f.write(ir)
            if args.stage_c:
                cmd = ['clang', '-c', ll_path]
            else:
                cmd = ['clang', ll_path, runtime_lib_path()]
            if args.opt_level is not None:
                cmd.append(f'-O{args.opt_level}')
            cmd += ['-o', out]
            return _run_clang(cmd, verbose)

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
