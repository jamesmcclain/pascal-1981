{ Native Pascal Code Generator, pascal1981-dialect implementation.

  Scope (v1): the smallest possible vertical slice proving the whole native
  lex|parse|typecheck|codegen pipeline end to end -- a PROGRAM whose body is
  a sequence of WRITELN calls, each with exactly one string-literal
  argument. Everything else (scalar expressions, control flow, procedures/
  functions, aggregates, C-ABI, units) is out of scope for v1 and is
  rejected loudly via AbortWith rather than silently mishandled.

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
FUNCTION LLVMInt8TypeInContext(ctx: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMPointerType(elem_ty: ADRMEM; addr_space: CINT): ADRMEM [C]; EXTERN;
FUNCTION LLVMFunctionType(ret_ty: ADRMEM; params: ADRMEM; pcount: CINT; vararg: CINT): ADRMEM [C]; EXTERN;
FUNCTION LLVMAddFunction(m: ADRMEM; name: ADRMEM; fty: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMAppendBasicBlockInContext(ctx: ADRMEM; fn: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMCreateBuilderInContext(ctx: ADRMEM): ADRMEM [C]; EXTERN;
PROCEDURE LLVMPositionBuilderAtEnd(b: ADRMEM; bb: ADRMEM) [C]; EXTERN;
FUNCTION LLVMBuildGlobalStringPtr(b: ADRMEM; str: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMConstInt(ty: ADRMEM; n: CLONG; signext: CINT): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildCall2(b: ADRMEM; fty: ADRMEM; fn: ADRMEM; args: ADRMEM; nargs: CINT; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildRet(b: ADRMEM; v: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMPrintModuleToString(m: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMVerifyModule(m: ADRMEM; action: CINT; outmsg: ADRMEM): CINT [C]; EXTERN;
FUNCTION malloc(size: CINT): ADRMEM [C]; EXTERN;
FUNCTION puts(str: ADRMEM): CINT [C]; EXTERN;
PROCEDURE exit(code: CINT) [C]; EXTERN;

CONST
  LLVMAbortProcessAction = 0;

TYPE
  PAdr = ^ADRMEM;

VAR
  ctx, modl, i32ty, i8ty, i8ptrty: ADRMEM;
  main_fnty, main_fn, entry_bb, builder: ADRMEM;
  puts_fnty, puts_fn: ADRMEM;

{ ============================== utilities ============================== }

PROCEDURE AbortWith(msg: Str255);
VAR
  res_c: CINT;
BEGIN
  res_c := puts(MakeCStr(msg));
  exit(1);
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

FUNCTION BuildPtrArray1(v: ADRMEM): ADRMEM;
{ The single-element case of the malloc-and-cast idiom jsonutil.pas already
  uses for C strings: llvm-c takes LLVMTypeRef*/LLVMValueRef* arrays as a
  raw pointer + count, so a one-element argument/param list still needs a
  heap cell to point at. }
VAR
  raw: ADRMEM;
  p: PAdr;
BEGIN
  raw := malloc(8);
  p := raw;
  p^ := v;
  BuildPtrArray1 := raw;
END;

{ ============================ statement codegen ========================= }

PROCEDURE CodegenWritelnStmt(stmt: ADRMEM);
VAR
  args, arg0, expr: ADRMEM;
  strval: Str255;
  strptr, call_args, call_ret: ADRMEM;
  res_c: CINT;
BEGIN
  args := GetObj(stmt, 'args');
  IF ArrSize(args) <> 1 THEN
    AbortWith('codegen v1: WRITELN supports exactly one string-literal argument');
  arg0 := ArrItem(args, 0);
  IF NodeType(arg0) <> 'WriteArg' THEN
    AbortWith('codegen v1: expected WriteArg node');
  expr := GetObj(arg0, 'expr');
  IF NodeType(expr) <> 'StringLiteral' THEN
    AbortWith('codegen v1: WRITELN argument must be a string literal');

  strval := DecodeStringLiteral(GetStr(expr, 'value'));
  strptr := LLVMBuildGlobalStringPtr(builder, MakeCStr(strval), MakeCStr('str'));
  call_args := BuildPtrArray1(strptr);
  call_ret := LLVMBuildCall2(builder, puts_fnty, puts_fn, call_args, 1, MakeCStr('callputs'));
END;

PROCEDURE CodegenStmt(stmt: ADRMEM);
VAR
  name: Str255;
  msg: Str255;
BEGIN
  IF NodeType(stmt) = 'ProcCallStmt' THEN
  BEGIN
    name := GetStr(stmt, 'name');
    IF name = 'WRITELN' THEN
      CodegenWritelnStmt(stmt)
    ELSE
    BEGIN
      msg := 'codegen v1: unhandled procedure call: ';
      CONCAT(msg, name);
      AbortWith(msg);
    END;
  END
  ELSE
  BEGIN
    msg := 'codegen v1: unhandled statement kind: ';
    CONCAT(msg, NodeType(stmt));
    AbortWith(msg);
  END;
END;

{ ============================== driver =================================== }

VAR
  root, block, body, stmt: ADRMEM;
  nstmts, i: INTEGER32;
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
  i8ty := LLVMInt8TypeInContext(ctx);
  i8ptrty := LLVMPointerType(i8ty, 0);

  main_fnty := LLVMFunctionType(i32ty, NIL, 0, 0);
  main_fn := LLVMAddFunction(modl, MakeCStr('main'), main_fnty);
  entry_bb := LLVMAppendBasicBlockInContext(ctx, main_fn, MakeCStr('entry'));
  builder := LLVMCreateBuilderInContext(ctx);
  LLVMPositionBuilderAtEnd(builder, entry_bb);

  param_arr := BuildPtrArray1(i8ptrty);
  puts_fnty := LLVMFunctionType(i32ty, param_arr, 1, 0);
  puts_fn := LLVMAddFunction(modl, MakeCStr('puts'), puts_fnty);

  block := GetObj(root, 'block');
  IF NodeType(block) <> 'Block' THEN
    AbortWith('codegen v1: expected Block under ProgramUnit');
  body := GetObj(block, 'body');
  nstmts := ArrSize(body);
  FOR i := 0 TO nstmts - 1 DO
  BEGIN
    stmt := ArrItem(body, i);
    CodegenStmt(stmt);
  END;

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
