#!/usr/bin/env python3
"""
Pascal to LLVM IR compiler driver.

Usage:
    python3 compile_to_llvm.py [-v|--verbose] <source.pas> [output.ll]

If output.ll is not specified, IR is written to stdout.
With -v/--verbose, codegen logs each declaration/statement it processes and
prints a full traceback if compilation fails.
"""

import sys
import traceback
from parser import parse_file
from type_checker import PascalTypeChecker
from codegen_llvm import compile_to_llvm


def main() -> int:
    argv = sys.argv[1:]
    verbose = False
    positional = []
    for a in argv:
        if a in ('-v', '--verbose'):
            verbose = True
        elif a.startswith('-'):
            print(f'Unknown option: {a}', file=sys.stderr)
            return 2
        else:
            positional.append(a)

    if not positional:
        print('Usage: python3 compile_to_llvm.py [-v|--verbose] <source.pas> [output.ll]', file=sys.stderr)
        return 2

    source_file = positional[0]
    output_file = positional[1] if len(positional) > 1 else None

    try:
        # Parse
        print(f'Parsing {source_file}...', file=sys.stderr)
        ast = parse_file(source_file)

        # Type check
        print(f'Type checking...', file=sys.stderr)
        type_checker = PascalTypeChecker()
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
        print(f'Generating LLVM IR...', file=sys.stderr)
        ir = compile_to_llvm(ast, verbose=verbose)

        # Output
        if output_file:
            with open(output_file, 'w') as f:
                f.write(ir)
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
