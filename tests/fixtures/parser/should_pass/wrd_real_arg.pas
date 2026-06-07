(* 4.7: WRD does not accept a REAL argument — must be rejected *)
PROGRAM WrdRealArg;
VAR
    w : WORD;
    x : REAL;
BEGIN
    x := 3.14;
    w := WRD(x)   (* ERROR: REAL is not an ordinal type *)
END.
