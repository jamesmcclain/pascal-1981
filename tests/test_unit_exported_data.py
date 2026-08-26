"""UNIT export lists may name types, constants, and variables."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_uses_imports_exported_data_declarations():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "base.inc").write_text("""INTERFACE;
UNIT base;
TYPE T = INTEGER;
CONST Count = 2;
VAR left, right: T;
PROCEDURE Ping(x: T);
END;
""")
        source = root / "main.pas"
        source.write_text("""(*$INCLUDE:'base.inc'*)
PROGRAM main;
USES base;
VAR x: T;
BEGIN
  left := Count; right := left; Ping(right)
END.
""")
        lex = subprocess.run(
            [sys.executable, "-m", "pascal1981.cli_lex", str(source)],
            cwd=root,
            text=True,
            capture_output=True,
            check=True,
            env={
                **os.environ, "PYTHONPATH": str(ROOT / "src")
            },
        )
        parse = subprocess.run(
            [sys.executable, "-m", "pascal1981.cli_parse", "--source-file", str(source), "--dialect", "extended"],
            cwd=root,
            input=lex.stdout,
            text=True,
            capture_output=True,
            check=True,
            env={
                **os.environ, "PYTHONPATH": str(ROOT / "src")
            },
        )
        checked = subprocess.run(
            [sys.executable, "-m", "pascal1981.cli_typecheck", "--source-file",
             str(source), "--dialect", "extended"],
            cwd=root,
            input=parse.stdout,
            text=True,
            capture_output=True,
            env={
                **os.environ, "PYTHONPATH": str(ROOT / "src")
            },
        )
        assert checked.returncode == 0, checked.stderr
