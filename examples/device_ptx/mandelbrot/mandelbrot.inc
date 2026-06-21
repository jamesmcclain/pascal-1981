{ DEVICE INTERFACE for the Mandelbrot kernels.

  This is the interface half of a DEVICE UNIT. By convention in this repository
  interface files carry the `.inc` extension (the compiler does not require it;
  resolve_module_path also accepts an extensionless sibling or a `.pas` file).

  Both exported procedures are launchable PTX `.entry` points. Their parameter
  ABI is a drop-in match for the CUDA kernels in the companion mandelbrot-gpu
  repository (mandelbrot.cu):

    extern "C" __global__ void mandelbrot_f32(
        int* output, int width, int height, int max_iter,
        float  x_min, float  x_max, float  y_min, float  y_max);
    extern "C" __global__ void mandelbrot_f64(
        int* output, int width, int height, int max_iter,
        double x_min, double x_max, double y_min, double y_max);

  The output buffer is a SUPER ARRAY [0..*] OF INTEGER32, which lowers to a raw
  `int*` device pointer (no fat descriptor, no hidden bound parameters); image
  dimensions are carried explicitly by `width`/`height`, exactly as in CUDA. }

DEVICE INTERFACE;
UNIT MANDELBROT (mandelbrot_f32, mandelbrot_f64);

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

PROCEDURE mandelbrot_f64(
  output: ADS(GLOBAL) OF PIXELS;
  width: INTEGER32;
  height: INTEGER32;
  max_iter: INTEGER32;
  x_min: REAL64;
  x_max: REAL64;
  y_min: REAL64;
  y_max: REAL64);

END;
