import logging
import time

import control_channel
import frame_mirror
from sbio.boardpreview import BoardPreview

debug = logging.getLogger("scoreboard")

POLL_INTERVAL = 0.5          # how often the spool is checked for commands
STATE_REFRESH = 3.0          # re-publish an unchanged state this often while watched
MAX_REMEMBERED_RESULTS = 20


class LocalControls(object):
    """Runs the dashboard's control commands inside the scoreboard process.

    Does what the MQTT control topics do (see sbMQTT), without needing a broker.
    Commands arrive through control_channel; the outcome of each is published in
    the state file so the dashboard can show "done" or the reason it was refused.
    """

    def __init__(self, data, matrix, sleep_event, screensaver, preview=None):
        self.data = data
        self.matrix = matrix
        self.sleep_event = sleep_event
        self.screensaver = screensaver
        self.preview = preview or BoardPreview(data, matrix)
        self.results = {}
        self._seen = set()
        self._last_published = None
        self._last_publish_time = 0.0

    # --- commands -------------------------------------------------------------

    def handle(self, cmd):
        """Carry out one validated command. Returns (ok, message)."""
        try:
            return getattr(self, '_do_' + cmd['action'])(cmd)
        except Exception as e:
            debug.error(f"Local control {cmd.get('action')} failed: {e}")
            return False, f"That didn't work: {e}"

    def _do_brightness(self, cmd):
        if self.data.config.dimmer_enabled:
            return False, ("Brightness is controlled by the automatic dimmer. "
                           "Turn the dimmer off in Advanced I/O to set it by hand.")
        self.matrix.set_brightness(cmd['value'])
        debug.info(f"Control: brightness set to {cmd['value']}")
        return True, f"Brightness set to {cmd['value']}%."

    def _do_dimmer(self, cmd):
        if not self.data.config.dimmer_enabled:
            return False, "The automatic dimmer is turned off in the config."
        self.data.config.dimmer_sunrise_brightness = cmd['sunrise']
        self.data.config.dimmer_sunset_brightness = cmd['sunset']
        debug.info(f"Control: dimmer sunrise={cmd['sunrise']} sunset={cmd['sunset']}")
        return True, f"Dimmer set: {cmd['sunrise']}% by day, {cmd['sunset']}% at night."

    def _do_screensaver(self, cmd):
        if self.screensaver is None:
            return False, "The screensaver isn't enabled in the config."
        if cmd['value'] == 'on':
            self.screensaver.runSaver()
            return True, "Screensaver started."
        self.screensaver.stopSaver()
        return True, "Screensaver stopped."

    def _do_showboard(self, cmd):
        board = cmd['board']
        self.data.mqtt_showboard = board
        self.data.mqtt_trigger = True
        self.sleep_event.set()
        debug.info(f"Control: showing board {board} on next loop")
        return True, f"Showing {board} next."

    def _do_preview(self, cmd):
        return self.preview.start(cmd['board'])

    def _do_preview_stop(self, cmd):
        self.preview.stop()
        return True, "Preview stopped."

    # --- loop -------------------------------------------------------------------

    def state(self):
        brightness = getattr(self.matrix, 'brightness', None)
        if brightness is None:
            brightness = getattr(getattr(self.matrix, 'matrix', None), 'brightness', None)
        cfg = self.data.config
        return {
            'board': self.data.curr_board,
            'brightness': brightness,
            'dimmer_enabled': bool(cfg.dimmer_enabled),
            'dimmer': ({'sunrise': cfg.dimmer_sunrise_brightness,
                        'sunset': cfg.dimmer_sunset_brightness}
                       if cfg.dimmer_enabled else None),
            'screensaver_available': self.screensaver is not None,
            'screensaver_active': bool(self.data.screensaver),
            'preview': self.preview.status(),
            'results': dict(self.results),
        }

    def poll_once(self):
        ran = False
        for cmd_id, cmd in control_channel.drain(seen=self._seen):
            ok, message = self.handle(cmd)
            self.results[cmd_id] = {'ok': ok, 'message': message}
            ran = True
        while len(self.results) > MAX_REMEMBERED_RESULTS:
            self.results.pop(next(iter(self.results)))
        if len(self._seen) > 500:
            self._seen.clear()
        self._publish(force=ran)

    def _publish(self, force):
        """Write the state file: at once after a command, otherwise only while a
        dashboard page is open (so an idle scoreboard never touches the disk)."""
        now = time.monotonic()
        if not force and not frame_mirror.viewer_present():
            return
        state = self.state()
        if (not force and state == self._last_published
                and now - self._last_publish_time < STATE_REFRESH):
            return
        control_channel.publish_state(state)
        self._last_published = state
        self._last_publish_time = now

    def run(self):
        while True:
            try:
                self.poll_once()
            except Exception as e:
                debug.error(f"Local control loop error: {e}")
            time.sleep(POLL_INTERVAL)
