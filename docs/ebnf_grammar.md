# EBNF Grammar for IBM Pascal 2.0

Evidence grades used throughout this document:

- **[OBSERVED]** — verified by successful compilation through `pas1` and `pas2` to a valid `.obj`
  file, or confirmed by a specific, attributed compiler error message.
- **[DOCUMENTED]** — confirmed in the authoritative IBM Pascal Compiler (Aug 1981) manual but
  not yet independently verified by compilation test.
- **[INFERRED]** — a reasonable deduction from observed compiler behavior or manual text.

Where a production carries no annotation, it inherits the status of the surrounding section.

---

```ebnf
(* ═══════════════════════════════════════════════════════════════════
   COMPILATION UNIT STRUCTURE                               [OBSERVED]
   ═══════════════════════════════════════════════════════════════════ *)

compilation_unit =
      program_unit
    | module_unit
    | interface_unit
    | implementation_unit ;

program_unit =
    [ include_directive ]
    "PROGRAM" identifier [ "(" identifier_list ")" ] ";"
    [ uses_clause ]
    block "." ;

module_unit =
    [ include_directive ]
    "MODULE" identifier ";"
    [ uses_clause ]
    module_block "." ;

interface_unit =
    "INTERFACE" ";"
    "UNIT" identifier [ "(" identifier_list ")" ] ";"
    [ uses_clause ]
    interface_block
    [ "BEGIN" [ statement { ";" statement } [ ";" ] ] ]
    "END" ";" ;
    (* {BEGIN}- END ; : one END terminates the optional init block AND the
       interface. GRAPHI = "BEGIN END;", BASEPLOT = "END;". Never two ENDs. *)

implementation_unit =
    [ include_directive ]
    "IMPLEMENTATION" "OF" identifier ";"
    [ uses_clause ]
    implementation_block "." ;


(* ═══════════════════════════════════════════════════════════════════
   IMPORT CLAUSE                                            [OBSERVED]

   Both the sequential form (USES A; USES B;) and the comma-separated
   combined form (USES A, B;) are accepted by the compiler.
   Renaming by position is also supported: USES myunit ( local_name ).
   ═══════════════════════════════════════════════════════════════════ *)

uses_clause =
    "USES" uses_import { "," uses_import }
    { ";" "USES" uses_import { "," uses_import } }
    [ ";" ] ;

uses_import = identifier [ "(" identifier_list ")" ] ;


(* ═══════════════════════════════════════════════════════════════════
   BLOCKS
   ═══════════════════════════════════════════════════════════════════ *)

(* [OBSERVED] Full program block: declarations then compound statement. *)
block =
    { declaration_section }
    compound_stmt ;

(* [OBSERVED] Module block: declarations only, no compound statement. *)
module_block =
    { declaration_section } ;

(* [OBSERVED] Interface block: headers and declarations only. *)
interface_block =
    { interface_declaration } ;

(* [OBSERVED] Implementation block: declarations, optional initializer. *)
implementation_block =
    { declaration_section }
    [ compound_stmt ] ;


(* ═══════════════════════════════════════════════════════════════════
   DECLARATION SECTIONS
   ═══════════════════════════════════════════════════════════════════ *)

(* [OBSERVED] IBM Pascal allows declaration sections to appear in any
   order (unlike Standard Pascal, which requires LABEL, CONST, TYPE,
   VAR in fixed order). *)
declaration_section =
      const_decl
    | type_decl
    | var_decl
    | value_decl
    | label_decl
    | proc_decl
    | func_decl ;

interface_declaration =
      const_decl
    | type_decl
    | var_decl
    | label_decl
    | proc_decl_header
    | func_decl_header ;

(* [OBSERVED] *)
const_decl = "CONST" identifier "=" constant ";" { identifier "=" constant ";" } ;
type_decl  = "TYPE"  identifier "=" type ";"    { identifier "=" type ";" } ;
label_decl = "LABEL" label_id { "," label_id } ";" ;

(* [OBSERVED] Optional attribute section precedes the identifier list. *)
var_decl = "VAR" { var_item } ;
var_item = [ attribute_section ] identifier_list ":" type ";" ;

(* [OBSERVED] VALUE initializes variables after declaration. Both = and
   := are accepted as the separator. *)
value_decl =
    "VALUE" identifier ( "=" | ":=" ) constant ";"
    { identifier ( "=" | ":=" ) constant ";" } ;


(* ═══════════════════════════════════════════════════════════════════
   BRACKETED ATTRIBUTES                                     [OBSERVED]

   Placed immediately after the VAR keyword for variables, or after
   the procedure/function identifier (and return type for functions)
   before the final semicolon. Multiple attributes are comma-separated
   within a single bracket section.

   EXTERN and EXTERNAL are synonyms in attribute position (see note
   in Procedures section). Unrecognized attribute names cause
   error 310 "Attribute Expected".
   ═══════════════════════════════════════════════════════════════════ *)

attribute_section = "[" attribute_item { "," attribute_item } "]" ;

attribute_item =
      "READONLY"                    (* prevent write assignments; init via VALUE *)
    | "PUBLIC"                      (* export to linker globally *)
    | "STATIC"                      (* force static allocation *)
    | "EXTERN"                      (* synonym for EXTERNAL in attribute position *)
    | "EXTERNAL"                    (* reference identifier from another module *)
    | "PURE"                        (* declare routine side-effect-free *)
    | "OVERLAY"                     (* declare routine as overlay-loaded *)
    | "FORTRAN"                     (* use Fortran calling conventions *)
    | "ORIGIN" "(" constant ")" ;   (* bind EXTERN routine to absolute address *)

(* NOTE: Commas inside an attribute's parentheses (e.g. [ORIGIN(100,200)])
   are parsed as attribute separators, causing errors. All parameterized
   attributes take exactly one argument. *)

(* UNVERIFIED: PORT(addr) is recognized syntactically but rejected as
   "Attribute Invalid" in all tested contexts. Its intended use is unknown. *)


(* ═══════════════════════════════════════════════════════════════════
   PROCEDURES AND FUNCTIONS                                 [OBSERVED]

   EXTERN and EXTERNAL are complete semantic synonyms (documented three
   times in the compiler manual: lines 3637-3639, 8190, 8205-8206, 10469).
   They exist as dual forms for source portability across Pascal dialects.
   ORIGIN(addr) is orthogonal to both (manual lines 10448-10453).

   FORWARD constraint [OBSERVED]: When providing the body for a
   previously FORWARD-declared procedure or function, the actual
   declaration MUST omit the parameter list. Repeating the parameters
   causes error 320 "Previous Forward Skip Parameter List". The actual
   declaration takes the form:
       PROCEDURE identifier ";" block ";"
   The grammar's optional parameter list [ "(" parameter_list ")" ]
   accommodates this: simply omit the parameters in the actual declaration.
   ═══════════════════════════════════════════════════════════════════ *)

proc_decl =
    proc_decl_header ";"
    ( "EXTERN" ";"
    | "EXTERNAL" ";"
    | "FORWARD" ";"
    | block ";" ) ;

proc_decl_header =
    "PROCEDURE" identifier
    [ "(" parameter_list ")" ]
    [ attribute_section ] ;

func_decl =
    func_decl_header ";"
    ( "EXTERN" ";"
    | "EXTERNAL" ";"
    | "FORWARD" ";"
    | block ";" ) ;

func_decl_header =
    "FUNCTION" identifier
    [ "(" parameter_list ")" ]
    ":" type
    [ attribute_section ] ;

(* [OBSERVED] Parameter modes: VAR/CONST are near references; VARS/CONSTS are
   far/segmented reference forms. *)
parameter_list  = parameter_group { ";" parameter_group } ;
parameter_group = [ "VAR" | "CONST" | "VARS" | "CONSTS" ] identifier_list ":" type ;


(* ═══════════════════════════════════════════════════════════════════
   STATEMENTS AND CONTROL FLOW
   ═══════════════════════════════════════════════════════════════════ *)

compound_stmt = "BEGIN" [ statement { ";" statement } [ ";" ] ] "END" ;

statement =
      assignment
    | proc_call
    | compound_stmt
    | if_stmt
    | for_stmt
    | repeat_stmt
    | while_stmt
    | case_stmt
    | with_stmt
    | goto_stmt
    | label_stmt
    | break_stmt
    | cycle_stmt
    | return_stmt
    | empty_stmt ;

empty_stmt  = (* empty *) ;

(* [OBSERVED] *)
if_stmt     = "IF" boolexp "THEN" statement [ "ELSE" statement ] ;
for_stmt    = "FOR" [ "STATIC" ] identifier ":=" expression ( "TO" | "DOWNTO" ) expression "DO" statement ;
repeat_stmt = "REPEAT" [ statement { ";" statement } [ ";" ] ] "UNTIL" boolexp ;
while_stmt  = "WHILE" boolexp "DO" statement ;

(* ── CASE statement ────────────────────────────────────────────────
   [OBSERVED] The index expression must be an ordinal type (INTEGER,
   BOOLEAN, CHAR, or user-defined enumerated). Constants can be single
   values or ranges (n..m). The OTHERWISE clause is optional and
   catches all unmatched values. Closes with END (not END CASE).
   ─────────────────────────────────────────────────────────────────── *)
case_stmt =
    "CASE" ordinal_expr "OF"
    case_element { ";" case_element } [ ";" ]
    [ "OTHERWISE" statement ]
    "END" ;

case_element       = case_constant_list ":" statement ;
case_constant_list = case_constant { "," case_constant } ;
case_constant      =
      constant                  (* single value:  1, TRUE, 'A'  *)
    | constant ".." constant ;  (* range:         1..3, 4..6    *)
ordinal_expr       = expression ; (* semantic constraint: must be ordinal type *)

(* ── WITH statement ─────────────────────────────────────────────────
   [OBSERVED] The target is a full designator (not just a bare
   identifier): field access (.), array subscripts ([]), and pointer
   dereferences (^) are all valid. Multiple comma-separated targets
   are supported, equivalent to nested WITH statements.
   See WITH.md for the formal compiler BNF recovery (line 20574).
   ─────────────────────────────────────────────────────────────────── *)
with_stmt  = "WITH" designator { "," designator } "DO" statement ;

(* ── GOTO statement ─────────────────────────────────────────────────
   [OBSERVED] The compiler enforces label visibility at the procedure
   boundary: cross-procedure jumps are rejected with error 129
   "Label Not Encountered".

   [DOCUMENTED vs OBSERVED DISCREPANCY] The manual states that jumping
   INTO a structured statement (IF, WHILE, FOR, etc.) is illegal.
   Empirical testing found the compiler does NOT enforce this at the
   pas1 level — such jumps compile without error. Behavior at runtime
   is undefined and should be avoided in portable code.
   ─────────────────────────────────────────────────────────────────── *)
goto_stmt  = "GOTO" label_id ;
label_stmt = label_id ":" statement ;

(* [OBSERVED] IBM Pascal control-flow extensions. *)
break_stmt  = "BREAK" [ label_id ] ;   (* exit enclosing loop; optional labeled target *)
cycle_stmt  = "CYCLE" [ label_id ] ;   (* continue to next loop iteration; optional labeled target *)

(* [OBSERVED] RETURN is a pure control-flow jump to the exit point of
   the current procedure, function, or program. It does NOT accept an
   expression. Functions return values via assignment to the function
   name (e.g. f := 42). Attempting RETURN expr causes syntax
   errors 185/186. *)
return_stmt = "RETURN" ;

assignment = designator ":=" expression ;
proc_call  = identifier [ "(" expression_list ")" ]
           | ( "WRITE" | "WRITELN" ) [ "(" write_arg_list ")" ] ;
write_arg_list = write_arg { "," write_arg } ;
write_arg      = expression [ ":" expression [ ":" expression ] ] ;


(* ═══════════════════════════════════════════════════════════════════
   EXPRESSIONS
   ═══════════════════════════════════════════════════════════════════ *)

boolexp     = expression { ( "AND" "THEN" | "OR" "ELSE" ) expression } ;
expression  = simple_expr [ rel_op simple_expr ] ;
simple_expr = [ "+" | "-" ] term { add_op term } ;
term        = factor { mul_op factor } ;

factor =
      designator
    | constant
    | string_literal
    | function_call
    | "(" expression ")"
    | "NOT" factor                              (* unary boolean negation            *)
    | "ADR" identifier                        (* 16-bit near offset of identifier *)
    | "ADS" identifier                        (* segmented address; LLVM lowers segment to 0 *)
    | "SIZEOF" "(" ( identifier | type ) ")"  (* byte size of identifier or type  *)
    | "UPPER" "(" identifier ")"              (* upper bound of super array        *)
    | set_constructor ;

(* [OBSERVED] A designator is an identifier followed by zero or more
   selector steps. Used in both expression and WITH contexts. *)
designator = identifier { selector } ;
selector   = "[" expression "]"   (* array subscript  *)
           | "." identifier       (* field access      *)
           | "^" ;                (* pointer deref     *)

function_call    = identifier "(" [ expression_list ] ")" ;
expression_list  = expression { "," expression } ;

(* [OBSERVED] Set constructors support both single elements and ranges.
   Example: [1..3, 7, 9..15] *)
set_constructor = "[" [ set_element { "," set_element } ] "]" ;
set_element     = expression [ ".." expression ] ;

rel_op = "=" | "<>" | "<" | "<=" | ">" | ">=" | "IN" ;
add_op = "+" | "-" | "OR" | "XOR" ;
mul_op = "*" | "/" | "DIV" | "MOD" | "AND" ;


(* ═══════════════════════════════════════════════════════════════════
   TYPES
   ═══════════════════════════════════════════════════════════════════ *)

type =
      simple_type
    | subrange_type
    | enum_type
    | array_type
    | super_array_type
    | record_type
    | pointer_type
    | set_type
    | file_type
    | lstring_type
    | type_designator   (* super array instantiation, e.g. VECT(10) *)
    | identifier ;      (* named type *)

(* [OBSERVED] *)
simple_type = "INTEGER" | "REAL" | "BOOLEAN" | "CHAR" | "WORD" | "ADRMEM" ;

(* [ADDED] Enumerated type. Values are zero-indexed ordinals introduced
   into the current scope as named constants of the declared type.
   Example: TYPE color = (red, green, blue);
   ORD(red) = 0, ORD(green) = 1, ORD(blue) = 2.
   Enumerated values satisfy the identifier alternative in constant,
   making them usable as CASE selectors and SET base types. *)
enum_type = "(" identifier { "," identifier } ")" ;

subrange_type = constant ".." constant ;

(* [OBSERVED] *)
array_type       = [ "PACKED" ] "ARRAY" "[" index_range "]" "OF" type ;
super_array_type = "SUPER" "ARRAY" "[" constant ".." "*" "]" "OF" type ;

(* [OBSERVED] type_designator instantiates a super array type to a
   fixed size. Valid anywhere a type is expected, including TYPE
   declarations, VAR declarations, and record field declarations.
   Example: VAR v : VECT(10);  or  TYPE v10 = VECT(10); *)
type_designator = identifier "(" constant ")" ;

(* [OBSERVED] IBM Pascal allows any ordering of field declarations.
   type_designator is valid as a field type. *)
record_type = [ "PACKED" ] "RECORD" field_list "END" ;
field_list  = field_decl { ";" field_decl } [ ";" ] ;
field_decl  = identifier_list ":" type ;

pointer_type = "^" type
             | "ADR" "OF" type
             | "ADS" "OF" type ;

(* [OBSERVED] SET OF is fully implemented. Small sets (ordinal values
   0..15) are generated inline; larger sets (up to 0..255) use runtime
   routines. Sets with maximum ORD value > 255 are not permitted.
   The base type may be a subrange, a named ordinal type (CHAR,
   BOOLEAN, user-defined enumerated), or an anonymous subrange. *)
set_type = "SET" "OF" ( index_range | identifier ) ;

(* [DOCUMENTED] FILE OF is fully specified with GET, PUT, READ, WRITE,
   RESET, REWRITE semantics. Compilation to .obj is confirmed.
   Runtime I/O is unverified due to linker library path issue. *)
file_type = "FILE" "OF" type ;

lstring_type = "LSTRING" "(" constant ")" ;


(* ═══════════════════════════════════════════════════════════════════
   LEXICAL RULES                                              [ADDED]

   CASE INSENSITIVITY
   All keywords and identifiers are case-insensitive. BEGIN, Begin,
   and begin are identical tokens. The grammar is written in upper
   case for keywords; the letter primitive below matches both cases.

   WHITESPACE
   Space (0x20), horizontal tab (0x09), carriage return (0x0D), and
   line feed (0x0A) are non-significant between tokens. At least one
   whitespace character or comment must separate any two consecutive
   identifier or keyword tokens.

   COMMENTS
   Two comment forms are recognised and are equivalent:
     (* ... *)   parenthesis-star
     { ... }     brace
   Comments may appear wherever whitespace is legal. Comments do NOT
   nest; the first matching close delimiter ends the comment
   regardless of any embedded opening delimiters.

   DIRECTIVE DISAMBIGUATION
   A "{" immediately followed by "$" begins a compiler directive
   (see include_directive below), not a comment. The scanner must
   check for "$" before entering brace-comment-skipping mode.
   ═══════════════════════════════════════════════════════════════════ *)


(* ═══════════════════════════════════════════════════════════════════
   LEXICAL DIRECTIVES AND HELPERS
   ═══════════════════════════════════════════════════════════════════ *)

include_directive =
    ( "(*$INCLUDE:" | "{$INCLUDE:" )
    "'" filename "'"
    ( "*)" | "}" ) ;

filename = letter { letter | digit | "_" | "." | ":" | "\" | "/" } ;

identifier_list = identifier { "," identifier } ;
index_range     = constant ".." constant ;
label_id        = digit { digit } ;
identifier      = letter { letter | digit | "_" } ;

(* [ADDED] Sign prefix applies to numeric constants only.
   NIL is a typed null-pointer constant compatible with any pointer type.
   identifier covers names introduced by CONST declarations and values of
   enumerated types. boolean_constant and NIL are listed before identifier
   to ensure the predefined names TRUE, FALSE, and NIL are matched as
   constants rather than as generic identifiers. *)
constant =
      [ "+" | "-" ] integer_constant
    | [ "+" | "-" ] real_constant
    | char_constant
    | boolean_constant
    | "NIL"
    | identifier ;

(* Decimal and radix integer forms. Radix uses the manual n#digits form,
   e.g. 16#FF; radix_digits is case-insensitive.
   NOTE: the `$FF` hex form was previously accepted as an implementer-added
   compatibility extension but has been removed — it is not attested in the
   IBM Pascal 2.0 manual, whose only hexadecimal notation is the radix form. *)
integer_constant = decimal_integer | radix_integer ;
decimal_integer  = digit { digit } ;
radix_integer    = digit { digit } "#" radix_digits ;
radix_digits     = hex_digit { hex_digit } ;
hex_digit        = digit | "A" | "B" | "C" | "D" | "E" | "F" ;

(* [ADDED] Optional exponent suffix for scientific notation, e.g. 1.5E10, 6.022E+23. *)
real_constant = digit { digit } "." digit { digit } [ exponent ] ;
exponent      = ( "E" | "e" ) [ "+" | "-" ] digit { digit } ;

char_constant    = "'" character "'" ;
boolean_constant = "TRUE" | "FALSE" ;

(* [OBSERVED] A literal single-quote character within a string is
   represented by two adjacent single quotes.
   Example: 'It''s' represents the string:  It's
   Manual reference: IBM_Pascal_Compiler_Aug81_djvu.txt, line 5684. *)
string_literal = "'" { character | "''" } "'" ;

(* [ADDED] Primitive character classes referenced throughout the grammar.
   All letter matches are case-insensitive (see Lexical Rules above). *)
letter    = "A" | "B" | "C" | "D" | "E" | "F" | "G" | "H" | "I"
          | "J" | "K" | "L" | "M" | "N" | "O" | "P" | "Q" | "R"
          | "S" | "T" | "U" | "V" | "W" | "X" | "Y" | "Z" ;

digit     = "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" ;

character = (* any printable ASCII character in the range 0x20–0x7E,
               or horizontal tab (0x09); the null byte is excluded *) ;
```

---

## Change Log

| Change | Basis | Evidence |
|---|---|---|
| `uses_clause` — corrected to support comma-separated form (`USES A, B`) | OBSERVED | Compilation test (T_UCOMB.PAS) |
| `uses_import` — replaces `uses_item`; `USES` keyword moved to `uses_clause` | OBSERVED | Compilation test |
| `with_stmt` — target changed from `identifier { selector }` to `designator` | OBSERVED | Manual line 20574; WITH.md |
| Duplicate `selector` production removed from `with_stmt` section; unified under expressions | Structural | — |
| `set_type` — base type expanded from `index_range` to `( index_range \| identifier )` | OBSERVED | Compilation tests (T_SET1–T_SET3) |
| `set_constructor` — element changed from `expression` to `set_element` supporting ranges | DOCUMENTED | Manual lines 9069–9073 |
| `set_element` — new production for `expression [ ".." expression ]` | DOCUMENTED | Manual lines 9069–9073 |
| `return_stmt` — comment expanded with error numbers 185/186 | OBSERVED | Compilation test (T_RET.PAS) |
| `goto_stmt` — comment added noting enforcement discrepancy | OBSERVED | Compilation test (error 129); manual lines 9606–9610 |
| `string_literal` — production corrected to `"''"` escape; comment added citing line 5684 | OBSERVED | Compilation test (T_STR.PAS) |
| `proc_decl` — FORWARD constraint comment added (error 320, parameter omission) | OBSERVED | Compilation test (T_FWRD.PAS) |
| `attribute_item` — added `"EXTERN"` as synonym for `"EXTERNAL"` in attribute position | DOCUMENTED | Manual lines 8197–8206 |
| `type_designator` — note added confirming validity in record field declarations | OBSERVED | Compilation test (T_TDREC.PAS) |
| `enum_type` — new production added; `type` updated to include it as an alternative | ADDED (Pillar 1) | Standard Pascal; semantics required by `CASE` and `set_type` |
| `constant` — sign prefix `[ "+" \| "-" ]` added for numeric constants | ADDED (Pillar 1) | Required for subrange bounds, `VALUE` initializers, `CASE` arms |
| `constant` — `"NIL"` alternative added as null-pointer constant | ADDED (Pillar 1) | Required for any pointer-type code |
| `constant` — `identifier` alternative added for named constants and enum values | ADDED (Pillar 1) | Standard Pascal `CONST`-declared names; enum member names |
| `integer_constant` — split into `decimal_integer` and `hex_integer` (`$FF` form) | ADDED (Pillar 1) | Required for systems-level and hardware-address literals |
| `integer_constant` — `radix_integer` (`n#digits`) added as the manual's hex/radix notation | DOCUMENTED | Manual `number` production (appendix F-4) |
| `integer_constant` — `hex_integer` (`$FF`) **removed**; not attested in the manual, superseded by radix form | OBSERVED | Manual shows only the `#`-radix form; `$` notation absent |
| `real_constant` — `exponent` suffix added for scientific notation (`1.5E10`) | ADDED (Pillar 1) | Standard Pascal requirement |
| `factor` — `"NOT" factor` alternative added for unary boolean negation | ADDED (Pillar 1) | `NOT` was entirely absent from grammar |
| Lexical Rules section added: case-insensitivity, whitespace, comment forms, directive disambiguation | ADDED (Pillar 1) | Unspecified in original grammar |
| `letter`, `digit`, `character`, `hex_digit`, `exponent` productions defined | ADDED (Pillar 1) | Primitives referenced throughout grammar but never defined |

---

## Remaining Unverified Items

| Area | Status | Recommended Test |
|---|---|---|
| `CASE` with `CHAR` index type | INFERRED | `CASE ch OF 'A': ... 'B': ...` |
| `CASE` with user-defined enumerated index | INFERRED | Declare enum type, CASE on it |
| `CASE` duplicate constant values | INFERRED (error) | Two arms with same constant |
| `CASE` with multiple statements per arm | INFERRED (needs BEGIN..END) | `1: BEGIN s1; s2 END` |
| `WITH` chained complex selectors at runtime | INFERRED | `WITH arr[i].field^ DO` — needs executable |
| `USES` initialization order | DOCUMENTED | Requires two units with `BEGIN` blocks |
| `USES` selective import (partial list) | UNVERIFIED | `USES u ( a )` when u exports `(a, b)` |
| `GOTO` into structured statement (runtime) | OBSERVED (compiles); UNVERIFIED (runtime) | Link and run T_GBAD.EXE |
| `FILE OF` runtime I/O | DOCUMENTED | Requires linker library path fix |
| Large set runtime behavior (> 15 elements) | OBSERVED (compiles); UNVERIFIED (runtime) | Link and run T_SET2.EXE |
| `FORWARD` with function return type propagation | INFERRED | Forward-declared FUNCTION with consumers |
