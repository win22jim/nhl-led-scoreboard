import logging
import os
import threading
import time

import frame_mirror

debug = logging.getLogger("scoreboard")

MAX_PREVIEW_SECONDS = 45.0
PREVIEW_FRAME_INTERVAL = 0.2   # up to 5 frames a second to the browser


class _PreviewPanel(object):
    """Stands in for the LED hardware: a preview draws, but nothing lights up."""

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.brightness = 100

    def SetImage(self, image, *args, **kwargs):
        pass  # the frame is handed to the dashboard by the matrix's frame mirror

    def Clear(self):
        pass

    def Fill(self, *args):
        pass


class _GuardedMirror(frame_mirror.FrameMirror):
    """Stops publishing the moment its preview is stopped or replaced, so a board
    that is slow to notice cannot overwrite the next preview's frames."""

    def __init__(self, stop, **kwargs):
        super().__init__(**kwargs)
        self._preview_stopped = stop

    def publish(self, image):
        if not self._preview_stopped.is_set():
            super().publish(image)


def _default_matrix_factory(width, height):
    from renderer.matrix import Matrix
    return Matrix(_PreviewPanel(width, height))


class BoardPreview(object):
    """Renders one board to the dashboard without touching the real panel.

    The board is built fresh on a "shadow" matrix (same size as the panel, no
    hardware behind it) whose frames go to their own file, and it runs on its
    own thread with its own sleep event, so the real rotation carries on
    undisturbed. One preview at a time; starting another replaces it, and a
    preview ends by itself after MAX_PREVIEW_SECONDS.
    """

    def __init__(self, data, matrix, matrix_factory=None, max_seconds=MAX_PREVIEW_SECONDS):
        self.data = data
        self.matrix = matrix
        self.matrix_factory = matrix_factory or _default_matrix_factory
        self.max_seconds = max_seconds
        self._lock = threading.Lock()
        self._stop = None
        self._status = {'board': None, 'running': False, 'error': None}

    def status(self):
        with self._lock:
            return dict(self._status)

    def start(self, board_id):
        """Begin previewing `board_id`. Returns (ok, message)."""
        registry = getattr(self.data, 'boards_registry', None)
        boards = getattr(registry, '_boards', None)
        if boards is None:
            return False, "Preview isn't ready yet: the scoreboard is still starting up."
        if board_id not in boards:
            return False, f"The scoreboard doesn't have a '{board_id}' board."
        self.stop()
        try:
            os.remove(frame_mirror.PREVIEW_FRAME_PATH)  # don't show the previous board's last frame
        except OSError:
            pass
        stop = threading.Event()
        with self._lock:
            self._stop = stop
            self._status = {'board': board_id, 'running': True, 'error': None}
        thread = threading.Thread(target=self._run, args=(boards[board_id], board_id, stop), daemon=True)
        thread.start()
        return True, f"Previewing {board_id} (it never appears on the panel)."

    def stop(self):
        with self._lock:
            stop, self._stop = self._stop, None
            if stop is not None:
                self._status = dict(self._status, running=False)
        if stop is not None:
            stop.set()
            return True
        return False

    def _finish(self, stop, error=None):
        with self._lock:
            if self._stop is stop:  # not already replaced by a newer preview
                self._status = dict(self._status, running=False, error=error)
                self._stop = None

    def _run(self, board_class, board_id, stop):
        timer = threading.Timer(self.max_seconds, stop.set)
        timer.daemon = True
        timer.start()
        error = None
        try:
            shadow = self.matrix_factory(self.matrix.width, self.matrix.height)
            shadow.frame_mirror = _GuardedMirror(
                stop, frame_path=frame_mirror.PREVIEW_FRAME_PATH, interval=PREVIEW_FRAME_INTERVAL)
            board = board_class(self.data, shadow, stop)
            run = getattr(board, 'render', None) or getattr(board, 'draw')
            while not stop.is_set():
                started = time.monotonic()
                run()
                if time.monotonic() - started < 0.2:
                    stop.wait(0.2)  # a board that returns at once must not spin
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            debug.error(f"Board preview of {board_id} failed: {error}")
        finally:
            timer.cancel()
            self._finish(stop, error)
