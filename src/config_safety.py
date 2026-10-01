"""Safe editing of config/config.json from the web dashboard.

The scoreboard refuses to start when config.json fails config.schema.json, and
silently keeps its old settings when a live reload fails. So the dashboard must
never write a config that doesn't validate. This module provides:

  validate_config()   check a config against the schema, with a readable error
  diff_configs()      what a save would change, for the "review changes" step
  write_config()      atomic write that keeps rotating, restorable backups
  list_backups() / restore_backup()
"""

import copy
import datetime
import json
import os
import re
import shutil
import tempfile

import fastjsonschema

MAX_BACKUPS = 10
MAX_DIFF_ENTRIES = 300

BACKUP_RE = re.compile(r'config\.json\.\d{8}_\d{6}\.bak')
_SENSITIVE_KEY = re.compile(r'pass(word)?|secret|token|api_?key|credential', re.I)
MASK = '••••••'


class ConfigError(Exception):
    """A config that must not be written. `path` points at the offending setting."""

    def __init__(self, message, path=''):
        super().__init__(message)
        self.message = message
        self.path = path


class BadBackupName(ConfigError):
    """The requested name isn't one of our config backup files."""


def validate_config(config, schema_path):
    """Raise ConfigError unless `config` is a valid scoreboard config."""
    if not isinstance(config, dict):
        raise ConfigError("The config must be a JSON object.")
    try:
        with open(schema_path, 'r') as f:
            schema = json.load(f)
        validator = fastjsonschema.compile(schema)
    except (OSError, ValueError, fastjsonschema.JsonSchemaDefinitionException) as e:
        raise ConfigError(f"Could not load the config schema ({e}).")
    try:
        # fastjsonschema fills in schema defaults by modifying the data it is
        # given. Validate a copy so checking a config can never change it.
        validator(copy.deepcopy(config))
    except fastjsonschema.JsonSchemaValueException as e:
        # e.path is like ['data', 'preferences', 'teams']; drop the 'data' root.
        path = '.'.join(str(p) for p in e.path[1:])
        raise ConfigError(_friendly(e.message), path)


def _friendly(message):
    """fastjsonschema words everything relative to its root, called "data"."""
    if message.startswith('data.'):
        return message[len('data.'):]
    if message.startswith('data '):
        return 'The config ' + message[len('data '):]
    return message


def _display(value, key=None):
    if key is not None and _SENSITIVE_KEY.search(str(key)) and value not in (None, ''):
        return MASK
    return value


def diff_summary(old, new, limit=MAX_DIFF_ENTRIES):
    """What changed between two configs, for the "review changes" step.

    Returns {"changes": [{path, change, old, new}], "truncated": bool}.
    Dicts are compared key by key; lists and scalars as whole values (a board
    rotation reads best as "before list -> after list"). Values of settings
    that look like passwords or keys are masked. At most `limit` entries are
    returned; `truncated` says whether there were more.
    """
    changes = []

    def add(entry):
        changes.append(entry)
        return len(changes) > limit  # one past the limit is enough to know

    def walk(a, b, path, key):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b), key=str):
                sub = f"{path}.{k}" if path else str(k)
                if k not in a:
                    full = add({'path': sub, 'change': 'added', 'old': None, 'new': _display(b[k], k)})
                elif k not in b:
                    full = add({'path': sub, 'change': 'removed', 'old': _display(a[k], k), 'new': None})
                elif a[k] != b[k]:
                    full = walk(a[k], b[k], sub, k)
                else:
                    full = False
                if full:
                    return True
            return False
        if a != b:
            return add({'path': path, 'change': 'changed',
                        'old': _display(a, key), 'new': _display(b, key)})
        return False

    walk(old if isinstance(old, dict) else {}, new if isinstance(new, dict) else {}, '', None)
    return {'changes': changes[:limit], 'truncated': len(changes) > limit}


def diff_configs(old, new):
    """Just the list of changes; see diff_summary."""
    return diff_summary(old, new)['changes']


def _backup_name(now=None):
    return 'config.json.' + (now or datetime.datetime.now()).strftime('%Y%m%d_%H%M%S') + '.bak'


def list_backups(config_dir):
    """Backups newest first: [{name, saved_at (ISO), size}]."""
    try:
        names = [n for n in os.listdir(config_dir) if BACKUP_RE.fullmatch(n)]
    except OSError:
        return []
    out = []
    for name in sorted(names, reverse=True):
        stamp = name[len('config.json.'):-len('.bak')]
        try:
            saved = datetime.datetime.strptime(stamp, '%Y%m%d_%H%M%S').isoformat()
        except ValueError:
            continue
        try:
            size = os.stat(os.path.join(config_dir, name)).st_size
        except OSError:
            continue
        out.append({'name': name, 'saved_at': saved, 'size': size})
    return out


def _prune_backups(config_dir):
    for old in list_backups(config_dir)[MAX_BACKUPS:]:
        try:
            os.remove(os.path.join(config_dir, old['name']))
        except OSError:
            pass


def write_config(config, config_file, schema_path, now=None):
    """Validate, back up the current file, then atomically replace it.

    Raises ConfigError (nothing written) if the config is invalid.
    """
    validate_config(config, schema_path)
    config_dir = os.path.dirname(config_file)
    if os.path.exists(config_file):
        shutil.copy2(config_file, os.path.join(config_dir, _backup_name(now)))
        _prune_backups(config_dir)
    fd, tmp = tempfile.mkstemp(dir=config_dir, prefix='.config.json.')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(config, f, indent=4)
        if os.path.exists(config_file):
            shutil.copymode(config_file, tmp)  # keep whatever permissions the file had
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, config_file)  # the live-reload watcher never sees a half-written file
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def restore_backup(name, config_file, schema_path):
    """Make a backup the live config. The config it replaces is itself backed up
    first, so a restore can be undone. Raises ConfigError / FileNotFoundError."""
    if not BACKUP_RE.fullmatch(name or ''):
        raise BadBackupName("That isn't a config backup.")
    config_dir = os.path.dirname(config_file)
    path = os.path.join(config_dir, name)
    with open(path, 'r') as f:  # FileNotFoundError if it has been pruned
        try:
            config = json.load(f)
        except ValueError:
            raise ConfigError("That backup isn't valid JSON, so it can't be restored.")
    try:
        validate_config(config, schema_path)
    except ConfigError as e:
        raise ConfigError(f"That backup no longer passes validation: {e.message}", e.path)
    write_config(config, config_file, schema_path)
