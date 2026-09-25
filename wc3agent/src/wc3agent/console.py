"""Debug view: macro replies and micro choices, live and in color, in the terminal and on the game screen.

The terminal streams: macro's whole reply in cyan, then one line per unit, the option Jev picked in
the words Jev was shown, with the group name at the end, and only when that unit's choice changed.

The game screen holds a board that updates in place: the latest macro turn on top, then each group
with its units' current choices, units with the same choice on one line. The board always has
SCREEN_LINES lines, because the overlay's position on screen moves with its line count, and it stays
within the SCREEN_BYTES Warcraft shows of a message: past that the text was cut mid-word or mid-color-code.
Each group keeps its own color in both.
"""

import os
import textwrap

# (terminal ANSI code, Warcraft hex color) pairs
MACRO = ("\033[96m", "00ffff")
RED = ("\033[31m", "ff5555")
GROUPS = (
    ("\033[93m", "ffff55"),
    ("\033[92m", "55ff55"),
    ("\033[95m", "ff77ff"),
    ("\033[94m", "77aaff"),
    ("\033[33m", "ffaa00"),
    ("\033[32m", "00cc66"),
    ("\033[35m", "cc66ff"),
    ("\033[34m", "66ccff"),
)
RESET, DIM, GRAY = "\033[0m", "\033[2m", "999999"
SCREEN_LINES = 22  # every board has this many lines, padded with blanks
SCREEN_MACRO_LINES = 7  # wrapped reply lines shown under the macro title
SCREEN_WIDTH = 70  # characters per line on screen; longer lines wrap (Warcraft wrapped 80 again, pushing the top off)
STALE_SECONDS = 15.0  # a unit type Jev has not answered for in this long leaves the board
SCREEN_BYTES = 1020  # Warcraft shows about the first 1023 bytes of a text message, color codes included


def paint(hex_color, text):
    return f"|cff{hex_color}{text}|r"


def wrap(text, indent="  "):
    """Screen lines for one line of text, wrapped at SCREEN_WIDTH; continuations indent further."""
    return textwrap.wrap(text, SCREEN_WIDTH, initial_indent=indent, subsequent_indent=indent + "  ") or [indent]


class Console:
    def __init__(self):
        os.system("")  # turns on ANSI colors in the Windows console
        self.colors = {}  # group -> (ANSI, hex)
        self.last = {}  # unit -> the choice last printed for it
        self.macro_lines = []
        self.board = {}  # group -> {"group / unit type": (game time, [(unit, choice, note)])}
        self.now = 0.0  # latest game time seen
        self.shown = None  # the board last put on screen

    def show(self, record):
        if record["kind"] == "macro":
            self.macro(record)
        else:
            self.micro(record)

    def macro(self, record):
        seconds = (record.get("latency_ms") or 0) / 1000
        landed = record["landed_at_game_time"]
        self.now = max(self.now, landed)
        lines = [line for line in record["reply"].strip().splitlines() if line.strip()]
        print(f"{MACRO[0]}[{landed:7.1f}s] MACRO turn {record['turn']} ({seconds:.1f}s){RESET}")
        for line in lines:
            print(f"{MACRO[0]}  {line}{RESET}")
        for problem in record["problems"]:
            print(f"{RED[0]}  ! {problem}{RESET}")
        print(flush=True)
        body = [paint(RED[1], row) for problem in record["problems"] for row in wrap("! " + problem)]
        body += [paint(MACRO[1], row) for line in lines for row in wrap(line)]
        if len(body) > SCREEN_MACRO_LINES:
            body = body[: SCREEN_MACRO_LINES - 1] + [paint(GRAY, "  (rest of the reply in the terminal)")]
        title = paint(MACRO[1], f"MACRO turn {record['turn']} at {landed:.0f}s ({seconds:.1f}s)")
        self.macro_lines = [title, *body]

    def micro(self, record):
        group = record["group"]
        ansi, _ = self.colors.setdefault(group, GROUPS[len(self.colors) % len(GROUPS)])
        landed = record.get("landed_at_game_time", record["at_game_time"])
        self.now = max(self.now, landed)
        at = f"[{landed:7.1f}s]"
        rows = self.board.setdefault(group, {})
        if record.get("error"):
            text = f"Jev failed: {record['error'][:200]}"
            print(f"{RED[0]}{at} {record['control_group']}: {text}{RESET}", flush=True)
            rows[record["control_group"]] = (landed, [(record["control_group"], text, "")])
            return
        picked = record.get("picked", {})
        dropped = {d["unit"]: d["reason"] for d in record.get("dropped_actions", []) if "unit" in d}
        notes = {unit: f"  (not sent: {dropped[unit]})" if unit in dropped else "" for unit in picked}
        rows[record["control_group"]] = (landed, [(unit, choice, notes[unit]) for unit, choice in picked.items()])
        # Jev re-picks every second; the terminal prints a unit only when its choice changes.
        changed = {u: c for u, c in picked.items() if self.last.get(u) != c or u in dropped}
        self.last.update(changed)
        width = max((len(unit) for unit in changed), default=0)
        for unit, choice in changed.items():
            print(f"{ansi}{at} {unit:<{width}}  {choice}{DIM}{notes[unit]}  [{group}]{RESET}", flush=True)

    def panel(self):
        """The board's text, or None when it has not changed since last shown."""
        lines = [*self.macro_lines, " "]
        for group, rows in self.board.items():
            units = [row for when, found in rows.values() if self.now - when <= STALE_SECONDS for row in found]
            if units:
                hex_color = self.colors[group][1]
                lines.append(paint(hex_color, group))
                by_choice = {}
                for unit, choice, note in units:
                    by_choice.setdefault(choice + note, []).append(unit)
                for choice, members in by_choice.items():
                    lines += [paint(hex_color, row) for row in wrap(f"{', '.join(members)}: {choice}")]
        return self._show(lines)

    def _show(self, lines):
        """Fit the lines to SCREEN_LINES and SCREEN_BYTES, whole lines only, and pad them."""
        more = len(paint(GRAY, "  ... 99 more")) + 1  # room kept for the line saying what was left out
        kept, used = [], 0
        for index, line in enumerate(lines):
            size = len(line.encode("utf-8")) + 1
            last = index == len(lines) - 1
            padding = 2 * (SCREEN_LINES - index - 1)  # the blank lines (" " and a newline) after this one
            if (
                index >= SCREEN_LINES - (0 if last else 1)
                or used + size + padding + (0 if last else more) > SCREEN_BYTES
            ):
                kept.append(paint(GRAY, f"  ... {len(lines) - index} more"))
                break
            kept.append(line)
            used += size
        kept += [" "] * (SCREEN_LINES - len(kept))
        text = "\n".join(kept)
        if text == self.shown:
            return None
        self.shown = text
        return text
