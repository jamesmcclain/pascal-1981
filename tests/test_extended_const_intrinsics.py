"""Contract tests for extended-only intrinsic calls in CONST declarations."""

import pytest

from pascal1981.features import resolve_features
from tests.support import requires_exe, typecheck_source
from tests.test_codegen import build_and_run

EXTENDED = resolve_features("extended")


@requires_exe
@pytest.mark.xfail(strict=True, reason="extended CONST intrinsic parsing and folding are not implemented")
def test_extended_const_intrinsics_fold_nested_values_and_preserve_char_output():
    source = """
PROGRAM ExtendedConstIntrinsics(output);
CONST
  base = 65;
  initial = cHr(base);
  next = ChR(sUcC(oRd(initial)));
  previous = pReD(oRd(next));
VAR
  c: CHAR;
BEGIN
  c := next;
  WRITELN(initial);
  WRITELN(c);
  WRITELN(previous)
END.
"""
    returncode, stdout = build_and_run(source, features=EXTENDED)
    assert returncode == 0
    assert stdout == "A\nB\n65\n"


@pytest.mark.xfail(strict=True, reason="CONST intrinsic calls are still rejected by the parser")
def test_vintage_rejects_extended_const_intrinsics_after_parsing():
    result = typecheck_source("PROGRAM P; CONST n = ORD('A'); BEGIN END.")
    assert not result.success
    assert "extended-const-intrinsics" in " ".join(str(error) for error in result.errors)


@pytest.mark.xfail(strict=True, reason="extended CONST intrinsic validation is not implemented")
@pytest.mark.parametrize(
    "source, expected",
    [
        ("PROGRAM P; CONST n = ORD('A', 'B'); BEGIN END.", "ORD expects 1 argument"),
        ("PROGRAM P; CONST c = CHR('A'); BEGIN END.", "CHR"),
        ("PROGRAM P; CONST n = SUCC(32767); BEGIN END.", "outside"),
        ("PROGRAM P; CONST n = PRED(WRD(0)); BEGIN END.", "outside"),
    ],
)
def test_extended_const_intrinsics_reject_invalid_arguments_and_ordinal_endpoints(source, expected):
    result = typecheck_source(source, features=EXTENDED)
    assert not result.success
    assert expected in " ".join(str(error) for error in result.errors)
