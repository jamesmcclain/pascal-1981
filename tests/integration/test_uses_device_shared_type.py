"""Tests for Fix C: import_symbols accepts DEVICE INTERFACE with shared TYPEs.

Previously, any interface whose body contained a TYPE (or CONST) declaration
alongside its exported routines would fail with "export list does not match its
declarations" because import_symbols counted all decls against the export list.

These tests verify:
  1. The type checker accepts USES of an interface that carries a TYPE section.
  2. The shared type name is visible in the importing scope.
  3. The too-many-aliases guard still fires correctly.
  4. A missing export name (export list names something not declared) still errors.

Full host-calls-device-kernel execution is deferred to Milestone D (host
orchestration); the CPU-device x86 path is used here for compile+link tests
that exercise the codegen path without requiring ADS parameters.
"""

import unittest

from tests.support import typecheck_module, requires_exe, build_and_run_pascal_project

# --- Shared-type visibility (type-checker level) ----------------------------

_IFACE_WITH_TYPE = """\
DEVICE INTERFACE;
UNIT FILLBUF (fill_buffer);
TYPE
  BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
PROCEDURE fill_buffer(outp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
END;
"""

# The host body itself uses only plain host types; device-dialect types
# (INTEGER32, ADS) live in the imported interface and are resolved under
# device context during import, not in the host body.
_HOST_USES_TYPE = """\
(*$INCLUDE:'fillbuf'*)
PROGRAM host_test (OUTPUT);
USES FILLBUF (fill_buffer);
BEGIN
END.
"""


class TestImportSymbolsWithSharedType(unittest.TestCase):

    def test_uses_interface_with_type_section_succeeds(self):
        """import_symbols must not reject an interface that has a TYPE section."""
        result = typecheck_module(
            iface_code=_IFACE_WITH_TYPE,
            prog_code=_HOST_USES_TYPE,
            module_name='fillbuf',
        )
        self.assertTrue(result.success, result.errors)

    def test_shared_type_visible_in_host_scope(self):
        """The shared TYPE from the interface is importable into the host scope."""
        # Host references BUFFER by name — succeeds if the type was imported.
        host = """\
(*$INCLUDE:'fillbuf'*)
PROGRAM host_test (OUTPUT);
USES FILLBUF (fill_buffer);
TYPE
  ALIAS = BUFFER;
BEGIN
END.
"""
        result = typecheck_module(
            iface_code=_IFACE_WITH_TYPE,
            prog_code=host,
            module_name='fillbuf',
        )
        self.assertTrue(result.success, result.errors)

    def test_too_many_import_aliases_still_errors(self):
        """Requesting more import aliases than exports is still an error."""
        iface = """\
DEVICE INTERFACE;
UNIT T (kernel_entry);
TYPE
  BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
PROCEDURE kernel_entry(outp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
END;
"""
        host = """\
(*$INCLUDE:'t'*)
PROGRAM P (OUTPUT);
USES T (alias1, alias2);
BEGIN
END.
"""
        result = typecheck_module(iface_code=iface, prog_code=host, module_name='t')
        self.assertFalse(result.success)

    def test_missing_export_declaration_still_errors(self):
        """Export list names a routine with no matching decl — must still error."""
        iface = """\
DEVICE INTERFACE;
UNIT T (nonexistent_proc);
TYPE
  BUFFER = SUPER ARRAY [0..*] OF INTEGER32;
PROCEDURE kernel_entry(outp: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
END;
"""
        host = """\
(*$INCLUDE:'t'*)
PROGRAM P (OUTPUT);
USES T;
BEGIN
END.
"""
        result = typecheck_module(iface_code=iface, prog_code=host, module_name='t')
        self.assertFalse(result.success)

    def test_plain_rename_import_still_works(self):
        """Plain USES with no TYPE in the interface still works (no regression)."""
        iface = """\
DEVICE INTERFACE;
UNIT T (kernel_entry);
PROCEDURE kernel_entry(outp: ADS(GLOBAL) OF ARRAY [0..255] OF INTEGER32; n: INTEGER32);
END;
"""
        host = """\
(*$INCLUDE:'t'*)
PROGRAM P (OUTPUT);
USES T (my_kernel);
BEGIN
END.
"""
        result = typecheck_module(iface_code=iface, prog_code=host, module_name='t')
        self.assertTrue(result.success, result.errors)


# --- Compile + link (no run): device unit with TYPE in interface, CPU path ----

_IFACE_SIMPLE = """\
DEVICE INTERFACE;
UNIT COUNTER (count_down);
TYPE
  RESULT = INTEGER32;
PROCEDURE count_down(n: INTEGER32);
END;
"""

_IMPL_SIMPLE = """\
(*$INCLUDE:'counter.inc'*)
DEVICE IMPLEMENTATION OF COUNTER;
PROCEDURE count_down(n: INTEGER32);
BEGIN
END;
.
"""

_HOST_SIMPLE = """\
(*$INCLUDE:'counter.inc'*)
PROGRAM host_test (OUTPUT);
USES COUNTER (count_down);
BEGIN
  count_down(3)
END.
"""


class TestUsesDeviceSharedTypeCompile(unittest.TestCase):

    @requires_exe
    def test_compile_links_device_unit_with_type_in_interface(self):
        """Full compile+link: device unit with a TYPE section in the interface."""
        rc, out, err = build_and_run_pascal_project(
            files={
                'counter.inc': _IFACE_SIMPLE,
                'counter.pas': _IMPL_SIMPLE,
                'host_test.pas': _HOST_SIMPLE,
            },
            compile_pairs=[
                ('counter.inc', 'counter-iface.ll'),
                ('counter.pas', 'counter.ll'),
                ('host_test.pas', 'host_test.ll'),
            ],
            link_ir_relpaths=['counter.ll', 'host_test.ll'],
            exe_name='host_test',
        )
        self.assertEqual(rc, 0, err)


if __name__ == '__main__':
    unittest.main()
