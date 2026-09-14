"""Radar geometry, complete-frame cache, atomic publication and real palette fidelity."""
import builtins
import io
import json
import math
import os
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from lib import almanac_emit as ae
from tests.fixtures.config import make_config


@pytest.fixture(autouse=True)
def radar_dir(tmp_path, monkeypatch):
    directory = tmp_path / 'radar'
    monkeypatch.setenv('WFP_RADAR_DIR', str(directory))
    monkeypatch.setattr(ae, 'RADAR_DIR', os.environ['WFP_RADAR_DIR'])
    return directory


@pytest.fixture
def radar_net(monkeypatch):
    tile = io.BytesIO()
    Image.new('RGBA', (256, 256), (0, 163, 224, 100)).save(tile, format='PNG')
    state = dict(times=[1800000000, 1800000600], calls=[], fail=None, tile=tile.getvalue())

    def fetch(req, timeout):
        assert 0 < timeout <= ae.RADAR_HTTP_TIMEOUT_SEC
        assert req.get_header('User-agent') == 'WeatherFlow-PiConsole-almanac'
        url = req.full_url
        if url == ae.RADAR_IEM_METADATA_URL:
            raise urllib.error.URLError('primary unavailable in fallback fixture')
        state['calls'].append(url)
        if state['fail']:
            state['fail'](url)
        if url == ae.RADAR_RAINVIEWER_MANIFEST_URL:
            return io.BytesIO(json.dumps(dict(host='https://tiles.example', radar=dict(
                past=[dict(time=t, path=f'/v2/{t}') for t in reversed(state['times'])],
                nowcast=[dict(time=1999999999, path='/never')]))).encode())
        assert url.endswith('/2/0_0.png') and int(url.split('/256/')[1].split('/')[0]) in range(4, 8)
        return io.BytesIO(state['tile'])

    monkeypatch.setattr(ae.RadarSession, 'open', lambda self, *a, **k: fetch(*a, **k))
    monkeypatch.setattr(ae.time, 'sleep', lambda _: None)
    monkeypatch.setattr(ae.time, 'time', lambda: state['times'][-1] + 240)
    return state


def tile_calls(state):
    return [u for u in state['calls'] if u != ae.RADAR_RAINVIEWER_MANIFEST_URL]


@pytest.fixture
def radar_viewed(tmp_path):
    marker = tmp_path / 'radar_viewed'
    marker.write_text(str(ae.time.time()))
    return marker


@pytest.mark.parametrize('lat,expected', [(0, 9), (47.6, 8), (60, 8), (78, 6),
                                         (-78, 6), (85, 5), (90, 4), (-90, 4)])
def test_zoom_targets_coverage_with_clamps(lat, expected):
    zoom = ae._radar_zoom_for(lat)
    assert zoom == expected
    assert ae.RADAR_MIN_ZOOM <= zoom <= ae.RADAR_MAX_ZOOM
    raw = math.log2(156543.03392 * math.cos(math.radians(lat)) / (200000 / 490))
    if 4 <= round(raw) <= 9:
        coverage = ae._radar_viewport(lat, 0, zoom, 490)[1] * 490
        assert 200000 / math.sqrt(2) <= coverage <= 200000 * math.sqrt(2)
    assert ae._radar_zoom_for(0) > ae._radar_zoom_for(78)


def test_zoom_recomputed_each_pass_and_latitude_change(make_emitter, radar_net, monkeypatch):
    calls = []
    original = ae._radar_zoom_for
    def zoom_for(lat):
        calls.append(lat)
        return original(lat)
    monkeypatch.setattr(ae, '_radar_zoom_for', zoom_for)
    emitter = make_emitter()
    emitter._do_radar(); emitter._do_radar()
    assert calls == [47.61, 47.61]
    emitter.app.config = make_config(Station={'Latitude': '78'})
    radar_net['times'] = [1800001200]
    radar_net['calls'].clear()
    emitter._do_radar()
    assert calls == [47.61, 47.61, 78]
    assert emitter._build_payload()['radar']['zoom'] == 6
    assert all('/256/6/' in url for url in tile_calls(radar_net))


@pytest.mark.parametrize('marker', [None, 'old', 'boundary', 'garbage', '', 'nan', 'inf', '-inf', 'future', b'\xff'])
def test_unviewed_builds_only_latest(make_emitter, radar_net, radar_dir, tmp_path, marker):
    if marker is not None:
        marker = {'old': str(ae.time.time() - 901), 'boundary': str(ae.time.time() - 900),
                  'future': str(ae.time.time() + 3600)}.get(marker, marker)
        (tmp_path / 'radar_viewed').write_bytes(marker if isinstance(marker, bytes) else marker.encode())
    radar_net['times'] = [1800000000 + i * 600 for i in range(13)]
    emitter = make_emitter(); emitter._do_radar()
    frames = emitter._radar_frames
    assert [f['ts'] for f in frames] == radar_net['times'][-7:]
    assert all(not f['complete'] and 'url' not in f for f in frames[:-1])
    assert frames[-1]['complete'] and emitter._radar_latest == frames[-1]['id']
    assert len(list(radar_dir.rglob('*.png'))) == 1
    assert len(tile_calls(radar_net)) == 15
    assert len(radar_net['calls']) == 16


def test_viewing_warms_history_then_expiry_prunes_it(make_emitter, radar_net, radar_dir, tmp_path):
    radar_net['times'] = [1800000000 + i * 600 for i in range(13)]
    emitter = make_emitter(); emitter._do_radar()
    marker = tmp_path / 'radar_viewed'
    marker.write_text(str(ae.time.time()))
    radar_net['calls'].clear(); emitter._radar_request_times.clear(); emitter._do_radar()
    emitter._radar_request_times.clear(); emitter._do_radar()
    assert len(tile_calls(radar_net)) == 7 * 15  # six history frames + adjacent newest
    assert sum('/256/6/' in u for u in tile_calls(radar_net)) == 15
    assert all(f['complete'] for f in emitter._radar_frames)
    assert len(list(radar_dir.rglob('*.png'))) == 7
    emitter._radar_request_times.clear()
    marker.write_text(str(ae.time.time() - ae.RADAR_VIEW_TTL - 1))
    # Two new manifest frames: even an uncached intermediate frame is skipped.
    radar_net['times'] = radar_net['times'][2:] + [1800007800, 1800008400]
    radar_net['calls'].clear(); emitter._do_radar()
    assert len(tile_calls(radar_net)) == 15
    assert len(list(radar_dir.rglob('*.png'))) == 8  # retired files have a decode grace period
    assert emitter._radar_latest.endswith('/1800008400')
    assert all(not f['complete'] for f in emitter._radar_frames[:-1])
    radar_net['calls'].clear(); emitter._do_radar()
    assert not tile_calls(radar_net)  # unchanged latest remains a cache hit


@pytest.mark.parametrize('lat,lon', [(47.61, -122.33), (0, 0), (-33.8, 151.2)])
def test_viewport_covers_crop_and_marker(lat, lon):
    tiles, mpp, bounds, marker = ae._radar_viewport(lat, lon, 7, 480)
    assert marker == pytest.approx((240, 240))
    assert mpp == pytest.approx(2 * math.pi * 6378137 * math.cos(math.radians(lat)) / 32768, rel=.001)
    assert bounds['s'] < lat < bounds['n'] and bounds['w'] < lon < bounds['e']
    mask = Image.new('1', (480, 480))
    for tx, ty, x, y in tiles:
        assert 0 <= tx < 128 and 0 <= ty < 128
        mask.paste(1, (x, y, x + 256, y + 256))
    assert mask.getextrema() == (1, 1)
    left = (lon + 180) / 360 * 32768 - 240
    top = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 32768 - 240
    for tx, ty, x, y in tiles:
        assert x == int(tx * 256 - left) and y == int(ty * 256 - top)


@pytest.mark.parametrize('lat,lon', [(0, 179.99), (0, -179.99), (90, 0), (-90, 0)])
def test_viewport_wraps_and_clamps(lat, lon):
    tiles, mpp, bounds, marker = ae._radar_viewport(lat, lon, 7, 480)
    assert all(0 <= x < 128 and 0 <= y < 128 for x, y, _, _ in tiles)
    assert math.isfinite(mpp) and mpp > 0
    if abs(lon) > 179:
        assert {0, 127} <= {t[0] for t in tiles}
        assert bounds['e'] < bounds['w']


@pytest.mark.parametrize('setting,unit,factor', [('mi', 'mi', 1609.344), ('miles', 'mi', 1609.344), ('km', 'km', 1000)])
def test_scale_and_rings(setting, unit, factor):
    mpp = ae._radar_viewport(47.61, -122.33, 7, 480)[1]
    assert ae._radar_distance_unit(make_config(Units={'Distance': setting})) == unit
    bar, rings = ae._radar_scale(mpp, 480, unit)
    dist = int(bar['distDisp'].split()[0])
    assert dist == max(d for d in [5, 10, 20, 25, 50, 100, 150, 200, 250] if d * factor / mpp <= 192)
    assert bar['pixels'] == pytest.approx(bar['meters'] / mpp)
    assert bar['meters'] == dist * factor and bar['unit'] == unit
    assert all(r['px'] <= 480 / math.sqrt(2) for r in rings)
    assert rings[0]['px'] == bar['pixels']


def test_nexrad_is_caption_only_and_uses_station_unit():
    assert len(ae._NEXRAD_SITES) == 160
    r = ae._radar_nexrad(47.61, -122.33, 'mi')
    assert r['id'] == 'KATX' and r['bearing'] == 'N' and r['distanceDisp'].endswith(' mi')
    assert ae._radar_nexrad(0, 0, 'km') is None
    assert ae._radar_nexrad(47.61, -122.33, 'km')['distanceDisp'].endswith(' km')


def test_composite_and_one_snapshot(make_emitter, radar_net, radar_dir, monkeypatch, radar_viewed):
    emitter = make_emitter()
    publications = []
    original = ae.AlmanacEmitter.__setattr__
    def record(self, key, value):
        if key == '_radar_result':
            publications.append(value)
        original(self, key, value)
    monkeypatch.setattr(ae.AlmanacEmitter, '__setattr__', record)
    original_replace = os.replace
    def replace(src, dst):
        assert not emitter._radar_result.available or all(
            (radar_dir / (f['id'] + '.png')).exists() for f in emitter._radar_result.frames if f['complete'])
        assert str(src).endswith('.tmp.' + str(os.getpid()))
        with Image.open(src) as image:
            assert image.size == (956, 490)
        original_replace(src, dst)
    monkeypatch.setattr(os, 'replace', replace)
    emitter._do_radar()
    assert len(publications) == 2  # latest published before history
    assert all(p.latest == p.frames[-1]['id'] and p.frames[-1]['complete'] for p in publications)
    result = emitter._radar_result
    assert result.available and result.latest.endswith('/1800000600')
    assert [f['ts'] for f in result.frames] == radar_net['times']
    assert all(f['complete'] and f['url'] == 'radar/' + f['id'] + '.png' for f in result.frames)
    assert sum('/256/7/' in u for u in tile_calls(radar_net)) == 2 * len(ae._radar_viewport(47.61, -122.33, 7, 956, 490)[0])
    assert sum('/256/6/' in u for u in tile_calls(radar_net)) == 15  # no extra publication
    with Image.open(radar_dir / (result.latest + '.png')) as image:
        assert image.getpixel((240, 240)) == (46, 147, 168, 100)  # alpha wasn't squared
    assert not list(radar_dir.rglob('*.tmp.*'))
    payload = emitter._build_payload()['radar']
    assert payload['frameCount'] == 2 and payload['marker'] == dict(x=.5, y=.5)
    assert payload['observedAt'] == datetime.fromtimestamp(result.ts_frame, ae.AlmanacEmitter._station_tz(emitter.app.config)).strftime('%H:%M')


def test_cache_and_prune(make_emitter, radar_net, radar_dir, radar_viewed):
    emitter = make_emitter(); emitter._do_radar()
    radar_net['calls'].clear(); emitter._do_radar()
    assert not tile_calls(radar_net)
    assert radar_net['calls'] == [ae.RADAR_RAINVIEWER_MANIFEST_URL]
    radar_net['times'] = [1800000600, 1800001200]
    emitter._do_radar()
    assert sum('/256/7/' in u for u in tile_calls(radar_net)) == len(ae._radar_viewport(47.61, -122.33, 7, 956, 490)[0])
    assert sum('/256/6/' in u for u in tile_calls(radar_net)) == 15  # new stamp warms again
    assert sorted(p.stem for p in radar_dir.rglob('*.png')) == ['1800000000', '1800000600', '1800001200']  # grace


@pytest.mark.parametrize('field', ['Latitude', 'Longitude'])
def test_missing_location(make_emitter, field):
    emitter = make_emitter(config=make_config(Station={field: ''})); emitter._do_radar()
    assert not emitter._radar_available and emitter._radar_reason == 'no location'


def test_zero_location_is_valid(make_emitter, radar_net):
    emitter = make_emitter(config=make_config(Station={'Latitude': '0', 'Longitude': '0'}))
    emitter._do_radar()
    assert emitter._radar_available and emitter._radar_nexrad is None


def test_pillow_absent(make_emitter, monkeypatch):
    real = builtins.__import__
    def importing(name, *args, **kwargs):
        if name == 'PIL':
            raise ImportError('Pillow absent')
        return real(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', importing)
    emitter = make_emitter(); emitter._do_radar()
    assert not emitter._radar_available and emitter._radar_reason == 'compositor unavailable'


def test_stale_uses_frame_not_manifest(make_emitter, radar_net, monkeypatch):
    ss = ae.RADAR_RAINVIEWER_STALE_SEC
    clock = {'now': radar_net['times'][-1] + ss - 1}
    monkeypatch.setattr(ae.time, 'time', lambda: clock['now'])
    emitter = make_emitter(); emitter._do_radar()
    r = emitter._build_payload()['radar']
    assert r['ageSec'] == ss - 1 and not r['stale'] and r['fetchedAt'] == clock['now']
    clock['now'] += 1                       # one second past the source's stale_sec
    r = emitter._build_payload()['radar']
    assert r['stale'] and r['ageSec'] == ss and r['staleSec'] == ss


@pytest.mark.parametrize('failure', ['manifest', 'tile', 'decode', 'save'])
def test_never_raises_keeps_last_good_and_warns_once(make_emitter, radar_net, monkeypatch, failure):
    emitter = make_emitter(); emitter._do_radar(); previous = emitter._radar_result
    radar_net['times'] = [1800001200]
    warnings, retries = [], []
    monkeypatch.setattr(ae.Logger, 'warning', warnings.append)
    monkeypatch.setattr(emitter, '_schedule_retry', lambda *a: retries.append(a))
    def fail(url):
        if failure == 'manifest' or (failure == 'tile' and url != ae.RADAR_RAINVIEWER_MANIFEST_URL):
            raise urllib.error.URLError('offline')
    radar_net['fail'] = fail
    if failure == 'decode': radar_net['tile'] = b'bad PNG'
    if failure == 'save':
        monkeypatch.setattr(Image.Image, 'save', lambda *a, **k: (_ for _ in ()).throw(OSError('disk full')))
    emitter._do_radar()
    assert emitter._radar_result is previous
    assert len(warnings) == 3 and len(retries) == 1
    assert all('candidates=' in w and 'elapsed=' in w for w in warnings[:2])
    assert retries[0] == ('radar', emitter._check_radar, 120)


def test_partial_latest_withheld_then_retried(make_emitter, radar_net, radar_dir):
    emitter = make_emitter(); emitter._do_radar()
    radar_net['times'].append(1800001200)
    failed = []
    def fail(url):
        if '/1800001200/' in url and not failed:
            failed.append(url); raise urllib.error.HTTPError(url, 404, 'not ready', {}, None)
    radar_net['fail'] = fail
    emitter._do_radar()
    assert emitter._radar_latest.endswith('/1800000600')
    assert not list(radar_dir.rglob('1800001200.png'))
    radar_net['fail'] = None
    emitter._radar_negative.clear()  # retry after negative cache expiry
    emitter._do_radar()
    assert emitter._radar_latest.endswith('/1800001200')


def test_radar_schedules_are_registered_and_cancelled(make_emitter, monkeypatch):
    from tests.test_emitter_lifecycle import FakeClock, HangingThread
    from types import SimpleNamespace
    clock = FakeClock(); monkeypatch.setattr(ae, 'Clock', clock)
    monkeypatch.setattr(ae, 'threading', SimpleNamespace(Thread=HangingThread))
    emitter = make_emitter(); emitter.start()
    assert sorted(e.timeout for e in clock.events if e.timeout in (60, 180)) == [60, 180]
    emitter._schedule_retry('radar', emitter._check_radar, 120)
    emitter._schedule_retry('radar', emitter._check_radar, 120)
    assert len(emitter._retries) == 1
    emitter._check_radar(); emitter._check_radar()
    assert emitter._inflight == {'radar'}
    emitter.stop(); assert not clock.events and not emitter._retries


# Authoritative RainViewer stops, transcribed from rainviewer_api_colors_table.csv
# (the "Universal Blue" column). The file lists a colour PER dBZ; the snow ramp is a
# second block keyed on the same dBZ axis, so its 20 dBZ stop is our snow swatch.
# Pinning the published table — not a sampled tile — is what makes the fidelity check
# deterministic: RainViewer's scale is continuous, so a rare anchor intensity (60/65
# dBZ severe cores) is often simply not falling anywhere on Earth at fetch time, and a
# live tile then paints 59/64 dBZ instead. That is weather, not a palette mismatch.
_UNIVERSAL_BLUE_RAIN = {5: '#92887164', 20: '#00a3e0ff', 30: '#005588ff',
                        40: '#ffaa00ff', 50: '#c10000ff', 60: '#ff77ffff', 65: '#ffffffff'}
_UNIVERSAL_BLUE_SNOW = {20: '#7fbfffff'}


def test_shared_legend_replaces_native_scales():
    assert all(source['legend'] is ae._RADAR_RAMP for source in ae._RADAR_SOURCES.values())
    assert all(source['legend'].get('snow') is None for source in ae._RADAR_SOURCES.values())


@pytest.mark.skipif(os.environ.get('RADAR_NET_TEST') != '1',
                    reason='fetches the RainViewer colour table; opt in with RADAR_NET_TEST=1 (kept out of CI)')
def test_legend_fidelity_against_published_colortable():
    """Online: the provider still publishes exactly the stops we render.

    Fetches rainviewer_api_colors_table.csv and checks each anchor against the
    Universal Blue column (rain) and the snow block. Deterministic — it verifies
    the source of truth, so it catches a real scale change yet never flakes on the
    weather. Skip transport outages only, never a colour mismatch. Opt-in so CI
    stays hermetic.
    """
    context = ssl.create_default_context()
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    url = 'https://www.rainviewer.com/files/rainviewer_api_colors_table.csv'
    request = urllib.request.Request(url, headers={'User-Agent': 'WeatherFlow-PiConsole-almanac'})
    try:
        with urllib.request.urlopen(request, timeout=25, context=context) as response:
            rows = response.read().decode().splitlines()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        pytest.skip(f'RainViewer colour table offline: {error}')
    header = rows[0].split(',')
    blue = header.index('Universal Blue')
    # The file is two dBZ-keyed blocks (rain, then snow); split on the dBZ reset.
    rain, snow, prev = {}, {}, None
    target = rain
    for row in rows[1:]:
        cells = row.split(',')
        dbz = int(cells[0])
        if prev is not None and dbz < prev:
            target = snow
        target[dbz] = cells[blue]
        prev = dbz
    for dbz, hexa in _UNIVERSAL_BLUE_RAIN.items():
        assert rain[dbz] == hexa, f'{dbz} dBZ drifted: table {rain[dbz]} vs legend {hexa}'
    assert snow[20] == _UNIVERSAL_BLUE_SNOW[20], 'snow stop drifted from the table'


def test_cold_start_rate_limit_covers_all_frames(make_emitter, radar_net, monkeypatch, radar_viewed, radar_dir):
    monkeypatch.setattr(ae, 'RADAR_REQUESTS_PER_MIN', 90)  # frame counts below are budget-relative
    monkeypatch.setattr(ae, 'RADAR_HISTORY_RESERVE', 17)
    clock = [0.0]
    starts = []
    original_fetch = ae.RadarSession().open
    def fetch(*args, **kwargs):
        if '/256/' in args[0].full_url:
            starts.append(clock[0])
        return original_fetch(*args, **kwargs)
    monkeypatch.setattr(ae.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(ae.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(ae.RadarSession, 'open', lambda self, *a, **k: fetch(*a, **k))
    radar_net['times'] = [1800000000 + i * 600 for i in range(13)]
    radar_viewed.write_text(str(ae.time.time()))
    emitter = make_emitter(); emitter._do_radar()
    assert len(emitter._radar_frames) == 7
    assert sum(f['complete'] for f in emitter._radar_frames) == 4
    clock[0] += 60; emitter._do_radar()
    assert all(f['complete'] for f in emitter._radar_frames)
    assert len(list(radar_dir.rglob('*.png'))) == 7
    assert len(radar_net['calls']) == 107
    assert len(starts) == 105  # every tile belongs to a complete frame
    assert all(sum(t <= v < t + 60 for v in starts) <= 90 for t in starts)
