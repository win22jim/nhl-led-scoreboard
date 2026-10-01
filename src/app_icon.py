"""The dashboard's home-screen / browser-tab icon, drawn in code.

A small LED matrix with a glowing red goal light on a dark panel. Generated
with Pillow (already a scoreboard dependency) instead of shipping image files.
Deliberately contains no league or team marks.
"""

import io
from functools import lru_cache

from PIL import Image, ImageDraw

BACKGROUND = (13, 21, 38)
LED_OFF = (22, 36, 72)
LED_ICE = (150, 196, 255)
GOAL_LIGHT = (255, 64, 64)
GOAL_GLOW = (126, 28, 40)

GRID = 12
SUPERSAMPLE = 4  # draw at 4x, then scale down for smooth dot edges

SIZES = (32, 180, 192, 512)


@lru_cache(maxsize=None)
def render_icon(size):
    """PNG bytes for a square icon `size` pixels wide."""
    if size not in SIZES:
        raise ValueError(f"unsupported icon size {size}")
    big = size * SUPERSAMPLE
    img = Image.new('RGB', (big, big), BACKGROUND)
    draw = ImageDraw.Draw(img)

    # Keep the artwork inside the central 72% so it survives the circular and
    # rounded masks that Android and iOS apply to home-screen icons.
    margin = big * 0.14
    cell = (big - 2 * margin) / GRID
    radius = cell * 0.40
    light_x, light_y = (GRID - 1) / 2, 4.5

    for row in range(GRID):
        for col in range(GRID):
            dist = ((col - light_x) ** 2 + (row - light_y) ** 2) ** 0.5
            if dist <= 2.3:
                color = GOAL_LIGHT
            elif dist <= 3.4:
                color = GOAL_GLOW
            elif row >= GRID - 2:
                color = LED_ICE
            else:
                color = LED_OFF
            cx = margin + (col + 0.5) * cell
            cy = margin + (row + 0.5) * cell
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color)

    img = img.resize((size, size), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, 'PNG', optimize=True)
    return out.getvalue()
