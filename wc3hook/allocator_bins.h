/* Native free blocks are grouped by size / 32, with all sizes >= 256 in bin 8. */
#pragma once
#include <stddef.h>

typedef struct ArenaFreeBlock {
    unsigned short size;
    unsigned char padding, flags;
    struct ArenaFreeBlock *next;
} ArenaFreeBlock;

static ArenaFreeBlock **arena_find_free(ArenaFreeBlock **bins, unsigned need) {
    unsigned first = need >> 5;
    if (first > 8)
        first = 8;
    for (unsigned bin = first; bin < 9; bin++) {
        ArenaFreeBlock **best = NULL;
        for (ArenaFreeBlock **link = &bins[bin]; *link; link = &(*link)->next) {
            if ((*link)->size >= need && (!best || (*link)->size < (*best)->size)) {
                best = link;
                if ((*link)->size - need < 16)
                    break; /* native minimum split size */
            }
        }
        if (best)
            return best;
    }
    return NULL;
}
