"""
CLI Entry point for standalone pascal1981 lexer process.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .lexer import Lexer, LexerError, lex_file
from .serialization import tokens_to_json


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pascal-1981 Standalone Lexer Process")
    parser.add_argument("source_file", nargs="?", default="-", help="Source Pascal file (or '-' for stdin)")
    parser.add_argument("-o", "--output", dest="output_file", default="-", help="Output token JSON file (or '-' for stdout)")
    parser.add_argument("--indent", type=int, default=None, help="JSON indentation level for human readability")

    args = parser.parse_args(argv)

    try:
        if args.source_file == "-" or not args.source_file:
            source_text = sys.stdin.read()
            tokens = Lexer(source_text).tokenize()
        else:
            tokens = lex_file(args.source_file)

        json_out = tokens_to_json(tokens, indent=args.indent)

        if args.output_file == "-" or not args.output_file:
            sys.stdout.write(json_out if json_out.endswith("\n") else json_out + "\n")
        else:
            with open(args.output_file, "w", encoding="utf-8") as f:
                f.write(json_out if json_out.endswith("\n") else json_out + "\n")
        return 0

    except LexerError as exc:
        print(f"Lexer error: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"File not found: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
