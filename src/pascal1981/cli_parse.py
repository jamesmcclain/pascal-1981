"""
CLI Entry point for standalone pascal1981 parser process.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .depth_limits import recursion_error_message
from .features import resolve_features
from .parser import Parser, ParserError
from .serialization import ast_to_json, tokens_from_json


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pascal-1981 Standalone Parser Process")
    parser.add_argument("token_file", nargs="?", default="-", help="Input token JSON file (or '-' for stdin)")
    parser.add_argument("-o", "--output", dest="output_file", default="-", help="Output AST JSON file (or '-' for stdout)")
    parser.add_argument("--source-file", type=str, default=None, help="Original Pascal source file path (for error messages / module resolution)")
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
        if args.token_file == "-" or not args.token_file:
            token_data = sys.stdin.read()
        else:
            with open(args.token_file, "r", encoding="utf-8") as f:
                token_data = f.read()

        tokens = tokens_from_json(token_data)
        ast = Parser(tokens).parse()

        json_out = ast_to_json(ast, indent=args.indent)

        if args.output_file == "-" or not args.output_file:
            sys.stdout.write(json_out if json_out.endswith("\n") else json_out + "\n")
        else:
            with open(args.output_file, "w", encoding="utf-8") as f:
                f.write(json_out if json_out.endswith("\n") else json_out + "\n")
        return 0

    except ParserError as exc:
        print(f"Parser error: {exc}", file=sys.stderr)
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
