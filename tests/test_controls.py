"""Tests for the dashboard -> scoreboard control path:
src/control_channel.py, src/sbio/localcontrol.py and the /api/scoreboard
command and state endpoints.

Run with: python -m pytest tests/test_controls.py
"""

import json
import os
import threading
import time

import pytest


# --- fakes standing in for the running scoreboard ----------------------------

class Cfg:
    dimmer_enabled = False
    dimmer_sunrise_brightness = 50
    dimmer_sunset_brightness = 20


class Data:
    def __init__(self):
        self.config = Cfg()
        self.curr_board = 'clock'
        self.screensaver = False
        self.mqtt_trigger = False
        self.mqtt_showboard = 'clock'


class Matrix:
    def __init__(self):
        self.brightness = 60

    def set_brightness(self, value):
        self.brightness = value


class Saver:
    def __init__(self):
        self.calls = []

    def runSaver(self):
        self.calls.append('on')

    def stopSaver(self):
        self.calls.append('off')


@pytest.fixture
def board(le, tmp_path, monkeypatch):
    """Spool/state/want files redirected to tmp, plus a LocalControls on fakes."""
    import control_channel
    import frame_mirror
    from sbio.localcontrol import LocalControls

    assert le.control_channel is control_channel  # one shared copy
    monkeypatch.setattr(control_channel, 'SPOOL_DIR', str(tmp_path / 'spool'))
    monkeypatch.setattr(control_channel, 'STATE_PATH', str(tmp_path / 'state.json'))
    monkeypatch.setattr(frame_mirror, 'WANT_PATH', str(tmp_path / 'frame.want'))
    monkeypatch.setattr(frame_mirror, 'FRAME_PATH', str(tmp_path / 'frame.png'))

    class Board:
        data, matrix, saver = Data(), Matrix(), Saver()
        sleep = threading.Event()
        channel, mirror = control_channel, frame_mirror
        controls = LocalControls(data, matrix, sleep, saver)

    return Board


@pytest.fixture
def running(board):
    """Keep the fake scoreboard polling in the background, like the real thread."""
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            board.controls.poll_once()
            time.sleep(0.02)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    yield board
    stop.set()
    t.join(2)


# --- command validation -----------------------------------------------------------

@pytest.mark.parametrize('cmd', [
    {'action': 'brightness', 'value': 1}, {'action': 'brightness', 'value': 100},
    {'action': 'dimmer', 'sunrise': 80, 'sunset': 10},
    {'action': 'screensaver', 'value': 'on'}, {'action': 'screensaver', 'value': 'off'},
    {'action': 'showboard', 'board': 'clock'}, {'action': 'showboard', 'board': 'team_summary'},
])
def test_valid_commands_pass(board, cmd):
    assert board.channel.validate_command(cmd) == cmd


@pytest.mark.parametrize('cmd', [
    None, [], 'brightness', {}, {'action': 'reboot'}, {'action': 'brightness'},
    {'action': 'brightness', 'value': 0}, {'action': 'brightness', 'value': 101},
    {'action': 'brightness', 'value': '50'}, {'action': 'brightness', 'value': 50.5},
    {'action': 'brightness', 'value': True}, {'action': 'brightness', 'value': None},
    {'action': 'dimmer', 'sunrise': 50}, {'action': 'dimmer', 'sunrise': 50, 'sunset': 0},
    {'action': 'screensaver', 'value': 'maybe'}, {'action': 'screensaver'},
    {'action': 'showboard', 'board': ''}, {'action': 'showboard', 'board': 'Clock'},
    {'action': 'showboard', 'board': '../etc'}, {'action': 'showboard', 'board': 'a b'},
    {'action': 'showboard', 'board': 'x' * 41}, {'action': 'showboard', 'board': 5},
])
def test_invalid_commands_are_rejected(board, cmd):
    with pytest.raises(board.channel.CommandError):
        board.channel.validate_command(cmd)


def test_unknown_fields_are_dropped(board):
    cleaned = board.channel.validate_command(
        {'action': 'brightness', 'value': 5, 'shell': 'rm -rf /', '__class__': 1})
    assert cleaned == {'action': 'brightness', 'value': 5}


# --- spool ------------------------------------------------------------------------

def test_send_and_drain_preserve_order(board):
    ids = [board.channel.send({'action': 'brightness', 'value': v}) for v in (10, 20, 30)]
    got = board.channel.drain()
    assert [c['value'] for _, c in got] == [10, 20, 30]
    assert [i for i, _ in got] == ids
    assert board.channel.drain() == []  # consumed


def test_spool_is_usable_by_any_local_user(board):
    board.channel.send({'action': 'brightness', 'value': 10})
    assert os.stat(board.channel.SPOOL_DIR).st_mode & 0o777 == 0o777


def test_stale_commands_are_not_run(board):
    board.channel.send({'action': 'brightness', 'value': 10})
    name = os.listdir(board.channel.SPOOL_DIR)[0]
    old = time.time() - 60
    os.utime(os.path.join(board.channel.SPOOL_DIR, name), (old, old))
    assert board.channel.drain() == []
    assert os.listdir(board.channel.SPOOL_DIR) == []  # and cleaned up


def test_junk_in_the_spool_is_discarded(board, tmp_path):
    spool = board.channel._spool()
    good = board.channel.send({'action': 'brightness', 'value': 42})
    with open(os.path.join(spool, '00000000000000000001-junk.json'), 'w') as f:
        f.write('{not json')
    with open(os.path.join(spool, '00000000000000000002-bad.json'), 'w') as f:
        json.dump({'action': 'brightness', 'value': 9999}, f)
    with open(os.path.join(spool, '00000000000000000003-big.json'), 'w') as f:
        f.write(json.dumps({'action': 'brightness', 'value': 5, 'pad': 'x' * 5000}))
    with open(os.path.join(spool, 'notes.txt'), 'w') as f:
        f.write('ignored')
    got = board.channel.drain()
    assert [i for i, _ in got] == [good]
    assert sorted(os.listdir(spool)) == ['notes.txt']


def test_symlinks_in_the_spool_are_never_followed(board, tmp_path):
    spool = board.channel._spool()
    target = tmp_path / 'elsewhere.json'
    target.write_text(json.dumps({'action': 'brightness', 'value': 7}))
    os.symlink(target, os.path.join(spool, '00000000000000000001-link.json'))
    assert board.channel.drain() == []
    assert target.exists()  # the link was removed, not what it pointed at


def test_a_command_is_never_run_twice(board):
    seen = set()
    spool = board.channel._spool()
    path = os.path.join(spool, '00000000000000000005-abc.json')
    runs = []
    for _attempt in range(2):  # file reappears under the same id (e.g. could not be deleted)
        with open(path, 'w') as f:
            json.dump({'action': 'brightness', 'value': 5}, f)
        runs.append(len(board.channel.drain(seen=seen)))
    assert runs == [1, 0]


def test_withdraw_cancels_a_pending_command(board):
    cid = board.channel.send({'action': 'brightness', 'value': 10})
    board.channel.withdraw(cid)
    assert board.channel.drain() == []


def test_state_round_trip(board):
    assert board.channel.read_state() is None
    board.channel.publish_state({'board': 'clock'})
    state = board.channel.read_state()
    assert state['board'] == 'clock' and 0 <= state['age'] < 5
    with open(board.channel.STATE_PATH, 'w') as f:
        f.write('garbage')
    assert board.channel.read_state() is None


# --- what the scoreboard does with each command -------------------------------------

def test_brightness(board):
    ok, msg = board.controls.handle({'action': 'brightness', 'value': 35})
    assert ok and board.matrix.brightness == 35 and '35' in msg


def test_brightness_is_refused_while_the_dimmer_is_on(board):
    board.data.config.dimmer_enabled = True
    ok, msg = board.controls.handle({'action': 'brightness', 'value': 35})
    assert not ok and 'dimmer' in msg.lower() and board.matrix.brightness == 60


def test_dimmer_levels(board):
    ok, _ = board.controls.handle({'action': 'dimmer', 'sunrise': 70, 'sunset': 15})
    assert not ok  # dimmer off in config: refused
    board.data.config.dimmer_enabled = True
    ok, _ = board.controls.handle({'action': 'dimmer', 'sunrise': 70, 'sunset': 15})
    assert ok and (board.data.config.dimmer_sunrise_brightness,
                   board.data.config.dimmer_sunset_brightness) == (70, 15)


def test_screensaver(board):
    assert board.controls.handle({'action': 'screensaver', 'value': 'on'})[0]
    assert board.controls.handle({'action': 'screensaver', 'value': 'off'})[0]
    assert board.saver.calls == ['on', 'off']
    board.controls.screensaver = None
    ok, msg = board.controls.handle({'action': 'screensaver', 'value': 'on'})
    assert not ok and 'enabled' in msg


def test_showboard_uses_the_same_trigger_as_mqtt(board):
    ok, _ = board.controls.handle({'action': 'showboard', 'board': 'weather'})
    assert ok and board.data.mqtt_showboard == 'weather' and board.data.mqtt_trigger
    assert board.sleep.is_set()  # wakes the render loop immediately


def test_a_failing_command_reports_instead_of_crashing(board):
    def boom():
        raise RuntimeError('no start time configured')
    board.saver.runSaver = boom
    ok, msg = board.controls.handle({'action': 'screensaver', 'value': 'on'})
    assert not ok and 'no start time configured' in msg


# --- polling and state publishing ----------------------------------------------------

def test_poll_runs_commands_and_publishes_results(board):
    cid = board.channel.send({'action': 'brightness', 'value': 44})
    board.controls.poll_once()
    state = board.channel.read_state()
    assert board.matrix.brightness == 44
    assert state['results'][cid] == {'ok': True, 'message': 'Brightness set to 44%.'}
    assert state['board'] == 'clock' and state['brightness'] == 44
    assert state['dimmer_enabled'] is False and state['screensaver_available'] is True


def test_idle_scoreboard_does_not_write_state_when_nobody_watches(board):
    board.controls.poll_once()
    assert not os.path.exists(board.channel.STATE_PATH)
    board.mirror.request_frames()  # a dashboard page opens
    board.controls.poll_once()
    assert os.path.exists(board.channel.STATE_PATH)


def test_only_recent_results_are_kept(board):
    for v in range(1, 31):
        board.channel.send({'action': 'brightness', 'value': v})
    board.controls.poll_once()
    assert len(board.controls.results) == 20
    assert board.matrix.brightness == 30  # all thirty ran, in order


# --- the dashboard endpoints -----------------------------------------------------------

def test_command_endpoint_round_trip(le, authed, running):
    r = authed.post('/api/scoreboard/command', json={'action': 'brightness', 'value': 25})
    assert r.status_code == 200 and r.get_json() == {'ok': True, 'message': 'Brightness set to 25%.'}
    assert running.matrix.brightness == 25


def test_command_endpoint_reports_a_refusal(le, authed, running):
    running.data.config.dimmer_enabled = True
    r = authed.post('/api/scoreboard/command', json={'action': 'brightness', 'value': 25})
    assert r.status_code == 409 and r.get_json()['ok'] is False
    assert 'dimmer' in r.get_json()['message'].lower()


@pytest.mark.parametrize('body', [
    {}, {'action': 'brightness', 'value': 500}, {'action': 'rm', 'value': 1}, {'action': 'showboard', 'board': 'x;id'},
    {'action': 'showboard', 'board': 'no_such_board'},
])
def test_command_endpoint_rejects_bad_requests(le, authed, running, body):
    r = authed.post('/api/scoreboard/command', json=body)
    assert r.status_code == 400 and r.get_json()['ok'] is False
    spool = running.channel.SPOOL_DIR
    assert not os.path.isdir(spool) or os.listdir(spool) == []  # nothing was queued


def test_command_endpoint_rejects_non_json(le, authed, running):
    r = authed.post('/api/scoreboard/command', data='action=brightness', content_type='text/plain')
    assert r.status_code == 400


def test_showboard_accepts_real_boards(le, authed, running):
    r = authed.post('/api/scoreboard/command', json={'action': 'showboard', 'board': 'clock'})
    assert r.status_code == 200 and running.data.mqtt_showboard == 'clock'


def test_command_times_out_cleanly_when_the_scoreboard_is_not_running(le, authed, board, monkeypatch):
    monkeypatch.setattr(le, 'COMMAND_TIMEOUT', 0.3)
    r = authed.post('/api/scoreboard/command', json={'action': 'brightness', 'value': 25})
    assert r.status_code == 504 and "didn't respond" in r.get_json()['message']
    # ...and the command is withdrawn, so it cannot fire when the scoreboard starts later
    assert board.channel.drain() == []


def test_now_endpoint(le, authed, running):
    assert authed.get('/api/scoreboard/now').get_json()['available'] in (True, False)
    deadline = time.time() + 3
    while time.time() < deadline:  # the poll thread publishes once a viewer is present
        body = authed.get('/api/scoreboard/now').get_json()
        if body['available']:
            break
        time.sleep(0.05)
    assert body['available'] is True
    assert body['board'] == 'clock' and body['brightness'] == 60
    assert 'results' not in body  # internal bookkeeping stays internal


def test_now_endpoint_when_scoreboard_is_down(le, authed, board):
    assert authed.get('/api/scoreboard/now').get_json() == {'available': False}
    os.makedirs(os.path.dirname(board.channel.STATE_PATH), exist_ok=True)
    board.channel.publish_state({'board': 'clock'})
    old = time.time() - 60
    os.utime(board.channel.STATE_PATH, (old, old))
    assert authed.get('/api/scoreboard/now').get_json() == {'available': False}


def test_control_endpoints_need_a_login(le, authed, running):
    anon = le.app.test_client()
    assert anon.post('/api/scoreboard/command', json={'action': 'brightness', 'value': 5}).status_code == 401
    assert anon.get('/api/scoreboard/now').status_code == 401
    assert running.matrix.brightness == 60
