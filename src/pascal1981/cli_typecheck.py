"""
CLI Entry point for standalone pascal1981 type checker process.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .features import resolve_features
from .serialization import ast_from_json, ast_to_json
from .type_checker import PascalTypeChecker


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pascal-1981 Standalone Type Checker Process")
    parser.add_argument("ast_file", nargs="?", default="-", help="Input raw AST JSON file (or '-' for stdin)")
    parser.add_argument("-o", "--output", dest="output_file", default="-", help="Output annotated AST JSON file (or '-' for stdout)")
    parser.add_argument("--source-file", type=str, default=None, help="Original Pascal source file path (for error/diagnostic context)")
    parser.add_argument("--dialect", choices=["vintage", "extended", "device"], default="vintage", help="Language dialect")
    parser.add_argument("-f", "--feature", action="append", default=[], help="Enable/disable feature flags (e.g. -f c_interop or -f -c_interop)")
    parser.add_argument("--indent", type=int, default=None, help="JSON indentation level for human readability")

    args = parser.parse_args(argv)

    try:
        features = resolve_features(args.dialect, args.feature)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        if args.ast_file == "-" or not args.ast_file:
            ast_data = sys.stdin.read()
        else:
            with open(args.ast_file, "r", encoding="utf-8") as f:
                ast_data = f.read()

        ast = ast_from_json(ast_data)

        source_file = args.source_file or (args.ast_file if args.ast_file != "-" else None)
        type_checker = PascalTypeChecker(source_file=source_file, features=features)
        check_result = type_checker.check(ast)

        if not check_result.success:
            print("Type checking failed:", file=sys.stderr)
            for error in check_result.errors:
                print(f"  {error}", file=sys.stderr)
            return 1

        if check_result.warnings:
            for warning in check_result.warnings:
                print(f"Warning: {warning}", file=sys.stderr)

        json_out = ast_to_json(ast, indent=args.indent)

        if args.output_file == "-" or not args.output_file:
            sys.stdout.write(json_out if json_out.endswith("\n") else json_out + "\n")
        else:
            with open(args.output_file, "w", encoding="utf-8") as f:
                f.write(json_out if json_out.endswith("\n") else json_out + "\n")
        return 0

    except FileNotFoundError as exc:
        print(f"File not found: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
