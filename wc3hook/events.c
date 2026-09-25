/* Game events from C: a detour on the game's trigger-event dispatcher.
 *
 * Every playerunitevent the observation reports (EVENT_PLAYER_UNIT_DEATH and the rest of
 * common.j's ConvertPlayerUnitEvent ids) is fired the same way: a per-kind function builds an
 * event object (a ref pair of the trigger unit at +0x20/+0x24, of a second widget at +0x38/+0x3c,
 * a third at +0x44/+0x48, an int or ref pair at +0x50/+0x54), then calls
 *   0xbcd00(this = unit, eventid = 0x80200 + id, object)          (thiscall)
 * which runs the registered triggers. The kinds themselves fire only if 0x43f0c0(eventid) says
 * something is registered, which on a stock map nothing is; that check is detoured so ours fire.
 * Events enter each observer's log only if visible when fired (own unit, or in that player's
 * fog). Separate bounded
 * logs keep hidden activity out of both retention and loss counts.
 * `observe(p)` consumes only that player's events.
 * Written as JSON objects (docs/specs/observations.md): object ids
 * as integers, type ids as four-character strings.
 */
#include "wc3hook.h"

#define RVA_FIRE_EVENT 0x0bcd00
#define EV_BASE 0x80200
typedef int(__fastcall *FireEventFn)(void *unit, void *edx, int eventid, BYTE *obj);
static FireEventFn FireEvent_orig;

/* Per-observer rings, written and read on the game thread only. */
#define EV_CAP 1024
typedef struct {
    int id, owner, level;
    unsigned unit_id, other_id, item_id, seen, other_seen, item_seen;
    DWORD type, other_type, item_type, argument;
} Event;
static Event g_ev[16][EV_CAP];
static unsigned g_ev_head[16]; /* visible events ever logged for each player */
static unsigned g_cursor[16];  /* per player: the first event it has not been given yet */
static unsigned g_players;     /* bit p: player p is in the game (create_game), so events are judged for it */

void events_set_players(unsigned mask) {
    g_players = mask;
    memset(g_ev_head, 0, sizeof g_ev_head);
    memset(g_cursor, 0, sizeof g_cursor);
}

/* who can see an event on this unit, right now: its owner, and every player whose fog shows it */
static unsigned seen_by(BYTE *unit, int owner) {
    unsigned mask = 0;
    if (!unit)
        return 0;
    for (int p = 0; p < 16; p++)
        if ((g_players >> p & 1) && (p == owner || unit_visible(unit, player_slot(p))))
            mask |= 1u << p;
    return mask;
}
static void ev_append(const Event *e) {
    for (int p = 0; p < 16; p++)
        if (e->seen & (1u << p))
            g_ev[p][g_ev_head[p]++ % EV_CAP] = *e;
}
static void ev_unit(Event *e, BYTE *unit) { /* the unit as it is now: type, owner, who sees it */
    e->owner = -1;
    if (!unit)
        return;
    BYTE *pl = unit_owner(unit);
    e->owner = pl ? player_jass_id(pl) : -1;
    e->type = *(DWORD *)(unit + 0x34);
    e->unit_id = obs_id(unit);
    e->seen = seen_by(unit, e->owner);
}

static BYTE *ref_obj(BYTE *obj, int off) {
    DWORD id = *(DWORD *)(obj + off), salt = *(DWORD *)(obj + off + 4);
    if ((id & salt) == 0xffffffff)
        return NULL;
    return ((ResolveRefFn)(g_base + RVA_RESOLVE_REF))(id, salt);
}

/* The spell-effect fire function (0xbbd60, thiscall: this = the player, args unit, ability) stores
 * the ability into the event object as a ref pair that resolves to an agent node without a way
 * back to the ability object, so the ability id is read here from the argument, the way
 * GetSpellAbilityId reads it from the handle's object, and the dispatcher hook picks it up. */
#define RVA_FIRE_SPELL_EFFECT 0x0bbd60
typedef int(__fastcall *FireSpellFn)(void *player, void *edx, BYTE *unit, BYTE *ability);
static FireSpellFn FireSpell_orig;
static DWORD ability_id(BYTE *ab);
static int __fastcall FireSpell_hook(void *player, void *edx, BYTE *unit, BYTE *ability) {
    __try {
        if (g_players && unit) { /* recorded here, whole: the dispatcher hook skips id 277 */
            DWORD ab = ability ? ability_id(ability) : 0;
            Event e = {0};
            e.id = 277;
            e.argument = ab;
            ev_unit(&e, unit);
            ev_append(&e);
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return FireSpell_orig(player, edx, unit, ability);
}

/* 2. The per-kind fire functions build the record and call the dispatcher only if 0x83f020
 * (thiscall: this, eventid -> bool; the fire functions call it as VA 0x83f0c0) says a trigger is
 * registered for that kind, which on a stock map nothing is. The check is detoured to say yes for
 * the kinds the observation reports (its own bookkeeping still runs). */
#define RVA_EVENT_REGISTERED 0x043f0c0 /* thiscall(this, eventid): [this+8] -> 0x43f100(mgr; id, 0), a pure query */
typedef int(__fastcall *EventRegisteredFn)(void *self, void *edx, int eventid);
static EventRegisteredFn EventRegistered_orig;
static int wanted_kind(int id) {
    switch (id) {
    case 18:
    case 20:
    case 26:
    case 27:
    case 28:
    case 29:
    case 30:
    case 31:
    case 32:
    case 33:
    case 34:
    case 35:
    case 36:
    case 37:
    case 41:
    case 42:
    case 47:
    case 49:
    case 50:
    case 274:
    case 277:
        return 1;
    }
    return 0;
}
static int __fastcall EventRegistered_hook(void *self, void *edx, int eventid) {
    int r = EventRegistered_orig(self, edx, eventid);
    return r || wanted_kind(eventid - EV_BASE);
}

/* Resolve only this event's references while they are alive. No pointer survives the hook,
 * and observing another player cannot change how a later event is resolved. */
typedef struct {
    BYTE *node[3], *object[3];
    int remaining;
} EventRefs;
static int __cdecl event_object_cb(BYTE *o, void *ctx) {
    EventRefs *refs = (EventRefs *)ctx;
    BYTE *node = ref_obj(o, 0xc);
    for (int i = 0; i < 3; i++) {
        if (refs->node[i] && !refs->object[i] && refs->node[i] == node) {
            refs->object[i] = o;
            refs->remaining--;
        }
    }
    return refs->remaining != 0;
}
static unsigned event_object_id(BYTE *o, BYTE *node) {
    return o ? obs_id(o) : node ? *(DWORD *)(node + 0x14) : 0;
}

static int __fastcall FireEvent_hook(void *unit, void *edx, int eventid, BYTE *obj) {
    __try {
        int id = eventid - EV_BASE;
        if (g_players && obj && id != 277 && wanted_kind(id)) {
            /* the event object's slots: +0x38 the trigger unit, +0x44 the second unit, +0x50 an int, a
             * type id or a ref pair (the item, the ability); `this` of the dispatcher is not the unit */
            EventRefs refs = {0};
            refs.node[0] = ref_obj(obj, 0x38);
            if (id == 18 || id == 34 || id == 47 || id == 274)
                refs.node[1] = ref_obj(obj, 0x44);
            if (id == 49 || id == 50 || id == 274)
                refs.node[2] = ref_obj(obj, 0x50);
            for (int i = 0; i < 3; i++)
                if (refs.node[i])
                    refs.remaining++;
            if (refs.remaining)
                ((EnumObjectsFn)(g_base + RVA_ENUM_OBJECTS))(UNIT_CLASS, (void *)event_object_cb, &refs, 0);
            if (refs.remaining)
                ((EnumObjectsFn)(g_base + RVA_ENUM_OBJECTS))(ITEM_CLASS, (void *)event_object_cb, &refs, 0);
            Event e = {0};
            e.id = id;
            e.argument = *(DWORD *)(obj + 0x50);
            ev_unit(&e, refs.object[0]);
            e.unit_id = event_object_id(refs.object[0], refs.node[0]);
            e.other_id = event_object_id(refs.object[1], refs.node[1]);
            e.other_seen = refs.object[1]
                               ? seen_by(refs.object[1],
                                         unit_owner(refs.object[1]) ? player_jass_id(unit_owner(refs.object[1])) : -1)
                               : 0;
            if (refs.object[2])
                for (int p = 0; p < 16; p++)
                    if ((g_players & (1u << p)) && object_visible(refs.object[2], ITEM_CLASS, p))
                        e.item_seen |= 1u << p;
            e.item_id = event_object_id(refs.object[2], refs.node[2]);
            e.other_type = refs.object[1] ? *(DWORD *)(refs.object[1] + 0x34) : 0;
            e.item_type = refs.object[2] ? *(DWORD *)(refs.object[2] + 0x34) : 0;
            if (e.owner >= 0 && e.owner < 16)
                e.item_seen |= 1u << e.owner;
            if (id == 41 && refs.object[0])
                e.level = unit_hero_level(refs.object[0]);
            ev_append(&e);
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return FireEvent_orig(unit, edx, eventid, obj);
}

/* a ref pair at an address, resolved the way the order and ability accessors do (0x3fc7e0, ecx = &pair) */
static __declspec(naked) BYTE *__cdecl pair_obj(BYTE *pair) {
    __asm {
        mov ecx, [esp + 4]
        mov eax, [ecx + 4]
        and eax, [ecx]
        cmp eax, -1
        je none
        jmp dword ptr [g_fn_resolve_pair]
    none:
        xor eax, eax
        ret
    }
}
/* an ability's type id, as GetSpellAbilityId reads it: the handle's object -> its +0x50 pair (0xbd480)
 * -> that object's +0x2c pair (0x290bd0) -> +0x34. The event's +0x50 pair may point at either level. */
static DWORD ability_id(BYTE *ab) {
    BYTE *x = pair_obj(ab + 0x50);
    BYTE *t;
    if (x && (t = pair_obj(x + 0x2c)) != NULL)
        return *(DWORD *)(t + 0x34);
    if ((t = pair_obj(ab + 0x2c)) != NULL)
        return *(DWORD *)(t + 0x34);
    return 0;
}

/* one event: {"kind": k, <fields>} */
static JW *g_w;
static void ev(const char *kind) {
    jw_open(g_w, '{');
    jw_key(g_w, "kind");
    jw_string(g_w, kind);
}
static void ev_id(const char *key, unsigned id) {
    jw_key(g_w, key);
    jw_uint(g_w, id);
}
static void ev_type(const char *key, DWORD t) {
    jw_key(g_w, key);
    jw_fourcc(g_w, t);
}
static void ev_end(void) {
    jw_close(g_w, '}');
}

/* the events `player` could see since its previous observation, oldest first */
unsigned emit_events(JW *w, int player) {
    unsigned from = g_cursor[player], head = g_ev_head[player];
    unsigned lost = head - from > EV_CAP ? head - from - EV_CAP : 0;
    if (lost)
        from = head - EV_CAP;
    g_w = w;
    for (unsigned k = from; k != head; k++) {
        Event *e = &g_ev[player][k % EV_CAP];
        unsigned ua = e->unit_id, va = (e->other_seen & (1u << player)) ? e->other_id : 0;
        switch (e->id) {
        case 20:
            ev("death");
            ev_id("unit_id", ua);
            ev_type("type_id", e->type);
            jw_key(g_w, "owner");
            jw_int(g_w, e->owner);
            ev_end();
            break;
        case 26:
            ev("construct_start");
            ev_id("unit_id", ua);
            ev_type("type_id", e->type);
            ev_end();
            break;
        case 27:
            ev("construct_cancel");
            ev_id("unit_id", ua);
            ev_type("type_id", e->type);
            ev_end();
            break;
        case 28:
            ev("construct_finish");
            ev_id("unit_id", ua);
            ev_type("type_id", e->type);
            ev_end();
            break;
        case 29:
            ev("upgrade_start");
            ev_id("unit_id", ua);
            ev_type("type_id", e->type);
            ev_end();
            break;
        case 30:
            ev("upgrade_cancel");
            ev_id("unit_id", ua);
            ev_type("type_id", e->type);
            ev_end();
            break;
        case 31:
            ev("upgrade_finish");
            ev_id("unit_id", ua);
            ev_type("type_id", e->type);
            ev_end();
            break;
        case 32:
            ev("train_start");
            ev_id("unit_id", ua);
            ev_type("type_id", e->argument);
            ev_end();
            break;
        case 33:
            ev("train_cancel");
            ev_id("unit_id", ua);
            ev_type("type_id", e->argument);
            ev_end();
            break;
        case 34:
            ev("train_finish");
            ev_id("unit_id", ua);
            ev_id("trained_id", va);
            ev_type("type_id", va ? e->other_type : 0);
            ev_end();
            break;
        case 35:
            ev("research_start");
            ev_id("unit_id", ua);
            ev_type("type_id", e->argument);
            ev_end();
            break;
        case 36:
            ev("research_cancel");
            ev_id("unit_id", ua);
            ev_type("type_id", e->argument);
            ev_end();
            break;
        case 37:
            ev("research_finish");
            ev_id("unit_id", ua);
            ev_type("type_id", e->argument);
            ev_end();
            break;
        case 41:
            ev("hero_level");
            ev_id("unit_id", ua);
            jw_key(g_w, "level");
            jw_int(g_w, e->level);
            ev_end();
            break;
        case 42:
            ev("hero_learn");
            ev_id("unit_id", ua);
            ev_type("ability_id", e->argument);
            ev_end();
            break;
        case 47:
            ev("summon");
            ev_id("unit_id", va);
            ev_id("summoned_id", ua);
            ev_type("type_id", e->type);
            ev_end();
            break;
        case 49:
            ev("item_pickup");
            ev_id("unit_id", ua);
            ev_id("item_id", (e->item_seen & (1u << player)) ? e->item_id : 0);
            ev_type("type_id", (e->item_seen & (1u << player)) ? e->item_type : 0);
            ev_end();
            break;
        case 50:
            ev("item_use");
            ev_id("unit_id", ua);
            ev_type("type_id", (e->item_seen & (1u << player)) ? e->item_type : 0);
            ev_end();
            break;
        case 18:
            ev("attacked");
            ev_id("unit_id", ua);
            ev_id("attacker_id", va);
            ev_end();
            break;
        case 274:
            ev("item_sold");
            ev_id("unit_id", ua);
            ev_id("buyer_id", va);
            ev_type("type_id", (e->item_seen & (1u << player)) ? e->item_type : 0);
            ev_end();
            break;
        case 277:
            ev("spell_effect");
            ev_id("unit_id", ua);
            ev_type("ability_id", e->argument);
            ev_end();
            break;
        }
    }
    g_cursor[player] = head;
    return lost;
}

void events_init_hooks(void) {
    MH_STATUS s = MH_CreateHook(g_base + RVA_FIRE_EVENT, (void *)FireEvent_hook, (void **)&FireEvent_orig);
    MH_STATUS t = MH_CreateHook(g_base + RVA_FIRE_SPELL_EFFECT, (void *)FireSpell_hook, (void **)&FireSpell_orig);
    MH_STATUS g =
        MH_CreateHook(g_base + RVA_EVENT_REGISTERED, (void *)EventRegistered_hook, (void **)&EventRegistered_orig);
    hook_log("FireEvent hook: %s, spell effect: %s, registered check: %s", MH_StatusToString(s), MH_StatusToString(t),
             MH_StatusToString(g));
}
