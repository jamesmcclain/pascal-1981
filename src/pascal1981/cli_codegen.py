"""
CLI Entry point for standalone pascal1981 code generator process.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .codegen_llvm import CodegenError, compile_to_llvm
from .depth_limits import recursion_error_message
from .features import resolve_features
from .serialization import ast_from_json


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pascal-1981 Standalone Code Generator Process")
    parser.add_argument("ast_file", nargs="?", default="-", help="Input AST JSON file (or '-' for stdin)")
    parser.add_argument("-o", "--output", dest="output_file", default="-", help="Output LLVM IR / assembly file (or '-' for stdout)")
    parser.add_argument("--source-file", type=str, default=None, help="Original Pascal source file path (for error/debug metadata)")
    parser.add_argument("--dialect", choices=["vintage", "extended", "device"], default="vintage", help="Language dialect")
    parser.add_argument("-f", "--feature", action="append", default=[], help="Enable/disable feature flags")
    parser.add_argument("--host-triple", type=str, default="x86_64-pc-linux-gnu", help="Target LLVM host triple")
    parser.add_argument("--device-triple", type=str, default="x86_64-pc-linux-gnu", help="Target device triple")
    parser.add_argument("--device-backend", choices=["cpu", "cuda"], default="cpu", help="Backend for device code")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose codegen output")

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

        ir = compile_to_llvm(
            ast,
            verbose=args.verbose,
            source_file=args.source_file,
            features=features,
            host_triple=args.host_triple,
            device_triple=args.device_triple,
            device_backend=args.device_backend,
        )

        if args.output_file == "-" or not args.output_file:
            sys.stdout.write(ir if ir.endswith("\n") else ir + "\n")
        else:
            with open(args.output_file, "w", encoding="utf-8") as f:
                f.write(ir if ir.endswith("\n") else ir + "\n")
        return 0

    except CodegenError as exc:
        print(f"Codegen error: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"File not found: {exc}", file=sys.stderr)
        return 1
    except RecursionError:
        print(f"Error: {recursion_error_message()}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
