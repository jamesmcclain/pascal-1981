{ Host program for the fill_indices example.

  The companion DEVICE UNIT (fill.pas / fill.inc) exports one launchable kernel,
  `fill_indices`, whose PTX is a drop-in match for a hand-written CUDA kernel.
  This host PROGRAM does the full device orchestration *in Pascal* -- allocate,
  copy, launch, copy back -- exactly the calls a CUDA host would make, lowered by
  the compiler to the pas_dev_* runtime shim (CPU stand-in or real CUDA driver,
  chosen at build time; see the Makefile's DEVICE switch).

  The kernel writes outp[i] := i for each global thread index i < n, so a correct
  run leaves the buffer holding 0, 1, 2, ... n-1. }

(*$INCLUDE:'fill.inc'*)
PROGRAM fill_host(output);

USES FILL (fill_indices);

CONST
  n = 256;                          { fill.inc fixes the buffer at 256 elements }

VAR
  host_buf: ARRAY [0..255] OF INTEGER32;
  dev: ADRMEM;
  i, bytes, mismatches: INTEGER;

BEGIN
  bytes := n * 4;                   { 256 x INTEGER32 }

  { Seed with a sentinel so we can tell written elements from untouched ones. }
  FOR i := 0 TO n - 1 DO
    host_buf[i] := -1;

  { --- device orchestration ------------------------------------------------ }
  dev := DEVALLOC(bytes);
  DEVCOPYTO(dev, ADR host_buf, bytes);
  { One block of 256 threads -> one thread per element (grid, block). }
  LAUNCH(fill_indices, 1, 256, dev, n);
  DEVCOPYFROM(ADR host_buf, dev, bytes);
  DEVFREE(dev);

  { --- check + report ------------------------------------------------------ }
  mismatches := 0;
  FOR i := 0 TO n - 1 DO
    IF host_buf[i] <> i THEN
      mismatches := mismatches + 1;

  WRITE('fill_indices: first 8 = ');
  FOR i := 0 TO 7 DO
    WRITE(host_buf[i], ' ');
  WRITELN;

  IF mismatches = 0 THEN
    WRITELN('OK: all ', n, ' indices correct')
  ELSE
    WRITELN('FAIL: ', mismatches, ' of ', n, ' elements wrong')
END.
