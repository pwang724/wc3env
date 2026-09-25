/* Observer relationships, map bounds and own scores.
 * RVAs/prototypes verified with tools/natives.py on the supported executable. */
#include "wc3hook.h"

#define N_NEUTRAL_AGGRESSIVE 0x094680
#define N_PLAYER_TEAM 0x094b80
#define N_PLAYER_ALLIANCE 0x0944c0
#define N_PLAYER_ALLY 0x099510
#define N_PLAYER_ENEMY 0x099580
#define N_PLAYER_SCORE 0x094710
#define N_WORLD_BOUNDS 0x097ef0
#define N_RECT_MIN_X 0x095080
#define N_RECT_MIN_Y 0x0950a0
#define N_RECT_MAX_X 0x095040
#define N_RECT_MAX_Y 0x095060
#define N_REMOVE_RECT 0x0a51e0

typedef int(__cdecl *N0)(void);
typedef int(__cdecl *N3)(int, int, int);

static int g_bounds_ready;
static float g_bounds[4];
void metadata_clear(void) {
    g_bounds_ready = 0;
}
static void bounds_init(void) {
    if (g_bounds_ready)
        return;
    int rect = NATIVE(N_WORLD_BOUNDS, N0)();
    g_bounds[0] = bits_to_f(NATIVE(N_RECT_MIN_X, NativeI_I)(rect));
    g_bounds[1] = bits_to_f(NATIVE(N_RECT_MIN_Y, NativeI_I)(rect));
    g_bounds[2] = bits_to_f(NATIVE(N_RECT_MAX_X, NativeI_I)(rect));
    g_bounds[3] = bits_to_f(NATIVE(N_RECT_MAX_Y, NativeI_I)(rect));
    NATIVE(N_REMOVE_RECT, NativeI_I)(rect);
    g_bounds_ready = 1; /* retain only numbers; the temporary rect is released */
}

static const char *SCORES[] = {"units_trained",
                               "units_killed",
                               "structures_built",
                               "structures_razed",
                               "tech_percent",
                               "food_max_produced",
                               "food_max_used",
                               "heroes_killed",
                               "items_gained",
                               "mercenaries_hired",
                               "gold_mined",
                               "gold_mined_upkeep",
                               "gold_lost_upkeep",
                               "gold_lost_tax",
                               "gold_given",
                               "gold_received",
                               "lumber_total",
                               "lumber_lost_upkeep",
                               "lumber_lost_tax",
                               "lumber_given",
                               "lumber_received",
                               "unit_total",
                               "hero_total",
                               "resource_total",
                               "total"}; /* common.j PLAYER_SCORE_* enum order, 0..24 */

void metadata_write(JW *w, int observer) {
    int own = NATIVE(RVA_N_PLAYER, NativeI_I)(observer);
    int neutral = NATIVE(N_NEUTRAL_AGGRESSIVE, N0)();
    jw_key(w, "players");
    jw_open(w, '[');
    for (int p = 0; p < neutral + 4; p++) {
        int handle = NATIVE(RVA_N_PLAYER, NativeI_I)(p);
        int state = NATIVE(RVA_N_PLAYERSLOTSTATE, NativeI_I)(handle);
        if (state == 0 && p < neutral && p != observer)
            continue;
        int control = NATIVE(RVA_N_PLAYERCONTROL, NativeI_I)(handle);
        const char *relation = p == observer                                     ? "self"
                               : NATIVE(N_PLAYER_ENEMY, NativeI_II)(handle, own) ? "enemy"
                               : NATIVE(N_PLAYER_ALLY, NativeI_II)(handle, own)  ? "ally"
                                                                                 : "neutral";
        jw_open(w, '{');
        jw_key(w, "id");
        jw_int(w, p);
        jw_key(w, "kind");
        jw_string(w, p >= neutral ? "neutral" : "player");
        jw_key(w, "relation");
        jw_string(w, relation);
        jw_key(w, "team");
        jw_int(w, NATIVE(N_PLAYER_TEAM, NativeI_I)(handle));
        jw_key(w, "active");
        jw_bool(w, state == 1);
        jw_key(w, "controller");
        jw_string(w, p < 16 && player_is_agent(p) ? "agent"
                     : control == 0               ? "human"
                     : control == 1               ? "computer"
                                                  : "none");
        jw_key(w, "shares_vision");
        jw_bool(w, NATIVE(N_PLAYER_ALLIANCE, N3)(handle, own, 5));
        jw_key(w, "shares_control");
        jw_bool(w, NATIVE(N_PLAYER_ALLIANCE, N3)(handle, own, 6));
        jw_close(w, '}');
    }
    jw_close(w, ']');
    bounds_init();
    jw_key(w, "map");
    jw_open(w, '{');
    jw_key(w, "bounds");
    jw_open(w, '{');
    static const char *KEYS[] = {"min_x", "min_y", "max_x", "max_y"};
    for (int i = 0; i < 4; i++) {
        jw_key(w, KEYS[i]);
        jw_num(w, g_bounds[i]);
    }
    jw_close(w, '}');
    jw_close(w, '}');
    jw_key(w, "score");
    jw_open(w, '{');
    for (int i = 0; i < 25; i++) {
        jw_key(w, SCORES[i]);
        jw_int(w, NATIVE(N_PLAYER_SCORE, NativeI_II)(own, i));
    }
    jw_close(w, '}');
}
