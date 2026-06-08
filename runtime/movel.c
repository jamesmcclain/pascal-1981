/* Runtime support for MOVEL. */

#include <string.h>

int movel(char *src, char *dst, unsigned short len) {
    memmove(dst, src, len);
    return 0;
}
