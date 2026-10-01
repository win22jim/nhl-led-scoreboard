"""Tests for previewing a board from the dashboard without touching the panel:
src/sbio/boardpreview.py, its control commands and the preview-frame endpoint.

Run with: python -m pytest tests/test_board_preview.py
"""

import os
import sys
import threading
import time
import types

import pytest
from PIL import Image


def wait_for(condition, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


# --- fakes -------------------------------------------------------------------------

class ShadowMatrix:
    """What the real Matrix does for a preview: draw, then hand the frame to its mirror."""

    def __init__(self, width, height):
        self.width, self.height = width, height
        self.image = Image.new('RGB', (width, height))
        self.frame_mirror = None

    def render(self):
        self.frame_mirror.publish(self.image)


class GoodBoard:
    def __init__(self, data, matrix, sleep_event):
        self.matrix = matrix

    def render(self):
        self.matrix.image.paste((255, 0, 0), (0, 0, self.matrix.width, self.matrix.height))
        self.matrix.render()


class BoomBoard:
    def __init__(self, data, matrix, sleep_event):
        raise ValueError('no data')


class SlowBoard:
    """Waits on the sleep event it was given, like a board pacing itself."""
    instances = []

    def __init__(self, data, matrix, sleep_event):
        self.matrix, self.sleep_event = matrix, sleep_event
        SlowBoard.instances.append(self)

    def render(self):
        self.matrix.render()
        self.sleep_event.wait(30)


class Data:
    pass


class PanelSize:
    width, height = 64, 32


@pytest.fixture
def pv(le, tmp_path, monkeypatch):
    import control_channel
    import frame_mirror
    from sbio.boardpreview import BoardPreview

    monkeypatch.setattr(control_channel, 'SPOOL_DIR', str(tmp_path / 'spool'))
    monkeypatch.setattr(control_channel, 'STATE_PATH', str(tmp_path / 'state.json'))
    monkeypatch.setattr(frame_mirror, 'WANT_PATH', str(tmp_path / 'frame.want'))
    monkeypatch.setattr(frame_mirror, 'FRAME_PATH', str(tmp_path / 'frame.png'))
    monkeypatch.setattr(frame_mirror, 'PREVIEW_FRAME_PATH', str(tmp_path / 'preview.png'))
    SlowBoard.instances = []

    class Env:
        data = Data()
        data.boards_registry = types.SimpleNamespace(
            _boards={'good': GoodBoard, 'boom': BoomBoard, 'slow': SlowBoard, 'clock': GoodBoard})
        channel, mirror = control_channel, frame_mirror
        preview = BoardPreview(data, PanelSize(), matrix_factory=ShadowMatrix, max_seconds=30)

    yield Env
    Env.preview.stop()


# --- the runner -------------------------------------------------------------------------

def test_preview_needs_the_registry(pv):
    pv.data.boards_registry = None
    ok, msg = pv.preview.start('good')
    assert not ok and 'starting up' in msg


def test_unknown_board_is_refused(pv):
    ok, msg = pv.preview.start('nope')
    assert not ok and 'nope' in msg
    assert pv.preview.status()['running'] is False


def test_preview_publishes_the_boards_frames_to_its_own_file(pv):
    pv.mirror.request_frames()  # a dashboard page is open
    ok, _ = pv.preview.start('good')
    assert ok and pv.preview.status() == {'board': 'good', 'running': True, 'error': None}
    assert wait_for(lambda: os.path.exists(pv.mirror.PREVIEW_FRAME_PATH)), pv.preview.status()
    with Image.open(pv.mirror.PREVIEW_FRAME_PATH) as img:
        assert img.size == (64, 32) and img.convert('RGB').getpixel((10, 10)) == (255, 0, 0)


def test_preview_never_touches_the_live_panel_frame(pv):
    # the one property that must hold: the picture of the real panel is not replaced
    pv.mirror.request_frames()
    Image.new('RGB', (64, 32), (0, 0, 255)).save(pv.mirror.FRAME_PATH)
    before = open(pv.mirror.FRAME_PATH, 'rb').read()
    pv.preview.start('good')
    assert wait_for(lambda: os.path.exists(pv.mirror.PREVIEW_FRAME_PATH))
    time.sleep(0.5)
    assert open(pv.mirror.FRAME_PATH, 'rb').read() == before


def test_no_frames_are_written_when_nobody_is_watching(pv):
    pv.preview.start('good')
    time.sleep(0.6)
    assert not os.path.exists(pv.mirror.PREVIEW_FRAME_PATH)


def test_the_previous_boards_last_frame_is_cleared_on_start(pv):
    Image.new('RGB', (64, 32), (0, 255, 0)).save(pv.mirror.PREVIEW_FRAME_PATH)
    pv.preview.start('good')  # nobody watching, so nothing new is written
    assert not os.path.exists(pv.mirror.PREVIEW_FRAME_PATH)


def test_stop_ends_a_board_that_is_waiting(pv):
    pv.preview.start('slow')
    assert wait_for(lambda: SlowBoard.instances)
    assert pv.preview.stop() is True
    assert pv.preview.status()['running'] is False
    assert SlowBoard.instances[0].sleep_event.is_set()  # the board is woken, not abandoned
    assert pv.preview.stop() is False  # nothing left to stop


def test_a_new_preview_replaces_the_old_one(pv):
    pv.preview.start('slow')
    assert wait_for(lambda: SlowBoard.instances)
    pv.preview.start('good')
    assert pv.preview.status()['board'] == 'good' and pv.preview.status()['running']
    assert SlowBoard.instances[0].sleep_event.is_set()


def test_a_failing_board_reports_instead_of_crashing(pv):
    pv.preview.start('boom')
    assert wait_for(lambda: not pv.preview.status()['running'])
    assert 'ValueError: no data' in pv.preview.status()['error']


def test_a_preview_ends_by_itself(pv):
    from sbio.boardpreview import BoardPreview
    short = BoardPreview(pv.data, PanelSize(), matrix_factory=ShadowMatrix, max_seconds=0.3)
    short.start('slow')
    assert wait_for(lambda: not short.status()['running'], timeout=3)
    assert short.status()['error'] is None


def test_a_stopped_preview_cannot_overwrite_a_newer_ones_frames(pv):
    from sbio.boardpreview import _GuardedMirror
    stop = threading.Event()
    pv.mirror.request_frames()
    mirror = _GuardedMirror(stop, frame_path=pv.mirror.PREVIEW_FRAME_PATH, interval=0)
    mirror.publish(Image.new('RGB', (4, 4), (255, 0, 0)))
    assert os.path.exists(pv.mirror.PREVIEW_FRAME_PATH)
    os.remove(pv.mirror.PREVIEW_FRAME_PATH)
    stop.set()
    mirror.publish(Image.new('RGB', (4, 4), (255, 0, 0)))
    assert not os.path.exists(pv.mirror.PREVIEW_FRAME_PATH)


def test_the_real_matrix_draws_a_preview_without_hardware(pv, monkeypatch):
    pytest.importorskip('numpy')
    for name, mod in {
        'driver': types.SimpleNamespace(is_hardware=lambda: False, is_emulated=lambda: True),
        'RGBMatrixEmulator': types.SimpleNamespace(graphics=object()),
        'utils': types.SimpleNamespace(round_normal=lambda x, *a: round(x)),
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, 'renderer.matrix', raising=False)
    from sbio.boardpreview import BoardPreview

    class RealishBoard:
        def __init__(self, data, matrix, sleep_event):
            self.matrix = matrix

        def render(self):
            self.matrix.image.paste((0, 200, 0), (0, 0, 20, 10))
            self.matrix.render()  # the real Matrix.render, drawing onto a fake panel

    pv.data.boards_registry = types.SimpleNamespace(_boards={'real': RealishBoard})
    preview = BoardPreview(pv.data, PanelSize())  # default factory = the real Matrix class
    pv.mirror.request_frames()
    assert preview.start('real')[0]
    try:
        assert wait_for(lambda: os.path.exists(pv.mirror.PREVIEW_FRAME_PATH))
        with Image.open(pv.mirror.PREVIEW_FRAME_PATH) as img:
            assert img.size == (64, 32) and img.convert('RGB').getpixel((5, 5)) == (0, 200, 0)
        assert not os.path.exists(pv.mirror.FRAME_PATH)  # the live panel's frame is untouched
    finally:
        preview.stop()


# --- panel sizes -------------------------------------------------------------------------

# Panels come in many sizes (chained and stacked); the preview must be exactly the
# configured panel, because boards choose layouts and clip text by matrix size.
SIZES = [(32, 32), (64, 32), (128, 32), (128, 64), (192, 64), (64, 64), (256, 128)]


@pytest.mark.parametrize('w,h', SIZES)
def test_preview_is_exactly_the_panels_size(pv, w, h):
    from sbio.boardpreview import BoardPreview
    seen = []

    class Measuring:
        def __init__(self, data, matrix, sleep_event):
            seen.append((matrix.width, matrix.height))  # what a board reads to pick its layout
            self.matrix = matrix

        def render(self):
            self.matrix.render()

    pv.data.boards_registry = types.SimpleNamespace(_boards={'m': Measuring})
    preview = BoardPreview(pv.data, types.SimpleNamespace(width=w, height=h), matrix_factory=ShadowMatrix)
    pv.mirror.request_frames()
    try:
        assert preview.start('m')[0]
        assert wait_for(lambda: os.path.exists(pv.mirror.PREVIEW_FRAME_PATH))
        with Image.open(pv.mirror.PREVIEW_FRAME_PATH) as img:
            assert img.size == (w, h)
        assert seen[0] == (w, h)
    finally:
        preview.stop()


@pytest.mark.parametrize('w,h', SIZES)
def test_the_real_matrix_previews_at_every_panel_size(pv, monkeypatch, w, h):
    pytest.importorskip('numpy')
    for name, mod in {
        'driver': types.SimpleNamespace(is_hardware=lambda: False, is_emulated=lambda: True),
        'RGBMatrixEmulator': types.SimpleNamespace(graphics=object()),
        'utils': types.SimpleNamespace(round_normal=lambda x, *a: round(x)),
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, 'renderer.matrix', raising=False)
    from sbio.boardpreview import BoardPreview

    class Corner:  # lights the last pixel, so a wrongly sized canvas would show
        def __init__(self, data, matrix, sleep_event):
            self.matrix = matrix

        def render(self):
            self.matrix.image.putpixel((self.matrix.width - 1, self.matrix.height - 1), (255, 255, 255))
            self.matrix.render()

    pv.data.boards_registry = types.SimpleNamespace(_boards={'corner': Corner})
    preview = BoardPreview(pv.data, types.SimpleNamespace(width=w, height=h))  # real Matrix class
    pv.mirror.request_frames()
    try:
        assert preview.start('corner')[0]
        assert wait_for(lambda: os.path.exists(pv.mirror.PREVIEW_FRAME_PATH))
        with Image.open(pv.mirror.PREVIEW_FRAME_PATH) as img:
            assert img.size == (w, h)
            assert img.convert('RGB').getpixel((w - 1, h - 1)) == (255, 255, 255)
    finally:
        preview.stop()


# --- commands ---------------------------------------------------------------------------

def test_preview_commands_validate(pv):
    v = pv.channel.validate_command
    assert v({'action': 'preview', 'board': 'clock'}) == {'action': 'preview', 'board': 'clock'}
    assert v({'action': 'preview_stop', 'junk': 1}) == {'action': 'preview_stop'}
    for bad in ({'action': 'preview'}, {'action': 'preview', 'board': '../x'},
                {'action': 'preview', 'board': 'Clock'}):
        with pytest.raises(pv.channel.CommandError):
            v(bad)


class FakeCfg:
    dimmer_enabled = False
    dimmer_sunrise_brightness = 50
    dimmer_sunset_brightness = 20


class FakeData:
    config = FakeCfg()
    curr_board = 'clock'
    screensaver = False
    mqtt_trigger = False
    mqtt_showboard = 'clock'


class FakeMatrix:
    brightness = 60


class FakePreview:
    def __init__(self):
        self.calls = []

    def start(self, board):
        self.calls.append(('start', board))
        return True, f'Previewing {board}.'

    def stop(self):
        self.calls.append(('stop',))

    def status(self):
        return {'board': 'clock', 'running': True, 'error': None}


def test_local_controls_route_preview_commands(pv):
    from sbio.localcontrol import LocalControls
    fake = FakePreview()
    controls = LocalControls(FakeData(), FakeMatrix(), threading.Event(), None, preview=fake)
    assert controls.handle({'action': 'preview', 'board': 'clock'}) == (True, 'Previewing clock.')
    assert controls.handle({'action': 'preview_stop'})[0] is True
    assert fake.calls == [('start', 'clock'), ('stop',)]
    assert controls.state()['preview'] == {'board': 'clock', 'running': True, 'error': None}


# --- dashboard endpoints ------------------------------------------------------------------

@pytest.fixture
def running(pv):
    from sbio.localcontrol import LocalControls
    fake = FakePreview()
    controls = LocalControls(FakeData(), FakeMatrix(), threading.Event(), None, preview=fake)
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            controls.poll_once()
            time.sleep(0.02)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    pv.fake = fake
    yield pv
    stop.set()
    t.join(2)


def test_preview_command_endpoint(le, authed, running):
    r = authed.post('/api/scoreboard/command', json={'action': 'preview', 'board': 'clock'})
    assert r.status_code == 200 and r.get_json()['ok'] is True
    assert running.fake.calls == [('start', 'clock')]
    r = authed.post('/api/scoreboard/command', json={'action': 'preview_stop'})
    assert r.status_code == 200 and running.fake.calls[-1] == ('stop',)


def test_preview_of_an_unknown_board_is_rejected_before_it_is_sent(le, authed, running):
    r = authed.post('/api/scoreboard/command', json={'action': 'preview', 'board': 'no_such_board'})
    assert r.status_code == 400 and running.fake.calls == []


def test_preview_frame_endpoint(le, authed, pv):
    r = authed.get('/api/scoreboard/preview-frame')
    assert r.status_code == 503 and r.headers['Cache-Control'] == 'no-store'
    Image.new('RGB', (64, 32), (1, 2, 3)).save(pv.mirror.PREVIEW_FRAME_PATH)
    r = authed.get('/api/scoreboard/preview-frame')
    assert r.status_code == 200 and r.mimetype == 'image/png' and float(r.headers['X-Frame-Age']) < 5
    # it is a different picture from the live panel's
    assert authed.get('/api/scoreboard/frame').status_code == 503


def test_preview_endpoints_need_a_login(le, authed, running):
    anon = le.app.test_client()
    assert anon.get('/api/scoreboard/preview-frame').status_code == 401
    assert anon.post('/api/scoreboard/command', json={'action': 'preview', 'board': 'clock'}).status_code == 401
    assert running.fake.calls == []
