/* Opt-in offline map configuration. Apply before melee units and AI are created. */
#include "wc3hook.h"

typedef void(__cdecl *SetOneFn)(int);
typedef void(__cdecl *SetTwoFn)(int, int);
typedef void(__cdecl *LocationFn)(int, const float *, const float *);
typedef void(__fastcall *ResolveRacesFn)(BYTE *, void *);
typedef void(__fastcall *AssignStartsFn)(void *, void *);
static SetTwoFn Race_orig, Controller_orig, Start_orig, ForceStart_orig;
static LocationFn Location_orig;
static SetOneFn Players_orig;
static ResolveRacesFn ResolveRaces_orig;
static AssignStartsFn AssignStarts_orig;
static int g_enabled, g_closed, g_seed, g_shuffle, g_shuffled, g_bad_locations;
static unsigned g_players, g_computers, g_defined, g_random_races, g_slot_starts;
static int g_races[16], g_seen_config;
static float g_xy[16][2];

static unsigned next_random(unsigned *state) {
    unsigned x = *state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return *state = x;
}
static int slot(int handle) {
    return NATIVE(RVA_N_PLAYERID, NativeI_I)(handle);
}
static void seed_rng(void) {
    NATIVE(0x0a99a0, SetOneFn)(g_seed);
}
static void __cdecl players_hook(int count) {
    Players_orig(count);
    if (!g_closed) {
        g_shuffled = g_bad_locations = 0;
        g_defined = g_slot_starts = 0;
        g_seen_config = 1;
        seed_rng();
        hook_log("setup: configure %d players seed=%d", count, g_seed);
    }
}
static void __cdecl race_hook(int handle, int preference) {
    int p = slot(handle);
    if (!g_closed && p >= 0 && p < 16 && g_races[p]) {
        preference = g_races[p];
        hook_log("setup: player %d race preference %d", p, preference);
    }
    Race_orig(handle, preference);
}
static void __cdecl controller_hook(int handle, int controller) {
    int p = slot(handle);
    if (!g_closed && p >= 0 && p < 16 && (g_computers & (1u << p)))
        controller = 1;
    Controller_orig(handle, controller);
}
/* The lobby copies its preferences after map config and resolves them here.
 * This routine writes only the race/preference fields before melee startup.
 * Applying here avoids a later lobby copy undoing the requested preference. */
static void __fastcall resolve_races_hook(BYTE *players, void *edx) {
    ResolveRaces_orig(players, edx);
    if (g_closed)
        return;
    DWORD count = *(DWORD *)(players + 0x54);
    for (int p = 0; p < 16 && (DWORD)p < count; p++) {
        BYTE *player = *(BYTE **)(players + 0x58 + 4 * p);
        if (!player || !(g_players & g_slot_starts & (1u << p)))
            continue;
        if (g_races[p]) {
            int race = g_races[p] == 1 ? 1 : g_races[p] == 2 ? 2 : g_races[p] == 8 ? 3 : 4;
            *(DWORD *)(player + 0x2bc) = (*(DWORD *)(player + 0x2bc) & 0x40) | g_races[p];
            *(DWORD *)(player + 0x2c0) = race;
        }
        if ((g_computers & (1u << p)) || *(DWORD *)(player + 0x2d0) == 0) {
            /* Additional map-defined slots start as computer slots; agent startup
             * AI is suppressed by players.c. Do not invent map slots or human peers. */
            *(DWORD *)(player + 0x2d0) = 1;
            *(DWORD *)(player + 0x2c8) = 1;
            ((ResolveRacesFn)(g_base + 0x0c1450))(player, NULL);
        }
    }
}
/* The lobby assigns start locations only to the slots it knows about (random placement moves
 * them off their scripted index). Additional activated slots keep the scripted index and can
 * land on an occupied location: the second town hall is displaced and the first player's
 * workers can no longer mine. Earlier slots keep their location; colliding slots take the
 * lowest free map-defined one. */
static void __fastcall assign_starts_hook(void *lobby, void *edx) {
    AssignStarts_orig(lobby, edx);
    if (g_closed)
        return;
    unsigned used = 0, moved = 0;
    for (int p = 0; p < 16; p++) {
        if (!(g_players & (1u << p)))
            continue;
        int loc = NATIVE(0x0949a0, NativeI_I)(NATIVE(RVA_N_PLAYER, NativeI_I)(p)); /* GetPlayerStartLocation */
        if (loc >= 0 && loc < 16 && (g_defined & (1u << loc)) && !(used & (1u << loc)))
            used |= 1u << loc;
        else
            moved |= 1u << p;
    }
    for (int p = 0; p < 16; p++) {
        if (!(moved & (1u << p)))
            continue;
        for (int loc = 0; loc < 16; loc++)
            if ((g_defined & ~used) & (1u << loc)) {
                Start_orig(NATIVE(RVA_N_PLAYER, NativeI_I)(p), loc);
                used |= 1u << loc;
                hook_log("setup: player %d moved to free start location %d", p, loc);
                break;
            }
    }
}
static void __cdecl location_hook(int index, const float *x, const float *y) {
    if (!g_closed) {
        if (index < 0 || index >= 16 || g_shuffled)
            g_bad_locations = 1;
        else {
            g_xy[index][0] = *x;
            g_xy[index][1] = *y;
            g_defined |= 1u << index;
        }
    }
    Location_orig(index, x, y);
}
static void shuffle_locations(void) {
    if (g_closed || !g_shuffle || g_shuffled)
        return;
    int ids[16], permutation[16], count = 0;
    for (int i = 0; i < 16; i++)
        if (g_defined & (1u << i))
            ids[count] = permutation[count] = i, count++;
    if (count < 2) {
        g_bad_locations = 1;
        return;
    }
    unsigned state = (unsigned)g_seed ^ 0x9e3779b9u;
    for (int i = count - 1; i > 0; i--) {
        int j = (int)(next_random(&state) % (unsigned)(i + 1));
        int tmp = permutation[i];
        permutation[i] = permutation[j];
        permutation[j] = tmp;
    }
    for (int i = 0; i < count; i++)
        Location_orig(ids[i], &g_xy[permutation[i]][0], &g_xy[permutation[i]][1]);
    g_shuffled = 1;
    hook_log("setup: shuffled %d locations", count);
}
static void __cdecl start_hook(int handle, int location) {
    int p = slot(handle);
    if (!g_closed && p >= 0 && p < 16 && location >= 0 && location < 16 && (g_defined & (1u << location)))
        g_slot_starts |= 1u << p;
    shuffle_locations();
    Start_orig(handle, location);
}
static void __cdecl force_start_hook(int handle, int location) {
    shuffle_locations();
    ForceStart_orig(handle, location);
}
void setup_clear(void) {
    g_closed = g_shuffled = g_bad_locations = g_seen_config = 0;
    g_defined = g_slot_starts = 0;
    memset(g_xy, 0, sizeof g_xy);
}
void setup_hold(void) {
    if (g_enabled && !g_closed) {
        seed_rng();
        g_closed = 1;
    }
}
const char *setup_error(void) {
    if (!g_enabled)
        return NULL;
    if (!g_seen_config)
        return "map did not run supported player configuration";
    if (g_players & ~g_slot_starts)
        return "requested player has no map-defined start location";
    if (g_shuffle && (g_bad_locations || !g_shuffled))
        return "map start locations cannot be safely randomized";
    return NULL;
}
int setup_random_race(int player) {
    return g_enabled && (g_random_races & (1u << player));
}
void setup_write(JW *w) {
    jw_key(w, "match_setup");
    if (!g_enabled) {
        jw_null(w);
        return;
    }
    jw_open(w, '{');
    jw_key(w, "seed");
    jw_int(w, g_seed);
    jw_key(w, "randomize_starts");
    jw_bool(w, g_shuffle);
    jw_close(w, '}');
}
void setup_init_hooks(void) {
    char value[4096];
    DWORD len = GetEnvironmentVariableA("WC3HOOK_SETUP", value, sizeof value);
    if (!len)
        return;
    if (len >= sizeof value) {
        hook_log("invalid WC3HOOK_SETUP length");
        ExitProcess(1);
    }
    yyjson_doc *doc = jr_read(value);
    yyjson_val *root = yyjson_doc_get_root(doc);
    LONGLONG seed;
    yyjson_val *players = yyjson_obj_get(root, "players");
    yyjson_val *shuffle = yyjson_obj_get(root, "randomize_starts");
    if (!jr_is_int(yyjson_obj_get(root, "seed"), &seed) || seed < 0 || seed > INT_MAX || !yyjson_is_bool(shuffle) ||
        !yyjson_is_arr(players) || !yyjson_arr_size(players))
        goto invalid;
    g_seed = (int)seed;
    g_shuffle = yyjson_get_bool(shuffle);
    for (size_t i = 0; i < yyjson_arr_size(players); i++) {
        yyjson_val *p = yyjson_arr_get(players, i);
        LONGLONG id;
        char control[16], race[16];
        if (!jr_is_int(yyjson_obj_get(p, "slot"), &id) || id < 0 || id >= 16 || (g_players & (1u << id)) ||
            !jr_str(yyjson_obj_get(p, "control"), control, sizeof control))
            goto invalid;
        g_players |= 1u << id;
        if (!strcmp(control, "computer") || !strcmp(control, "agent_ai")) /* agent_ai: an agent slot loaded as a computer */
            g_computers |= 1u << id;
        else if (strcmp(control, "agent"))
            goto invalid;
        yyjson_val *r = yyjson_obj_get(p, "race");
        if (r) {
            if (!jr_str(r, race, sizeof race))
                goto invalid;
            if (!strcmp(race, "human"))
                g_races[id] = 1;
            else if (!strcmp(race, "orc"))
                g_races[id] = 2;
            else if (!strcmp(race, "night_elf"))
                g_races[id] = 4;
            else if (!strcmp(race, "undead"))
                g_races[id] = 8;
            else if (!strcmp(race, "random")) {
                g_random_races |= 1u << id;
                unsigned state = (unsigned)g_seed ^ (0x85ebca6bu * (unsigned)(id + 1));
                g_races[id] = 1 << (next_random(&state) % 4);
            } else
                goto invalid;
        }
    }
    yyjson_doc_free(doc);
    g_enabled = 1;
    MH_CreateHook(g_base + 0x0a9990, players_hook, (void **)&Players_orig);
    MH_CreateHook(g_base + 0x0a9720, race_hook, (void **)&Race_orig);
    MH_CreateHook(g_base + 0x0a9570, controller_hook, (void **)&Controller_orig);
    MH_CreateHook(g_base + 0x08f200, location_hook, (void **)&Location_orig);
    MH_CreateHook(g_base + 0x0a9780, start_hook, (void **)&Start_orig);
    MH_CreateHook(g_base + 0x0908a0, force_start_hook, (void **)&ForceStart_orig);
    MH_CreateHook(g_base + 0x07ea50, resolve_races_hook, (void **)&ResolveRaces_orig);
    MH_CreateHook(g_base + 0x07ed10, assign_starts_hook, (void **)&AssignStarts_orig);
    return;
invalid:
    yyjson_doc_free(doc);
    hook_log("invalid WC3HOOK_SETUP");
    ExitProcess(1);
}
