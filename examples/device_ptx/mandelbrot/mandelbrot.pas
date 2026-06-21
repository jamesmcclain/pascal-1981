{ DEVICE IMPLEMENTATION of the Mandelbrot kernels (interface in mandelbrot.inc).

  A faithful port of mandelbrot.cu. Each pixel is one thread; the escape-time
  iteration count is written to output[py*width + px]. The f32 kernel computes
  entirely in single precision (REAL32); the f64 kernel in double (REAL64).

  Note the integer literal 2 in `2 * x * y`: an integer operand promotes to the
  surrounding real width, so the f32 kernel stays pure f32 (the analog of CUDA's
  2.0f) without a stray double creeping in. }

DEVICE IMPLEMENTATION OF MANDELBROT;

TYPE
  PIXELS = SUPER ARRAY [0..*] OF INTEGER32;

PROCEDURE mandelbrot_f32(
  output: ADS(GLOBAL) OF PIXELS;
  width: INTEGER32;
  height: INTEGER32;
  max_iter: INTEGER32;
  x_min: REAL32;
  x_max: REAL32;
  y_min: REAL32;
  y_max: REAL32);
VAR
  px, py, idx, iteration: INTEGER32;
  wd, hd, x0, y0, x, y, xtemp: REAL32;
BEGIN
  px := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  py := THREADIDX_Y + BLOCKIDX_Y * BLOCKDIM_Y;
  IF (px < width) AND (py < height) THEN
  BEGIN
    IF width > 1 THEN wd := width - 1 ELSE wd := 1;
    IF height > 1 THEN hd := height - 1 ELSE hd := 1;
    x0 := x_min + (x_max - x_min) * px / wd;
    y0 := y_min + (y_max - y_min) * py / hd;
    x := 0;
    y := 0;
    iteration := 0;
    { Escape radius squared = 4; an integer literal here promotes to the
      surrounding REAL32 width, keeping the test in single precision. }
    WHILE ((x * x + y * y) <= 4) AND (iteration < max_iter) DO
    BEGIN
      xtemp := x * x - y * y + x0;
      y := 2 * x * y + y0;
      x := xtemp;
      iteration := iteration + 1
    END;
    idx := py * width + px;
    output^[idx] := iteration
  END
END;

PROCEDURE mandelbrot_f64(
  output: ADS(GLOBAL) OF PIXELS;
  width: INTEGER32;
  height: INTEGER32;
  max_iter: INTEGER32;
  x_min: REAL64;
  x_max: REAL64;
  y_min: REAL64;
  y_max: REAL64);
VAR
  px, py, idx, iteration: INTEGER32;
  wd, hd, x0, y0, x, y, xtemp: REAL64;
BEGIN
  px := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  py := THREADIDX_Y + BLOCKIDX_Y * BLOCKDIM_Y;
  IF (px < width) AND (py < height) THEN
  BEGIN
    IF width > 1 THEN wd := width - 1 ELSE wd := 1;
    IF height > 1 THEN hd := height - 1 ELSE hd := 1;
    x0 := x_min + (x_max - x_min) * px / wd;
    y0 := y_min + (y_max - y_min) * py / hd;
    x := 0.0;
    y := 0.0;
    iteration := 0;
    WHILE ((x * x + y * y) <= 4.0) AND (iteration < max_iter) DO
    BEGIN
      xtemp := x * x - y * y + x0;
      y := 2.0 * x * y + y0;
      x := xtemp;
      iteration := iteration + 1
    END;
    idx := py * width + px;
    output^[idx] := iteration
  END
END;
.
