"""Multi-file integration test: plain INTERFACE/IMPLEMENTATION via USES."""

import unittest

from tests.support import build_and_run_pascal_project, requires_exe

_INTERFACE = """INTERFACE;
UNIT mathbox (seed, add1, twice);
PROCEDURE seed;
FUNCTION add1(x: INTEGER): INTEGER;
FUNCTION twice(x: INTEGER): INTEGER;
END;
"""

_IMPLEMENTATION = """(*$INCLUDE:'mathbox.inc'*)
IMPLEMENTATION OF mathbox;
VAR
  bias: INTEGER;

PROCEDURE seed;
BEGIN
  bias := 1
END;

FUNCTION add1(x: INTEGER): INTEGER;
BEGIN
  add1 := x + bias
END;

FUNCTION twice(x: INTEGER): INTEGER;
BEGIN
  twice := x + x
END;
.
"""

_MAIN = """(*$INCLUDE:'mathbox.inc'*)
PROGRAM main(output);
USES mathbox;
BEGIN
  seed;
  WRITELN(add1(41));
  WRITELN(twice(21))
END.
"""

_EXPECTED = ["42", "42"]


@requires_exe
class TestHostUsesIntegration(unittest.TestCase):

    def test_plain_interface_implementation_builds_and_runs_via_uses(self):
        rc, out, err = build_and_run_pascal_project(
            files={
                'mathbox.inc': _INTERFACE,
                'mathbox.pas': _IMPLEMENTATION,
                'main.pas': _MAIN,
            },
            compile_pairs=[
                ('mathbox.inc', 'mathbox-interface.ll'),
                ('mathbox.pas', 'mathbox.ll'),
                ('main.pas', 'main.ll'),
            ],
            link_ir_relpaths=['mathbox.ll', 'main.ll'],
            exe_name='host-uses',
            link_flags=[],  # INPUT/OUTPUT ownership fix eliminates the collision
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual([line.strip() for line in out.splitlines() if line.strip()], _EXPECTED)


_INIT_INTERFACE = """INTERFACE;
UNIT counter (bump);
FUNCTION bump: INTEGER;
END;
"""

_INIT_IMPLEMENTATION = """(*$INCLUDE:'counter.inc'*)
IMPLEMENTATION OF counter;
VAR
  n: INTEGER;

FUNCTION bump: INTEGER;
BEGIN
  n := n + 1;
  bump := n
END;

BEGIN
  n := 41
END.
"""

_INIT_MAIN = """(*$INCLUDE:'counter.inc'*)
PROGRAM main(output);
USES counter;
BEGIN
  WRITELN(bump)
END.
"""


@requires_exe
class TestUnitInitializationCalledIntegration(unittest.TestCase):
    """A UNIT's INITIALIZATION (BEGIN..END) body must actually run before
    PROGRAM's own body -- pascal_init_<unit> being generated is not enough
    if nothing ever calls it."""

    def test_unit_initialization_body_runs_before_program_body(self):
        rc, out, err = build_and_run_pascal_project(
            files={
                'counter.inc': _INIT_INTERFACE,
                'counter.pas': _INIT_IMPLEMENTATION,
                'main.pas': _INIT_MAIN,
            },
            compile_pairs=[
                ('counter.inc', 'counter-interface.ll'),
                ('counter.pas', 'counter.ll'),
                ('main.pas', 'main.ll'),
            ],
            link_ir_relpaths=['counter.ll', 'main.ll'],
            exe_name='host-uses-init',
            link_flags=[],
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual([line.strip() for line in out.splitlines() if line.strip()], ["42"])


if __name__ == '__main__':
    unittest.main()
