/*
 * Runtime support for FILLSC.
 *
 * This is the segmented-address sibling of fillc; in this codebase we model
 * it with the same host representation and behavior as fillc.
 */

#include <string.h>

int fillsc(char *loc, unsigned short len, char val) {
    memset(loc, (unsigned char)val, len);
    return 0;
}
