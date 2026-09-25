/* Diagnostics over the pipe: scan, dump, hardware write-watch, game-thread profile, every thread's stack; exit and
 * dialog logging. */
#include "wc3hook.h"

int g_watch_skip_game, g_watch_hits;

/* the exception filter for game code called from the DLL: the fault and the stack to the log */
DWORD g_exc_addr;
int native_exc(EXCEPTION_POINTERS *e) {
    g_exc_addr = (DWORD)e->ExceptionRecord->ExceptionAddress;
    hook_log("native exception %08lx at rva %lx", e->ExceptionRecord->ExceptionCode, g_exc_addr - (DWORD)g_base);
    log_stack_from("  stack", (DWORD *)e->ContextRecord->Ebp);
    return EXCEPTION_EXECUTE_HANDLER;
}

/* ---- "scan <hex>": find a byte pattern in every committed, writable region; hits to the log
 * and the pipe (command-queue hunt: where does a clicked order's unit id pair land?) */
void mem_scan(const char *hex) {
    BYTE pat[64];
    int n = 0;
    for (; n < 64 && hex[2 * n] && hex[2 * n + 1]; n++) {
        unsigned v;
        sscanf(hex + 2 * n, "%2x", &v);
        pat[n] = (BYTE)v;
    }
    if (!n)
        return;
    MEMORY_BASIC_INFORMATION mi;
    BYTE *p = NULL;
    int hits = 0;
    char b[160];
    while (VirtualQuery(p, &mi, sizeof mi) == sizeof mi && hits < 40) {
        BYTE *base = (BYTE *)mi.BaseAddress;
        SIZE_T sz = mi.RegionSize;
        if (mi.State == MEM_COMMIT && (mi.Protect & (PAGE_READWRITE | PAGE_EXECUTE_READWRITE)) &&
            !(mi.Protect & PAGE_GUARD)) {
            __try { /* the game runs meanwhile: a region can go away between the query and the read */
                for (BYTE *q = base; q + n <= base + sz && hits < 40; q++) {
                    if (*q == pat[0] && memcmp(q, pat, n) == 0) {
                        _snprintf(b, sizeof b, "scan hit=%p region=%p+%x type=%s", q, base, (unsigned)sz,
                                  mi.Type == MEM_PRIVATE ? "private"
                                  : mi.Type == MEM_IMAGE ? "image"
                                                         : "mapped");
                        hook_log("%s", b);
                        pipe_send(b);
                        hits++;
                        q += n - 1;
                    }
                }
            } __except (EXCEPTION_EXECUTE_HANDLER) {
            }
        }
        p = base + sz;
        if (p < base)
            break;
    }
    _snprintf(b, sizeof b, "scan done hits=%d", hits);
    hook_log("%s", b);
    pipe_send(b);
}

/* ---- "dump <hex addr> <n>": n bytes as hex, 32 per line */
void mem_dump(DWORD addr, int n) {
    char b[160];
    for (int off = 0; off < n; off += 32) {
        int k = 0, len = n - off < 32 ? n - off : 32;
        k += _snprintf(b + k, sizeof b - k, "dump %08lx ", addr + off);
        if (IsBadReadPtr((void *)(addr + off), len)) {
            strcpy(b + k, "??");
        } else
            for (int i = 0; i < len; i++)
                k += _snprintf(b + k, sizeof b - k, "%02x", ((BYTE *)addr)[off + i]);
        hook_log("%s", b);
        pipe_send(b);
    }
}

/* ---- "watch <hex addr>": hardware write breakpoint (DR0, 4 bytes) on the game thread; the first
 * hit logs the writer's stack and disarms. For finding who appends to the command stream. */
static DWORD g_watch_addr;
static LONG CALLBACK watch_veh(EXCEPTION_POINTERS *e) {
    if (e->ExceptionRecord->ExceptionCode != EXCEPTION_SINGLE_STEP)
        return EXCEPTION_CONTINUE_SEARCH;
    CONTEXT *c = e->ContextRecord;
    if (!g_watch_addr) { /* another thread already reported; just disarm this one */
        if (!(c->Dr7 & 1))
            return EXCEPTION_CONTINUE_SEARCH;
        c->Dr7 = 0;
        c->Dr0 = 0;
        return EXCEPTION_CONTINUE_EXECUTION;
    }
    if (g_watch_skip_game && GetCurrentThreadId() == g_game_tid)
        return EXCEPTION_CONTINUE_EXECUTION; /* keep waiting for another thread */
    hook_log("watch hit: write to %08lx from rva %lx on thread %lu%s", g_watch_addr,
             (DWORD)e->ExceptionRecord->ExceptionAddress - (DWORD)g_base, GetCurrentThreadId(),
             GetCurrentThreadId() == g_game_tid ? " (game thread)" : "");
    log_stack_from("  stack", (DWORD *)c->Ebp);
    if (--g_watch_hits > 0)
        return EXCEPTION_CONTINUE_EXECUTION; /* keep it armed for the next writer */
    {
        char b[64];
        _snprintf(b, sizeof b, "watch hit rva=%lx", (DWORD)e->ExceptionRecord->ExceptionAddress - (DWORD)g_base);
        pipe_send(b);
    }
    c->Dr7 = 0;
    c->Dr0 = 0;
    g_watch_addr = 0;
    return EXCEPTION_CONTINUE_EXECUTION;
}
static int watch_thread(HANDLE t, DWORD addr) {
    SuspendThread(t);
    CONTEXT c;
    c.ContextFlags = CONTEXT_DEBUG_REGISTERS;
    int ok = GetThreadContext(t, &c);
    if (ok) {
        c.Dr0 = addr;
        c.Dr7 = addr ? (1 /* L0 */ | (1 << 16) /* write */ | (3 << 18) /* 4 bytes */) : 0;
        ok = SetThreadContext(t, &c);
    }
    ResumeThread(t);
    return ok;
}
/* armed on every thread of the process (the writer may be a network thread) */
int watch_set(DWORD addr) {
    static int veh_added;
    if (!veh_added) {
        AddVectoredExceptionHandler(1, watch_veh);
        veh_added = 1;
    }
    DWORD me = GetCurrentThreadId(), pid = GetCurrentProcessId();
    int n = 0;
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    THREADENTRY32 te = {sizeof te};
    if (snap == INVALID_HANDLE_VALUE || !Thread32First(snap, &te))
        return 0;
    g_watch_addr = addr;
    do {
        if (te.th32OwnerProcessID != pid || te.th32ThreadID == me)
            continue;
        HANDLE t = OpenThread(THREAD_ALL_ACCESS, FALSE, te.th32ThreadID);
        if (!t)
            continue;
        n += watch_thread(t, addr);
        CloseHandle(t);
    } while (Thread32Next(snap, &te));
    CloseHandle(snap);
    hook_log("watch %08lx: armed on %d threads", addr, n);
    return n > 0;
}

/* ---- "profile <n>": sample the game thread n times (1 ms apart) and histogram which call site
 * of the frame loop (0x445630..0x445a30) is on the stack, i.e. where frame time goes (render off). */
void profile(int n, DWORD lo, DWORD hi) {
    DWORD sites[64];
    int counts[64], nsites = 0, none = 0, inexe = 0;
    HANDLE t = g_game_tid ? OpenThread(THREAD_ALL_ACCESS, FALSE, g_game_tid) : NULL;
    if (!t) {
        hook_log("profile: no game thread yet");
        return;
    }
    for (int i = 0; i < n; i++) {
        Sleep_orig(1);
        if (SuspendThread(t) == (DWORD)-1)
            continue;
        CONTEXT c;
        c.ContextFlags = CONTEXT_CONTROL;
        DWORD site = 0;
        if (GetThreadContext(t, &c)) {
            /* scan the raw stack rather than the ebp chain: not every frame keeps a frame pointer */
            DWORD rva = c.Eip - (DWORD)g_base;
            if (rva < 0x1000000)
                inexe++;
            DWORD *sp = (DWORD *)(c.Esp & ~3);
            if (i < 3) {
                char l[512];
                int n = _snprintf(l, sizeof l, "  sample %d eip rva %lx stack:", i, c.Eip - (DWORD)g_base);
                for (int d = 0; d < 2048 && n < 480; d++) {
                    if (IsBadReadPtr(sp + d, 4))
                        break;
                    rva = sp[d] - (DWORD)g_base;
                    if (rva < 0x1000000 && rva > 0x1000)
                        n += _snprintf(l + n, sizeof l - n, " %lx", rva);
                }
                hook_log("%s", l);
            }
            for (int d = 0; d < 2048 && !site; d++) {
                if (IsBadReadPtr(sp + d, 4))
                    break;
                rva = sp[d] - (DWORD)g_base;
                if (rva >= lo && rva < hi)
                    site = rva;
            }
        }
        ResumeThread(t);
        if (!site) {
            none++;
            continue;
        }
        int k;
        for (k = 0; k < nsites && sites[k] != site; k++)
            ;
        if (k == nsites && nsites < 64) {
            sites[nsites] = site;
            counts[nsites++] = 0;
        }
        if (k < 64)
            counts[k]++;
    }
    CloseHandle(t);
    hook_log("profile %lx..%lx: %d samples, %d outside, eip in exe %d", lo, hi, n, none, inexe);
    for (int k = 0; k < nsites; k++)
        hook_log("  site %06lx: %d", sites[k], counts[k]);
}

/* ---- "where": log every thread's stack (module+offset) to see what a stalled copy waits on -- */
void log_addr(const char *prefix, DWORD addr) {
    HMODULE m = NULL;
    char name[MAX_PATH] = "?";
    if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           (LPCSTR)addr, &m) &&
        m) {
        GetModuleFileNameA(m, name, MAX_PATH);
        char *s = strrchr(name, '\\');
        if (s)
            memmove(name, s + 1, strlen(s));
        hook_log("%s%s+%x", prefix, name, addr - (DWORD)m);
    } else
        hook_log("%s%08x", prefix, addr);
}
/* stack of the calling thread via the ebp chain (the exe keeps frame pointers) */
void log_stack_from(const char *label, DWORD *frame) {
    hook_log("%s:", label);
    for (int i = 0; i < 24 && frame; i++) {
        if (IsBadReadPtr(frame, 8))
            break;
        DWORD ret = frame[1];
        if (!ret)
            break;
        log_addr("      ", ret);
        DWORD *next = (DWORD *)frame[0];
        if (next <= frame)
            break;
        frame = next;
    }
}
void log_stack_here(const char *label) {
    DWORD *frame;
    __asm { mov frame, ebp }
    log_stack_from(label, frame);
}
void where(void) {
    DWORD me = GetCurrentThreadId(), pid = GetCurrentProcessId();
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    THREADENTRY32 te = {sizeof te};
    if (snap == INVALID_HANDLE_VALUE || !Thread32First(snap, &te))
        return;
    do {
        if (te.th32OwnerProcessID != pid || te.th32ThreadID == me)
            continue;
        HANDLE t = OpenThread(THREAD_ALL_ACCESS, FALSE, te.th32ThreadID);
        if (!t)
            continue;
        if (SuspendThread(t) != (DWORD)-1) {
            CONTEXT c;
            c.ContextFlags = CONTEXT_CONTROL;
            if (GetThreadContext(t, &c)) {
                hook_log("thread %lu:", te.th32ThreadID);
                log_addr("  eip ", c.Eip);
                DWORD ebp = c.Ebp;
                for (int i = 0; i < 24 && ebp; i++) {
                    DWORD ret;
                    if (IsBadReadPtr((void *)ebp, 8))
                        break;
                    ret = *(DWORD *)(ebp + 4);
                    if (!ret)
                        break;
                    log_addr("      ", ret);
                    DWORD next = *(DWORD *)ebp;
                    if (next <= ebp)
                        break;
                    ebp = next;
                }
            }
            ResumeThread(t);
        }
        CloseHandle(t);
    } while (Thread32Next(snap, &te));
    CloseHandle(snap);
}

typedef VOID(WINAPI *ExitProcessFn)(UINT);
typedef int(WINAPI *MessageBoxAFn)(HWND, LPCSTR, LPCSTR, UINT);
typedef int(WINAPI *MessageBoxWFn)(HWND, LPCWSTR, LPCWSTR, UINT);
static ExitProcessFn ExitProcess_orig;
MessageBoxAFn MessageBoxA_orig;
static MessageBoxWFn MessageBoxW_orig;

static VOID WINAPI ExitProcess_hook(UINT code) {
    hook_log("ExitProcess code=%u at gametime %lu", code, game_time());
    log_stack_here("ExitProcess stack");
    ExitProcess_orig(code);
}
static int WINAPI MessageBoxA_hook(HWND w, LPCSTR text, LPCSTR cap, UINT t) {
    hook_log("MessageBoxA [%s] %s", cap ? cap : "", text ? text : "");
    return MessageBoxA_orig(w, text, cap, t);
}
static int WINAPI MessageBoxW_hook(HWND w, LPCWSTR text, LPCWSTR cap, UINT t) {
    hook_log("MessageBoxW [%S] %S", cap ? cap : L"", text ? text : L"");
    return MessageBoxW_orig(w, text, cap, t);
}

void diag_init_hooks(void) {
    MH_CreateHookApi(L"user32", "MessageBoxA", (void *)MessageBoxA_hook, (void **)&MessageBoxA_orig);
    MH_CreateHookApi(L"user32", "MessageBoxW", (void *)MessageBoxW_hook, (void **)&MessageBoxW_orig);
    MH_CreateHookApi(L"kernel32", "ExitProcess", (void *)ExitProcess_hook, (void **)&ExitProcess_orig);
}
