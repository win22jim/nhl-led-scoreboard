"""
API Status board module implementation.

Shown in place of any board that needs live NHL data while the NHL API is
unreachable. The NHL API goes down for real (a multi-hour outage on
2026-08-13 is what prompted this board), and an appliance should explain
itself rather than go dark or restart in a loop.
"""
import logging

from PIL import Image

from boards.base_board import BoardBase
from boards.builtins._text import sanitize, scroll_line

from . import __board_name__, __description__, __version__

debug = logging.getLogger("scoreboard")


class ApiStatusBoard(BoardBase):
    """Displays an 'NHL API unavailable' notice.

    Layout mirrors season_countdown: NHL logo on the right behind a gradient,
    headline text on the left, and a scrolling explanation along the bottom.
    """

    def __init__(self, data, matrix, sleepEvent):
        super().__init__(data, matrix, sleepEvent)

        self.board_name = __board_name__
        self.board_version = __version__
        self.board_description = __description__

        self.headline_line1 = self.board_config.get("headline_line1", "WE'LL")
        self.headline_line2 = self.board_config.get("headline_line2", "BE BACK")
        self.scroll_text = self.board_config.get(
            "scroll_text", "NHL API IS CURRENTLY DOWN, WE WILL TRY AGAIN LATER"
        )
        self.headline_color = tuple(self.board_config.get("headline_color", [255, 165, 0]))
        self.scroll_color = tuple(self.board_config.get("scroll_color", [255, 255, 255]))
        self.show_outage_duration = self.board_config.get("show_outage_duration", True)
        self.scroll_speed = self.board_config.get("scroll_speed", 0.04)
        self.display_seconds = self.board_config.get("display_seconds", 5)

        self.font = data.config.layout.font

    def _outage_suffix(self):
        """' - DOWN FOR 12M' style suffix, or '' when we can't tell.

        Purely informational; never let a formatting problem take the board
        down, since this board IS the failure path.
        """
        if not self.show_outage_duration:
            return ""
        try:
            since = getattr(self.data, "nhl_api_down_since", None)
            if not since:
                return ""
            from datetime import datetime

            minutes = int((datetime.now() - since).total_seconds() // 60)
            if minutes < 1:
                return ""
            if minutes < 60:
                return " - DOWN FOR {}M".format(minutes)
            return " - DOWN FOR {}H{:02d}M".format(minutes // 60, minutes % 60)
        except Exception as e:
            debug.debug("api_status: could not compute outage duration: {}".format(e))
            return ""

    def _draw_static(self, layout, nhl_logo, gradient):
        """Paint the logo + headline. Re-used as scroll_line's redraw_static
        callback so the scrolling row doesn't erase them."""
        if nhl_logo is not None:
            self.matrix.draw_image_layout(layout.logo, nhl_logo)
        if gradient is not None:
            self.matrix.draw_image_layout(layout.gradient, gradient)
        self.matrix.draw_text_layout(layout.headline_line1, self.headline_line1, fillColor=self.headline_color)
        self.matrix.draw_text_layout(layout.headline_line2, self.headline_line2, fillColor=self.headline_color)

    def render(self):
        # This board is normally substituted in automatically by the rotation
        # (see Boards._substitute_if_nhl_api_down) and shouldn't be added to a
        # state list by hand. If someone does add it via the dashboard, stay
        # silent while the API is healthy instead of claiming an outage.
        if not getattr(self.data, "nhl_api_down", False):
            debug.debug("api_status: NHL API is healthy, nothing to report")
            return

        layout = self.get_board_layout("api_status")

        self.matrix.clear()

        rows = self.matrix.height
        cols = self.matrix.width

        nhl_logo = None
        gradient = None
        try:
            nhl_logo = Image.open(f"assets/images/{cols}x{rows}_nhl_logo.png").convert("RGBA")
            gradient = Image.open(f"assets/images/{cols}x{rows}_scoreboard_center_gradient.png")
        except Exception as e:
            # No logo for this matrix size — still show the text. This board
            # must never fail; it is what the user sees when things are broken.
            debug.warning("api_status: could not open NHL logo assets: {}".format(e))

        self._draw_static(layout, nhl_logo, gradient)
        self.matrix.render()
        self.sleepEvent.wait(0.5)

        message = sanitize(self.scroll_text + self._outage_suffix())
        debug.info("NHL API unavailable — showing api_status board")

        scroll_y = self.matrix.layout_position(layout.scroll)[1]
        scroll_line(
            self.matrix,
            self.sleepEvent,
            self.font,
            message,
            scroll_y,
            self.scroll_color,
            frame_delay=self.scroll_speed,
            redraw_static=lambda: self._draw_static(layout, nhl_logo, gradient),
        )

        self.sleepEvent.wait(self.display_seconds)
