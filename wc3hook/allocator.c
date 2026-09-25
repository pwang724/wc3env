/* Reuse native free space before reserving more arenas. */
#include "wc3hook.h"
#include "allocator_bins.h"

typedef void *(__cdecl *BlockAllocFn)(DWORD *, BYTE *, DWORD, SIZE_T);
static BlockAllocFn BlockAlloc_orig;
static void *free_search_resume;

static void *__cdecl BlockAlloc_hook(DWORD *lock, BYTE *arena, DWORD flags, SIZE_T size) {
    if ((BYTE *)_ReturnAddress() == g_base + RVA_REALLOC_MOVE_RET && arena) {
        /* SMemReAlloc tries in-place growth first. Its fallback passes the old arena
         * here, causing each moved buffer to reserve another arena when that one is
         * full. Select the current arena with the same tag, exactly as SMemAlloc does
         * at 0x40683. The caller already holds this group's lock; native code still
         * allocates, copies, zeroes growth, frees the old block and releases the lock. */
        DWORD group = *(DWORD *)(arena + 8), tag = *(DWORD *)(arena + 4);
        BYTE *current = *(BYTE **)(g_base + RVA_ALLOCATOR_ARENAS + group * 4);
        while (current && *(DWORD *)(current + 4) != tag)
            current = *(BYTE **)current;
        if (current)
            arena = current;
    }
    return BlockAlloc_orig(lock, arena, flags, size);
}

static ArenaFreeBlock **__cdecl find_free(BYTE *arena, DWORD need) {
    ArenaFreeBlock **bins = (ArenaFreeBlock **)(arena + 0x44);
    ArenaFreeBlock **found = arena_find_free(bins, need);
    if (!found && *(DWORD *)(arena + 0x24)) {
        /* The native search checks only the first nonempty bin. Smaller blocks in
         * that bin can hide larger ones; adjacent frees may also need merging.
         * The caller holds the arena group's lock. Coalescing never moves live data. */
        ((void(__cdecl *)(BYTE *))(g_base + RVA_ARENA_COALESCE))(arena);
        found = arena_find_free(bins, need);
    }
    return found;
}

/* At 0x3f2d2: ESI is the arena, [EBP-8] the padded size, [EBP-0x10] the selected
 * free-list link. Retry a failed search, then let native code unlink/split the block
 * and update its headers/counters. A complete miss follows native arena growth. */
static __declspec(naked) void free_search_hook(void) {
    __asm {
        cmp dword ptr [ebp-0x10], 0
        jne resume
        pushad
        push dword ptr [ebp-8]
        push esi
        call find_free
        add esp, 8
        mov dword ptr [ebp-0x10], eax
        popad
    resume:
        jmp dword ptr [free_search_resume]
    }
}

void allocator_init_hooks(void) {
    require_hook(MH_CreateHook(g_base + RVA_ARENA_SEARCH_END, free_search_hook, &free_search_resume),
                 "allocator free-space search");
    require_hook(MH_CreateHook(g_base + RVA_BLOCK_ALLOC, BlockAlloc_hook, (void **)&BlockAlloc_orig),
                 "allocator arena selection");
}
