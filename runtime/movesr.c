/* Runtime support for MOVESR. */

#include <string.h>

int movesr(char *src, char *dst, unsigned short len) {
    memmove(dst, src, len);
    return 0;
}
