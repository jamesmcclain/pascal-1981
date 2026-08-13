{ Shared Str255/cJSON-node helpers for the native pascal1981 stages (lexer.pas,
  parser.pas, and later typechecker.pas). Factored out once two independent
  copies of this boilerplate existed, to avoid a third. Compiles to its own
  object file; callers splice jsonutil.inc and USES jsonutil, then link
  against this unit's object file. }

(*$INCLUDE:'jsonutil.inc'*)
IMPLEMENTATION OF jsonutil;

FUNCTION malloc(size: CINT): ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateObject: ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateString(val: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateNumber(num: REAL): ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateBool(b: CINT): ADRMEM [C]; EXTERN;
FUNCTION cJSON_CreateNull: ADRMEM [C]; EXTERN;
PROCEDURE cJSON_AddItemToObject(obj: ADRMEM; key: ADRMEM; item: ADRMEM) [C]; EXTERN;

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

BEGIN
END.
