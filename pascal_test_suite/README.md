# IBM Pascal 2.0 parser test suite

Programs are categorized by what the **EBNF grammar** (the spec) dictates, not by
what the current parser happens to do. Run `./run_suite.sh <dir-with-parser.py>`;
any line marked `BUG` is a divergence from the grammar.

## should_pass/ — valid per grammar; an authentic parser MUST accept

| File | Exercises | Current parser |
|---|---|---|
| 01_not_in_factor | `a AND NOT b` (NOT lives in `factor`) | ❌ rejects — NOT placed at wrong precedence |
| 02_not_leading | `NOT a` at head of expr | ✅ accepts |
| 03_hex_constant | `$FF` hex integer | ❌ rejects — lexer has no `$` |
| 04_scientific_real | `1.5E10`, `6.022E+23` | ❌ rejects — lexer has no exponent |
| 05_vars_param | `VARS` / `CONSTS` params | ❌ rejects — only VAR/CONST handled |
| 06_extern_attribute | `[EXTERN]` attribute synonym | ❌ rejects — only EXTERNAL in attr set |
| 07_enum_type | `(RED, GREEN, BLUE)` | ❌ rejects — enum_type missing from `type` |
| 08_interface_uses | `USES` inside an interface unit | ❌ rejects — no uses loop in interface |
| 09_uses_rename | `USES a (x), b (y)` per-import rename | ❌ rejects — rename only on last item |
| 10_kitchen_sink | sets, ranges, CASE/OTHERWISE, VAR param | ✅ accepts |

## should_fail/ — invalid per grammar; an authentic parser MUST reject

| File | Why invalid | Current parser |
|---|---|---|
| 01_return_expression | `RETURN` takes no expression | ✅ rejects |
| 02_module_body | module has no compound stmt | ✅ rejects |
| 03_implementation_no_of | needs `OF identifier` | ✅ rejects |
| 04_implementation_stray_end | implementation has no `END` | ❌ accepts — stray END swallowed |
| 05_proc_missing_semicolon | trailing `;` after body required | ✅ rejects |
| 06_double_sign | at most one leading sign | ❌ accepts — sign recurses |
| 07_signed_char | sign is numeric-only | ❌ accepts — sign before char |
| 08_star_plain_array | `..*` is SUPER ARRAY only | ❌ accepts — `*` on plain array |
| 09_unterminated_begin | needs matching `END` | ✅ rejects |
| 10_bogus_attribute | unknown attribute name | ✅ rejects |

## judgment_calls/ — classification depends on a decision you haven't made

- **A_write_field_width** (`WRITELN(x:5:2)`): field-width `:w:d` is standard Pascal,
  but it is NOT in this grammar's `expression_list`. If you want it, extend the
  grammar (ideally scoped to the write family); otherwise it's an over-acceptance.
- **B_colon_args_any_call** (`FOO(1:2:3)`): the parser applies the same colon
  syntax to *every* call. Almost certainly should be rejected. Currently accepted.

Summary: 8 of 10 should_pass currently regress; 4 of 10 should_fail leak through.
