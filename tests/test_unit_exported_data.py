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


def _run(root, source, module, stdin=None, check=True):
    return subprocess.run(
        [sys.executable, "-m", f"pascal1981.{module}"] + ([str(source)] if module == "cli_lex" else ["--source-file", str(source), "--dialect", "extended"]),
        cwd=root,
        input=stdin,
        text=True,
        capture_output=True,
        check=check,
        env={
            **os.environ, "PYTHONPATH": str(ROOT / "src")
        },
    )


def _compile(root, source, last="cli_typecheck"):
    out = _run(root, source, "cli_lex").stdout
    out = _run(root, source, "cli_parse", stdin=out).stdout
    checked = _run(root, source, "cli_typecheck", stdin=out, check=False)
    if last == "cli_typecheck":
        return checked
    assert checked.returncode == 0, checked.stderr
    return _run(root, source, "cli_codegen", stdin=checked.stdout, check=False)


def test_explicit_export_list_still_imports_unlisted_types():
    """A UNIT's TYPE/CONST decls are vocabulary, not exports.

    ``jsonutil`` lists only routines in its export list, yet every caller
    needs its ``Str255`` type to spell those routines' arguments. Gating
    TYPE/CONST import on the export list breaks every importer.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "base.inc").write_text("""INTERFACE;
UNIT base (Ping);
TYPE T = LSTRING(255);
CONST Count = 2;
PROCEDURE Ping(x: T);
END;
""")
        source = root / "main.pas"
        source.write_text("""(*$INCLUDE:'base.inc'*)
PROGRAM main;
USES base;
VAR x: T;
BEGIN
  x := 'hi'; IF Count > 1 THEN Ping(x)
END.
""")
        checked = _compile(root, source)
        assert checked.returncode == 0, checked.stderr


def test_omitted_export_list_imports_routines_and_vars():
    """``UNIT name;`` exports the whole interface, routines and VARs alike.

    The importer must get a `declare` for each routine and an
    initializer-less `external global` for each VAR, so the linker binds
    them to the separately compiled IMPLEMENTATION.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "base.inc").write_text("""INTERFACE;
UNIT base;
VAR tally: INTEGER;
PROCEDURE Ping(x: INTEGER);
END;
""")
        source = root / "main.pas"
        source.write_text("""(*$INCLUDE:'base.inc'*)
PROGRAM main;
USES base;
BEGIN
  tally := 1; Ping(tally)
END.
""")
        result = _compile(root, source, last="cli_codegen")
        assert result.returncode == 0, result.stderr
        # llvmlite quotes global names, hence @"tally" rather than @tally.
        assert '@"tally" = external global' in result.stdout, result.stdout
        assert 'declare i32 @"Ping"' in result.stdout, result.stdout


def test_implementation_inherits_interface_uses():
    """An IMPLEMENTATION sees the units its own INTERFACE uses.

    cg_base.pas carries no USES of its own -- ``USES jsonutil`` sits in the
    spliced cg_base.inc -- yet the storage it defines is typed with jsonutil's
    Str255. Without inheriting the interface's USES the implementation cannot
    name the types of its own variables.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "vocab.inc").write_text("""INTERFACE;
UNIT vocab (Ping);
TYPE Token = LSTRING(64);
PROCEDURE Ping(x: Token);
END;
""")
        (root / "holder.inc").write_text("""INTERFACE;
UNIT holder;
USES vocab;
VAR current: Token;
END;
""")
        source = root / "holder.pas"
        source.write_text("""(*$INCLUDE:'vocab.inc'*)
(*$INCLUDE:'holder.inc'*)
IMPLEMENTATION OF holder;
VAR current: Token;
BEGIN
END.
""")
        result = _compile(root, source, last="cli_codegen")
        assert result.returncode == 0, result.stderr
        # The implementation owns the storage, so this is a definition.
        assert '@"current" = global' in result.stdout, result.stdout
