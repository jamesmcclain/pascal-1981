"""Lazy host-runtime externs + INPUT/OUTPUT ownership — artifact-level guards.

Two orthogonal properties verified here:

1. **Zero-declare guarantee (lazy registration, §2.2.1)**
   A host PROGRAM / MODULE / UNIT that references no host-runtime extern must
   emit zero `declare` lines.  Previously impossible — the eager dump added ~40
   declares unconditionally.  This is the durable artifact check for the "dead
   extern is structurally impossible" invariant.

2. **INPUT/OUTPUT single-definition property (§4.1)**
   PROGRAM is the root compiland: it owns the strong global definitions @input
   and @output. MODULE and UNIT compilands emit external declarations only.
   When library objects are linked with a PROGRAM, exactly one strong definition
   of each exists.
"""

import os
import re
import shutil
import tempfile
import unittest

import llvmlite.ir as ir

from pascal1981.codegen import compile_to_llvm
from pascal1981.parser import parse_file
from pascal1981.type_checker import PascalTypeChecker
from tests.support import parse_source


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HOST_RUNTIME_NAMES = (
    'abort', 'fflush', 'memcpy', 'memset', 'memmove', 'printf',
    'movel', 'mover', 'movesl', 'movesr', 'fillc', 'fillsc',
    'pas_read_int', 'pas_read_word', 'pas_read_real', 'pas_read_char',
    'pas_read_lstring', 'pas_read_string', 'pas_readln_skip',
    'pas_write_fmt', 'pas_enum_write_token', 'pas_read_enum_name', 'pas_fread_enum_name', 'pabort',
    'pas_file_buffer', 'pas_file_touch_buffer',
    'pas_file_reset', 'pas_file_rewrite', 'pas_file_get', 'pas_file_put',
    'pas_file_close', 'pas_file_discard', 'pas_file_assign',
    'pas_file_attach_std', 'pas_file_eof', 'pas_file_eoln',
    'pas_fread_int', 'pas_fread_word', 'pas_fread_real', 'pas_fread_char',
    'pas_fread_lstring', 'pas_fread_string', 'pas_freadln_skip',
    'pas_freadset', 'pas_fread_filename',
    'malloc', 'free',
    'positn', 'scaneq', 'scanne', 'encode_value', 'decode_value',
    'sqrt', 'sin', 'cos', 'log', 'exp', 'atan',
)


def _compile(src, **kw):
    ast = parse_source(src)
    r = PascalTypeChecker().check(ast)
    assert r.success, r.errors
    return compile_to_llvm(ast, **kw)


def _compile_unit(iface_src, impl_src, module_name='U', **kw):
    tmpdir = tempfile.mkdtemp()
    try:
        iface_path = os.path.join(tmpdir, module_name.lower())
        impl_path  = os.path.join(tmpdir, f'{module_name.lower()}.pas')
        with open(iface_path, 'w') as f:
            f.write(iface_src)
        with open(impl_path, 'w') as f:
            f.write(impl_src)
        ast = parse_file(impl_path)
        r = PascalTypeChecker(source_file=impl_path).check(ast)
        assert r.success, r.errors
        return compile_to_llvm(ast, source_file=impl_path, **kw)
    finally:
        shutil.rmtree(tmpdir)


def _declares(ir_text):
    """Return names of all declared (external) functions in the IR."""
    return re.findall(r'declare\b.*?@"([^"]+)"', ir_text)


def _strong_globals(ir_text):
    """Return names of strongly-defined globals (not `external global`)."""
    return re.findall(r'^@"([^"]+)"\s*=\s*global\b', ir_text, re.MULTILINE)


def _extern_globals(ir_text):
    """Return names of externally-declared globals (`external global`)."""
    return re.findall(r'^@"([^"]+)"\s*=\s*external global\b', ir_text, re.MULTILINE)


# ---------------------------------------------------------------------------
# Property 1 — zero-declare guarantee
# ---------------------------------------------------------------------------

class TestZeroDeclareGuarantee(unittest.TestCase):
    """Programs / units with no host-runtime references emit zero declares."""

    def test_minimal_program_zero_declares(self):
        """The simplest possible PROGRAM (no I/O, no heap, no strings) must
        emit zero host-runtime `declare` lines.  This was impossible under the
        old eager dump which always added ~40 declares."""
        ir_text = _compile('PROGRAM P; VAR x: INTEGER; BEGIN x := 1 END.')
        found = [n for n in _declares(ir_text) if n in _HOST_RUNTIME_NAMES]
        self.assertEqual(found, [],
                         f'dead host-runtime declares in minimal program:\n{ir_text}')

    def test_minimal_module_zero_declares(self):
        """A MODULE with no body I/O likewise emits zero host-runtime declares."""
        ir_text = _compile('MODULE M; VAR x: INTEGER; .')
        found = [n for n in _declares(ir_text) if n in _HOST_RUNTIME_NAMES]
        self.assertEqual(found, [],
                         f'dead host-runtime declares in minimal module:\n{ir_text}')

    def test_empty_unit_zero_declares(self):
        """An empty unit implementation (no I/O, no heap) emits zero declares."""
        iface = 'INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n'
        impl  = "(*$INCLUDE:'u'*)\nIMPLEMENTATION OF U;\nPROCEDURE go;\nBEGIN END;\n.\n"
        ir_text = _compile_unit(iface, impl)
        found = [n for n in _declares(ir_text) if n in _HOST_RUNTIME_NAMES]
        self.assertEqual(found, [],
                         f'dead host-runtime declares in empty unit:\n{ir_text}')

    def test_unit_with_movel_emits_only_movel(self):
        """A unit that uses MOVEL (and nothing else from the runtime) must
        emit exactly one host-runtime declare: movel.  The other ~39 entries
        must be absent."""
        iface = 'INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n'
        impl  = ("(*$INCLUDE:'u'*)\n"
                 'IMPLEMENTATION OF U;\n'
                 'VAR a, b: ARRAY[1..4] OF CHAR;\n'
                 'PROCEDURE go;\nBEGIN MOVEL(ADR a, ADR b, WRD(4)) END;\n.\n')
        ir_text = _compile_unit(iface, impl)
        runtime_declares = [n for n in _declares(ir_text) if n in _HOST_RUNTIME_NAMES]
        self.assertEqual(runtime_declares, ['movel'],
                         f'expected only movel; got {runtime_declares}')


# ---------------------------------------------------------------------------
# Property 2 — INPUT/OUTPUT single-definition
# ---------------------------------------------------------------------------


    def test_runtime_extern_uses_factory_cache_without_module_scan(self):
        """Direct runtime_extern calls materialise once and return the cached object."""
        from pascal1981.codegen import Codegen

        cg = Codegen()
        first = cg.runtime_extern('pas_read_int')
        second = cg.runtime_extern('pas_read_int')
        self.assertIs(first, second)
        self.assertEqual([f.name for f in cg.module.functions].count('pas_read_int'), 1)

    def test_unknown_runtime_extern_fails_clearly(self):
        from pascal1981.codegen import Codegen
        from pascal1981.codegen.base import CodegenError

        cg = Codegen()
        with self.assertRaisesRegex(CodegenError, "unknown runtime extern 'bogus_runtime'"):
            cg.runtime_extern('bogus_runtime')

    def test_string_assignment_materialises_memcpy_and_memset_lazily(self):
        ir_text = _compile("PROGRAM P; VAR s: STRING(5); BEGIN s := 'ABCDE' END.")
        runtime_declares = [n for n in _declares(ir_text) if n in _HOST_RUNTIME_NAMES]
        self.assertIn('memcpy', runtime_declares)
        self.assertIn('memset', runtime_declares)

    def test_math_intrinsic_materialises_libm_lazily(self):
        ir_text = _compile('PROGRAM P; VAR r: REAL; BEGIN r := SQRT(4.0) END.')
        runtime_declares = [n for n in _declares(ir_text) if n in _HOST_RUNTIME_NAMES]
        self.assertEqual(runtime_declares, ['sqrt'])

    def test_runtime_check_materialises_abort_and_fflush_lazily(self):
        ir_text = _compile('PROGRAM P; VAR a: ARRAY[1..2] OF INTEGER; i: INTEGER; BEGIN i := 3; a[i] := 1 END.')
        runtime_declares = [n for n in _declares(ir_text) if n in _HOST_RUNTIME_NAMES]
        self.assertIn('abort', runtime_declares)
        self.assertIn('fflush', runtime_declares)


class TestInputOutputOwnership(unittest.TestCase):
    """PROGRAM owns INPUT/OUTPUT; MODULE/UNIT declare them externally."""

    def test_program_owns_input_output(self):
        """A PROGRAM emits strong global definitions for @input and @output."""
        ir_text = _compile('PROGRAM P; BEGIN END.')
        strong = _strong_globals(ir_text)
        self.assertIn('input',  strong, '@input should be a strong def in PROGRAM')
        self.assertIn('output', strong, '@output should be a strong def in PROGRAM')
        # Must NOT appear as external declarations
        extern = _extern_globals(ir_text)
        self.assertNotIn('input',  extern)
        self.assertNotIn('output', extern)

    def test_module_declares_input_output_externally(self):
        """A MODULE is library-like and does not own process-wide files."""
        ir_text = _compile('MODULE M; .')
        extern = _extern_globals(ir_text)
        self.assertIn('input',  extern, '@input should be external decl in MODULE')
        self.assertIn('output', extern, '@output should be external decl in MODULE')
        strong = _strong_globals(ir_text)
        self.assertNotIn('input',  strong)
        self.assertNotIn('output', strong)

    def test_unit_declares_input_output_externally(self):
        """A UNIT (interface + implementation) emits external declarations for
        @input and @output, not strong definitions — the linker resolves them
        to the root compiland's copies (§4.1)."""
        iface = 'INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n'
        impl  = "(*$INCLUDE:'u'*)\nIMPLEMENTATION OF U;\nPROCEDURE go;\nBEGIN END;\n.\n"
        ir_text = _compile_unit(iface, impl)
        extern = _extern_globals(ir_text)
        self.assertIn('input',  extern, '@input should be external decl in UNIT')
        self.assertIn('output', extern, '@output should be external decl in UNIT')
        # Must NOT appear as strong definitions
        strong = _strong_globals(ir_text)
        self.assertNotIn('input',  strong)
        self.assertNotIn('output', strong)

    def test_no_multiple_strong_defs_when_linking_program_and_unit(self):
        """Linking a PROGRAM IR with a UNIT IR has one owner of @input/@output."""
        prog_ir = _compile('PROGRAM P; BEGIN END.')
        iface = 'INTERFACE;\nUNIT U (go);\nPROCEDURE go;\nEND;\n'
        impl  = "(*$INCLUDE:'u'*)\nIMPLEMENTATION OF U;\nPROCEDURE go;\nBEGIN END;\n.\n"
        unit_ir = _compile_unit(iface, impl)

        prog_strong = _strong_globals(prog_ir)
        unit_strong = _strong_globals(unit_ir)
        unit_extern = _extern_globals(unit_ir)

        # Program owns them
        self.assertIn('input',  prog_strong)
        self.assertIn('output', prog_strong)
        # Unit does NOT re-define them
        self.assertNotIn('input',  unit_strong)
        self.assertNotIn('output', unit_strong)
        # Unit references them via external decl
        self.assertIn('input',  unit_extern)
        self.assertIn('output', unit_extern)

    def test_no_multiple_strong_defs_when_linking_program_and_module(self):
        """PROGRAM + separately compiled MODULE must not both define files."""
        prog_ir = _compile('PROGRAM P; BEGIN END.')
        module_ir = _compile('MODULE M; VAR x: INTEGER; .')

        prog_strong = _strong_globals(prog_ir)
        module_strong = _strong_globals(module_ir)
        module_extern = _extern_globals(module_ir)
        combined_strong = prog_strong + module_strong

        self.assertEqual(combined_strong.count('input'), 1)
        self.assertEqual(combined_strong.count('output'), 1)
        self.assertNotIn('input', module_strong)
        self.assertNotIn('output', module_strong)
        self.assertIn('input', module_extern)
        self.assertIn('output', module_extern)


if __name__ == '__main__':
    unittest.main()
