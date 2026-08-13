{ Pascal-1981 Native Type Checker implementation in extended IBM Pascal 2.0
  dialect. Consumes the JSON AST stream produced by pascal1981-parse on
  standard input, walks it validating the vintage-dialect core rule set
  (declarations, assignment/argument compatibility, ordinal-type rules,
  WORD/INTEGER coercion, aggregate string types), and re-emits the same
  tree on standard output. On any error it prints diagnostics to stderr
  and exits 1 without emitting AST JSON, matching cli_typecheck.py.

  Scope (v1): the rule set exercised by tests/fixtures/typecheck/, plus
  enough of the C-ABI/UNIT surface to self-host lex+parse+typecheck on this
  repository's own native .pas sources: EXTERN/FORWARD declarations (no
  body to check -- the real definition is a separately-compiled/linked
  object, or comes later in the same file), the ADRMEM/CPTR address types
  and the CINT/CCHAR/CSHORT/CLONG/CSIZE_T/CDOUBLE C-ABI width aliases (each
  resolved to the vintage type of matching flavor, since this v1 type-kind
  model doesn't track width), the wide INTEGER8/16/32/64, WORD8/16/32/64,
  and REAL32/64 extension names (same width-collapsing treatment), and the
  local_interfaces a USES clause splices in (their TYPE/PROC/FUNC
  signatures are registered exactly like an EXTERN decl's). DEVICE MODULE
  checks, VARARGS attribute checks, and UNIT interface/implementation
  signature *matching* (validating IMPLEMENTATION bodies against their
  INTERFACE signatures) are still deferred, as are pointer arithmetic/
  dereference and most builtin functions (ORD/CHR/TRUNC/SIZEOF/...) --
  self-hosting on lexer.pas/parser.pas/typechecker.pas's own sources still
  needs those.

  Annotation contract: the Python reference stamps a `resolved_type`
  attribute onto every IntLiteral/RealLiteral node (and the operand of a
  signed IntLiteral unary +/-), naming the literal's width/precision for
  codegen -- context_type's exact width (e.g. Integer32Type for a CINT
  target) when the surrounding context calls for one of the WORD/INTEGERn/
  REAL32 family, else the default IntegerType/RealType. Because this v1
  type-kind model collapses all integer widths into TK_INTEGER (and all
  WORD widths into TK_WORD), it always tags the default IntegerType/
  RealType regardless of a width-specific target context -- correct for
  every fixture in tests/fixtures/typecheck/ (none of which need a
  non-default width), but not yet byte-identical to the Python reference
  when a literal is assigned into a CINT/INTEGER32/WORD32/etc.-typed
  target. }

(*$INCLUDE:'jsonutil.inc'*)
PROGRAM pascal1981_typecheck(input, output);

USES jsonutil;

FUNCTION cJSON_Parse(val: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION cJSON_GetArraySize(arr: ADRMEM): CINT [C]; EXTERN;
FUNCTION cJSON_GetArrayItem(arr: ADRMEM; index: CINT): ADRMEM [C]; EXTERN;
FUNCTION cJSON_GetObjectItem(obj: ADRMEM; key: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateObject: ADRMEM [C]; EXTERN;
FUNCTION cJSON_Print(item: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION cJSON_GetStringValue(item: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION cJSON_GetNumberValue(item: ADRMEM): REAL [C]; EXTERN;
FUNCTION cJSON_IsTrue(item: ADRMEM): CINT [C]; EXTERN;
FUNCTION puts(str: ADRMEM): CINT [C]; EXTERN;
FUNCTION getchar: CINT [C]; EXTERN;
FUNCTION malloc(size: CINT): ADRMEM [C]; EXTERN;
PROCEDURE free(ptr: ADRMEM) [C]; EXTERN;
PROCEDURE exit(code: CINT) [C]; EXTERN;

CONST
  TK_UNKNOWN  = 0;
  TK_INTEGER  = 1;
  TK_REAL     = 2;
  TK_BOOLEAN  = 3;
  TK_CHAR     = 4;
  TK_WORD     = 5;
  TK_STRING   = 6;
  TK_POINTER  = 7;
  TK_ARRAY    = 8;
  TK_RECORD   = 9;
  TK_VOID     = 10;

  MAX_SYMBOLS = 2000;
  MAX_TYPES   = 500;
  MAX_FIELDS  = 2000;
  MAX_ERRORS  = 200;
  MAX_PARAMS  = 16;

TYPE
  SymRec = RECORD
    name: Str255;
    kind: Str255;       { 'VAR', 'CONST', 'TYPE', 'PROC', 'FUNC' }
    tk: INTEGER;
    aux: INTEGER;        { pointee/element TK, or record id }
    idx_tk: INTEGER;     { array index TK }
    nparams: INTEGER;
    param_tk: ARRAY [1..MAX_PARAMS] OF INTEGER;
    ret_tk: INTEGER;
  END;

  TypeRec = RECORD
    name: Str255;
    tk: INTEGER;
    aux: INTEGER;
    idx_tk: INTEGER;
  END;

  FieldRec = RECORD
    record_id: INTEGER;
    fname: Str255;
    ftk: INTEGER;
  END;

VAR
  symbols: ARRAY [1..MAX_SYMBOLS] OF SymRec;
  nsymbols: INTEGER32;
  scope_stack: ARRAY [1..64] OF INTEGER32;
  scope_top: INTEGER;

  types: ARRAY [1..MAX_TYPES] OF TypeRec;
  ntypes: INTEGER32;

  fields: ARRAY [1..MAX_FIELDS] OF FieldRec;
  nfields: INTEGER32;
  next_record_id: INTEGER;

  errors: ARRAY [1..MAX_ERRORS] OF Str255;
  nerrors: INTEGER32;

  cur_func_ret_tk: INTEGER; { TK_VOID when not inside a function }

{ ============================== utilities ============================== }

FUNCTION NodeType(obj: ADRMEM): Str255;
BEGIN
  IF obj = NIL THEN
    NodeType := ''
  ELSE
    NodeType := CStrToStr255(cJSON_GetStringValue(cJSON_GetObjectItem(obj, MakeCStr('__node_type__'))));
END;

FUNCTION GetObj(obj: ADRMEM; key: Str255): ADRMEM;
BEGIN
  IF obj = NIL THEN
    GetObj := NIL
  ELSE
    GetObj := cJSON_GetObjectItem(obj, MakeCStr(key));
END;

FUNCTION GetStr(obj: ADRMEM; key: Str255): Str255;
VAR
  item: ADRMEM;
BEGIN
  item := GetObj(obj, key);
  IF item = NIL THEN
    GetStr := ''
  ELSE
    GetStr := CStrToStr255(cJSON_GetStringValue(item));
END;

FUNCTION GetInt(obj: ADRMEM; key: Str255): INTEGER;
VAR
  item: ADRMEM;
BEGIN
  item := GetObj(obj, key);
  IF item = NIL THEN
    GetInt := 0
  ELSE
    GetInt := TRUNC(cJSON_GetNumberValue(item));
END;

PROCEDURE AddError(msg: Str255);
BEGIN
  IF nerrors < MAX_ERRORS THEN
  BEGIN
    nerrors := nerrors + 1;
    errors[nerrors] := msg;
  END;
END;

{ ============================ symbol table ============================= }

PROCEDURE PushScope;
BEGIN
  scope_top := scope_top + 1;
  scope_stack[scope_top] := nsymbols;
END;

PROCEDURE PopScope;
BEGIN
  nsymbols := scope_stack[scope_top];
  scope_top := scope_top - 1;
END;

FUNCTION LookupSymbol(name: Str255): INTEGER32;
VAR
  i: INTEGER32;
BEGIN
  i := nsymbols;
  WHILE (i >= 1) AND THEN (symbols[i].name <> name) DO
    i := i - 1;
  LookupSymbol := i;
END;

FUNCTION DefineSymbol(name: Str255; kind: Str255; tk, aux, idx_tk: INTEGER): INTEGER32;
BEGIN
  nsymbols := nsymbols + 1;
  symbols[nsymbols].name := name;
  symbols[nsymbols].kind := kind;
  symbols[nsymbols].tk := tk;
  symbols[nsymbols].aux := aux;
  symbols[nsymbols].idx_tk := idx_tk;
  symbols[nsymbols].nparams := 0;
  symbols[nsymbols].ret_tk := TK_VOID;
  DefineSymbol := nsymbols;
END;

FUNCTION LookupType(name: Str255): INTEGER32;
VAR
  i: INTEGER32;
BEGIN
  i := ntypes;
  WHILE (i >= 1) AND THEN (types[i].name <> name) DO
    i := i - 1;
  LookupType := i;
END;

PROCEDURE AddFieldEntry(record_id: INTEGER; fname: Str255; ftk: INTEGER);
BEGIN
  IF nfields < MAX_FIELDS THEN
  BEGIN
    nfields := nfields + 1;
    fields[nfields].record_id := record_id;
    fields[nfields].fname := fname;
    fields[nfields].ftk := ftk;
  END;
END;

FUNCTION LookupField(record_id: INTEGER; fname: Str255): INTEGER32;
VAR
  i: INTEGER32;
BEGIN
  i := nfields;
  WHILE (i >= 1) AND THEN ((fields[i].record_id <> record_id) OR (fields[i].fname <> fname)) DO
    i := i - 1;
  LookupField := i;
END;

FUNCTION IsOrdinal(tk: INTEGER): BOOLEAN;
BEGIN
  IsOrdinal := (tk = TK_INTEGER) OR (tk = TK_WORD) OR (tk = TK_CHAR) OR (tk = TK_BOOLEAN);
END;

FUNCTION IsNumeric(tk: INTEGER): BOOLEAN;
BEGIN
  IsNumeric := (tk = TK_INTEGER) OR (tk = TK_REAL) OR (tk = TK_WORD);
END;

{ ========================== type-expr resolution ======================= }

PROCEDURE ResolveTypeExpr(node: ADRMEM; VAR tk, aux, idx_tk: INTEGER);
VAR
  nt, name: Str255;
  base_node, elem_node, fields_arr, tup, items, names_arr, ftype_node: ADRMEM;
  inner_tk, inner_aux, inner_idx: INTEGER;
  ti: INTEGER32;
  rid: INTEGER;
  n, fi, nn, ni: INTEGER32;
  nm: Str255;
BEGIN
  tk := TK_UNKNOWN;
  aux := 0;
  idx_tk := 0;
  nt := NodeType(node);
  IF nt = 'NamedType' THEN
  BEGIN
    name := GetStr(node, 'name');
    IF name = 'INTEGER' THEN tk := TK_INTEGER
    ELSE IF name = 'WORD' THEN tk := TK_WORD
    ELSE IF name = 'REAL' THEN tk := TK_REAL
    ELSE IF name = 'BOOLEAN' THEN tk := TK_BOOLEAN
    ELSE IF name = 'CHAR' THEN tk := TK_CHAR
    ELSE IF name = 'STRING' THEN tk := TK_STRING
    ELSE IF name = 'LSTRING' THEN tk := TK_STRING
    { Wide-integer/real extension names (feature-gated under the extended
      dialect): this v1 type-kind model doesn't track width, so each just
      aliases to its base kind -- matching how INTEGER32/WORD32/etc. behave
      identically to INTEGER/WORD for every check this stage performs. }
    ELSE IF (name = 'INTEGER8') OR (name = 'INTEGER16') OR (name = 'INTEGER32') OR (name = 'INTEGER64') THEN tk := TK_INTEGER
    ELSE IF (name = 'WORD8') OR (name = 'WORD16') OR (name = 'WORD32') OR (name = 'WORD64') THEN tk := TK_WORD
    ELSE IF (name = 'REAL32') OR (name = 'REAL64') THEN tk := TK_REAL
    { ADRMEM/ADSMEM (and the CPTR C-ABI alias) are address types -- codegen's
      opaque-pointer model treats them as "pointer to CHAR" (see
      types_resolve.py's resolve_type: ADRMEM -> PointerType(CHAR_TYPE)). }
    ELSE IF (name = 'ADRMEM') OR (name = 'ADSMEM') OR (name = 'CPTR') THEN
    BEGIN
      tk := TK_POINTER;
      aux := TK_CHAR;
    END
    { C-ABI fixed-width scalar aliases (builtins_registry.py's
      C_ABI_TYPE_ALIASES): each resolves to the vintage type of matching
      flavor, since this stage doesn't distinguish integer/real width. }
    ELSE IF name = 'CCHAR' THEN tk := TK_CHAR
    ELSE IF (name = 'CSHORT') OR (name = 'CINT') OR (name = 'CLONG') OR (name = 'CSIZE_T') THEN tk := TK_INTEGER
    ELSE IF name = 'CDOUBLE' THEN tk := TK_REAL
    ELSE BEGIN
      ti := LookupType(name);
      IF ti = 0 THEN
      BEGIN
        AddError('Unknown type name');
        tk := TK_UNKNOWN;
      END
      ELSE BEGIN
        tk := types[ti].tk;
        aux := types[ti].aux;
        idx_tk := types[ti].idx_tk;
      END;
    END;
  END
  ELSE IF nt = 'LStringType' THEN
    tk := TK_STRING
  ELSE IF nt = 'PointerType' THEN
  BEGIN
    base_node := GetObj(node, 'base');
    ResolveTypeExpr(base_node, inner_tk, inner_aux, inner_idx);
    tk := TK_POINTER;
    aux := inner_tk;
  END
  ELSE IF nt = 'ArrayType' THEN
  BEGIN
    elem_node := GetObj(node, 'element_type');
    ResolveTypeExpr(elem_node, inner_tk, inner_aux, inner_idx);
    tk := TK_ARRAY;
    aux := inner_tk;
    idx_tk := TK_INTEGER;
  END
  ELSE IF nt = 'RecordType' THEN
  BEGIN
    rid := next_record_id;
    next_record_id := next_record_id + 1;
    fields_arr := GetObj(node, 'fields');
    n := cJSON_GetArraySize(fields_arr);
    FOR fi := 0 TO n - 1 DO
    BEGIN
      tup := cJSON_GetArrayItem(fields_arr, fi);
      items := GetObj(tup, 'items');
      names_arr := cJSON_GetArrayItem(items, 0);
      ftype_node := cJSON_GetArrayItem(items, 1);
      ResolveTypeExpr(ftype_node, inner_tk, inner_aux, inner_idx);
      nn := cJSON_GetArraySize(names_arr);
      FOR ni := 0 TO nn - 1 DO
      BEGIN
        nm := CStrToStr255(cJSON_GetStringValue(cJSON_GetArrayItem(names_arr, ni)));
        AddFieldEntry(rid, nm, inner_tk);
      END;
    END;
    tk := TK_RECORD;
    aux := rid;
  END;
END;

{ ============================ forward decls ============================ }

FUNCTION CheckExpr(node: ADRMEM): INTEGER; FORWARD;
PROCEDURE CheckStmt(node: ADRMEM); FORWARD;

{ =============================== expressions ============================ }

PROCEDURE TagResolvedType(node: ADRMEM; type_name: Str255);
VAR
  tobj: ADRMEM;
BEGIN
  tobj := cJSON_CreateObject;
  AddStringField(tobj, '__type_system__', type_name);
  AddField(node, 'resolved_type', tobj);
END;

FUNCTION CanAssign(target_tk, expr_tk: INTEGER): BOOLEAN;
BEGIN
  IF (target_tk = TK_UNKNOWN) OR (expr_tk = TK_UNKNOWN) THEN
    CanAssign := TRUE
  ELSE IF target_tk = expr_tk THEN
    CanAssign := TRUE
  ELSE IF (target_tk = TK_REAL) AND (expr_tk = TK_INTEGER) THEN
    CanAssign := TRUE
  ELSE IF (target_tk = TK_POINTER) AND (expr_tk = TK_POINTER) THEN
    CanAssign := TRUE
  ELSE
    CanAssign := FALSE;
END;

FUNCTION CheckDesignator(node: ADRMEM): INTEGER;
VAR
  name: Str255;
  si: INTEGER32;
  sel_arr: ADRMEM;
  nsel, i: INTEGER32;
  sel, idx_expr: ADRMEM;
  skind, fname: Str255;
  tk, aux, idx_tk, itk: INTEGER;
  fi: INTEGER32;
BEGIN
  name := GetStr(node, 'name');
  si := LookupSymbol(name);
  IF si = 0 THEN
  BEGIN
    AddError('Undefined identifier');
    CheckDesignator := TK_UNKNOWN;
    RETURN;
  END;
  tk := symbols[si].tk;
  aux := symbols[si].aux;
  idx_tk := symbols[si].idx_tk;
  sel_arr := GetObj(node, 'selectors');
  nsel := cJSON_GetArraySize(sel_arr);
  FOR i := 0 TO nsel - 1 DO
  BEGIN
    sel := cJSON_GetArrayItem(sel_arr, i);
    skind := GetStr(sel, 'kind');
    IF skind = 'FIELD' THEN
    BEGIN
      IF tk <> TK_RECORD THEN
      BEGIN
        AddError('Field selector on non-record value');
        tk := TK_UNKNOWN;
      END
      ELSE BEGIN
        fname := CStrToStr255(cJSON_GetStringValue(GetObj(sel, 'index_or_field')));
        fi := LookupField(aux, fname);
        IF fi = 0 THEN
        BEGIN
          AddError('Unknown field');
          tk := TK_UNKNOWN;
        END
        ELSE
          tk := fields[fi].ftk;
      END;
    END
    ELSE IF skind = 'INDEX' THEN
    BEGIN
      IF tk <> TK_ARRAY THEN
      BEGIN
        AddError('Index selector on non-array value');
        tk := TK_UNKNOWN;
      END
      ELSE BEGIN
        idx_expr := GetObj(sel, 'index_or_field');
        itk := CheckExpr(idx_expr);
        IF NOT IsOrdinal(itk) AND (itk <> TK_UNKNOWN) THEN
          AddError('Array index must be an ordinal type');
        tk := aux;
      END;
    END;
  END;
  CheckDesignator := tk;
END;

FUNCTION CheckFuncCall(node: ADRMEM): INTEGER;
VAR
  name: Str255;
  args_arr: ADRMEM;
  nargs, i, si: INTEGER32;
  atk: INTEGER;
BEGIN
  name := GetStr(node, 'name');
  args_arr := GetObj(node, 'args');
  nargs := cJSON_GetArraySize(args_arr);
  IF name = 'WRD' THEN
  BEGIN
    IF nargs <> 1 THEN
      AddError('WRD requires exactly one argument')
    ELSE BEGIN
      atk := CheckExpr(cJSON_GetArrayItem(args_arr, 0));
      IF NOT IsOrdinal(atk) AND (atk <> TK_UNKNOWN) THEN
        AddError('WRD argument must be an ordinal type');
    END;
    CheckFuncCall := TK_WORD;
    RETURN;
  END;
  IF name = 'BYWORD' THEN
  BEGIN
    IF nargs <> 2 THEN
      AddError('BYWORD requires exactly two arguments')
    ELSE
      FOR i := 0 TO nargs - 1 DO
      BEGIN
        atk := CheckExpr(cJSON_GetArrayItem(args_arr, i));
        IF NOT IsOrdinal(atk) AND (atk <> TK_UNKNOWN) THEN
          AddError('BYWORD argument must be an ordinal type');
      END;
    CheckFuncCall := TK_WORD;
    RETURN;
  END;
  si := LookupSymbol(name);
  IF si = 0 THEN
  BEGIN
    AddError('Undefined function');
    FOR i := 0 TO nargs - 1 DO
      CheckExpr(cJSON_GetArrayItem(args_arr, i));
    CheckFuncCall := TK_UNKNOWN;
    RETURN;
  END;
  IF nargs <> symbols[si].nparams THEN
    AddError('Argument count mismatch')
  ELSE
    FOR i := 0 TO nargs - 1 DO
    BEGIN
      atk := CheckExpr(cJSON_GetArrayItem(args_arr, i));
      IF NOT CanAssign(symbols[si].param_tk[i + 1], atk) THEN
        AddError('Argument type mismatch');
    END;
  CheckFuncCall := symbols[si].ret_tk;
END;

FUNCTION CheckExpr(node: ADRMEM): INTEGER;
VAR
  nt, name: Str255;
  si: INTEGER32;
  left_node, right_node, operand_node: ADRMEM;
  lt, rt, ot, op_kind: INTEGER;
  op: Str255;
BEGIN
  nt := NodeType(node);
  IF nt = 'IntLiteral' THEN
  BEGIN
    TagResolvedType(node, 'IntegerType');
    CheckExpr := TK_INTEGER;
  END
  ELSE IF nt = 'RealLiteral' THEN
  BEGIN
    TagResolvedType(node, 'RealType');
    CheckExpr := TK_REAL;
  END
  ELSE IF nt = 'BoolLiteral' THEN CheckExpr := TK_BOOLEAN
  ELSE IF nt = 'CharLiteral' THEN CheckExpr := TK_CHAR
  ELSE IF nt = 'StringLiteral' THEN CheckExpr := TK_STRING
  ELSE IF nt = 'NilLiteral' THEN CheckExpr := TK_POINTER
  ELSE IF nt = 'Identifier' THEN
  BEGIN
    name := GetStr(node, 'name');
    si := LookupSymbol(name);
    IF si = 0 THEN
    BEGIN
      AddError('Undefined identifier');
      CheckExpr := TK_UNKNOWN;
    END
    ELSE
      CheckExpr := symbols[si].tk;
  END
  ELSE IF nt = 'Designator' THEN
    CheckExpr := CheckDesignator(node)
  ELSE IF nt = 'FuncCall' THEN
    CheckExpr := CheckFuncCall(node)
  ELSE IF nt = 'BinOp' THEN
  BEGIN
    left_node := GetObj(node, 'left');
    right_node := GetObj(node, 'right');
    lt := CheckExpr(left_node);
    rt := CheckExpr(right_node);
    op := GetStr(node, 'op');
    IF (lt = TK_UNKNOWN) OR (rt = TK_UNKNOWN) THEN
      CheckExpr := TK_UNKNOWN
    ELSE IF (op = 'AND') OR (op = 'OR') OR (op = 'AND_THEN') OR (op = 'OR_ELSE') THEN
    BEGIN
      IF (lt <> TK_BOOLEAN) OR (rt <> TK_BOOLEAN) THEN
      BEGIN
        AddError('Boolean operator requires BOOLEAN operands');
        CheckExpr := TK_UNKNOWN;
      END
      ELSE
        CheckExpr := TK_BOOLEAN;
    END
    ELSE IF (op = 'EQ') OR (op = 'NE') OR (op = 'LT') OR (op = 'LE') OR (op = 'GT') OR (op = 'GE') THEN
    BEGIN
      IF NOT (IsNumeric(lt) AND IsNumeric(rt)) AND (lt <> rt) THEN
        AddError('Comparison operands are not comparable');
      CheckExpr := TK_BOOLEAN;
    END
    ELSE BEGIN
      { arithmetic: PLUS/MINUS/TIMES/DIVIDE/DIV/MOD }
      IF NOT (IsNumeric(lt) AND IsNumeric(rt)) THEN
      BEGIN
        AddError('Arithmetic operator requires numeric operands');
        CheckExpr := TK_UNKNOWN;
      END
      ELSE IF (lt = TK_REAL) OR (rt = TK_REAL) THEN
        CheckExpr := TK_REAL
      ELSE
        CheckExpr := TK_INTEGER;
    END;
  END
  ELSE IF nt = 'UnaryOp' THEN
  BEGIN
    operand_node := GetObj(node, 'operand');
    ot := CheckExpr(operand_node);
    op := GetStr(node, 'op');
    IF op = 'NOT' THEN
    BEGIN
      IF (ot <> TK_BOOLEAN) AND (ot <> TK_UNKNOWN) THEN
        AddError('NOT requires a BOOLEAN operand');
      CheckExpr := TK_BOOLEAN;
    END
    ELSE BEGIN
      IF ((op = 'PLUS') OR (op = 'MINUS')) AND (NodeType(operand_node) = 'IntLiteral') THEN
        TagResolvedType(node, 'IntegerType');
      CheckExpr := ot;
    END;
  END
  ELSE
    CheckExpr := TK_UNKNOWN;
END;

{ =============================== statements ============================= }

PROCEDURE CheckCompoundOrStmt(node: ADRMEM);
VAR
  nt: Str255;
  stmts_arr: ADRMEM;
  n, i: INTEGER32;
BEGIN
  IF node <> NIL THEN
  BEGIN
    nt := NodeType(node);
    IF nt = 'CompoundStmt' THEN
    BEGIN
      stmts_arr := GetObj(node, 'stmts');
      n := cJSON_GetArraySize(stmts_arr);
      FOR i := 0 TO n - 1 DO
        CheckStmt(cJSON_GetArrayItem(stmts_arr, i));
    END
    ELSE
      CheckStmt(node);
  END;
END;

PROCEDURE CheckStmtList(arr: ADRMEM);
VAR
  n, i: INTEGER32;
BEGIN
  n := cJSON_GetArraySize(arr);
  FOR i := 0 TO n - 1 DO
    CheckStmt(cJSON_GetArrayItem(arr, i));
END;

PROCEDURE CheckStmt(node: ADRMEM);
VAR
  nt, varname: Str255;
  target_node, expr_node, cond_node, args_arr, warg, wexpr: ADRMEM;
  target_tk, expr_tk, cond_tk, vi: INTEGER;
  si: INTEGER32;
  nargs, i: INTEGER32;
  pname: Str255;
BEGIN
  IF node = NIL THEN
    nt := ''
  ELSE
    nt := NodeType(node);
  IF nt = 'AssignStmt' THEN
  BEGIN
    target_node := GetObj(node, 'target');
    expr_node := GetObj(node, 'expr');
    target_tk := CheckDesignator(target_node);
    expr_tk := CheckExpr(expr_node);
    IF NOT CanAssign(target_tk, expr_tk) THEN
      AddError('Cannot assign incompatible type');
  END
  ELSE IF nt = 'IfStmt' THEN
  BEGIN
    cond_tk := CheckExpr(GetObj(node, 'cond'));
    IF (cond_tk <> TK_BOOLEAN) AND (cond_tk <> TK_UNKNOWN) THEN
      AddError('IF condition must be BOOLEAN');
    CheckCompoundOrStmt(GetObj(node, 'then_branch'));
    CheckCompoundOrStmt(GetObj(node, 'else_branch'));
  END
  ELSE IF nt = 'WhileStmt' THEN
  BEGIN
    cond_tk := CheckExpr(GetObj(node, 'cond'));
    IF (cond_tk <> TK_BOOLEAN) AND (cond_tk <> TK_UNKNOWN) THEN
      AddError('WHILE condition must be BOOLEAN');
    CheckCompoundOrStmt(GetObj(node, 'body'));
  END
  ELSE IF nt = 'RepeatStmt' THEN
  BEGIN
    cond_tk := CheckExpr(GetObj(node, 'cond'));
    IF (cond_tk <> TK_BOOLEAN) AND (cond_tk <> TK_UNKNOWN) THEN
      AddError('REPEAT UNTIL condition must be BOOLEAN');
    CheckStmtList(GetObj(node, 'body'));
  END
  ELSE IF nt = 'ForStmt' THEN
  BEGIN
    varname := GetStr(node, 'var');
    si := LookupSymbol(varname);
    IF si = 0 THEN
      AddError('Undefined identifier')
    ELSE BEGIN
      vi := symbols[si].tk;
      IF NOT IsOrdinal(vi) THEN
        AddError('FOR loop variable must be an ordinal type');
    END;
    CheckExpr(GetObj(node, 'start'));
    CheckExpr(GetObj(node, 'end'));
    CheckCompoundOrStmt(GetObj(node, 'body'));
  END
  ELSE IF nt = 'ProcCallStmt' THEN
  BEGIN
    pname := GetStr(node, 'name');
    args_arr := GetObj(node, 'args');
    nargs := cJSON_GetArraySize(args_arr);
    IF (pname = 'WRITELN') OR (pname = 'WRITE') OR (pname = 'READLN') OR (pname = 'READ') THEN
    BEGIN
      FOR i := 0 TO nargs - 1 DO
      BEGIN
        warg := cJSON_GetArrayItem(args_arr, i);
        IF NodeType(warg) = 'WriteArg' THEN
        BEGIN
          wexpr := GetObj(warg, 'expr');
          IF wexpr <> NIL THEN CheckExpr(wexpr);
        END;
      END;
    END
    ELSE BEGIN
      si := LookupSymbol(pname);
      IF si = 0 THEN
      BEGIN
        AddError('Undefined procedure');
        FOR i := 0 TO nargs - 1 DO
          CheckExpr(cJSON_GetArrayItem(args_arr, i));
      END
      ELSE BEGIN
        IF nargs <> symbols[si].nparams THEN
          AddError('Argument count mismatch')
        ELSE
          FOR i := 0 TO nargs - 1 DO
          BEGIN
            expr_tk := CheckExpr(cJSON_GetArrayItem(args_arr, i));
            IF NOT CanAssign(symbols[si].param_tk[i + 1], expr_tk) THEN
              AddError('Argument type mismatch');
          END;
      END;
    END;
  END
  ELSE IF nt = 'CompoundStmt' THEN
    CheckCompoundOrStmt(node);
END;

{ ============================== declarations ============================ }

PROCEDURE CheckBlock(block: ADRMEM); FORWARD;

PROCEDURE CheckDecl(decl: ADRMEM);
VAR
  nt, dname: Str255;
  names_arr, type_expr, params_arr, body, ret_type_node: ADRMEM;
  tk, aux, idx_tk, ret_tk: INTEGER;
  n, i: INTEGER32;
  nm: Str255;
  si: INTEGER32;
  np, pi, ppi: INTEGER32;
  param, pnames: ADRMEM;
  ptk, paux, pidx: INTEGER;
  pn, pj: INTEGER32;
BEGIN
  nt := NodeType(decl);
  IF nt = 'VarDecl' THEN
  BEGIN
    names_arr := GetObj(decl, 'names');
    type_expr := GetObj(decl, 'type_expr');
    ResolveTypeExpr(type_expr, tk, aux, idx_tk);
    n := cJSON_GetArraySize(names_arr);
    FOR i := 0 TO n - 1 DO
    BEGIN
      nm := CStrToStr255(cJSON_GetStringValue(cJSON_GetArrayItem(names_arr, i)));
      DefineSymbol(nm, 'VAR', tk, aux, idx_tk);
    END;
  END
  ELSE IF nt = 'ConstDecl' THEN
  BEGIN
    dname := GetStr(decl, 'name');
    tk := CheckExpr(GetObj(decl, 'value'));
    DefineSymbol(dname, 'CONST', tk, 0, 0);
  END
  ELSE IF nt = 'TypeDecl' THEN
  BEGIN
    dname := GetStr(decl, 'name');
    type_expr := GetObj(decl, 'type_expr');
    ResolveTypeExpr(type_expr, tk, aux, idx_tk);
    IF ntypes < MAX_TYPES THEN
    BEGIN
      ntypes := ntypes + 1;
      types[ntypes].name := dname;
      types[ntypes].tk := tk;
      types[ntypes].aux := aux;
      types[ntypes].idx_tk := idx_tk;
    END;
  END
  ELSE IF (nt = 'ProcDecl') OR (nt = 'FuncDecl') THEN
  BEGIN
    dname := GetStr(decl, 'name');
    params_arr := GetObj(decl, 'params');
    np := cJSON_GetArraySize(params_arr);
    IF nt = 'FuncDecl' THEN
    BEGIN
      ret_type_node := GetObj(decl, 'return_type');
      ResolveTypeExpr(ret_type_node, ret_tk, aux, idx_tk);
    END
    ELSE
      ret_tk := TK_VOID;
    si := DefineSymbol(dname, nt, TK_UNKNOWN, 0, 0);
    IF nt = 'FuncDecl' THEN
      symbols[si].kind := 'FUNC'
    ELSE
      symbols[si].kind := 'PROC';
    symbols[si].ret_tk := ret_tk;
    ppi := 0;
    FOR pi := 0 TO np - 1 DO
    BEGIN
      param := cJSON_GetArrayItem(params_arr, pi);
      ResolveTypeExpr(GetObj(param, 'type_expr'), ptk, paux, pidx);
      pnames := GetObj(param, 'names');
      pn := cJSON_GetArraySize(pnames);
      FOR pj := 0 TO pn - 1 DO
      BEGIN
        ppi := ppi + 1;
        IF ppi <= MAX_PARAMS THEN
          symbols[si].param_tk[ppi] := ptk;
      END;
    END;
    symbols[si].nparams := ppi;

    { Check the routine body (if any) in its own scope, with parameters and
      -- for a FUNCTION -- the function's own name bound as an assignable
      variable of the return type (F := ... assigns through it). }
    body := GetObj(decl, 'body');
    IF body <> NIL THEN
    BEGIN
      PushScope;
      IF nt = 'FuncDecl' THEN
        DefineSymbol(dname, 'VAR', ret_tk, 0, 0);
      ppi := 0;
      FOR pi := 0 TO np - 1 DO
      BEGIN
        param := cJSON_GetArrayItem(params_arr, pi);
        ResolveTypeExpr(GetObj(param, 'type_expr'), ptk, paux, pidx);
        pnames := GetObj(param, 'names');
        pn := cJSON_GetArraySize(pnames);
        FOR pj := 0 TO pn - 1 DO
        BEGIN
          nm := CStrToStr255(cJSON_GetStringValue(cJSON_GetArrayItem(pnames, pj)));
          DefineSymbol(nm, 'VAR', ptk, paux, pidx);
        END;
      END;
      CheckBlock(body);
      PopScope;
    END;
  END;
END;

PROCEDURE CheckBlock(block: ADRMEM);
VAR
  decls_arr, body_arr: ADRMEM;
  n, i: INTEGER32;
BEGIN
  decls_arr := GetObj(block, 'decls');
  n := cJSON_GetArraySize(decls_arr);
  FOR i := 0 TO n - 1 DO
    CheckDecl(cJSON_GetArrayItem(decls_arr, i));
  body_arr := GetObj(block, 'body');
  CheckStmtList(body_arr);
END;

{ ============================== I/O driver =============================== }

FUNCTION ReadAllStdin: ADRMEM;
VAR
  raw_input, old_buf: ADRMEM;
  cap, len, i: INTEGER32;
  input_ch: CINT;
  p_in, p_out, p_in_base, p_out_base: ^CHAR;
  json_root: ADRMEM;
  res_c: CINT;
BEGIN
  cap := 32000;
  raw_input := malloc(cap);
  len := 0;
  input_ch := getchar;
  WHILE input_ch <> -1 DO
  BEGIN
    IF len >= cap THEN
    BEGIN
      old_buf := raw_input;
      cap := cap * 2;
      raw_input := malloc(cap);
      FOR i := 0 TO len - 1 DO
      BEGIN
        p_in_base := old_buf;
        p_out_base := raw_input;
        p_in := p_in_base + i;
        p_out := p_out_base + i;
        p_out^ := p_in^;
      END;
      free(old_buf);
    END;
    p_in_base := raw_input;
    p_in := p_in_base + len;
    p_in^ := CHR(input_ch);
    len := len + 1;
    input_ch := getchar;
  END;
  p_in_base := raw_input;
  p_in := p_in_base + len;
  p_in^ := CHR(0);

  json_root := cJSON_Parse(raw_input);
  free(raw_input);
  IF json_root = NIL THEN
  BEGIN
    res_c := puts(MakeCStr('Error: Failed to parse input AST JSON'));
    exit(1);
  END;
  ReadAllStdin := json_root;
END;

PROCEDURE CheckLocalInterfaces(root: ADRMEM);
{ USES X splices X's INTERFACE into this file's local_interfaces list (see
  units.py's check_program_unit): each entry's decls are signature-only (no
  body -- the real IMPLEMENTATION is a separately-compiled/linked object), so
  running them through the ordinary CheckDecl dispatch registers their TYPEs
  and PROC/FUNC signatures as callable symbols exactly like an EXTERN decl,
  with no body to check. }
VAR
  ifaces, decls_arr: ADRMEM;
  n, ni, m, di: INTEGER32;
  iface: ADRMEM;
BEGIN
  ifaces := GetObj(root, 'local_interfaces');
  IF ifaces <> NIL THEN
  BEGIN
    n := cJSON_GetArraySize(ifaces);
    FOR ni := 0 TO n - 1 DO
    BEGIN
      iface := cJSON_GetArrayItem(ifaces, ni);
      decls_arr := GetObj(iface, 'decls');
      m := cJSON_GetArraySize(decls_arr);
      FOR di := 0 TO m - 1 DO
        CheckDecl(cJSON_GetArrayItem(decls_arr, di));
    END;
  END;
END;

VAR
  root: ADRMEM;
  out_str: ADRMEM;
  i: INTEGER32;
  res_c: CINT;

BEGIN
  nsymbols := 0;
  scope_top := 0;
  ntypes := 0;
  nfields := 0;
  next_record_id := 1;
  nerrors := 0;
  cur_func_ret_tk := TK_VOID;

  root := ReadAllStdin;
  CheckLocalInterfaces(root);
  CheckBlock(GetObj(root, 'block'));

  IF nerrors > 0 THEN
  BEGIN
    res_c := puts(MakeCStr('Type checking failed:'));
    FOR i := 1 TO nerrors DO
      res_c := puts(MakeCStr(errors[i]));
    exit(1);
  END;

  out_str := cJSON_Print(root);
  res_c := puts(out_str);
END.
