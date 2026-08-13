{ Pascal-1981 Native Lexer implementation in extended IBM Pascal 2.0 dialect.
  Converts Pascal source code from standard input into JSON token stream on stdout. }

PROGRAM pascal1981_lex(input, output);

{ C-FFI bindings to libcjson and stdlib }
FUNCTION cJSON_CreateArray: ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateObject: ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateString(val: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateNumber(num: REAL): ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateBool(b: CINT): ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateNull: ADRMEM [C]; EXTERN;
PROCEDURE cJSON_AddItemToArray(arr: ADRMEM; item: ADRMEM) [C]; EXTERN;
PROCEDURE cJSON_AddItemToObject(obj: ADRMEM; key: ADRMEM; item: ADRMEM) [C]; EXTERN;
FUNCTION cJSON_Print(item: ADRMEM): ADRMEM [C]; EXTERN;
PROCEDURE cJSON_Delete(item: ADRMEM) [C]; EXTERN;
PROCEDURE puts(str: ADRMEM) [C]; EXTERN;
FUNCTION getchar: CINT [C]; EXTERN;
FUNCTION malloc(size: CINT): ADRMEM [C]; EXTERN;
PROCEDURE free(ptr: ADRMEM) [C]; EXTERN;
PROCEDURE c_exit(code: CINT) [C]; EXTERN;

TYPE
  Str255     = LSTRING(255);
  CharBuf256 = ARRAY [0..255] OF CHAR;
  PCharBuf   = ^CharBuf256;

  MetacmdFlags = RECORD
    Brave: BOOLEAN;
    Debug: BOOLEAN;
    Entry: BOOLEAN;
    GotoFlag: BOOLEAN;
    IndexCk: BOOLEAN;
    InitCk: BOOLEAN;
    LineFlag: BOOLEAN;
    List: BOOLEAN;
    MathCk: BOOLEAN;
    NilCk: BOOLEAN;
    Ocode: BOOLEAN;
    RangeCk: BOOLEAN;
    Runtime: BOOLEAN;
    StackCk: BOOLEAN;
    Symtab: BOOLEAN;
    Warn: BOOLEAN;
  END;

VAR
  root_array: ADRMEM;
  flags: MetacmdFlags;
  json_str: ADRMEM;
  
  { Source input buffer }
  src_buf: ADRMEM;
  src_len, src_pos: INTEGER;
  cur_line, cur_col: INTEGER;

PROCEDURE InitFlags(VAR f: MetacmdFlags);
BEGIN
  f.Brave := TRUE;
  f.Debug := TRUE;
  f.Entry := FALSE;
  f.GotoFlag := FALSE;
  f.IndexCk := TRUE;
  f.InitCk := FALSE;
  f.LineFlag := FALSE;
  f.List := TRUE;
  f.MathCk := TRUE;
  f.NilCk := TRUE;
  f.Ocode := TRUE;
  f.RangeCk := TRUE;
  f.Runtime := FALSE;
  f.StackCk := TRUE;
  f.Symtab := TRUE;
  f.Warn := TRUE;
END;

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

PROCEDURE AddFlagToObj(obj: ADRMEM; name_str: Str255; val: BOOLEAN);
VAR
  key_ptr: ADRMEM;
BEGIN
  key_ptr := MakeCStr(name_str);
  IF val THEN
    cJSON_AddItemToObject(obj, key_ptr, cJSON_CreateBool(1))
  ELSE
    cJSON_AddItemToObject(obj, key_ptr, cJSON_CreateBool(0));
END;

FUNCTION CreateFlagsObj(VAR f: MetacmdFlags): ADRMEM;
VAR
  f_obj: ADRMEM;
  s: Str255;
BEGIN
  f_obj := cJSON_CreateObject;
  s := 'BRAVE'; AddFlagToObj(f_obj, s, f.Brave);
  s := 'DEBUG'; AddFlagToObj(f_obj, s, f.Debug);
  s := 'ENTRY'; AddFlagToObj(f_obj, s, f.Entry);
  s := 'GOTO'; AddFlagToObj(f_obj, s, f.GotoFlag);
  s := 'INDEXCK'; AddFlagToObj(f_obj, s, f.IndexCk);
  s := 'INITCK'; AddFlagToObj(f_obj, s, f.InitCk);
  s := 'LINE'; AddFlagToObj(f_obj, s, f.LineFlag);
  s := 'LIST'; AddFlagToObj(f_obj, s, f.List);
  s := 'MATHCK'; AddFlagToObj(f_obj, s, f.MathCk);
  s := 'NILCK'; AddFlagToObj(f_obj, s, f.NilCk);
  s := 'OCODE'; AddFlagToObj(f_obj, s, f.Ocode);
  s := 'RANGECK'; AddFlagToObj(f_obj, s, f.RangeCk);
  s := 'RUNTIME'; AddFlagToObj(f_obj, s, f.Runtime);
  s := 'STACKCK'; AddFlagToObj(f_obj, s, f.StackCk);
  s := 'SYMTAB'; AddFlagToObj(f_obj, s, f.Symtab);
  s := 'WARN'; AddFlagToObj(f_obj, s, f.Warn);
  CreateFlagsObj := f_obj;
END;

PROCEDURE AddToken(kind: Str255; code: INTEGER; lexeme: Str255; val_type: INTEGER; int_val: INTEGER; real_val: REAL; str_val: Str255; line, col: INTEGER);
VAR
  tok_obj, val_item: ADRMEM;
  kind_ptr, lex_ptr, str_ptr, key_ptr: ADRMEM;
  fieldName: Str255;
BEGIN
  tok_obj := cJSON_CreateObject;
  
  kind_ptr := MakeCStr(kind);
  lex_ptr := MakeCStr(lexeme);

  fieldName := 'kind'; key_ptr := MakeCStr(fieldName);
  cJSON_AddItemToObject(tok_obj, key_ptr, cJSON_CreateString(kind_ptr));
  
  fieldName := 'code'; key_ptr := MakeCStr(fieldName);
  cJSON_AddItemToObject(tok_obj, key_ptr, cJSON_CreateNumber(code));

  fieldName := 'lexeme'; key_ptr := MakeCStr(fieldName);
  cJSON_AddItemToObject(tok_obj, key_ptr, cJSON_CreateString(lex_ptr));

  { Value field handling: 0=null, 1=int, 2=real, 3=str, 4=bool }
  IF val_type = 1 THEN
    val_item := cJSON_CreateNumber(int_val)
  ELSE IF val_type = 2 THEN
    val_item := cJSON_CreateNumber(real_val)
  ELSE IF val_type = 3 THEN
  BEGIN
    str_ptr := MakeCStr(str_val);
    val_item := cJSON_CreateString(str_ptr);
  END
  ELSE IF val_type = 4 THEN
    val_item := cJSON_CreateBool(int_val)
  ELSE
    val_item := cJSON_CreateNull;

  fieldName := 'value'; key_ptr := MakeCStr(fieldName);
  cJSON_AddItemToObject(tok_obj, key_ptr, val_item);

  fieldName := 'line'; key_ptr := MakeCStr(fieldName);
  cJSON_AddItemToObject(tok_obj, key_ptr, cJSON_CreateNumber(line));

  fieldName := 'column'; key_ptr := MakeCStr(fieldName);
  cJSON_AddItemToObject(tok_obj, key_ptr, cJSON_CreateNumber(col));

  fieldName := 'flags'; key_ptr := MakeCStr(fieldName);
  cJSON_AddItemToObject(tok_obj, key_ptr, CreateFlagsObj(flags));

  cJSON_AddItemToArray(root_array, tok_obj);
END;

FUNCTION GetKeywordCode(kw: Str255): INTEGER;
BEGIN
  IF kw = 'PROGRAM' THEN GetKeywordCode := 1
  ELSE IF kw = 'MODULE' THEN GetKeywordCode := 2
  ELSE IF kw = 'INTERFACE' THEN GetKeywordCode := 3
  ELSE IF kw = 'IMPLEMENTATION' THEN GetKeywordCode := 4
  ELSE IF kw = 'USES' THEN GetKeywordCode := 5
  ELSE IF kw = 'CONST' THEN GetKeywordCode := 6
  ELSE IF kw = 'TYPE' THEN GetKeywordCode := 7
  ELSE IF kw = 'VAR' THEN GetKeywordCode := 8
  ELSE IF kw = 'VALUE' THEN GetKeywordCode := 9
  ELSE IF kw = 'LABEL' THEN GetKeywordCode := 10
  ELSE IF kw = 'PROCEDURE' THEN GetKeywordCode := 11
  ELSE IF kw = 'FUNCTION' THEN GetKeywordCode := 12
  ELSE IF kw = 'BEGIN' THEN GetKeywordCode := 13
  ELSE IF kw = 'END' THEN GetKeywordCode := 14
  ELSE IF kw = 'IF' THEN GetKeywordCode := 15
  ELSE IF kw = 'THEN' THEN GetKeywordCode := 16
  ELSE IF kw = 'ELSE' THEN GetKeywordCode := 17
  ELSE IF kw = 'FOR' THEN GetKeywordCode := 18
  ELSE IF kw = 'TO' THEN GetKeywordCode := 19
  ELSE IF kw = 'DOWNTO' THEN GetKeywordCode := 20
  ELSE IF kw = 'DO' THEN GetKeywordCode := 21
  ELSE IF kw = 'REPEAT' THEN GetKeywordCode := 22
  ELSE IF kw = 'UNTIL' THEN GetKeywordCode := 23
  ELSE IF kw = 'WHILE' THEN GetKeywordCode := 24
  ELSE IF kw = 'CASE' THEN GetKeywordCode := 25
  ELSE IF kw = 'OF' THEN GetKeywordCode := 26
  ELSE IF kw = 'OTHERWISE' THEN GetKeywordCode := 27
  ELSE IF kw = 'WITH' THEN GetKeywordCode := 28
  ELSE IF kw = 'GOTO' THEN GetKeywordCode := 29
  ELSE IF kw = 'BREAK' THEN GetKeywordCode := 30
  ELSE IF kw = 'CYCLE' THEN GetKeywordCode := 31
  ELSE IF kw = 'RETURN' THEN GetKeywordCode := 32
  ELSE IF kw = 'EXTERN' THEN GetKeywordCode := 33
  ELSE IF kw = 'EXTERNAL' THEN GetKeywordCode := 34
  ELSE IF kw = 'FORWARD' THEN GetKeywordCode := 35
  ELSE IF kw = 'PACKED' THEN GetKeywordCode := 36
  ELSE IF kw = 'SUPER' THEN GetKeywordCode := 37
  ELSE IF kw = 'ARRAY' THEN GetKeywordCode := 38
  ELSE IF kw = 'RECORD' THEN GetKeywordCode := 39
  ELSE IF kw = 'SET' THEN GetKeywordCode := 40
  ELSE IF kw = 'FILE' THEN GetKeywordCode := 41
  ELSE IF kw = 'LSTRING' THEN GetKeywordCode := 42
  ELSE IF kw = 'ORIGIN' THEN GetKeywordCode := 43
  ELSE IF kw = 'READONLY' THEN GetKeywordCode := 44
  ELSE IF kw = 'PUBLIC' THEN GetKeywordCode := 45
  ELSE IF kw = 'STATIC' THEN GetKeywordCode := 46
  ELSE IF kw = 'PURE' THEN GetKeywordCode := 47
  ELSE IF kw = 'OVERLAY' THEN GetKeywordCode := 48
  ELSE IF kw = 'FORTRAN' THEN GetKeywordCode := 49
  ELSE IF kw = 'ADR' THEN GetKeywordCode := 50
  ELSE IF kw = 'SIZEOF' THEN GetKeywordCode := 51
  ELSE IF kw = 'UPPER' THEN GetKeywordCode := 52
  ELSE IF kw = 'IN' THEN GetKeywordCode := 53
  ELSE IF kw = 'DIV' THEN GetKeywordCode := 54
  ELSE IF kw = 'MOD' THEN GetKeywordCode := 55
  ELSE IF kw = 'OR' THEN GetKeywordCode := 56
  ELSE IF kw = 'XOR' THEN GetKeywordCode := 57
  ELSE IF kw = 'AND' THEN GetKeywordCode := 58
  ELSE IF kw = 'NOT' THEN GetKeywordCode := 87
  ELSE IF kw = 'UNIT' THEN GetKeywordCode := 88
  ELSE IF kw = 'VARS' THEN GetKeywordCode := 89
  ELSE IF kw = 'CONSTS' THEN GetKeywordCode := 90
  ELSE IF kw = 'NIL' THEN GetKeywordCode := 91
  ELSE IF kw = 'ADS' THEN GetKeywordCode := 92
  ELSE IF kw = 'LOWER' THEN GetKeywordCode := 93
  ELSE GetKeywordCode := 0;
END;

FUNCTION ReadBufChar(pos: INTEGER): CHAR;
VAR
  p: ^CHAR;
BEGIN
  IF (pos >= 0) AND (pos < src_len) THEN
  BEGIN
    p := src_buf + pos;
    ReadBufChar := p^;
  END
  ELSE
    ReadBufChar := CHR(0);
END;

PROCEDURE ReadSourceInput;
VAR
  input_ch: INTEGER32;
  cap, i: INTEGER;
  p, p_old, p_new: ^CHAR;
  old_buf: ADRMEM;
BEGIN
  cap := 32000;
  src_buf := malloc(cap);
  src_len := 0;
  input_ch := getchar;
  WHILE input_ch <> -1 DO
  BEGIN
    IF src_len >= cap THEN
    BEGIN
      { Reallocate buffer if full }
      old_buf := src_buf;
      cap := cap * 2;
      src_buf := malloc(cap);
      { Copy old buffer contents }
      FOR i := 0 TO src_len - 1 DO
      BEGIN
        p_old := old_buf + i;
        p_new := src_buf + i;
        p_new^ := p_old^;
      END;
      free(old_buf);
    END;
    p := src_buf + src_len;
    p^ := CHR(input_ch);
    src_len := src_len + 1;
    input_ch := getchar;
  END;
END;

PROCEDURE AdvancePos(count: INTEGER);
VAR
  i: INTEGER;
  ch: CHAR;
BEGIN
  FOR i := 1 TO count DO
  BEGIN
    IF src_pos < src_len THEN
    BEGIN
      ch := ReadBufChar(src_pos);
      src_pos := src_pos + 1;
      IF ch = CHR(10) THEN
      BEGIN
        cur_line := cur_line + 1;
        cur_col := 1;
      END
      ELSE
        cur_col := cur_col + 1;
    END;
  END;
END;

PROCEDURE SkipWhitespace;
VAR
  ch: CHAR;
  done: BOOLEAN;
BEGIN
  done := FALSE;
  WHILE (src_pos < src_len) AND NOT done DO
  BEGIN
    ch := ReadBufChar(src_pos);
    IF (ch = ' ') OR (ch = CHR(9)) OR (ch = CHR(13)) OR (ch = CHR(10)) THEN
      AdvancePos(1)
    ELSE
      done := TRUE;
  END;
END;

PROCEDURE SkipComments;
VAR
  ch, c2: CHAR;
  done: BOOLEAN;
BEGIN
  done := FALSE;
  WHILE (src_pos < src_len) AND NOT done DO
  BEGIN
    ch := ReadBufChar(src_pos);
    c2 := ReadBufChar(src_pos + 1);
    IF (ch = '(') AND (c2 = '*') THEN
    BEGIN
      AdvancePos(2);
      WHILE (src_pos < src_len) AND NOT ((ReadBufChar(src_pos) = '*') AND (ReadBufChar(src_pos + 1) = ')')) DO
        AdvancePos(1);
      IF src_pos < src_len THEN AdvancePos(2);
    END
    ELSE IF ch = '{' THEN
    BEGIN
      AdvancePos(1);
      WHILE (src_pos < src_len) AND (ReadBufChar(src_pos) <> '}') DO
        AdvancePos(1);
      IF src_pos < src_len THEN AdvancePos(1);
    END
    ELSE
      done := TRUE;
  END;
END;

FUNCTION IsAlpha(ch: CHAR): BOOLEAN;
BEGIN
  IsAlpha := ((ch >= 'a') AND (ch <= 'z')) OR ((ch >= 'A') AND (ch <= 'Z')) OR (ch = '_');
END;

FUNCTION IsDigit(ch: CHAR): BOOLEAN;
BEGIN
  IsDigit := (ch >= '0') AND (ch <= '9');
END;

FUNCTION UpCaseChar(ch: CHAR): CHAR;
BEGIN
  IF (ch >= 'a') AND (ch <= 'z') THEN
    UpCaseChar := CHR(ORD(ch) - 32)
  ELSE
    UpCaseChar := ch;
END;

PROCEDURE ScanIdentifier;
VAR
  start_pos, start_line, start_col, len: INTEGER;
  lexeme, upper_str: Str255;
  i, code: INTEGER;
  ch: CHAR;
  kind_str: Str255;
BEGIN
  start_pos := src_pos;
  start_line := cur_line;
  start_col := cur_col;
  
  WHILE (src_pos < src_len) AND (IsAlpha(ReadBufChar(src_pos)) OR IsDigit(ReadBufChar(src_pos))) DO
    AdvancePos(1);
  
  len := src_pos - start_pos;
  IF len > 255 THEN len := 255;
  lexeme[0] := CHR(len);
  upper_str[0] := CHR(len);
  FOR i := 1 TO len DO
  BEGIN
    ch := ReadBufChar(start_pos + i - 1);
    lexeme[i] := ch;
    upper_str[i] := UpCaseChar(ch);
  END;
  
  IF upper_str = 'TRUE' THEN
  BEGIN
    kind_str := 'BOOLEAN_LITERAL';
    AddToken(kind_str, 85, lexeme, 4, 1, 0.0, lexeme, start_line, start_col);
  END
  ELSE IF upper_str = 'FALSE' THEN
  BEGIN
    kind_str := 'BOOLEAN_LITERAL';
    AddToken(kind_str, 85, lexeme, 4, 0, 0.0, lexeme, start_line, start_col);
  END
  ELSE
  BEGIN
    code := GetKeywordCode(upper_str);
    IF code > 0 THEN
      AddToken(upper_str, code, lexeme, 3, 0, 0.0, upper_str, start_line, start_col)
    ELSE
    BEGIN
      kind_str := 'IDENTIFIER';
      AddToken(kind_str, 80, lexeme, 3, 0, 0.0, lexeme, start_line, start_col);
    END;
  END;
END;

PROCEDURE ScanNumber;
VAR
  start_pos, start_line, start_col, len, int_val, i: INTEGER;
  lexeme: Str255;
  ch: CHAR;
  kind_str: Str255;
BEGIN
  start_pos := src_pos;
  start_line := cur_line;
  start_col := cur_col;
  int_val := 0;

  WHILE (src_pos < src_len) AND IsDigit(ReadBufChar(src_pos)) DO
  BEGIN
    ch := ReadBufChar(src_pos);
    int_val := int_val * 10 + (ORD(ch) - ORD('0'));
    AdvancePos(1);
  END;

  len := src_pos - start_pos;
  IF len > 255 THEN len := 255;
  lexeme[0] := CHR(len);
  FOR i := 1 TO len DO
    lexeme[i] := ReadBufChar(start_pos + i - 1);

  kind_str := 'INTEGER_LITERAL';
  AddToken(kind_str, 81, lexeme, 1, int_val, 0.0, lexeme, start_line, start_col);
END;

PROCEDURE ScanString;
VAR
  start_line, start_col, str_len: INTEGER;
  ch: CHAR;
  str_val, lexeme: Str255;
  kind_str: Str255;
BEGIN
  start_line := cur_line;
  start_col := cur_col;
  AdvancePos(1); { Skip opening quote }
  str_len := 0;

  WHILE src_pos < src_len DO
  BEGIN
    ch := ReadBufChar(src_pos);
    IF ch = '''' THEN
    BEGIN
      IF (src_pos + 1 < src_len) AND (ReadBufChar(src_pos + 1) = '''') THEN
      BEGIN
        str_len := str_len + 1;
        IF str_len <= 255 THEN str_val[str_len] := '''';
        AdvancePos(2);
      END
      ELSE
      BEGIN
        AdvancePos(1);
        BREAK;
      END;
    END
    ELSE
    BEGIN
      str_len := str_len + 1;
      IF str_len <= 255 THEN str_val[str_len] := ch;
      AdvancePos(1);
    END;
  END;

  str_val[0] := CHR(str_len);
  lexeme := str_val;

  IF str_len = 1 THEN
  BEGIN
    kind_str := 'CHAR_LITERAL';
    AddToken(kind_str, 83, lexeme, 3, 0, 0.0, str_val, start_line, start_col);
  END
  ELSE
  BEGIN
    kind_str := 'STRING_LITERAL';
    AddToken(kind_str, 84, lexeme, 3, 0, 0.0, str_val, start_line, start_col);
  END;
END;

PROCEDURE ScanSymbol;
VAR
  start_line, start_col: INTEGER;
  ch, c2: CHAR;
  kind_str, lex_str: Str255;
BEGIN
  start_line := cur_line;
  start_col := cur_col;
  ch := ReadBufChar(src_pos);
  c2 := ReadBufChar(src_pos + 1);

  IF (ch = ':') AND (c2 = '=') THEN
  BEGIN
    AdvancePos(2);
    kind_str := 'ASSIGN'; lex_str := ':=';
    AddToken(kind_str, 59, lex_str, 3, 0, 0.0, lex_str, start_line, start_col);
  END
  ELSE IF (ch = '<') AND (c2 = '>') THEN
  BEGIN
    AdvancePos(2);
    kind_str := 'NEQ'; lex_str := '<>';
    AddToken(kind_str, 61, lex_str, 3, 0, 0.0, lex_str, start_line, start_col);
  END
  ELSE IF (ch = '<') AND (c2 = '=') THEN
  BEGIN
    AdvancePos(2);
    kind_str := 'LE'; lex_str := '<=';
    AddToken(kind_str, 63, lex_str, 3, 0, 0.0, lex_str, start_line, start_col);
  END
  ELSE IF (ch = '>') AND (c2 = '=') THEN
  BEGIN
    AdvancePos(2);
    kind_str := 'GE'; lex_str := '>=';
    AddToken(kind_str, 65, lex_str, 3, 0, 0.0, lex_str, start_line, start_col);
  END
  ELSE IF (ch = '.') AND (c2 = '.') THEN
  BEGIN
    AdvancePos(2);
    kind_str := 'RANGE'; lex_str := '..';
    AddToken(kind_str, 70, lex_str, 3, 0, 0.0, lex_str, start_line, start_col);
  END
  ELSE
  BEGIN
    AdvancePos(1);
    lex_str[0] := CHR(1); lex_str[1] := ch;
    IF ch = '=' THEN BEGIN kind_str := 'EQ'; AddToken(kind_str, 60, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END
    ELSE IF ch = '<' THEN BEGIN kind_str := 'LT'; AddToken(kind_str, 62, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END
    ELSE IF ch = '>' THEN BEGIN kind_str := 'GT'; AddToken(kind_str, 64, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END
    ELSE IF ch = '+' THEN BEGIN kind_str := 'PLUS'; AddToken(kind_str, 66, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END
    ELSE IF ch = '-' THEN BEGIN kind_str := 'MINUS'; AddToken(kind_str, 67, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END
    ELSE IF ch = '*' THEN BEGIN kind_str := 'MUL'; AddToken(kind_str, 68, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END
    ELSE IF ch = '/' THEN BEGIN kind_str := 'SLASH'; AddToken(kind_str, 69, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END
    ELSE IF ch = '^' THEN BEGIN kind_str := 'POINTER'; AddToken(kind_str, 71, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END
    ELSE IF ch = '[' THEN BEGIN kind_str := 'LBRACKET'; AddToken(kind_str, 72, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END
    ELSE IF ch = ']' THEN BEGIN kind_str := 'RBRACKET'; AddToken(kind_str, 73, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END
    ELSE IF ch = '(' THEN BEGIN kind_str := 'LPAREN'; AddToken(kind_str, 74, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END
    ELSE IF ch = ')' THEN BEGIN kind_str := 'RPAREN'; AddToken(kind_str, 75, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END
    ELSE IF ch = ';' THEN BEGIN kind_str := 'SEMICOLON'; AddToken(kind_str, 76, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END
    ELSE IF ch = ',' THEN BEGIN kind_str := 'COMMA'; AddToken(kind_str, 77, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END
    ELSE IF ch = ':' THEN BEGIN kind_str := 'COLON'; AddToken(kind_str, 78, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END
    ELSE IF ch = '.' THEN BEGIN kind_str := 'DOT'; AddToken(kind_str, 79, lex_str, 3, 0, 0.0, lex_str, start_line, start_col); END;
  END;
END;

VAR
  ch: CHAR;
  empty_str, eof_kind: Str255;

BEGIN
  InitFlags(flags);
  root_array := cJSON_CreateArray;
  cur_line := 1;
  cur_col := 1;
  src_pos := 0;

  ReadSourceInput;

  WHILE src_pos < src_len DO
  BEGIN
    SkipWhitespace;
    SkipComments;
    IF src_pos >= src_len THEN BREAK;
    
    ch := ReadBufChar(src_pos);
    IF IsAlpha(ch) THEN
      ScanIdentifier
    ELSE IF IsDigit(ch) THEN
      ScanNumber
    ELSE IF ch = '''' THEN
      ScanString
    ELSE
      ScanSymbol;
  END;

  { Add EOF token at end }
  eof_kind := 'EOF';
  empty_str := '';
  AddToken(eof_kind, 0, empty_str, 0, 0, 0.0, empty_str, cur_line, cur_col);

  json_str := cJSON_Print(root_array);
  puts(json_str);
  free(json_str);
  cJSON_Delete(root_array);
  free(src_buf);
END.
