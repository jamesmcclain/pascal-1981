{ Pascal-1981 Native Parser implementation in extended IBM Pascal 2.0 dialect.
  Consumes JSON token stream from standard input (produced by pascal1981-lex)
  and outputs JSON AST stream on standard output (consumed by pascal1981-codegen). }

PROGRAM pascal1981_parse(input, output);

{ C-FFI bindings to libcjson and standard C library routines }
FUNCTION cJSON_Parse(val: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION cJSON_GetArraySize(arr: ADRMEM): CINT [C]; EXTERN;
FUNCTION cJSON_GetArrayItem(arr: ADRMEM; index: CINT): ADRMEM [C]; EXTERN;
FUNCTION cJSON_GetObjectItem(obj: ADRMEM; key: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateObject: ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateArray: ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateString(val: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateNumber(num: REAL): ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateBool(b: CINT): ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateNull: ADRMEM [C]; EXTERN;
PROCEDURE cJSON_AddItemToObject(obj: ADRMEM; key: ADRMEM; item: ADRMEM) [C]; EXTERN;
PROCEDURE cJSON_AddItemToArray(arr: ADRMEM; item: ADRMEM) [C]; EXTERN;
FUNCTION cJSON_Print(item: ADRMEM): ADRMEM [C]; EXTERN;
PROCEDURE cJSON_Delete(item: ADRMEM) [C]; EXTERN;

FUNCTION cJSON_GetStringValue(item: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION cJSON_GetNumberValue(item: ADRMEM): REAL [C]; EXTERN;
FUNCTION puts(str: ADRMEM): CINT [C]; EXTERN;
FUNCTION getchar: CINT [C]; EXTERN;
FUNCTION malloc(size: CINT): ADRMEM [C]; EXTERN;
PROCEDURE free(ptr: ADRMEM) [C]; EXTERN;
PROCEDURE exit(code: CINT) [C]; EXTERN;

TYPE
  Str255     = LSTRING(255);
  CharBuf256 = ARRAY [0..255] OF CHAR;
  PCharBuf   = ^CharBuf256;

  Token = RECORD
    kind: Str255;
    code: CINT;
    lexeme: Str255;
    value_str: Str255;
    value_int: CINT;
    value_real: REAL;
    value_type: CINT; { 0=null, 1=int, 2=real, 3=str, 4=bool }
    line: CINT;
    col: CINT;
  END;

  PToken = ^Token;
  TokenBufArray = ARRAY [0..32000] OF Token;
  PTokenBufArray = ^TokenBufArray;

VAR
  tokens_buf: ADRMEM; { heap allocated array of Token }
  num_tokens, pos: CINT;

FUNCTION MakeCStr(s: Str255): ADRMEM;
VAR
  raw: ADRMEM;
  pbuf: PCharBuf;
  i, len: INTEGER;
BEGIN
  len := ORD(s[0]);
  raw := malloc(256);
  pbuf := raw;
  FOR i := 0 TO 255 DO pbuf^[i] := CHR(0);
  FOR i := 1 TO len DO pbuf^[i - 1] := s[i];
  pbuf^[len] := CHR(0);
  MakeCStr := raw;
END;

FUNCTION CStrToStr255(ptr: ADRMEM): Str255;
VAR
  res: Str255;
  pbuf: PCharBuf;
  len, i: INTEGER;
BEGIN
  res := '';
  IF ptr <> NIL THEN
  BEGIN
    pbuf := ptr;
    len := 0;
    WHILE (len < 255) AND (pbuf^[len] <> CHR(0)) DO
      len := len + 1;
    res[0] := CHR(len);
      FOR i := 1 TO len DO
        res[i] := pbuf^[i - 1];
  END;
  CStrToStr255 := res;
END;

PROCEDURE AddField(obj: ADRMEM; key_str: Str255; val_node: ADRMEM);
VAR
  key_ptr: ADRMEM;
BEGIN
  key_ptr := MakeCStr(key_str);
  cJSON_AddItemToObject(obj, key_ptr, val_node);
END;

PROCEDURE AddStringField(obj: ADRMEM; key_str: Str255; val_str: Str255);
VAR
  v_ptr: ADRMEM;
BEGIN
  v_ptr := MakeCStr(val_str);
  AddField(obj, key_str, cJSON_CreateString(v_ptr));
END;

PROCEDURE AddIntField(obj: ADRMEM; key_str: Str255; val_int: INTEGER);
BEGIN
  AddField(obj, key_str, cJSON_CreateNumber(val_int));
END;

PROCEDURE AddRealField(obj: ADRMEM; key_str: Str255; val_real: REAL);
BEGIN
  AddField(obj, key_str, cJSON_CreateNumber(val_real));
END;

PROCEDURE AddBoolField(obj: ADRMEM; key_str: Str255; val_bool: BOOLEAN);
BEGIN
  IF val_bool THEN
    AddField(obj, key_str, cJSON_CreateBool(1))
  ELSE
    AddField(obj, key_str, cJSON_CreateBool(0));
END;

PROCEDURE AddNullField(obj: ADRMEM; key_str: Str255);
BEGIN
  AddField(obj, key_str, cJSON_CreateNull);
END;

FUNCTION CreateNode(type_name: Str255): ADRMEM;
VAR
  obj: ADRMEM;
BEGIN
  obj := cJSON_CreateObject;
  AddStringField(obj, '__node_type__', type_name);
  CreateNode := obj;
END;

PROCEDURE ReadInputAndParseTokens;
VAR
  raw_input, old_buf: ADRMEM;
  cap, len, i: INTEGER;
  input_ch, tok_count, res_c: CINT;
  p_in, p_out, p_in_base, p_out_base: ^CHAR;
  json_root, item, field, val_obj, val_str_ptr, base_ptr, val_ptr: ADRMEM;
  k_kind, k_code, k_lex, k_val, k_line, k_col: ADRMEM;
  k_val_type: ADRMEM;
  empty_s, fieldName: Str255;
  p_tok: PToken;
  p_tok_arr: PTokenBufArray;
  tok_elem: ADRMEM;
  p_adrmem_off: ^ADRMEM;
  p_real_off: ^REAL;
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
  
  { Null terminate raw_input }
  p_in_base := raw_input;
  p_in := p_in_base + len;
  p_in^ := CHR(0);

  json_root := cJSON_Parse(raw_input);
  free(raw_input);

  IF json_root = NIL THEN
  BEGIN
    res_c := puts(MakeCStr('Error: Failed to parse input token JSON'));
    exit(1);
  END;

  tok_count := cJSON_GetArraySize(json_root);
  num_tokens := tok_count;
  tokens_buf := malloc(num_tokens * SIZEOF(Token));

  fieldName := 'kind'; k_kind := MakeCStr(fieldName);
  fieldName := 'code'; k_code := MakeCStr(fieldName);
  fieldName := 'lexeme'; k_lex := MakeCStr(fieldName);
  fieldName := 'value'; k_val := MakeCStr(fieldName);
  fieldName := 'line'; k_line := MakeCStr(fieldName);
  fieldName := 'column'; k_col := MakeCStr(fieldName);

  empty_s := '';

  FOR i := 0 TO num_tokens - 1 DO
  BEGIN
    item := cJSON_GetArrayItem(json_root, i);
    p_tok := tokens_buf + (i * SIZEOF(Token));

    field := cJSON_GetObjectItem(item, k_kind);
    IF field <> NIL THEN
      p_tok^.kind := CStrToStr255(cJSON_GetStringValue(field))
    ELSE
      p_tok^.kind := empty_s;

    field := cJSON_GetObjectItem(item, k_code);
    IF field <> NIL THEN
      p_tok^.code := TRUNC(cJSON_GetNumberValue(field))
    ELSE
      p_tok^.code := 0;

    field := cJSON_GetObjectItem(item, k_lex);
    IF field <> NIL THEN
      p_tok^.lexeme := CStrToStr255(cJSON_GetStringValue(field))
    ELSE
      p_tok^.lexeme := empty_s;

    field := cJSON_GetObjectItem(item, k_val);
    IF field <> NIL THEN
    BEGIN
      p_tok^.value_str := p_tok^.lexeme;
      p_tok^.value_int := 0;
      p_tok^.value_real := 0.0;
      p_tok^.value_type := 3;
    END;

    field := cJSON_GetObjectItem(item, k_line);
    IF field <> NIL THEN
      p_tok^.line := TRUNC(cJSON_GetNumberValue(field))
    ELSE
      p_tok^.line := 1;

    field := cJSON_GetObjectItem(item, k_col);
    IF field <> NIL THEN
      p_tok^.col := TRUNC(cJSON_GetNumberValue(field))
    ELSE
      p_tok^.col := 1;
  END;

  cJSON_Delete(json_root);
END;

FUNCTION GetTok(off: CINT): PToken;
VAR
  idx: CINT;
  res_c: CINT;
BEGIN
  idx := pos + off;
  IF (idx >= 0) AND (idx < num_tokens) THEN
    GetTok := tokens_buf + (idx * SIZEOF(Token))
  ELSE
  BEGIN
    { Return EOF token static fallback }
    GetTok := tokens_buf + ((num_tokens - 1) * SIZEOF(Token));
  END;
END;

FUNCTION CurKind: Str255;
VAR
  pt: PToken;
  res: Str255;
  res_c: CINT;
BEGIN
  pt := GetTok(0);
  res := pt^.kind;
  CurKind := res;
END;

FUNCTION CurLex: Str255;
VAR
  pt: PToken;
  res: Str255;
BEGIN
  pt := GetTok(0);
  res := pt^.lexeme;
  CurLex := res;
END;

FUNCTION StrToIntVal(s: Str255): INTEGER;
VAR
  i, len, val: INTEGER;
  neg: BOOLEAN;
BEGIN
  len := ORD(s[0]);
  val := 0;
  neg := FALSE;
  i := 1;
  IF (len >= 1) AND (s[1] = '-') THEN
  BEGIN
    neg := TRUE;
    i := 2;
  END;
  WHILE i <= len DO
  BEGIN
    IF (s[i] >= '0') AND (s[i] <= '9') THEN
      val := val * 10 + (ORD(s[i]) - ORD('0'));
    i := i + 1;
  END;
  IF neg THEN val := -val;
  StrToIntVal := val;
END;

FUNCTION StringEqual(s1, s2: Str255): BOOLEAN;
VAR
  len1, len2, i: INTEGER;
  eq: BOOLEAN;
BEGIN
  len1 := ORD(s1[0]);
  len2 := ORD(s2[0]);
  IF len1 <> len2 THEN
    StringEqual := FALSE
  ELSE
  BEGIN
    eq := TRUE;
    FOR i := 1 TO len1 DO
      IF s1[i] <> s2[i] THEN eq := FALSE;
    StringEqual := eq;
  END;
END;

PROCEDURE Expect(k: Str255);
VAR
  err_msg, ck, target_k: Str255;
  res_c: CINT;
BEGIN
  target_k := k;
  ck := CurKind();
  IF StringEqual(ck, target_k) THEN
  BEGIN
    pos := pos + 1;
  END
  ELSE
  BEGIN
    res_c := puts(MakeCStr('Parser Error: Expected token match failed. Expected:'));
    res_c := puts(MakeCStr(target_k));
    res_c := puts(MakeCStr('Got:'));
    res_c := puts(MakeCStr(ck));
    exit(1);
  END;
END;

FUNCTION Match(k: Str255): BOOLEAN;
VAR
  target_k: Str255;
BEGIN
  target_k := k;
  IF StringEqual(CurKind(), target_k) THEN
  BEGIN
    pos := pos + 1;
    Match := TRUE;
  END
  ELSE
    Match := FALSE;
END;

{ AST Builder Parser Stubs }

FUNCTION ParseExpression: ADRMEM; FORWARD;
FUNCTION ParseBooleanExpression: ADRMEM; FORWARD;
FUNCTION ParseSimpleExpression: ADRMEM; FORWARD;
FUNCTION ParseTerm: ADRMEM; FORWARD;
FUNCTION ParseFactor: ADRMEM; FORWARD;
FUNCTION ParseStatement: ADRMEM; FORWARD;
FUNCTION ParseBlock: ADRMEM; FORWARD;
FUNCTION ParseType: ADRMEM; FORWARD;

FUNCTION ParseIdentifier: ADRMEM;
VAR
  node: ADRMEM;
  name: Str255;
BEGIN
  node := CreateNode('Identifier');
  name := CurLex();
  Expect('IDENTIFIER');
  AddStringField(node, 'name', name);
  ParseIdentifier := node;
END;

FUNCTION ParseDesignatorRest(name: Str255): ADRMEM;
VAR
  node, selectors_arr, sel_obj: ADRMEM;
  has_sel: BOOLEAN;
BEGIN
  selectors_arr := cJSON_CreateArray;
  has_sel := FALSE;

  WHILE (CurKind() = 'LBRACKET') OR (CurKind() = 'DOT') OR (CurKind() = 'POINTER') DO
  BEGIN
    has_sel := TRUE;
    IF CurKind() = 'LBRACKET' THEN
    BEGIN
      pos := pos + 1;
      sel_obj := CreateNode('Selector');
      AddStringField(sel_obj, 'kind', 'INDEX');
      AddField(sel_obj, 'index_or_field', ParseExpression);
      WHILE Match('COMMA') DO
      BEGIN
        cJSON_AddItemToArray(selectors_arr, sel_obj);
        sel_obj := CreateNode('Selector');
        AddStringField(sel_obj, 'kind', 'INDEX');
        AddField(sel_obj, 'index_or_field', ParseExpression);
      END;
      Expect('RBRACKET');
      cJSON_AddItemToArray(selectors_arr, sel_obj);
    END
    ELSE IF CurKind() = 'DOT' THEN
    BEGIN
      pos := pos + 1;
      sel_obj := CreateNode('Selector');
      AddStringField(sel_obj, 'kind', 'FIELD');
      AddStringField(sel_obj, 'index_or_field', CurLex());
      Expect('IDENTIFIER');
      cJSON_AddItemToArray(selectors_arr, sel_obj);
    END
    ELSE IF CurKind() = 'POINTER' THEN
    BEGIN
      pos := pos + 1;
      sel_obj := CreateNode('Selector');
      AddStringField(sel_obj, 'kind', 'DEREF');
      AddNullField(sel_obj, 'index_or_field');
      cJSON_AddItemToArray(selectors_arr, sel_obj);
    END;
  END;

  IF has_sel THEN
  BEGIN
    node := CreateNode('Designator');
    AddStringField(node, 'name', name);
    AddField(node, 'selectors', selectors_arr);
    ParseDesignatorRest := node;
  END
  ELSE
  BEGIN
    node := CreateNode('Identifier');
    AddStringField(node, 'name', name);
    ParseDesignatorRest := node;
  END;
END;

FUNCTION ParseDesignator: ADRMEM;
VAR
  node, selectors_arr, sel_obj: ADRMEM;
  name: Str255;
BEGIN
  node := CreateNode('Designator');
  name := CurLex();
  Expect('IDENTIFIER');
  AddStringField(node, 'name', name);
  selectors_arr := cJSON_CreateArray;

  WHILE (CurKind() = 'LBRACKET') OR (CurKind() = 'DOT') OR (CurKind() = 'POINTER') DO
  BEGIN
    IF CurKind() = 'LBRACKET' THEN
    BEGIN
      pos := pos + 1;
      sel_obj := CreateNode('Selector');
      AddStringField(sel_obj, 'kind', 'INDEX');
      AddField(sel_obj, 'index_or_field', ParseExpression);
      Expect('RBRACKET');
      cJSON_AddItemToArray(selectors_arr, sel_obj);
    END
    ELSE IF CurKind() = 'DOT' THEN
    BEGIN
      pos := pos + 1;
      sel_obj := CreateNode('Selector');
      AddStringField(sel_obj, 'kind', 'FIELD');
      AddStringField(sel_obj, 'index_or_field', CurLex());
      Expect('IDENTIFIER');
      cJSON_AddItemToArray(selectors_arr, sel_obj);
    END
    ELSE IF CurKind() = 'POINTER' THEN
    BEGIN
      pos := pos + 1;
      sel_obj := CreateNode('Selector');
      AddStringField(sel_obj, 'kind', 'DEREF');
      AddNullField(sel_obj, 'index_or_field');
      cJSON_AddItemToArray(selectors_arr, sel_obj);
    END;
  END;

  AddField(node, 'selectors', selectors_arr);
  ParseDesignator := node;
END;

FUNCTION NextKind: Str255;
VAR
  pt: PToken;
  res: Str255;
BEGIN
  pt := GetTok(1);
  res := pt^.kind;
  NextKind := res;
END;

FUNCTION MakeBinOp(op_str: Str255; left, right: ADRMEM): ADRMEM;
VAR
  node: ADRMEM;
BEGIN
  node := CreateNode('BinOp');
  AddStringField(node, 'op', op_str);
  AddField(node, 'left', left);
  AddField(node, 'right', right);
  MakeBinOp := node;
END;

FUNCTION ParseActualParameterList: ADRMEM;
VAR
  args_arr: ADRMEM;
BEGIN
  args_arr := cJSON_CreateArray;
  IF CurKind() <> 'RPAREN' THEN
  BEGIN
    cJSON_AddItemToArray(args_arr, ParseExpression);
    WHILE Match('COMMA') DO
      cJSON_AddItemToArray(args_arr, ParseExpression);
  END;
  ParseActualParameterList := args_arr;
END;

FUNCTION ParseFactor: ADRMEM;
VAR
  node, expr, args_arr: ADRMEM;
  val_str, name, kop: Str255;
  res_c: CINT;
BEGIN
  IF CurKind() = 'NOT' THEN
  BEGIN
    pos := pos + 1;
    node := CreateNode('UnaryOp');
    AddStringField(node, 'op', 'NOT');
    AddField(node, 'operand', ParseFactor);
    ParseFactor := node;
  END
  ELSE IF CurKind() = 'INTEGER_LITERAL' THEN
  BEGIN
    node := CreateNode('IntLiteral');
    val_str := CurLex();
    Expect('INTEGER_LITERAL');
    AddIntField(node, 'value', StrToIntVal(val_str));
    ParseFactor := node;
  END
  ELSE IF CurKind() = 'CHAR_LITERAL' THEN
  BEGIN
    node := CreateNode('CharLiteral');
    val_str := CurLex();
    Expect('CHAR_LITERAL');
    AddStringField(node, 'value', val_str);
    ParseFactor := node;
  END
  ELSE IF CurKind() = 'STRING_LITERAL' THEN
  BEGIN
    node := CreateNode('StringLiteral');
    val_str := CurLex();
    Expect('STRING_LITERAL');
    AddStringField(node, 'value', val_str);
    ParseFactor := node;
  END
  ELSE IF CurKind() = 'BOOLEAN_LITERAL' THEN
  BEGIN
    node := CreateNode('BoolLiteral');
    val_str := CurLex();
    Expect('BOOLEAN_LITERAL');
    AddBoolField(node, 'value', StringEqual(val_str, 'TRUE') OR StringEqual(val_str, 'true'));
    ParseFactor := node;
  END
  ELSE IF CurKind() = 'NIL' THEN
  BEGIN
    pos := pos + 1;
    node := CreateNode('NilLiteral');
    ParseFactor := node;
  END
  ELSE IF CurKind() = 'IDENTIFIER' THEN
  BEGIN
    name := CurLex();
    IF NextKind() = 'LPAREN' THEN
    BEGIN
      pos := pos + 2;
      IF CurKind() <> 'RPAREN' THEN
        args_arr := ParseActualParameterList
      ELSE
        args_arr := cJSON_CreateArray;
      Expect('RPAREN');
      node := CreateNode('FuncCall');
      AddStringField(node, 'name', name);
      AddField(node, 'args', args_arr);
      ParseFactor := node;
    END
    ELSE
    BEGIN
      pos := pos + 1;
      ParseFactor := ParseDesignatorRest(name);
    END;
  END
  ELSE IF CurKind() = 'LPAREN' THEN
  BEGIN
    Expect('LPAREN');
    expr := ParseExpression;
    Expect('RPAREN');
    ParseFactor := expr;
  END
  ELSE
  BEGIN
    res_c := puts(MakeCStr('Parser Error: Invalid factor expression'));
    res_c := puts(MakeCStr(CurKind()));
    exit(1);
  END;
END;

FUNCTION ParseTerm: ADRMEM;
VAR
  left: ADRMEM;
  op_str: Str255;
  k: Str255;
BEGIN
  left := ParseFactor;
  k := CurKind();
  WHILE (k = 'MUL') OR (k = 'SLASH') OR (k = 'DIV') OR (k = 'MOD') OR (k = 'AND') DO
  BEGIN
    IF (k = 'AND') AND (NextKind() = 'THEN') THEN
      k := ''
    ELSE
    BEGIN
      op_str := k;
      pos := pos + 1;
      left := MakeBinOp(op_str, left, ParseFactor);
      k := CurKind();
    END;
  END;
  ParseTerm := left;
END;

FUNCTION ParseSimpleExpression: ADRMEM;
VAR
  left: ADRMEM;
  sign_minus: BOOLEAN;
  op_str, k: Str255;
  un: ADRMEM;
BEGIN
  sign_minus := FALSE;
  IF CurKind() = 'MINUS' THEN
  BEGIN
    sign_minus := TRUE;
    pos := pos + 1;
  END
  ELSE IF CurKind() = 'PLUS' THEN
    pos := pos + 1;
  left := ParseTerm;
  IF sign_minus THEN
  BEGIN
    un := CreateNode('UnaryOp');
    AddStringField(un, 'op', 'MINUS');
    AddField(un, 'operand', left);
    left := un;
  END;
  k := CurKind();
  WHILE (k = 'PLUS') OR (k = 'MINUS') OR (k = 'OR') OR (k = 'XOR') DO
  BEGIN
    IF (k = 'OR') AND (NextKind() = 'ELSE') THEN
      k := ''
    ELSE
    BEGIN
      op_str := k;
      pos := pos + 1;
      left := MakeBinOp(op_str, left, ParseTerm);
      k := CurKind();
    END;
  END;
  ParseSimpleExpression := left;
END;

FUNCTION ParseExpression: ADRMEM;
VAR
  left: ADRMEM;
  op_str, k: Str255;
BEGIN
  left := ParseSimpleExpression;
  k := CurKind();
  IF (k = 'EQ') OR (k = 'NEQ') OR (k = 'LT') OR (k = 'LE') OR (k = 'GT') OR (k = 'GE') OR (k = 'IN') THEN
  BEGIN
    op_str := k;
    pos := pos + 1;
    ParseExpression := MakeBinOp(op_str, left, ParseSimpleExpression);
  END
  ELSE
    ParseExpression := left;
END;

FUNCTION ParseBooleanExpression: ADRMEM;
VAR
  left: ADRMEM;
  op_str: Str255;
BEGIN
  left := ParseExpression;
  WHILE ((CurKind() = 'AND') AND (NextKind() = 'THEN')) OR ((CurKind() = 'OR') AND (NextKind() = 'ELSE')) DO
  BEGIN
    IF CurKind() = 'AND' THEN
      op_str := 'AND_THEN'
    ELSE
      op_str := 'OR_ELSE';
    pos := pos + 2;
    left := MakeBinOp(op_str, left, ParseExpression);
  END;
  ParseBooleanExpression := left;
END;

FUNCTION ParseAssignOrCallStmt: ADRMEM;
VAR
  node, target, args_arr: ADRMEM;
  pt: PToken;
  name: Str255;
BEGIN
  name := CurLex();
  pt := GetTok(1);
  IF (pt^.kind = 'LPAREN') OR (pt^.kind = 'SEMICOLON') THEN
  BEGIN
    node := CreateNode('ProcCallStmt');
    Expect('IDENTIFIER');
    AddStringField(node, 'name', name);
    args_arr := cJSON_CreateArray;
    IF Match('LPAREN') THEN
    BEGIN
      IF CurKind() <> 'RPAREN' THEN
      BEGIN
        cJSON_AddItemToArray(args_arr, ParseExpression);
        WHILE Match('COMMA') DO
          cJSON_AddItemToArray(args_arr, ParseExpression);
      END;
      Expect('RPAREN');
    END;
    AddField(node, 'args', args_arr);
    AddBoolField(node, 'rangeck', TRUE);
    AddNullField(node, 'meta_flags');
    ParseAssignOrCallStmt := node;
  END
  ELSE
  BEGIN
    node := CreateNode('AssignStmt');
    target := ParseDesignator;
    IF Match('ASSIGN') OR Match('EQ') THEN ;
    AddField(node, 'target', target);
    AddField(node, 'expr', ParseExpression);
    AddBoolField(node, 'rangeck', TRUE);
    AddNullField(node, 'meta_flags');
    ParseAssignOrCallStmt := node;
  END;
END;

FUNCTION ParseCompoundStmt: ADRMEM;
VAR
  node, stmts_arr: ADRMEM;
BEGIN
  Expect('BEGIN');
  node := CreateNode('CompoundStmt');
  stmts_arr := cJSON_CreateArray;
  WHILE (CurKind() <> 'END') AND (CurKind() <> 'EOF') DO
  BEGIN
    cJSON_AddItemToArray(stmts_arr, ParseStatement);
    IF CurKind() = 'SEMICOLON' THEN pos := pos + 1;
  END;
  Expect('END');
  AddField(node, 'stmts', stmts_arr);
  ParseCompoundStmt := node;
END;

FUNCTION ParseStatement: ADRMEM;
BEGIN
  IF CurKind() = 'BEGIN' THEN
    ParseStatement := ParseCompoundStmt
  ELSE IF CurKind() = 'IDENTIFIER' THEN
    ParseStatement := ParseAssignOrCallStmt
  ELSE
    ParseStatement := CreateNode('EmptyStmt');
END;

FUNCTION ParseType: ADRMEM;
VAR
  node: ADRMEM;
BEGIN
  node := CreateNode('NamedType');
  AddStringField(node, 'name', CurLex());
  AddNullField(node, 'param');
  Expect('IDENTIFIER');
  ParseType := node;
END;

FUNCTION ParseBlock: ADRMEM;
VAR
  node, decls_arr, stmts_arr, var_node, names_arr: ADRMEM;
  var_name: Str255;
BEGIN
  node := CreateNode('Block');
  decls_arr := cJSON_CreateArray;

  IF Match('VAR') THEN
  BEGIN
    WHILE CurKind() = 'IDENTIFIER' DO
    BEGIN
      var_name := CurLex();
      Expect('IDENTIFIER');
      Expect('COLON');
      var_node := CreateNode('VarDecl');
      names_arr := cJSON_CreateArray;
      cJSON_AddItemToArray(names_arr, cJSON_CreateString(MakeCStr(var_name)));
      AddField(var_node, 'names', names_arr);
      AddField(var_node, 'type_expr', ParseType);
      AddField(var_node, 'attributes', cJSON_CreateArray);
      AddNullField(var_node, 'meta_flags');
      Expect('SEMICOLON');
      cJSON_AddItemToArray(decls_arr, var_node);
    END;
  END;

  AddField(node, 'decls', decls_arr);

  IF CurKind() = 'BEGIN' THEN
    AddField(node, 'body', ParseCompoundStmt)
  ELSE
    AddField(node, 'body', cJSON_CreateArray);

  ParseBlock := node;
END;

FUNCTION ParseProgramUnit: ADRMEM;
VAR
  node, params_arr, uses_arr, block_node: ADRMEM;
  name: Str255;
  res_c: CINT;
BEGIN
  Expect('PROGRAM');
  name := CurLex();
  Expect('IDENTIFIER');

  node := CreateNode('ProgramUnit');
  AddStringField(node, 'name', name);

  params_arr := cJSON_CreateArray;
  IF Match('LPAREN') THEN
  BEGIN
    IF CurKind() <> 'RPAREN' THEN
    BEGIN
      cJSON_AddItemToArray(params_arr, cJSON_CreateString(MakeCStr(CurLex())));
      Expect('IDENTIFIER');
      WHILE Match('COMMA') DO
      BEGIN
        cJSON_AddItemToArray(params_arr, cJSON_CreateString(MakeCStr(CurLex())));
        Expect('IDENTIFIER');
      END;
    END;
    Expect('RPAREN');
  END;
  AddField(node, 'params', params_arr);

  Expect('SEMICOLON');

  uses_arr := cJSON_CreateArray;
  AddField(node, 'uses', uses_arr);

  block_node := ParseBlock;
  AddField(node, 'block', block_node);

  AddField(node, 'local_interfaces', cJSON_CreateArray);

  Expect('DOT');
  ParseProgramUnit := node;
END;

VAR
  ast_root, json_out: ADRMEM;
  res_c: CINT;

BEGIN
  pos := 0;
  ReadInputAndParseTokens;
  ast_root := ParseProgramUnit;
  json_out := cJSON_Print(ast_root);
  res_c := puts(json_out);
  free(json_out);
  cJSON_Delete(ast_root);
  free(tokens_buf);
END.
