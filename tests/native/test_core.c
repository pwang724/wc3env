/* Pure core regressions: no game, injection, or Windows desktop needed. */
#define _CRT_SECURE_NO_WARNINGS
#include <assert.h>
#include "../../wc3hook/json.h"
#include "../../wc3hook/clock_math.h"
#include "../../wc3hook/allocator_bins.h"

static int valid(const char *s) {
    yyjson_doc *doc = jr_read(s);
    int ok = doc != NULL;
    yyjson_doc_free(doc);
    return ok;
}
int main(void) {
    /* A nonempty bin of 32-byte holes must not hide a usable 512-byte block. */
    ArenaFreeBlock small = {32, 0, 2, NULL}, large_block = {512, 0, 2, NULL};
    ArenaFreeBlock *bins[9] = {0};
    bins[1] = &small;
    bins[8] = &large_block;
    assert(arena_find_free(bins, 40) == &bins[8]);
    assert(arena_find_free(bins, 32) == &bins[1]);
    assert(arena_find_free(bins, 520) == NULL);
    ArenaFreeBlock fitting = {48, 0, 2, NULL};
    small.next = &fitting;
    assert(arena_find_free(bins, 40) == &small.next);
    assert(bins[1] == &small && bins[8] == &large_block); /* search does not mutate lists */
    memset(bins, 0, sizeof bins);
    assert(arena_find_free(bins, 16) == NULL);
    assert(valid("{\"a\":[1,-2.25e+3,true,null],\"b\":{\"x\":\"\\ud83d\\ude00\"}}"));
    const char *bad[] = {"{\"a\":1 \"b\":2}",
                         "{\"a\",1}",
                         "[1,]",
                         "{\"a\":1,}",
                         "[1 2]",
                         "{\"a\":NaN}",
                         "[01]",
                         "[-]",
                         "[1e]",
                         "[1.]",
                         "[1e999]",
                         "{}{}",
                         "{\"a\":\"\\ud800\"}",
                         "{\"a\":\"raw\nnewline\"}",
                         "[\"\xc0\xaf\"]",
                         "[/* comment */1]",
                         "[+1]",
                         "[.5]",
                         "{a:1}",
                         "[true false]"};
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        if (valid(bad[i])) {
            fprintf(stderr, "accepted invalid JSON: %s\n", bad[i]);
            return 1;
        }
    }
    const char *s = "{\"x\":9223372036854775808,\"y\":-9223372036854775808,\"z\":4294967296,"
                    "\"float\":1.0,\"exp\":1e0,\"max\":9223372036854775807,\"zero\":0,\"negative\":-1,"
                    "\"nul\":\"a\\u0000b\",\"unicode\":\"\\ud83d\\ude00\",\"fourcc\":\"hfoo\"}";
    yyjson_doc *doc = jr_read(s);
    assert(doc);
    yyjson_val *root = yyjson_doc_get_root(doc);
    long long v;
    assert(!jr_is_int(yyjson_obj_get(root, "x"), &v));
    assert(jr_is_int(yyjson_obj_get(root, "y"), &v) && v == LLONG_MIN);
    assert(jr_is_int(yyjson_obj_get(root, "z"), &v) && v == 4294967296LL);
    assert(jr_is_int(yyjson_obj_get(root, "max"), &v) && v == LLONG_MAX);
    assert(jr_is_int(yyjson_obj_get(root, "zero"), &v) && v == 0);
    assert(jr_is_int(yyjson_obj_get(root, "negative"), &v) && v == -1);
    assert(!jr_is_int(yyjson_obj_get(root, "float"), &v));
    assert(!jr_is_int(yyjson_obj_get(root, "exp"), &v));
    assert(!jr_is_int(NULL, &v));
    char buf[5];
    assert(!jr_str(yyjson_obj_get(root, "nul"), buf, sizeof buf));
    assert(jr_str(yyjson_obj_get(root, "unicode"), buf, sizeof buf) && !strcmp(buf, "\xf0\x9f\x98\x80"));
    assert(!jr_str(yyjson_obj_get(root, "unicode"), buf, 4));
    assert(jr_fourcc(yyjson_obj_get(root, "fourcc")) == 0x68666f6f);
    yyjson_doc_free(doc);
    /* Depth, node count, and byte budgets are independent. */
    char deep[132];
    for (int depth = 64; depth <= 65; depth++) {
        memset(deep, '[', depth);
        deep[depth] = '0';
        memset(deep + depth + 1, ']', depth);
        deep[2 * depth + 1] = 0;
        assert(valid(deep) == (depth == 64));
    }
    char many[8194];
    many[0] = '[';
    for (int k = 0; k < 4096; k++) {
        many[1 + 2 * k] = '0';
        many[2 + 2 * k] = ',';
    }
    many[8192] = ']';
    many[8193] = 0;
    assert(!valid(many));
    many[8190] = ']';
    many[8191] = 0;
    assert(valid(many));
    char *large = (char *)malloc(65536);
    assert(large);
    memset(large, ' ', 65535);
    large[0] = '[';
    large[1] = ']';
    large[65535] = 0;
    assert(!valid(large));
    large[65534] = 0;
    assert(valid(large));
    free(large);
    /* Previous intermediate multiplication overflowed after 25.6 virtual hours at 10MHz. */
    assert(clock_units(10000000LL * 86400 * 365, 10000000, 10000000) == 10000000LL * 86400 * 365);
    assert(clock_units(1234567890123LL, 10000000, 1000) == 123456789);
    assert(clock_units(-1234567890123LL, 10000000, 1000) == -123456789);
    JW w = {0};
    jw_open(&w, '[');
    jw_num(&w, 1e300);
    jw_close(&w, ']');
    assert(valid(w.p));
    free(w.p);
    puts("Native JSON, clock and allocator regressions passed.");
    return 0;
}
