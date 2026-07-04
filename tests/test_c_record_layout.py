"""The C record-layout guarantee (docs/c-abi-foreign-functions.md, "Record
layout across the C boundary").

A Pascal RECORD whose fields are C-representable scalars, pointers, and fixed
arrays is laid out exactly like the corresponding C struct on the host triple:
same field offsets (natural alignment, implicit padding included) and same
total size (tail padding included, which SIZEOF reports).  That is what makes
it sound to declare a third-party C struct (a libpng ``png_image``, a
``struct timeval``, ...) as a Pascal RECORD and pass it *by pointer* to an
unmodified C function through a ``[C] EXTERN`` declaration.

These tests are differential against clang, in the same spirit as the Phase 2
aggregate-classifier validation: the C side checks its own ``offsetof``/
``sizeof`` against values the Pascal side computed, and writes through the
struct so field-address agreement is proven by value round-trips, not just
arithmetic.
"""

import unittest

from pascal1981.features import extended_features
from tests.support import build_and_run_pascal_project, requires_exe

EXT = extended_features()


@requires_exe
class TestCRecordLayout(unittest.TestCase):
    def _run(self, files, exe):
        pas = [f for f in files if f.endswith('.pas')]
        assert len(pas) == 1
        rc, out, err = build_and_run_pascal_project(
            files=files, compile_pairs=[(pas[0], 'p.ll')],
            link_ir_relpaths=['p.ll'] + [f for f in files if f.endswith('.c')],
            exe_name=exe, features=EXT)
        return rc, out, err

    def test_mixed_alignment_offsets_and_sizeof_match_clang(self):
        # {char; int32; char; int64}: implicit padding after each char.
        # Expected System V layout: offsets 0, 4, 8, 16; sizeof 24.
        # The C side verifies offsetof/sizeof and stamps every field; the
        # Pascal side checks the stamped values land in the right fields.
        src = ("PROGRAM P(output);\n"
               "TYPE mixed = RECORD\n"
               "  c1: CHAR; i: CINT; c2: CHAR; l: CLONG\n"
               "END;\n"
               "FUNCTION check_and_stamp(VAR m: mixed; pas_size: CINT): CINT [C]; EXTERN;\n"
               "VAR m: mixed; ok: CINT;\n"
               "BEGIN\n"
               "  ok := check_and_stamp(m, SIZEOF(m));\n"
               "  WRITELN(ok, ' ', m.c1, ' ', m.i, ' ', m.c2, ' ', m.l)\n"
               "END.")
        c = ("#include <stdint.h>\n"
             "#include <stddef.h>\n"
             "struct mixed { char c1; int32_t i; char c2; int64_t l; };\n"
             "int32_t check_and_stamp(struct mixed *m, int32_t pas_size) {\n"
             "  if (offsetof(struct mixed, c1) != 0) return -1;\n"
             "  if (offsetof(struct mixed, i)  != 4) return -2;\n"
             "  if (offsetof(struct mixed, c2) != 8) return -3;\n"
             "  if (offsetof(struct mixed, l)  != 16) return -4;\n"
             "  if (sizeof(struct mixed) != 24) return -5;\n"
             "  if (pas_size != (int32_t)sizeof(struct mixed)) return -6;\n"
             "  m->c1 = 'A'; m->i = 12345; m->c2 = 'Z'; m->l = 9000000000;\n"
             "  return 1;\n"
             "}\n")
        rc, out, err = self._run({'p.pas': src, 'impl.c': c}, 'record-layout-mixed')
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.split(), ['1', 'A', '12345', 'Z', '9000000000'])

    def test_pointer_uints_and_char_array_fields_png_image_shape(self):
        # The shape that motivated the guarantee: libpng's simplified-API
        # png_image is {pointer; five uint32; uint32; char[64]}.  WORD32
        # spells png_uint_32; the trailing CHAR array is the message buffer.
        src = ("PROGRAM P(output);\n"
               "TYPE imagerec = RECORD\n"
               "  opaque: ADRMEM;\n"
               "  version: WORD32; width: WORD32; height: WORD32;\n"
               "  format: WORD32; flags: WORD32; colormap_entries: WORD32;\n"
               "  warning_or_error: WORD32;\n"
               "  message: ARRAY[0..63] OF CHAR\n"
               "END;\n"
               "FUNCTION check_image(VAR im: imagerec; pas_size: CINT): CINT [C]; EXTERN;\n"
               "VAR im: imagerec; ok: CINT;\n"
               "BEGIN\n"
               "  im.width := 640; im.height := 360;\n"
               "  ok := check_image(im, SIZEOF(im));\n"
               "  WRITELN(ok, ' ', im.version, ' ', im.message[0], im.message[1])\n"
               "END.")
        c = ("#include <stdint.h>\n"
             "#include <stddef.h>\n"
             "struct imagerec {\n"
             "  void *opaque;\n"
             "  uint32_t version, width, height, format, flags, colormap_entries;\n"
             "  uint32_t warning_or_error;\n"
             "  char message[64];\n"
             "};\n"
             "int32_t check_image(struct imagerec *im, int32_t pas_size) {\n"
             "  if (pas_size != (int32_t)sizeof(struct imagerec)) return -1;\n"
             "  if (offsetof(struct imagerec, version) != 8) return -2;\n"
             "  if (offsetof(struct imagerec, message) != 36) return -3;\n"
             "  if (im->width != 640 || im->height != 360) return -4;\n"
             "  im->version = 1;\n"
             "  im->message[0] = 'o'; im->message[1] = 'k';\n"
             "  return 1;\n"
             "}\n")
        rc, out, err = self._run({'p.pas': src, 'impl.c': c}, 'record-layout-png')
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.split(), ['1', '1', 'ok'])

    def test_nested_record_and_byte_fields(self):
        # A nested record and 8-bit integer fields (WORD8/INTEGER8) keep the
        # same layout as nested C structs with uint8_t/int8_t members.
        src = ("PROGRAM P(output);\n"
               "TYPE inner = RECORD a: WORD8; b: INTEGER8; w: WORD END;\n"
               "     outer = RECORD tag: CHAR; pair: inner; count: CINT END;\n"
               "FUNCTION check_nested(VAR o: outer; pas_size: CINT): CINT [C]; EXTERN;\n"
               "VAR o: outer; ok: CINT;\n"
               "BEGIN\n"
               "  ok := check_nested(o, SIZEOF(o));\n"
               "  WRITELN(ok, ' ', o.pair.a, ' ', o.pair.b, ' ', o.count)\n"
               "END.")
        c = ("#include <stdint.h>\n"
             "#include <stddef.h>\n"
             "struct inner { uint8_t a; int8_t b; uint16_t w; };\n"
             "struct outer { char tag; struct inner pair; int32_t count; };\n"
             "int32_t check_nested(struct outer *o, int32_t pas_size) {\n"
             "  if (pas_size != (int32_t)sizeof(struct outer)) return -1;\n"
             "  if (offsetof(struct outer, pair) != 2) return -2;\n"
             "  if (offsetof(struct outer, count) != 8) return -3;\n"
             "  o->pair.a = 200; o->pair.b = -7; o->count = 42;\n"
             "  return 1;\n"
             "}\n")
        rc, out, err = self._run({'p.pas': src, 'impl.c': c}, 'record-layout-nested')
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(out.split(), ['1', '200', '-7', '42'])


if __name__ == '__main__':
    unittest.main()
