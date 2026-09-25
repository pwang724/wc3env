"""Replay videos: the Warcraft window on the left, Jev's decisions on the right.

While a visible duel runs, `Recorder` captures the game window (game.mp4, with each frame's wall-clock
time in frames.json) and the duel notes the game clock against the wall clock (clock.json). Afterwards
`render` draws, for every frame, a panel of Jev's latest decisions at that moment of game time (from
calls.jsonl): one drawer per unit type with its icon, and in it each unit's chosen action and its
probability, all in one column. Drawing afterwards never slows or changes the fight.

Needs mss, numpy and imageio[ffmpeg], and pyaudiowpatch for sound; icons come from `python -m tools.prepare.icons` (build/icons).
"""

from __future__ import annotations

import bisect
import ctypes
import json
import threading
import time
from ctypes import wintypes
from pathlib import Path

from .config import ROOT

FPS = 15
TRIM_SECONDS = 1.4  # left off the start of every replay: the moments before the game window is in front
PANEL_WIDTH = 620
HEADER, GAP, ROW, ICON = 34, 6, 26, 28  # drawer header, gap between drawers, unit row, icon size
NAME_WIDTH = 170  # room for a unit's name before its action
ICONS = ROOT / "build" / "icons"
FONT = "C:/Windows/Fonts/segoeui.ttf"
FONT_BOLD = "C:/Windows/Fonts/segoeuib.ttf"
NEW_SECONDS = 1.2  # a choice this recent is highlighted
STALE_SECONDS = 4.0  # a unit Jev has not decided for this long is dimmed (dead, or held by code)
GONE_SECONDS = 8.0  # and after this long it leaves the panel
BACKGROUND, DRAWER, TEXT, DIM, BAR, TRACK, NEW = (
    (14, 16, 22),
    (28, 32, 42),
    (228, 231, 238),
    (125, 130, 142),
    (64, 150, 255),
    (44, 48, 60),
    (255, 206, 84),
)
ACCENTS = [(96, 180, 255), (120, 220, 140), (255, 160, 90), (220, 130, 230), (255, 220, 110), (110, 210, 210)]


def _game_window():
    """The Warcraft III client area on screen: (left, top, width, height) in physical pixels."""
    user32 = ctypes.windll.user32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # physical pixels, as mss captures them
    except OSError:
        pass
    hwnd = user32.FindWindowW(None, "Warcraft III")
    if not hwnd:
        raise RuntimeError("no Warcraft III window to record")
    rect, origin = wintypes.RECT(), wintypes.POINT(0, 0)
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    user32.SetForegroundWindow(hwnd)  # mss captures the screen, so nothing may cover the game
    width, height = rect.right - rect.left, rect.bottom - rect.top
    return origin.x, origin.y, width - width % 2, height - height % 2


class Sound:
    """Records what the PC plays (WASAPI loopback) into audio.wav. Loopback sends nothing while the PC is
    silent, so each chunk is placed by the time it arrived and the gaps stay silent; audio.json holds the
    wall-clock time of the file's first sample."""

    def __init__(self, folder):
        self.folder, self.chunks = Path(folder), []  # (wall time the chunk ended, bytes)
        self.audio = self.stream = None

    def start(self):
        try:
            import pyaudiowpatch as pyaudio
        except ImportError:
            return  # video only
        self.audio = pyaudio.PyAudio()
        device = self.audio.get_default_wasapi_loopback()
        self.rate, self.channels = int(device["defaultSampleRate"]), device["maxInputChannels"]

        def callback(data, frames, info, status):
            self.chunks.append((time.monotonic(), data))
            return (None, pyaudio.paContinue)

        self.started = time.monotonic()
        self.stream = self.audio.open(
            format=pyaudio.paInt16, channels=self.channels, rate=self.rate, input=True,
            input_device_index=device["index"], frames_per_buffer=1024, stream_callback=callback,
        )  # fmt: skip

    def stop(self):
        import wave

        if not self.stream:
            return
        self.stream.stop_stream()
        self.stream.close()
        self.audio.terminate()
        frame = 2 * self.channels  # bytes per sample frame (16-bit)
        end = max([t for t, _ in self.chunks], default=self.started)
        track = bytearray(int((end - self.started) * self.rate) * frame)
        for ended, data in self.chunks:
            at = max(0, int((ended - self.started) * self.rate) * frame - len(data))
            track[at : at + len(data)] = data
        with wave.open(str(self.folder / "audio.wav"), "wb") as out:
            out.setnchannels(self.channels)
            out.setsampwidth(2)
            out.setframerate(self.rate)
            out.writeframes(bytes(track))
        (self.folder / "audio.json").write_text(json.dumps({"start": self.started}), encoding="utf-8")


class Recorder:
    """Captures the game window at FPS, and the PC's sound, from start() to stop()."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.times, self.clock = [], []  # wall time per frame; (wall time, game time) pairs
        self._stop = threading.Event()
        self._thread = None
        self.sound = Sound(folder)

    def start(self):
        import imageio.v2 as imageio
        import mss
        import numpy as np

        left, top, width, height = _game_window()
        area = {"left": left, "top": top, "width": width, "height": height}
        writer = imageio.get_writer(self.folder / "game.mp4", fps=FPS, codec="libx264", quality=8, macro_block_size=2)

        def run():
            with mss.mss() as screen:
                next_at = time.monotonic()
                while not self._stop.is_set():
                    shot = np.asarray(screen.grab(area))[:, :, 2::-1]  # BGRA -> RGB
                    writer.append_data(np.ascontiguousarray(shot))
                    self.times.append(time.monotonic())
                    next_at += 1 / FPS
                    time.sleep(max(0.0, next_at - time.monotonic()))
            writer.close()

        self._thread = threading.Thread(target=run, name="recorder", daemon=True)
        self._thread.start()
        self.sound.start()

    def note(self, game_seconds):
        """The game clock now, to place each frame in game time."""
        self.clock.append((time.monotonic(), game_seconds))

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join()
        self.sound.stop()
        (self.folder / "frames.json").write_text(json.dumps(self.times), encoding="utf-8")
        (self.folder / "clock.json").write_text(json.dumps(self.clock), encoding="utf-8")


def _game_time(wall, clock):
    """Game seconds at a wall-clock moment, interpolated from the duel's (wall, game) notes."""
    walls = [w for w, _ in clock]
    i = bisect.bisect_left(walls, wall)
    if i <= 0:
        return clock[0][1]
    if i >= len(clock):
        return clock[-1][1]
    (w0, g0), (w1, g1) = clock[i - 1], clock[i]
    return g0 + (g1 - g0) * (wall - w0) / ((w1 - w0) or 1)


def _decisions(folder):
    """Every answer Jev gave, ordered by when it reached the game:
    (game time, unit type, type name, unit, chosen option, its probability)."""
    rows = []
    for line in open(Path(folder) / "calls.jsonl", encoding="utf-8"):
        record = json.loads(line)
        response = record.get("response")
        answers = response.get("answers", {}) if isinstance(response, dict) else {}
        landed = record.get("landed_at_game_time", record.get("at_game_time", 0))
        type_name = str(record.get("control_group", "")).split(" / ")[-1]
        for unit, answer in answers.items():
            choice, probabilities = answer.get("choice", ""), answer.get("probabilities") or {}
            rows.append((landed, record.get("type_id", ""), type_name, unit, choice, probabilities.get(choice, 0.0)))
    rows.sort(key=lambda row: row[0])
    return rows


def _fit(draw, text, font, width):
    """`text` shortened with an ellipsis to fit `width` pixels."""
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


class Panel:
    """Draws the decision panel; icons and fonts are loaded once."""

    def __init__(self, title, height):
        from PIL import ImageFont

        self.title, self.height, self.icons = title, height, {}
        self.fonts = {
            "title": ImageFont.truetype(FONT_BOLD, 26),
            "head": ImageFont.truetype(FONT_BOLD, 19),
            "unit": ImageFont.truetype(FONT_BOLD, 16),
            "text": ImageFont.truetype(FONT, 16),
            "small_bold": ImageFont.truetype(FONT_BOLD, 13),
            "small": ImageFont.truetype(FONT, 13),
        }

    def icon(self, raw):
        from PIL import Image

        if raw not in self.icons:
            path = ICONS / f"{raw}.png"
            self.icons[raw] = Image.open(path).convert("RGB").resize((ICON, ICON)) if path.exists() else None
        return self.icons[raw]

    def draw(self, now, decisions):
        from PIL import Image, ImageDraw

        width = PANEL_WIDTH
        image = Image.new("RGB", (width, self.height), BACKGROUND)
        draw = ImageDraw.Draw(image)
        fonts = self.fonts
        draw.text((16, 10), self.title, font=fonts["title"], fill=TEXT)
        minutes, seconds = divmod(max(0.0, now), 60)
        draw.text((width - 16, 16), f"{int(minutes)}:{seconds:04.1f}", font=fonts["head"], fill=DIM, anchor="ra")

        latest = {}
        for at, raw, type_name, unit, choice, p in decisions[: bisect.bisect_right([d[0] for d in decisions], now)]:
            latest[unit] = (at, raw, type_name, choice, p)
        groups = {}
        for unit, (at, raw, type_name, choice, p) in latest.items():
            if now - at < GONE_SECONDS:
                groups.setdefault((type_name, raw), []).append((unit, at, choice, p))
        # Every unit on one column: rows shrink when there are many.
        top = 52
        units = sum(len(g) for g in groups.values()) or 1
        row = max(15, min(ROW, (self.height - top - len(groups) * (HEADER + GAP)) // units))
        font = fonts["text"] if row >= 20 else fonts["small"]
        bold = fonts["unit"] if row >= 20 else fonts["small_bold"]

        y = top
        for index, ((type_name, raw), members) in enumerate(sorted(groups.items())):
            accent = ACCENTS[index % len(ACCENTS)]
            height = HEADER + row * len(members)
            draw.rounded_rectangle((8, y, width - 8, y + height), 6, fill=DRAWER)
            draw.rectangle((8, y + 4, 11, y + HEADER - 4), fill=accent)
            icon = self.icon(raw)
            if icon is not None:
                image.paste(icon, (20, y + (HEADER - icon.height) // 2))
            draw.text((20 + ICON + 10, y + HEADER // 2), type_name or raw, font=fonts["head"], fill=TEXT, anchor="lm")
            draw.text((width - 20, y + HEADER // 2), f"×{len(members)}", font=fonts["text"], fill=DIM, anchor="rm")
            line = y + HEADER
            for unit, at, choice, p in sorted(members):
                age = now - at
                stale = age > STALE_SECONDS
                middle = line + row // 2
                if age < NEW_SECONDS:
                    draw.rectangle((13, line + 2, 16, line + row - 2), fill=NEW)
                draw.text((24, middle), unit, font=bold, fill=DIM if stale else accent, anchor="lm")
                label_x = 24 + NAME_WIDTH
                bar_x = width - 110
                draw.text(
                    (label_x, middle), _fit(draw, choice, font, bar_x - label_x - 10), font=font,
                    fill=DIM if stale else TEXT, anchor="lm",
                )  # fmt: skip
                draw.rectangle((bar_x, middle - 3, bar_x + 56, middle + 3), fill=TRACK)
                draw.rectangle((bar_x, middle - 3, bar_x + max(1, int(56 * p)), middle + 3), fill=DIM if stale else BAR)
                draw.text((width - 20, middle), f"{100 * p:.0f}%", font=font, fill=DIM if stale else TEXT, anchor="rm")
                line += row
            y += height + GAP
        return image


def render(folder, title):
    """replay.mp4 in `folder`: game.mp4 with the Jev panel beside every frame."""
    import imageio.v2 as imageio
    import numpy as np

    folder = Path(folder)
    times = json.loads((folder / "frames.json").read_text(encoding="utf-8"))
    clock = json.loads((folder / "clock.json").read_text(encoding="utf-8"))
    decisions = _decisions(folder)
    reader = imageio.get_reader(folder / "game.mp4")
    silent = folder / "replay-silent.mp4"
    writer = imageio.get_writer(silent, fps=FPS, codec="libx264", quality=6, macro_block_size=2)
    panel = None
    first = times[0] + TRIM_SECONDS if times else 0
    for frame, wall in zip(reader, times):
        if wall < first:
            continue
        panel = panel or Panel(title, frame.shape[0])
        writer.append_data(np.hstack([frame, np.asarray(panel.draw(_game_time(wall, clock), decisions))]))
    reader.close()
    writer.close()
    out = folder / "replay.mp4"
    if (folder / "audio.wav").exists() and times:
        import subprocess

        import imageio_ffmpeg

        # The sound started `offset` seconds after the first frame (negative: before it).
        offset = json.loads((folder / "audio.json").read_text(encoding="utf-8"))["start"] - first
        audio = ["-itsoffset", f"{offset:.3f}", "-i", str(folder / "audio.wav")] if offset >= 0 else [
            "-ss", f"{-offset:.3f}", "-i", str(folder / "audio.wav")
        ]  # fmt: skip
        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(silent), *audio,
             "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", str(out)],
            check=True,
        )  # fmt: skip
        silent.unlink()
    else:
        silent.replace(out)
    return out
