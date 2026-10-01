"""Tests for the dashboard login and the input hardening in src/logo_editor.py.

Run with: python -m pytest tests/test_dashboard_auth.py

logo_editor is imported against a throwaway install directory (--dir), so
nothing in the real config/ folder is read or written.
"""

import os
import shlex
import stat
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).parent.parent / "src")

PASSWORD = "correct horse"


@pytest.fixture
def client(le):
    return le.app.test_client()


def claim(client, password=PASSWORD):
    return client.post('/setup', data={'password': password, 'confirm': password})


# --- first run ---------------------------------------------------------------

def test_first_run_redirects_pages_to_setup(client):
    r = client.get('/dashboard')
    assert r.status_code == 302 and r.headers['Location'].endswith('/setup')


def test_first_run_api_reports_setup_required(client):
    r = client.get('/api/scoreboard/status')
    assert r.status_code == 401 and r.get_json()['error'] == 'setup_required'


def test_setup_page_renders(client):
    r = client.get('/setup')
    assert r.status_code == 200 and b'Choose a password' in r.data


def test_setup_is_refused_from_the_public_internet(client):
    r = client.post('/setup', data={'password': PASSWORD, 'confirm': PASSWORD},
                    environ_base={'REMOTE_ADDR': '8.8.8.8'})
    assert r.status_code == 403
    assert client.get('/login').headers['Location'].endswith('/setup')  # still unclaimed


def test_setup_validates_the_password(client):
    assert claim(client, 'short').status_code == 400
    r = client.post('/setup', data={'password': PASSWORD, 'confirm': 'different one'})
    assert r.status_code == 400 and b'do not match' in r.data


def test_setup_signs_you_in_and_cannot_be_repeated(le, client):
    r = claim(client)
    assert r.status_code == 302 and r.headers['Location'].endswith('/dashboard')
    assert client.get('/api/scoreboard/config').status_code != 401  # 404: no config file
    other = le.app.test_client()
    r = other.post('/setup', data={'password': 'attacker pw', 'confirm': 'attacker pw'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/login')


def test_auth_file_is_private_and_stores_no_plaintext(le):
    c = le.app.test_client()
    claim(c)
    mode = stat.S_IMODE(os.stat(le.AUTH_FILE).st_mode)
    assert mode == 0o600
    assert PASSWORD not in Path(le.AUTH_FILE).read_text()


# --- logging in --------------------------------------------------------------

def test_requires_login_after_setup(le, authed):
    c = le.app.test_client()
    r = c.get('/dashboard')
    assert r.status_code == 302 and '/login?next=/dashboard' in r.headers['Location']
    r = c.get('/api/scoreboard/config')
    assert r.status_code == 401 and r.get_json()['error'] == 'login_required'
    assert c.get('/api/health').status_code == 200  # intentionally public


@pytest.mark.parametrize('path', [
    '/', '/team_summary', '/api/files', '/api/colors', '/api/scoreboard/logs',
    '/api/scoreboard/frame', '/assets/logos/WPG/light/64x32.png', '/api/emulator/status',
])
def test_every_other_route_is_protected(le, authed, path):
    c = le.app.test_client()
    assert c.get(path).status_code in (302, 401)


def test_all_mutating_routes_are_protected(le, authed):
    c = le.app.test_client()
    for rule in le.app.url_map.iter_rules():
        if rule.endpoint in ('static',) or not (rule.methods & {'POST', 'PUT', 'DELETE'}):
            continue
        if rule.endpoint.startswith('auth.'):
            continue
        path = rule.rule.replace('<filename>', 'x.json')
        r = c.post(path, json={})
        assert r.status_code == 401, f'{rule.endpoint} ({path}) is reachable without login'


def test_login_wrong_then_right(le, authed):
    c = le.app.test_client()
    r = c.post('/login', data={'password': 'nope nope nope'})
    assert r.status_code == 401 and b'Incorrect password' in r.data
    assert c.get('/api/scoreboard/config').status_code == 401
    r = c.post('/login', data={'password': PASSWORD})
    assert r.status_code == 302
    assert c.get('/api/scoreboard/config').status_code != 401


def test_login_follows_only_local_next_urls(le, authed):
    for bad in ('//evil.example', 'https://evil.example', '/\\evil.example', 'javascript:alert(1)'):
        c = le.app.test_client()
        r = c.post('/login', data={'password': PASSWORD, 'next': bad})
        assert r.headers['Location'].endswith('/dashboard'), bad
    c = le.app.test_client()
    r = c.post('/login', data={'password': PASSWORD, 'next': '/team_summary'})
    assert r.headers['Location'].endswith('/team_summary')


def test_repeated_failures_lock_the_client_out(le, authed):
    c = le.app.test_client()
    for _ in range(5):
        assert c.post('/login', data={'password': 'wrong wrong'}).status_code == 401
    r = c.post('/login', data={'password': PASSWORD})  # even the right one
    assert r.status_code == 429 and int(r.headers['Retry-After']) > 0
    # a different client is unaffected
    other = le.app.test_client()
    r = other.post('/login', data={'password': PASSWORD}, environ_base={'REMOTE_ADDR': '192.168.1.50'})
    assert r.status_code == 302


def test_logout(authed):
    assert authed.post('/logout').status_code == 302
    assert authed.get('/api/scoreboard/config').status_code == 401


# --- changing the password ---------------------------------------------------

def test_change_password(le, authed):
    phone = le.app.test_client()
    phone.post('/login', data={'password': PASSWORD})
    assert phone.get('/api/scoreboard/config').status_code != 401

    r = authed.post('/api/auth/password', json={'current': 'wrong wrong', 'new': 'brand new pw'})
    assert r.status_code == 403
    r = authed.post('/api/auth/password', json={'current': PASSWORD, 'new': 'short'})
    assert r.status_code == 400
    r = authed.post('/api/auth/password', json={'current': PASSWORD, 'new': 'brand new pw'})
    assert r.status_code == 200

    assert authed.get('/api/scoreboard/config').status_code != 401  # this device stays in
    assert phone.get('/api/scoreboard/config').status_code == 401   # others are signed out
    fresh = le.app.test_client()
    assert fresh.post('/login', data={'password': PASSWORD}).status_code == 401
    assert fresh.post('/login', data={'password': 'brand new pw'}).status_code == 302


def test_cli_password_reset_signs_everyone_out(le, authed):
    le.auth_store.set_password('reset from the shell')  # what --set-password does
    assert authed.get('/api/scoreboard/config').status_code == 401
    c = le.app.test_client()
    assert c.post('/login', data={'password': 'reset from the shell'}).status_code == 302


# --- cross-site request forgery ---------------------------------------------

def test_cross_origin_posts_are_rejected_even_when_signed_in(authed):
    r = authed.post('/api/scoreboard/control', json={'action': 'restart'},
                    headers={'Origin': 'http://evil.example'})
    assert r.status_code == 403
    r = authed.post('/api/scoreboard/control', json={'action': 'restart'},
                    headers={'Origin': 'null'})
    assert r.status_code == 403
    r = authed.post('/api/scoreboard/control', json={'action': 'restart'},
                    headers={'Sec-Fetch-Site': 'cross-site'})
    assert r.status_code == 403


def test_same_origin_posts_pass_the_gate(authed):
    r = authed.post('/api/scoreboard/control', json={'action': 'bogus'},
                    headers={'Origin': 'http://localhost'})
    assert r.status_code == 400  # reached the handler, which rejects the action


def test_cross_origin_login_is_rejected(le, authed):
    c = le.app.test_client()
    r = c.post('/login', data={'password': PASSWORD}, headers={'Origin': 'http://evil.example'})
    assert r.status_code == 403


def test_session_cookie_flags(le, authed):
    c = le.app.test_client()
    r = c.post('/login', data={'password': PASSWORD})
    cookie = r.headers['Set-Cookie']
    assert 'HttpOnly' in cookie and 'SameSite=Lax' in cookie


def test_polling_never_reissues_the_session_cookie(le, authed):
    # Re-sending the cookie on every response lets another tab's in-flight
    # request undo a sign-out. Only login/password change may set it.
    for path in ('/api/scoreboard/status', '/api/scoreboard/frame', '/api/health'):
        assert 'Set-Cookie' not in authed.get(path).headers, path


def test_signing_out_in_one_tab_signs_out_the_others(le, authed):
    # same browser = same cookie jar: a late request from tab B must not revive tab A's logout
    assert authed.post('/logout').status_code == 302
    r = authed.get('/api/scoreboard/status')
    assert r.status_code == 401 and 'Set-Cookie' not in r.headers


# --- is_local_client ---------------------------------------------------------

@pytest.mark.parametrize('addr,expected', [
    ('127.0.0.1', True), ('::1', True), ('192.168.1.5', True), ('10.0.0.2', True),
    ('172.16.5.5', True), ('169.254.1.1', True), ('fe80::1%eth0', True),
    ('fd12:3456::1', True), ('100.64.0.7', True),            # Tailscale / CGNAT
    ('::ffff:192.168.0.1', True), ('::ffff:8.8.8.8', False),
    ('8.8.8.8', False), ('2001:4860:4860::8888', False),
    ('', False), (None, False), ('not-an-ip', False),
])
def test_is_local_client(le, addr, expected):
    assert sys.modules[le.init_auth.__module__].is_local_client(addr) is expected


# --- hardening of the existing endpoints ------------------------------------

class FakePopen:
    commands = []

    def __init__(self, cmd, **kw):
        FakePopen.commands.append(cmd)
        self.pid = 4242

    def poll(self):
        return None


@pytest.fixture
def popen(le, monkeypatch):
    FakePopen.commands = []
    monkeypatch.setattr(le.subprocess, 'Popen', FakePopen)
    return FakePopen


@pytest.mark.parametrize('payload', [
    {'mode': 'simulator', 'team': 'WPG; touch /tmp/pwned', 'date': '2025-01-01'},
    {'mode': 'simulator', 'team': 'WPG', 'date': '2025-01-01 && reboot'},
    {'mode': 'simulator', 'team': 'WPG', 'date': '2025-01-01', 'speed': '1; id'},
    {'mode': 'simulator', 'team': 'WPG', 'date': '2025-01-01', 'speed': -1},
    {'mode': 'simulator', 'team': '$(id)', 'date': '2025-01-01'},
    {'mode': 'simulator', 'team': '', 'date': '2025-01-01'},
    {'mode': 'live', 'w': '64; id', 'h': 32},
    {'mode': 'live', 'w': 99999, 'h': 32},
    {'mode': 'rm -rf /'},
])
def test_emulator_start_rejects_shell_metacharacters(authed, popen, payload):
    r = authed.post('/api/emulator/start', json=payload)
    assert r.status_code == 400
    assert popen.commands == []


def test_emulator_start_accepts_valid_requests(authed, popen):
    r = authed.post('/api/emulator/start', json={
        'mode': 'simulator', 'team': 'wpg', 'date': '2025-01-01',
        'speed': 2, 'w': 128, 'h': 64, 'stop_at_end': True})
    assert r.status_code == 200
    cmd = popen.commands[0]
    assert '--team WPG --date 2025-01-01 --speed 2.0' in cmd
    assert '--led-cols=128 --led-rows=64' in cmd and '--stop-at-end' in cmd
    assert shlex.split(cmd.split('&&')[-1])  # parses as a well-formed command line


def test_emulator_start_live_mode(authed, popen):
    r = authed.post('/api/emulator/start', json={'mode': 'live', 'w': 64, 'h': 32})
    assert r.status_code == 200 and 'src/main.py' in popen.commands[0]


@pytest.mark.parametrize('team', ['../../etc', '..', 'a/b', 'WPG/../..', '', 'x' * 40])
def test_discard_alt_rejects_path_traversal(le, authed, team, monkeypatch):
    rmtree_calls = []
    monkeypatch.setattr(le.shutil, 'rmtree', lambda *a, **k: rmtree_calls.append(a))
    r = authed.post('/api/discard_alt', json={'team': team})
    assert r.status_code == 400
    assert rmtree_calls == []


def test_upload_alt_rejects_bad_team(authed):
    r = authed.post('/api/upload_alt', data={'team': '../../x', 'url': 'http://127.0.0.1:1/x.png'})
    # either the download fails first (400) or the team is rejected (400): never a write
    assert r.status_code == 400


def test_logo_selection_rejects_bad_team(authed):
    r = authed.post('/api/logo_selection', json={'team': '../x', 'type': 'alt'})
    assert r.status_code == 400


@pytest.mark.parametrize('name', ['evil.sh', '.hidden.json', 'a b.json', 'x.json.bak', '..'])
def test_config_endpoint_rejects_odd_file_names(authed, name):
    assert authed.post(f'/api/config/{name}', json={}).status_code in (400, 404)
    assert authed.get(f'/api/config/{name}').status_code in (400, 404)


def test_config_endpoint_still_works_for_real_names(le, authed):
    os.makedirs(le.CONFIG_DIR, exist_ok=True)
    assert authed.post('/api/config/logos_64x32.json', json={'a': 1}).status_code == 200
    assert authed.get('/api/config/logos_64x32.json').get_json() == {'a': 1}


# --- live display endpoint ---------------------------------------------------

def test_frame_endpoint_before_and_after_the_scoreboard_publishes(le, authed, tmp_path, monkeypatch):
    fm = le.frame_mirror
    monkeypatch.setattr(fm, 'FRAME_PATH', str(tmp_path / 'frame.png'))
    monkeypatch.setattr(fm, 'WANT_PATH', str(tmp_path / 'frame.want'))

    r = authed.get('/api/scoreboard/frame')
    assert r.status_code == 503 and r.headers['Cache-Control'] == 'no-store'
    assert os.path.exists(fm.WANT_PATH)  # asking wakes the renderer's publisher

    from PIL import Image
    Image.new('RGB', (64, 32), (255, 0, 0)).save(fm.FRAME_PATH)
    r = authed.get('/api/scoreboard/frame')
    assert r.status_code == 200 and r.mimetype == 'image/png'
    assert float(r.headers['X-Frame-Age']) < 5
    assert r.headers['Cache-Control'] == 'no-store'
    assert r.data.startswith(b'\x89PNG')
