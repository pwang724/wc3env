/* Suppress agent slots' startup AI and register their offline command identities. */
#include "wc3hook.h"

static unsigned g_launch_agents = 1, g_agents, g_disabled_ai, g_registered, g_keep_ai;
static BYTE g_wire[16];
typedef void(__cdecl *StartAIFn)(int player, int script);
static StartAIFn StartMeleeAI_orig, StartCampaignAI_orig;

/* Agent is our control mode. Keep Warcraft's slot type and block its startup AI;
 * changing a computer to a human here makes melee startup treat it as disconnected. */
static int claim_agent(int handle) {
    int p = NATIVE(RVA_N_PLAYERID, NativeI_I)(handle);
    if (p < 0 || p >= 16 || !(g_launch_agents & (1u << p)))
        return 0;
    g_disabled_ai |= 1u << p;
    if (g_keep_ai & (1u << p)) { /* AI-backed agent: its melee AI plays; step() orders act alongside */
        hook_log("players: agent %d keeps its startup AI", p);
        return 0;
    }
    hook_log("players: disabled startup AI for agent %d", p);
    return 1;
}
const char *player_arg(yyjson_val *args, int *handle) {
    long long p;
    if (!jr_is_int(yyjson_obj_get(args, "player"), &p) || p < 0 || p >= 16)
        return "player must be a slot 0..15";
    if (!(*handle = NATIVE(RVA_N_PLAYER, NativeI_I)((int)p)))
        return "no such player";
    return NULL;
}

static void __cdecl StartMeleeAI_hook(int player, int script) {
    if (!claim_agent(player)) {
        difficulty_apply(player);
        StartMeleeAI_orig(player, script);
    }
}
static void __cdecl StartCampaignAI_hook(int player, int script) {
    if (!claim_agent(player))
        StartCampaignAI_orig(player, script);
}
void players_init_hooks(void) {
    difficulty_init_hooks();
    char agents[32];
    DWORD len = GetEnvironmentVariableA("WC3HOOK_AGENTS", agents, sizeof agents);
    if (len && len < sizeof agents)
        g_launch_agents = strtoul(agents, NULL, 10) & 0xffff;
    len = GetEnvironmentVariableA("WC3HOOK_AGENT_AI", agents, sizeof agents);
    if (len && len < sizeof agents)
        g_keep_ai = strtoul(agents, NULL, 10) & g_launch_agents;
    MH_CreateHook(g_base + RVA_N_STARTMELEEAI, (void *)StartMeleeAI_hook, (void **)&StartMeleeAI_orig);
    MH_CreateHook(g_base + RVA_N_STARTCAMPAIGNAI, (void *)StartCampaignAI_hook, (void **)&StartCampaignAI_orig);
}

int player_is_agent(int player) {
    return (g_agents & (1u << player)) != 0;
}
int player_wire_id(int player) {
    return g_wire[player];
}
int player_prepared_agent(int player) {
    return (g_disabled_ai & (1u << player)) != 0;
}
void players_clear(void) {
    g_agents = g_disabled_ai = g_registered = 0;
    memset(g_wire, 0, sizeof g_wire);
}
void players_unregister(void) {
    /* Remove identities we added: Warcraft otherwise waits for them during teardown.
     * Check membership first; the native unregister method dereferences a missing entry. */
    BYTE *table = g_game + 0x3d0;
    for (int p = 0; p < 16; p++)
        if (g_registered & (1u << p)) {
            int found = 0;
            for (int group = 0; group < 2; group++) {
                DWORD count = *(DWORD *)(table + 0x2c + 0x10 * group);
                BYTE **entries = *(BYTE ***)(table + 0x30 + 0x10 * group);
                for (DWORD i = 0; i < count; i++)
                    if (entries[i] && entries[i][0x18] == g_wire[p])
                        found = 1;
            }
            if (found) {
                typedef BYTE(__fastcall * UnregisterFn)(BYTE *, void *, int);
                ((UnregisterFn)(g_base + RVA_UNREGISTER_PLAYER))(table, NULL, g_wire[p]);
            }
            g_registered &= ~(1u << p);
        }
}

/* The active and waiting arrays share a hash from wire id to game slot. Register and assign
 * through Warcraft's own table methods; the ordinary turn parser then resolves both players. */
typedef BYTE(__fastcall *RegisterPlayerFn)(BYTE *, void *, int, BYTE *);
typedef void(__fastcall *AssignPlayerFn)(BYTE *, void *, int, int);
const char *players_configure(unsigned agents) {
    if (!g_game)
        return "no running game";
    if (agents != g_launch_agents)
        return "agent slots must match the launch configuration";
    if (!agents || !game_is_offline())
        return "agent control requires an offline game";
    BYTE wire[16] = {0}, used[256] = {0};
    unsigned found = 0;
    {
        BYTE *table = g_game + 0x3d0;
        for (int group = 0; group < 2; group++) {
            DWORD count = *(DWORD *)(table + 0x2c + 0x10 * group);
            BYTE **entries = *(BYTE ***)(table + 0x30 + 0x10 * group);
            if (count > 24)
                return "unexpected player table";
            for (DWORD i = 0; i < count; i++)
                if (entries[i]) {
                    int slot = entries[i][0x36];
                    used[entries[i][0x18]] = 1;
                    if (slot < 16) {
                        wire[slot] = entries[i][0x18];
                        found |= 1u << slot;
                    }
                }
        }
        int available = 0, needed = 0;
        for (int id = 1; id < 25; id++)
            if (!used[id])
                available++;
        for (int p = 0; p < 16; p++)
            if ((agents & (1u << p)) && !(found & (1u << p)))
                needed++;
        if (available < needed)
            return "no free command-stream player identity";
        for (int p = 0; p < 16; p++)
            if ((agents & (1u << p)) && !(found & (1u << p))) {
                int id = 1;
                while (used[id])
                    id++;
                BYTE info[25] = {0};
                _snprintf((char *)info, 16, "Agent %d", p);
                BYTE waiting = ((RegisterPlayerFn)(g_base + RVA_REGISTER_PLAYER))(table, NULL, id, info);
                /* Register returns a tagged waiting index: high bit set, low bits = wire id - 1. */
                if (waiting != ((id - 1) | 0x80)) {
                    players_unregister();
                    return "could not register command-stream player identity";
                }
                ((AssignPlayerFn)(g_base + RVA_ASSIGN_PLAYER))(table, NULL, waiting, p);
                g_wire[p] = wire[p] = (BYTE)id;
                used[id] = 1;
                g_registered |= 1u << p;
                hook_log("players: registered agent slot=%d wire=%d", p, id);
            }
    }
    memcpy(g_wire, wire, sizeof wire);
    g_agents = agents;
    return NULL;
}
