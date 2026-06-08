/* Runtime support for MOVER. */

#include <string.h>

int mover(char *src, char *dst, unsigned short len) {
    memmove(dst, src, len);
    return 0;
}
