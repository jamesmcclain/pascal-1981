/*
 * Runtime support for MOVESL.
 *
 * The short-count move variants share MOVEL/MOVER's left/right direction
 * convention: MOVESL moves starting at the LEFT end (forward). Only the
 * direction defect is fixed here (previously this was an overlap-erasing
 * memmove identical to every other move); the full "short count" length
 * semantics await reconciliation with the manual, so the explicit caller-
 * supplied length is used as-is.
 */

int movesl(char *src, char *dst, unsigned short len) {
    unsigned short i;
    for (i = 0; i < len; i++) {
        dst[i] = src[i];
    }
    return 0;
}
