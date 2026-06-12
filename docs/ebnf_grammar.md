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
    | string_type
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

(* [OBSERVED] SET OF is implemented end-to-end (checklist 9.6). All sets
   use one fixed representation: a 256-bit bitvector (four i64 words), so
   element ORD values must be 0..255. Constant constructors fold at compile
   time; non-constant elements and ranges (e.g. [i, lo..hi]) are built at
   runtime. IN, union (+), intersection (*), difference (-), and the set
   comparisons all lower over this representation. The base type may be an
   anonymous subrange (SET OF 1..10, SET OF 'A'..'Z'), a named-constant
   subrange (SET OF lo..hi), or a named ordinal type (CHAR, BOOLEAN, or a
   user-defined enumerated type). *)
set_type = "SET" "OF" ( index_range | identifier ) ;

(* [DOCUMENTED] FILE OF is fully specified with GET, PUT, READ, WRITE,
   RESET, REWRITE semantics. Compilation to .obj is confirmed.
   Vintage-toolchain runtime I/O is unverified due to linker library path
   issue (the grade above is about the original pas1/pas2 compiler).
   Reimplementation status: the file runtime is implemented and run-tested
   — FCB/buffer-variable model, RESET/REWRITE/GET/PUT, ASSIGN/CLOSE/
   DISCARD, READSET/READFN, EOF/EOLN, FILEMODES/F.MODE, and read/write
   mode enforcement; see checklist Section 8. TEXT, FILEMODES, and FCBFQQ
   are predeclared identifiers, not grammar productions. *)
file_type = "FILE" "OF" type ;

(* [OBSERVED] STRING(n) is fixed-length string storage (PACKED ARRAY [1..n] OF CHAR):
   - bytes [0..n-1] contain characters, no length prefix
   - blank-padded (0x20) on assignment; write outputs all n chars
   - lowered as inline aggregate [n x i8]
   - ADR points to byte 0; SIZEOF = n
   
   LSTRING(n) is length-prefixed string storage (PACKED ARRAY [0..n] OF CHAR):
   - byte [0] = current length (0..n, max n = 255 per manual §5-11, §6-17)
   - bytes [1..n] = characters
   - null-terminated at byte [len+1] for libc convenience
   - lowered as inline aggregate [n+1 x i8]
   - ADR points to byte 0 (the length); SIZEOF = n+1
   
   Both pass as references (super-array semantics, not pointer-to-side-buffer).
   Assignment overflow (src_len > n) emits range-check error, not silent truncate.
   The bare identifier STRING is also predeclared as a type name. *)
string_type  = "STRING"  "(" constant ")" ;      (* PACKED ARRAY [1..n] OF CHAR, inline [n x i8] *)
lstring_type = "LSTRING" "(" constant ")" ;    (* PACKED ARRAY [0..n] OF CHAR, inline [n+1 x i8] *)


(* ═══════════════════════════════════════════════════════════════════
   TEXTFILE I/O DATA PARAMETERS                            [DOCUMENTED]

   Manual 12-17 (source text line ~13473): data parameters to READ,
   READLN, WRITE, and WRITELN on textfiles take the forms

       P    P:M    P:M:N    P::N

   where M and N are INTEGER value parameters used for formatting;
   omitting M or N is the same as passing MAXINT (the default width is
   then used). M and N are documented for READs as well as WRITEs
   ("for later input format control") and are not accepted for BINARY
   files. An optional leading file argument selects the stream.

   Reimplementation status (see checklist 8.3/8.3a): P, P:M, P:M:N,
   and P::N are parsed and lowered on WRITE/WRITELN (P::N closed via
   discrepancy D-002: width omitted means the default field width — for
   REAL a 14-character field, matching the vintage output
   '        123.46' for 123.456::2 [OBSERVED]). READ-side M/N are not
   parsed. Known parser looseness:
   the colon forms are currently accepted on EVERY call, not just the
   I/O procedures (see tests/fixtures/parser/judgment_calls/
   B_colon_args_any_call.pas); rejection of M/N on binary files is not
   enforced. *)

io_data_param = expression [ ":" ( expression [ ":" expression ]
                                 | ":" expression ) ] ;
                (* P | P:M | P:M:N | P::N *)


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

(* ───────────────────────────────────────────────────────────────────
   METACOMMANDS  [DOCUMENTED]  (IBM Pascal manual Chapter 4)

   Metacommands appear at the START of a brace or paren comment
   (the first character after the comment opener must be "$").
   A comment that begins with "$" is consumed entirely as a metacommand
   comment; it is never passed to the Pascal parser as whitespace.

   One or more metacommands may appear in a single comment, separated
   by commas.  Blanks, tabs, and line ends between elements are ignored.

   Syntax of a metacommand comment:

     metacommand_comment =
         ( "(*$" | "{$" )
         metacommand { "," "$" metacommand }
         ( "*)" | "}" ) ;

     metacommand =
         on_off_cmd
       | int_cmd
       | str_cmd
       | push_cmd
       | pop_cmd
       | message_cmd
       | inconst_cmd
       | if_directive ;

   ON/OFF switches  ("$NAME+", "$NAME-", "$NAME:n", or bare "$NAME"):

     on_off_cmd = on_off_name [ "+" | "-" | ":" integer_constant ] ;

     on_off_name =
         "BRAVE"   | "DEBUG"   | "ENTRY"  | "GOTO"    | "INDEXCK"
       | "INITCK"  | "LINE"    | "LIST"   | "MATHCK"  | "NILCK"
       | "OCODE"   | "RANGECK" | "RUNTIME"| "STACKCK" | "SYMTAB"
       | "WARN" ;

   (* $DEBUG+/- is a master switch: it sets ENTRY INDEXCK INITCK
      MATHCK NILCK RANGECK STACKCK to the same value.  Individual
      flags may still be overridden afterward in the same session.
      $LINE+ automatically sets $ENTRY+ (manual §4-20).

      Codegen status [OBSERVED]: RANGECK, INDEXCK, MATHCK, NILCK, and
      INITCK emit real checks (see checklist; INITCK's -32768 sentinel
      is widened to -2147483648 for 32-bit INTEGER).  STACKCK is a
      documented no-op on this target (OS guard page).  The remaining
      switches (BRAVE, ENTRY, GOTO, LINE, RUNTIME, WARN and the listing
      flags) are state-tracked but have no codegen effect. *)

   INTEGER metacommands  (affect listing/output only, no codegen effect):

     int_cmd = int_name [ ":" integer_constant ] ;

     int_name =
         "ERRORS" | "LINESIZE" | "PAGE" | "PAGEIF" | "PAGESIZE" | "SKIP" ;

   STRING metacommands  (listing page headers, no codegen effect):

     str_cmd = str_name ":" ( "'" { any_char } "'" | identifier ) ;

     str_name = "SUBTITLE" | "TITLE" ;

   Typeless metacommands:

     push_cmd    = "PUSH" ;   (* save snapshot of all current flag values *)
     pop_cmd     = "POP" ;    (* restore most-recently saved snapshot      *)
     message_cmd = "MESSAGE" ":" "'" { any_char } "'" ;
                              (* print text to stderr during compilation   *)
     inconst_cmd = "INCONST" ":" identifier ;
                              (* prompt user for a WORD constant value;
                                 non-interactive builds use 0             *)

   Conditional compilation  ($IF/$THEN/$ELSE/$END span multiple comments):

     if_directive =
         "IF" constant "THEN" ;   (* closes current comment; source text
                                     follows until $ELSE or $END comment *)

     else_directive = "ELSE" ;    (* in a comment by itself: {$ELSE}      *)
     end_directive  = "END" ;     (* in a comment by itself: {$END}       *)

   (* If constant > 0 the then-branch is compiled; otherwise it is
      skipped at the character level before tokenisation (syntax errors
      in skipped text are invisible to the parser).  Nesting is
      supported: an inner $IF/$END pair inside a skipped block is
      tracked by depth so the outer $END is never confused with an
      inner one.  $ELSE and $END are the only metacommands recognised
      inside a skipped block; all others are ignored.

      constant: a literal integer, a $INCONST-defined identifier, or
      an ON/OFF flag name (treated as 1 if on, 0 if off). *)

   $INCLUDE is handled separately as include_directive (see below)
   because its argument syntax differs.
   ─────────────────────────────────────────────────────────────────── *)

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
| `io_data_param` — new production for READ/WRITE field formatting (`P`, `P:M`, `P:M:N`, `P::N`) | DOCUMENTED | Manual 12-17 (line ~13473); previously recorded only in judgment_calls fixture comments |
| `file_type` — comment amended to separate vintage-toolchain verification (still blocked) from reimplementation runtime status (implemented, checklist §8) | Structural | Checklist Section 8 evidence |
| `metacommand_comment` — new section documenting all 30 metacommands (Chapter 4): ON/OFF switches with defaults, INTEGER/STRING listing metacommands, typeless `$PUSH`/`$POP`/`$MESSAGE`/`$INCONST`, and `$IF`/`$THEN`/`$ELSE`/`$END` conditional compilation with nesting semantics | DOCUMENTED | Checklist §9.5; IBM Pascal manual Chapter 4 |
| `io_data_param` — `P::N` now parsed and lowered on WRITE/WRITELN; comment updated (was: rejected by parser) | OBSERVED | Discrepancy D-002 differential probe (vintage accepts, output `        123.46`); manual 12-17 |
| Metacommand codegen — $INDEXCK/$MATHCK/$NILCK/$INITCK now emit runtime checks; $STACKCK ruled a documented no-op; INITCK sentinel widened to INT32_MIN | OBSERVED | Manual metacommand pages (images, Chapter 4); checklist runtime-check item; TestRuntimeCheckFlags |

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
| `FILE OF` runtime I/O (vintage toolchain) | DOCUMENTED | Requires linker library path fix; reimplementation runtime is implemented and run-tested (checklist §8) |
| Large set runtime behavior (> 15 elements) | OBSERVED (compiles); UNVERIFIED (runtime) | Link and run T_SET2.EXE |
| `FORWARD` with function return type propagation | INFERRED | Forward-declared FUNCTION with consumers |
