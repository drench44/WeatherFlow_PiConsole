"""Exercise the server's admission gate directly, without opening a socket."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def server(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('radar_buffer_serve', Path('design/almanac/kiosk/serve.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'DATA', str(tmp_path/'wx.json'))
    monkeypatch.setattr(module, '_write_settled_camera', lambda *args: True)
    return module


@pytest.mark.parametrize('generation', [1, 2])
def test_reordered_motion_is_rejected_after_user_claim(server, generation):
    session = 'buffer-session-12345'
    params = dict(radarSession=[session], radarGeneration=['1'], radarHeartbeat=['1'], radarClaim=[''], radarClaimEpoch=['0'], radarCommit=['1'], radarPolicy=['manual'])
    activity = dict(moving=False, zoom=8, center=dict(lat=47, lon=-122))
    assert server._camera_transaction(activity, params)
    params.update(radarGeneration=[str(generation)], radarCommit=['1'], radarPolicy=['manual'], radarSource=['site'])
    params['radarHeartbeat'] = ['3']
    assert server._camera_transaction(activity, params)
    owner = dict(server._radar_owner)
    for heartbeat in ('2', '3'):
        params['radarHeartbeat'] = [heartbeat]
        assert not server._camera_transaction(dict(activity, moving=True), params)
        assert server._radar_owner == owner
    params['radarHeartbeat'] = ['4']
    assert server._camera_transaction(activity, params)


def test_malformed_reload_heartbeat_cannot_claim_owner(server):
    params = dict(radarSession=['buffer-session-12345'], radarGeneration=['0'], radarHeartbeat=['bad'], radarClaim=[''])
    assert not server._camera_transaction({}, params)
    assert not server._radar_owner or not server._radar_owner['session']
