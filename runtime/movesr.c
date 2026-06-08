/*
 * Runtime support for MOVESR.
 *
 * Short-count sibling of MOVESL that moves starting at the RIGHT end
 * (backward), mirroring MOVER. As with movesl, only the direction defect is
 * fixed here; the explicit caller-supplied length is used as-is pending the
 * manual's full short-count semantics.
 */

int movesr(char *src, char *dst, unsigned short len) {
    unsigned short i;
    for (i = len; i > 0; i--) {
        dst[i - 1] = src[i - 1];
    }
    return 0;
}
