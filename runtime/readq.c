#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static int skip_ws_except_nl(void) {
    int ch;
    while ((ch = getchar()) != EOF) {
        if (ch == '\n' || !isspace((unsigned char)ch)) return ch;
    }
    return EOF;
}

static void unread(int ch) {
    if (ch != EOF) ungetc(ch, stdin);
}

static void die(const char *msg) {
    fprintf(stderr, "runtime error: %s\n", msg);
    abort();
}

int pas_read_int(int32_t *out) {
    int ch = skip_ws_except_nl();
    if (ch == EOF) die("unexpected EOF while reading integer");
    unread(ch);
    long v;
    if (scanf("%ld", &v) != 1) die("malformed integer input");
    *out = (int32_t)v;
    return 0;
}

int pas_read_word(uint16_t *out) {
    int32_t v = 0;
    if (pas_read_int(&v) != 0) return -1;
    if (v < 0 || v > 65535) die("word out of range");
    *out = (uint16_t)v;
    return 0;
}

int pas_read_real(double *out) {
    int ch = skip_ws_except_nl();
    if (ch == EOF) die("unexpected EOF while reading real");
    unread(ch);
    if (scanf("%lf", out) != 1) die("malformed real input");
    return 0;
}

int pas_read_char(uint8_t *out) {
    int ch = getchar();
    if (ch == EOF) die("unexpected EOF while reading char");
    *out = (ch == '\n') ? ' ' : (uint8_t)ch;
    return 0;
}

int pas_read_lstring(uint8_t *buf, int cap) {
    int ch;
    int n = 0;
    while ((ch = getchar()) != EOF && ch != '\n') {
        if (n < cap) buf[1 + n] = (uint8_t)ch;
        n++;
    }
    if (ch == EOF) die("unexpected EOF while reading string");
    if (cap < 0) cap = 0;
    buf[0] = (uint8_t)(n < cap ? n : cap);
    return 0;
}

void pas_readln_skip(void) {
    int ch;
    while ((ch = getchar()) != EOF && ch != '\n') {}
}
