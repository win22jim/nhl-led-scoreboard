"""Shared fixtures for the pytest-based tests (the older test_*_renderer.py
files in this directory are manual scripts and don't use these)."""

import importlib
import sys
from pathlib import Path

import pytest

ROOT = str(Path(__file__).parent.parent)
SRC = str(Path(__file__).parent.parent / "src")

PASSWORD = "correct horse"

# Modules that logo_editor and the scoreboard-side code share; they are
# re-imported per test so patched paths and state never leak between tests.
FRESH_MODULES = ('logo_editor', 'src.logo_editor', 'dashboard_auth', 'src.dashboard_auth',
                 'frame_mirror', 'src.frame_mirror', 'control_channel', 'src.control_channel',
                 'sbio.localcontrol')


@pytest.fixture
def le(tmp_path, monkeypatch):
    """A freshly imported logo_editor bound to an empty install dir.

    The repo root is taken off sys.path so `from src import x` fails exactly as
    it does when the app runs as `python3 src/logo_editor.py`; every module
    then resolves to a single top-level copy shared with src/sbio code.
    """
    monkeypatch.setattr(sys, 'path', [p for p in sys.path if p not in ('', ROOT)])
    monkeypatch.syspath_prepend(SRC)
    monkeypatch.setattr(sys, 'argv', ['logo_editor.py', '--dir', str(tmp_path)])
    for name in FRESH_MODULES:
        sys.modules.pop(name, None)
    module = importlib.import_module('logo_editor')
    module.app.config['TESTING'] = True
    return module


@pytest.fixture
def authed(le):
    """A test client that has completed first-run setup and is signed in."""
    c = le.app.test_client()
    r = c.post('/setup', data={'password': PASSWORD, 'confirm': PASSWORD})
    assert r.status_code == 302
    return c
