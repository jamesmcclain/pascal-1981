{ Native Pascal Code Generator, pascal1981-dialect implementation.

  Scope (v1, scalar core): a PROGRAM whose declarations are top-level scalar
  VAR declarations (INTEGER/REAL/BOOLEAN/CHAR) and whose body is built from
  assignment, IF/WHILE/REPEAT/FOR, compound statements, and WRITE/WRITELN of
  string-literal/scalar arguments. Integer/real/boolean/char expressions
  support the full arithmetic/relational/logical operator set, but operand
  types are not implicitly promoted (mixing INTEGER and REAL in one
  expression is rejected, not silently coerced). Everything else --
  procedures/functions, arrays/records/sets/pointers/files, C-ABI, units,
  CASE, WRITE width:precision, MATHCK/RANGECK-style runtime traps -- is out
  of scope for v1 and is rejected loudly via AbortWith rather than silently
  mishandled or miscompiled. This mirrors the phasing of the earlier native
  stages: reject unhandled constructs instead of guessing.

  Reads the annotated JSON AST produced by pascal1981-typecheck on standard
  input, builds an LLVM module via the LLVM-C API (linked against
  libLLVM), and prints the resulting IR to standard output. On any
  unsupported construct, prints a diagnostic and exits 1 without emitting
  IR, matching the other native stages' error convention. }

(*$INCLUDE:'jsonutil.inc'*)
PROGRAM pascal1981_codegen(input, output);

USES jsonutil;

FUNCTION LLVMContextCreate: ADRMEM [C]; EXTERN;
FUNCTION LLVMModuleCreateWithNameInContext(id: ADRMEM; ctx: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMInt32TypeInContext(ctx: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMInt16TypeInContext(ctx: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMInt8TypeInContext(ctx: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMInt1TypeInContext(ctx: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMDoubleTypeInContext(ctx: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMPointerType(elem_ty: ADRMEM; addr_space: CINT): ADRMEM [C]; EXTERN;
FUNCTION LLVMFunctionType(ret_ty: ADRMEM; params: ADRMEM; pcount: CINT; vararg: CINT): ADRMEM [C]; EXTERN;
FUNCTION LLVMAddFunction(m: ADRMEM; name: ADRMEM; fty: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMAppendBasicBlockInContext(ctx: ADRMEM; fn: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMCreateBuilderInContext(ctx: ADRMEM): ADRMEM [C]; EXTERN;
PROCEDURE LLVMPositionBuilderAtEnd(b: ADRMEM; bb: ADRMEM) [C]; EXTERN;
FUNCTION LLVMBuildGlobalStringPtr(b: ADRMEM; str: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMConstInt(ty: ADRMEM; n: CLONG; signext: CINT): ADRMEM [C]; EXTERN;
FUNCTION LLVMConstReal(ty: ADRMEM; n: REAL): ADRMEM [C]; EXTERN;
FUNCTION LLVMAddGlobal(m: ADRMEM; ty: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
PROCEDURE LLVMSetInitializer(gvar: ADRMEM; val: ADRMEM) [C]; EXTERN;
FUNCTION LLVMBuildLoad2(b: ADRMEM; ty: ADRMEM; ptr: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
PROCEDURE LLVMBuildStore(b: ADRMEM; val: ADRMEM; ptr: ADRMEM) [C]; EXTERN;
FUNCTION LLVMBuildAdd(b: ADRMEM; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildSub(b: ADRMEM; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildMul(b: ADRMEM; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildSDiv(b: ADRMEM; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildSRem(b: ADRMEM; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildFAdd(b: ADRMEM; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildFSub(b: ADRMEM; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildFMul(b: ADRMEM; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildFDiv(b: ADRMEM; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildAnd(b: ADRMEM; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildOr(b: ADRMEM; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildXor(b: ADRMEM; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildICmp(b: ADRMEM; pred: CINT; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildFCmp(b: ADRMEM; pred: CINT; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildSExt(b: ADRMEM; val: ADRMEM; destty: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
PROCEDURE LLVMBuildBr(b: ADRMEM; dest: ADRMEM) [C]; EXTERN;
PROCEDURE LLVMBuildCondBr(b: ADRMEM; cond: ADRMEM; then_bb: ADRMEM; else_bb: ADRMEM) [C]; EXTERN;
FUNCTION LLVMBuildCall2(b: ADRMEM; fty: ADRMEM; fn: ADRMEM; args: ADRMEM; nargs: CINT; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildRet(b: ADRMEM; v: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMPrintModuleToString(m: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMVerifyModule(m: ADRMEM; action: CINT; outmsg: ADRMEM): CINT [C]; EXTERN;
FUNCTION malloc(size: CINT): ADRMEM [C]; EXTERN;
FUNCTION puts(str: ADRMEM): CINT [C]; EXTERN;
PROCEDURE exit(code: CINT) [C]; EXTERN;
FUNCTION cJSON_GetStringValue(item: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION cJSON_IsNull(item: ADRMEM): CINT [C]; EXTERN;

CONST
  LLVMAbortProcessAction = 0;

  LLVMIntEQ  = 32; LLVMIntNE  = 33;
  LLVMIntSGT = 38; LLVMIntSGE = 39; LLVMIntSLT = 40; LLVMIntSLE = 41;

  LLVMRealOEQ = 1; LLVMRealOGT = 2; LLVMRealOGE = 3;
  LLVMRealOLT = 4; LLVMRealOLE = 5; LLVMRealONE = 6;

  TK_UNKNOWN = 0;
  TK_INTEGER = 1;
  TK_REAL    = 2;
  TK_BOOLEAN = 3;
  TK_CHAR    = 4;

  MAX_SYMBOLS = 500;

TYPE
  PAdr = ^ADRMEM;

  SymRec = RECORD
    name: Str255;
    tk: INTEGER;
    llvm_val: ADRMEM; { the LLVMValueRef of the global variable slot }
  END;

VAR
  ctx, modl, builder: ADRMEM;
  i32ty, i16ty, i8ty, i1ty, dblty, i8ptrty: ADRMEM;
  main_fnty, main_fn, entry_bb: ADRMEM;
  printf_fnty, printf_fn: ADRMEM;

  symbols: ARRAY [1..MAX_SYMBOLS] OF SymRec;
  nsymbols: INTEGER32;

  last_val_tk: INTEGER; { side-channel result of CodegenExpr, mirroring the
                          typechecker's own aux-field convention: the dialect
                          has no tuple returns, so the type of the most
                          recently codegen'd expression is communicated back
                          through this global rather than threaded as a var
                          parameter through every call site. }

{ ============================== utilities ============================== }

PROCEDURE AbortWith(msg: Str255);
VAR
  res_c: CINT;
BEGIN
  res_c := puts(MakeCStr(msg));
  exit(1);
END;

FUNCTION GetObjOrNil(obj: ADRMEM; key: Str255): ADRMEM;
{ jsonutil's GetObj returns cJSON's own pointer for a key whose JSON value
  is literally `null` -- a real, non-NULL cJSON node of type cJSON_NULL, not
  a NIL/absent-key sentinel. Optional AST fields (e.g. IfStmt's
  else_branch) are serialized as `null` when absent, so a bare "GetObj(...)
  <> NIL" check treats "no else" the same as "else present" and then fails
  trying to codegen a nonexistent statement. Fold both "absent" and
  "present but null" down to NIL here so callers can use one check. }
VAR
  v: ADRMEM;
BEGIN
  v := GetObj(obj, key);
  IF (v <> NIL) AND (cJSON_IsNull(v) <> 0) THEN v := NIL;
  GetObjOrNil := v;
END;

PROCEDURE AbortWith2(prefix: Str255; suffix: Str255);
VAR
  msg: Str255;
BEGIN
  msg := prefix;
  CONCAT(msg, suffix);
  AbortWith(msg);
END;

FUNCTION DecodeStringLiteral(raw: Str255): Str255;
{ raw is the token lexeme convention: outer single quotes kept, embedded
  quote pairs ('') collapsed to a single quote -- the inverse of lexer.py's
  "'" + value.replace("'", "''") + "'". }
VAR
  res: Str255;
  len, i, outlen: INTEGER;
BEGIN
  len := ORD(raw[0]);
  outlen := 0;
  IF len < 2 THEN AbortWith('codegen v1: malformed string literal');
  i := 2;
  WHILE i <= len - 1 DO
  BEGIN
    IF (raw[i] = '''') AND (i + 1 <= len - 1) AND (raw[i + 1] = '''') THEN
    BEGIN
      outlen := outlen + 1;
      res[outlen] := '''';
      i := i + 2;
    END
    ELSE
    BEGIN
      outlen := outlen + 1;
      res[outlen] := raw[i];
      i := i + 1;
    END;
  END;
  res[0] := CHR(outlen);
  DecodeStringLiteral := res;
END;

PROCEDURE AppendChar(VAR s: Str255; ch: CHAR);
VAR
  len: INTEGER;
BEGIN
  len := ORD(s[0]);
  IF len < 255 THEN
  BEGIN
    s[len + 1] := ch;
    s[0] := CHR(len + 1);
  END;
END;

FUNCTION AllocPtrArray(n: INTEGER32): ADRMEM;
{ The N-element generalization of the malloc-and-cast idiom jsonutil.pas
  already uses for C strings: llvm-c takes LLVMTypeRef*/LLVMValueRef*
  arrays as a raw pointer + count. }
BEGIN
  AllocPtrArray := malloc(n * 8);
END;

PROCEDURE SetPtrArrayElem(arr: ADRMEM; idx: INTEGER32; v: ADRMEM);
VAR
  base, cell: PAdr;
BEGIN
  base := arr;
  cell := base + idx;
  cell^ := v;
END;

{ ============================== type model =============================== }

FUNCTION ResolveTypeExpr(te: ADRMEM): INTEGER;
VAR
  nm: Str255;
BEGIN
  IF NodeType(te) <> 'NamedType' THEN
    AbortWith('codegen v1: only bare scalar type names are supported');
  nm := GetStr(te, 'name');
  IF nm = 'INTEGER' THEN ResolveTypeExpr := TK_INTEGER
  ELSE IF nm = 'REAL' THEN ResolveTypeExpr := TK_REAL
  ELSE IF nm = 'BOOLEAN' THEN ResolveTypeExpr := TK_BOOLEAN
  ELSE IF nm = 'CHAR' THEN ResolveTypeExpr := TK_CHAR
  ELSE
  BEGIN
    AbortWith2('codegen v1: unsupported scalar type: ', nm);
    ResolveTypeExpr := TK_UNKNOWN;
  END;
END;

FUNCTION LLVMTypeForTk(tk: INTEGER): ADRMEM;
BEGIN
  IF tk = TK_INTEGER THEN LLVMTypeForTk := i16ty
  ELSE IF tk = TK_REAL THEN LLVMTypeForTk := dblty
  ELSE IF tk = TK_BOOLEAN THEN LLVMTypeForTk := i1ty
  ELSE IF tk = TK_CHAR THEN LLVMTypeForTk := i8ty
  ELSE
  BEGIN
    AbortWith('codegen v1: LLVMTypeForTk: unknown type kind');
    LLVMTypeForTk := NIL;
  END;
END;

{ ============================ symbol table ============================== }

FUNCTION LookupSym(name: Str255): INTEGER32;
VAR
  i: INTEGER32;
  found: INTEGER32;
BEGIN
  found := 0;
  FOR i := 1 TO nsymbols DO
    IF symbols[i].name = name THEN found := i;
  LookupSym := found;
END;

PROCEDURE DeclareVar(name: Str255; tk: INTEGER);
VAR
  gvar, zero: ADRMEM;
BEGIN
  IF LookupSym(name) <> 0 THEN
    AbortWith2('codegen v1: duplicate declaration: ', name);
  gvar := LLVMAddGlobal(modl, LLVMTypeForTk(tk), MakeCStr(name));
  IF tk = TK_REAL THEN zero := LLVMConstReal(dblty, 0.0)
  ELSE zero := LLVMConstInt(LLVMTypeForTk(tk), 0, 0);
  LLVMSetInitializer(gvar, zero);
  nsymbols := nsymbols + 1;
  symbols[nsymbols].name := name;
  symbols[nsymbols].tk := tk;
  symbols[nsymbols].llvm_val := gvar;
END;

{ ============================== expressions =============================== }

FUNCTION CodegenExpr(node: ADRMEM): ADRMEM; FORWARD;

FUNCTION CodegenBinOp(op: Str255; left_node, right_node: ADRMEM): ADRMEM;
VAR
  lval, rval, res: ADRMEM;
  ltk, rtk: INTEGER;
BEGIN
  lval := CodegenExpr(left_node);
  ltk := last_val_tk;
  rval := CodegenExpr(right_node);
  rtk := last_val_tk;

  { A single flat ELSE IF chain, deliberately avoiding the bare EXIT
    statement: EXIT from deep inside nested IFs inside a FUNCTION triggers a
    pre-existing crash in the Python reference compiler's C-ABI call
    codegen (c_abi.py's codegen_c_abi_call indexes past the end of an empty
    arg list) that this repository's own native sources never happened to
    exercise before. Restructuring to a single terminal assignment sidesteps
    it without touching the compiler that builds this very file. }
  IF (op = 'AND') OR (op = 'OR') THEN
  BEGIN
    IF (ltk <> TK_BOOLEAN) OR (rtk <> TK_BOOLEAN) THEN
      AbortWith('codegen v1: AND/OR require BOOLEAN operands');
    IF op = 'AND' THEN res := LLVMBuildAnd(builder, lval, rval, MakeCStr(''))
    ELSE res := LLVMBuildOr(builder, lval, rval, MakeCStr(''));
    last_val_tk := TK_BOOLEAN;
  END
  ELSE IF ltk <> rtk THEN
  BEGIN
    AbortWith('codegen v1: mixed-type operands are not supported (no implicit promotion)');
    res := NIL;
  END
  ELSE IF (op = 'EQ') OR (op = 'NEQ') OR (op = 'LT') OR (op = 'LE') OR (op = 'GT') OR (op = 'GE') THEN
  BEGIN
    IF ltk = TK_INTEGER THEN
    BEGIN
      IF op = 'EQ' THEN res := LLVMBuildICmp(builder, LLVMIntEQ, lval, rval, MakeCStr(''))
      ELSE IF op = 'NEQ' THEN res := LLVMBuildICmp(builder, LLVMIntNE, lval, rval, MakeCStr(''))
      ELSE IF op = 'LT' THEN res := LLVMBuildICmp(builder, LLVMIntSLT, lval, rval, MakeCStr(''))
      ELSE IF op = 'LE' THEN res := LLVMBuildICmp(builder, LLVMIntSLE, lval, rval, MakeCStr(''))
      ELSE IF op = 'GT' THEN res := LLVMBuildICmp(builder, LLVMIntSGT, lval, rval, MakeCStr(''))
      ELSE res := LLVMBuildICmp(builder, LLVMIntSGE, lval, rval, MakeCStr(''));
    END
    ELSE IF ltk = TK_REAL THEN
    BEGIN
      IF op = 'EQ' THEN res := LLVMBuildFCmp(builder, LLVMRealOEQ, lval, rval, MakeCStr(''))
      ELSE IF op = 'NEQ' THEN res := LLVMBuildFCmp(builder, LLVMRealONE, lval, rval, MakeCStr(''))
      ELSE IF op = 'LT' THEN res := LLVMBuildFCmp(builder, LLVMRealOLT, lval, rval, MakeCStr(''))
      ELSE IF op = 'LE' THEN res := LLVMBuildFCmp(builder, LLVMRealOLE, lval, rval, MakeCStr(''))
      ELSE IF op = 'GT' THEN res := LLVMBuildFCmp(builder, LLVMRealOGT, lval, rval, MakeCStr(''))
      ELSE res := LLVMBuildFCmp(builder, LLVMRealOGE, lval, rval, MakeCStr(''));
    END
    ELSE
    BEGIN
      AbortWith('codegen v1: relational operators support only INTEGER/REAL operands');
      res := NIL;
    END;
    last_val_tk := TK_BOOLEAN;
  END
  ELSE IF ltk = TK_INTEGER THEN
  BEGIN
    IF op = 'PLUS' THEN res := LLVMBuildAdd(builder, lval, rval, MakeCStr(''))
    ELSE IF op = 'MINUS' THEN res := LLVMBuildSub(builder, lval, rval, MakeCStr(''))
    ELSE IF op = 'MUL' THEN res := LLVMBuildMul(builder, lval, rval, MakeCStr(''))
    ELSE IF op = 'DIV' THEN res := LLVMBuildSDiv(builder, lval, rval, MakeCStr(''))
    ELSE IF op = 'MOD' THEN res := LLVMBuildSRem(builder, lval, rval, MakeCStr(''))
    ELSE
    BEGIN
      AbortWith2('codegen v1: unhandled INTEGER operator: ', op);
      res := NIL;
    END;
    last_val_tk := TK_INTEGER;
  END
  ELSE IF ltk = TK_REAL THEN
  BEGIN
    IF op = 'PLUS' THEN res := LLVMBuildFAdd(builder, lval, rval, MakeCStr(''))
    ELSE IF op = 'MINUS' THEN res := LLVMBuildFSub(builder, lval, rval, MakeCStr(''))
    ELSE IF op = 'MUL' THEN res := LLVMBuildFMul(builder, lval, rval, MakeCStr(''))
    ELSE IF op = 'SLASH' THEN res := LLVMBuildFDiv(builder, lval, rval, MakeCStr(''))
    ELSE
    BEGIN
      AbortWith2('codegen v1: unhandled REAL operator: ', op);
      res := NIL;
    END;
    last_val_tk := TK_REAL;
  END
  ELSE
  BEGIN
    AbortWith('codegen v1: arithmetic operators support only INTEGER/REAL operands');
    res := NIL;
  END;
  CodegenBinOp := res;
END;

FUNCTION CodegenUnaryOp(op: Str255; operand_node: ADRMEM): ADRMEM;
VAR
  v, res: ADRMEM;
  tk: INTEGER;
BEGIN
  v := CodegenExpr(operand_node);
  tk := last_val_tk;
  IF op = 'MINUS' THEN
  BEGIN
    IF tk = TK_INTEGER THEN res := LLVMBuildSub(builder, LLVMConstInt(i16ty, 0, 1), v, MakeCStr(''))
    ELSE IF tk = TK_REAL THEN res := LLVMBuildFSub(builder, LLVMConstReal(dblty, 0.0), v, MakeCStr(''))
    ELSE
    BEGIN
      AbortWith('codegen v1: unary MINUS requires INTEGER/REAL operand');
      res := NIL;
    END;
    last_val_tk := tk;
  END
  ELSE IF op = 'NOT' THEN
  BEGIN
    IF tk <> TK_BOOLEAN THEN
      AbortWith('codegen v1: NOT requires a BOOLEAN operand');
    res := LLVMBuildXor(builder, v, LLVMConstInt(i1ty, 1, 0), MakeCStr(''));
    last_val_tk := TK_BOOLEAN;
  END
  ELSE
  BEGIN
    AbortWith2('codegen v1: unhandled unary operator: ', op);
    res := NIL;
  END;
  CodegenUnaryOp := res;
END;

FUNCTION CodegenExpr(node: ADRMEM): ADRMEM;
VAR
  nt: Str255;
  nm: Str255;
  symi: INTEGER32;
  ch: Str255;
  res: ADRMEM;
BEGIN
  nt := NodeType(node);
  IF nt = 'IntLiteral' THEN
  BEGIN
    res := LLVMConstInt(i16ty, GetInt(node, 'value'), 1);
    last_val_tk := TK_INTEGER;
  END
  ELSE IF nt = 'RealLiteral' THEN
  BEGIN
    res := LLVMConstReal(dblty, GetReal(node, 'value'));
    last_val_tk := TK_REAL;
  END
  ELSE IF nt = 'CharLiteral' THEN
  BEGIN
    ch := GetStr(node, 'value');
    res := LLVMConstInt(i8ty, ORD(ch[1]), 0);
    last_val_tk := TK_CHAR;
  END
  ELSE IF nt = 'Identifier' THEN
  BEGIN
    nm := GetStr(node, 'name');
    symi := LookupSym(nm);
    IF symi = 0 THEN
    BEGIN
      AbortWith2('codegen v1: undefined variable: ', nm);
      res := NIL;
    END
    ELSE
    BEGIN
      res := LLVMBuildLoad2(builder, LLVMTypeForTk(symbols[symi].tk), symbols[symi].llvm_val, MakeCStr(''));
      last_val_tk := symbols[symi].tk;
    END;
  END
  ELSE IF nt = 'BinOp' THEN
    res := CodegenBinOp(GetStr(node, 'op'), GetObj(node, 'left'), GetObj(node, 'right'))
  ELSE IF nt = 'UnaryOp' THEN
    res := CodegenUnaryOp(GetStr(node, 'op'), GetObj(node, 'operand'))
  ELSE
  BEGIN
    AbortWith2('codegen v1: unhandled expression kind: ', nt);
    res := NIL;
  END;
  CodegenExpr := res;
END;

{ ============================ WRITE/WRITELN =============================== }

PROCEDURE CodegenWriteArgs(args: ADRMEM; newline: BOOLEAN);
VAR
  nargs, i: INTEGER32;
  fmt: Str255;
  arg_node, expr: ADRMEM;
  vals: ADRMEM;
  v: ADRMEM;
  strval: Str255;
  call_ret: ADRMEM;
BEGIN
  nargs := ArrSize(args);
  fmt := '';
  vals := AllocPtrArray(nargs + 1);
  FOR i := 0 TO nargs - 1 DO
  BEGIN
    arg_node := ArrItem(args, i);
    IF NodeType(arg_node) <> 'WriteArg' THEN
      AbortWith('codegen v1: expected WriteArg node');
    expr := GetObj(arg_node, 'expr');
    IF NodeType(expr) = 'StringLiteral' THEN
    BEGIN
      strval := DecodeStringLiteral(GetStr(expr, 'value'));
      v := LLVMBuildGlobalStringPtr(builder, MakeCStr(strval), MakeCStr('str'));
      CONCAT(fmt, '%s');
    END
    ELSE
    BEGIN
      v := CodegenExpr(expr);
      IF last_val_tk = TK_INTEGER THEN
      BEGIN
        v := LLVMBuildSExt(builder, v, i32ty, MakeCStr(''));
        CONCAT(fmt, '%d');
      END
      ELSE IF last_val_tk = TK_REAL THEN
        CONCAT(fmt, '%14.7E')
      ELSE IF last_val_tk = TK_CHAR THEN
        CONCAT(fmt, '%c')
      ELSE
        AbortWith('codegen v1: unsupported WRITE argument type');
    END;
    SetPtrArrayElem(vals, i + 1, v);
  END;
  IF newline THEN AppendChar(fmt, CHR(10));
  SetPtrArrayElem(vals, 0, LLVMBuildGlobalStringPtr(builder, MakeCStr(fmt), MakeCStr('fmt')));
  call_ret := LLVMBuildCall2(builder, printf_fnty, printf_fn, vals, nargs + 1, MakeCStr('callprintf'));
END;

{ ============================== statements ================================ }

PROCEDURE CodegenStmt(stmt: ADRMEM); FORWARD;

PROCEDURE CodegenStmtArray(arr: ADRMEM);
VAR
  n, i: INTEGER32;
BEGIN
  n := ArrSize(arr);
  FOR i := 0 TO n - 1 DO
    CodegenStmt(ArrItem(arr, i));
END;

PROCEDURE CodegenAssignStmt(stmt: ADRMEM);
VAR
  target, sel: ADRMEM;
  nm: Str255;
  symi: INTEGER32;
  v: ADRMEM;
BEGIN
  target := GetObj(stmt, 'target');
  IF NodeType(target) <> 'Designator' THEN
    AbortWith('codegen v1: unsupported assignment target');
  sel := GetObj(target, 'selectors');
  IF ArrSize(sel) <> 0 THEN
    AbortWith('codegen v1: indexed/field assignment targets are not supported');
  nm := GetStr(target, 'name');
  symi := LookupSym(nm);
  IF symi = 0 THEN
    AbortWith2('codegen v1: undefined variable: ', nm);
  v := CodegenExpr(GetObj(stmt, 'expr'));
  IF last_val_tk <> symbols[symi].tk THEN
    AbortWith2('codegen v1: assignment type mismatch for: ', nm);
  LLVMBuildStore(builder, v, symbols[symi].llvm_val);
END;

PROCEDURE CodegenIfStmt(stmt: ADRMEM);
VAR
  cond_val: ADRMEM;
  then_bb, else_bb, end_bb: ADRMEM;
  else_branch: ADRMEM;
BEGIN
  cond_val := CodegenExpr(GetObj(stmt, 'cond'));
  IF last_val_tk <> TK_BOOLEAN THEN
    AbortWith('codegen v1: IF condition must be BOOLEAN');

  then_bb := LLVMAppendBasicBlockInContext(ctx, main_fn, MakeCStr('if_then'));
  end_bb := LLVMAppendBasicBlockInContext(ctx, main_fn, MakeCStr('if_end'));
  else_branch := GetObjOrNil(stmt, 'else_branch');
  IF else_branch <> NIL THEN
    else_bb := LLVMAppendBasicBlockInContext(ctx, main_fn, MakeCStr('if_else'))
  ELSE
    else_bb := end_bb;

  LLVMBuildCondBr(builder, cond_val, then_bb, else_bb);

  LLVMPositionBuilderAtEnd(builder, then_bb);
  CodegenStmt(GetObj(stmt, 'then_branch'));
  LLVMBuildBr(builder, end_bb);

  IF else_branch <> NIL THEN
  BEGIN
    LLVMPositionBuilderAtEnd(builder, else_bb);
    CodegenStmt(else_branch);
    LLVMBuildBr(builder, end_bb);
  END;

  LLVMPositionBuilderAtEnd(builder, end_bb);
END;

PROCEDURE CodegenWhileStmt(stmt: ADRMEM);
VAR
  loop_bb, body_bb, end_bb, cond_val: ADRMEM;
BEGIN
  loop_bb := LLVMAppendBasicBlockInContext(ctx, main_fn, MakeCStr('while_loop'));
  body_bb := LLVMAppendBasicBlockInContext(ctx, main_fn, MakeCStr('while_body'));
  end_bb := LLVMAppendBasicBlockInContext(ctx, main_fn, MakeCStr('while_end'));

  LLVMBuildBr(builder, loop_bb);
  LLVMPositionBuilderAtEnd(builder, loop_bb);
  cond_val := CodegenExpr(GetObj(stmt, 'cond'));
  IF last_val_tk <> TK_BOOLEAN THEN
    AbortWith('codegen v1: WHILE condition must be BOOLEAN');
  LLVMBuildCondBr(builder, cond_val, body_bb, end_bb);

  LLVMPositionBuilderAtEnd(builder, body_bb);
  CodegenStmt(GetObj(stmt, 'body'));
  LLVMBuildBr(builder, loop_bb);

  LLVMPositionBuilderAtEnd(builder, end_bb);
END;

PROCEDURE CodegenRepeatStmt(stmt: ADRMEM);
VAR
  loop_bb, end_bb, cond_val: ADRMEM;
BEGIN
  loop_bb := LLVMAppendBasicBlockInContext(ctx, main_fn, MakeCStr('repeat_loop'));
  end_bb := LLVMAppendBasicBlockInContext(ctx, main_fn, MakeCStr('repeat_end'));

  LLVMBuildBr(builder, loop_bb);
  LLVMPositionBuilderAtEnd(builder, loop_bb);
  CodegenStmtArray(GetObj(stmt, 'body'));
  cond_val := CodegenExpr(GetObj(stmt, 'cond'));
  IF last_val_tk <> TK_BOOLEAN THEN
    AbortWith('codegen v1: REPEAT..UNTIL condition must be BOOLEAN');
  LLVMBuildCondBr(builder, cond_val, end_bb, loop_bb);

  LLVMPositionBuilderAtEnd(builder, end_bb);
END;

PROCEDURE CodegenForStmt(stmt: ADRMEM);
VAR
  var_name: Str255;
  symi: INTEGER32;
  start_val, end_val, cur_val, cmp_val, next_val: ADRMEM;
  loop_bb, body_bb, step_bb, end_bb: ADRMEM;
  down: BOOLEAN;
BEGIN
  var_name := GetStr(stmt, 'var');
  symi := LookupSym(var_name);
  IF symi = 0 THEN
    AbortWith2('codegen v1: undefined FOR loop variable: ', var_name);
  IF symbols[symi].tk <> TK_INTEGER THEN
    AbortWith('codegen v1: FOR loop variable must be INTEGER');

  start_val := CodegenExpr(GetObj(stmt, 'start'));
  IF last_val_tk <> TK_INTEGER THEN
    AbortWith('codegen v1: FOR loop bounds must be INTEGER');
  LLVMBuildStore(builder, start_val, symbols[symi].llvm_val);

  end_val := CodegenExpr(GetObj(stmt, 'end'));
  IF last_val_tk <> TK_INTEGER THEN
    AbortWith('codegen v1: FOR loop bounds must be INTEGER');

  down := GetStr(stmt, 'direction') = 'DOWNTO';

  loop_bb := LLVMAppendBasicBlockInContext(ctx, main_fn, MakeCStr('for_loop'));
  body_bb := LLVMAppendBasicBlockInContext(ctx, main_fn, MakeCStr('for_body'));
  step_bb := LLVMAppendBasicBlockInContext(ctx, main_fn, MakeCStr('for_step'));
  end_bb := LLVMAppendBasicBlockInContext(ctx, main_fn, MakeCStr('for_end'));

  LLVMBuildBr(builder, loop_bb);
  LLVMPositionBuilderAtEnd(builder, loop_bb);
  cur_val := LLVMBuildLoad2(builder, i16ty, symbols[symi].llvm_val, MakeCStr(''));
  IF down THEN
    cmp_val := LLVMBuildICmp(builder, LLVMIntSGE, cur_val, end_val, MakeCStr(''))
  ELSE
    cmp_val := LLVMBuildICmp(builder, LLVMIntSLE, cur_val, end_val, MakeCStr(''));
  LLVMBuildCondBr(builder, cmp_val, body_bb, end_bb);

  LLVMPositionBuilderAtEnd(builder, body_bb);
  CodegenStmt(GetObj(stmt, 'body'));
  LLVMBuildBr(builder, step_bb);

  LLVMPositionBuilderAtEnd(builder, step_bb);
  cur_val := LLVMBuildLoad2(builder, i16ty, symbols[symi].llvm_val, MakeCStr(''));
  IF down THEN
    next_val := LLVMBuildSub(builder, cur_val, LLVMConstInt(i16ty, 1, 0), MakeCStr(''))
  ELSE
    next_val := LLVMBuildAdd(builder, cur_val, LLVMConstInt(i16ty, 1, 0), MakeCStr(''));
  LLVMBuildStore(builder, next_val, symbols[symi].llvm_val);
  LLVMBuildBr(builder, loop_bb);

  LLVMPositionBuilderAtEnd(builder, end_bb);
END;

PROCEDURE CodegenProcCallStmt(stmt: ADRMEM);
VAR
  name, msg: Str255;
BEGIN
  name := GetStr(stmt, 'name');
  IF name = 'WRITELN' THEN
    CodegenWriteArgs(GetObj(stmt, 'args'), TRUE)
  ELSE IF name = 'WRITE' THEN
    CodegenWriteArgs(GetObj(stmt, 'args'), FALSE)
  ELSE
  BEGIN
    msg := 'codegen v1: unhandled procedure call: ';
    CONCAT(msg, name);
    AbortWith(msg);
  END;
END;

PROCEDURE CodegenStmt(stmt: ADRMEM);
VAR
  nt, msg: Str255;
BEGIN
  nt := NodeType(stmt);
  IF nt = 'AssignStmt' THEN CodegenAssignStmt(stmt)
  ELSE IF nt = 'CompoundStmt' THEN CodegenStmtArray(GetObj(stmt, 'stmts'))
  ELSE IF nt = 'IfStmt' THEN CodegenIfStmt(stmt)
  ELSE IF nt = 'WhileStmt' THEN CodegenWhileStmt(stmt)
  ELSE IF nt = 'RepeatStmt' THEN CodegenRepeatStmt(stmt)
  ELSE IF nt = 'ForStmt' THEN CodegenForStmt(stmt)
  ELSE IF nt = 'ProcCallStmt' THEN CodegenProcCallStmt(stmt)
  ELSE
  BEGIN
    msg := 'codegen v1: unhandled statement kind: ';
    CONCAT(msg, nt);
    AbortWith(msg);
  END;
END;

{ ============================== declarations =============================== }

PROCEDURE CodegenDecl(decl: ADRMEM);
VAR
  names: ADRMEM;
  tk: INTEGER;
  n, i: INTEGER32;
BEGIN
  IF NodeType(decl) <> 'VarDecl' THEN
    AbortWith2('codegen v1: unhandled declaration kind: ', NodeType(decl));
  tk := ResolveTypeExpr(GetObj(decl, 'type_expr'));
  names := GetObj(decl, 'names');
  n := ArrSize(names);
  FOR i := 0 TO n - 1 DO
    DeclareVar(CStrToStr255(cJSON_GetStringValue(ArrItem(names, i))), tk);
END;

{ ============================== driver =================================== }

VAR
  root, block, decls, body: ADRMEM;
  ndecls, i: INTEGER32;
  param_arr: ADRMEM;
  ret_val: ADRMEM;
  verify_msg_raw: ADRMEM;
  verify_msg: PAdr;
  ok: CINT;
  ir_text: ADRMEM;
  res_c: CINT;

BEGIN
  root := ReadAllStdin;
  IF NodeType(root) <> 'ProgramUnit' THEN
    AbortWith('codegen v1: expected ProgramUnit at root');

  ctx := LLVMContextCreate;
  modl := LLVMModuleCreateWithNameInContext(MakeCStr('pascal_program'), ctx);
  i32ty := LLVMInt32TypeInContext(ctx);
  i16ty := LLVMInt16TypeInContext(ctx);
  i8ty := LLVMInt8TypeInContext(ctx);
  i1ty := LLVMInt1TypeInContext(ctx);
  dblty := LLVMDoubleTypeInContext(ctx);
  i8ptrty := LLVMPointerType(i8ty, 0);

  main_fnty := LLVMFunctionType(i32ty, NIL, 0, 0);
  main_fn := LLVMAddFunction(modl, MakeCStr('main'), main_fnty);
  entry_bb := LLVMAppendBasicBlockInContext(ctx, main_fn, MakeCStr('entry'));
  builder := LLVMCreateBuilderInContext(ctx);
  LLVMPositionBuilderAtEnd(builder, entry_bb);

  param_arr := AllocPtrArray(1);
  SetPtrArrayElem(param_arr, 0, i8ptrty);
  printf_fnty := LLVMFunctionType(i32ty, param_arr, 1, 1);
  printf_fn := LLVMAddFunction(modl, MakeCStr('printf'), printf_fnty);

  nsymbols := 0;

  block := GetObj(root, 'block');
  IF NodeType(block) <> 'Block' THEN
    AbortWith('codegen v1: expected Block under ProgramUnit');

  decls := GetObj(block, 'decls');
  ndecls := ArrSize(decls);
  FOR i := 0 TO ndecls - 1 DO
    CodegenDecl(ArrItem(decls, i));

  body := GetObj(block, 'body');
  CodegenStmtArray(body);

  ret_val := LLVMBuildRet(builder, LLVMConstInt(i32ty, 0, 0));

  verify_msg_raw := malloc(8);
  verify_msg := verify_msg_raw;
  verify_msg^ := NIL;
  ok := LLVMVerifyModule(modl, LLVMAbortProcessAction, verify_msg_raw);
  IF ok <> 0 THEN
  BEGIN
    res_c := puts(MakeCStr('codegen v1: module verification failed:'));
    res_c := puts(verify_msg^);
    exit(1);
  END;

  ir_text := LLVMPrintModuleToString(modl);
  res_c := puts(ir_text);
END.
