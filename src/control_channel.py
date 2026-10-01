"""Send commands from the web dashboard to the running scoreboard.

The dashboard (Flask) and the scoreboard (renderer) are separate processes.
This is the same idea as the MQTT control topics, but with no broker needed:
the dashboard drops a small JSON file into a spool directory, the scoreboard
picks it up within half a second, acts on it, and reports the outcome in a
state file the dashboard can read back.

    dashboard:   cid = send({"action": "brightness", "value": 40})
                 ... read_state()["results"][cid] -> {"ok": True, "message": ...}
    scoreboard:  for cid, cmd in drain(): ...; publish_state({...results...})

Both sides call validate_command(), so a malformed or out-of-range command is
rejected wherever it comes from. Commands are only honoured for COMMAND_TTL
seconds, so one sent while the scoreboard was down does not fire minutes later
when it starts.
"""

import json
import os
import re
import stat
import tempfile
import time
import uuid

SPOOL_DIR = '/tmp/nhl-scoreboard-control'
STATE_PATH = '/tmp/nhl-scoreboard-state.json'

COMMAND_TTL = 10.0        # seconds a queued command stays valid
MAX_COMMAND_BYTES = 2048  # nothing legitimate is anywhere near this

BOARD_ID_RE = re.compile(r'[a-z0-9_]{1,40}')


class CommandError(ValueError):
    """The command is malformed or out of range."""


def _percent(cmd, key):
    value = cmd.get(key)
    # bool is an int subclass; True must not pass as brightness 1
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise CommandError(f"{key} must be a whole number from 1 to 100")
    return value


def validate_command(cmd):
    """Return a clean copy of `cmd` containing only known fields, or raise CommandError."""
    if not isinstance(cmd, dict):
        raise CommandError("command must be a JSON object")
    action = cmd.get('action')
    if action == 'brightness':
        return {'action': action, 'value': _percent(cmd, 'value')}
    if action == 'dimmer':
        return {'action': action, 'sunrise': _percent(cmd, 'sunrise'),
                'sunset': _percent(cmd, 'sunset')}
    if action == 'screensaver':
        if cmd.get('value') not in ('on', 'off'):
            raise CommandError("screensaver value must be 'on' or 'off'")
        return {'action': action, 'value': cmd['value']}
    if action == 'showboard':
        board = cmd.get('board')
        if not isinstance(board, str) or not BOARD_ID_RE.fullmatch(board):
            raise CommandError("board must be a board id such as 'clock'")
        return {'action': action, 'board': board}
    raise CommandError("unknown action")


def _spool(spool_dir=None):
    path = spool_dir or SPOOL_DIR
    # The dashboard and the scoreboard may run as different users (e.g. one as
    # root under supervisor), so either must be able to create files here and
    # clear them. Every command is strictly validated and short-lived, which
    # bounds what a stray local process could do with write access.
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o777)
    except PermissionError:
        pass  # created by the other user, who already opened it up
    return path


def _write_json_atomically(path, payload, mode):
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix='.tmp-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(payload, f)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# --- dashboard side -----------------------------------------------------------

def send(cmd, spool_dir=None):
    """Queue a command for the scoreboard. Returns its id. Raises CommandError."""
    cmd = validate_command(cmd)
    spool = _spool(spool_dir)
    cmd_id = uuid.uuid4().hex
    # Zero-padded nanosecond prefix keeps commands in the order they were sent.
    name = f"{time.time_ns():020d}-{cmd_id}.json"
    _write_json_atomically(os.path.join(spool, name), cmd, 0o666)
    return cmd_id


def withdraw(cmd_id, spool_dir=None):
    """Cancel a command the scoreboard has not picked up yet."""
    spool = spool_dir or SPOOL_DIR
    try:
        for name in os.listdir(spool):
            if name.endswith(f"-{cmd_id}.json"):
                os.remove(os.path.join(spool, name))
    except OSError:
        pass


def read_state(state_path=None):
    """The scoreboard's last published state, or None. Adds 'age' in seconds."""
    path = state_path or STATE_PATH
    try:
        with open(path, 'r') as f:
            state = json.load(f)
        if not isinstance(state, dict):
            return None
        state['age'] = max(0.0, time.time() - os.stat(path).st_mtime)
        return state
    except (OSError, ValueError):
        return None


# --- scoreboard side ------------------------------------------------------------

def drain(spool_dir=None, ttl=None, seen=None):
    """Collect and clear pending commands, oldest first: [(id, command), ...].

    Skips (and deletes) anything stale, oversized, not a plain file, or invalid.
    `seen` is a set of ids already handled; it guards against re-running a
    command whose file could not be deleted (e.g. owned by another user).
    """
    spool = spool_dir or SPOOL_DIR
    ttl = COMMAND_TTL if ttl is None else ttl
    try:
        names = sorted(n for n in os.listdir(spool)
                       if n.endswith('.json') and not n.startswith('.'))
    except OSError:
        return []

    commands = []
    now = time.time()
    for name in names:
        path = os.path.join(spool, name)
        cmd_id = name[:-len('.json')].split('-', 1)[-1]
        if seen is not None and cmd_id in seen:
            _remove(path)
            continue
        try:
            info = os.lstat(path)  # lstat: never follow a planted symlink
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_COMMAND_BYTES:
                raise ValueError("not an acceptable command file")
            if now - info.st_mtime > ttl:
                raise ValueError("stale")
            with open(path, 'r') as f:
                cmd = validate_command(json.load(f))
        except (OSError, ValueError):
            _remove(path)
            continue
        _remove(path)
        if seen is not None:
            seen.add(cmd_id)
        commands.append((cmd_id, cmd))
    return commands


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def publish_state(state, state_path=None):
    _write_json_atomically(state_path or STATE_PATH, state, 0o644)
