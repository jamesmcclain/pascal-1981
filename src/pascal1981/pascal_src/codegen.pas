{ Native Pascal Code Generator, pascal1981-dialect implementation.

  Goal: full parity with the Python reference code generator
  (src/pascal1981/codegen/). Built up incrementally -- each supported
  construct is real, but the construct set covered so far is a proper
  subset of the reference. Currently covers: PROGRAM-level and routine-local
  scalar VAR declarations (INTEGER/REAL/BOOLEAN/CHAR); TYPE-declared and
  inline ARRAY/RECORD types (single-dimension, non-PACKED, non-SUPER),
  including arrays of records and indexed/field designator reads and
  writes; the full arithmetic/relational/logical expression operator set
  (no implicit cross-type promotion -- mixing INTEGER and REAL in one
  expression is rejected, not silently coerced); assignment; IF/WHILE/
  REPEAT/FOR and compound statements; WRITE/WRITELN of string-literal/
  scalar arguments; and PROCEDURE/FUNCTION declarations with value and VAR
  parameters (VAR-mode ARRAY/RECORD/LSTRING parameters work; value-mode
  aggregate ones are rejected -- pass by VAR instead), including recursion;
  LSTRING(n) variables (declaration, string-literal assignment,
  WRITE/WRITELN, 1-based character indexing s[i]) and POINTER variables
  (^Type, NEW/DISPOSE, dereference p^ as an lvalue and rvalue); STRING(n)
  variables (declaration, exact-length string-literal assignment,
  WRITE/WRITELN, 1-based character indexing s[i], no length prefix); and
  CONCAT(VAR D: LSTRING; CONST S: STRING-or-LSTRING-or-literal), appending
  S onto D via a runtime byte-copy loop (S's length is not always known at
  compile time, unlike a literal assignment's); SET OF lo..hi variables
  (TYPE-declared, over an INTEGER subrange base only), set constructors
  (`[..]`, both single elements and lo..hi ranges, constant or dynamic --
  all lowered as runtime bit-set instructions rather than the Python
  reference's compile-time-constant-folded words, a deliberate behavioral-
  parity-over-IR-shape-parity tradeoff), the set operators +/-/*, =, <>,
  <=, <, >=, > and IN, and set-to-set assignment; and CASE/OTHERWISE over
  an INTEGER selector with single-constant and comma-separated labels
  (lowered as a sequential test-block chain, not a jump table) -- a lo..hi
  label range is rejected, matching the Python reference's own
  not-yet-supported limitation there, not falling short of it. Not yet
  covered: COPYLST/COPYSTR/other string builtins, files, multi-dimension
  arrays, CHAR-keyed CASE, CASE label ranges, WRITE width:precision,
  MATHCK/RANGECK-style runtime traps (including CONCAT's own capacity
  overflow, which is unchecked -- same simplification as an unchecked
  array index elsewhere in this file), C-ABI externs, units, and DEVICE
  MODULE/PTX generation. Anything not yet covered is
  rejected loudly via AbortWith rather than silently mishandled
  or miscompiled -- reject unhandled constructs instead of guessing, the
  same discipline the earlier native stages (lexer.pas/parser.pas/
  typechecker.pas) already follow.

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
FUNCTION LLVMInt64TypeInContext(ctx: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMDoubleTypeInContext(ctx: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMPointerType(elem_ty: ADRMEM; addr_space: CINT): ADRMEM [C]; EXTERN;
FUNCTION LLVMArrayType(elem_ty: ADRMEM; count: CINT): ADRMEM [C]; EXTERN;
FUNCTION LLVMStructTypeInContext(ctx: ADRMEM; elem_tys: ADRMEM; count: CINT; is_packed: CINT): ADRMEM [C]; EXTERN;
FUNCTION LLVMConstNull(ty: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildGEP2(b: ADRMEM; ty: ADRMEM; ptr: ADRMEM; indices: ADRMEM; nindices: CINT; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildBitCast(b: ADRMEM; val: ADRMEM; destty: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildZExt(b: ADRMEM; val: ADRMEM; destty: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildTrunc(b: ADRMEM; val: ADRMEM; destty: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
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
FUNCTION LLVMBuildNot(b: ADRMEM; val: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildShl(b: ADRMEM; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildUDiv(b: ADRMEM; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildURem(b: ADRMEM; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildExtractValue(b: ADRMEM; agg: ADRMEM; idx: CINT; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildInsertValue(b: ADRMEM; agg: ADRMEM; elt: ADRMEM; idx: CINT; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildICmp(b: ADRMEM; pred: CINT; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildFCmp(b: ADRMEM; pred: CINT; lhs: ADRMEM; rhs: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildSExt(b: ADRMEM; val: ADRMEM; destty: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
PROCEDURE LLVMBuildBr(b: ADRMEM; dest: ADRMEM) [C]; EXTERN;
PROCEDURE LLVMBuildCondBr(b: ADRMEM; cond: ADRMEM; then_bb: ADRMEM; else_bb: ADRMEM) [C]; EXTERN;
FUNCTION LLVMBuildCall2(b: ADRMEM; fty: ADRMEM; fn: ADRMEM; args: ADRMEM; nargs: CINT; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMBuildRet(b: ADRMEM; v: ADRMEM): ADRMEM [C]; EXTERN;
PROCEDURE LLVMBuildRetVoid(b: ADRMEM) [C]; EXTERN;
FUNCTION LLVMBuildAlloca(b: ADRMEM; ty: ADRMEM; name: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMGetParam(fn: ADRMEM; idx: CINT): ADRMEM [C]; EXTERN;
FUNCTION LLVMVoidTypeInContext(ctx: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMPrintModuleToString(m: ADRMEM): ADRMEM [C]; EXTERN;
FUNCTION LLVMVerifyModule(m: ADRMEM; action: CINT; outmsg: ADRMEM): CINT [C]; EXTERN;
FUNCTION malloc(size: CINT): ADRMEM [C]; EXTERN;
PROCEDURE free(p: ADRMEM) [C]; EXTERN;
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
  TK_ARRAY   = 5;
  TK_RECORD  = 6;
  TK_LSTRING = 7;
  TK_POINTER = 8;
  TK_STRING  = 9;
  TK_SET     = 10;

  MAX_SYMBOLS = 500;
  MAX_SCOPES = 64;
  MAX_PARAMS = 16;
  MAX_ROUTINES = 200;
  MAX_TYPES = 200;
  MAX_FIELDS = 500;
  MAX_RECORD_FIELDS = 32;

TYPE
  PAdr = ^ADRMEM;

  { A type id ("tid") is either a bare scalar TK_* constant (1..4) or an
    index into `types` (5..) for an ARRAY/RECORD -- see TypeKind/
    RegisterType below. Every "tk"/"tid"-named field or parameter in this
    file holds one of these; MAX_TYPES/MAX_FIELDS are both comfortably
    under INTEGER's 16-bit range, so plain INTEGER (not INTEGER32) is
    correct here, unlike a count/size/loop-index field. }

  TypeRec = RECORD
    name: Str255;    { the TYPE decl's name that introduced this entry, ''
                        for an anonymous inline ARRAY/RECORD type_expr }
    tk: INTEGER;      { TK_ARRAY or TK_RECORD }
    elem_tid: INTEGER; { ARRAY only: the element type's id }
    lo, hi: INTEGER;   { ARRAY only: the index range's bounds }
    llvm_ty: ADRMEM;   { the cached LLVMTypeRef for this type }
  END;

  FieldRec = RECORD
    rec_tid: INTEGER;
    fname: Str255;
    field_tid: INTEGER;
    field_index: INTEGER; { 0-based, matches the LLVM struct's GEP index }
  END;

  SymRec = RECORD
    name: Str255;
    tk: INTEGER;
    llvm_val: ADRMEM; { the LLVMValueRef of the variable's storage: an
                        LLVMAddGlobal for a global, an LLVMBuildAlloca for a
                        local or a value-mode parameter, or the raw incoming
                        pointer argument itself for a VAR-mode parameter --
                        all three are just opaque pointers to CodegenExpr's
                        LLVMBuildLoad2/LLVMBuildStore call sites, so no
                        separate "kind" tag is needed. }
  END;

  ParamNameArr = ARRAY [1..MAX_PARAMS] OF Str255;
  ParamTkArr = ARRAY [1..MAX_PARAMS] OF INTEGER;
  ParamVarArr = ARRAY [1..MAX_PARAMS] OF BOOLEAN;

  RoutineRec = RECORD
    name: Str255;
    is_func: BOOLEAN;
    fn: ADRMEM;
    fnty: ADRMEM;
    ret_tk: INTEGER;
    nparams: INTEGER32;
    param_tk: ParamTkArr;
    param_is_var: ParamVarArr;
  END;

VAR
  ctx, modl, builder: ADRMEM;
  i32ty, i16ty, i8ty, i1ty, i64ty, dblty, i8ptrty, voidty: ADRMEM;
  setty: ADRMEM; { the one physical set representation shared by every SET
                   type regardless of declared base range, matching the
                   Python reference's set_llvm_type: a fixed [4 x i64]
                   256-bit bitvector. }
  generic_set_tid: INTEGER; { lazily registered the first time a "set value
                   with no single declared named type" is produced (a set
                   constructor's result, or a set binop's result) -- see
                   EnsureGenericSetType. Every SET type shares the same
                   physical layout, so operations that mix two differently
                   *named* set types (or an anonymous constructor value with
                   a named one) are still valid; TypesCompatibleForAssign
                   below is what actually allows that, this tid just needs
                   to be *some* valid registered TK_SET entry to satisfy
                   TypeKind's table lookup. }
  main_fnty, main_fn, entry_bb: ADRMEM;
  printf_fnty, printf_fn: ADRMEM;
  malloc_fnty, malloc_fn, free_fnty, free_fn: ADRMEM; { the *target program's*
    malloc/free, declared+called as ordinary LLVM externs -- distinct from
    this compiler's own host-side `malloc`/`free` FFI used by AllocPtrArray
    and friends. NEW/DISPOSE must emit a runtime call instruction, not
    allocate on the compiler's own process heap. }
  cur_fn: ADRMEM; { the LLVM function LLVMAppendBasicBlockInContext should
                    attach new blocks to: main_fn at top level, or the
                    routine currently being codegen'd. }

  types: ARRAY [1..MAX_TYPES] OF TypeRec;
  ntypes: INTEGER; { MAX_TYPES=200 is well under INTEGER's 16-bit range, so
                     unlike nsymbols/nroutines this stays plain INTEGER --
                     matches every tid value it produces, which also flow
                     into plain-INTEGER tk/tid fields (SymRec.tk,
                     TypeRec.elem_tid, RoutineRec.param_tk, ...); mixing
                     INTEGER32 in here would just create narrowing-assignment
                     friction against those fields for no value-range benefit. }
  fields: ARRAY [1..MAX_FIELDS] OF FieldRec;
  nfields: INTEGER;

  symbols: ARRAY [1..MAX_SYMBOLS] OF SymRec;
  nsymbols: INTEGER32;
  scope_stack: ARRAY [1..MAX_SCOPES] OF INTEGER32;
  scope_top: INTEGER32;
  in_local_scope: BOOLEAN; { FALSE while codegen'ing top-level VAR decls
                             (global storage), TRUE while inside a routine
                             body (alloca'd local storage). }

  routines: ARRAY [1..MAX_ROUTINES] OF RoutineRec;
  nroutines: INTEGER32;

  cur_func_name: Str255; { '' unless codegen'ing a FUNCTION body, in which
                           case it is that function's own name -- mirrors
                           typechecker.pas's cur_func_name: `Name := expr`
                           inside a FUNCTION's own body assigns through the
                           return-value slot rather than any symbol-table
                           entry, and (as in typechecker.pas) the function's
                           own name is deliberately never registered as a
                           symbol, so a recursive call resolves through the
                           routine table instead of being shadowed. }
  cur_func_ret_tk: INTEGER;
  cur_func_ret_slot: ADRMEM;

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
  IF len < 2 THEN AbortWith('codegen: malformed string literal');
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

FUNCTION LLVMTypeForTk(tk: INTEGER): ADRMEM;
BEGIN
  IF tk = TK_INTEGER THEN LLVMTypeForTk := i16ty
  ELSE IF tk = TK_REAL THEN LLVMTypeForTk := dblty
  ELSE IF tk = TK_BOOLEAN THEN LLVMTypeForTk := i1ty
  ELSE IF tk = TK_CHAR THEN LLVMTypeForTk := i8ty
  ELSE IF tk >= 5 THEN LLVMTypeForTk := types[tk].llvm_ty
  ELSE
  BEGIN
    AbortWith('codegen: LLVMTypeForTk: unknown type kind');
    LLVMTypeForTk := NIL;
  END;
END;

FUNCTION TypeKind(tid: INTEGER): INTEGER;
{ tid <= 4 IS its own kind (a bare scalar TK_* constant); tid >= 5 is an
  index into `types`, whose own .tk says ARRAY or RECORD. }
BEGIN
  IF tid <= 4 THEN TypeKind := tid
  ELSE TypeKind := types[tid].tk;
END;

FUNCTION LookupNamedType(name: Str255): INTEGER;
VAR
  i, found: INTEGER;
BEGIN
  found := 0;
  FOR i := 5 TO ntypes DO
    IF types[i].name = name THEN found := i;
  LookupNamedType := found;
END;

FUNCTION LookupField(rec_tid: INTEGER; fname: Str255): INTEGER;
VAR
  i, found: INTEGER;
BEGIN
  found := 0;
  FOR i := 1 TO nfields DO
    IF (fields[i].rec_tid = rec_tid) AND (fields[i].fname = fname) THEN found := i;
  LookupField := found;
END;

FUNCTION RegisterType(tk: INTEGER; elem_tid, lo, hi: INTEGER; llvm_ty: ADRMEM): INTEGER;
BEGIN
  IF ntypes >= MAX_TYPES THEN AbortWith('codegen: too many types');
  ntypes := ntypes + 1;
  types[ntypes].name := '';
  types[ntypes].tk := tk;
  types[ntypes].elem_tid := elem_tid;
  types[ntypes].lo := lo;
  types[ntypes].hi := hi;
  types[ntypes].llvm_ty := llvm_ty;
  RegisterType := ntypes;
END;

FUNCTION EnsureGenericSetType: INTEGER;
{ Lazily registers (once) a canonical TK_SET table entry with no declared
  base range, for set-typed values that have no single named declared type
  of their own -- a set constructor's result, or a set binop's result.
  Every SET type shares the exact same physical layout (setty), so this is
  always a safe stand-in tid; see TypesCompatibleForAssign, which is the
  part that actually allows mixing this with a specifically-named SET type. }
BEGIN
  IF generic_set_tid = 0 THEN
    generic_set_tid := RegisterType(TK_SET, TK_INTEGER, 0, 255, setty);
  EnsureGenericSetType := generic_set_tid;
END;

FUNCTION TypesCompatibleForAssign(from_tid, to_tid: INTEGER): BOOLEAN;
{ Exact tid equality is the normal rule everywhere else in this file, but
  two SET types are freely assignment-compatible with each other regardless
  of which specific TYPE declaration (or none, for a constructor/binop
  result) produced their tid, since every SET physically is the same
  [4 x i64] bitvector -- see EnsureGenericSetType. }
BEGIN
  TypesCompatibleForAssign := (from_tid = to_tid) OR
    ((TypeKind(from_tid) = TK_SET) AND (TypeKind(to_tid) = TK_SET));
END;

FUNCTION TypeSizeBytes(tid: INTEGER): INTEGER32;
{ Used only by NEW's malloc-sized allocation; not a general ABI sizeof (no
  struct-padding modeling), sufficient for allocating one heap block of a
  known Pascal type. }
VAR
  i: INTEGER;
  total: INTEGER32;
BEGIN
  IF tid = TK_INTEGER THEN TypeSizeBytes := 2
  ELSE IF tid = TK_REAL THEN TypeSizeBytes := 8
  ELSE IF tid = TK_BOOLEAN THEN TypeSizeBytes := 1
  ELSE IF tid = TK_CHAR THEN TypeSizeBytes := 1
  ELSE IF TypeKind(tid) = TK_ARRAY THEN
    TypeSizeBytes := TypeSizeBytes(types[tid].elem_tid) * (types[tid].hi - types[tid].lo + 1)
  ELSE IF TypeKind(tid) = TK_RECORD THEN
  BEGIN
    total := 0;
    FOR i := 1 TO nfields DO
      IF fields[i].rec_tid = tid THEN
        total := total + TypeSizeBytes(fields[i].field_tid);
    TypeSizeBytes := total;
  END
  ELSE IF TypeKind(tid) = TK_LSTRING THEN TypeSizeBytes := types[tid].hi + 1
  ELSE IF TypeKind(tid) = TK_STRING THEN TypeSizeBytes := types[tid].hi
  ELSE IF TypeKind(tid) = TK_SET THEN TypeSizeBytes := 32
  ELSE IF TypeKind(tid) = TK_POINTER THEN TypeSizeBytes := 8
  ELSE
  BEGIN
    AbortWith('codegen: TypeSizeBytes: unsupported type');
    TypeSizeBytes := 0;
  END;
END;

FUNCTION ResolveIntLiteral(node: ADRMEM): INTEGER;
{ An array index bound is a full constant-expression AST node (the parser
  never unwraps it the way it does e.g. NamedType.param) -- so reading it
  needs to drill into the node's own 'value' field, not treat the node
  itself as a bare JSON number. Scoped to the literal case only; a CONST-
  identifier or computed bound is not yet supported. }
BEGIN
  IF NodeType(node) <> 'IntLiteral' THEN
    AbortWith('codegen: array index bounds must be integer literals');
  ResolveIntLiteral := GetInt(node, 'value');
END;

FUNCTION ResolveTypeExpr(te: ADRMEM): INTEGER;
VAR
  nm: Str255;
  nt: Str255;
  tid: INTEGER;
  elem_tid, lo, hi, count: INTEGER;
  arr_ty: ADRMEM;
  fields_arr, field_tuple, items, fnames_arr, ftype_expr: ADRMEM;
  nfd, fi, fn2, fni: INTEGER;
  field_tid: INTEGER;
  fname: Str255;
  elem_llvm_types: ADRMEM;
  struct_ty: ADRMEM;
  field_index: INTEGER;
BEGIN
  nt := NodeType(te);
  IF nt = 'NamedType' THEN
  BEGIN
    nm := GetStr(te, 'name');
    IF nm = 'INTEGER' THEN tid := TK_INTEGER
    ELSE IF nm = 'REAL' THEN tid := TK_REAL
    ELSE IF nm = 'BOOLEAN' THEN tid := TK_BOOLEAN
    ELSE IF nm = 'CHAR' THEN tid := TK_CHAR
    ELSE IF nm = 'STRING' THEN
    BEGIN
      IF GetObjOrNil(te, 'param') = NIL THEN hi := 256
      ELSE hi := GetInt(te, 'param');
      arr_ty := LLVMArrayType(i8ty, hi);
      tid := RegisterType(TK_STRING, TK_CHAR, 1, hi, arr_ty);
    END
    ELSE
    BEGIN
      tid := LookupNamedType(nm);
      IF tid = 0 THEN
      BEGIN
        AbortWith2('codegen: unsupported or undeclared type: ', nm);
        tid := TK_UNKNOWN;
      END;
    END;
  END
  ELSE IF nt = 'ArrayType' THEN
  BEGIN
    IF GetBool(te, 'packed') OR GetBool(te, 'super') THEN
      AbortWith('codegen: PACKED/SUPER arrays are not supported');
    lo := ResolveIntLiteral(GetObj(GetObj(te, 'index_range'), 'low'));
    hi := ResolveIntLiteral(GetObj(GetObj(te, 'index_range'), 'high'));
    elem_tid := ResolveTypeExpr(GetObj(te, 'element_type'));
    count := hi - lo + 1;
    arr_ty := LLVMArrayType(LLVMTypeForTk(elem_tid), count);
    tid := RegisterType(TK_ARRAY, elem_tid, lo, hi, arr_ty);
  END
  ELSE IF nt = 'RecordType' THEN
  BEGIN
    IF GetBool(te, 'packed') THEN
      AbortWith('codegen: PACKED records are not supported');
    fields_arr := GetObj(te, 'fields');
    nfd := ArrSize(fields_arr);
    { First pass: flatten every (names, type_expr) group into one struct
      field per name, in declaration order, and register each in `fields`.
      Two passes because the LLVM struct type itself needs the flattened
      element-type array built before LLVMStructTypeInContext is called. }
    field_index := 0;
    elem_llvm_types := AllocPtrArray(MAX_RECORD_FIELDS);
    tid := RegisterType(TK_RECORD, 0, 0, 0, NIL); { placeholder; llvm_ty patched below }
    FOR fi := 0 TO nfd - 1 DO
    BEGIN
      field_tuple := ArrItem(fields_arr, fi);
      items := GetObj(field_tuple, 'items');
      fnames_arr := ArrItem(items, 0);
      ftype_expr := ArrItem(items, 1);
      field_tid := ResolveTypeExpr(ftype_expr);
      fn2 := ArrSize(fnames_arr);
      FOR fni := 0 TO fn2 - 1 DO
      BEGIN
        IF field_index >= MAX_RECORD_FIELDS THEN AbortWith('codegen: too many record fields');
        fname := CStrToStr255(cJSON_GetStringValue(ArrItem(fnames_arr, fni)));
        IF nfields >= MAX_FIELDS THEN AbortWith('codegen: too many record fields overall');
        nfields := nfields + 1;
        fields[nfields].rec_tid := tid;
        fields[nfields].fname := fname;
        fields[nfields].field_tid := field_tid;
        fields[nfields].field_index := field_index;
        SetPtrArrayElem(elem_llvm_types, field_index, LLVMTypeForTk(field_tid));
        field_index := field_index + 1;
      END;
    END;
    struct_ty := LLVMStructTypeInContext(ctx, elem_llvm_types, field_index, 0);
    types[tid].llvm_ty := struct_ty;
  END
  ELSE IF nt = 'LStringType' THEN
  BEGIN
    hi := GetInt(te, 'max_len');
    arr_ty := LLVMArrayType(i8ty, hi + 1);
    tid := RegisterType(TK_LSTRING, TK_CHAR, 0, hi, arr_ty);
  END
  ELSE IF nt = 'PointerType' THEN
  BEGIN
    IF GetStr(te, 'flavor') <> 'POINTER' THEN
      AbortWith('codegen: only plain POINTER (not ADR/ADS) is supported');
    elem_tid := ResolveTypeExpr(GetObj(te, 'base'));
    arr_ty := LLVMPointerType(LLVMTypeForTk(elem_tid), 0);
    tid := RegisterType(TK_POINTER, elem_tid, 0, 0, arr_ty);
  END
  ELSE IF nt = 'SetType' THEN
  BEGIN
    { Every SET type shares the same physical [4 x i64] 256-bit-bitvector
      representation regardless of declared base range (matching the Python
      reference's set_llvm_type) -- only the base's low/high are kept, and
      only to know the ordinal's legal range, not to size the storage.
      Scoped to a SubrangeType base (SET OF lo..hi); a bare enum/named-type
      base is not yet supported. }
    IF NodeType(GetObj(te, 'base')) <> 'SubrangeType' THEN
      AbortWith('codegen: SET OF <base> is only supported over a lo..hi subrange');
    lo := ResolveIntLiteral(GetObj(GetObj(te, 'base'), 'low'));
    hi := ResolveIntLiteral(GetObj(GetObj(te, 'base'), 'high'));
    tid := RegisterType(TK_SET, TK_INTEGER, lo, hi, setty);
  END
  ELSE
  BEGIN
    AbortWith2('codegen: unsupported type expression: ', nt);
    tid := TK_UNKNOWN;
  END;
  ResolveTypeExpr := tid;
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

FUNCTION CurScopeBase: INTEGER32;
BEGIN
  IF scope_top = 0 THEN CurScopeBase := 0
  ELSE CurScopeBase := scope_stack[scope_top];
END;

PROCEDURE DeclareVar(name: Str255; tk: INTEGER);
VAR
  gvar, zero: ADRMEM;
  i, base: INTEGER32;
  dup: BOOLEAN;
BEGIN
  { Only the current scope's own slice of the symbol table can collide --
    a local is allowed (expected, even) to shadow an outer/global variable
    of the same name, matching ordinary Pascal scoping. }
  base := CurScopeBase;
  dup := FALSE;
  FOR i := base + 1 TO nsymbols DO
    IF symbols[i].name = name THEN dup := TRUE;
  IF dup THEN
    AbortWith2('codegen: duplicate declaration: ', name);
  IF in_local_scope THEN
    gvar := LLVMBuildAlloca(builder, LLVMTypeForTk(tk), MakeCStr(name))
  ELSE
  BEGIN
    gvar := LLVMAddGlobal(modl, LLVMTypeForTk(tk), MakeCStr(name));
    IF (TypeKind(tk) = TK_ARRAY) OR (TypeKind(tk) = TK_RECORD) OR
       (TypeKind(tk) = TK_LSTRING) OR (TypeKind(tk) = TK_POINTER) OR
       (TypeKind(tk) = TK_STRING) OR (TypeKind(tk) = TK_SET) THEN
      zero := LLVMConstNull(LLVMTypeForTk(tk))
    ELSE IF tk = TK_REAL THEN zero := LLVMConstReal(dblty, 0.0)
    ELSE zero := LLVMConstInt(LLVMTypeForTk(tk), 0, 0);
    LLVMSetInitializer(gvar, zero);
  END;
  nsymbols := nsymbols + 1;
  symbols[nsymbols].name := name;
  symbols[nsymbols].tk := tk;
  symbols[nsymbols].llvm_val := gvar;
END;

{ ============================ routine table =============================== }

FUNCTION LookupRoutine(name: Str255): INTEGER32;
VAR
  i: INTEGER32;
  found: INTEGER32;
BEGIN
  found := 0;
  FOR i := 1 TO nroutines DO
    IF routines[i].name = name THEN found := i;
  LookupRoutine := found;
END;

{ ============================== expressions =============================== }

FUNCTION CodegenExpr(node: ADRMEM): ADRMEM; FORWARD;
FUNCTION ComputeDesignatorAddress(node: ADRMEM): ADRMEM; FORWARD;

{ ------------------------------ sets --------------------------------------
  Every SET, regardless of its declared base range, is represented the same
  physical way the Python reference represents it: a fixed 256-bit bitvector
  (setty = [4 x i64]), ordinal N's bit living at word N DIV 64, bit N MOD 64.
  Unlike the reference, nothing here is constant-folded at compile time --
  every element (even a literal like `[1, 2, 3]`) is set via a real runtime
  OR-in instruction sequence. That is behaviorally identical and much
  simpler to implement correctly than carrying a parallel compile-time-words
  accumulator through SetConstructor the way strings.py does, at the cost of
  a few more instructions in the emitted IR -- an acceptable tradeoff given
  this file's methodology is behavioral parity, not IR-shape parity. }

PROCEDURE SetRuntimeBit(slot: ADRMEM; ordinal_val: ADRMEM);
{ ordinal_val is an already-codegen'd i16 INTEGER SSA value; slot is the
  address of a setty-typed alloca. ORs ordinal_val's bit into *slot. }
VAR
  ord64, word_idx, bit_idx, mask, word_val, new_word: ADRMEM;
  gep_idx, word_ptr: ADRMEM;
BEGIN
  ord64 := LLVMBuildSExt(builder, ordinal_val, i64ty, MakeCStr(''));
  word_idx := LLVMBuildUDiv(builder, ord64, LLVMConstInt(i64ty, 64, 0), MakeCStr(''));
  bit_idx := LLVMBuildURem(builder, ord64, LLVMConstInt(i64ty, 64, 0), MakeCStr(''));
  gep_idx := AllocPtrArray(2);
  SetPtrArrayElem(gep_idx, 0, LLVMConstInt(i32ty, 0, 0));
  SetPtrArrayElem(gep_idx, 1, word_idx);
  word_ptr := LLVMBuildGEP2(builder, setty, slot, gep_idx, 2, MakeCStr(''));
  mask := LLVMBuildShl(builder, LLVMConstInt(i64ty, 1, 0), bit_idx, MakeCStr(''));
  word_val := LLVMBuildLoad2(builder, i64ty, word_ptr, MakeCStr(''));
  new_word := LLVMBuildOr(builder, word_val, mask, MakeCStr(''));
  LLVMBuildStore(builder, new_word, word_ptr);
END;

PROCEDURE EmitSetRangeLoop(slot: ADRMEM; low_node, high_node: ADRMEM);
{ FOR i := low TO high DO SetRuntimeBit(slot, i) -- same alloca-counter loop
  idiom as CodegenForStmt/EmitByteCopyLoop, done here instead of reusing
  CodegenForStmt directly since there is no surface-syntax FOR loop AST node
  to hand it (RangeExpr's bounds are arbitrary INTEGER expressions, not
  necessarily a declared loop variable). A reversed range (low > high) is
  simply empty, exactly like the Python reference. }
VAR
  low_val, high_val: ADRMEM;
  i_slot: ADRMEM;
  loop_bb, body_bb, end_bb: ADRMEM;
  cur_i, cmp_val, next_i: ADRMEM;
BEGIN
  low_val := CodegenExpr(low_node);
  IF last_val_tk <> TK_INTEGER THEN AbortWith('codegen: a set range bound must be INTEGER');
  high_val := CodegenExpr(high_node);
  IF last_val_tk <> TK_INTEGER THEN AbortWith('codegen: a set range bound must be INTEGER');

  i_slot := LLVMBuildAlloca(builder, i16ty, MakeCStr(''));
  LLVMBuildStore(builder, low_val, i_slot);

  loop_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('setrange_loop'));
  body_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('setrange_body'));
  end_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('setrange_end'));

  LLVMBuildBr(builder, loop_bb);
  LLVMPositionBuilderAtEnd(builder, loop_bb);
  cur_i := LLVMBuildLoad2(builder, i16ty, i_slot, MakeCStr(''));
  cmp_val := LLVMBuildICmp(builder, LLVMIntSLE, cur_i, high_val, MakeCStr(''));
  LLVMBuildCondBr(builder, cmp_val, body_bb, end_bb);

  LLVMPositionBuilderAtEnd(builder, body_bb);
  cur_i := LLVMBuildLoad2(builder, i16ty, i_slot, MakeCStr(''));
  SetRuntimeBit(slot, cur_i);
  next_i := LLVMBuildAdd(builder, cur_i, LLVMConstInt(i16ty, 1, 0), MakeCStr(''));
  LLVMBuildStore(builder, next_i, i_slot);
  LLVMBuildBr(builder, loop_bb);

  LLVMPositionBuilderAtEnd(builder, end_bb);
END;

FUNCTION CodegenSetConstructor(node: ADRMEM): ADRMEM;
VAR
  slot: ADRMEM;
  elements, el: ADRMEM;
  n, i: INTEGER32;
  ordv: ADRMEM;
BEGIN
  slot := LLVMBuildAlloca(builder, setty, MakeCStr(''));
  LLVMBuildStore(builder, LLVMConstNull(setty), slot);
  elements := GetObj(node, 'elements');
  n := ArrSize(elements);
  FOR i := 0 TO n - 1 DO
  BEGIN
    el := ArrItem(elements, i);
    IF NodeType(el) = 'RangeExpr' THEN
      EmitSetRangeLoop(slot, GetObj(el, 'low'), GetObj(el, 'high'))
    ELSE
    BEGIN
      ordv := CodegenExpr(el);
      IF last_val_tk <> TK_INTEGER THEN
        AbortWith('codegen: a set element must be INTEGER');
      SetRuntimeBit(slot, ordv);
    END;
  END;
  CodegenSetConstructor := LLVMBuildLoad2(builder, setty, slot, MakeCStr(''));
  last_val_tk := EnsureGenericSetType;
END;

FUNCTION CodegenSetMember(ordinal_val, set_val: ADRMEM): ADRMEM;
{ Lowers ordinal IN set to a bit test, mirroring codegen_set_member. }
VAR
  slot: ADRMEM;
  ord64, word_idx, bit_idx, mask, word_val, anded: ADRMEM;
  gep_idx, word_ptr: ADRMEM;
BEGIN
  slot := LLVMBuildAlloca(builder, setty, MakeCStr(''));
  LLVMBuildStore(builder, set_val, slot);
  ord64 := LLVMBuildSExt(builder, ordinal_val, i64ty, MakeCStr(''));
  word_idx := LLVMBuildUDiv(builder, ord64, LLVMConstInt(i64ty, 64, 0), MakeCStr(''));
  bit_idx := LLVMBuildURem(builder, ord64, LLVMConstInt(i64ty, 64, 0), MakeCStr(''));
  gep_idx := AllocPtrArray(2);
  SetPtrArrayElem(gep_idx, 0, LLVMConstInt(i32ty, 0, 0));
  SetPtrArrayElem(gep_idx, 1, word_idx);
  word_ptr := LLVMBuildGEP2(builder, setty, slot, gep_idx, 2, MakeCStr(''));
  word_val := LLVMBuildLoad2(builder, i64ty, word_ptr, MakeCStr(''));
  mask := LLVMBuildShl(builder, LLVMConstInt(i64ty, 1, 0), bit_idx, MakeCStr(''));
  anded := LLVMBuildAnd(builder, word_val, mask, MakeCStr(''));
  CodegenSetMember := LLVMBuildICmp(builder, LLVMIntNE, anded, LLVMConstInt(i64ty, 0, 0), MakeCStr(''));
END;

FUNCTION CodegenSetBinOp(op: Str255; lval, rval: ADRMEM): ADRMEM;
{ Extracts all 4 words of each operand via compile-time-constant-index
  ExtractValue (no loop needed, unlike the constructor/membership paths
  above), combines them per-word, and (for +/-/*) reassembles a result set
  via InsertValue starting from an all-zero aggregate. Sets last_val_tk
  itself: TK_BOOLEAN for the comparison operators, EnsureGenericSetType
  for the set-valued ones. }
VAR
  lw, rw, res_words: ARRAY [0..3] OF ADRMEM;
  i: INTEGER;
  res: ADRMEM;
  eq_all, sub_all, wc: ADRMEM;
  le_ok, ge_ok, eqv: ADRMEM;
BEGIN
  FOR i := 0 TO 3 DO
  BEGIN
    lw[i] := LLVMBuildExtractValue(builder, lval, i, MakeCStr(''));
    rw[i] := LLVMBuildExtractValue(builder, rval, i, MakeCStr(''));
  END;

  IF op = 'PLUS' THEN
  BEGIN
    res := LLVMConstNull(setty);
    FOR i := 0 TO 3 DO
      res := LLVMBuildInsertValue(builder, res, LLVMBuildOr(builder, lw[i], rw[i], MakeCStr('')), i, MakeCStr(''));
    CodegenSetBinOp := res;
    last_val_tk := EnsureGenericSetType;
  END
  ELSE IF op = 'MUL' THEN
  BEGIN
    res := LLVMConstNull(setty);
    FOR i := 0 TO 3 DO
      res := LLVMBuildInsertValue(builder, res, LLVMBuildAnd(builder, lw[i], rw[i], MakeCStr('')), i, MakeCStr(''));
    CodegenSetBinOp := res;
    last_val_tk := EnsureGenericSetType;
  END
  ELSE IF op = 'MINUS' THEN
  BEGIN
    res := LLVMConstNull(setty);
    FOR i := 0 TO 3 DO
    BEGIN
      wc := LLVMBuildNot(builder, rw[i], MakeCStr(''));
      res := LLVMBuildInsertValue(builder, res, LLVMBuildAnd(builder, lw[i], wc, MakeCStr('')), i, MakeCStr(''));
    END;
    CodegenSetBinOp := res;
    last_val_tk := EnsureGenericSetType;
  END
  ELSE IF (op = 'EQ') OR (op = 'NEQ') THEN
  BEGIN
    eq_all := LLVMBuildICmp(builder, LLVMIntEQ, lw[0], rw[0], MakeCStr(''));
    FOR i := 1 TO 3 DO
      eq_all := LLVMBuildAnd(builder, eq_all, LLVMBuildICmp(builder, LLVMIntEQ, lw[i], rw[i], MakeCStr('')), MakeCStr(''));
    IF op = 'EQ' THEN CodegenSetBinOp := eq_all
    ELSE CodegenSetBinOp := LLVMBuildXor(builder, eq_all, LLVMConstInt(i1ty, 1, 0), MakeCStr(''));
    last_val_tk := TK_BOOLEAN;
  END
  ELSE IF (op = 'LE') OR (op = 'LT') OR (op = 'GE') OR (op = 'GT') THEN
  BEGIN
    { subset(A, B): every bit in A is also in B, i.e. (A AND NOT B) = 0 for
      every word. LE/LT test subset(left, right); GE/GT test the reverse. }
    IF (op = 'LE') OR (op = 'LT') THEN
    BEGIN
      sub_all := LLVMBuildICmp(builder, LLVMIntEQ, LLVMBuildAnd(builder, lw[0], LLVMBuildNot(builder, rw[0], MakeCStr('')), MakeCStr('')), LLVMConstInt(i64ty, 0, 0), MakeCStr(''));
      FOR i := 1 TO 3 DO
      BEGIN
        wc := LLVMBuildICmp(builder, LLVMIntEQ, LLVMBuildAnd(builder, lw[i], LLVMBuildNot(builder, rw[i], MakeCStr('')), MakeCStr('')), LLVMConstInt(i64ty, 0, 0), MakeCStr(''));
        sub_all := LLVMBuildAnd(builder, sub_all, wc, MakeCStr(''));
      END;
    END
    ELSE
    BEGIN
      sub_all := LLVMBuildICmp(builder, LLVMIntEQ, LLVMBuildAnd(builder, rw[0], LLVMBuildNot(builder, lw[0], MakeCStr('')), MakeCStr('')), LLVMConstInt(i64ty, 0, 0), MakeCStr(''));
      FOR i := 1 TO 3 DO
      BEGIN
        wc := LLVMBuildICmp(builder, LLVMIntEQ, LLVMBuildAnd(builder, rw[i], LLVMBuildNot(builder, lw[i], MakeCStr('')), MakeCStr('')), LLVMConstInt(i64ty, 0, 0), MakeCStr(''));
        sub_all := LLVMBuildAnd(builder, sub_all, wc, MakeCStr(''));
      END;
    END;
    IF (op = 'LE') OR (op = 'GE') THEN
      CodegenSetBinOp := sub_all
    ELSE
    BEGIN
      eqv := LLVMBuildICmp(builder, LLVMIntEQ, lw[0], rw[0], MakeCStr(''));
      FOR i := 1 TO 3 DO
        eqv := LLVMBuildAnd(builder, eqv, LLVMBuildICmp(builder, LLVMIntEQ, lw[i], rw[i], MakeCStr('')), MakeCStr(''));
      CodegenSetBinOp := LLVMBuildAnd(builder, sub_all, LLVMBuildXor(builder, eqv, LLVMConstInt(i1ty, 1, 0), MakeCStr('')), MakeCStr(''));
    END;
    last_val_tk := TK_BOOLEAN;
  END
  ELSE
  BEGIN
    AbortWith2('codegen: unsupported SET operator: ', op);
    CodegenSetBinOp := NIL;
  END;
END;

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
      AbortWith('codegen: AND/OR require BOOLEAN operands');
    IF op = 'AND' THEN res := LLVMBuildAnd(builder, lval, rval, MakeCStr(''))
    ELSE res := LLVMBuildOr(builder, lval, rval, MakeCStr(''));
    last_val_tk := TK_BOOLEAN;
  END
  ELSE IF op = 'IN' THEN
  BEGIN
    IF ltk <> TK_INTEGER THEN
      AbortWith('codegen: IN requires an INTEGER left operand');
    IF TypeKind(rtk) <> TK_SET THEN
      AbortWith('codegen: IN requires a SET right operand');
    res := CodegenSetMember(lval, rval);
    last_val_tk := TK_BOOLEAN;
  END
  ELSE IF (TypeKind(ltk) = TK_SET) AND (TypeKind(rtk) = TK_SET) THEN
    res := CodegenSetBinOp(op, lval, rval)
  ELSE IF ltk <> rtk THEN
  BEGIN
    AbortWith('codegen: mixed-type operands are not supported (no implicit promotion)');
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
      AbortWith('codegen: relational operators support only INTEGER/REAL operands');
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
      AbortWith2('codegen: unhandled INTEGER operator: ', op);
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
      AbortWith2('codegen: unhandled REAL operator: ', op);
      res := NIL;
    END;
    last_val_tk := TK_REAL;
  END
  ELSE
  BEGIN
    AbortWith('codegen: arithmetic operators support only INTEGER/REAL operands');
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
      AbortWith('codegen: unary MINUS requires INTEGER/REAL operand');
      res := NIL;
    END;
    last_val_tk := tk;
  END
  ELSE IF op = 'NOT' THEN
  BEGIN
    IF tk <> TK_BOOLEAN THEN
      AbortWith('codegen: NOT requires a BOOLEAN operand');
    res := LLVMBuildXor(builder, v, LLVMConstInt(i1ty, 1, 0), MakeCStr(''));
    last_val_tk := TK_BOOLEAN;
  END
  ELSE
  BEGIN
    AbortWith2('codegen: unhandled unary operator: ', op);
    res := NIL;
  END;
  CodegenUnaryOp := res;
END;

FUNCTION CodegenCallCommon(name: Str255; args_arr: ADRMEM): ADRMEM;
{ Shared by a FuncCall expression and a bare ProcCallStmt that isn't
  WRITE/WRITELN: look up a user-declared routine, marshal its arguments
  (VAR-mode: the callee needs the callee's storage address directly, so the
  actual argument must be a bare Identifier and is passed unloaded; value
  mode: CodegenExpr as usual), and build the call. Sets last_val_tk to the
  routine's return type kind (TK_UNKNOWN for a PROCEDURE, meaningless to
  the caller in that case). }
VAR
  ri: INTEGER32;
  nargs, i: INTEGER32;
  call_args: ADRMEM;
  arg_node, v: ADRMEM;
  arg_nm: Str255;
  symi: INTEGER32;
  res: ADRMEM;
BEGIN
  ri := LookupRoutine(name);
  IF ri = 0 THEN
  BEGIN
    AbortWith2('codegen: undefined procedure/function: ', name);
    res := NIL;
  END
  ELSE
  BEGIN
    nargs := ArrSize(args_arr);
    IF nargs <> routines[ri].nparams THEN
      AbortWith2('codegen: argument count mismatch calling: ', name);
    call_args := AllocPtrArray(nargs);
    FOR i := 0 TO nargs - 1 DO
    BEGIN
      arg_node := ArrItem(args_arr, i);
      IF routines[ri].param_is_var[i + 1] THEN
      BEGIN
        IF NodeType(arg_node) = 'Identifier' THEN
        BEGIN
          arg_nm := GetStr(arg_node, 'name');
          symi := LookupSym(arg_nm);
          IF symi = 0 THEN
            AbortWith2('codegen: undefined variable: ', arg_nm);
          IF symbols[symi].tk <> routines[ri].param_tk[i + 1] THEN
            AbortWith2('codegen: VAR argument type mismatch calling: ', name);
          v := symbols[symi].llvm_val;
        END
        ELSE IF NodeType(arg_node) = 'Designator' THEN
        BEGIN
          v := ComputeDesignatorAddress(arg_node);
          IF last_val_tk <> routines[ri].param_tk[i + 1] THEN
            AbortWith2('codegen: VAR argument type mismatch calling: ', name);
        END
        ELSE
        BEGIN
          AbortWith2('codegen: a VAR argument must be an lvalue, calling: ', name);
          v := NIL;
        END;
      END
      ELSE
      BEGIN
        v := CodegenExpr(arg_node);
        IF last_val_tk <> routines[ri].param_tk[i + 1] THEN
          AbortWith2('codegen: argument type mismatch calling: ', name);
      END;
      SetPtrArrayElem(call_args, i, v);
    END;
    res := LLVMBuildCall2(builder, routines[ri].fnty, routines[ri].fn, call_args, nargs, MakeCStr(''));
    last_val_tk := routines[ri].ret_tk;
  END;
  CodegenCallCommon := res;
END;

FUNCTION ComputeDesignatorAddress(node: ADRMEM): ADRMEM;
{ Shared by a Designator read (CodegenExpr) and a Designator write
  (CodegenAssignStmt): walk `name` plus zero or more INDEX/FIELD selectors,
  emitting one GEP per selector, and return the final element/field's
  address. Sets last_val_tk to that final element/field's type id, exactly
  like CodegenExpr's own convention -- callers load or store through the
  returned pointer using that type. }
VAR
  nm: Str255;
  symi: INTEGER32;
  base_ptr: ADRMEM;
  cur_tid: INTEGER;
  selectors, sel, idx_expr, gep_idx: ADRMEM;
  nsel, si: INTEGER32;
  kind, fname: Str255;
  idx_val, offset: ADRMEM;
  fi: INTEGER;
BEGIN
  nm := GetStr(node, 'name');
  symi := LookupSym(nm);
  IF symi = 0 THEN
    AbortWith2('codegen: undefined variable: ', nm);
  base_ptr := symbols[symi].llvm_val;
  cur_tid := symbols[symi].tk;

  selectors := GetObj(node, 'selectors');
  nsel := ArrSize(selectors);
  FOR si := 0 TO nsel - 1 DO
  BEGIN
    sel := ArrItem(selectors, si);
    kind := GetStr(sel, 'kind');
    IF kind = 'INDEX' THEN
    BEGIN
      IF (TypeKind(cur_tid) <> TK_ARRAY) AND (TypeKind(cur_tid) <> TK_LSTRING) AND (TypeKind(cur_tid) <> TK_STRING) THEN
        AbortWith('codegen: an INDEX selector was applied to a non-array');
      idx_expr := GetObj(sel, 'index_or_field');
      idx_val := CodegenExpr(idx_expr);
      IF last_val_tk <> TK_INTEGER THEN
        AbortWith('codegen: an array index must be INTEGER');
      offset := LLVMBuildSub(builder, idx_val, LLVMConstInt(i16ty, types[cur_tid].lo, 1), MakeCStr(''));
      gep_idx := AllocPtrArray(2);
      SetPtrArrayElem(gep_idx, 0, LLVMConstInt(i32ty, 0, 0));
      SetPtrArrayElem(gep_idx, 1, offset);
      base_ptr := LLVMBuildGEP2(builder, LLVMTypeForTk(cur_tid), base_ptr, gep_idx, 2, MakeCStr(''));
      cur_tid := types[cur_tid].elem_tid;
    END
    ELSE IF kind = 'DEREF' THEN
    BEGIN
      IF TypeKind(cur_tid) <> TK_POINTER THEN
        AbortWith('codegen: a DEREF selector was applied to a non-pointer');
      base_ptr := LLVMBuildLoad2(builder, LLVMTypeForTk(cur_tid), base_ptr, MakeCStr(''));
      cur_tid := types[cur_tid].elem_tid;
    END
    ELSE IF kind = 'FIELD' THEN
    BEGIN
      IF TypeKind(cur_tid) <> TK_RECORD THEN
        AbortWith('codegen: a FIELD selector was applied to a non-record');
      fname := GetStr(sel, 'index_or_field');
      fi := LookupField(cur_tid, fname);
      IF fi = 0 THEN
        AbortWith2('codegen: unknown record field: ', fname);
      gep_idx := AllocPtrArray(2);
      SetPtrArrayElem(gep_idx, 0, LLVMConstInt(i32ty, 0, 0));
      SetPtrArrayElem(gep_idx, 1, LLVMConstInt(i32ty, fields[fi].field_index, 0));
      base_ptr := LLVMBuildGEP2(builder, LLVMTypeForTk(cur_tid), base_ptr, gep_idx, 2, MakeCStr(''));
      cur_tid := fields[fi].field_tid;
    END
    ELSE
      AbortWith2('codegen: unhandled selector kind: ', kind);
  END;

  last_val_tk := cur_tid;
  ComputeDesignatorAddress := base_ptr;
END;

PROCEDURE CodegenLStringLiteralAssign(dest_addr: ADRMEM; dest_tid: INTEGER; s: Str255);
{ Stores a compile-time-known string literal's characters plus its
  length-prefix byte (LSTRING layout: byte[0] = length, byte[1..n] = chars)
  directly into an LSTRING destination -- the counterpart of the Python
  reference's strings.py literal-store path, but done with a plain unrolled
  GEP+store per character since the source is always a constant here. }
VAR
  i, len, cap: INTEGER;
  gep_idx, elem_ptr: ADRMEM;
BEGIN
  len := ORD(s[0]);
  cap := types[dest_tid].hi;
  IF len > cap THEN
    AbortWith('codegen: string literal too long for LSTRING capacity');
  FOR i := 1 TO len DO
  BEGIN
    gep_idx := AllocPtrArray(2);
    SetPtrArrayElem(gep_idx, 0, LLVMConstInt(i32ty, 0, 0));
    SetPtrArrayElem(gep_idx, 1, LLVMConstInt(i32ty, i, 0));
    elem_ptr := LLVMBuildGEP2(builder, LLVMTypeForTk(dest_tid), dest_addr, gep_idx, 2, MakeCStr(''));
    LLVMBuildStore(builder, LLVMConstInt(i8ty, ORD(s[i]), 0), elem_ptr);
  END;
  gep_idx := AllocPtrArray(2);
  SetPtrArrayElem(gep_idx, 0, LLVMConstInt(i32ty, 0, 0));
  SetPtrArrayElem(gep_idx, 1, LLVMConstInt(i32ty, 0, 0));
  elem_ptr := LLVMBuildGEP2(builder, LLVMTypeForTk(dest_tid), dest_addr, gep_idx, 2, MakeCStr(''));
  LLVMBuildStore(builder, LLVMConstInt(i8ty, len, 0), elem_ptr);
END;

PROCEDURE CodegenStringLiteralAssign(dest_addr: ADRMEM; dest_tid: INTEGER; s: Str255);
{ STRING(n) counterpart of CodegenLStringLiteralAssign: no length-prefix
  byte -- STRING is just [n x i8], 1-based chars at byte[0..n-1] -- and the
  typechecker already guarantees an exact-length match (STRING assignment
  requires from_type.max_len = to_type.max_len, unlike LSTRING's <=), so
  every byte in the destination gets written here, not just a prefix. }
VAR
  i, len: INTEGER;
  gep_idx, elem_ptr: ADRMEM;
BEGIN
  len := ORD(s[0]);
  IF len <> types[dest_tid].hi THEN
    AbortWith('codegen: string literal length does not match STRING capacity');
  FOR i := 1 TO len DO
  BEGIN
    gep_idx := AllocPtrArray(2);
    SetPtrArrayElem(gep_idx, 0, LLVMConstInt(i32ty, 0, 0));
    SetPtrArrayElem(gep_idx, 1, LLVMConstInt(i32ty, i - 1, 0));
    elem_ptr := LLVMBuildGEP2(builder, LLVMTypeForTk(dest_tid), dest_addr, gep_idx, 2, MakeCStr(''));
    LLVMBuildStore(builder, LLVMConstInt(i8ty, ORD(s[i]), 0), elem_ptr);
  END;
END;

FUNCTION CodegenExpr(node: ADRMEM): ADRMEM;
VAR
  nt: Str255;
  nm: Str255;
  symi: INTEGER32;
  ch: Str255;
  res, addr: ADRMEM;
  result_tid: INTEGER;
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
      AbortWith2('codegen: undefined variable: ', nm);
      res := NIL;
    END
    ELSE
    BEGIN
      res := LLVMBuildLoad2(builder, LLVMTypeForTk(symbols[symi].tk), symbols[symi].llvm_val, MakeCStr(''));
      last_val_tk := symbols[symi].tk;
    END;
  END
  ELSE IF nt = 'Designator' THEN
  BEGIN
    addr := ComputeDesignatorAddress(node);
    result_tid := last_val_tk;
    res := LLVMBuildLoad2(builder, LLVMTypeForTk(result_tid), addr, MakeCStr(''));
    last_val_tk := result_tid;
  END
  ELSE IF nt = 'BinOp' THEN
    res := CodegenBinOp(GetStr(node, 'op'), GetObj(node, 'left'), GetObj(node, 'right'))
  ELSE IF nt = 'SetConstructor' THEN
    res := CodegenSetConstructor(node)
  ELSE IF nt = 'UnaryOp' THEN
    res := CodegenUnaryOp(GetStr(node, 'op'), GetObj(node, 'operand'))
  ELSE IF nt = 'FuncCall' THEN
  BEGIN
    nm := GetStr(node, 'name');
    symi := LookupRoutine(nm);
    IF symi = 0 THEN
    BEGIN
      AbortWith2('codegen: undefined function: ', nm);
      res := NIL;
    END
    ELSE IF NOT routines[symi].is_func THEN
    BEGIN
      AbortWith2('codegen: called as a function but is a PROCEDURE: ', nm);
      res := NIL;
    END
    ELSE
      res := CodegenCallCommon(nm, GetObj(node, 'args'));
  END
  ELSE
  BEGIN
    AbortWith2('codegen: unhandled expression kind: ', nt);
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
  vi: INTEGER32;
  addr, len_ptr, chars_ptr, gep_idx, len_val: ADRMEM;
  lstr_tid: INTEGER;
  symi: INTEGER32;
  is_lstring, is_string: BOOLEAN;
BEGIN
  nargs := ArrSize(args);
  fmt := '';
  vals := AllocPtrArray(nargs * 2 + 1);
  vi := 1;
  FOR i := 0 TO nargs - 1 DO
  BEGIN
    arg_node := ArrItem(args, i);
    IF NodeType(arg_node) <> 'WriteArg' THEN
      AbortWith('codegen: expected WriteArg node');
    expr := GetObj(arg_node, 'expr');
    is_lstring := FALSE;
    is_string := FALSE;
    IF NodeType(expr) = 'StringLiteral' THEN
    BEGIN
      strval := DecodeStringLiteral(GetStr(expr, 'value'));
      v := LLVMBuildGlobalStringPtr(builder, MakeCStr(strval), MakeCStr('str'));
      CONCAT(fmt, '%s');
      SetPtrArrayElem(vals, vi, v);
      vi := vi + 1;
    END
    ELSE IF NodeType(expr) = 'Identifier' THEN
    BEGIN
      symi := LookupSym(GetStr(expr, 'name'));
      IF (symi <> 0) AND (TypeKind(symbols[symi].tk) = TK_LSTRING) THEN
      BEGIN
        is_lstring := TRUE;
        addr := symbols[symi].llvm_val;
        lstr_tid := symbols[symi].tk;
      END
      ELSE IF (symi <> 0) AND (TypeKind(symbols[symi].tk) = TK_STRING) THEN
      BEGIN
        is_string := TRUE;
        addr := symbols[symi].llvm_val;
        lstr_tid := symbols[symi].tk;
      END;
      IF is_lstring THEN
      BEGIN
        gep_idx := AllocPtrArray(2);
        SetPtrArrayElem(gep_idx, 0, LLVMConstInt(i32ty, 0, 0));
        SetPtrArrayElem(gep_idx, 1, LLVMConstInt(i32ty, 0, 0));
        len_ptr := LLVMBuildGEP2(builder, LLVMTypeForTk(lstr_tid), addr, gep_idx, 2, MakeCStr(''));
        len_val := LLVMBuildLoad2(builder, i8ty, len_ptr, MakeCStr(''));
        len_val := LLVMBuildZExt(builder, len_val, i32ty, MakeCStr(''));
        gep_idx := AllocPtrArray(2);
        SetPtrArrayElem(gep_idx, 0, LLVMConstInt(i32ty, 0, 0));
        SetPtrArrayElem(gep_idx, 1, LLVMConstInt(i32ty, 1, 0));
        chars_ptr := LLVMBuildGEP2(builder, LLVMTypeForTk(lstr_tid), addr, gep_idx, 2, MakeCStr(''));
        CONCAT(fmt, '%.*s');
        SetPtrArrayElem(vals, vi, len_val);
        vi := vi + 1;
        SetPtrArrayElem(vals, vi, chars_ptr);
        vi := vi + 1;
      END
      ELSE IF is_string THEN
      BEGIN
        len_val := LLVMConstInt(i32ty, types[lstr_tid].hi, 0);
        gep_idx := AllocPtrArray(2);
        SetPtrArrayElem(gep_idx, 0, LLVMConstInt(i32ty, 0, 0));
        SetPtrArrayElem(gep_idx, 1, LLVMConstInt(i32ty, 0, 0));
        chars_ptr := LLVMBuildGEP2(builder, LLVMTypeForTk(lstr_tid), addr, gep_idx, 2, MakeCStr(''));
        CONCAT(fmt, '%.*s');
        SetPtrArrayElem(vals, vi, len_val);
        vi := vi + 1;
        SetPtrArrayElem(vals, vi, chars_ptr);
        vi := vi + 1;
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
          AbortWith('codegen: unsupported WRITE argument type');
        SetPtrArrayElem(vals, vi, v);
        vi := vi + 1;
      END;
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
        AbortWith('codegen: unsupported WRITE argument type');
      SetPtrArrayElem(vals, vi, v);
      vi := vi + 1;
    END;
  END;
  IF newline THEN AppendChar(fmt, CHR(10));
  SetPtrArrayElem(vals, 0, LLVMBuildGlobalStringPtr(builder, MakeCStr(fmt), MakeCStr('fmt')));
  call_ret := LLVMBuildCall2(builder, printf_fnty, printf_fn, vals, vi, MakeCStr('callprintf'));
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
  v, addr: ADRMEM;
  target_tid: INTEGER;
BEGIN
  target := GetObj(stmt, 'target');
  IF NodeType(target) <> 'Designator' THEN
    AbortWith('codegen: unsupported assignment target');
  sel := GetObj(target, 'selectors');
  nm := GetStr(target, 'name');

  IF (ArrSize(sel) = 0) AND (cur_func_name <> '') AND (nm = cur_func_name) THEN
  BEGIN
    { `FuncName := expr` inside FuncName's own body assigns through the
      return-value slot, not a symbol -- see cur_func_name's declaration. }
    v := CodegenExpr(GetObj(stmt, 'expr'));
    IF NOT TypesCompatibleForAssign(last_val_tk, cur_func_ret_tk) THEN
      AbortWith2('codegen: return-value type mismatch in: ', nm);
    LLVMBuildStore(builder, v, cur_func_ret_slot);
  END
  ELSE IF ArrSize(sel) = 0 THEN
  BEGIN
    symi := LookupSym(nm);
    IF symi = 0 THEN
      AbortWith2('codegen: undefined variable: ', nm);
    IF (TypeKind(symbols[symi].tk) = TK_LSTRING) AND (NodeType(GetObj(stmt, 'expr')) = 'StringLiteral') THEN
      CodegenLStringLiteralAssign(symbols[symi].llvm_val, symbols[symi].tk,
        DecodeStringLiteral(GetStr(GetObj(stmt, 'expr'), 'value')))
    ELSE IF (TypeKind(symbols[symi].tk) = TK_STRING) AND (NodeType(GetObj(stmt, 'expr')) = 'StringLiteral') THEN
      CodegenStringLiteralAssign(symbols[symi].llvm_val, symbols[symi].tk,
        DecodeStringLiteral(GetStr(GetObj(stmt, 'expr'), 'value')))
    ELSE
    BEGIN
      v := CodegenExpr(GetObj(stmt, 'expr'));
      IF NOT TypesCompatibleForAssign(last_val_tk, symbols[symi].tk) THEN
        AbortWith2('codegen: assignment type mismatch for: ', nm);
      LLVMBuildStore(builder, v, symbols[symi].llvm_val);
    END;
  END
  ELSE
  BEGIN
    addr := ComputeDesignatorAddress(target);
    target_tid := last_val_tk;
    v := CodegenExpr(GetObj(stmt, 'expr'));
    IF NOT TypesCompatibleForAssign(last_val_tk, target_tid) THEN
      AbortWith2('codegen: assignment type mismatch for: ', nm);
    LLVMBuildStore(builder, v, addr);
  END;
END;

PROCEDURE CodegenIfStmt(stmt: ADRMEM);
VAR
  cond_val: ADRMEM;
  then_bb, else_bb, end_bb: ADRMEM;
  else_branch: ADRMEM;
BEGIN
  cond_val := CodegenExpr(GetObj(stmt, 'cond'));
  IF last_val_tk <> TK_BOOLEAN THEN
    AbortWith('codegen: IF condition must be BOOLEAN');

  then_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('if_then'));
  end_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('if_end'));
  else_branch := GetObjOrNil(stmt, 'else_branch');
  IF else_branch <> NIL THEN
    else_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('if_else'))
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

PROCEDURE CodegenCaseStmt(stmt: ADRMEM);
{ Lowered as a sequential chain of test/body block pairs (like a chain of
  IFs), not a jump table -- simplicity over the optimization the Python
  reference doesn't attempt either at this level (llvmlite's own -O passes
  are what would turn either shape into a real jump table). Scoped to an
  INTEGER selector: a CHAR-keyed CASE is not yet supported, consistent with
  CodegenBinOp's relational operators also only covering INTEGER/REAL. }
VAR
  case_val: ADRMEM;
  case_tk: INTEGER;
  elements, el, constants, c: ADRMEM;
  n, i, nc, ci: INTEGER32;
  end_bb, cur_test_bb, next_test_bb, body_bb: ADRMEM;
  otherwise_stmt: ADRMEM;
  cond_val, one_cond, cval: ADRMEM;
BEGIN
  case_val := CodegenExpr(GetObj(stmt, 'expr'));
  case_tk := last_val_tk;
  IF case_tk <> TK_INTEGER THEN
    AbortWith('codegen: CASE selector must be INTEGER (CHAR-keyed CASE is not yet supported)');

  elements := GetObj(stmt, 'elements');
  n := ArrSize(elements);
  otherwise_stmt := GetObjOrNil(stmt, 'otherwise');
  end_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('case_end'));

  cur_test_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('case_test'));
  LLVMBuildBr(builder, cur_test_bb);

  FOR i := 0 TO n - 1 DO
  BEGIN
    LLVMPositionBuilderAtEnd(builder, cur_test_bb);
    el := ArrItem(elements, i);
    constants := GetObj(el, 'constants');
    nc := ArrSize(constants);
    cond_val := NIL;
    FOR ci := 0 TO nc - 1 DO
    BEGIN
      c := ArrItem(constants, ci);
      IF NodeType(c) = 'RangeExpr' THEN
      BEGIN
        { The Python reference's codegen_case_stmt does not support a
          RangeExpr CASE constant either (it raises "Expression type
          RangeExpr not yet supported"), so rejecting it here too is
          matching that limitation, not falling short of it -- confirmed by
          running the same input through both pipelines. }
        AbortWith('codegen: a CASE label range (lo..hi) is not yet supported');
        one_cond := NIL;
      END
      ELSE
      BEGIN
        cval := CodegenExpr(c);
        IF last_val_tk <> TK_INTEGER THEN
          AbortWith('codegen: a CASE constant must be INTEGER');
        one_cond := LLVMBuildICmp(builder, LLVMIntEQ, case_val, cval, MakeCStr(''));
      END;
      IF cond_val = NIL THEN cond_val := one_cond
      ELSE cond_val := LLVMBuildOr(builder, cond_val, one_cond, MakeCStr(''));
    END;

    body_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('case_body'));
    IF i = n - 1 THEN
    BEGIN
      IF otherwise_stmt <> NIL THEN
        next_test_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('case_otherwise'))
      ELSE
        next_test_bb := end_bb;
    END
    ELSE
      next_test_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('case_test'));

    LLVMBuildCondBr(builder, cond_val, body_bb, next_test_bb);

    LLVMPositionBuilderAtEnd(builder, body_bb);
    CodegenStmt(GetObj(el, 'stmt'));
    LLVMBuildBr(builder, end_bb);

    cur_test_bb := next_test_bb;
  END;

  IF (n = 0) OR (otherwise_stmt <> NIL) THEN
  BEGIN
    { n = 0: cur_test_bb is still the never-entered initial block (an empty
      CASE with only OTHERWISE, or entirely empty). n > 0: cur_test_bb is
      the dedicated case_otherwise block the last iteration created above,
      still needing its body emitted. Either way it must end in a branch to
      end_bb, or it is left as an unterminated block. }
    LLVMPositionBuilderAtEnd(builder, cur_test_bb);
    IF otherwise_stmt <> NIL THEN CodegenStmt(otherwise_stmt);
    LLVMBuildBr(builder, end_bb);
  END;

  LLVMPositionBuilderAtEnd(builder, end_bb);
END;

PROCEDURE CodegenWhileStmt(stmt: ADRMEM);
VAR
  loop_bb, body_bb, end_bb, cond_val: ADRMEM;
BEGIN
  loop_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('while_loop'));
  body_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('while_body'));
  end_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('while_end'));

  LLVMBuildBr(builder, loop_bb);
  LLVMPositionBuilderAtEnd(builder, loop_bb);
  cond_val := CodegenExpr(GetObj(stmt, 'cond'));
  IF last_val_tk <> TK_BOOLEAN THEN
    AbortWith('codegen: WHILE condition must be BOOLEAN');
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
  loop_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('repeat_loop'));
  end_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('repeat_end'));

  LLVMBuildBr(builder, loop_bb);
  LLVMPositionBuilderAtEnd(builder, loop_bb);
  CodegenStmtArray(GetObj(stmt, 'body'));
  cond_val := CodegenExpr(GetObj(stmt, 'cond'));
  IF last_val_tk <> TK_BOOLEAN THEN
    AbortWith('codegen: REPEAT..UNTIL condition must be BOOLEAN');
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
    AbortWith2('codegen: undefined FOR loop variable: ', var_name);
  IF symbols[symi].tk <> TK_INTEGER THEN
    AbortWith('codegen: FOR loop variable must be INTEGER');

  start_val := CodegenExpr(GetObj(stmt, 'start'));
  IF last_val_tk <> TK_INTEGER THEN
    AbortWith('codegen: FOR loop bounds must be INTEGER');
  LLVMBuildStore(builder, start_val, symbols[symi].llvm_val);

  end_val := CodegenExpr(GetObj(stmt, 'end'));
  IF last_val_tk <> TK_INTEGER THEN
    AbortWith('codegen: FOR loop bounds must be INTEGER');

  down := GetStr(stmt, 'direction') = 'DOWNTO';

  loop_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('for_loop'));
  body_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('for_body'));
  step_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('for_step'));
  end_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('for_end'));

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

PROCEDURE ResolveStringExprCharsLen(expr: ADRMEM; VAR chars_ptr: ADRMEM; VAR len_val: ADRMEM);
{ The counterpart of the Python reference's get_string_chars_and_len: given
  a CONST STRING-typed actual argument (a string literal, or an Identifier
  naming an LSTRING/STRING variable), returns a pointer to its first
  character plus its length as an i32 -- LSTRING's is the dynamic runtime
  length byte, STRING's is its fixed declared capacity. Scoped to what
  CONCAT/COPYLST/COPYSTR need; a designator (indexed/field string
  sub-expression) is not yet supported here. }
VAR
  strval: Str255;
  symi: INTEGER32;
  tid: INTEGER;
  addr, gep_idx, len_ptr: ADRMEM;
BEGIN
  IF NodeType(expr) = 'StringLiteral' THEN
  BEGIN
    strval := DecodeStringLiteral(GetStr(expr, 'value'));
    chars_ptr := LLVMBuildGlobalStringPtr(builder, MakeCStr(strval), MakeCStr('str'));
    len_val := LLVMConstInt(i32ty, ORD(strval[0]), 0);
  END
  ELSE IF NodeType(expr) = 'Identifier' THEN
  BEGIN
    symi := LookupSym(GetStr(expr, 'name'));
    IF symi = 0 THEN
      AbortWith2('codegen: undefined variable: ', GetStr(expr, 'name'));
    tid := symbols[symi].tk;
    addr := symbols[symi].llvm_val;
    IF TypeKind(tid) = TK_LSTRING THEN
    BEGIN
      gep_idx := AllocPtrArray(2);
      SetPtrArrayElem(gep_idx, 0, LLVMConstInt(i32ty, 0, 0));
      SetPtrArrayElem(gep_idx, 1, LLVMConstInt(i32ty, 0, 0));
      len_ptr := LLVMBuildGEP2(builder, LLVMTypeForTk(tid), addr, gep_idx, 2, MakeCStr(''));
      len_val := LLVMBuildLoad2(builder, i8ty, len_ptr, MakeCStr(''));
      len_val := LLVMBuildZExt(builder, len_val, i32ty, MakeCStr(''));
      gep_idx := AllocPtrArray(2);
      SetPtrArrayElem(gep_idx, 0, LLVMConstInt(i32ty, 0, 0));
      SetPtrArrayElem(gep_idx, 1, LLVMConstInt(i32ty, 1, 0));
      chars_ptr := LLVMBuildGEP2(builder, LLVMTypeForTk(tid), addr, gep_idx, 2, MakeCStr(''));
    END
    ELSE IF TypeKind(tid) = TK_STRING THEN
    BEGIN
      len_val := LLVMConstInt(i32ty, types[tid].hi, 0);
      gep_idx := AllocPtrArray(2);
      SetPtrArrayElem(gep_idx, 0, LLVMConstInt(i32ty, 0, 0));
      SetPtrArrayElem(gep_idx, 1, LLVMConstInt(i32ty, 0, 0));
      chars_ptr := LLVMBuildGEP2(builder, LLVMTypeForTk(tid), addr, gep_idx, 2, MakeCStr(''));
    END
    ELSE
    BEGIN
      AbortWith2('codegen: not a string-typed variable: ', GetStr(expr, 'name'));
      chars_ptr := NIL;
      len_val := NIL;
    END;
  END
  ELSE
  BEGIN
    AbortWith('codegen: unsupported string expression (only literals and bare variables are supported)');
    chars_ptr := NIL;
    len_val := NIL;
  END;
END;

PROCEDURE EmitByteCopyLoop(dest_ptr, src_ptr: ADRMEM; count: ADRMEM);
{ Copies `count` (an i32 LLVMValueRef) bytes one at a time from src_ptr to
  dest_ptr, via an alloca'd i32 loop counter -- the same alloca-based
  loop-variable idiom CodegenForStmt already uses, rather than building
  phi nodes by hand. Used by CONCAT/COPYLST/COPYSTR, whose source length is
  only known at runtime (an LSTRING's dynamic length byte), so the
  compile-time-known-literal shortcut CodegenLStringLiteralAssign/
  CodegenStringLiteralAssign use does not apply. }
VAR
  i_slot: ADRMEM;
  loop_bb, body_bb, end_bb: ADRMEM;
  cur_i, cmp_val, next_i: ADRMEM;
  s_ptr, d_ptr, byte_val: ADRMEM;
  gep_idx: ADRMEM;
BEGIN
  i_slot := LLVMBuildAlloca(builder, i32ty, MakeCStr(''));
  LLVMBuildStore(builder, LLVMConstInt(i32ty, 0, 0), i_slot);

  loop_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('strcpy_loop'));
  body_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('strcpy_body'));
  end_bb := LLVMAppendBasicBlockInContext(ctx, cur_fn, MakeCStr('strcpy_end'));

  LLVMBuildBr(builder, loop_bb);
  LLVMPositionBuilderAtEnd(builder, loop_bb);
  cur_i := LLVMBuildLoad2(builder, i32ty, i_slot, MakeCStr(''));
  cmp_val := LLVMBuildICmp(builder, LLVMIntSLT, cur_i, count, MakeCStr(''));
  LLVMBuildCondBr(builder, cmp_val, body_bb, end_bb);

  LLVMPositionBuilderAtEnd(builder, body_bb);
  cur_i := LLVMBuildLoad2(builder, i32ty, i_slot, MakeCStr(''));
  gep_idx := AllocPtrArray(1);
  SetPtrArrayElem(gep_idx, 0, cur_i);
  s_ptr := LLVMBuildGEP2(builder, i8ty, src_ptr, gep_idx, 1, MakeCStr(''));
  gep_idx := AllocPtrArray(1);
  SetPtrArrayElem(gep_idx, 0, cur_i);
  d_ptr := LLVMBuildGEP2(builder, i8ty, dest_ptr, gep_idx, 1, MakeCStr(''));
  byte_val := LLVMBuildLoad2(builder, i8ty, s_ptr, MakeCStr(''));
  LLVMBuildStore(builder, byte_val, d_ptr);
  next_i := LLVMBuildAdd(builder, cur_i, LLVMConstInt(i32ty, 1, 0), MakeCStr(''));
  LLVMBuildStore(builder, next_i, i_slot);
  LLVMBuildBr(builder, loop_bb);

  LLVMPositionBuilderAtEnd(builder, end_bb);
END;

PROCEDURE CodegenConcat(args: ADRMEM);
{ CONCAT(VAR D: LSTRING; CONST S: STRING): appends S's characters to D and
  grows D's length byte by length(S) -- manual 11-20. No RANGECK-style
  capacity guard yet (matches this file's documented MATHCK/RANGECK
  simplification elsewhere: a capacity overflow here just corrupts memory,
  same as an unchecked array index). }
VAR
  d_arg: ADRMEM;
  d_symi: INTEGER32;
  d_tid: INTEGER;
  d_addr, len_ptr, dest_len_byte, dest_len, new_len, new_len_byte: ADRMEM;
  dest_chars, append_ptr: ADRMEM;
  src_chars, src_len: ADRMEM;
  gep_idx: ADRMEM;
BEGIN
  IF ArrSize(args) <> 2 THEN
    AbortWith('codegen: CONCAT expects exactly 2 arguments');
  d_arg := ArrItem(args, 0);
  IF NodeType(d_arg) <> 'Identifier' THEN
    AbortWith('codegen: CONCAT''s destination must be a bare LSTRING variable');
  d_symi := LookupSym(GetStr(d_arg, 'name'));
  IF d_symi = 0 THEN
    AbortWith2('codegen: undefined variable: ', GetStr(d_arg, 'name'));
  d_tid := symbols[d_symi].tk;
  IF TypeKind(d_tid) <> TK_LSTRING THEN
    AbortWith('codegen: CONCAT''s destination must be an LSTRING variable');
  d_addr := symbols[d_symi].llvm_val;

  ResolveStringExprCharsLen(ArrItem(args, 1), src_chars, src_len);

  gep_idx := AllocPtrArray(2);
  SetPtrArrayElem(gep_idx, 0, LLVMConstInt(i32ty, 0, 0));
  SetPtrArrayElem(gep_idx, 1, LLVMConstInt(i32ty, 0, 0));
  len_ptr := LLVMBuildGEP2(builder, LLVMTypeForTk(d_tid), d_addr, gep_idx, 2, MakeCStr(''));
  dest_len_byte := LLVMBuildLoad2(builder, i8ty, len_ptr, MakeCStr(''));
  dest_len := LLVMBuildZExt(builder, dest_len_byte, i32ty, MakeCStr(''));
  new_len := LLVMBuildAdd(builder, dest_len, src_len, MakeCStr(''));

  gep_idx := AllocPtrArray(2);
  SetPtrArrayElem(gep_idx, 0, LLVMConstInt(i32ty, 0, 0));
  SetPtrArrayElem(gep_idx, 1, LLVMConstInt(i32ty, 1, 0));
  dest_chars := LLVMBuildGEP2(builder, LLVMTypeForTk(d_tid), d_addr, gep_idx, 2, MakeCStr(''));
  gep_idx := AllocPtrArray(1);
  SetPtrArrayElem(gep_idx, 0, dest_len);
  append_ptr := LLVMBuildGEP2(builder, i8ty, dest_chars, gep_idx, 1, MakeCStr(''));
  EmitByteCopyLoop(append_ptr, src_chars, src_len);

  new_len_byte := LLVMBuildTrunc(builder, new_len, i8ty, MakeCStr(''));
  LLVMBuildStore(builder, new_len_byte, len_ptr);
END;

PROCEDURE CodegenProcCallStmt(stmt: ADRMEM);
VAR
  name: Str255;
  discard: ADRMEM;
  args, arg0: ADRMEM;
  symi: INTEGER32;
  ptr_tid, pointee_tid: INTEGER;
  raw, casted, call_args: ADRMEM;
BEGIN
  name := GetStr(stmt, 'name');
  IF name = 'WRITELN' THEN
    CodegenWriteArgs(GetObj(stmt, 'args'), TRUE)
  ELSE IF name = 'WRITE' THEN
    CodegenWriteArgs(GetObj(stmt, 'args'), FALSE)
  ELSE IF name = 'CONCAT' THEN
    CodegenConcat(GetObj(stmt, 'args'))
  ELSE IF (name = 'NEW') OR (name = 'DISPOSE') THEN
  BEGIN
    args := GetObj(stmt, 'args');
    IF ArrSize(args) <> 1 THEN
      AbortWith2('codegen: expected one pointer argument to: ', name);
    arg0 := ArrItem(args, 0);
    IF NodeType(arg0) <> 'Identifier' THEN
      AbortWith2('codegen: argument must be a bare pointer variable: ', name);
    symi := LookupSym(GetStr(arg0, 'name'));
    IF symi = 0 THEN
      AbortWith2('codegen: undefined variable: ', GetStr(arg0, 'name'));
    ptr_tid := symbols[symi].tk;
    IF TypeKind(ptr_tid) <> TK_POINTER THEN
      AbortWith2('codegen: argument is not a POINTER variable: ', name);
    IF name = 'NEW' THEN
    BEGIN
      pointee_tid := types[ptr_tid].elem_tid;
      call_args := AllocPtrArray(1);
      SetPtrArrayElem(call_args, 0, LLVMConstInt(i32ty, TypeSizeBytes(pointee_tid), 0));
      raw := LLVMBuildCall2(builder, malloc_fnty, malloc_fn, call_args, 1, MakeCStr(''));
      casted := LLVMBuildBitCast(builder, raw, LLVMTypeForTk(ptr_tid), MakeCStr(''));
      LLVMBuildStore(builder, casted, symbols[symi].llvm_val);
    END
    ELSE
    BEGIN
      raw := LLVMBuildLoad2(builder, LLVMTypeForTk(ptr_tid), symbols[symi].llvm_val, MakeCStr(''));
      casted := LLVMBuildBitCast(builder, raw, i8ptrty, MakeCStr(''));
      call_args := AllocPtrArray(1);
      SetPtrArrayElem(call_args, 0, casted);
      discard := LLVMBuildCall2(builder, free_fnty, free_fn, call_args, 1, MakeCStr(''));
    END;
  END
  ELSE
    discard := CodegenCallCommon(name, GetObj(stmt, 'args'));
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
  ELSE IF nt = 'CaseStmt' THEN CodegenCaseStmt(stmt)
  ELSE IF nt = 'ProcCallStmt' THEN CodegenProcCallStmt(stmt)
  ELSE
  BEGIN
    msg := 'codegen: unhandled statement kind: ';
    CONCAT(msg, nt);
    AbortWith(msg);
  END;
END;

{ ============================== declarations =============================== }

PROCEDURE CodegenDecl(decl: ADRMEM); FORWARD;

PROCEDURE CodegenDeclList(decls_arr: ADRMEM);
VAR
  n, i: INTEGER32;
BEGIN
  n := ArrSize(decls_arr);
  FOR i := 0 TO n - 1 DO
    CodegenDecl(ArrItem(decls_arr, i));
END;

PROCEDURE CodegenVarDecl(decl: ADRMEM);
VAR
  names: ADRMEM;
  tk: INTEGER;
  n, i: INTEGER32;
BEGIN
  tk := ResolveTypeExpr(GetObj(decl, 'type_expr'));
  names := GetObj(decl, 'names');
  n := ArrSize(names);
  FOR i := 0 TO n - 1 DO
    DeclareVar(CStrToStr255(cJSON_GetStringValue(ArrItem(names, i))), tk);
END;

PROCEDURE FlattenParams(params_arr: ADRMEM; VAR n: INTEGER32; VAR names: ParamNameArr;
                         VAR tks: ParamTkArr; VAR isvar: ParamVarArr);
{ A Pascal formal-parameter section groups several names under one type
  (`a, b: INTEGER`); this flattens that grouping into parallel arrays of
  one entry per actual parameter, matching how llvm-c's LLVMFunctionType
  and the routine table both want one slot per parameter, not one per
  group. }
VAR
  np, pi, nn, ni: INTEGER32;
  param, pnames: ADRMEM;
  tk: INTEGER;
  is_v: BOOLEAN;
BEGIN
  n := 0;
  np := ArrSize(params_arr);
  FOR pi := 0 TO np - 1 DO
  BEGIN
    param := ArrItem(params_arr, pi);
    tk := ResolveTypeExpr(GetObj(param, 'type_expr'));
    is_v := GetStr(param, 'mode') = 'VAR';
    IF (NOT is_v) AND ((TypeKind(tk) = TK_ARRAY) OR (TypeKind(tk) = TK_RECORD) OR
       (TypeKind(tk) = TK_LSTRING) OR (TypeKind(tk) = TK_STRING)) THEN
      AbortWith('codegen: value-mode ARRAY/RECORD/LSTRING/STRING parameters are not supported (pass by VAR)');
    pnames := GetObj(param, 'names');
    nn := ArrSize(pnames);
    FOR ni := 0 TO nn - 1 DO
    BEGIN
      IF n >= MAX_PARAMS THEN AbortWith('codegen: too many parameters');
      n := n + 1;
      names[n] := CStrToStr255(cJSON_GetStringValue(ArrItem(pnames, ni)));
      tks[n] := tk;
      isvar[n] := is_v;
    END;
  END;
END;

PROCEDURE CodegenRoutineDecl(decl: ADRMEM; is_func: BOOLEAN);
VAR
  name: Str255;
  params_arr, body_blk: ADRMEM;
  n: INTEGER32;
  names: ParamNameArr;
  tks: ParamTkArr;
  isvar: ParamVarArr;
  param_llvm_types: ADRMEM;
  i: INTEGER32;
  ret_tk: INTEGER;
  ret_llvm_ty, fnty, fn, entry_bb2: ADRMEM;
  param_val, palloca, ret_load: ADRMEM;
BEGIN
  name := GetStr(decl, 'name');
  IF LookupRoutine(name) <> 0 THEN
    AbortWith2('codegen: duplicate routine declaration: ', name);

  params_arr := GetObj(decl, 'params');
  FlattenParams(params_arr, n, names, tks, isvar);

  param_llvm_types := AllocPtrArray(n);
  FOR i := 1 TO n DO
  BEGIN
    IF isvar[i] THEN
      SetPtrArrayElem(param_llvm_types, i - 1, LLVMPointerType(LLVMTypeForTk(tks[i]), 0))
    ELSE
      SetPtrArrayElem(param_llvm_types, i - 1, LLVMTypeForTk(tks[i]));
  END;

  IF is_func THEN
  BEGIN
    ret_tk := ResolveTypeExpr(GetObj(decl, 'return_type'));
    ret_llvm_ty := LLVMTypeForTk(ret_tk);
  END
  ELSE
  BEGIN
    ret_tk := TK_UNKNOWN;
    ret_llvm_ty := voidty;
  END;

  fnty := LLVMFunctionType(ret_llvm_ty, param_llvm_types, n, 0);
  fn := LLVMAddFunction(modl, MakeCStr(name), fnty);

  { Register the routine before codegen'ing its body -- direct
    self-recursion (Fact calling Fact) needs the routine table entry to
    already exist when the body's own FuncCall/ProcCallStmt nodes resolve
    it. Mutual recursion (A calls B declared later) is out of scope, same
    as it would be without a FORWARD declaration in standard Pascal. }
  nroutines := nroutines + 1;
  routines[nroutines].name := name;
  routines[nroutines].is_func := is_func;
  routines[nroutines].fn := fn;
  routines[nroutines].fnty := fnty;
  routines[nroutines].ret_tk := ret_tk;
  routines[nroutines].nparams := n;
  FOR i := 1 TO n DO
  BEGIN
    routines[nroutines].param_tk[i] := tks[i];
    routines[nroutines].param_is_var[i] := isvar[i];
  END;

  entry_bb2 := LLVMAppendBasicBlockInContext(ctx, fn, MakeCStr('entry'));
  LLVMPositionBuilderAtEnd(builder, entry_bb2);
  cur_fn := fn;
  PushScope;
  in_local_scope := TRUE;

  IF is_func THEN
  BEGIN
    cur_func_name := name;
    cur_func_ret_tk := ret_tk;
    cur_func_ret_slot := LLVMBuildAlloca(builder, ret_llvm_ty, MakeCStr('return_value'));
    IF ret_tk = TK_REAL THEN LLVMBuildStore(builder, LLVMConstReal(dblty, 0.0), cur_func_ret_slot)
    ELSE LLVMBuildStore(builder, LLVMConstInt(ret_llvm_ty, 0, 0), cur_func_ret_slot);
  END
  ELSE
    cur_func_name := '';

  FOR i := 1 TO n DO
  BEGIN
    param_val := LLVMGetParam(fn, i - 1);
    IF isvar[i] THEN
      palloca := param_val { the incoming pointer already IS the storage }
    ELSE
    BEGIN
      palloca := LLVMBuildAlloca(builder, LLVMTypeForTk(tks[i]), MakeCStr(names[i]));
      LLVMBuildStore(builder, param_val, palloca);
    END;
    nsymbols := nsymbols + 1;
    symbols[nsymbols].name := names[i];
    symbols[nsymbols].tk := tks[i];
    symbols[nsymbols].llvm_val := palloca;
  END;

  body_blk := GetObj(decl, 'body');
  IF NodeType(body_blk) <> 'Block' THEN
    AbortWith('codegen: expected Block as routine body');
  CodegenDeclList(GetObj(body_blk, 'decls'));
  CodegenStmtArray(GetObj(body_blk, 'body'));

  IF is_func THEN
  BEGIN
    ret_load := LLVMBuildLoad2(builder, ret_llvm_ty, cur_func_ret_slot, MakeCStr(''));
    ret_load := LLVMBuildRet(builder, ret_load);
  END
  ELSE
    LLVMBuildRetVoid(builder);

  PopScope;
  in_local_scope := FALSE;
  cur_func_name := '';
  cur_fn := main_fn;
  LLVMPositionBuilderAtEnd(builder, entry_bb);
END;

PROCEDURE CodegenTypeDecl(decl: ADRMEM);
VAR
  name: Str255;
  tid: INTEGER;
BEGIN
  name := GetStr(decl, 'name');
  IF LookupNamedType(name) <> 0 THEN
    AbortWith2('codegen: duplicate type declaration: ', name);
  tid := ResolveTypeExpr(GetObj(decl, 'type_expr'));
  IF tid < 5 THEN
    AbortWith2('codegen: TYPE cannot alias a bare scalar name: ', name);
  types[tid].name := name;
END;

PROCEDURE CodegenDecl(decl: ADRMEM);
VAR
  nt: Str255;
BEGIN
  nt := NodeType(decl);
  IF nt = 'VarDecl' THEN CodegenVarDecl(decl)
  ELSE IF nt = 'TypeDecl' THEN CodegenTypeDecl(decl)
  ELSE IF nt = 'ProcDecl' THEN CodegenRoutineDecl(decl, FALSE)
  ELSE IF nt = 'FuncDecl' THEN CodegenRoutineDecl(decl, TRUE)
  ELSE
    AbortWith2('codegen: unhandled declaration kind: ', nt);
END;

{ ============================== driver =================================== }

VAR
  root, block, body: ADRMEM;
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
    AbortWith('codegen: expected ProgramUnit at root');

  ctx := LLVMContextCreate;
  modl := LLVMModuleCreateWithNameInContext(MakeCStr('pascal_program'), ctx);
  i32ty := LLVMInt32TypeInContext(ctx);
  i16ty := LLVMInt16TypeInContext(ctx);
  i8ty := LLVMInt8TypeInContext(ctx);
  i1ty := LLVMInt1TypeInContext(ctx);
  i64ty := LLVMInt64TypeInContext(ctx);
  dblty := LLVMDoubleTypeInContext(ctx);
  i8ptrty := LLVMPointerType(i8ty, 0);
  voidty := LLVMVoidTypeInContext(ctx);
  setty := LLVMArrayType(i64ty, 4);
  generic_set_tid := 0;

  main_fnty := LLVMFunctionType(i32ty, NIL, 0, 0);
  main_fn := LLVMAddFunction(modl, MakeCStr('main'), main_fnty);
  entry_bb := LLVMAppendBasicBlockInContext(ctx, main_fn, MakeCStr('entry'));
  builder := LLVMCreateBuilderInContext(ctx);
  LLVMPositionBuilderAtEnd(builder, entry_bb);
  cur_fn := main_fn;

  param_arr := AllocPtrArray(1);
  SetPtrArrayElem(param_arr, 0, i8ptrty);
  printf_fnty := LLVMFunctionType(i32ty, param_arr, 1, 1);
  printf_fn := LLVMAddFunction(modl, MakeCStr('printf'), printf_fnty);

  param_arr := AllocPtrArray(1);
  SetPtrArrayElem(param_arr, 0, i32ty);
  malloc_fnty := LLVMFunctionType(i8ptrty, param_arr, 1, 0);
  malloc_fn := LLVMAddFunction(modl, MakeCStr('malloc'), malloc_fnty);

  param_arr := AllocPtrArray(1);
  SetPtrArrayElem(param_arr, 0, i8ptrty);
  free_fnty := LLVMFunctionType(voidty, param_arr, 1, 0);
  free_fn := LLVMAddFunction(modl, MakeCStr('free'), free_fnty);

  nsymbols := 0;
  scope_top := 0;
  in_local_scope := FALSE;
  nroutines := 0;
  cur_func_name := '';
  ntypes := 4; { ids 1..4 are the bare TK_INTEGER..TK_CHAR scalars, not
                 `types` table entries -- the first RegisterType call must
                 hand out id 5, not 1. }
  nfields := 0;

  block := GetObj(root, 'block');
  IF NodeType(block) <> 'Block' THEN
    AbortWith('codegen: expected Block under ProgramUnit');

  CodegenDeclList(GetObj(block, 'decls'));

  body := GetObj(block, 'body');
  CodegenStmtArray(body);

  ret_val := LLVMBuildRet(builder, LLVMConstInt(i32ty, 0, 0));

  verify_msg_raw := malloc(8);
  verify_msg := verify_msg_raw;
  verify_msg^ := NIL;
  ok := LLVMVerifyModule(modl, LLVMAbortProcessAction, verify_msg_raw);
  IF ok <> 0 THEN
  BEGIN
    res_c := puts(MakeCStr('codegen: module verification failed:'));
    res_c := puts(verify_msg^);
    exit(1);
  END;

  ir_text := LLVMPrintModuleToString(modl);
  res_c := puts(ir_text);
END.
