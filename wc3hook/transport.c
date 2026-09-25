/* The named pipe to the host. A line that starts with '{' is an RPC request (rpc.c); anything
 * else is a diagnostic command from the table below: reverse-engineering tools whose output goes
 * to the log and, as `scan ...` / `dump ...` / `watch ...` / `queued ...` lines, to the pipe. */
#include "wc3hook.h"

HANDLE g_pipe = INVALID_HANDLE_VALUE;
CRITICAL_SECTION g_lock;

/* The pipe is overlapped: a synchronous ReadFile pending on the reader thread would otherwise
 * block every WriteFile from the game thread (non-overlapped pipe handles serialise I/O). */
static BOOL ov_write(HANDLE h, const void *data, DWORD len) {
    OVERLAPPED ov = {0};
    DWORD n = 0;
    ov.hEvent = CreateEventA(NULL, TRUE, FALSE, NULL);
    if (!ov.hEvent)
        return FALSE;
    BOOL ok = WriteFile(h, data, len, &n, &ov);
    if (!ok && GetLastError() == ERROR_IO_PENDING) {
        if (real_wait(ov.hEvent, 5000) != WAIT_OBJECT_0) {
            CancelIoEx(h, &ov);
            real_wait(ov.hEvent, INFINITE); /* keep the buffer alive until cancellation completes */
            ok = FALSE;
        } else
            ok = GetOverlappedResult(h, &ov, &n, FALSE);
    }
    CloseHandle(ov.hEvent);
    return ok && n == len;
}

void pipe_send(const char *line) {
    EnterCriticalSection(&g_lock);
    if (g_pipe != INVALID_HANDLE_VALUE) {
        /* never a zero-length write: on a byte-mode pipe it reaches the client as a successful
         * zero-byte read, which looks like end of stream */
        if ((line[0] && !ov_write(g_pipe, line, (DWORD)strlen(line))) || !ov_write(g_pipe, "\n", 1)) {
            hook_log("pipe: client stopped reading; disconnecting");
            DisconnectNamedPipe(g_pipe);
        }
    }
    LeaveCriticalSection(&g_lock);
}

/* ---- diagnostic commands: a handler gets the text after the command word ("" for a bare word) */
static void cmd_ping(const char *a) {
    pipe_send("pong");
}
static void cmd_echo(const char *a) {
    pipe_send(a);
}
static void cmd_info(const char *a) {
    char b[256];
    _snprintf(b, sizeof b, "info pid=%lu base=%p stepmode=%ld gametime=%lu game=%p speed=%.0f frozen=%ld floor=%ld",
              GetCurrentProcessId(), g_base, g_stepmode, game_time(), g_game, g_speed, g_frozen, g_wait_floor);
    pipe_send(b);
}
static void cmd_where(const char *a) {
    where();
    pipe_send("ok");
}
static void cmd_pktlog(const char *a) {
    g_pktlog = atoi(a);
    pipe_send("ok");
}
static void cmd_scan(const char *a) {
    mem_scan(a);
    pipe_send("ok");
}
static void cmd_dump(const char *a) {
    DWORD addr = 0;
    int n = 64;
    sscanf(a, "%lx %d", &addr, &n);
    mem_dump(addr, n);
    pipe_send("ok");
}
static void cmd_trace(const char *a) {
    g_trace = atoi(a);
    g_trace_n = 0;
    pipe_send("ok");
}
static void cmd_watch(const char *a) {
    DWORD addr = 0;
    g_watch_skip_game = 0;
    g_watch_hits = 1;
    sscanf(a, "%lx %d %d", &addr, &g_watch_skip_game, &g_watch_hits);
    pipe_send(watch_set(addr) ? "ok" : "err watch");
}
static void cmd_profile(const char *a) {
    DWORD lo = FRAME_LOOP_LO, hi = FRAME_LOOP_HI;
    int n = 0;
    sscanf(a, "%d %lx %lx", &n, &lo, &hi);
    profile(n, lo, hi);
    pipe_send("ok");
}

static const struct {
    const char *name;
    void (*fn)(const char *arg);
} COMMANDS[] = {
    {"ping", cmd_ping}, {"echo", cmd_echo}, {"info", cmd_info},   {"where", cmd_where}, {"pktlog", cmd_pktlog},
    {"scan", cmd_scan}, {"dump", cmd_dump}, {"trace", cmd_trace}, {"watch", cmd_watch}, {"profile", cmd_profile},
};

static void handle_line(char *line) {
    if (line[0] == '{') {
        rpc_handle(line);
        return;
    }
    for (size_t i = 0; i < sizeof COMMANDS / sizeof COMMANDS[0]; i++) {
        size_t n = strlen(COMMANDS[i].name);
        if (strncmp(line, COMMANDS[i].name, n) == 0 && (line[n] == 0 || line[n] == ' ')) {
            COMMANDS[i].fn(line[n] == ' ' ? line + n + 1 : line + n);
            return;
        }
    }
    rpc_handle(line); /* neither: the RPC answers `bad_request` in JSON */
}

DWORD WINAPI pipe_thread(LPVOID arg) {
    char name[128];
    _snprintf(name, sizeof name, "\\\\.\\pipe\\wc3hook-%lu", GetCurrentProcessId());
    for (;;) {
        HANDLE h = CreateNamedPipeA(name, PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED,
                                    PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT, 1, 1 << 20, 1 << 20, 0, NULL);
        if (h == INVALID_HANDLE_VALUE) {
            hook_log("pipe: create failed %lu", GetLastError());
            return 1;
        }
        hook_log("pipe: waiting on %s", name);
        OVERLAPPED cov = {0};
        cov.hEvent = CreateEventA(NULL, TRUE, FALSE, NULL);
        BOOL connected = ConnectNamedPipe(h, &cov);
        DWORD err = GetLastError();
        if (!connected && err == ERROR_IO_PENDING) {
            WaitForSingleObject(cov.hEvent, INFINITE);
            connected = TRUE;
        } else if (!connected && err == ERROR_PIPE_CONNECTED)
            connected = TRUE;
        CloseHandle(cov.hEvent);
        if (!connected) {
            hook_log("pipe: connect failed %lu", err);
            CloseHandle(h);
            continue;
        }
        hook_log("pipe: client connected");
        EnterCriticalSection(&g_lock);
        g_pipe = h;
        LeaveCriticalSection(&g_lock);
        static char buf[1 << 16];
        int used = 0;
        DWORD got;
        OVERLAPPED rov = {0};
        rov.hEvent = CreateEventA(NULL, TRUE, FALSE, NULL);
        for (;;) {
            got = 0;
            BOOL ok = ReadFile(h, buf + used, sizeof buf - used - 1, &got, &rov);
            if (!ok && GetLastError() == ERROR_IO_PENDING)
                ok = GetOverlappedResult(h, &rov, &got, TRUE);
            if (!ok || !got)
                break;
            used += got;
            buf[used] = 0;
            char *start = buf, *nl;
            while ((nl = strchr(start, '\n'))) {
                *nl = 0;
                if (nl > start && nl[-1] == '\r')
                    nl[-1] = 0;
                handle_line(start);
                start = nl + 1;
            }
            used = (int)strlen(start);
            memmove(buf, start, used + 1);
        }
        CloseHandle(rov.hEvent);
        hook_log("pipe: client gone");
        EnterCriticalSection(&g_lock);
        g_pipe = INVALID_HANDLE_VALUE;
        LeaveCriticalSection(&g_lock);
        DisconnectNamedPipe(h);
        CloseHandle(h);
    }
}
