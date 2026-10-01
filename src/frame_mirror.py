"""Hand the frame currently on the LED panel to the web dashboard.

The scoreboard (renderer) and the dashboard (Flask) are separate processes, so
they pass frames through a small PNG in /tmp, the same way Data publishes the
runtime status JSON.

Watching costs nothing when nobody is looking: the dashboard touches WANT_PATH
each time it asks for a frame, and the renderer only encodes and writes frames
while that file is fresh (WANT_TTL seconds).

Renderer side: FrameMirror.publish(image), called once per rendered frame.
Dashboard side: request_frames() / frame_age().
"""

import os
import tempfile
import time

FRAME_PATH = '/tmp/nhl-scoreboard-frame.png'
WANT_PATH = '/tmp/nhl-scoreboard-frame.want'

WANT_TTL = 10.0         # seconds a request keeps the renderer publishing
PUBLISH_INTERVAL = 0.5  # at most this often (2 fps), plenty for a status view


def request_frames(want_path=None):
    """Dashboard side: tell the renderer somebody is watching."""
    want_path = want_path or WANT_PATH
    with open(want_path, 'a'):
        os.utime(want_path)


def frame_age(frame_path=None):
    """Dashboard side: seconds since the renderer last published, else None."""
    frame_path = frame_path or FRAME_PATH
    try:
        return max(0.0, time.time() - os.stat(frame_path).st_mtime)
    except OSError:
        return None


class FrameMirror:
    def __init__(self, frame_path=None, want_path=None,
                 interval=PUBLISH_INTERVAL, ttl=WANT_TTL, clock=time.monotonic):
        self.frame_path = frame_path or FRAME_PATH
        self.want_path = want_path or WANT_PATH
        self.interval = interval
        self.ttl = ttl
        self._clock = clock
        self._next_check = 0.0
        self._last_pixels = None
        self._warned = False

    def publish(self, image):
        """Renderer side. Cheap when idle and never raises into the render loop."""
        now = self._clock()
        if now < self._next_check:
            return
        self._next_check = now + self.interval
        try:
            if self._watched():
                self._write(image)
        except Exception as e:
            if not self._warned:
                self._warned = True
                try:
                    import debug
                    debug.warning(f"Live display mirror disabled after error: {e}")
                except Exception:
                    pass
            # Back off hard rather than retrying a failing disk every frame.
            self._next_check = now + 30.0

    def _watched(self):
        try:
            return time.time() - os.stat(self.want_path).st_mtime < self.ttl
        except OSError:
            return False

    def _write(self, image):
        rgb = image.convert('RGB')
        pixels = (rgb.size, rgb.tobytes())
        if pixels == self._last_pixels and os.path.exists(self.frame_path):
            # Unchanged picture: just refresh the timestamp so the dashboard
            # can tell "static board" apart from "scoreboard not running".
            os.utime(self.frame_path)
            return
        directory = os.path.dirname(self.frame_path)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix='.nhl-frame.')
        try:
            with os.fdopen(fd, 'wb') as f:
                rgb.save(f, 'PNG', compress_level=1)
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.frame_path)  # atomic: readers never see half a file
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        self._last_pixels = pixels
