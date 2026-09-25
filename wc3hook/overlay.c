/* Local UI text, never network chat. The caller owns content, rows and expiry. */
#include "wc3hook.h"

const char *overlay_show(yyjson_val *args, JW *w) {
    yyjson_val *text = yyjson_obj_get(args, "panel");
    const char *panel = yyjson_get_str(text);
    if (!panel || yyjson_get_len(text) > 4096 || strlen(panel) != yyjson_get_len(text))
        return "panel must be text without embedded NUL (maximum 4096 bytes)";
    double x = -.30, y = .60, seconds = 2;
    if (yyjson_obj_get(args, "x") && (!jr_is_num(yyjson_obj_get(args, "x"), &x) || fabs(x) > 1))
        return "x must be finite within -1..1";
    if (yyjson_obj_get(args, "y") && (!jr_is_num(yyjson_obj_get(args, "y"), &y) || fabs(y) > 1))
        return "y must be finite within -1..1";
    if (yyjson_obj_get(args, "seconds") &&
        (!jr_is_num(yyjson_obj_get(args, "seconds"), &seconds) || seconds < 0 || seconds > 3600))
        return "seconds must be finite within 0..3600";
    typedef void(__cdecl * TextFn)(int, const float *, const float *, const float *, void *);
    DWORD inner[8] = {0}, outer[3] = {0};
    float fx = (float)x, fy = (float)y, duration = (float)seconds;
    inner[7] = (DWORD)panel;
    outer[2] = (DWORD)inner;
    int player = NATIVE(0x093790, int(__cdecl *)(void))();
    NATIVE(0x08a9b0, void(__cdecl *)(void))(); /* ClearTextMessages */
    NATIVE(0x08fbe0, TextFn)(player, &fx, &fy, &duration, outer);
    jw_key(w, "displayed");
    jw_bool(w, 1);
    return NULL;
}
