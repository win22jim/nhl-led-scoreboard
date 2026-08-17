"""Text + scrolling helpers shared by the off-season / phase boards.

Two responsibilities:

1. **Pixel-font sanitization.** The matrix uses 04B_24 (8px) and a few
   other bitmap fonts that only cover basic ASCII glyphs. Unicode
   punctuation from upstream sources (NHL headlines, Spotrac scrapes,
   trophy descriptions) shows up as empty boxes when rendered. This
   module normalizes those characters to ASCII equivalents before drawing.

2. **Horizontal marquee scrolling.** Long text (news headlines, trophy
   narratives) doesn't fit the 64-wide matrix. ``scroll_line`` redraws a
   region of the matrix on a short cadence so the text slides right-to-
   left across it. Returns once the text has fully exited the visible
   area OR ``sleepEvent`` fires (which happens when the rotation needs
   to advance, the screensaver kicks in, etc.).
"""

import logging
import unicodedata

debug = logging.getLogger("scoreboard")

# Curly quotes, dashes, ellipsis, NBSP, bullets — common offenders in NHL
# editorial content that the 04B_24 font doesn't have glyphs for.
_REPLACEMENTS = {
    "‘": "'", "’": "'",   # ‘ ’
    "“": '"', "”": '"',   # “ ”
    "–": "-", "—": "-",   # – —
    "…": "...",                # …
    " ": " ",                  # NBSP
    "•": "*",                  # •
    "·": "*",                  # ·
    "­": "",                   # soft hyphen
    "°": " deg",               # °
    "½": "1/2", "¼": "1/4", "¾": "3/4",
    "©": "(c)", "®": "(r)", "™": "(tm)",
    # Nordic / Slavic chars that don't decompose under NFKD. Common in NHL
    # rosters (Jørgen, Æ, Łukáš, Đjuro, etc.).
    "ø": "o", "Ø": "O",
    "æ": "ae", "Æ": "AE",
    "œ": "oe", "Œ": "OE",
    "ß": "ss",
    "đ": "d", "Đ": "D",
    "ł": "l", "Ł": "L",
    "þ": "th", "Þ": "Th",
    "ð": "d", "Ð": "D",
    "ı": "i",
}


def sanitize(s) -> str:
    """Map unicode punctuation to ASCII and strip diacritics.

    ``Éric`` -> ``Eric``, ``Don't`` (curly) -> ``Don't`` (straight),
    ``head — chest`` -> ``head - chest``. Anything still outside ASCII
    after that round-trip is dropped to avoid rendering as a glyph box.
    """
    if not s:
        return ""
    try:
        s = str(s)
    except Exception:
        return ""
    for src, dst in _REPLACEMENTS.items():
        s = s.replace(src, dst)
    # Decompose accented chars (é -> e + combining acute) then strip the marks.
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    # Anything still non-ASCII is unrenderable; drop it.
    try:
        s = s.encode("ascii", "ignore").decode("ascii")
    except Exception:
        pass
    return s


def text_width(font, text: str) -> int:
    """Width in pixels of ``text`` rendered with ``font``. Returns 0 on
    failure so callers don't have to guard against AttributeError."""
    try:
        bbox = font.getbbox(text or "")
        return max(0, bbox[2] - bbox[0])
    except Exception:
        return 0


def wrap_lines(font, text: str, max_width: int, max_lines: int = None):
    """Greedy word-wrap ``text`` to lines no wider than ``max_width`` pixels.

    With ``max_lines=None`` (the default) every line is returned, so callers
    can page through long text rather than truncate or fall back to a marquee.
    Pass an int to cap it, in which case ``fits`` reports whether the text
    actually needed more room.

    Returns ``(lines, fits)``.

    Static wrapped text beats a marquee wherever it fits: a 64px-wide matrix
    holds ~14 characters per line, so a typical headline lands in 2-3 lines and
    can simply be read, rather than crawling past one character at a time.
    """
    words = (text or "").split()
    if not words:
        return [], True

    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if text_width(font, candidate) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    if max_lines is None:
        return lines, True
    return lines[:max_lines], len(lines) <= max_lines


def paginate(lines, per_page: int):
    """Split wrapped ``lines`` into screen-sized pages."""
    per_page = max(1, per_page)
    return [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]


def line_overflows(font, lines, max_width: int) -> bool:
    """True if any single line is wider than the panel.

    Wrapping can't help a single word longer than the display (rare, but e.g.
    a hyphen-free compound). Callers fall back to a marquee for those.
    """
    return any(text_width(font, ln) > max_width for ln in lines)


def read_seconds(text: str, min_seconds: float = 2.5, max_seconds: float = 8.0,
                 wpm: float = 110.0) -> float:
    """How long to hold static text on screen so it can actually be read.

    Proportional to length rather than a fixed dwell — a five-word headline
    shouldn't sit there as long as a fifteen-word one, and padding short items
    to a fixed duration is exactly the dead air we're trying to avoid.

    The 110 wpm default is deliberately well below prose reading speed: this is
    a small pixel font on an LED panel, usually read from across a room and
    often glanced at mid-stride.
    """
    words = len((text or "").split())
    if not words:
        return min_seconds
    seconds = (words / max(1.0, wpm)) * 60.0
    return max(min_seconds, min(max_seconds, seconds))


def scroll_line(
    matrix,
    sleepEvent,
    font,
    text: str,
    y: int,
    color,
    *,
    region=None,
    frame_delay: float = 0.04,
    pad: int = 4,
    redraw_static=None,
):
    """Scroll ``text`` right-to-left at ``y`` until it fully exits the
    matrix (or ``sleepEvent`` fires).

    Args:
        matrix: the Matrix object (has draw_text / draw_rectangle / render).
        sleepEvent: threading event — when set, scrolling aborts immediately
            so the rotation can advance.
        font: pixel font to render ``text`` in.
        text: the (already-sanitized) line to scroll.
        y: top-left y coordinate for the text baseline.
        color: text fill color.
        region: optional ``(x0, y0, x1, y1)`` rectangle to clear each frame
            so the scroll doesn't overdraw on top of static elements (icons
            in the left margin, header banners, etc.). Defaults to the full
            row width on the row containing ``y``.
        frame_delay: seconds between pixel steps (~0.04 => 25fps).
        pad: extra blank pixels appended after the text so it fully exits
            before the next item starts.
        redraw_static: optional callable invoked each frame after the
            clear+before the scroll-text draw, used to repaint static
            elements (icons, header text) that the clear wiped.
    """
    if not text:
        return
    try:
        w = text_width(font, text)
    except Exception:
        return
    if w <= 0:
        return

    if region is None:
        # 7px tall row by default (height of the 04B_24 8pt glyph).
        region = (0, y - 1, matrix.width, y + 8)
    rx0, ry0, rx1, ry1 = region
    rect_w = max(1, rx1 - rx0)
    rect_h = max(1, ry1 - ry0)

    x = matrix.width
    end_x = -(w + pad)
    while x > end_x:
        if sleepEvent.is_set():
            return
        try:
            matrix.draw_rectangle((rx0, ry0), (rect_w, rect_h), fill=(0, 0, 0))
            if redraw_static is not None:
                try:
                    redraw_static()
                except Exception as e:
                    debug.warning(f"scroll_line: redraw_static raised: {e}")
            matrix.draw_text((x, y), text, font=font, fill=color)
            matrix.render()
        except Exception as e:
            debug.warning(f"scroll_line: draw failed at x={x}: {e}")
            return
        try:
            sleepEvent.wait(frame_delay)
        except Exception:
            return
        x -= 1
