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
import argparse
from parser import parse_file

from codegen_llvm import compile_to_llvm
from type_checker import PascalTypeChecker


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pascal to LLVM IR compiler driver.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        'source_file', 
        type=str, 
        help='Source Pascal file (e.g., program.pas)'
    )
    parser.add_argument(
        'output_file', 
        nargs='?',  # Optional positional argument
        default=None, 
        help='Output LLVM IR file to write to.'
    )
    parser.add_argument(
        '-v', '--verbose', 
        action='store_true', 
        help='Log each declaration/statement and print a full traceback on failure.'
    )
    args = parser.parse_args()

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
        print(f'Parsing {source_file}...', file=sys.stderr)
        ast = parse_file(source_file)

        # Type check
        print(f'Type checking...', file=sys.stderr)
        type_checker = PascalTypeChecker(source_file=source_file)
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
    try:
        # Parse
        print(f'Parsing {source_file}...', file=sys.stderr)
        ast = parse_file(source_file)

        # Type check
        print(f'Type checking...', file=sys.stderr)
        type_checker = PascalTypeChecker(source_file=source_file)
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
