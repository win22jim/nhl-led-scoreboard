"""Tests for the home-screen app support: manifest, icons and page tags.

Run with: python -m pytest tests/test_pwa.py
"""

import io
import json

import pytest
from PIL import Image


@pytest.fixture
def client(le):
    return le.app.test_client()


def test_manifest_is_public_and_valid(client):
    # public even before first-run setup: the setup/login pages link to it
    r = client.get('/manifest.webmanifest')
    assert r.status_code == 200 and r.mimetype == 'application/manifest+json'
    m = json.loads(r.data)
    assert m['start_url'] == '/dashboard' and m['display'] == 'standalone'
    assert m['name'] and m['short_name'] and m['theme_color'] and m['background_color']
    assert {i['sizes'] for i in m['icons']} >= {'192x192', '512x512'}


def test_every_icon_in_the_manifest_resolves(client):
    for icon in json.loads(client.get('/manifest.webmanifest').data)['icons']:
        r = client.get(icon['src'])
        assert r.status_code == 200 and r.mimetype == 'image/png', icon['src']
        w, h = (int(n) for n in icon['sizes'].split('x'))
        assert Image.open(io.BytesIO(r.data)).size == (w, h)


@pytest.mark.parametrize('size', [32, 180, 192, 512])
def test_icons_are_drawn_and_not_blank(client, size):
    img = Image.open(io.BytesIO(client.get(f'/app-icon/{size}.png').data)).convert('RGB')
    assert img.size == (size, size)
    colors = {c for _, c in img.getcolors(maxcolors=size * size)}
    assert any(r > 200 and g < 110 for r, g, b in colors), 'goal light is missing'
    assert any(b > 140 and r < 200 for r, g, b in colors), 'LED grid is missing'


@pytest.mark.parametrize('path', ['/app-icon/16.png', '/app-icon/9999.png', '/app-icon/0.png'])
def test_unsupported_icon_sizes_are_404(client, path):
    assert client.get(path).status_code == 404


def test_favicon_stops_the_404(client):
    r = client.get('/favicon.ico')
    assert r.status_code == 200 and r.mimetype == 'image/png'


def test_assets_are_public_after_login_is_enabled(le, authed):
    anon = le.app.test_client()
    for path in ('/manifest.webmanifest', '/app-icon/180.png', '/favicon.ico'):
        assert anon.get(path).status_code == 200, path


def test_pages_are_installable(le, client, authed):
    anon = le.app.test_client()
    pages = {'login': anon.get('/login').data, 'dashboard': authed.get('/dashboard').data}
    for name, html in pages.items():
        assert b'rel="manifest" href="/manifest.webmanifest"' in html, name
        assert b'rel="apple-touch-icon" href="/app-icon/180.png"' in html, name
        assert b'apple-mobile-web-app-capable' in html, name
        assert b'viewport-fit=cover' in html, name


def test_setup_page_is_installable(client):
    html = client.get('/setup').data
    assert b'rel="manifest"' in html and b'rel="apple-touch-icon"' in html
