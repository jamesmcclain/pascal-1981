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

_IMPLEMENTATION = """IMPLEMENTATION OF mathbox;
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

_MAIN = """PROGRAM main(output);
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
                'mathbox': _INTERFACE,
                'mathbox.pas': _IMPLEMENTATION,
                'main.pas': _MAIN,
            },
            compile_pairs=[
                ('mathbox', 'mathbox-interface.ll'),
                ('mathbox.pas', 'mathbox.ll'),
                ('main.pas', 'main.ll'),
            ],
            link_ir_relpaths=['mathbox.ll', 'main.ll'],
            exe_name='host-uses',
            link_flags=['-Wl,--allow-multiple-definition'],
        )
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual([line.strip() for line in out.splitlines() if line.strip()], _EXPECTED)


if __name__ == '__main__':
    unittest.main()
