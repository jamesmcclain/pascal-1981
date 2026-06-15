"""Allow ``python -m pascal1981`` to invoke the CLI."""

from __future__ import annotations

from .compile_to_llvm import main

if __name__ == '__main__':
    raise SystemExit(main())
