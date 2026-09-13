"""Zoom intent, source geometry, persistence and backpressure through real adapters."""
import builtins
import json
import os
import subprocess
import urllib.error
from pathlib import Path

import pytest
from PIL import Image

from lib import almanac_emit as ae
from tests.fixtures.config import make_config
from tests.test_radar_hybrid import hybrid  # noqa: F401 - shared hermetic transport
from tests.test_radar_emit import radar_dir, radar_net, tile_calls  # noqa: F401
from tests.test_freshness_health import _load_serve, _payload, _get, serve_at  # noqa: F401


@pytest.mark.parametrize('value,desired', [(None, None), ('auto', None), ('garbage', None),
    ('', None), ('3', None), ('11', None), ('-1', None), ('7.0', None), ('NaN', None),
    ('8\n9', None), (b'\xff', None), ('8' * 200, None), ('8' + ' ' * 128 + 'junk', None), ('4', 4), ('6', 6),
    ('7', 7), ('8', 8), ('9\n', 9)])
def test_override_validation_and_restart(make_emitter, hybrid, tmp_path, value, desired):
    pref = tmp_path / 'radar_zoom'
    if value is not None:
        pref.write_bytes(value if isinstance(value, bytes) else value.encode())
    for _ in range(2):  # new process-equivalent emitter reads the saved preference
        emitter = make_emitter(); emitter._do_radar()
        r = emitter._build_payload()['radar']
        assert r['available']
        assert r['zoom'] == (desired if desired is not None else ae._radar_zoom_for(47.61))
        assert r['zoomDesired'] == desired and r['zoomAuto'] is (desired is None)
        assert r['zoomAutoLevel'] == 8 and r['zoomMin'] == 4 and r['zoomMax'] == 9
        assert not r['zoomCapped'] and r['zoomSource'] == 'MRMS'
    if value is not None:
        assert pref.read_bytes() == (value if isinstance(value, bytes) else value.encode())


def test_preference_unreadable_falls_back(make_emitter, hybrid, tmp_path):
    (tmp_path / 'radar_zoom').mkdir()
    emitter = make_emitter(); emitter._do_radar()
    assert emitter._build_payload()['radar']['zoomAuto']


def test_source_clamp_restore_sticky_and_reset(make_emitter, hybrid, tmp_path):
    pref = tmp_path / 'radar_zoom'; pref.write_text('8\n')
    emitter = make_emitter(); emitter._do_radar()
    original = emitter._radar_result
    assert original.zoom == 8
    hybrid.failure = lambda req, _: (_ for _ in ()).throw(urllib.error.URLError('primary down')) if 'iastate.edu' in req.full_url else None
    hybrid.mono=241; hybrid.calls.clear(); emitter._do_radar()
    fallback = emitter._build_payload()['radar']
    assert fallback['zoom'] == fallback['zoomMax'] == 7
    assert fallback['zoomSource'] == 'RainViewer' and fallback['zoomCapped']
    assert fallback['zoomDesired'] == 8 and not fallback['zoomAuto']
    assert all('/256/7/' in c[2] for c in hybrid.calls if '/256/' in c[2])
    assert pref.read_text() == '8\n'
    emitter._do_radar(); assert emitter._build_payload()['radar']['zoomCapped']
    hybrid.failure = None; hybrid.mono=0; hybrid.calls.clear(); emitter._do_radar()
    restored = emitter._build_payload()['radar']
    assert restored['zoom'] == 8 and restored['zoomMax'] == 9 and not restored['zoomCapped']
    assert restored['latest'] == original.latest and len(hybrid.calls) == 1  # warm z8 restored
    pref.write_text('auto\n'); emitter._do_radar()
    reset = emitter._build_payload()['radar']
    assert reset['zoom'] == reset['zoomAutoLevel'] == 8 and reset['zoomAuto']
    assert reset['zoomDesired'] is None and pref.read_text() == 'auto\n'


@pytest.mark.parametrize('viewed', [False, True])
def test_zoom_cache_identity_rebuild_and_retirement(make_emitter, hybrid, tmp_path, viewed):
    if viewed: hybrid.view()
    emitter = make_emitter(); emitter._do_radar()
    old = emitter._radar_result
    old_files = [Path(ae.RADAR_DIR) / (f['id'] + '.png') for f in old.frames if f['complete']]
    hybrid.mono += 60
    (tmp_path / 'radar_zoom').write_text('9')
    emitter._do_radar(); new = emitter._radar_result
    assert old.latest.split('/')[1] != new.latest.split('/')[1]
    assert new.ts_frame == old.ts_frame and new.mpp == pytest.approx(old.mpp / 2)
    assert new.bounds != old.bounds and new.scalebar != old.scalebar and new.rings != old.rings
    assert all(p.exists() for p in old_files)  # decode grace starts at retirement
    new_latest = Path(ae.RADAR_DIR) / (new.latest + '.png')
    before = new_latest.stat().st_mtime_ns
    if viewed:
        # Complete the viewed history before testing a fully warm pass.
        for _ in range(6):
            hybrid.now -= 60
            hybrid.mono += 60; emitter._do_radar()
        assert all(f['complete'] for f in emitter._radar_frames)
    else:
        assert sum(f['complete'] for f in new.frames) == 1
        hybrid.mono += ae.RADAR_CACHE_GRACE_SEC
    if viewed:
        hybrid.mono += 120  # retirement grace uses wall time
    hybrid.calls.clear(); emitter._do_radar()
    assert len(hybrid.calls) == 1 and hybrid.calls[0][2] == ae.RADAR_IEM_METADATA_URL
    assert new_latest.stat().st_mtime_ns == before
    assert all(not p.exists() for p in old_files)
    assert not list(Path(ae.RADAR_DIR).rglob('*.tmp.*'))


def test_unused_history_expires_and_zoom_only_builds_latest(make_emitter, radar_net, radar_dir, tmp_path, monkeypatch):
    # RainViewer remains fresh beyond the view TTL; exercise its actual expiry boundary.
    now = [radar_net['times'][-1] + 1]
    monkeypatch.setattr(ae.time, 'time', lambda: now[0])
    monkeypatch.setattr(ae.time, 'monotonic', lambda: now[0])
    (tmp_path / 'radar_viewed').write_text(str(now[0]))
    emitter = make_emitter(); emitter._do_radar()
    assert sum(f['complete'] for f in emitter._radar_frames) == 2
    retired = radar_dir / (emitter._radar_frames[0]['id'] + '.png')
    now[0] += ae.RADAR_VIEW_TTL
    radar_net['calls'].clear(); emitter._do_radar()
    assert sum(f['complete'] for f in emitter._radar_frames) == 1 and not tile_calls(radar_net)
    now[0] += ae.RADAR_CACHE_GRACE_SEC; emitter._do_radar()
    assert not retired.exists()
    (tmp_path / 'radar_zoom').write_text('6')
    radar_net['calls'].clear(); emitter._do_radar()
    r = emitter._build_payload()['radar']
    assert r['zoom'] == 6 and r['completeFrameCount'] == 1
    assert len(tile_calls(radar_net)) == len(ae._radar_viewport(47.61, -122.33, 6, 956, 490)[0])
    assert all('/256/6/' in c for c in tile_calls(radar_net))
    radar_net['calls'].clear(); emitter._do_radar()
    assert not tile_calls(radar_net)


@pytest.mark.parametrize('retry', ['180', 'Sun, 13 Sep 2026 00:11:00 GMT'])
def test_zoom_during_429_obeys_source_cooldown(make_emitter, hybrid, tmp_path, retry):
    def fail(req, _):
        if 'iastate.edu' in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 429, 'slow', {'Retry-After': retry}, None)
    hybrid.failure = fail
    emitter = make_emitter(); emitter._do_radar()
    (tmp_path / 'radar_zoom').write_text('8')
    hybrid.failure = None; hybrid.calls.clear(); emitter._do_radar()
    assert all(c[0] == 'rainviewer' for c in hybrid.calls)
    assert emitter._build_payload()['radar']['zoomCapped']
    hybrid.mono = 179; hybrid.calls.clear(); emitter._do_radar()
    assert all(c[0] == 'rainviewer' for c in hybrid.calls)
    hybrid.mono = 180; emitter._do_radar()
    assert emitter._radar_zoom == 8 and emitter._radar_result.source_id == 'iem-mrms-lcref'


def test_zoom_cannot_reset_shared_rate_limit(make_emitter, hybrid, tmp_path):
    hybrid.view()
    emitter = make_emitter(); emitter._do_radar()
    assert len(hybrid.calls) == ae.RADAR_REQUESTS_PER_MIN
    before = emitter._radar_result
    (tmp_path / 'radar_zoom').write_text('9')
    emitter._do_radar()
    assert len(hybrid.calls) == ae.RADAR_REQUESTS_PER_MIN and emitter._radar_result is before
    hybrid.mono = 60; emitter._do_radar()
    assert emitter._radar_zoom == 9
    starts = [c[3] for c in hybrid.calls]
    assert all(sum(t <= v < t + 60 for v in starts) <= ae.RADAR_REQUESTS_PER_MIN for t in starts)


def test_zoom_primary_fallback_share_build_budget(make_emitter, hybrid, tmp_path, monkeypatch):
    monkeypatch.setattr(ae, 'RADAR_MAX_FRAME_BUILDS_PER_PASS', 1)
    (tmp_path / 'radar_zoom').write_text('9')
    def fail(req, _):
        if req.get_method() == 'HEAD':
            raise urllib.error.HTTPError(req.full_url, 404, 'no archive', {}, None)
    hybrid.failure = fail
    emitter = make_emitter(); emitter._do_radar()
    assert not emitter._radar_available  # fallback cannot reset exhausted build count
    assert sum(c[1] == 'HEAD' for c in hybrid.calls) == 1
    assert not any('/256/' in c[2] for c in hybrid.calls)


def test_zoom_total_deadline_and_failure_keeps_geometry(make_emitter, hybrid, tmp_path, monkeypatch):
    emitter = make_emitter(); emitter._do_radar(); before = emitter._radar_result
    (tmp_path / 'radar_zoom').write_text('9')
    monkeypatch.setattr(ae, 'RADAR_REQUESTS_PER_MIN', 1000)
    monkeypatch.setattr(ae, 'RADAR_MAX_FRAME_BUILDS_PER_PASS', 100)
    hybrid.view(); start = hybrid.mono
    hybrid.failure = lambda *_: setattr(hybrid, 'mono', hybrid.mono + 1)
    emitter._do_radar()
    assert hybrid.mono - start == ae.RADAR_BUILD_DEADLINE_SEC
    assert emitter._radar_zoom == 9 and emitter._radar_mpp == pytest.approx(before.mpp / 2)
    previous = emitter._radar_result
    (tmp_path / 'radar_zoom').write_text('4')
    hybrid.failure = lambda *_: (_ for _ in ()).throw(urllib.error.URLError('outage'))
    emitter._do_radar()
    assert emitter._radar_result is previous  # never relabel the retained crop z4


@pytest.mark.parametrize('lat,lon', [(47.61,-122.33), (52.52,13.40), (-33.87,151.21), (0,-140), (0,179.5)])
@pytest.mark.parametrize('zoom', [4, 9])
@pytest.mark.parametrize('unit,factor', [('mi',1609.344), ('km',1000)])
def test_extreme_zoom_geometry(lat, lon, zoom, unit, factor):
    tiles, mpp, bounds, marker = ae._radar_viewport(lat, lon, zoom, 480)
    mask = Image.new('1', (480,480))
    for x,y,dx,dy in tiles:
        assert 0 <= x < 2**zoom and 0 <= y < 2**zoom
        mask.paste(1, (dx,dy,dx+256,dy+256))
    assert mask.getextrema() == (1,1) and marker == (240,240)
    assert bounds['s'] < lat < bounds['n']
    if lon == 179.5: assert bounds['e'] < bounds['w']
    else: assert bounds['w'] < lon < bounds['e']
    bar, rings = ae._radar_scale(mpp,480,unit)
    assert bar['pixels'] == pytest.approx(bar['meters']/mpp) and bar['pixels'] <= 192
    for ring in rings:
        assert ring['px'] == pytest.approx(float(ring['label'].split()[0])*factor/mpp)
        assert ring['px'] <= 480 / 2**.5


@pytest.mark.parametrize('query,expected', [('radarZoom=4','4'), ('radarZoom=%38','8'),
    ('radarZoom=9','9'), ('radarZoom=auto','auto'), ('radarZoom=3',None),
    ('radarZoom=11',None), ('radarZoom=10','10'), ('radarZoom=7.0',None), ('radarZoom=',None),
    ('radarZoom=8&radarZoom=9',None), ('radarZoom=auto&radarZoom=',None),
    ('x=radarZoom%3D8',None), ('radarZoom=8junk',None), ('radarZoom=%FF',None)])
@pytest.mark.parametrize('address', ['127.0.0.1', '::1', '::ffff:127.0.0.1', '198.51.100.1'])
def test_server_parses_only_loopback(monkeypatch, tmp_path, query, expected, address):
    module = _load_serve(monkeypatch,tmp_path,_payload())
    assert module.RADAR_MIN_ZOOM == ae.RADAR_MIN_ZOOM
    assert module.RADAR_MAX_DESIRED_ZOOM == max(s['max_zoom'] for s in ae._RADAR_SOURCES.values())
    served = []
    monkeypatch.setattr(module.http.server.SimpleHTTPRequestHandler,'do_GET',lambda h: served.append(h.path))
    h = object.__new__(module.Handler); h.client_address=(address,1); h.path='/wx.json?'+query
    h.do_GET(); pref=tmp_path/'radar_zoom'
    expected = expected if address in module.LOOPBACK else None
    assert (pref.read_text().strip() if pref.exists() else None) == expected
    assert served == [h.path] and module._polls == 1


def test_server_atomic_changed_only_and_restart(serve_at, tmp_path, monkeypatch):
    module, url = serve_at(_payload(radar={'available':False,'reason':'offline'}))
    pref = tmp_path/'radar_zoom'; calls=[]; replace=os.replace
    def replacing(src,dst):
        assert Path(src).parent == Path(dst).parent
        assert Path(src).read_text() in ('8\n','auto\n')
        calls.append((src,dst)); replace(src,dst)
    monkeypatch.setattr(module.os,'replace',replacing)
    for _ in range(3): assert _get(url+'/wx.json?radarZoom=8')[0] == 200
    assert len(calls) == 1 and pref.read_text() == '8\n'
    module2 = _load_serve(monkeypatch,tmp_path,_payload())
    module2._write_radar_zoom(['8']); assert len(calls) == 1
    module2._write_radar_zoom(['auto']); assert len(calls) == 2 and pref.read_text() == 'auto\n'
    assert not list(tmp_path.glob('radar_zoom.tmp.*'))
    assert _get(url+'/health')[1]['status'] == 'ok'


@pytest.mark.parametrize('failure', ['open','replace'])
def test_zoom_io_error_does_not_break_polling(serve_at,tmp_path,monkeypatch,failure):
    module,url=serve_at(_payload())
    pref=tmp_path/'radar_zoom'; pref.write_text('6')
    if failure == 'replace':
        monkeypatch.setattr(module.os,'replace',lambda *_: (_ for _ in ()).throw(OSError('read only')))
    else:
        original=builtins.open
        def opening(path,*args,**kwargs):
            if 'radar_zoom.tmp' in str(path): raise OSError('read only')
            return original(path,*args,**kwargs)
        monkeypatch.setattr(builtins,'open',opening)
    assert _get(url+'/wx.json?radarZoom=8')[0] == 200
    assert pref.read_text() == '6' and not list(tmp_path.glob('radar_zoom.tmp.*'))
    assert _get(url+'/health')[1]['status'] == 'ok'


def test_durable_link_survives_runtime_directory_recreation(make_emitter,hybrid,tmp_path,monkeypatch):
    # Execute only the launcher's storage setup, never its Pi/display/process commands.
    script=Path('design/almanac/kiosk/almanac-kiosk.sh').read_text()
    setup=script.split('RADAR_STATE=',1)[1].split('ln -sfn "$RADAR_STATE/radar_zoom" "$DATA_DIR/radar_zoom"',1)[0]
    setup='RADAR_STATE='+setup+'ln -sfn "$RADAR_STATE/radar_zoom" "$DATA_DIR/radar_zoom"'
    state=tmp_path/'durable'; runtime=tmp_path/'runtime'; runtime.mkdir()
    env=dict(os.environ, XDG_STATE_HOME=str(state), DATA_DIR=str(runtime))
    (runtime/'radar_zoom').write_text('6')
    subprocess.run(['bash','-c',setup],env=env,check=True)
    module=_load_serve(monkeypatch,runtime,_payload())
    module._write_radar_zoom(['8'])
    assert (runtime/'radar_zoom').is_symlink()
    (runtime/'radar_zoom').unlink(); (runtime/'wx.json').unlink(); runtime.rmdir(); runtime.mkdir()
    subprocess.run(['bash','-c',setup],env=env,check=True)
    assert (runtime/'radar_zoom').read_text() == '8\n'
    emitter=make_emitter(output_path=str(runtime/'wx.json')); emitter._do_radar()
    assert emitter._radar_zoom == 8
    module._write_radar_zoom(['auto'])
    assert (runtime/'radar_zoom').is_symlink() and (state/'wfpiconsole/radar_zoom').read_text() == 'auto\n'


def test_zoom_wakeup_coalesces_busy_changes_and_stops(make_emitter,tmp_path,monkeypatch):
    emitter=make_emitter(); emitter._running=True
    spawned=[]; monkeypatch.setattr(emitter,'_check_radar',lambda: spawned.append(True))
    pref=tmp_path/'radar_zoom'; pref.write_text('6')
    emitter._inflight.add('radar')
    emitter._check_radar_zoom(); pref.write_text('8'); emitter._check_radar_zoom()
    assert not spawned
    emitter._inflight.clear(); emitter._check_radar_zoom(); emitter._check_radar_zoom()
    assert len(spawned) == 1
    pref.write_text('auto'); emitter._check_radar_zoom(); assert len(spawned)==2
    pref.unlink(); emitter._check_radar_zoom(); assert len(spawned)==3
    emitter.stop(); pref.write_text('9'); emitter._check_radar_zoom(); assert len(spawned)==3
