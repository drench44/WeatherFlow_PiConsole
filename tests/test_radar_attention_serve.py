"""The server turns the page's touch flag into a presence marker, throttled."""
import importlib.util
import os
import time
from pathlib import Path
import pytest


@pytest.fixture
def server(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('attention_serve', Path('design/almanac/kiosk/serve.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'DATA', str(tmp_path / 'wx.json'))
    return module


def test_presence_marker_is_an_epoch_written_at_most_every_ten_seconds(server, tmp_path):
    marker = tmp_path / 'presence'
    server._note_presence()
    first = float(marker.read_text())
    assert abs(first - time.time()) < 5
    os.utime(marker, (1, 1))
    server._note_presence()                       # throttled: the marker is untouched
    assert marker.stat().st_mtime == 1
    server._presence_at = 0
    server._note_presence()
    assert marker.stat().st_mtime > 1
