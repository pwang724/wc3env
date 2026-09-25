/* Several games on one machine: isolated instance state and background window/input policy. */
#include "wc3hook.h"

/* ---- single-instance defeat --------------------------------------------------------------
 * WC3 guards against a second copy with a named mutex (CreateMutexA, then ERROR_ALREADY_EXISTS,
 * and OpenMutexA). Per-process-unique names make every instance believe it is the first, so N
 * copies run at once. Names are mangled with the pid; unnamed mutexes pass through. */
typedef HANDLE(WINAPI *CreateMutexFn)(LPSECURITY_ATTRIBUTES, BOOL, LPCSTR);
typedef HANDLE(WINAPI *OpenMutexFn)(DWORD, BOOL, LPCSTR);
CreateMutexFn CreateMutex_orig;
OpenMutexFn OpenMutex_orig;

const char *mangle(const char *name, char *buf, int cap) {
    if (!name || strcmp(name, "Warcraft III Game Application"))
        return name;
    _snprintf(buf, cap, "%s-wc3hook%lu", name, GetCurrentProcessId());
    return buf;
}
static HANDLE WINAPI CreateMutex_hook(LPSECURITY_ATTRIBUTES sa, BOOL owner, LPCSTR name) {
    char buf[512];
    if (name)
        hook_log("CreateMutexA name=%s", name);
    return CreateMutex_orig(sa, owner, mangle(name, buf, sizeof buf));
}
static HANDLE WINAPI OpenMutex_hook(DWORD access, BOOL inherit, LPCSTR name) {
    char buf[512];
    if (name)
        hook_log("OpenMutexA name=%s", name);
    return OpenMutex_orig(access, inherit, mangle(name, buf, sizeof buf));
}

typedef HANDLE(WINAPI *CreateEventAFn)(LPSECURITY_ATTRIBUTES, BOOL, BOOL, LPCSTR);
static CreateEventAFn CreateEventA_orig;
/* second single-instance guard (RVA 0x2e9ff -> 0x397c80): CreateEventA("Warcraft III Game
 * Application") + ERROR_ALREADY_EXISTS; same cure as the mutex */
static HANDLE WINAPI CreateEventA_hook(LPSECURITY_ATTRIBUTES sa, BOOL manual, BOOL init, LPCSTR name) {
    char buf[512];
    return CreateEventA_orig(sa, manual, init, mangle(name, buf, sizeof buf));
}

/* ---- per-instance user folder ---------------------------------------------------------------
 * Instances share Documents\Warcraft III and fight over exclusive files there (TempReplay.w3g
 * was the killer: ERROR_SHARING_VIOLATION). With WC3HOOK_DOCS set, Documents lookups
 * return that directory instead, so each instance gets its own "Documents" and, via the
 * game's own logic, its own Warcraft III\ tree. */
static wchar_t g_docs[MAX_PATH];
typedef HRESULT(WINAPI *SHGetFolderPathWFn)(HWND, int, HANDLE, DWORD, LPWSTR);
typedef HRESULT(WINAPI *SHGetKnownFolderPathFn)(const GUID *, DWORD, HANDLE, PWSTR *);
static SHGetFolderPathWFn SHGetFolderPathW_orig;
static SHGetKnownFolderPathFn SHGetKnownFolderPath_orig;
static HRESULT WINAPI SHGetFolderPathW_hook(HWND w, int csidl, HANDLE tok, DWORD flags, LPWSTR out) {
    HRESULT hr = SHGetFolderPathW_orig(w, csidl, tok, flags, out);
    if ((csidl & 0xff) == 5 && g_docs[0] && SUCCEEDED(hr)) {
        wcsncpy(out, g_docs, MAX_PATH - 1);
        out[MAX_PATH - 1] = 0;
    }
    return hr;
}
static HRESULT WINAPI SHGetKnownFolderPath_hook(const GUID *id, DWORD flags, HANDLE tok, PWSTR *out) {
    HRESULT hr = SHGetKnownFolderPath_orig(id, flags, tok, out);
    /* FOLDERID_Documents, kept local to avoid linking all shell GUID definitions. */
    static const GUID documents = {0xfdd39ad0, 0x238f, 0x46af, {0xad, 0xb4, 0x6c, 0x85, 0x48, 0x03, 0x69, 0xc7}};
    if (id && !memcmp(id, &documents, sizeof documents) && SUCCEEDED(hr) && out && *out) {
        if (g_docs[0]) {
            size_t n = (wcslen(g_docs) + 1) * sizeof(wchar_t);
            PWSTR p = (PWSTR)CoTaskMemAlloc(n);
            if (!p) {
                CoTaskMemFree(*out);
                *out = NULL;
                return E_OUTOFMEMORY;
            }
            memcpy(p, g_docs, n);
            CoTaskMemFree(*out);
            *out = p;
        }
    }
    return hr;
}
void docs_init_hooks(void) {
    GetEnvironmentVariableW(L"WC3HOOK_DOCS", g_docs, MAX_PATH);
    if (g_docs[0]) {
        CreateDirectoryW(g_docs, NULL);
        hook_log("user folder redirected to %S", g_docs);
    }
    MH_CreateHookApi(L"shell32", "SHGetFolderPathW", (void *)SHGetFolderPathW_hook, (void **)&SHGetFolderPathW_orig);
    MH_CreateHookApi(L"shell32", "SHGetKnownFolderPath", (void *)SHGetKnownFolderPath_hook,
                     (void **)&SHGetKnownFolderPath_orig);
}

/* ---- background windows --------------------------------------------------------------------
 * Warcraft must believe it has focus to keep simulating, but must not acquire real focus or
 * touch the user's cursor. Install before startup: moving the window after launch is too late.
 * Offscreen, every key and click is swallowed. Visible, the window sits quietly until the user
 * clicks it: then it takes focus like any window and input reaches Warcraft, while clicking away
 * still never pauses the game. Interactive mode leaves all of this to Warcraft. */
static HWND g_hwnd;
static WNDPROC g_wndproc_orig;
typedef HWND(WINAPI *GetHwndFn)(void);
static GetHwndFn GetForegroundWindow_orig, GetActiveWindow_orig, GetFocus_orig;
typedef HWND(WINAPI *CreateWindowExAFn)(DWORD, LPCSTR, LPCSTR, DWORD, int, int, int, int, HWND, HMENU, HINSTANCE,
                                        LPVOID);
typedef BOOL(WINAPI *ShowWindowFn)(HWND, int);
typedef BOOL(WINAPI *SetWindowPosFn)(HWND, HWND, int, int, int, int, UINT);
static CreateWindowExAFn CreateWindowExA_orig;
static ShowWindowFn ShowWindow_orig;
static SetWindowPosFn SetWindowPos_orig;

static int background_visible(void) {
    char value[8] = {0};
    GetEnvironmentVariableA("WC3HOOK_BACKGROUND_VISIBLE", value, sizeof value);
    return !strcmp(value, "1");
}

static int offscreen_x(int width) {
    return GetSystemMetrics(SM_XVIRTUALSCREEN) - (width > 0 ? width : GetSystemMetrics(SM_CXVIRTUALSCREEN)) - 1;
}
static int offscreen_y(int height) {
    return GetSystemMetrics(SM_YVIRTUALSCREEN) - (height > 0 ? height : GetSystemMetrics(SM_CYVIRTUALSCREEN)) - 1;
}

static LRESULT CALLBACK wndproc_hook(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    if (background_visible())
        chat_key(msg, wp);
    switch (msg) {
    case WM_ACTIVATEAPP:
        wp = TRUE;
        break;
    case WM_ACTIVATE:
        if (LOWORD(wp) == WA_INACTIVE)
            return 0;
        break;
    case WM_KILLFOCUS:
        return 0;
    case WM_NCACTIVATE:
        wp = TRUE;
        break;
    case WM_MOUSEACTIVATE:
        return background_visible() ? MA_ACTIVATE : MA_NOACTIVATEANDEAT;
    case WM_KEYDOWN:
    case WM_KEYUP:
    case WM_CHAR:
    case WM_SYSKEYDOWN:
    case WM_SYSKEYUP:
    case WM_SYSCHAR:
    case WM_MOUSEMOVE:
    case WM_LBUTTONDOWN:
    case WM_LBUTTONUP:
    case WM_LBUTTONDBLCLK:
    case WM_RBUTTONDOWN:
    case WM_RBUTTONUP:
    case WM_RBUTTONDBLCLK:
    case WM_MBUTTONDOWN:
    case WM_MBUTTONUP:
    case WM_MBUTTONDBLCLK:
    case WM_MOUSEWHEEL:
    case WM_MOUSEHWHEEL:
    case WM_XBUTTONDOWN:
    case WM_XBUTTONUP:
        if (!background_visible())
            return 0;
        break;
    }
    return CallWindowProcA(g_wndproc_orig, h, msg, wp, lp);
}
static HWND WINAPI GetForegroundWindow_hook(void) {
    return g_hwnd ? g_hwnd : GetForegroundWindow_orig();
}
static HWND WINAPI GetActiveWindow_hook(void) {
    return g_hwnd ? g_hwnd : GetActiveWindow_orig();
}
static HWND WINAPI GetFocus_hook(void) {
    return g_hwnd ? g_hwnd : GetFocus_orig();
}

static HWND WINAPI CreateWindowExA_hook(DWORD ex, LPCSTR cls, LPCSTR title, DWORD style, int x, int y, int w, int h,
                                        HWND parent, HMENU menu, HINSTANCE inst, LPVOID param) {
    int main = !parent && title && !strcmp(title, "Warcraft III");
    HWND hwnd;
    if (main) {
        ex = background_visible() ? ((ex | WS_EX_APPWINDOW) & ~(WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST))
                                  : ((ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW) & ~(WS_EX_APPWINDOW | WS_EX_TOPMOST));
        x = background_visible() ? 0 : offscreen_x(w);
        y = background_visible() ? 0 : offscreen_y(h);
    }
    hwnd = CreateWindowExA_orig(ex, cls, title, style, x, y, w, h, parent, menu, inst, param);
    if (main && hwnd) {
        g_hwnd = hwnd;
        g_wndproc_orig = (WNDPROC)SetWindowLongPtrA(hwnd, GWLP_WNDPROC, (LONG_PTR)wndproc_hook);
        /* Activate the simulation after the caller has stored its HWND, without activating Windows. */
        PostMessageA(hwnd, WM_ACTIVATEAPP, TRUE, 0);
        PostMessageA(hwnd, WM_ACTIVATE, WA_ACTIVE, 0);
        PostMessageA(hwnd, WM_SETFOCUS, 0, 0);
        hook_log("background window: %p subclassed (%s)", hwnd, g_wndproc_orig ? "ok" : "FAILED");
    }
    return hwnd;
}
static BOOL WINAPI ShowWindow_hook(HWND h, int cmd) {
    return ShowWindow_orig(h, cmd == SW_HIDE ? SW_HIDE : SW_SHOWNOACTIVATE);
}
static BOOL WINAPI SetWindowPos_hook(HWND h, HWND after, int x, int y, int w, int height, UINT flags) {
    if (h == g_hwnd && !background_visible()) {
        RECT r;
        int width = w, tall = height;
        if ((flags & SWP_NOSIZE) && GetWindowRect(h, &r)) {
            width = r.right - r.left;
            tall = r.bottom - r.top;
        }
        x = offscreen_x(width);
        y = offscreen_y(tall);
    }
    return SetWindowPos_orig(h, background_visible() ? after : HWND_BOTTOM, x, y, w, height, flags | SWP_NOACTIVATE);
}
static BOOL WINAPI SetForegroundWindow_hook(HWND h) {
    return FALSE;
}
static HWND WINAPI SetActiveWindow_hook(HWND h) {
    return GetActiveWindow_orig();
}
static HWND WINAPI SetFocus_hook(HWND h) {
    return GetFocus_orig();
}
static BOOL WINAPI ClipCursor_hook(const RECT *rect) {
    return TRUE;
}
static BOOL WINAPI SetCursorPos_hook(int x, int y) {
    return TRUE;
}
static HWND WINAPI SetCapture_hook(HWND h) {
    return NULL;
}
static int WINAPI ShowCursor_hook(BOOL show) {
    /* Some callers loop until the counter crosses zero; preserve that contract locally. */
    static __declspec(thread) int count;
    return show ? ++count : --count;
}

static void window_hook(const char *name, void *hook, void **original) {
    MH_STATUS status = MH_CreateHookApi(L"user32", name, hook, original);
    if (status != MH_OK) {
        hook_log("background hook %s failed: %s", name, MH_StatusToString(status));
        ExitProcess(1);
    }
}

static void window_init_hooks(void) {
    char mode[32] = {0}, render[8] = {0};
    GetEnvironmentVariableA("WC3HOOK_WINDOW_MODE", mode, sizeof mode);
    GetEnvironmentVariableA("WC3HOOK_RENDER", render, sizeof render);
    if (!strcmp(render, "0"))
        g_render = 0;
    hook_log("window mode: %s render=%ld", !strcmp(mode, "interactive") ? "interactive" : "background", g_render);
    if (!strcmp(mode, "interactive"))
        return;
    window_hook("GetForegroundWindow", (void *)GetForegroundWindow_hook, (void **)&GetForegroundWindow_orig);
    window_hook("GetActiveWindow", (void *)GetActiveWindow_hook, (void **)&GetActiveWindow_orig);
    window_hook("GetFocus", (void *)GetFocus_hook, (void **)&GetFocus_orig);
    window_hook("CreateWindowExA", (void *)CreateWindowExA_hook, (void **)&CreateWindowExA_orig);
    window_hook("ShowWindow", (void *)ShowWindow_hook, (void **)&ShowWindow_orig);
    window_hook("SetWindowPos", (void *)SetWindowPos_hook, (void **)&SetWindowPos_orig);
    window_hook("SetForegroundWindow", (void *)SetForegroundWindow_hook, NULL);
    window_hook("SetActiveWindow", (void *)SetActiveWindow_hook, NULL);
    window_hook("SetFocus", (void *)SetFocus_hook, NULL);
    window_hook("ClipCursor", (void *)ClipCursor_hook, NULL);
    window_hook("SetCursorPos", (void *)SetCursorPos_hook, NULL);
    window_hook("SetCapture", (void *)SetCapture_hook, NULL);
    window_hook("ShowCursor", (void *)ShowCursor_hook, NULL);
}

/* Silent launches skip Miles' audio device; Warcraft supports running without a digital driver.
 * This does not change Windows volume or saved Warcraft preferences. */
static void *WINAPI silent_audio_driver(unsigned rate, int bits, int channels, unsigned flags) {
    hook_log("sound disabled: skipped audio output");
    return NULL;
}

static void sound_init_hooks(void) {
    char sound[8] = {0};
    GetEnvironmentVariableA("WC3HOOK_SOUND", sound, sizeof sound);
    if (strcmp(sound, "0"))
        return;
    require_hook(MH_CreateHookApi(L"mss32", "_AIL_open_digital_driver@16", (void *)silent_audio_driver, NULL),
                 "disable audio output");
}

void instance_init_hooks(void) {
    MH_STATUS m1 = MH_CreateHookApi(L"kernel32", "CreateMutexA", (void *)CreateMutex_hook, (void **)&CreateMutex_orig);
    MH_STATUS m2 = MH_CreateHookApi(L"kernel32", "OpenMutexA", (void *)OpenMutex_hook, (void **)&OpenMutex_orig);
    hook_log("mutex hooks: create=%s open=%s", MH_StatusToString(m1), MH_StatusToString(m2));
    MH_CreateHookApi(L"kernel32", "CreateEventA", (void *)CreateEventA_hook, (void **)&CreateEventA_orig);
    docs_init_hooks();
    sound_init_hooks();
    window_init_hooks();
}
