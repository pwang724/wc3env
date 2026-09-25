/* Optional melee-AI difficulty, configured before startup. Pinned executable only. */
#include "wc3hook.h"

static int g_difficulty = -1;
static int(__cdecl *MeleeDifficulty_orig)(void);

static int __cdecl MeleeDifficulty_hook(void) {
    typedef BYTE *(__fastcall * ResolvePair)(BYTE *, void *);
    BYTE *context = *(BYTE **)(g_base + 0xdb4a10);
    BYTE *controller = context ? ((ResolvePair)(g_base + RVA_RESOLVE_PAIR))(context + 0x2c, NULL) : NULL;
    if (controller) {
        DWORD old = *(DWORD *)(controller + 0x2d0);
        /* Player config is easy/normal/insane = 0/1/2. The AI controller's
         * normal flags are zero; bit 15 selects easy and bit 16 insane. */
        static const DWORD flags[] = {0x8000u, 0, 0x10000u};
        DWORD value = (old & ~0x18000u) | flags[g_difficulty];
        *(DWORD *)(controller + 0x2d0) = value;
        if (old != value)
            hook_log("difficulty: AI controller flags %x -> %x", old, value);
    }
    int value = MeleeDifficulty_orig();
    hook_log("difficulty: original MeleeDifficulty()=%d", value);
    return value;
}

void difficulty_init_hooks(void) {
    char value[8] = {0};
    DWORD n = GetEnvironmentVariableA("WC3HOOK_AI_DIFFICULTY", value, sizeof value);
    if (!n)
        return; /* Preserve the map's settings by default. */
    if (n != 1 || value[0] < '0' || value[0] > '2') {
        hook_log("difficulty: invalid launch setting");
        ExitProcess(1);
    }
    g_difficulty = value[0] - '0';
    MH_STATUS status = MH_CreateHook(g_base + 0x6b5a90, (void *)MeleeDifficulty_hook, (void **)&MeleeDifficulty_orig);
    if (status != MH_OK) {
        hook_log("difficulty: hook failed: %s", MH_StatusToString(status));
        ExitProcess(1);
    }
}

void difficulty_apply(int player) {
    if (g_difficulty < 0)
        return;
    typedef BYTE *(__cdecl * PlayerObject)(int);
    BYTE *obj = ((PlayerObject)(g_base + RVA_PLAYER_OBJECT))(player);
    int before = NATIVE(0x090aa0, NativeI_I)(player);
    if (!obj || before < 0 || before > 2) {
        hook_log("difficulty: setup FAILED");
        return;
    }
    *(int *)(obj + 0x2cc) = g_difficulty;
    hook_log("difficulty: player=%d GetAIDifficulty %d -> %d", NATIVE(RVA_N_PLAYERID, NativeI_I)(player), before,
             NATIVE(0x090aa0, NativeI_I)(player));
}

const char *difficulty_read(yyjson_val *args, JW *w) {
    int player;
    const char *err = player_arg(args, &player);
    if (err)
        return err;
    jw_key(w, "ai_difficulty");
    jw_int(w, NATIVE(0x090aa0, NativeI_I)(player));
    return NULL;
}
