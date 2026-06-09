#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

int32_t encode_value(char *dest_chars, int32_t dest_len, char *dest_raw, int32_t v1, int32_t v2, int32_t v3, int32_t v4) {
    (void)dest_raw; (void)v2; (void)v3; (void)v4;
    if (!dest_chars || dest_len <= 0) return 0;
    int n = snprintf(dest_chars, (size_t)dest_len + 1, "%d", v1);
    return n >= 0 && n <= dest_len;
}

int32_t decode_value(char *src_chars, int32_t src_len, char *dest_raw, int32_t v2, int32_t v3, int32_t v4, int32_t v5) {
    (void)dest_raw; (void)v2; (void)v3; (void)v4; (void)v5;
    if (!src_chars) return 0;
    char buf[512];
    if (src_len > 511) src_len = 511;
    memcpy(buf, src_chars, (size_t)src_len);
    buf[src_len] = '\0';
    char *end = NULL;
    strtol(buf, &end, 10);
    return end && *end == '\0';
}
