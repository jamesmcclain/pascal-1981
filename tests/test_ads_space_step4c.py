"""Closes two remaining ADS-value follow-ups:

  1. Mutual/indirect recursion in a DEVICE MODULE (call-graph cycle), not just
     direct self-calls.
  2. Residence of an in-routine [SPACE(s)] local: a device-space local is
     statically allocated in its address space (CUDA __shared__ style), not a
     stack alloca.
"""
import re
import unittest

from pascal1981.codegen import compile_to_llvm
from pascal1981.type_checker import PascalTypeChecker
from tests.support import parse_source


def _check(src):
    return PascalTypeChecker().check(parse_source(src))


def _compile(src, **kw):
    ast = parse_source(src)
    r = PascalTypeChecker().check(ast)
    assert r.success, r.errors
    return compile_to_llvm(ast, **kw)


class TestMutualRecursion(unittest.TestCase):
    # NOTE: a 2+ node cycle written across sibling routines needs FORWARD, which
    # the checker does not yet support (redeclaration is rejected). Mutual
    # recursion is therefore exercised via *nested* procedures (a nested routine
    # calling its enclosing one) for the end-to-end path, plus a unit-level test
    # of the cycle detector for longer cycles.
    def _err(self, src):
        r = _check(src)
        self.assertFalse(r.success)
        return ' '.join(str(e) for e in r.errors).lower()

    def test_nested_mutual_recursion_banned_in_device_module(self):
        src = ("DEVICE MODULE M;\n"
               "PROCEDURE outer;\n"
               "  PROCEDURE inner; BEGIN outer; END;\n"
               "BEGIN inner; END;\n"
               ".\n")
        self.assertIn('recursion', self._err(src))

    def test_nested_mutual_recursion_allowed_in_host_module(self):
        src = ("MODULE M;\n"
               "PROCEDURE outer;\n"
               "  PROCEDURE inner; BEGIN outer; END;\n"
               "BEGIN inner; END;\n"
               ".\n")
        r = _check(src)
        self.assertTrue(r.success, r.errors)

    def test_non_recursive_nesting_is_fine(self):
        # outer -> inner, no back-edge: allowed.
        src = ("DEVICE MODULE M;\n"
               "PROCEDURE outer;\n"
               "  PROCEDURE inner; BEGIN END;\n"
               "BEGIN inner; END;\n"
               ".\n")
        r = _check(src)
        self.assertTrue(r.success, r.errors)

    def test_detector_flags_a_three_node_cycle(self):
        # Longer cycles (a -> b -> c -> a) require FORWARD to write in source;
        # exercise the cycle detector directly to prove transitive detection.
        tc = PascalTypeChecker()
        tc.in_device_module = True
        tc._device_callgraph = {'A': [('B', None)], 'B': [('C', None)], 'C': [('A', None)]}
        errs = []
        tc.error = lambda m, n=None: errs.append(str(m))
        tc._detect_device_recursion()
        self.assertEqual(len(errs), 3)
        self.assertTrue(all('recursion' in e.lower() for e in errs))

    def test_detector_ignores_an_acyclic_graph(self):
        tc = PascalTypeChecker()
        tc.in_device_module = True
        tc._device_callgraph = {'A': [('B', None)], 'B': [('C', None)]}
        errs = []
        tc.error = lambda m, n=None: errs.append(str(m))
        tc._detect_device_recursion()
        self.assertEqual(errs, [])


class TestInRoutineResidence(unittest.TestCase):
    _SRC = ("DEVICE MODULE M;\n"
            "PROCEDURE k;\n"
            "VAR\n"
            "  [SPACE(SHARED)] tile: INTEGER;\n"
            "  t: ADS(SHARED) OF INTEGER;\n"
            "BEGIN\n"
            "  t := ADS tile;\n"
            "END;\n"
            ".\n")

    def test_shared_local_is_static_global_in_addrspace_three(self):
        ir = _compile(self._SRC, device_triple='nvptx64-nvidia-cuda')
        # The shared local is emitted as a statically-allocated addrspace(3)
        # global (name is function-prefixed), NOT a stack alloca.
        tile_line = next(l for l in ir.splitlines() if l.lstrip().startswith('@') and '.tile"' in l)
        self.assertIn('addrspace(3) global', tile_line)

    def test_ads_of_shared_local_has_no_punning(self):
        ir = _compile(self._SRC, device_triple='nvptx64-nvidia-cuda')
        body = ir.split('@"k"', 1)[1]
        self.assertNotIn('{i16*, i16}', body)
        # tile is not stack-allocated.
        self.assertNotRegex(body, r'alloca .*tile')
        self.assertRegex(body, r'store .*addrspace\(3\)')


if __name__ == '__main__':
    unittest.main()
