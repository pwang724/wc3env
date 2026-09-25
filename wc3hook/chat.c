/* Chat the person at the window types, for the local player's observation.
 *
 * Read from the game window's own key messages (instance.c's window procedure), so it needs no game
 * internals: Enter opens the chat box, typed characters and Backspace edit the line, Enter sends it,
 * Escape cancels. Pasted text, cursor movement inside the box and non-ASCII characters are not seen.
 * Sent lines queue until the local player's next observation (docs/specs/observations.md, `chat`);
 * the window thread writes, the game thread reads, so a lock guards both.
 */
#include "wc3hook.h"

#define CHAT_LINES 16
#define CHAT_LEN 256

static SRWLOCK g_chat_lock = SRWLOCK_INIT;
static char g_typing[CHAT_LEN]; /* the line in the open chat box */
static int g_typed, g_open;
static char g_sent[CHAT_LINES][CHAT_LEN]; /* sent lines not yet observed, oldest first */
static int g_count;

void chat_key(UINT msg, WPARAM wp) {
    if (msg != WM_CHAR)
        return;
    AcquireSRWLockExclusive(&g_chat_lock);
    if (wp == '\r') {
        if (g_open && g_typed && g_count < CHAT_LINES) {
            g_typing[g_typed] = 0;
            memcpy(g_sent[g_count++], g_typing, g_typed + 1);
        }
        g_open = !g_open;
        g_typed = 0;
    } else if (wp == 0x1b) {
        g_open = g_typed = 0;
    } else if (g_open && wp == '\b') {
        if (g_typed)
            g_typed--;
    } else if (g_open && wp >= 0x20 && wp < 0x7f && g_typed < CHAT_LEN - 1) {
        g_typing[g_typed++] = (char)wp;
    }
    ReleaseSRWLockExclusive(&g_chat_lock);
}

/* The lines sent since the last call, as a JSON array of strings; the queue empties. Game thread. */
void chat_write_json(JW *w) {
    AcquireSRWLockExclusive(&g_chat_lock);
    jw_open(w, '[');
    for (int i = 0; i < g_count; i++)
        jw_string(w, g_sent[i]);
    jw_close(w, ']');
    g_count = 0;
    ReleaseSRWLockExclusive(&g_chat_lock);
}
