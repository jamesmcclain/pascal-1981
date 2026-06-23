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
