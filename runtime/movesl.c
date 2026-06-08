/* Runtime support for MOVESL. */

#include <string.h>

int movesl(char *src, char *dst, unsigned short len) {
    memmove(dst, src, len);
    return 0;
}
