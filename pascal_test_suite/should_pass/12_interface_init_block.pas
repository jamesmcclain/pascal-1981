(* should_pass: interface_unit may include proc/func headers and an init block. *)
INTERFACE;
UNIT GRAPHICS (BJUMP, WJUMP);
USES OTHER;
PROCEDURE BJUMP (X, Y: INTEGER);
FUNCTION WJUMP (X, Y: INTEGER): INTEGER;
BEGIN
END;
END;
