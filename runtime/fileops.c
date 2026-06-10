#include <stdio.h>
#include <stdlib.h>

/* Must match codegen/base.py and codegen/files.py:
 * { i32 elem_size, i32 structure, i32 touched, i32 mode, i8* buffer, i8* handle }
 * mode low bits: 0 = closed/unopened, 1 = inspection/read, 2 = generation/write
 * mode bit 2 (0x4): eof recorded for future EOF/EOLN support.
 */
#define MODE_CLOSED 0
#define MODE_READ   1
#define MODE_WRITE  2
#define MODE_BITS   3
#define MODE_EOF    4

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

static size_t elem_size(struct pas_file_fcb *f) {
    return (size_t)(f->elem_size > 0 ? f->elem_size : 1);
}

static int current_mode(struct pas_file_fcb *f) {
    return f->mode & MODE_BITS;
}

static void set_mode(struct pas_file_fcb *f, int mode, int eof) {
    f->mode = mode | (eof ? MODE_EOF : 0);
}

static int is_eof(struct pas_file_fcb *f) {
    return (f->mode & MODE_EOF) != 0;
}

static FILE *ensure_handle(struct pas_file_fcb *f) {
    if (!f) die("file runtime: null fcb");
    if (f->handle) return f->handle;
    f->handle = tmpfile();
    if (!f->handle) die("file runtime: tmpfile failed");
    return f->handle;
}

static void checked_buffer(struct pas_file_fcb *f) {
    if (!f || !f->buffer) die("file runtime: null buffer");
}

void *pas_file_buffer(struct pas_file_fcb *f) {
    return f ? f->buffer : NULL;
}

void pas_file_touch_buffer(struct pas_file_fcb *f) {
    if (f) f->touched = 1;
}

void pas_file_get(struct pas_file_fcb *f) {
    FILE *h = ensure_handle(f);
    checked_buffer(f);
    if (current_mode(f) != MODE_READ) die("file runtime: GET requires RESET/read mode");
    if (is_eof(f)) die("file runtime: GET past eof");

    size_t n = f->structure == 1 ? 1 : elem_size(f);
    size_t got = fread(f->buffer, 1, n, h);
    if (got != n) {
        if (feof(h)) {
            set_mode(f, MODE_READ, 1);
            f->touched = 0;
            return;
        }
        die("file runtime: read failed");
    }
    set_mode(f, MODE_READ, 0);
    f->touched = 0;
}

void pas_file_reset(struct pas_file_fcb *f) {
    FILE *h = ensure_handle(f);
    if (current_mode(f) == MODE_WRITE && fflush(h) != 0) die("file runtime: flush failed");
    rewind(h);
    set_mode(f, MODE_READ, 0);
    f->touched = 0;
    pas_file_get(f); /* manual-required implicit first GET */
}

void pas_file_rewrite(struct pas_file_fcb *f) {
    if (!f) die("file runtime: null fcb");
    if (f->handle) {
        fclose(f->handle);
    }
    f->handle = tmpfile();
    if (!f->handle) die("file runtime: tmpfile failed");
    set_mode(f, MODE_WRITE, 1);
    f->touched = 0;
}

void pas_file_put(struct pas_file_fcb *f) {
    FILE *h = ensure_handle(f);
    checked_buffer(f);
    if (current_mode(f) != MODE_WRITE) die("file runtime: PUT requires REWRITE/write mode");

    size_t n = f->structure == 1 ? 1 : elem_size(f);
    if (fwrite(f->buffer, 1, n, h) != n) die("file runtime: write failed");
    set_mode(f, MODE_WRITE, 1);
    f->touched = 0;
}
