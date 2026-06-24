/* CPU-device orchestration shim (Milestone D, cuda-kernel-prescription.md §7).
 *
 * The "device" is the host CPU: device memory is ordinary heap memory, the
 * host->device and device->host copies are plain memcpy, and a kernel launch
 * is a direct call emitted by the compiler (so there is no launch entry here).
 * This lets a host Pascal program exercise the full allocate / copy / launch /
 * copy-back surface with zero GPU, as the fast correctness loop. Swapping these
 * four functions for CUDA Driver API wrappers (cuMemAlloc / cuMemcpyHtoD /
 * cuMemcpyDtoH / cuMemFree) turns the same Pascal program into a real GPU run
 * without touching the Pascal side (Strategy 1, §5.2).
 *
 * The Pascal builtins lower to these symbols:
 *   DEVALLOC(n)            -> pas_dev_alloc(n)
 *   DEVCOPYTO(dev,src,n)   -> pas_dev_copy_to(dev, src, n)   (H2D)
 *   DEVCOPYFROM(dst,dev,n) -> pas_dev_copy_from(dst, dev, n) (D2H)
 *   DEVFREE(dev)           -> pas_dev_free(dev)
 */

#include <stdlib.h>
#include <string.h>

/* Allocate n bytes of "device" memory; returns an opaque handle the host must
 * not dereference (the dereferenceability invariant). On the CPU device the
 * handle happens to be a real heap pointer, but Pascal code only ever hands it
 * back to the copy/launch/free builtins. */
void *pas_dev_alloc(long long nbytes) {
    if (nbytes <= 0)
        return NULL;
    return malloc((size_t)nbytes);
}

/* Host -> device copy. */
void pas_dev_copy_to(void *dev_dst, const void *host_src, long long nbytes) {
    if (dev_dst && host_src && nbytes > 0)
        memcpy(dev_dst, host_src, (size_t)nbytes);
}

/* Device -> host copy. */
void pas_dev_copy_from(void *host_dst, const void *dev_src, long long nbytes) {
    if (host_dst && dev_src && nbytes > 0)
        memcpy(host_dst, dev_src, (size_t)nbytes);
}

/* Free a handle returned by pas_dev_alloc. */
void pas_dev_free(void *dev_ptr) {
    free(dev_ptr);
}

/* Launch a kernel.  The compiler hands us, for every LAUNCH, a GPU-faithful
 * argument bundle: the kernel name (used by the CUDA shim to find the entry in
 * the loaded module), a per-kernel host dispatch thunk (used by *this* CPU-device
 * shim), the six cuLaunchKernel geometry values, and a void** argument array
 * whose i-th slot points at the storage cell holding kernel argument i.
 *
 * On the CPU device the kernel runs as a single-thread grid -- we invoke the
 * thunk once, which unpacks argv and calls the kernel.  A grid-stride kernel
 * (i := tid + bid*bdim; i += bdim*gdim) therefore still covers the whole buffer
 * because BLOCKDIM_X/GRIDDIM_X lower to 1 on the CPU device.  The geometry and
 * name are unused here; they carry the same information the CUDA driver shim
 * will consume when it replaces this function with a cuLaunchKernel by name
 * out of a cuModuleLoadData'd PTX module -- with no change to the Pascal side. */
typedef void (*pas_klaunch_fn)(void **);
void pas_dev_launch(const char *name, void *thunk,
                    long long gx, long long gy, long long gz,
                    long long bx, long long by, long long bz,
                    void **argv) {
    (void)name;
    (void)gx; (void)gy; (void)gz;
    (void)bx; (void)by; (void)bz;
    if (thunk)
        ((pas_klaunch_fn)thunk)(argv);
}
