{ INTERFACE header for jsonutil, the shared Str255/cJSON-node helper UNIT
  for the native pascal1981 stages (lexer.pas, parser.pas, typechecker.pas).
  Spliced via (*$INCLUDE:'jsonutil.inc'*) into every USES jsonutil caller,
  and into jsonutil.pas itself ahead of its IMPLEMENTATION. }

INTERFACE;
UNIT jsonutil (MakeCStr, CStrToStr255, CreateNode, AddField, AddStringField,
               AddIntField, AddRealField, AddBoolField, AddNullField);

TYPE
  Str255     = LSTRING(255);
  CharBuf256 = ARRAY [0..255] OF CHAR;
  PCharBuf   = ^CharBuf256;

FUNCTION MakeCStr(s: Str255): ADRMEM;
FUNCTION CStrToStr255(ptr: ADRMEM): Str255;
FUNCTION CreateNode(type_name: Str255): ADRMEM;
PROCEDURE AddField(obj: ADRMEM; key_str: Str255; val_node: ADRMEM);
PROCEDURE AddStringField(obj: ADRMEM; key_str: Str255; val_str: Str255);
PROCEDURE AddIntField(obj: ADRMEM; key_str: Str255; val_int: INTEGER);
PROCEDURE AddRealField(obj: ADRMEM; key_str: Str255; val_real: REAL);
PROCEDURE AddBoolField(obj: ADRMEM; key_str: Str255; val_bool: BOOLEAN);
PROCEDURE AddNullField(obj: ADRMEM; key_str: Str255);

END;
