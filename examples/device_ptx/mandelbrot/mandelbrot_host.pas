{ Host program for the Mandelbrot example.

  The companion DEVICE UNIT (mandelbrot.pas / mandelbrot.inc) exports two
  launchable kernels, `mandelbrot_f32` and `mandelbrot_f64`, whose PTX is a
  drop-in match for the CUDA kernels in mandelbrot.cu. This host PROGRAM does the
  full device orchestration *in Pascal* (allocate / copy / launch / copy back),
  lowered by the compiler to the pas_dev_* runtime shim -- the CPU stand-in or
  the real CUDA driver, selected at build time by the Makefile's DEVICE switch.

  It renders one fixed view of the set into an in-memory image: the kernel fills
  a width*height buffer of escape-iteration counts, a fixed "fire" palette turns
  those into an in-memory RGB image, and an ASCII reduction is printed so a run
  is observably correct. Inspired by the host in the companion mandelbrot-gpu
  repository, but deliberately minimal: one fixed view, one palette, nothing
  written to disk.

  This host launches the f64 kernel, so the plane coordinates are ordinary REAL
  (double) values and no wide-reals feature is needed; INTEGER32 is used for the
  pixel buffer to match the kernel's int* output (needs -f wide-integers). }

(*$INCLUDE:'mandelbrot.inc'*)
PROGRAM mandelbrot_host(output);

USES MANDELBROT (mandelbrot_f64);

CONST
  width    = 78;
  height   = 28;
  max_iter = 120;
  pixmax   = 2183;   { width * height - 1     (array bounds take a constant, }
  rgbmax   = 6551;   { width * height * 3 - 1  not an expression, so named here) }

VAR
  iters: ARRAY [0 .. pixmax] OF INTEGER32;   { escape counts }
  rgb:   ARRAY [0 .. rgbmax] OF INTEGER32;   { in-memory image (R,G,B per pixel) }
  dev: ADRMEM;
  i, idx, px, py, it, bytes: INTEGER;
  x_min, x_max, y_min, y_max, norm, c: REAL;
  ch: CHAR;

BEGIN
  { Classic full-set overview window. }
  x_min := -2.5;
  x_max :=  1.0;
  y_min := -1.25;
  y_max :=  1.25;
  bytes := width * height * 4;

  { --- device orchestration ------------------------------------------------ }
  dev := DEVALLOC(bytes);
  { 2-D launch: 16x16 blocks covering the image (one thread per pixel on a GPU). }
  LAUNCH(mandelbrot_f64, 5, 2, 1, 16, 16, 1,
         dev, width, height, max_iter, x_min, x_max, y_min, y_max);
  DEVCOPYFROM(ADR iters, dev, bytes);
  DEVFREE(dev);

  { --- fixed "fire" palette -> in-memory RGB image -------------------------- }
  FOR i := 0 TO width * height - 1 DO
  BEGIN
    it := iters[i];
    IF it >= max_iter THEN
    BEGIN                                  { interior of the set: black }
      rgb[3 * i]     := 0;
      rgb[3 * i + 1] := 0;
      rgb[3 * i + 2] := 0
    END
    ELSE
    BEGIN
      norm := it / max_iter;               { 0 .. 1 }
      c := norm * 512;          IF c > 255 THEN c := 255;  rgb[3 * i]     := TRUNC(c);
      c := (norm - 0.5) * 512;  IF c < 0 THEN c := 0;      rgb[3 * i + 1] := TRUNC(c);
      c := (norm - 0.75) * 1024; IF c < 0 THEN c := 0;
                                 IF c > 255 THEN c := 255;  rgb[3 * i + 2] := TRUNC(c)
    END
  END;

  { --- ASCII reduction of the same image, so a run is observable ----------- }
  WRITELN('Mandelbrot ', width, 'x', height, ', max_iter=', max_iter,
          ' (f64 kernel)');
  FOR py := 0 TO height - 1 DO
  BEGIN
    FOR px := 0 TO width - 1 DO
    BEGIN
      idx := py * width + px;
      it := iters[idx];
      IF it >= max_iter THEN
        ch := '@'                          { in the set }
      ELSE
      BEGIN
        i := it * 7 DIV max_iter;          { escape speed -> density ramp 0..6 }
        IF i < 0 THEN i := 0;
        IF i > 6 THEN i := 6;
        CASE i OF
          0: ch := ' ';
          1: ch := '.';
          2: ch := ':';
          3: ch := '-';
          4: ch := '=';
          5: ch := '+';
          6: ch := '*'
        END
      END;
      WRITE(ch)
    END;
    WRITELN
  END
END.
