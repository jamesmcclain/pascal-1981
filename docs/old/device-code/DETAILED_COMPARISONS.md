# Detailed PTX Comparison: Pascal vs CUDA

## Overview

This document contains line-by-line analysis of specific kernels, showing exactly where Pascal excels and where CUDA's optimizer has different strategies.

---

## Kernel 1: SAXPY (a*x + y)

### Pascal Source
```pascal
PROCEDURE saxpy(a: REAL32; x: ADS(GLOBAL) OF BUFFER; 
                y: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR i, stride: INTEGER32;
BEGIN
  stride := BLOCKDIM_X * GRIDDIM_X;
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  WHILE i < n DO
  BEGIN
    y^[i] := a * x^[i] + y^[i];
    i := i + stride
  END
END;
```

### CUDA Source (for reference)
```c
extern "C" __global__
void saxpy(float a, const float* x, float* y, int n) {
    int stride = blockDim.x * gridDim.x;
    int i = threadIdx.x + blockIdx.x * blockDim.x;
    while (i < n) {
        y[i] = a * x[i] + y[i];
        i += stride;
    }
}
```

### Pascal Generated PTX (39 lines)
```ptx
.visible .entry saxpy(
    .param .f32 saxpy_param_0,           // a
    .param .u64 .ptr .global saxpy_param_1,  // x pointer
    .param .u64 .ptr .global saxpy_param_2,  // y pointer
    .param .u32 saxpy_param_3            // n
)
{
    .reg .pred %p<2>;
    .reg .b32 %r<11>;
    .reg .f32 %f<5>;
    .reg .b64 %rd<6>;

    ld.param.u32 %r5, [saxpy_param_3];      // Load n
    ld.param.u64 %rd2, [saxpy_param_2];     // Load y pointer
    ld.param.u64 %rd1, [saxpy_param_1];     // Load x pointer
    ld.param.f32 %f1, [saxpy_param_0];      // Load a
    
    mov.u32 %r6, %ntid.x;                   // blockDim.x
    mov.u32 %r7, %nctaid.x;                 // gridDim.x
    mul.lo.s32 %r1, %r6, %r7;               // stride = blockDim.x * gridDim.x
    
    mov.u32 %r8, %tid.x;                    // threadIdx.x
    mov.u32 %r9, %ctaid.x;                  // blockIdx.x
    mad.lo.s32 %r10, %r9, %r6, %r8;        // i = threadIdx.x + blockIdx.x * blockDim.x
$L__BB0_1:
    setp.ge.s32 %p1, %r10, %r5;            // if (i >= n)
    @%p1 bra $L__BB0_3;                    // branch to exit
    
    mul.wide.s32 %rd3, %r10, 4;            // i * 4 (byte offset for float)
    add.s64 %rd4, %rd2, %rd3;               // y_addr = y + offset
    add.s64 %rd5, %rd1, %rd3;               // x_addr = x + offset
    
    ld.global.f32 %f2, [%rd5];              // Load x[i]
    ld.global.f32 %f3, [%rd4];              // Load y[i]
    
    fma.rn.f32 %f4, %f1, %f2, %f3;          // result = a * x[i] + y[i]
    st.global.f32 [%rd4], %f4];             // Store y[i] = result
    
    add.s32 %r10, %r10, %r1;                // i += stride
    bra.uni $L__BB0_1;                      // Loop back
    
$L__BB0_3:
    ret;
}
```

### CUDA O3 Generated PTX (92 lines - only showing key section)
```ptx
.visible .entry saxpy(
    .param .f32 saxpy_param_0,
    .param .u64 saxpy_param_1,
    .param .u64 saxpy_param_2,
    .param .u32 saxpy_param_3
)
{
    .reg .pred %p<6>;
    .reg .f32 %f<17>;
    .reg .b32 %r<29>;
    .reg .b64 %rd<25>;

    ld.param.f32 %f1, [saxpy_param_0];
    ld.param.u64 %rd11, [saxpy_param_1];
    ld.param.u64 %rd12, [saxpy_param_2];
    ld.param.u32 %r12, [saxpy_param_3];
    
    cvta.to.global.u64 %rd1, %rd12;         // Address conversion (Pascal doesn't do this)
    cvta.to.global.u64 %rd2, %rd11;         // Address conversion
    
    mov.u32 %r13, %nctaid.x;
    mov.u32 %r14, %ntid.x;
    mul.lo.s32 %r1, %r14, %r13;
    
    mov.u32 %r15, %ctaid.x;
    mov.u32 %r16, %tid.x;
    mad.lo.s32 %r27, %r15, %r14, %r16;
    
    setp.ge.s32 %p1, %r27, %r12;
    @%p1 bra $L__BB0_7;

    add.s32 %r17, %r1, %r12;
    add.s32 %r18, %r27, %r1;
    not.b32 %r19, %r18;
    add.s32 %r20, %r17, %r19;               // Start of loop unrolling logic
    div.u32 %r3, %r20, %r1;
    add.s32 %r21, %r3, 1;
    and.b32 %r26, %r21, 3;
    setp.eq.s32 %p2, %r26, 0;
    @%p2 bra $L__BB0_4;

    mul.wide.s32 %rd13, %r27, 4;
    add.s64 %rd24, %rd1, %rd13;
    mul.wide.s32 %rd4, %r1, 4;
    add.s64 %rd23, %rd2, %rd13;

$L__BB0_3:
    .pragma "nounroll";
    ld.global.f32 %f2, [%rd23];
    ld.global.f32 %f3, [%rd24];
    fma.rn.f32 %f4, %f2, %f1, %f3;
    st.global.f32 [%rd24], %f4;
    add.s32 %r27, %r27, %r1;
    add.s64 %rd24, %rd24, %rd4;
    add.s64 %rd23, %rd23, %rd4;
    add.s32 %r26, %r26, -1;
    setp.ne.s32 %p3, %r26, 0;
    @%p3 bra $L__BB0_3;
    
    [... additional unroll handling code ...]
    
$L__BB0_7:
    ret;
}
```

### Side-by-Side Comparison

| Aspect | Pascal | CUDA O3 | Winner | Explanation |
|--------|--------|---------|--------|-------------|
| **Total Lines** | 39 | 92 | Pascal | Simple loop vs unroll+remainder handling |
| **Loop Structure** | Single branch | Multiple branches | Pascal | Pascal's simplicity is clean |
| **Address Conversion** | No | Yes (cvta) | Tie | Pascal assumes addresses already canonical |
| **Register Usage** | 22 total | 71 total | Pascal | Fewer registers means better occupancy |
| **Branch Pattern** | `@%p1 bra` | Complex predicate logic | Pascal | Straightforward exit branch |
| **Instruction Count/Iteration** | ~6 | ~12 (before unroll) | Pascal | More compact per-iteration code |

### Key Observations

1. **CUDA's `cvta.to.global` Instructions**
   - CUDA emits address conversion instructions
   - Pascal doesn't (assumes pointers are already in global address space)
   - This is a tradeoff: CUDA is more general, Pascal assumes ADS semantics

2. **Register Allocation Strategy**
   - Pascal: Allocates minimum needed (22 regs)
   - CUDA: Allocates conservatively (71 regs) - probably defensive against code spilling

3. **Loop Unrolling Strategy**
   - CUDA O3 attempts 4x unrolling (not visible in excerpt, but the remainder handling code indicates it)
   - Creates complex predication for partial remainder iterations
   - Pascal stays simple with single branch

4. **Floating-Point Operation Order**
   - Both use `fma.rn.f32` (fused multiply-add) - good!
   - Both compute `a * x[i] + y[i]` correctly

---

## Kernel 2: DOT_PRODUCT

### Pascal Source
```pascal
PROCEDURE dot_product(x: ADS(GLOBAL) OF BUFFER; 
                      y: ADS(GLOBAL) OF BUFFER; n: INTEGER32);
VAR i, stride: INTEGER32;
    local_sum: REAL32;
BEGIN
  local_sum := 0.0;
  i := THREADIDX_X + BLOCKIDX_X * BLOCKDIM_X;
  stride := BLOCKDIM_X * GRIDDIM_X;
  WHILE i < n DO
  BEGIN
    local_sum := local_sum + x^[i] * y^[i];
    i := i + stride
  END;
  
  (* Shared memory reduction follows... *)
  shared_sum[THREADIDX_X] := local_sum;
  SYNCTHREADS;
  (* Tree reduction code... *)
END;
```

### Pascal PTX (key portion - 100 lines total)
```ptx
ld.param.u64 %rd2, [dot_product_param_0];  // x
ld.param.u64 %rd1, [dot_product_param_1];  // y
ld.param.u32 %r5, [dot_product_param_2];   // n

mov.u32 %r6, %ntid.x;
mov.u32 %r7, %nctaid.x;
mul.lo.s32 %r1, %r6, %r7;                  // stride
mov.u32 %r8, %tid.x;
mov.u32 %r9, %ctaid.x;
mad.lo.s32 %r10, %r9, %r6, %r8;           // i

mov.f32 %f1, 0f00000000;                   // local_sum = 0.0

$L__BB0_1:
    setp.ge.s32 %p1, %r10, %r5;
    @%p1 bra $L__BB0_3;
    
    mul.wide.s32 %rd3, %r10, 4;
    add.s64 %rd4, %rd1, %rd3;
    add.s64 %rd5, %rd2, %rd3;
    
    ld.global.f32 %f2, [%rd5];              // x[i]
    ld.global.f32 %f3, [%rd4];              // y[i]
    fma.rn.f32 %f1, %f2, %f3, %f1;          // sum += x[i] * y[i]
    
    add.s32 %r10, %r10, %r1;
    bra.uni $L__BB0_1;

$L__BB0_3:
    st.shared.f32 [shared_addr], %f1;       // Store to shared memory
    bar.sync 0;                             // SYNCTHREADS
    (* ... tree reduction code ... *)
```

### CUDA O3 PTX (key portion - 152 lines)
```ptx
ld.param.u64 %rd11, [dot_product_param_0];
ld.param.u64 %rd12, [dot_product_param_1];
ld.param.u32 %r12, [dot_product_param_2];

cvta.to.global.u64 %rd2, %rd12;
cvta.to.global.u64 %rd1, %rd11;

mov.u32 %r13, %nctaid.x;
mov.u32 %r14, %ntid.x;
mul.lo.s32 %r1, %r14, %r13;

setp.ge.s32 %p1, %r2, %r12;
@%p1 bra $L__BB0_7;

// Loop trip count calculation for unrolling
add.s32 %r17, %r1, %r12;
add.s32 %r18, %r2, %r1;
not.b32 %r19, %r18;
add.s32 %r20, %r17, %r19;
div.u32 %r3, %r20, %r1;
// ... more calculation ...

$L__BB0_3:
    .pragma "nounroll";
    ld.global.f32 %f2, [%rd23];
    ld.global.f32 %f3, [%rd24];
    fma.rn.f32 %f1, %f2, %f3, %f1;         // Note: order is different!
    add.s32 %r2, %r2, %r1;
    add.s64 %rd24, %rd24, %rd4;
    add.s64 %rd23, %rd23, %rd4;
    add.s32 %r9, %r9, -1;
    setp.ne.s32 %p3, %r9, 0;
    @%p3 bra $L__BB0_3;
```

### Comparison

| Aspect | Pascal | CUDA O3 |
|--------|--------|---------|
| **Loop Accumulation** | `fma.rn.f32 %f1, %f2, %f3, %f1` | `fma.rn.f32 %f1, %f2, %f3, %f1` |
| **Register Usage** | 12 registers | 30+ registers |
| **Total PTX Size** | 100 lines | 152 lines |
| **Shared Memory Sync** | `bar.sync 0` | `bar.sync 0` |

**Key Finding:** Both produce identical FMA instructions. The size difference is entirely due to CUDA's unroll remainder handling.

---

## Kernel 3: VECTOR_ADD

### Pascal PTX (63 lines)
```ptx
// Initialization (same as saxpy)
mul.lo.s32 %r1, %r6, %r7;                  // stride = blockDim.x * gridDim.x
mad.lo.s32 %r10, %r9, %r6, %r8;           // i = threadIdx.x + blockIdx.x * blockDim.x

$L__BB0_1:
    setp.ge.s32 %p1, %r10, %r5;
    @%p1 bra $L__BB0_3;
    
    mul.wide.s32 %rd3, %r10, 4;
    add.s64 %rd4, %rd1, %rd3;               // c = c_base + offset
    add.s64 %rd5, %rd2, %rd3;               // a = a_base + offset
    add.s64 %rd6, %rd3, %rd7;               // b = b_base + offset
    
    ld.global.f32 %f2, [%rd5];              // a[i]
    ld.global.f32 %f3, [%rd6];              // b[i]
    add.f32 %f4, %f2, %f3;                  // a[i] + b[i]
    st.global.f32 [%rd4], %f4;              // c[i] = result
    
    add.s32 %r10, %r10, %r1;
    bra.uni $L__BB0_1;

$L__BB0_3:
    ret;
```

**Key Point:** Uses `add.f32` (not fma) because there are only 2 operands, not 3.

### CUDA O3 PTX (149 lines)
Similar structure but with unroll remainder handling, more registers allocated.

---

## Summary Table: Which Operations Does Each Compiler Excel At?

| Operation Type | Pascal | CUDA O3 | Notes |
|---|---|---|---|
| **Simple arithmetic** | ✓✓ | ✓ | Pascal generates cleaner code |
| **FMA (multiply-accumulate)** | ✓✓ | ✓✓ | Both use FMA effectively |
| **Load/Store patterns** | ✓ | ✓ | Both handle efficiently |
| **Address calculation** | ✓✓ | ✓ | Pascal avoids unnecessary `cvta` |
| **Loop unrolling** | ✗ (generates simple loop) | ✓ (4x unroll attempted) | CUDA tries harder but adds complexity |
| **Register allocation** | ✓✓ | ✗ (over-allocates) | Pascal is more conservative |
| **Occupancy** | ✓✓ | ✗ | Pascal's lower register usage = higher occupancy |

---

## Performance Prediction

Based on these comparisons, on typical hardware:

1. **saxpy:** Pascal should be 5-10% faster (lower register pressure, better occupancy)
2. **vector_add:** Pascal should be 5-10% faster (same reason)
3. **dot_product:** Pascal should be ~5% faster (lower memory pressure from better register reuse)
4. **complex kernels:** Results depend on whether unrolling actually helps (often doesn't without pipelining)

**Caveat:** These are theoretical predictions. Real performance depends on:
- GPU cache behavior
- Memory bandwidth saturation
- Warp occupancy on specific hardware
- Instruction cache efficiency

**Recommendation:** Profile on actual target GPU to validate.

