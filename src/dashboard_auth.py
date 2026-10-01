"""Password login for the web dashboard (src/logo_editor.py).

Design goals: work out of the box with no configuration, and be hard to get
wrong. There is a single shared password, no usernames and no extra
dependencies (Flask/Werkzeug already ship everything used here).

First run: the dashboard has no password, so every page redirects to /setup
where the first visitor on the local network chooses one. After that every
page and API call requires a login. Forgot it? Run, on the device:

    python3 src/logo_editor.py --set-password

State lives in config/dashboard_auth.json (mode 0600): the password hash and
the key used to sign session cookies. It is gitignored.
"""

import hashlib
import ipaddress
import json
import os
import secrets
import tempfile
import threading
import time
from collections import deque
from datetime import timedelta
from urllib.parse import urlparse

from flask import (Blueprint, current_app, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 1024
SESSION_LIFETIME = timedelta(days=30)

# Wrong-password throttle: this many failures per client IP inside the window
# locks that IP out until the oldest failure ages out.
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 300

# Reachable without logging in. /api/health only returns {"status": "ok"}. The
# manifest and icons are fetched by the browser without cookies (home-screen
# install, tab icon), contain nothing private, and are needed on the login page.
PUBLIC_ENDPOINTS = frozenset({'auth.login', 'auth.setup', 'health_check',
                              'web_manifest', 'app_icon_png', 'favicon'})

SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})

auth_bp = Blueprint('auth', __name__)


class AuthStore:
    """Password hash + session-signing key, persisted as JSON.

    The file is re-read whenever its mtime changes, so `--set-password` run in
    another process takes effect immediately without restarting the dashboard.
    """

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._data = {}
        self._mtime = None
        self._load()
        if not self._data.get('secret_key'):
            self._data['secret_key'] = secrets.token_hex(32)
            self._write()

    def _load(self):
        try:
            mtime = os.stat(self.path).st_mtime_ns
        except OSError:
            self._data, self._mtime = {}, None
            return
        if mtime == self._mtime:
            return
        try:
            with open(self.path, 'r') as f:
                data = json.load(f)
            self._data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self._data = {}
        self._mtime = mtime

    def _write(self):
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix='.dashboard_auth.')
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(self._data, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        self._mtime = os.stat(self.path).st_mtime_ns

    @property
    def secret_key(self):
        return self._data['secret_key']

    def has_password(self):
        with self._lock:
            self._load()
            return bool(self._data.get('password_hash'))

    def set_password(self, password):
        with self._lock:
            self._load()
            self._data['password_hash'] = generate_password_hash(password)
            self._write()

    def verify(self, password):
        with self._lock:
            self._load()
            stored = self._data.get('password_hash')
        return bool(stored) and check_password_hash(stored, password)

    def fingerprint(self):
        """Changes whenever the password does, which signs out every session."""
        with self._lock:
            self._load()
            stored = self._data.get('password_hash') or ''
        return hashlib.sha256(stored.encode()).hexdigest()[:20] if stored else ''


class LoginThrottle:
    def __init__(self, max_failures=MAX_FAILURES, window=FAILURE_WINDOW_SECONDS):
        self.max_failures = max_failures
        self.window = window
        self._failures = {}
        self._lock = threading.Lock()

    def _prune(self, ip, now):
        q = self._failures.get(ip)
        while q and now - q[0] > self.window:
            q.popleft()
        if q is not None and not q:
            del self._failures[ip]

    def retry_after(self, ip):
        """Seconds until `ip` may try again; 0 when it is not locked out."""
        now = time.monotonic()
        with self._lock:
            self._prune(ip, now)
            q = self._failures.get(ip)
            if q and len(q) >= self.max_failures:
                return max(1, int(self.window - (now - q[0])) + 1)
        return 0

    def record_failure(self, ip):
        now = time.monotonic()
        with self._lock:
            if len(self._failures) > 1000:
                for key in list(self._failures):
                    self._prune(key, now)
            self._failures.setdefault(ip, deque()).append(now)

    def clear(self, ip):
        with self._lock:
            self._failures.pop(ip, None)


def is_local_client(remote_addr):
    """True for loopback, private, link-local and CGNAT (Tailscale) clients.

    Gates first-run setup so that a dashboard port-forwarded to the internet
    cannot be claimed by a stranger. X-Forwarded-For is deliberately ignored:
    it is client-controlled.
    """
    if not remote_addr:
        return False
    try:
        addr = ipaddress.ip_address(remote_addr.split('%')[0])
    except ValueError:
        return False
    mapped = getattr(addr, 'ipv4_mapped', None)
    if mapped is not None:
        addr = mapped
    return not addr.is_global


def _store():
    return current_app.extensions['dashboard_auth_store']


def _throttle():
    return current_app.extensions['dashboard_auth_throttle']


def _logged_in():
    fp = session.get('fp')
    return bool(fp) and secrets.compare_digest(fp, _store().fingerprint())


def _start_session():
    session.clear()
    session.permanent = True
    session['fp'] = _store().fingerprint()


def _safe_next(target):
    """Only follow same-site relative redirects (blocks //evil.com, https://...)."""
    if target and target.startswith('/') and not target.startswith('//') \
            and '\\' not in target:
        return target
    return url_for('dashboard')


def _wants_json():
    return request.path.startswith('/api/')


def _same_origin():
    """Cross-site request forgery guard for state-changing requests."""
    origin = request.headers.get('Origin')
    if origin:
        return urlparse(origin).netloc == request.host
    return request.headers.get('Sec-Fetch-Site') != 'cross-site'


def _validate_new_password(password, confirm=None):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        return f'Password must be at least {MIN_PASSWORD_LENGTH} characters.'
    if len(password) > MAX_PASSWORD_LENGTH:
        return 'Password is too long.'
    if confirm is not None and password != confirm:
        return 'Passwords do not match.'
    return None


@auth_bp.before_app_request
def _gate():
    if request.method not in SAFE_METHODS and not _same_origin():
        return jsonify({'error': 'cross-origin request blocked'}), 403

    if request.endpoint in PUBLIC_ENDPOINTS:
        return None

    if not _store().has_password():
        if _wants_json():
            return jsonify({'error': 'setup_required'}), 401
        return redirect(url_for('auth.setup'))

    if _logged_in():
        return None

    if _wants_json():
        return jsonify({'error': 'login_required'}), 401
    nxt = request.full_path.rstrip('?') if request.method == 'GET' else None
    return redirect(url_for('auth.login', next=nxt) if nxt else url_for('auth.login'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if not _store().has_password():
        return redirect(url_for('auth.setup'))
    nxt = request.values.get('next', '')
    if request.method == 'GET':
        if _logged_in():
            return redirect(_safe_next(nxt))
        return render_template('login.html', next=nxt)

    ip = request.remote_addr or '?'
    wait = _throttle().retry_after(ip)
    if wait:
        resp = render_template(
            'login.html', next=nxt,
            error=f'Too many attempts. Try again in {wait} seconds.')
        return resp, 429, {'Retry-After': str(wait)}

    password = request.form.get('password', '')
    if len(password) <= MAX_PASSWORD_LENGTH and _store().verify(password):
        _throttle().clear(ip)
        _start_session()
        return redirect(_safe_next(nxt))
    _throttle().record_failure(ip)
    return render_template('login.html', next=nxt, error='Incorrect password.'), 401


@auth_bp.route('/setup', methods=['GET', 'POST'])
def setup():
    if _store().has_password():
        return redirect(url_for('auth.login'))
    if not is_local_client(request.remote_addr):
        return render_template('setup.html', blocked=True), 403
    if request.method == 'GET':
        return render_template('setup.html')

    password = request.form.get('password', '')
    error = _validate_new_password(password, request.form.get('confirm', ''))
    if error:
        return render_template('setup.html', error=error), 400
    _store().set_password(password)
    _start_session()
    return redirect(url_for('dashboard'))


@auth_bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


@auth_bp.route('/api/auth/password', methods=['POST'])
def change_password():
    data = request.get_json(silent=True) or {}
    ip = request.remote_addr or '?'
    wait = _throttle().retry_after(ip)
    if wait:
        return jsonify({'error': f'Too many attempts. Try again in {wait} seconds.'}), 429

    current = data.get('current', '')
    if not isinstance(current, str) or not _store().verify(current):
        _throttle().record_failure(ip)
        return jsonify({'error': 'Current password is incorrect.'}), 403
    error = _validate_new_password(data.get('new'))
    if error:
        return jsonify({'error': error}), 400
    _throttle().clear(ip)
    _store().set_password(data['new'])
    _start_session()  # other devices are signed out; this one stays in
    return jsonify({'status': 'success'})


def init_auth(app, store_path):
    """Attach login to `app`. Call once, before the first request."""
    store = AuthStore(store_path)
    app.extensions['dashboard_auth_store'] = store
    app.extensions['dashboard_auth_throttle'] = LoginThrottle()
    app.secret_key = store.secret_key
    app.config.update(
        SESSION_COOKIE_NAME='nhlsb_session',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=SESSION_LIFETIME,
        # Issue the cookie at login only. Re-sending it on every response (the
        # default for permanent sessions) would let a background poll from
        # another open tab resurrect a session that was just signed out.
        SESSION_REFRESH_EACH_REQUEST=False,
    )
    app.register_blueprint(auth_bp)
    return store
