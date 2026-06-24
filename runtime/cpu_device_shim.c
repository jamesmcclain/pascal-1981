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

/* Launch a kernel through a CUDA-driver-shaped three-step path.  The compiler
 * emits, per host compiland, a kernel registry: parallel name/entry tables it
 * fills with the launchable kernels and their per-kernel dispatch thunks.  The
 * launch site then does:
 *
 *     module = pas_dev_module_load(registry, ptx);     // cuModuleLoadData
 *     entry  = pas_dev_module_get_function(module, name); // cuModuleGetFunction
 *     pas_dev_launch(entry, gx,gy,gz, bx,by,bz, argv);  // cuLaunchKernel
 *
 * On the CPU device the "module" is the registry, get_function is a by-name
 * lookup returning the thunk, and launch invokes the thunk as a single-thread
 * grid (so a grid-stride kernel still covers the whole buffer).  Swapping this
 * file for CUDA Driver API wrappers turns the *same* compiler output into a real
 * GPU launch with no Pascal-side change: load takes the embedded PTX blob,
 * get_function returns a CUfunction, launch is cuLaunchKernel.  (A CUDA shim
 * should cache the loaded module internally -- e.g. a static handle keyed on the
 * registry/ptx pointer -- so the per-launch load call stays cheap.) */

/* Must match the LLVM struct the compiler emits: { i8** names; i8** entries;
 * i64 count }. */
typedef struct {
    const char *const *names;
    void *const *entries;
    long long count;
} pas_dev_registry;

/* Load a "module".  CPU device: the module *is* the compiler-emitted registry;
 * the PTX blob is unused here (the CUDA shim cuModuleLoadData's it instead and
 * ignores the registry). */
void *pas_dev_module_load(void *registry, const char *ptx) {
    (void)ptx;
    return registry;
}

/* Resolve a kernel entry by name out of a loaded module.  CPU device: linear
 * search of the registry's name table, returning the matching dispatch thunk
 * (or NULL if absent). */
void *pas_dev_module_get_function(void *module, const char *name) {
    const pas_dev_registry *r = (const pas_dev_registry *)module;
    long long i;
    if (!r || !name)
        return 0;
    for (i = 0; i < r->count; i++)
        if (r->names[i] && strcmp(r->names[i], name) == 0)
            return r->entries[i];
    return 0;
}

/* Launch a resolved entry.  CPU device: the entry is the dispatch thunk; call
 * it once with the marshalled argument array.  Geometry is unused on the CPU
 * device (BLOCKDIM_X/GRIDDIM_X lower to 1, so a single-thread grid is correct);
 * it carries the same six values cuLaunchKernel consumes. */
typedef void (*pas_klaunch_fn)(void **);
void pas_dev_launch(void *entry,
                    long long gx, long long gy, long long gz,
                    long long bx, long long by, long long bz,
                    void **argv) {
    (void)gx; (void)gy; (void)gz;
    (void)bx; (void)by; (void)bz;
    if (entry)
        ((pas_klaunch_fn)entry)(argv);
}
