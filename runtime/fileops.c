#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct pas_file_fcb {
    int elem_size;
    int structure;
    int touched;
    int mode;
    void *buffer;
    FILE *handle;
};

static void die(const char *msg) {
    fprintf(stderr, "%s\n", msg);
    abort();
}

static FILE *ensure_handle(struct pas_file_fcb *f) {
    if (f->handle) return f->handle;
    f->handle = tmpfile();
    if (!f->handle) die("file runtime: tmpfile failed");
    return f->handle;
}

void *pas_file_buffer(struct pas_file_fcb *f) {
    return f ? f->buffer : NULL;
}

void pas_file_touch_buffer(struct pas_file_fcb *f) {
    if (f) f->touched = 1;
}

void pas_file_reset(struct pas_file_fcb *f) {
    f->mode = 1;
    f->touched = 0;
}

void pas_file_rewrite(struct pas_file_fcb *f) {
    f->mode = 2;
    f->touched = 0;
}

void pas_file_get(struct pas_file_fcb *f) {
    (void)f;
}

void pas_file_put(struct pas_file_fcb *f) {
    (void)f;
}
