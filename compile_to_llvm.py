#!/usr/bin/env python3
"""
Pascal to LLVM IR compiler driver.

Usage:
    python3 compile_to_llvm.py <source.pas> [output.ll]

If output.ll is not specified, IR is written to stdout.
"""

import sys
from parser import parse_file
from codegen_llvm import compile_to_llvm


def main() -> int:
    if len(sys.argv) < 2:
        print('Usage: python3 compile_to_llvm.py <source.pas> [output.ll]', file=sys.stderr)
        return 2

    source_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        # Parse
        print(f'Parsing {source_file}...', file=sys.stderr)
        ast = parse_file(source_file)

        # Codegen
        print(f'Generating LLVM IR...', file=sys.stderr)
        ir = compile_to_llvm(ast)

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
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
