/* Strict request parsing through yyjson; bounded native accessors and a streaming writer. */
#pragma once
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <limits.h>
#include "yyjson/yyjson.h"

/* The transport bounds bytes; keep the same bound for direct callers and tests.
 * Nesting is bounded by YYJSON_READER_DEPTH_LIMIT in build.bat. */
static yyjson_doc *jr_read(const char *line) {
    size_t len = strlen(line);
    if (len >= 65535)
        return NULL;
    yyjson_doc *doc = yyjson_read(line, len, 0);
    if (doc && yyjson_doc_get_val_count(doc) > 4096) {
        yyjson_doc_free(doc);
        return NULL;
    }
    return doc;
}
static int jr_is_int(yyjson_val *v, long long *out) {
    if (yyjson_is_sint(v)) {
        *out = yyjson_get_sint(v);
        return 1;
    }
    if (yyjson_is_uint(v) && yyjson_get_uint(v) <= LLONG_MAX) {
        *out = (long long)yyjson_get_uint(v);
        return 1;
    }
    return 0;
}
static int jr_is_num(yyjson_val *v, double *out) {
    if (!yyjson_is_num(v))
        return 0;
    *out = yyjson_get_num(v);
    return 1;
}
static int jr_str(yyjson_val *v, char *out, int cap) {
    if (cap <= 0)
        return 0;
    out[0] = 0;
    const char *s = yyjson_get_str(v);
    size_t len = yyjson_get_len(v);
    /* Native arguments use C strings: never truncate or accept embedded NUL. */
    if (!s || len >= (size_t)cap || memchr(s, 0, len))
        return 0;
    memcpy(out, s, len + 1);
    return 1;
}
static unsigned jr_fourcc(yyjson_val *v) {
    char s[5];
    if (!jr_str(v, s, sizeof s) || strlen(s) != 4)
        return 0;
    return ((unsigned)(unsigned char)s[0] << 24) | ((unsigned)(unsigned char)s[1] << 16) |
           ((unsigned)(unsigned char)s[2] << 8) | (unsigned char)s[3];
}

/* ---- writer ---------------------------------------------------------------------------------- */
typedef struct {
    char *p;
    size_t n, cap;
    int comma;
} JW;

static void jw_grow(JW *w, size_t need) {
    if (w->n + need + 1 <= w->cap)
        return;
    size_t cap = w->cap ? w->cap * 2 : 1 << 16;
    while (cap < w->n + need + 1)
        cap *= 2;
    char *p = (char *)realloc(w->p, cap);
    if (!p)
        abort(); /* never continue with a partial observation after allocation failure */
    w->p = p;
    w->cap = cap;
}
static void jw_reset(JW *w) {
    jw_grow(w, 0);
    w->n = 0;
    w->comma = 0;
    w->p[0] = 0;
}
static void jw_raw(JW *w, const char *s) {
    size_t k = strlen(s);
    jw_grow(w, k);
    memcpy(w->p + w->n, s, k);
    w->n += k;
    w->p[w->n] = 0;
}
static void jw_sep(JW *w) {
    if (w->comma)
        jw_raw(w, ",");
    w->comma = 1;
}
static void jw_str(JW *w, const char *s) {
    jw_grow(w, strlen(s) * 6 + 2);
    w->p[w->n++] = '"';
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') {
            w->p[w->n++] = '\\';
            w->p[w->n++] = c;
        } else if (c < 0x20)
            w->n += sprintf(w->p + w->n, "\\u%04x", c);
        else
            w->p[w->n++] = c;
    }
    w->p[w->n++] = '"';
    w->p[w->n] = 0;
}
static void jw_key(JW *w, const char *k) {
    jw_sep(w);
    jw_str(w, k);
    jw_raw(w, ":");
    w->comma = 0;
}
static void jw_int(JW *w, long long v) {
    char b[32];
    jw_sep(w);
    _snprintf(b, sizeof b, "%I64d", v);
    jw_raw(w, b);
}
static void jw_uint(JW *w, unsigned v) {
    char b[32];
    jw_sep(w);
    _snprintf(b, sizeof b, "%u", v);
    jw_raw(w, b);
}
static void jw_num(JW *w, double v) {
    char b[64];
    jw_sep(w);
    if (!_finite(v)) {
        jw_raw(w, "null");
        return;
    }
    /* Keep ordinary observations readable without overflowing on large engine values. */
    _snprintf(b, sizeof b, fabs(v) < 1e20 ? "%.3f" : "%.17g", v);
    jw_raw(w, b);
}
static void jw_bool(JW *w, int v) {
    jw_sep(w);
    jw_raw(w, v ? "true" : "false");
}
static void jw_null(JW *w) {
    jw_sep(w);
    jw_raw(w, "null");
}
static void jw_string(JW *w, const char *s) {
    jw_sep(w);
    jw_str(w, s);
}
static void jw_open(JW *w, char c) {
    char b[2] = {c, 0};
    jw_sep(w);
    jw_raw(w, b);
    w->comma = 0;
}
static void jw_close(JW *w, char c) {
    char b[2] = {c, 0};
    jw_raw(w, b);
    w->comma = 1;
}
static void jw_fourcc(JW *w, unsigned t) {
    char b[5] = {(char)(t >> 24), (char)(t >> 16), (char)(t >> 8), (char)t, 0};
    jw_string(w, b);
}
/* splice another writer's content (already-formed JSON values) as the next value */
static void jw_splice(JW *w, const JW *v) {
    if (!v->p || !v->n)
        return;
    jw_sep(w);
    jw_raw(w, v->p);
}
