/* The binary observation (docs/specs/observations.md#binary-observations): the same facts as the JSON
 * observation, written as fixed-size records into a shared memory mapping that the host reads in place
 * (numpy structured arrays), as BWAPI's client reads its GameData. Every field is 4 bytes, so records
 * have no padding. src/wc3env/binary.py mirrors these layouts; change both together. */
#pragma once
#include <windows.h>
#include <stdlib.h>
#include <string.h>

#define OBS_MAGIC 0x31424f57 /* "WOB1" little-endian */
#define OBS_VERSION 1
#define OBS_MAP_BYTES (16u << 20)

enum { T_UNITS, T_ABILITIES, T_BUFFS, T_QUEUE, T_INVENTORY, T_ITEMS, T_DESTRUCTABLES, T_EVENTS, OBS_TABLES };

typedef struct {
    DWORD offset, count, record_size; /* offset from the header's first byte */
} BinTable;
typedef struct {
    DWORD magic, version, size, player, sequence, game_time_ms;
    int gold, lumber, food_used, food_cap;
    DWORD result; /* 0 none, 1 victory, 2 defeat, 3 draw */
    DWORD events_lost;
    int score[25]; /* PLAYER_SCORE_* order, as SCORE_FIELDS */
    DWORD tables;
    BinTable table[OBS_TABLES];
} BinHeader;

#define BU_OWN 1
#define BU_STRUCTURE 2
#define BU_HERO 4
#define BU_INSIDE 8 /* the observer's unit inside a mine, building or transport */
typedef struct {
    DWORD unit_id, type_id;
    int owner;
    float x, y, hp, max_hp, mana, max_mana;
    DWORD flags;
    int level;
    DWORD order_id;     /* 0 when idle; a build order's id is the structure's type id */
    DWORD order_target; /* object id, 0xffffffff for a point order */
    float order_x, order_y;
    DWORD state; /* structures: 0 none, 1 constructing, 2 upgrading */
    float state_seconds, queue_seconds;
} BinUnit;
typedef struct {
    DWORD unit_id, ability_id;
    int level, mana_cost;
    float cooldown_seconds, cooldown_remaining;
} BinAbility;
typedef struct {
    DWORD unit_id, buff_id;
} BinBuff;
typedef struct {
    DWORD unit_id, slot, type_id; /* slot 0 is in progress */
} BinQueue;
typedef struct {
    DWORD unit_id, slot, type_id;
    int charges;
} BinInventory;
typedef struct {
    DWORD item_id, type_id;
    float x, y;
} BinItem;
#define BD_LUMBER 1
#define BD_INVULNERABLE 2
typedef struct {
    DWORD id, type_id;
    float x, y, hp;
    DWORD flags;
} BinDestructable;
typedef struct {
    DWORD kind;     /* the engine event id: 20 death, 26 construct_start, ... (binary.py EVENT_KINDS) */
    DWORD unit_id;  /* the JSON event's unit_id */
    DWORD other_id; /* trained_id, summoned_id, item_id, buyer_id or attacker_id; 0 if none or hidden */
    DWORD type_id;  /* type_id or ability_id; 0 if none or hidden */
    int value;      /* death: owner; hero_level: level */
} BinEvent;

/* src/wc3env/binary.py's dtypes have these sizes; tests/unit/test_binary.py checks its side */
typedef char bin_sizes_match[sizeof(BinHeader) == 248 && sizeof(BinUnit) == 72 && sizeof(BinAbility) == 24 &&
                                     sizeof(BinBuff) == 8 && sizeof(BinQueue) == 12 && sizeof(BinInventory) == 16 &&
                                     sizeof(BinItem) == 16 && sizeof(BinDestructable) == 24 && sizeof(BinEvent) == 20
                                 ? 1
                                 : -1];

/* A growable array of records, one per table. */
typedef struct {
    BYTE *p;
    size_t n, cap;
} BB;
static void bb_push(BB *b, const void *record, size_t size) {
    if (b->n + size > b->cap) {
        size_t cap = b->cap ? b->cap * 2 : 1 << 14;
        while (cap < b->n + size)
            cap *= 2;
        BYTE *p = (BYTE *)realloc(b->p, cap);
        if (!p)
            abort(); /* never continue with a partial observation after allocation failure */
        b->p = p;
        b->cap = cap;
    }
    memcpy(b->p + b->n, record, size);
    b->n += size;
}
