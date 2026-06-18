"""Step 3 of the ADS memory-spaces work: type-checker semantics.

Module-kind context, space folding/residence, ADS<->ADS identity, and the
dereferenceability invariant. See docs/ads-implementation-plan.md Step 3.
"""
import unittest

from tests.support import typecheck_source


def _errs(result):
    return " ".join(str(e) for e in result.errors)


class TestSpaceRequiresDeviceModule(unittest.TestCase):
    def test_ads_space_type_outside_device_rejected(self):
        r = typecheck_source("MODULE M; TYPE p = ADS(GLOBAL) OF REAL; .")
        self.assertFalse(r.success)
        self.assertIn("DEVICE MODULE", _errs(r))

    def test_space_attribute_outside_device_rejected(self):
        r = typecheck_source("MODULE M; VAR [SPACE(GLOBAL)] g: REAL; .")
        self.assertFalse(r.success)
        self.assertIn("DEVICE MODULE", _errs(r))

    def test_ads_space_type_inside_device_accepted(self):
        r = typecheck_source("DEVICE MODULE M; TYPE p = ADS(GLOBAL) OF REAL; .")
        self.assertTrue(r.success, msg=_errs(r))

    def test_space_attribute_inside_device_accepted(self):
        r = typecheck_source("DEVICE MODULE M; VAR [SPACE(GLOBAL)] g: REAL; .")
        self.assertTrue(r.success, msg=_errs(r))


class TestSpaceIdentity(unittest.TestCase):
    def test_distinct_spaces_incompatible(self):
        # Assigning an ADS(GLOBAL) into an ADS(SHARED) slot must fail.
        src = ("DEVICE MODULE M;\n"
               "VAR [SPACE(GLOBAL)] g: REAL;\n"
               "    s: ADS(SHARED) OF REAL;\n"
               "PROCEDURE Q;\n"
               "BEGIN s := ADS g END;\n"
               ".")
        r = typecheck_source(src)
        self.assertFalse(r.success)

    def test_matching_spaces_compatible(self):
        src = ("DEVICE MODULE M;\n"
               "VAR [SPACE(GLOBAL)] g: REAL;\n"
               "    p: ADS(GLOBAL) OF REAL;\n"
               "PROCEDURE Q;\n"
               "BEGIN p := ADS g END;\n"
               ".")
        r = typecheck_source(src)
        self.assertTrue(r.success, msg=_errs(r))


class TestDereferenceabilityInvariant(unittest.TestCase):
    def test_device_pointer_deref_in_host_rejected(self):
        # ADS g where g is [SPACE(GLOBAL)] -> a device-space pointer; deref
        # outside a DEVICE MODULE is an error. (Build the pointer in a device
        # module type, deref in host.) Here we keep it within one host module:
        # the type carries GLOBAL but the module is host.
        r = typecheck_source(
            "DEVICE MODULE M; VAR [SPACE(GLOBAL)] g: REAL; p: ADS(GLOBAL) OF REAL;\n"
            "PROCEDURE Q; VAR x: REAL; BEGIN p := ADS g; x := p^ END; .")
        self.assertTrue(r.success, msg=_errs(r))

    def test_host_pointer_deref_in_device_rejected(self):
        # A plain ADS pointer defaults to HOST space; dereferencing it inside a
        # DEVICE MODULE violates the two-worlds invariant.
        r = typecheck_source(
            "DEVICE MODULE M; VAR g: REAL; p: ADS OF REAL;\n"
            "PROCEDURE Q; VAR x: REAL; BEGIN p := ADS g; x := p^ END; .")
        self.assertFalse(r.success)
        self.assertIn("HOST-space", _errs(r))

    def test_device_pointer_deref_inside_device_ok(self):
        r = typecheck_source(
            "DEVICE MODULE M; VAR [SPACE(SHARED)] g: REAL; p: ADS(SHARED) OF REAL;\n"
            "PROCEDURE Q; VAR x: REAL; BEGIN p := ADS g; x := p^ END; .")
        self.assertTrue(r.success, msg=_errs(r))


if __name__ == '__main__':
    unittest.main()
