"""Tests for src/frame_mirror.py (the dashboard's live display feed).

Run with: python -m pytest tests/test_frame_mirror.py
"""

import io
import os
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import frame_mirror
from frame_mirror import FrameMirror


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_image(color, size=(8, 4)):
    return Image.new('RGB', size, color)


@pytest.fixture
def paths(tmp_path):
    return str(tmp_path / 'frame.png'), str(tmp_path / 'frame.want')


@pytest.fixture
def clock():
    return Clock()


def mirror_for(paths, clock, **kw):
    frame, want = paths
    return FrameMirror(frame_path=frame, want_path=want, clock=clock, **kw)


def watch(paths):
    frame_mirror.request_frames(paths[1])


def test_idle_when_nobody_is_watching(paths, clock):
    m = mirror_for(paths, clock)
    m.publish(make_image((255, 0, 0)))
    assert not os.path.exists(paths[0])


def test_writes_a_faithful_png_while_watched(paths, clock):
    watch(paths)
    mirror_for(paths, clock).publish(make_image((255, 0, 0)))
    with Image.open(paths[0]) as img:
        assert img.size == (8, 4)
        assert img.convert('RGB').getpixel((3, 2)) == (255, 0, 0)


def test_publishes_at_most_once_per_interval(paths, clock):
    watch(paths)
    m = mirror_for(paths, clock, interval=0.5)
    m.publish(make_image((255, 0, 0)))
    clock.now += 0.1
    m.publish(make_image((0, 255, 0)))  # too soon: skipped
    with Image.open(paths[0]) as img:
        assert img.convert('RGB').getpixel((0, 0)) == (255, 0, 0)
    clock.now += 0.5
    m.publish(make_image((0, 255, 0)))
    with Image.open(paths[0]) as img:
        assert img.convert('RGB').getpixel((0, 0)) == (0, 255, 0)


def test_unchanged_frame_only_refreshes_timestamp(paths, clock):
    watch(paths)
    m = mirror_for(paths, clock, interval=0.5)
    m.publish(make_image((1, 2, 3)))
    inode = os.stat(paths[0]).st_ino
    os.utime(paths[0], (0, 0))  # pretend it is ancient
    clock.now += 1
    m.publish(make_image((1, 2, 3)))
    assert os.stat(paths[0]).st_ino == inode  # not rewritten
    assert frame_mirror.frame_age(paths[0]) < 5  # but no longer stale


def test_stops_when_the_viewer_goes_away(paths, clock):
    watch(paths)
    os.utime(paths[1], (time.time() - 60, time.time() - 60))  # last request a minute ago
    mirror_for(paths, clock, ttl=10).publish(make_image((255, 0, 0)))
    assert not os.path.exists(paths[0])


def test_write_errors_never_reach_the_render_loop(tmp_path, clock):
    want = str(tmp_path / 'frame.want')
    frame_mirror.request_frames(want)
    m = FrameMirror(frame_path=str(tmp_path / 'missing-dir' / 'frame.png'),
                    want_path=want, clock=clock)
    m.publish(make_image((255, 0, 0)))  # must not raise
    # ...and it backs off instead of retrying every frame
    clock.now += 1
    assert m._next_check > clock.now


def test_frame_age_is_none_without_a_frame(paths):
    assert frame_mirror.frame_age(paths[0]) is None
