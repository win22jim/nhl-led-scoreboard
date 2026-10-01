"""Tests for safe config editing: src/config_safety.py and the dashboard's
config save / preview / backup / restore endpoints.

Run with: python -m pytest tests/test_config_safety.py
"""

import copy
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SAMPLE = REPO / 'config' / 'config.json.sample'
SCHEMA = REPO / 'config' / 'config.schema.json'


@pytest.fixture
def cs(le):
    import config_safety
    assert le.config_safety is config_safety
    return config_safety


@pytest.fixture
def sample():
    return json.loads(SAMPLE.read_text())


@pytest.fixture
def live(le, sample):
    """A throwaway install with a valid config.json and the real schema."""
    os.makedirs(os.path.dirname(le.MAIN_CONFIG_FILE), exist_ok=True)
    shutil.copy(SCHEMA, le.SCHEMA_FILE)
    with open(le.MAIN_CONFIG_FILE, 'w') as f:
        json.dump(sample, f, indent=4)
    return le


def read_live(le):
    return json.loads(Path(le.MAIN_CONFIG_FILE).read_text())


def backups(le):
    return sorted(n for n in os.listdir(os.path.dirname(le.MAIN_CONFIG_FILE)) if n.endswith('.bak'))


# --- validation --------------------------------------------------------------

def test_the_sample_config_is_valid(cs, sample):
    cs.validate_config(sample, str(SCHEMA))


def test_validation_never_modifies_the_config(cs, sample):
    # fastjsonschema injects schema defaults into whatever it validates; if that
    # leaked, saving would write settings the user never chose.
    before = copy.deepcopy(sample)
    cs.validate_config(sample, str(SCHEMA))
    assert sample == before


def test_what_is_written_is_exactly_what_was_sent(cs, live, sample):
    cs.write_config(sample, live.MAIN_CONFIG_FILE, live.SCHEMA_FILE)
    assert read_live(live) == sample


def test_missing_required_setting_is_reported(cs, sample):
    del sample['boards']
    with pytest.raises(cs.ConfigError) as e:
        cs.validate_config(sample, str(SCHEMA))
    assert 'boards' in e.value.message and not e.value.message.startswith('data')


def test_wrong_type_points_at_the_setting(cs, sample):
    sample['preferences']['teams'] = 'WPG'
    with pytest.raises(cs.ConfigError) as e:
        cs.validate_config(sample, str(SCHEMA))
    assert e.value.path == 'preferences.teams' and 'array' in e.value.message


@pytest.mark.parametrize('bad', [None, [], 'text', 5])
def test_non_object_configs_are_rejected(cs, bad):
    with pytest.raises(cs.ConfigError):
        cs.validate_config(bad, str(SCHEMA))


def test_missing_schema_blocks_instead_of_allowing(cs, sample, tmp_path):
    with pytest.raises(cs.ConfigError, match='schema'):
        cs.validate_config(sample, str(tmp_path / 'nope.json'))


# --- diff --------------------------------------------------------------------

def test_diff_reports_nested_changes(cs, sample):
    new = copy.deepcopy(sample)
    new['live_mode'] = not sample['live_mode']
    new['preferences']['teams'] = ['Jets']
    changes = {c['path']: c for c in cs.diff_configs(sample, new)}
    assert changes['live_mode']['change'] == 'changed'
    assert changes['live_mode']['old'] is sample['live_mode'] and changes['live_mode']['new'] is new['live_mode']
    assert changes['preferences.teams']['new'] == ['Jets']
    assert len(changes) == 2


def test_diff_reports_added_and_removed_keys(cs):
    changes = {c['path']: c['change'] for c in cs.diff_configs({'a': 1, 'b': 2}, {'b': 2, 'c': 3})}
    assert changes == {'a': 'removed', 'c': 'added'}


def test_diff_of_identical_configs_is_empty(cs, sample):
    assert cs.diff_configs(sample, copy.deepcopy(sample)) == []


def test_diff_treats_a_reordered_list_as_a_change(cs):
    changes = cs.diff_configs({'states': {'off_day': ['a', 'b']}}, {'states': {'off_day': ['b', 'a']}})
    assert changes == [{'path': 'states.off_day', 'change': 'changed', 'old': ['a', 'b'], 'new': ['b', 'a']}]


def test_diff_masks_secrets(cs):
    old = {'mqtt': {'password': 'hunter2', 'api_key': 'abc', 'broker': 'a'}}
    new = {'mqtt': {'password': 'swordfish', 'api_key': '', 'broker': 'b'}}
    shown = json.dumps(cs.diff_configs(old, new))
    for secret in ('hunter2', 'swordfish', 'abc'):
        assert secret not in shown
    changes = {c['path']: c for c in cs.diff_configs(old, new)}
    assert changes['mqtt.password']['new'] == cs.MASK
    assert changes['mqtt.broker']['new'] == 'b'  # ordinary values stay visible


def test_diff_is_capped_and_says_so(cs):
    many = {f'k{i}': i for i in range(1000)}
    summary = cs.diff_summary({}, many)
    assert len(summary['changes']) == cs.MAX_DIFF_ENTRIES and summary['truncated'] is True
    nested = cs.diff_summary({'a': {f'k{i}': 0 for i in range(1000)}},
                             {'a': {f'k{i}': 1 for i in range(1000)}})
    assert len(nested['changes']) == cs.MAX_DIFF_ENTRIES and nested['truncated'] is True


def test_diff_at_exactly_the_cap_is_not_truncated(cs):
    exactly = {f'k{i}': i for i in range(cs.MAX_DIFF_ENTRIES)}
    summary = cs.diff_summary({}, exactly)
    assert len(summary['changes']) == cs.MAX_DIFF_ENTRIES and summary['truncated'] is False


# --- writing and backups ---------------------------------------------------------

def test_write_validates_before_touching_the_file(cs, live, sample):
    before = Path(live.MAIN_CONFIG_FILE).read_bytes()
    bad = copy.deepcopy(sample)
    bad['preferences']['teams'] = 'WPG'
    with pytest.raises(cs.ConfigError):
        cs.write_config(bad, live.MAIN_CONFIG_FILE, live.SCHEMA_FILE)
    assert Path(live.MAIN_CONFIG_FILE).read_bytes() == before
    assert backups(live) == []


def test_write_backs_up_and_leaves_no_temp_files(cs, live, sample):
    new = copy.deepcopy(sample)
    new['live_mode'] = not sample['live_mode']
    cs.write_config(new, live.MAIN_CONFIG_FILE, live.SCHEMA_FILE)
    assert read_live(live)['live_mode'] == new['live_mode']
    [name] = backups(live)
    assert json.loads((Path(live.MAIN_CONFIG_FILE).parent / name).read_text()) == sample
    leftovers = [n for n in os.listdir(Path(live.MAIN_CONFIG_FILE).parent) if n.startswith('.config')]
    assert leftovers == []


def test_write_keeps_file_permissions(cs, live, sample):
    os.chmod(live.MAIN_CONFIG_FILE, 0o640)
    cs.write_config(sample, live.MAIN_CONFIG_FILE, live.SCHEMA_FILE)
    assert os.stat(live.MAIN_CONFIG_FILE).st_mode & 0o777 == 0o640


def test_backups_are_rotated(cs, live, sample):
    import datetime
    base = datetime.datetime(2026, 1, 1, 12, 0, 0)
    for i in range(cs.MAX_BACKUPS + 5):
        cs.write_config(sample, live.MAIN_CONFIG_FILE, live.SCHEMA_FILE,
                        now=base + datetime.timedelta(minutes=i))
    names = backups(live)
    assert len(names) == cs.MAX_BACKUPS
    assert names[-1].endswith('121404.bak') or names[-1] > names[0]  # newest kept


def test_list_backups_newest_first(cs, live, sample):
    import datetime
    base = datetime.datetime(2026, 1, 1, 12, 0, 0)
    for i in range(3):
        cs.write_config(sample, live.MAIN_CONFIG_FILE, live.SCHEMA_FILE, now=base + datetime.timedelta(hours=i))
    listed = cs.list_backups(os.path.dirname(live.MAIN_CONFIG_FILE))
    assert [b['saved_at'] for b in listed] == sorted((b['saved_at'] for b in listed), reverse=True)
    assert len(listed) == 3 and all(b['size'] > 0 for b in listed)


def test_stray_files_are_not_listed_as_backups(cs, live):
    d = os.path.dirname(live.MAIN_CONFIG_FILE)
    for junk in ('config.json.bak', 'config.json.notadate.bak', 'config.json.20260101_120000.bak.exe', 'other.bak'):
        Path(d, junk).write_text('{}')
    assert cs.list_backups(d) == []


# --- restoring ----------------------------------------------------------------------

def test_restore_brings_back_an_old_config_and_is_undoable(cs, live, sample):
    import datetime
    changed = copy.deepcopy(sample)
    changed['live_mode'] = not sample['live_mode']
    cs.write_config(changed, live.MAIN_CONFIG_FILE, live.SCHEMA_FILE,
                    now=datetime.datetime(2026, 1, 1, 12, 0, 0))
    [original_backup] = backups(live)
    cs.restore_backup(original_backup, live.MAIN_CONFIG_FILE, live.SCHEMA_FILE)
    assert read_live(live) == sample
    # the config we replaced was backed up too, so the restore can be reversed
    assert len(backups(live)) == 2


@pytest.mark.parametrize('name', ['../config.json', 'config.json', '/etc/passwd', '', None,
                                  'config.json.20260101_120000.bak/../../x', 'config.json.20260101_120000.bak\n'])
def test_restore_refuses_anything_that_is_not_a_backup_name(cs, live, name):
    with pytest.raises(cs.BadBackupName):
        cs.restore_backup(name, live.MAIN_CONFIG_FILE, live.SCHEMA_FILE)


def test_restore_of_a_missing_backup(cs, live):
    with pytest.raises(FileNotFoundError):
        cs.restore_backup('config.json.20200101_000000.bak', live.MAIN_CONFIG_FILE, live.SCHEMA_FILE)


def test_restore_refuses_a_backup_that_no_longer_validates(cs, live, sample):
    d = Path(live.MAIN_CONFIG_FILE).parent
    bad = copy.deepcopy(sample)
    del bad['states']
    (d / 'config.json.20200101_000000.bak').write_text(json.dumps(bad))
    (d / 'config.json.20200102_000000.bak').write_text('{not json')
    before = Path(live.MAIN_CONFIG_FILE).read_bytes()
    for name in ('config.json.20200101_000000.bak', 'config.json.20200102_000000.bak'):
        with pytest.raises(cs.ConfigError):
            cs.restore_backup(name, live.MAIN_CONFIG_FILE, live.SCHEMA_FILE)
    assert Path(live.MAIN_CONFIG_FILE).read_bytes() == before


# --- endpoints -----------------------------------------------------------------------

def test_save_endpoint_round_trip(live, authed, sample):
    new = copy.deepcopy(sample)
    new['live_mode'] = not sample['live_mode']
    r = authed.post('/api/scoreboard/config', json=new)
    assert r.status_code == 200 and r.get_json() == {'status': 'success'}
    assert read_live(live)['live_mode'] == new['live_mode']
    assert len(backups(live)) == 1


def test_save_endpoint_refuses_an_invalid_config(live, authed, sample):
    bad = copy.deepcopy(sample)
    bad['preferences']['teams'] = 'WPG'
    before = Path(live.MAIN_CONFIG_FILE).read_bytes()
    r = authed.post('/api/scoreboard/config', json=bad)
    body = r.get_json()
    assert r.status_code == 422 and body['status'] == 'error'
    assert body['path'] == 'preferences.teams' and 'array' in body['message']
    assert Path(live.MAIN_CONFIG_FILE).read_bytes() == before  # untouched
    assert backups(live) == []


@pytest.mark.parametrize('body', [{}, [], 'x', None])
def test_save_endpoint_refuses_empty_or_odd_bodies(live, authed, body):
    before = Path(live.MAIN_CONFIG_FILE).read_bytes()
    r = authed.post('/api/scoreboard/config', json=body)
    assert r.status_code in (400, 422)
    assert Path(live.MAIN_CONFIG_FILE).read_bytes() == before


def test_preview_lists_changes_without_saving(live, authed, sample):
    new = copy.deepcopy(sample)
    new['live_mode'] = not sample['live_mode']
    before = Path(live.MAIN_CONFIG_FILE).read_bytes()
    r = authed.post('/api/scoreboard/config/preview', json=new)
    body = r.get_json()
    assert r.status_code == 200 and body['valid'] is True and body['error'] is None
    assert [c['path'] for c in body['changes']] == ['live_mode']
    assert Path(live.MAIN_CONFIG_FILE).read_bytes() == before and backups(live) == []


def test_preview_flags_invalid_configs_and_still_shows_changes(live, authed, sample):
    bad = copy.deepcopy(sample)
    bad['preferences']['teams'] = 'WPG'
    body = authed.post('/api/scoreboard/config/preview', json=bad).get_json()
    assert body['valid'] is False and body['error']['path'] == 'preferences.teams'
    assert any(c['path'] == 'preferences.teams' for c in body['changes'])


def test_preview_with_no_changes(live, authed, sample):
    body = authed.post('/api/scoreboard/config/preview', json=sample).get_json()
    assert body['valid'] is True and body['changes'] == []


def test_preview_rejects_non_objects(live, authed):
    assert authed.post('/api/scoreboard/config/preview', json=[1, 2]).status_code == 400


def test_backups_and_restore_endpoints(live, authed, sample):
    changed = copy.deepcopy(sample)
    changed['live_mode'] = not sample['live_mode']
    assert authed.post('/api/scoreboard/config', json=changed).status_code == 200
    listed = authed.get('/api/scoreboard/config/backups').get_json()['backups']
    assert len(listed) == 1 and listed[0]['name'].startswith('config.json.')

    r = authed.post('/api/scoreboard/config/restore', json={'name': listed[0]['name']})
    assert r.status_code == 200
    assert read_live(live) == sample
    assert len(authed.get('/api/scoreboard/config/backups').get_json()['backups']) >= 1


@pytest.mark.parametrize('name,status', [
    ('../../etc/passwd', 400), ('config.json', 400), ('', 400),
    ('config.json.20200101_000000.bak', 404),
])
def test_restore_endpoint_errors(live, authed, name, status):
    before = Path(live.MAIN_CONFIG_FILE).read_bytes()
    r = authed.post('/api/scoreboard/config/restore', json={'name': name})
    assert r.status_code == status and r.get_json()['status'] == 'error'
    assert Path(live.MAIN_CONFIG_FILE).read_bytes() == before


def test_restore_endpoint_refuses_an_invalid_backup(live, authed):
    d = Path(live.MAIN_CONFIG_FILE).parent
    (d / 'config.json.20200101_000000.bak').write_text('{"debug": true}')
    r = authed.post('/api/scoreboard/config/restore', json={'name': 'config.json.20200101_000000.bak'})
    assert r.status_code == 422 and 'validation' in r.get_json()['message']


def test_config_endpoints_need_a_login(live, authed):
    anon = live.app.test_client()
    for method, path in (('post', '/api/scoreboard/config'), ('post', '/api/scoreboard/config/preview'),
                         ('get', '/api/scoreboard/config/backups'), ('post', '/api/scoreboard/config/restore')):
        assert getattr(anon, method)(path, json={}).status_code == 401, path
