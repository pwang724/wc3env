/* The observation: unit and item accessors copied from the natives, fog, inventory, ground items, destructables, the
 * result. */
#include "wc3hook.h"
#include "orders_table.h"

/* Enumerate units without a group handle. 0x3f4ba0(class, cb, ctx, 0) is the game's own
 * "for every live object of this class" (GroupEnumUnitsOfPlayer ends in it); the unit class
 * id is 0x2b773375. The callback gets the unit object and must return nonzero to continue.
 * Fields are read as the natives read them after their handle->object step:
 *   type id     [obj+0x34]                       (GetUnitTypeId)
 *   position    obj->vtbl[0xb8/4](&v3) then 0x3fcc50 -> float x,y   (GetUnitX/Y)
 *   owner       0x292340(obj) -> player object, byte +0x30 = index (GetOwningPlayer, Player)
 *   life etc.   0x2935c0(obj, &out, state) -> &float, state 0..3 = life, max life, mana, max mana */
typedef void(__cdecl *EnumObjectsFn)(int cls, void *cb, void *ctx, int zero);
BYTE *g_fn_owner, *g_fn_state, *g_fn_unpack; /* g_base + RVA_*, for the naked bodies */
/* the accessors copy the natives' instruction sequences (they rely on caller-cleanup quirks) */
__declspec(naked) int __cdecl unit_xy_bits(BYTE *u, int idx) { /* float bits of x (idx 0) or y (4) */
    __asm {
        push ebp
        mov ebp, esp
        sub esp, 0xc
        mov ecx, [ebp + 8]
        lea eax, [ebp - 0xc]
        push eax
        mov eax, [ecx]
        call dword ptr [eax + 0xb8]
        mov ecx, eax
        call dword ptr [g_fn_unpack]
        add eax, [ebp + 0xc]
        mov eax, [eax]
        mov esp, ebp
        pop ebp
        ret
    }
}
__declspec(naked) int __cdecl unit_state_bits(BYTE *u, int state) { /* 0 life 1 max 2 mana 3 max */
    __asm {
        push ebp
        mov ebp, esp
        push esi
        mov esi, [ebp + 0xc]
        push esi
        lea ecx, [ebp + 0xc]
        push ecx
        mov ecx, [ebp + 8]
        call dword ptr [g_fn_state]
        pop esi
        mov eax, [eax]
        pop ebp
        ret
    }
}
__declspec(naked) BYTE *__cdecl unit_owner(BYTE *u) {
    __asm {
        mov ecx, [esp + 4]
        jmp dword ptr [g_fn_owner]
    }
}
/* GetPlayerId's body after its handle step: the internal slot (byte +0x30), mapped 24..27 ->
 * 12..15 for maps whose script version is below 0x17ac (the neutral players on legacy maps) */
BYTE *g_vm_global, *g_fn_ctx, *g_fn_ver;
__declspec(naked) int __cdecl player_jass_id(BYTE *pl) {
    __asm {
        push ebp
        mov ebp, esp
        push esi
        mov eax, [ebp + 8]
        movzx esi, byte ptr [eax + 0x30]
        mov eax, dword ptr [g_vm_global]
        mov eax, [eax]
        mov ecx, [eax + 0x30]
        lea ecx, [ecx + 0x24]
        call dword ptr [g_fn_ctx]
        push eax
        call dword ptr [g_fn_ver]
        add esp, 4
        cmp eax, 0x17ac
        jae keep
        lea eax, [esi - 0x18]
        cmp eax, 3
        lea eax, [esi - 0xc]
        jbe done
    keep:
        mov eax, esi
    done:
        pop esi
        pop ebp
        ret
    }
}
/* IsUnitVisible(u, p): unit->vtbl[0xfc/4](player slot, 0, 4), callee cleans (thiscall, 3 args) */
__declspec(naked) int __cdecl unit_visible(BYTE *u, int player_slot) {
    __asm {
        push 4
        push 0
        push dword ptr [esp + 12 + 4]
        mov ecx, [esp + 12 + 4]
        mov eax, [ecx]
        call dword ptr [eax + 0xfc]
        ret
    }
}
/* UnitItemInSlot: inventory ability [u+0x404] -> 0x291820(u, slot), ret 4 */
BYTE *g_fn_slot;
BYTE *g_fn_resolve_pair; /* 0x3fc7e0: (ecx = &ref pair) -> object or 0 */
__declspec(naked) BYTE *__cdecl unit_item_in_slot(BYTE *u, int slot) {
    __asm {
        push dword ptr [esp + 8]
        mov ecx, [esp + 4 + 4]
        call dword ptr [g_fn_slot]
        ret
    }
}
/* IsItemOwned (0x990f0): ref pair +0x194/+0x198 == -1 -> not owned; otherwise owned only if the
 * pair still resolves through 0x3fc7e0 (a dropped item can keep a stale pair) */
__declspec(naked) int __cdecl item_owned(BYTE *it) {
    __asm {
        mov ecx, [esp + 4]
        mov eax, [ecx + 0x198]
        lea ecx, [ecx + 0x194]
        and eax, [ecx]
        cmp eax, -1
        je none
        call dword ptr [g_fn_resolve_pair]
        test eax, eax
        setne al
        movzx eax, al
        ret
    none:
        xor eax, eax
        ret
    }
}
float bits_to_f(int i) {
    union {
        float f;
        int i;
    } v;
    v.i = i;
    return v.f;
}
/* Observation contents (docs/specs/observations.md; implementation: docs/design.md):
 *   fog        IsUnitVisible's check on the unit object (vtable slot 0xfc, args slot,0,4)
 *   inventory  UnitItemInSlot: [u+0x404] inventory ability -> 0x291820(u, slot); item type [+0x34],
 *              charges [+0x18c]
 *   items      objects of registry class 'item' (0x6974656d) that are not hidden ([+0x20] & 1, IsItemVisible),
 *              not owned (IsItemOwned), and alive (GetWidgetLife's item getter reads ITEM_LIFE).
 *              Consumed power-ups can linger unowned and unhidden, with zero life.
 * IsUnitType(STRUCTURE): flags [u+0x164] & 0x10000. IsUnitType(HERO): type id's first letter is
 * upper case ([u+0x37]) and flags bit 0x40000000 is clear. GetHeroLevel: hero record [u+0x3fc],
 * its +0x70 read through the game's protected-int accessor 0x3fb2b0 (thiscall, no args), + 1. */
/* unit flag bits at [u+0x164], from the natives: 0x10 IsUnitLoaded (in a transport; a worker inside
 * a mine sets it too), 0x10000 STRUCTURE, 0x200000 IsUnitPaused, 0x40000000 IsUnitIllusion.
 * [u+0x20] & 1 is IsUnitHidden (ShowUnit false); the registry enumerator still lists those. */
#define UF_LOADED 0x10
#define UF_STRUCTURE 0x10000
#define UF_PAUSED 0x200000
#define UF_ILLUSION 0x40000000
#define UF_FLYING 0x20000000 /* every flyer has it (chimaera, hippogryph, gryphon, storm crow) */

static DWORD unit_flags(BYTE *u) {
    return *(DWORD *)(u + 0x164);
}
static int unit_is_hero(BYTE *u) {
    BYTE c = u[0x37];
    return c >= 'A' && c <= 'Z' && !(unit_flags(u) & UF_ILLUSION);
}
static int unit_is_structure(BYTE *u) {
    return (unit_flags(u) & UF_STRUCTURE) != 0;
}
int unit_hero_level(BYTE *u) {
    BYTE *h = *(BYTE **)(u + 0x3fc);
    int r;
    BYTE *fn = g_base + RVA_PROTECTED_INT;
    if (!unit_is_hero(u) || !h)
        return 0;
    h += 0x70;
    __asm { mov ecx, h }
    __asm {
        call fn
    }
    __asm { mov r, eax }
    return r + 1;
}
/* The RPC names a unit or item by the game's own object id ([o+0xc]: allocated in creation order,
 * stable for the object's life, identical across runs with the same inputs), never by address. */
unsigned obs_id(BYTE *o) {
    return *(DWORD *)(o + 0xc);
}

/* One observation in the making: the observer and the JSON arrays its callbacks fill. */
typedef struct {
    int player, slot, player_handle; /* JASS id, internal fog slot, read-only player handle */
    JW own, inside, others, inventory, items, destructables;
} Obs;

static int point_visible(float x, float y, int player_handle) {
    typedef int(__cdecl * VisibleFn)(const float *, const float *, int);
    return NATIVE(RVA_N_ISVISIBLE, VisibleFn)(&x, &y, player_handle);
}

/* GetDestructableLife: dead if vtbl[0x13c](d) is nonzero, else life from vtbl[0x12c](d, &out) */
static __declspec(naked) int __cdecl destr_dead(BYTE *d) {
    __asm {
        mov ecx, [esp + 4]
        mov eax, [ecx]
        call dword ptr [eax + 0x13c]
        ret
    }
}
static __declspec(naked) int __cdecl destr_life_bits(BYTE *d) {
    __asm {
        push ebp
        mov ebp, esp
        sub esp, 4
        lea eax, [ebp - 4]
        push eax
        mov ecx, [ebp + 8]
        mov eax, [ecx]
        call dword ptr [eax + 0x12c]
        mov eax, [ebp - 4]
        mov esp, ebp
        pop ebp
        ret
    }
}
static int item_on_ground(BYTE *it) {
    return *(DWORD *)(it + 0x34) && !(*(DWORD *)(it + 0x20) & 1) && bits_to_f(*(int *)(it + ITEM_LIFE)) > 0 &&
           !item_owned(it);
}
int object_visible(BYTE *object, int cls, int player) {
    if (*(DWORD *)(object + 0x20) & 1)
        return 0;
    if (cls == UNIT_CLASS)
        return unit_visible(object, player_slot(player));
    if (cls == ITEM_CLASS && !item_on_ground(object))
        return 0;
    float x = bits_to_f(unit_xy_bits(object, 0)), y = bits_to_f(unit_xy_bits(object, 4));
    return point_visible(x, y, NATIVE(RVA_N_PLAYER, NativeI_I)(player));
}
static int __cdecl obs_destructable_cb(BYTE *d, void *ctx) {
    Obs *o = (Obs *)ctx;
    if (destr_dead(d) || bits_to_f(destr_life_bits(d)) <= 0 || !object_visible(d, DESTRUCTABLE_CLASS, o->player))
        return 1;
    float x = bits_to_f(unit_xy_bits(d, 0)), y = bits_to_f(unit_xy_bits(d, 4));
    JW *w = &o->destructables;
    jw_open(w, '{');
    jw_key(w, "id");
    jw_uint(w, obs_id(d));
    jw_key(w, "type_id");
    jw_fourcc(w, *(DWORD *)(d + 0x34));
    jw_key(w, "x");
    jw_num(w, x);
    jw_key(w, "y");
    jw_num(w, y);
    jw_key(w, "hp");
    jw_num(w, bits_to_f(destr_life_bits(d)));
    /* The engine's destructable target-mask getter reads the loaded object-data
     * row (including custom types).
     * 0x40 is the tree target class, not a type-ID list. */
    typedef DWORD(__fastcall * TargetMaskFn)(BYTE *, void *);
    DWORD targets = ((TargetMaskFn)(g_base + 0x2ce930))(d, NULL);
    jw_key(w, "resource");
    if (targets & 0x40)
        jw_string(w, "lumber");
    else
        jw_null(w);
    jw_key(w, "invulnerable");
    jw_bool(w, (*(DWORD *)(d + 0x20) & 8) != 0);
    jw_close(w, '}');
    return 1;
}

/* a ref pair at an address -> its object, or NULL for the empty pair (-1, -1) */
static __declspec(naked) BYTE *__cdecl pair_object(BYTE *pair) {
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
/* an ability object's class tag: vtbl[0x1c]() ('Aque', 'ABnP', ...) */
static __declspec(naked) DWORD __cdecl ability_tag(BYTE *ability) {
    __asm {
        mov ecx, [esp + 4]
        mov eax, [ecx]
        jmp dword ptr [eax + 0x1c]
    }
}
/* The unit's current order, as GetUnitCurrentOrder (0x973a0) reads it: the pair at u+0x3a8 -> the order
 * object, id at +0x24. The same object holds the point at +0x48/+0x50 and the target's pair at +0x58,
 * whose first word is the target's object id (measured on harvest gold/tree, move and build). A build
 * order's id is the structure's type id, so ids outside the order table are written as four characters. */
int unit_order_point(BYTE *u, unsigned *id, float *x, float *y) {
    BYTE *ord = pair_object(u + 0x3a8);
    if (!ord || !*(DWORD *)(ord + 0x24))
        return 0;
    *id = *(DWORD *)(ord + 0x24);
    *x = *(float *)(ord + 0x48);
    *y = *(float *)(ord + 0x50);
    return 1;
}
static void write_order(JW *w, BYTE *u) {
    BYTE *ord = pair_object(u + 0x3a8);
    unsigned id = ord ? *(DWORD *)(ord + 0x24) : 0;
    jw_key(w, "order");
    if (!id) {
        jw_null(w);
        return;
    }
    jw_open(w, '{');
    jw_key(w, "name");
    const char *name = id == 851970u ? "harvest" : NULL; /* the harvest command's own id (act.c O_HARVEST) */
    for (size_t i = 0; i < sizeof ORDER_NAMES / sizeof ORDER_NAMES[0] && !name; i++)
        if (ORDER_NAMES[i].id == id)
            name = ORDER_NAMES[i].name;
    if (name)
        jw_string(w, name);
    else if (id >> 24)
        jw_fourcc(w, id);
    else
        jw_uint(w, id);
    jw_key(w, "target_id");
    if (*(DWORD *)(ord + 0x58) != 0xffffffff)
        jw_uint(w, *(DWORD *)(ord + 0x58));
    else
        jw_null(w);
    jw_key(w, "x");
    jw_num(w, *(float *)(ord + 0x48));
    jw_key(w, "y");
    jw_num(w, *(float *)(ord + 0x50));
    jw_close(w, '}');
}
/* A structure's production, from its ability chain (u+0x3e8, next at +0x24; the walk
 * UnitSetConstructionProgress 0x2d8bc0 does): 'ABnP' while under construction and 'AUnP' while upgrading
 * to another structure, each with the total seconds at +0x7c; 'Aque' holds the queued unit and research
 * type ids from +0xa8, front first, zero after the last, and the front item's total seconds at +0x7c. */
static void write_production(JW *w, BYTE *u) {
    BYTE *queue = NULL;
    const char *state = NULL;
    float seconds = 0;
    int n = 0;
    for (BYTE *a = pair_object(u + 0x3e8); a && n < 64; a = pair_object(a + 0x24), n++) {
        DWORD tag = ability_tag(a);
        if (tag == 'Aque')
            queue = a;
        else if (tag == 'ABnP' || tag == 'AUnP') {
            state = tag == 'ABnP' ? "constructing" : "upgrading";
            seconds = *(float *)(a + 0x7c);
        }
    }
    jw_key(w, "state");
    if (state)
        jw_string(w, state);
    else
        jw_null(w);
    jw_key(w, "state_seconds");
    jw_num(w, seconds);
    jw_key(w, "queue");
    jw_open(w, '[');
    for (int i = 0; queue && i < 7 && *(DWORD *)(queue + 0xa8 + 4 * i); i++)
        jw_fourcc(w, *(DWORD *)(queue + 0xa8 + 4 * i));
    jw_close(w, ']');
    jw_key(w, "queue_seconds");
    jw_num(w, queue && *(DWORD *)(queue + 0xa8) ? *(float *)(queue + 0x7c) : 0);
}

/* Every ability on the unit's chain that it has learned, with the live numbers the natives give:
 * the same walk as write_production. Internal abilities (movement, inventory, production) are on the
 * chain too; the client tells them apart by id. Nothing here says whether a cast would succeed. */
typedef int(__cdecl *NativeI_HI)(int, int);
typedef int(__cdecl *NativeI_HII)(int, int, int);
/* The class tag is the ability's base class ('Aprg'); a variant the unit actually has (the Shaman's 'Apg2',
 * the Spirit Walker's 'Adcn' on class 'Adis') reads level 0 under it. The object also holds its own id:
 * find the four-character code in it that the unit has a level of. Returns that level (0 if none). */
static int variant_id(BYTE *a, int handle, DWORD *aid, int buff) {
    static int offsets[2] = {-1, -1}; /* found once each for abilities and buffs, then read directly */
    int offset = offsets[buff];
    if (offset >= 0) {
        DWORD id = *(DWORD *)(a + offset);
        int level = NATIVE(RVA_N_UNITABILITYLEVEL, NativeI_HI)(handle, (int)id);
        if (level > 0) {
            *aid = id;
            return level;
        }
    }
    for (int off = 0x28; off <= 0x100; off += 4) {
        DWORD id = *(DWORD *)(a + off);
        BYTE first = (BYTE)(id >> 24);
        if (id == *aid || (buff ? first != 'B' : first != 'A' && first != 'S'))
            continue; /* ability ids start with A (or S for some specials), buff ids with B */
        int level = NATIVE(RVA_N_UNITABILITYLEVEL, NativeI_HI)(handle, (int)id);
        if (level > 0) {
            if (offset < 0) {
                offsets[buff] = off;
                hook_log("%s: variant ids at +0x%x", buff ? "buffs" : "abilities", off);
            }
            *aid = id;
            return level;
        }
    }
    return 0;
}
static void write_abilities(JW *w, BYTE *u) {
    int handle = handle_of(u);
    jw_key(w, "abilities");
    jw_open(w, '[');
    int n = 0;
    for (BYTE *a = handle ? pair_object(u + 0x3e8) : NULL; a && n < 64; a = pair_object(a + 0x24), n++) {
        DWORD aid = ability_tag(a);
        if ((BYTE)(aid >> 24) == 'B')
            continue; /* a buff: write_buffs */
        int level = NATIVE(RVA_N_UNITABILITYLEVEL, NativeI_HI)(handle, (int)aid);
        if (level <= 0)
            level = variant_id(a, handle, &aid, 0);
        if (level <= 0)
            continue;
        jw_open(w, '{');
        jw_key(w, "ability_id");
        jw_fourcc(w, aid);
        jw_key(w, "level");
        jw_int(w, level);
        jw_key(w, "mana_cost");
        jw_int(w, NATIVE(RVA_N_ABILITYMANACOST, NativeI_HII)(handle, (int)aid, level));
        jw_key(w, "cooldown_seconds");
        jw_num(w, bits_to_f(NATIVE(RVA_N_ABILITYCOOLDOWN, NativeI_HII)(handle, (int)aid, level)));
        jw_key(w, "cooldown_remaining");
        jw_num(w, bits_to_f(NATIVE(RVA_N_ABILITYCOOLDOWNLEFT, NativeI_HI)(handle, (int)aid)));
        jw_close(w, '}');
    }
    jw_close(w, ']');
}

/* The buffs on a unit, any unit in view: buffs sit on the same chain as abilities, their ids start with B
 * ('Bprg' purged, 'Bblo' bloodlust, 'BOae' endurance aura). A variant buff is found as variant_id finds
 * a variant ability. How long each has left is not read here. */
static void write_buffs(JW *w, BYTE *u) {
    int handle = 0; /* looked up at the first buff: most units have none */
    jw_key(w, "buffs");
    jw_open(w, '[');
    int n = 0;
    for (BYTE *a = pair_object(u + 0x3e8); a && n < 64; a = pair_object(a + 0x24), n++) {
        DWORD bid = ability_tag(a);
        if ((BYTE)(bid >> 24) != 'B')
            continue;
        if (!handle && !(handle = handle_of(u)))
            break;
        if (NATIVE(RVA_N_UNITABILITYLEVEL, NativeI_HI)(handle, (int)bid) <= 0 && variant_id(a, handle, &bid, 1) <= 0)
            continue;
        jw_fourcc(w, bid);
    }
    jw_close(w, ']');
}

static int __cdecl obs_unit_cb(BYTE *u, void *ctx) {
    Obs *o = (Obs *)ctx;
    float life = bits_to_f(unit_state_bits(u, 0));
    BYTE *pl = unit_owner(u);
    int owner = pl ? player_jass_id(pl) : -1;
    if (life <= 0)
        return 1;
    /* what GroupEnumUnitsInRect leaves out: hidden ([u+0x20] & 1; a worker in a mine is), in a transport (UF_LOADED),
     * and a locust ([u+0x20] bit 2 clear: 0x064c on uloc against 0x...e on every other unit surveyed, 25 kinds;
     * the flag word cannot tell a locust from a gryphon, both are 0x20001001). A wisp in an entangled mine
     * also has bit 2 clear, but is loaded (0x191405 / 0x1018); a locust is not. The observer's own hidden or
     * loaded units (in a mine, a Burrow, a building under construction, a transport) go to `inside`. */
    DWORD f20 = *(DWORD *)(u + 0x20);
    int loaded = (unit_flags(u) & UF_LOADED) != 0;
    if (!(f20 & 2) && !loaded)
        return 1;
    if ((f20 & 1) || loaded) {
        if (owner == o->player) {
            JW *w = &o->inside;
            jw_open(w, '{');
            jw_key(w, "unit_id");
            jw_uint(w, obs_id(u));
            jw_key(w, "type_id");
            jw_fourcc(w, *(DWORD *)(u + 0x34));
            jw_key(w, "x");
            jw_num(w, bits_to_f(unit_xy_bits(u, 0)));
            jw_key(w, "y");
            jw_num(w, bits_to_f(unit_xy_bits(u, 4)));
            jw_key(w, "hp");
            jw_int(w, (int)life);
            write_order(w, u);
            jw_close(w, '}');
        }
        return 1;
    }
    if (!unit_visible(u, o->slot))
        return 1;
    DWORD type = *(DWORD *)(u + 0x34);
    float x = bits_to_f(unit_xy_bits(u, 0)), y = bits_to_f(unit_xy_bits(u, 4));
    int maxhp = (int)bits_to_f(unit_state_bits(u, 1)), mana = (int)bits_to_f(unit_state_bits(u, 2)),
        maxmana = (int)bits_to_f(unit_state_bits(u, 3));
    int structure = unit_is_structure(u), hero = unit_is_hero(u), level = unit_hero_level(u), own = owner == o->player;
    JW *w = own ? &o->own : &o->others;
    jw_open(w, '{');
    jw_key(w, "unit_id");
    jw_uint(w, obs_id(u));
    jw_key(w, "type_id");
    jw_fourcc(w, type);
    jw_key(w, "owner");
    jw_int(w, owner);
    jw_key(w, "x");
    jw_num(w, x);
    jw_key(w, "y");
    jw_num(w, y);
    jw_key(w, "hp");
    jw_int(w, (int)life);
    jw_key(w, "max_hp");
    jw_int(w, maxhp);
    jw_key(w, "mana");
    jw_int(w, mana);
    jw_key(w, "max_mana");
    jw_int(w, maxmana);
    jw_key(w, "structure");
    jw_bool(w, structure);
    jw_key(w, "hero");
    jw_bool(w, hero);
    jw_key(w, "level");
    jw_int(w, level);
    write_buffs(w, u);
    if (own) {
        write_order(w, u);
        if (structure)
            write_production(w, u);
        write_abilities(w, u);
    }
    jw_close(w, '}');
    if (own && *(BYTE **)(u + 0x404)) {
        for (int slot = 0; slot < 6; slot++) {
            BYTE *it = unit_item_in_slot(u, slot);
            if (!it)
                continue;
            w = &o->inventory;
            jw_open(w, '{');
            jw_key(w, "unit_id");
            jw_uint(w, obs_id(u));
            jw_key(w, "slot");
            jw_int(w, slot);
            jw_key(w, "type_id");
            jw_fourcc(w, *(DWORD *)(it + 0x34));
            jw_key(w, "charges");
            jw_int(w, *(int *)(it + 0x18c));
            jw_close(w, '}');
        }
    }
    return 1;
}
static int __cdecl obs_item_cb(BYTE *it, void *ctx) {
    Obs *o = (Obs *)ctx;
    JW *w = &o->items;
    if (!item_on_ground(it))
        return 1;
    float x = bits_to_f(unit_xy_bits(it, 0)), y = bits_to_f(unit_xy_bits(it, 4));
    if (!point_visible(x, y, o->player_handle))
        return 1;
    jw_open(w, '{');
    jw_key(w, "item_id");
    jw_uint(w, obs_id(it));
    jw_key(w, "type_id");
    jw_fourcc(w, *(DWORD *)(it + 0x34));
    jw_key(w, "x");
    jw_num(w, x);
    jw_key(w, "y");
    jw_num(w, y);
    jw_close(w, '}');
    return 1;
}

/* the internal slot of a JASS player id, as the Player native (0xa4240) maps it: on a map whose
 * script version is below 0x17ac the neutral ids 12..15 are slots 24..27, otherwise identity.
 * The same version read as player_jass_id, which is this mapping's inverse. */
__declspec(naked) int __cdecl script_version(void) {
    __asm {
        mov eax, dword ptr [g_vm_global]
        mov eax, [eax]
        mov ecx, [eax + 0x30]
        lea ecx, [ecx + 0x24]
        call dword ptr [g_fn_ctx]
        push eax
        call dword ptr [g_fn_ver]
        add esp, 4
        ret
    }
}
int player_slot(int jass_id) {
    if (jass_id >= 12 && jass_id <= 15 && (unsigned)script_version() < 0x17ac)
        return jass_id + 12;
    return jass_id;
}

/* the raw function pointers the naked accessors jump through; once, after g_base is known */
void accessors_init(void) {
    g_fn_owner = g_base + RVA_UNIT_OWNER;
    g_fn_state = g_base + RVA_UNIT_STATE;
    g_fn_unpack = g_base + RVA_POS_UNPACK;
    g_vm_global = g_base + RVA_VM_GLOBAL;
    g_fn_ctx = g_base + RVA_VM_STR_CTX;
    g_fn_ver = g_base + RVA_SCRIPT_VERSION;
    g_fn_slot = g_base + RVA_ITEM_IN_SLOT;
    g_fn_resolve_pair = g_base + RVA_RESOLVE_PAIR;
}

/* The observation as one JSON object (docs/specs/observations.md). Game thread. */
void obs_write_json(JW *w, int player, int seq) {
    static Obs o;
    jw_reset(&o.own);
    jw_reset(&o.inside);
    jw_reset(&o.others);
    jw_reset(&o.inventory);
    jw_reset(&o.items);
    jw_reset(&o.destructables);
    o.player = player;
    o.slot = player_slot(player);
    int pj = NATIVE(RVA_N_PLAYER, NativeI_I)(player);
    o.player_handle = pj;
    NativeI_II ps = NATIVE(RVA_N_GETPLAYERSTATE, NativeI_II);
    ((EnumObjectsFn)(g_base + RVA_ENUM_OBJECTS))(UNIT_CLASS, (void *)obs_unit_cb, &o, 0);
    ((EnumObjectsFn)(g_base + RVA_ENUM_OBJECTS))(ITEM_CLASS, (void *)obs_item_cb, &o, 0);
    jw_open(w, '{');
    jw_key(w, "protocol_version");
    jw_int(w, 1);
    jw_key(w, "observer");
    jw_int(w, player);
    metadata_write(w, player);
    jw_key(w, "sequence");
    jw_int(w, seq);
    jw_key(w, "game_time_seconds");
    jw_num(w, game_time() / 1000.0);
    jw_key(w, "player");
    jw_open(w, '{');
    jw_key(w, "gold");
    jw_int(w, ps(pj, 1));
    jw_key(w, "lumber");
    jw_int(w, ps(pj, 2));
    jw_key(w, "food_used");
    jw_int(w, ps(pj, 5));
    jw_key(w, "food_cap");
    jw_int(w, ps(pj, 4));
    jw_close(w, '}');
    jw_key(w, "units");
    jw_open(w, '[');
    jw_splice(w, &o.own);
    jw_close(w, ']');
    jw_key(w, "inside");
    jw_open(w, '[');
    jw_splice(w, &o.inside);
    jw_close(w, ']');
    jw_key(w, "visible_enemies");
    jw_open(w, '[');
    jw_splice(w, &o.others);
    jw_close(w, ']');
    jw_key(w, "items");
    jw_open(w, '[');
    jw_splice(w, &o.items);
    jw_close(w, ']');
    jw_key(w, "inventory");
    jw_open(w, '[');
    jw_splice(w, &o.inventory);
    jw_close(w, ']');
    jw_key(w, "events");
    jw_open(w, '[');
    unsigned lost = emit_events(w, player);
    jw_close(w, ']');
    jw_key(w, "events_lost");
    jw_uint(w, lost);
    jw_key(w, "chat");
    if (pj == NATIVE(RVA_N_LOCAL_PLAYER, NativeI_V)())
        chat_write_json(w);
    else {
        jw_open(w, '[');
        jw_close(w, ']');
    }
    ((EnumObjectsFn)(g_base + RVA_ENUM_OBJECTS))(DESTRUCTABLE_CLASS, (void *)obs_destructable_cb, &o, 0);
    jw_key(w, "destructables");
    jw_open(w, '[');
    jw_splice(w, &o.destructables);
    jw_close(w, ']');
    jw_key(w, "result");
    jw_string(w, player_result(player));
    jw_close(w, '}');
}

/* ---- the game result, per player ---------------------------------------------------------------
 * Blizzard's melee script ends a player's game with RemovePlayer(player, PLAYER_GAME_RESULT_*)
 * (MeleeDoVictoryEnum, MeleeDoDefeat), right after it sets bj_meleeVictoried / bj_meleeDefeated.
 * The native is detoured: the result is recorded for that player and, for slot 0 (the local
 * player), the RPC's lifecycle moves to `ended`. The step finishes after GameUpdate returns,
 * so both players' results and the final turn are complete before observation. Result handles
 * are the enum values: 0 victory, 1 defeat, 2 tie. */
static char g_result_of[16][8];
void results_clear(void) {
    memset(g_result_of, 0, sizeof g_result_of);
}
const char *player_result(int jass_id) {
    return jass_id >= 0 && jass_id < 16 ? g_result_of[jass_id] : "";
}
int agents_finished(void) {
    int agents = 0;
    for (int p = 0; p < 16; p++)
        if (player_is_agent(p)) {
            agents++;
            if (!g_result_of[p][0])
                return 0;
        }
    return agents != 0;
}
static void result_set(int jass_id, const char *result) {
    if (g_status == ST_LAUNCHED)
        return; /* ignore startup and teardown callbacks */
    if (jass_id < 0 || jass_id >= 16 || g_result_of[jass_id][0])
        return;
    strncpy(g_result_of[jass_id], result, sizeof g_result_of[jass_id] - 1);
    hook_log("result: player %d %s (gametime %lu)", jass_id, result, game_time());
    if (agents_finished()) {
        rpc_set_ended();
        step_end_after_turn();
    }
}
typedef void(__cdecl *RemovePlayerFn)(int player, int result);
typedef BYTE *(__cdecl *PlayerObjectFn)(int player);
static RemovePlayerFn RemovePlayer_orig;
static void __cdecl RemovePlayer_hook(int player, int result) {
    BYTE *pl = ((PlayerObjectFn)(g_base + RVA_PLAYER_OBJECT))(player);
    if (pl && result >= 0 && result <= 2)
        result_set(player_jass_id(pl), result == 0 ? "victory" : result == 1 ? "defeat" : "draw");
    RemovePlayer_orig(player, result);
}
void result_init_hooks(void) {
    MH_STATUS s = MH_CreateHook(g_base + RVA_N_REMOVEPLAYER, (void *)RemovePlayer_hook, (void **)&RemovePlayer_orig);
    hook_log("RemovePlayer hook: %s", MH_StatusToString(s));
}
