/* Warcraft's native placement search, without an AI player or AI script.
 * Call orchestration follows 1.29.2's 0x6d4970. The engine constructs the local
 * pathing search, scores sites, checks the worker, and supplies the footprint.
 * Only scratch memory lives here; nothing changes player/controller state. */
#include "wc3hook.h"
#include "footprints_table.h"

typedef void *(__fastcall *SearchInitFn)(BYTE *, void *, int, float *, float *, unsigned, int, BYTE *);
typedef void *(__fastcall *SearchCopyFn)(BYTE *, void *, BYTE *);
typedef void *(__fastcall *SearchRunFn)(BYTE *, void *, float *, float *, int);
typedef void(__cdecl *SearchEnumFn)(void *, BYTE *, int, DWORD, DWORD, DWORD, int, int);
typedef int(__fastcall *PlayerCountFn)(BYTE *, void *, unsigned, int);
typedef int(__fastcall *PendingFn)(BYTE *, void *, unsigned);

typedef struct {
    BYTE *player;
    unsigned type;
    int count;
} PendingCount;

/* Same pending-production count as 0x6c0ad0, enumerated without the AI object's unit list. */
static int __cdecl count_pending(BYTE *u, void *ctx) {
    PendingCount *c = ctx;
    if (unit_owner(u) == c->player && !(*(DWORD *)(u + 0x20) & 1) && *(void **)(u + 0x400) &&
        *(DWORD *)(u + 0x3a0) == 2 && NATIVE(RVA_UNIT_PENDING_TYPE, PendingFn)(u, NULL, c->type))
        c->count++;
    return 1;
}

static int search_once(BYTE *worker, unsigned type, float *x, float *y) {
    /* The AI caller checks these engine predicates before entering the search too. */
    BYTE *ability = *(BYTE **)(worker + 0x400);
    if (!ability || !NATIVE(RVA_TYPE_IS_BUILDING, NativeI_I)((int)type))
        return 0;
    PendingFn can_build = (PendingFn)(*(DWORD **)(ability))[0x270 / 4];
    if (!can_build(ability, NULL, type))
        return 0;
    BYTE *player = unit_owner(worker);
    PendingCount c = {player, type, 0};
    NATIVE(RVA_ENUM_OBJECTS, EnumObjectsFn)(UNIT_CLASS, count_pending, &c, 0);
    c.count += NATIVE(RVA_PLAYER_TYPE_COUNT, PlayerCountFn)(player, NULL, type, 0x36);
    BYTE *primary = calloc(2, PLACEMENT_SEARCH_SIZE);
    if (!primary)
        return -1;
    BYTE *fallback = primary + PLACEMENT_SEARCH_SIZE;
    float site[3] = {0}, score = -1;
    __try {
        NATIVE(RVA_PLACEMENT_INIT, SearchInitFn)(primary, NULL, player_jass_id(player), x, y, type, c.count >= 2,
                                                 worker);
        NATIVE(RVA_PLACEMENT_ENUM, SearchEnumFn)(g_base + RVA_PLACEMENT_OBSTACLE, primary, 9,
                                                 *(DWORD *)(g_base + RVA_PLACEMENT_RADIUS),
                                                 *(DWORD *)(primary + 0x556c), *(DWORD *)(primary + 0x5570), 2, 31);
        NATIVE(RVA_PLACEMENT_COPY, SearchCopyFn)(fallback, NULL, primary);
        NATIVE(RVA_PLACEMENT_SEARCH, SearchRunFn)(primary, NULL, &score, site, 1);
        if (score == *(float *)(g_base + RVA_PLACEMENT_NO_SITE) && *(DWORD *)(primary + 0x20))
            NATIVE(RVA_PLACEMENT_SEARCH, SearchRunFn)(fallback, NULL, &score, site, 0);
    } __finally {
        free(primary);
    }
    if (score < 0 || !isfinite(score) || !isfinite(site[0]) || !isfinite(site[1]))
        return 0;
    *x = site[0];
    *y = site[1];
    return 1;
}

static const Footprint *footprint(unsigned type) {
    for (size_t i = 0; i < sizeof FOOTPRINTS / sizeof FOOTPRINTS[0]; i++)
        if (FOOTPRINTS[i].type == type)
            return &FOOTPRINTS[i];
    return NULL;
}

static int overlaps(unsigned type, float x, float y, const PendingSite *p) {
    const Footprint *a = footprint(type), *b = footprint(p->type);
    float w = (float)((a ? a->w : 128) + (b ? b->w : 128)), h = (float)((a ? a->h : 128) + (b ? b->h : 128));
    return 2 * fabsf(x - p->x) < w && 2 * fabsf(y - p->y) < h;
}

/* The engine's search sees only standing objects, so a site another worker is already walking to looks
 * free. Search again from anchors stepped outward until the chosen site clears every pending one. */
int placement_find(BYTE *worker, unsigned type, float *x, float *y, const PendingSite *pending, int n_pending,
                   const BYTE *ignore) {
    static const float DIRS[8][2] = {{1, 0}, {0.7071f, 0.7071f}, {0, 1}, {-0.7071f, 0.7071f},
                                     {-1, 0}, {-0.7071f, -0.7071f}, {0, -1}, {0.7071f, -0.7071f}};
    const Footprint *f = footprint(type);
    float step = f ? (float)max(f->w, f->h) : 128.0f;
    if (step < 128)
        step = 128;
    for (int ring = 0; ring <= 3; ring++) {
        for (int d = 0; d < (ring ? 8 : 1); d++) {
            float sx = *x + ring * step * DIRS[d][0], sy = *y + ring * step * DIRS[d][1];
            int found = search_once(worker, type, &sx, &sy);
            if (found < 0)
                return found;
            if (!found)
                continue;
            int clear = 1;
            for (int i = 0; i < n_pending && clear; i++)
                clear = (ignore && pending[i].worker == ignore) || !overlaps(type, sx, sy, &pending[i]);
            if (clear) {
                *x = sx;
                *y = sy;
                return 1;
            }
        }
    }
    return 0;
}

typedef struct {
    int player;
    PendingSite *out;
    int n, max;
} PendingScan;

static int __cdecl scan_pending(BYTE *u, void *ctx) {
    PendingScan *s = ctx;
    unsigned id;
    float px, py;
    BYTE *owner = unit_owner(u);
    if (s->n < s->max && owner && player_jass_id(owner) == s->player && !(*(DWORD *)(u + 0x20) & 1) &&
        unit_order_point(u, &id, &px, &py) && (id >> 24) && NATIVE(RVA_TYPE_IS_BUILDING, NativeI_I)((int)id))
        s->out[s->n++] = (PendingSite){id, px, py, u};
    return 1;
}

int placement_pending(int player, PendingSite *out, int max) {
    PendingScan s = {player, out, 0, max};
    __try {
        NATIVE(RVA_ENUM_OBJECTS, EnumObjectsFn)(UNIT_CLASS, scan_pending, &s, 0);
    } __except (native_exc(GetExceptionInformation())) {
        return 0;
    }
    return s.n;
}
