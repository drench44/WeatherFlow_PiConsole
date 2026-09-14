"""Radar engine v3: spherical coverage, multi-site scans and intent generations."""
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image, ImageChops

from lib import almanac_emit as ae
from lib.radar_geometry import circle_intersects_bounds, distance_meters, EARTH_RADIUS_METERS
from tests.fixtures.config import make_config
from tests.test_radar_hybrid import hybrid, png  # noqa: F401
from tests.test_emitter_lifecycle import FakeClock, InlineThread


def test_circle_intersection_edges_corners_and_wrap():
    import math
    bounds = dict(w=-1, e=1, s=-1, n=1)
    # 231 km beyond the top edge is out; 229 km is in.
    deg = math.degrees(1000 / EARTH_RADIUS_METERS)
    assert not circle_intersects_bounds(1+231*deg, 0, 230000, bounds)
    assert circle_intersects_bounds(1+229*deg, 0, 230000, bounds)
    # Diagonally beyond both edges: include a circle grazing the corner, but
    # exclude one whose bounding box alone overlaps (not a circle intersection).
    corner_distance = distance_meters(2, 2, 1, 1)
    assert circle_intersects_bounds(2, 2, corner_distance+1, bounds)
    assert not circle_intersects_bounds(2, 2, corner_distance-1, bounds)
    wrapped = dict(w=179, e=-179, s=-1, n=1)
    assert circle_intersects_bounds(0, -179.5, 1, wrapped)
    assert not circle_intersects_bounds(0, 0, 230000, wrapped)
    assert circle_intersects_bounds(82, 10, 230000, dict(w=-2, e=2, s=80, n=84))


def test_cap_is_viewport_distance_not_station(monkeypatch):
    sites = {f'K{i:03}': (47, -123+i*.1, str(i)) for i in range(9)}
    monkeypatch.setattr(ae, '_NEXRAD_SITES', sites)
    bounds = ae._radar_viewport(47, -122, 4, 956, 490)[2]
    selected, considered = ae._radar_sites((47, -123), bounds)
    assert considered == 9 and len(selected) == ae.RADAR_SITE_MAX_COUNT == 4
    assert [s['id'] for s in selected] == [f'K{i:03}' for i in range(5,9)]
    assert [s['viewportDistanceMeters'] for s in selected] == sorted((s['viewportDistanceMeters'] for s in selected), reverse=True)


@pytest.fixture
def multisite(hybrid, monkeypatch, tmp_path):
    monkeypatch.setattr(ae, '_NEXRAD_SITES', {
        'KNEA': (47.61, -122.33, 'nearest'),
        'KMID': (47.8, -122.33, 'middle'),
        'KFAR': (48, -122.33, 'far')})
    state = SimpleNamespace(scans={
        'KNEA': [hybrid.latest-600, hybrid.latest],
        'KMID': [hybrid.latest-720, hybrid.latest-60, hybrid.latest+60],
        'KFAR': []}, calls=[], colors={'KNEA': (82, 214, 162, 128), 'KMID': (12, 145, 16, 255)}, failure=None)
    original = ae.RadarSession.open
    def fetch(self, req, timeout):
        url = req.full_url
        if 'operation=list' in url:
            site = 'K'+parse_qs(urlsplit(url).query)['radar'][0]
            state.calls.append(('list', site))
            return io.BytesIO(json.dumps(dict(scans=[dict(ts=datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%MZ'))
                for t in state.scans[site]])).encode())
        if 'ridge::' in url:
            site = 'K'+url.split('ridge::')[1][:3]
            state.calls.append(('tile', site, url))
            if state.failure:
                state.failure(site, url)
            return io.BytesIO(png(state.colors[site]))
        return original(self, req, timeout)
    monkeypatch.setattr(ae.RadarSession, 'open', fetch)
    (tmp_path/'radar_source').write_text('site')
    return state


def test_multisite_alignment_stacking_dark_and_identity(make_emitter, hybrid, multisite):
    hybrid.view()
    emitter = make_emitter(); emitter._do_radar()
    r = emitter._build_payload()['radar']
    assert r['available'] and r['siteId'] == 'KNEA'
    assert r['sitesConsidered'] == r['sitesDrawn'] == 3
    assert [s['id'] for s in r['sites']] == ['KFAR', 'KMID', 'KNEA']
    assert not r['sites'][0]['contributing'] and r['sites'][0]['reason']=='not reporting'
    assert r['sites'][-1]['primary']
    assert [c for c in multisite.calls if c[0]=='list'] == [('list', 'KNEA'), ('list', 'KMID'), ('list', 'KFAR')]
    assert [f['ts'] for f in r['frames']] == multisite.scans['KNEA']
    assert [f['siteScans'] for f in r['frames']] == [
        [dict(id='KMID', ts=hybrid.latest-720), dict(id='KNEA', ts=hybrid.latest-600)],
        [dict(id='KMID', ts=hybrid.latest-60), dict(id='KNEA', ts=hybrid.latest)]]
    expected = Image.alpha_composite(ae.remap(Image.new('RGBA', (1, 1), multisite.colors['KMID']), 'iem-nexrad-n0b', ae._RADAR_LUT),
                                     ae.remap(Image.new('RGBA', (1, 1), multisite.colors['KNEA']), 'iem-nexrad-n0b', ae._RADAR_LUT)).getpixel((0, 0))
    with Image.open(Path(ae.RADAR_DIR)/(r['latest']+'.png')) as image:
        assert image.getpixel((478, 245)) == expected
    multisite.calls.clear(); emitter._do_radar()
    assert all(c[0]=='list' for c in multisite.calls)  # full warm reuse
    assert emitter._radar_latest == r['latest']
    multisite.scans['KMID'].insert(-1, hybrid.latest)
    multisite.calls.clear(); emitter._do_radar()
    assert emitter._radar_latest != r['latest']  # primary slot is unchanged
    assert emitter._radar_ts_frame == r['observedTs']


def test_dark_nearest_promotes_reporting_primary(make_emitter, hybrid, multisite):
    multisite.scans['KNEA'] = [hybrid.now-900]
    emitter = make_emitter(); emitter._do_radar()
    r = emitter._build_payload()['radar']
    assert r['available'] and r['sourceMode']=='site' and r['siteId']=='KMID'
    assert r['sources'][1]['siteId']=='KMID' and r['sources'][1]['available']
    assert r['sites'][-1]['reporting'] is False and r['sites'][-1]['ageSec']==900
    assert not any(c[:2]==('tile', 'KNEA') for c in multisite.calls)


def test_slot_omits_stale_and_future_and_dark_scans():
    ctx = dict(sites=[dict(id='old', reporting=True), dict(id='future', reporting=True),
                     dict(id='good', reporting=True), dict(id='dark', reporting=False)],
               site_scans={'old': (0,), 'future': (1001,), 'good': (101, 998, 1001), 'dark': (999,)})
    assert ae._radar_site_pairs(ctx, 1000) == (('good', 998),)


@pytest.mark.parametrize('zoom', [7, 8])
def test_cap_limits_scan_requests_in_real_adapter(make_emitter, hybrid, multisite, monkeypatch, tmp_path, zoom):
    sites = {f'K{i:03}': (47.61+i*.01, -122.33, str(i)) for i in range(9)}
    monkeypatch.setattr(ae, '_NEXRAD_SITES', sites)
    multisite.scans = {site: [hybrid.latest] for site in sites}
    multisite.colors = {site: (20, 80, 120, 100) for site in sites}
    (tmp_path/'radar_zoom').write_text(str(zoom))
    emitter = make_emitter(); emitter._do_radar()
    r = emitter._build_payload()['radar']
    assert r['sourceMode'] == 'site' and r['sitesConsidered'] == 9 and r['sitesDrawn'] == 4
    assert [c[1] for c in multisite.calls if c[0] == 'list'] == [f'K{i:03}' for i in range(4)]
    assert len(emitter._radar_request_times) <= ae.RADAR_REQUESTS_PER_MIN
    latest = next(f for f in r['frames'] if f['id'] == r['latest'])
    assert [s['id'] for s in latest['siteScans']] == [f'K{i:03}' for i in reversed(range(4))]


def test_neighbor_listing_error_does_not_fail_reporting_primary(make_emitter, hybrid, multisite, monkeypatch):
    emitter = make_emitter(); original = emitter._radar_request
    def fetch(source, url, *args, **kwargs):
        if 'radar=MID' in url:
            raise OSError('neighbor listing down')
        return original(source, url, *args, **kwargs)
    monkeypatch.setattr(emitter, '_radar_request', fetch)
    emitter._do_radar()
    assert emitter._radar_available and emitter._radar_result.site_id == 'KNEA'
    assert emitter._radar_result.sites[1]['reporting'] is False


def test_site_budget_aborts_without_negative_cache(make_emitter, hybrid, multisite, monkeypatch):
    monkeypatch.setattr(ae, 'RADAR_REQUESTS_PER_MIN', 5)
    emitter = make_emitter(); emitter._do_radar()
    assert not emitter._radar_available
    assert len(emitter._radar_request_times) == 5
    assert not emitter._radar_negative
    assert emitter._radar_refresh['state'] == 'failed'
    assert not list(Path(ae.RADAR_DIR).rglob('*.png'))


@pytest.mark.parametrize('budget', ['requests', 'deadline'])
def test_site_budget_retains_complete_nearest_layers(make_emitter, hybrid, multisite, monkeypatch, budget):
    emitter = make_emitter()
    tile_count = len(ae._radar_viewport(47.61, -122.33, 8, 956, 490)[0])
    if budget == 'requests':
        monkeypatch.setattr(ae, 'RADAR_REQUESTS_PER_MIN', 3+tile_count+2)
    else:
        def slow(site, url):
            if site == 'KMID':
                hybrid.mono = ae.RADAR_PRIMARY_DEADLINE_SEC
        multisite.failure = slow
    emitter._do_radar()
    assert emitter._radar_available and emitter._radar_result.site_id == 'KNEA'
    latest = next(f for f in emitter._radar_frames if f['id'] == emitter._radar_latest)
    assert latest['siteScans'] == [dict(id='KNEA', ts=hybrid.latest)]
    assert not emitter._radar_negative  # budget is never a site outage
    assert emitter._radar_refresh['state'] == 'idle'
    with Image.open(Path(ae.RADAR_DIR)/(latest['id']+'.png')) as image:
        assert image.getpixel((478, 245)) == ae.remap(Image.new('RGBA',(1,1),multisite.colors['KNEA']), 'iem-nexrad-n0b', ae._RADAR_LUT).getpixel((0,0))


def test_site_tile_failure_discards_whole_layer_recovers_after_negative_ttl(make_emitter, hybrid, multisite):
    n = [0]
    def failure(site, url):
        if site == 'KMID':
            n[0] += 1
            if n[0] == 2:
                raise OSError('second tile broken')
    multisite.failure = failure
    emitter = make_emitter(); emitter._do_radar()
    first = emitter._radar_result
    latest = next(f for f in first.frames if f['id']==first.latest)
    assert latest['siteScans'] == [dict(id='KNEA', ts=hybrid.latest)]
    with Image.open(Path(ae.RADAR_DIR)/(first.latest+'.png')) as image:
        assert image.getpixel((478, 245)) == ae.remap(Image.new('RGBA',(1,1),multisite.colors['KNEA']), 'iem-nexrad-n0b', ae._RADAR_LUT).getpixel((0,0))
    assert len(emitter._radar_negative)==1
    assert first.sites[1]['reporting']  # listing health and tile failure are distinct
    multisite.failure = None; multisite.calls.clear(); emitter._do_radar()
    assert emitter._radar_latest==first.latest and all(c[0]=='list' for c in multisite.calls)
    hybrid.mono += ae.RADAR_NEGATIVE_CACHE_SEC
    emitter._do_radar()
    assert emitter._radar_latest != first.latest
    assert len(next(f for f in emitter._radar_frames if f['id']==emitter._radar_latest)['siteScans'])==2


@pytest.mark.parametrize('preference,value', [('radar_zoom','9'), ('radar_source','site'), ('radar_center','47.8,-122.3'), ('radar_intent','41')])
def test_supersede_tile_boundary_immediate_new_pass(make_emitter, hybrid, monkeypatch, tmp_path, preference, value):
    emitter = make_emitter(); emitter._do_radar(); old = emitter._radar_result
    hybrid.latest += 120; hybrid.now += 120
    clock = FakeClock()
    monkeypatch.setattr(ae, 'Clock', clock)
    monkeypatch.setattr(ae, 'threading', SimpleNamespace(Thread=InlineThread))
    emitter._running = True
    emitter._schedule_retry('radar', emitter._check_radar, 120)
    seen = []
    def slow(req, timeout):
        if 'mrms::' in req.full_url and not seen:
            seen.append(emitter._radar_refresh)
            hybrid.mono += .25
            (tmp_path/preference).write_text(value)
    hybrid.failure = slow
    closed = []
    monkeypatch.setattr(ae.RadarSession, 'close', lambda self: closed.append(self))
    emitter._check_radar()
    assert emitter._radar_result is old and emitter._radar_restart
    assert emitter._radar_refresh['state'] == 'superseded' and not emitter._radar_negative
    assert not list(Path(ae.RADAR_DIR).rglob('*.tmp.*'))
    assert not closed and not emitter._inflight
    assert 'radar' not in emitter._retries
    assert any(e.timeout==0 for e in clock.events)
    intents = []
    original = emitter._radar_publish_refresh
    def record(ctx, **kw):
        original(ctx, **kw)
        intents.append(emitter._radar_refresh['intent'])
    monkeypatch.setattr(emitter, '_radar_publish_refresh', record)
    clock.advance(ae.EMIT_INTERVAL)
    assert not emitter._radar_restart and intents
    # Provider fallback may drop IEM, but normal same-provider passes keep it.
    if preference != 'radar_source': assert not closed
    expect = {'radar_zoom': ('zoom',9), 'radar_source': ('source','site'),
              'radar_center': ('center',dict(lat=47.8, lon=-122.3)), 'radar_intent': ('seq',41)}[preference]
    assert all(intent[expect[0]]==expect[1] for intent in intents)
    assert emitter._radar_refresh['state']=='idle'
    emitter.stop()


def test_supersede_during_tmp_save_removes_file(make_emitter, hybrid, monkeypatch, tmp_path):
    emitter=make_emitter()
    save=Image.Image.save
    def changed(image, target, *args, **kwargs):
        save(image, target, *args, **kwargs)
        if '.tmp.' in str(target):
            (tmp_path/'radar_zoom').write_text('9')
    monkeypatch.setattr(Image.Image, 'save', changed)
    emitter._do_radar()
    assert not emitter._radar_available and emitter._radar_restart
    assert not emitter._radar_negative and emitter._radar_refresh['state']=='superseded'
    assert not list(Path(ae.RADAR_DIR).rglob('*.png'))
    assert not list(Path(ae.RADAR_DIR).rglob('*.tmp.*'))


def test_supersede_between_history_frames_keeps_published_newest(make_emitter, hybrid, monkeypatch, tmp_path):
    hybrid.view(); emitter=make_emitter()
    original=ae.AlmanacEmitter.__setattr__
    published=[]
    def record(self, key, value):
        original(self, key, value)
        if key=='_radar_result' and value.available:
            published.append(value)
            (tmp_path/'radar_center').write_text('47.8,-122.3')
    monkeypatch.setattr(ae.AlmanacEmitter, '__setattr__', record)
    emitter._do_radar()
    assert len(published)==1 and emitter._radar_result is published[0]
    assert sum(f['complete'] for f in emitter._radar_frames)==1
    assert not emitter._radar_negative and emitter._radar_restart
    assert emitter._radar_refresh['state']=='superseded'


def test_site_negative_transaction_aborted_frame(make_emitter, hybrid, multisite, tmp_path):
    emitter=make_emitter()
    def fail(site, url):
        if site=='KNEA':
            raise OSError('site tile failed')
        (tmp_path/'radar_zoom').write_text('9')
    multisite.failure=fail
    emitter._do_radar()
    assert emitter._radar_restart and not emitter._radar_available
    assert not emitter._radar_negative
    assert not list(Path(ae.RADAR_DIR).rglob('*.png'))


@pytest.mark.parametrize('failure', [False, True])
def test_progress_atomic_shape_phases_and_reset(make_emitter, hybrid, monkeypatch, failure):
    hybrid.view(); emitter=make_emitter(); records=[]; copies=[]
    original=ae.AlmanacEmitter.__setattr__
    def record(self, key, value):
        original(self, key, value)
        if key=='_radar_refresh':
            records.append(value); copies.append(json.loads(json.dumps(value)))
            payload=self._build_payload()['radar']
            assert payload['refresh'] == {k:v for k,v in value.items() if k!='intent'}
            assert payload['intent'] == value['intent']
            assert 'progress' not in payload
    monkeypatch.setattr(ae.AlmanacEmitter, '__setattr__', record)
    if failure:
        hybrid.failure=lambda *_: (_ for _ in ()).throw(OSError('offline'))
    emitter._do_radar()
    assert records[-1]['state']==('failed' if failure else 'idle') and records==copies
    for r in records:
        assert set(r)=={'state','frameIndex','frameTotal','forSeq','intent'}
        assert 0<=r['frameIndex']<=r['frameTotal']
        assert r['intent']==dict(seq=0,zoom='auto',source='mosaic',center='station')
    if not failure:
        assert {r['state'] for r in records}=={'newest','history','idle'}
        indices=list(dict.fromkeys(r['frameIndex'] for r in records if r['frameIndex']))
        assert indices==list(range(1,max(indices)+1))
        assert any(r['state']=='newest' and r['frameTotal']==31 for r in records)


@pytest.mark.skipif(os.environ.get('RADAR_NET_TEST')!='1', reason='opt in with RADAR_NET_TEST=1')
def test_live_aberdeen_multisite(make_emitter, tmp_path, monkeypatch):
    """Real pass and independent pixel reconstruction; no synthetic echo evidence."""
    monkeypatch.setattr(ae, 'RADAR_DIR', str(tmp_path/'radar'))
    (tmp_path/'radar_source').write_text('site')
    (tmp_path/'radar_zoom').write_text('7')
    emitter=make_emitter(config=make_config(Station={'Latitude':'46.98','Longitude':'-123.82'}))
    raw_tiles={}; original=emitter._radar_request
    def request(source, url, *args, **kwargs):
        raw=original(source, url, *args, **kwargs)
        if 'ridge::' in url: raw_tiles[url]=raw
        return raw
    monkeypatch.setattr(emitter, '_radar_request', request)
    monkeypatch.setattr(ae.Logger, 'warning', print)
    emitter._do_radar()
    r=emitter._build_payload()['radar']
    print('Aberdeen live:', json.dumps({k:r[k] for k in ('available','sourceMode','siteId','zoom','sites','sitesConsidered','sitesDrawn')}))
    assert r['available'] and r['sourceMode']=='site' and r['zoom']==7
    assert {'KLGX','KATX','KRTX'} <= {s['id'] for s in r['sites']}
    reporting=[s for s in r['sites'] if s['reporting']]
    assert r['siteId']==min(reporting,key=lambda s:s['distanceMeters'])['id']
    newest=next(f for f in r['frames'] if f['id']==r['latest'])
    tiles=ae._radar_viewport(46.98,-123.82,7,956,490)[0]
    expected=Image.new('RGBA',(956,490)); layers=[]
    for pair in newest['siteScans']:
        stamp=datetime.fromtimestamp(pair['ts'],timezone.utc).strftime('%Y%m%d%H%M')
        layer=Image.new('RGBA',expected.size)
        for tx,ty,x,y in ae._radar_site_tiles(dict(zoom=7,tiles=tiles),pair['id']):
            url=ae.RADAR_SITE_TILE_TEMPLATE.format(site=pair['id'][1:],stamp=stamp,z=7,x=tx,y=ty)
            with Image.open(io.BytesIO(raw_tiles[url])) as tile:
                layer.paste(ae.remap(tile,'iem-nexrad-n0b',ae._RADAR_LUT),(x,y))
        layers.append((pair['id'],layer))
        expected.alpha_composite(layer)
    with Image.open(Path(ae.RADAR_DIR)/(r['latest']+'.png')) as actual:
        assert actual.tobytes()==expected.tobytes()
    # Visibility includes translucent foreground; measure nonzero per-site alpha
    # after the attenuation by all closer layers, rather than just tile receipts.
    transmission=Image.new('L',expected.size,255); visible={}
    for site,layer in reversed(layers):
        alpha=layer.getchannel('A')
        contribution=ImageChops.multiply(alpha,transmission)
        visible[site]=sum(count for value,count in enumerate(contribution.histogram()) if value)
        transmission=ImageChops.multiply(transmission,ImageChops.invert(alpha))
    print('Aberdeen visible echo pixels by site:',json.dumps(visible),'requests=',len(emitter._radar_request_times))
    (tmp_path/'aberdeen.json').write_text(json.dumps(dict(
        radar=r, visibleEchoPixels=visible, requests=len(emitter._radar_request_times)), indent=2))
    assert newest['siteScans'] and layers  # reporting and acquisition, independent of weather
    # Real dark sites are recorded without making the pass fail. If none are
    # dark today, the hermetic promotion/degradation test supplies that coverage.
    for site in r['sites']:
        if site['newestTs'] is None or site['ageSec']>=900:
            assert site['reporting'] is False
            assert site['id'] not in [p['id'] for p in newest['siteScans']]
    assert emitter._radar_refresh['state']=='idle'
    assert any(s['id']=='KLGX' and s['contributing'] for s in r['sites'])
    atx=next(s for s in r['sites'] if s['id']=='KATX')
    if not atx['reporting']:
        assert not atx['contributing'] and atx['reason']=='not reporting'
    print('KATX live reporting status:',json.dumps(atx))
    (tmp_path/'radar_zoom').write_text('5'); emitter._radar_request_times.clear(); emitter._do_radar()
    fallback=emitter._build_payload()['radar']
    print('Aberdeen z5:',json.dumps({k:fallback[k] for k in ('zoom','sourceMode','sourcePref','sourceFallback')}))
    assert fallback['zoom']==5 and fallback['sourceMode']=='mosaic' and fallback['sourceFallback']=='site-zoom-floor'


def test_site_preference_auto_swap_and_return(make_emitter, hybrid, multisite, tmp_path):
    pref=tmp_path/'radar_source'; before=pref.read_bytes()
    (tmp_path/'radar_zoom').write_text('5'); (tmp_path/'radar_intent').write_text('41')
    emitter=make_emitter();emitter._do_radar();r=emitter._build_payload()['radar']
    assert r['sourceMode']=='mosaic' and r['sourceFallback']=='site-zoom-floor'
    assert r['sourcePref']=='site' and r['sitePreferred'] and r['siteResumeZoom']==7
    assert r['zoom']==5 and r['zoomMin']==4 and not r['zoomCapped']
    assert r['intent']==dict(seq=41,zoom=5,source='site',center='station')
    assert r['refresh']['forSeq']==41 and pref.read_bytes()==before
    hybrid.mono+=60; (tmp_path/'radar_zoom').write_text('7'); emitter._do_radar()
    r=emitter._build_payload()['radar']
    assert r['sourceMode']=='site' and r['sourceFallback'] is None and pref.read_bytes()==before


@pytest.mark.parametrize('count,cap',[(1,31),(2,8)])
def test_site_history_cap(make_emitter,hybrid,multisite,monkeypatch,count,cap):
    monkeypatch.setattr(ae,'RADAR_REQUESTS_PER_MIN',10000)
    monkeypatch.setattr(ae,'RADAR_MAX_FRAME_BUILDS_PER_PASS',100)
    stamps=list(range(hybrid.latest-3600,hybrid.latest+1,120))
    multisite.scans={'KNEA':stamps,'KMID':stamps if count==2 else [],'KFAR':[]}
    hybrid.view(); emitter=make_emitter();emitter._do_radar()
    assert len(emitter._radar_frames)==cap


def test_site_tile_clipping_uses_circle_not_whole_viewport(monkeypatch):
    monkeypatch.setattr(ae,'_NEXRAD_SITES',{'KONE':(47,-124,'test')})
    tiles=ae._radar_viewport(47,-122,7,956,490)[0]
    selected=ae._radar_site_tiles(dict(zoom=7,tiles=tiles),'KONE')
    assert 0<len(selected)<len(tiles)
    for tile in tiles:
        x,y=tile[:2]; n,w=ae.world_inverse(x*256,y*256,7);south,e=ae.world_inverse((x+1)*256,(y+1)*256,7)
        assert (tile in selected)==circle_intersects_bounds(47,-124,230000,dict(n=n,s=south,w=w,e=e))


def test_runtime_sequence_validation(monkeypatch,tmp_path):
    from tests.test_freshness_health import _load_serve
    serve_at=_load_serve(monkeypatch,tmp_path,{})
    marker=tmp_path/'radar_intent'
    def write_seq(value):
        serve_at._write_radar_intent(dict(radarSeq=[value],radarZoom=['7'],radarSource=['site'],radarCenter=['station']))
    for value in ('1','41','999999999999'):
        write_seq(value)
        assert json.loads(marker.read_text())['seq']==int(value)
    before=marker.read_bytes()
    for value in ('-1','1.0','1e2','1234567890123','١',''):
        write_seq(value)
        assert marker.read_bytes()==before
    durable=tmp_path/'durable-seq';durable.write_text('8')
    marker.unlink();marker.symlink_to(durable)
    write_seq('42')
    assert not marker.is_symlink() and durable.read_text()=='8'


def test_remap_degradation_survives_warm_cache(make_emitter,hybrid):
    from lib.radar_palette import _tables
    colors=[c for c,d in _tables('iem-mrms-lcref')[0].items() if d>=10 and c[3]==255][:19]
    tile=Image.new('RGBA',(256,256),(19,37,53,255))
    for i,color in enumerate(colors[1:]+[(19,37,53,255)]):tile.putpixel((i,0),color)
    stream=io.BytesIO();tile.save(stream,'PNG');hybrid.tile=stream.getvalue()
    emitter=make_emitter();emitter._do_radar();r=emitter._build_payload()['radar']
    frame=next(f for f in r['frames'] if f['id']==r['latest'])
    assert r['legend']['remapped'] is False and frame['legend']['remapped'] is False
    assert frame['unmatchedColors']==1 and frame['unmatchedPixels']>.98*frame['opaquePixels']
    hybrid.calls.clear();emitter._do_radar();warm=emitter._build_payload()['radar']
    assert not warm['legend']['remapped']
    assert next(f for f in warm['frames'] if f['id']==warm['latest'])==frame
    assert not any('mrms::' in c[2] for c in hybrid.calls)


def test_superseded_notice_survives_replacement_pass_once(make_emitter,hybrid,tmp_path):
    emitter=make_emitter();once=[]
    def change(req,timeout):
        if 'mrms::' in req.full_url and not once:
            once.append(True);(tmp_path/'radar_intent').write_text('42')
    hybrid.failure=change;emitter._do_radar()
    assert emitter._radar_refresh['state']=='superseded' and not emitter._radar_negative
    hybrid.failure=None;emitter._do_radar()
    assert emitter._radar_refresh['state']=='idle'
    first=emitter._build_payload()['radar'];second=emitter._build_payload()['radar']
    assert first['refresh']['state']=='idle' and first['intent']['seq']==42  # current acknowledgement wins
    assert second['refresh']['state']=='idle' and second['intent']['seq']==42


@pytest.mark.parametrize('address',['127.0.0.1','::1','::ffff:127.0.0.1','198.51.100.2'])
def test_loopback_sequence_intent_and_duplicate(monkeypatch,tmp_path,address):
    from tests.test_freshness_health import _load_serve
    module=_load_serve(monkeypatch,tmp_path,{})
    monkeypatch.setattr(module.http.server.SimpleHTTPRequestHandler,'do_GET',lambda h:None)
    handler=object.__new__(module.Handler);handler.client_address=(address,1)
    handler.path='/wx.json?radarZoom=7&radarSource=site&radarCenter=station&radarSeq=41'
    handler.do_GET();marker=tmp_path/'radar_intent'
    assert marker.exists()==(address in module.LOOPBACK)
    if marker.exists():
        assert json.loads(marker.read_text())==dict(seq=41,zoom=7,source='site',center='station')
        assert (tmp_path/'radar_zoom').read_text().strip()=='7'
        assert (tmp_path/'radar_source').read_text().strip()=='site'
        handler.path='/wx.json?radarSeq=42&radarSeq=43';handler.do_GET()
        assert json.loads(marker.read_text())==dict(seq=41,zoom=7,source='site',center='station')


def test_all_dark_sites_keep_contract_on_mosaic(make_emitter,hybrid,multisite):
    multisite.scans={s:[] for s in multisite.scans}
    emitter=make_emitter();emitter._do_radar();r=emitter._build_payload()['radar']
    assert r['sourceMode']=='mosaic' and r['sourcePref']=='site'
    assert all(not s['contributing'] and not s['primary'] and s['reason']=='not reporting' for s in r['sites'])


def test_one_complete_site_keeps_full_history(make_emitter,hybrid,multisite,monkeypatch):
    monkeypatch.setattr(ae,'RADAR_REQUESTS_PER_MIN',10000)
    stamps=list(range(hybrid.latest-3600,hybrid.latest+1,120))
    multisite.scans.update(KNEA=stamps,KMID=stamps)
    def fail(site,url):
        if site=='KMID':raise OSError('layer down')
    multisite.failure=fail;hybrid.view();emitter=make_emitter();emitter._do_radar()
    assert len(emitter._radar_frames)==31


@pytest.mark.parametrize('cooldown',[False,True])
def test_back_to_back_budget_defers_before_progress(make_emitter,hybrid,monkeypatch,cooldown):
    hybrid.view(); emitter=make_emitter(); emitter._do_radar(); previous=emitter._radar_result
    hybrid.mono=10
    emitter._radar_request_times=[0]*ae.RADAR_REQUESTS_PER_MIN
    if cooldown: emitter._radar_cooldowns['iem-mrms-lcref']=75
    delays=[];monkeypatch.setattr(emitter,'_schedule_retry',lambda key,cb,delay:delays.append(delay))
    phases=[];original=emitter._radar_publish_refresh
    def publish(ctx,**kw):phases.append(kw['state']) if 'state' in kw else None;original(ctx,**kw)
    monkeypatch.setattr(emitter,'_radar_publish_refresh',publish)
    before=len(hybrid.calls);emitter._do_radar()
    assert emitter._radar_result is previous and emitter._radar_refresh['state']=='idle'
    assert phases==['newest','idle'] and len(hybrid.calls)==before
    assert emitter._build_payload()['radar']['geometryOnly']
    assert delays==[65 if cooldown else 50]
    assert emitter._radar_refresh['frameTotal']==1
    assert emitter._radar_refresh['frameIndex']==0


@pytest.mark.parametrize('previous,external',[(True,False),(False,False),(True,True)])
def test_mid_newest_budget_or_external_failure(make_emitter,hybrid,monkeypatch,previous,external):
    emitter=make_emitter()
    if previous:emitter._do_radar()
    retained=emitter._radar_result;hybrid.latest+=120;hybrid.now+=120
    delays=[];monkeypatch.setattr(emitter,'_schedule_retry',lambda key,cb,delay:delays.append(delay))
    original=emitter._radar_request
    def request(source,url,*a,**kw):
        if 'mrms::' in url:
            if external:raise OSError('HTTP unavailable')
            emitter._radar_request_times=[hybrid.mono]*ae.RADAR_REQUESTS_PER_MIN
            raise ae._RadarBudget('mid newest tiles')
        return original(source,url,*a,**kw)
    monkeypatch.setattr(emitter,'_radar_request',request);emitter._do_radar()
    assert emitter._radar_result is retained
    assert emitter._radar_refresh['state']==('idle' if previous and not external else 'failed')
    assert delays[-1]==(120 if external else 60)
    if previous:assert emitter._radar_refresh['frameTotal']==len(retained.frames)


def test_history_reserves_next_zoom_newest(make_emitter,hybrid,tmp_path):
    hybrid.view();emitter=make_emitter();emitter._do_radar()
    count=len(emitter._radar_request_times)
    assert count<=ae.RADAR_REQUESTS_PER_MIN-ae.RADAR_HISTORY_RESERVE
    before=emitter._radar_result
    (tmp_path/'radar_zoom').write_text('9');emitter._do_radar()
    assert emitter._radar_result.zoom==9 and emitter._radar_result is not before
    assert emitter._radar_refresh['state']=='idle'
    assert count<len(emitter._radar_request_times)<=ae.RADAR_REQUESTS_PER_MIN


@pytest.mark.parametrize('bad',['size','json','counts','truncated','revision','pixels'])
def test_corrupt_warm_cache_rebuilt(make_emitter,hybrid,bad):
    from PIL.PngImagePlugin import PngInfo
    emitter=make_emitter();emitter._do_radar()
    path=Path(ae.RADAR_DIR)/(emitter._radar_latest+'.png')
    with Image.open(path) as im:meta=json.loads(im.info['radarRemap']);image=im.copy()
    if bad=='size':image=Image.new('RGBA',(1,1))
    if bad=='pixels':image.putpixel((0,0),(19,37,53,255))
    if bad=='counts':meta['unmatchedPixels']=meta['opaquePixels']+1
    if bad=='revision':meta['revision']='old'
    info=PngInfo();info.add_text('radarRemap','broken' if bad=='json' else json.dumps(meta));image.save(path,pnginfo=info)
    if bad=='truncated':path.write_bytes(path.read_bytes()[:80])
    hybrid.calls.clear();emitter._do_radar()
    assert not any('mrms::' in c[2] for c in hybrid.calls)  # rebuild from native LRU
    with Image.open(path) as im:im.load();assert im.size==(956,490)
    assert emitter._radar_refresh['state']=='idle'


def test_primary_recovery_can_move_timestamp_back(make_emitter,hybrid,multisite):
    multisite.scans['KNEA']=[];emitter=make_emitter();emitter._do_radar()
    assert emitter._radar_result.site_id=='KMID'
    previous=emitter._radar_result
    multisite.scans['KNEA']=[previous.ts_frame-60]
    emitter._do_radar()
    assert emitter._radar_result.site_id=='KNEA' and emitter._radar_result.ts_frame==previous.ts_frame-60


def test_atomic_intent_worker_while_durable_writer_paused(make_emitter,hybrid,tmp_path,monkeypatch):
    import threading
    from tests.test_freshness_health import _load_serve
    module=_load_serve(monkeypatch,tmp_path,{})
    entered,release=threading.Event(),threading.Event()
    original=module._write_radar_zoom
    def pause(values):
        entered.set();assert release.wait(10);original(values)
    monkeypatch.setattr(module,'_write_radar_zoom',pause)
    params=dict(radarSeq=['42'],radarZoom=['9'],radarSource=['mosaic'],radarCenter=['47.8,-122.3'])
    worker=threading.Thread(target=module._write_radar_intent,args=(params,));worker.start()
    try:
        assert entered.wait(10)
        emitter=make_emitter();emitter._do_radar()
        assert emitter._radar_result.intent==dict(seq=42,zoom=9,source='mosaic',center=dict(lat=47.8,lon=-122.3))
        assert emitter._radar_result.zoom==9 and emitter._radar_result.center==dict(lat=47.8,lon=-122.3)
    finally:release.set();worker.join(10)
    assert not worker.is_alive()
    before=(tmp_path/'radar_intent').read_bytes()
    for seq in ('41','42'):
        module._write_radar_intent(dict(params,radarSeq=[seq],radarZoom=['5']))
        assert (tmp_path/'radar_intent').read_bytes()==before


def test_real_worker_supersede_at_network_barrier(make_emitter,hybrid,tmp_path):
    import threading
    emitter=make_emitter();emitter._do_radar();old=emitter._radar_result
    hybrid.latest+=120;hybrid.now+=120
    entered,release=threading.Event(),threading.Event()
    def fetch(req,timeout):
        if 'mrms::' in req.full_url:entered.set();assert release.wait(10)
    hybrid.failure=fetch
    worker=threading.Thread(target=emitter._do_radar);worker.start()
    try:
        assert entered.wait(10)
        (tmp_path/'radar_intent').write_text(json.dumps(dict(seq=42,zoom=9,source='mosaic',center='station')))
        assert emitter._build_payload()['radar']['geometryOnly']
        assert emitter._radar_result is old
    finally:release.set();worker.join(10)
    assert not worker.is_alive() and emitter._radar_restart and emitter._radar_result is old
    assert not emitter._radar_negative and not list(Path(ae.RADAR_DIR).rglob('*.tmp.*'))


@pytest.mark.skipif(os.environ.get('RADAR_NET_TEST')!='1',reason='opt in with RADAR_NET_TEST=1')
def test_live_seattle_two_pass(make_emitter,tmp_path,monkeypatch):
    monkeypatch.setattr(ae,'RADAR_DIR',str(tmp_path/'radar'))
    (tmp_path/'radar_viewed').write_text(str(ae.time.time()))
    emitter=make_emitter(config=make_config(Station={'Latitude':'47.61','Longitude':'-122.33'}))
    delays=[];monkeypatch.setattr(emitter,'_schedule_retry',lambda key,cb,delay:delays.append(delay))
    monkeypatch.setattr(ae.Logger,'warning',print)
    for n in range(2):
        start=ae.time.monotonic();emitter._do_radar();r=emitter._build_payload()['radar']
        print('Seattle pass',n,json.dumps(dict(state=r['refresh']['state'],frames=r['completeFrameCount'],total=r['frameCount'],age=r['ageSec'],requests=len(emitter._radar_request_times),elapsed=ae.time.monotonic()-start,retries=delays)))
        assert r['available'] and r['refresh']['state']=='idle'


def test_budget_retry_exact_subsecond_headroom(make_emitter,hybrid,monkeypatch):
    emitter=make_emitter();emitter._do_radar()
    needed=len(ae._radar_viewport(47.61,-122.33,8,956,490)[0])+2
    emitter._radar_request_times=[-59.5]*needed+[0]*(ae.RADAR_REQUESTS_PER_MIN-needed)
    delays=[];monkeypatch.setattr(emitter,'_schedule_retry',lambda key,cb,delay:delays.append(delay))
    emitter._do_radar();assert delays==[.5] and emitter._radar_refresh['state']=='idle'


def test_pan_cap_cannot_replace_station_timeline(make_emitter,hybrid,multisite,monkeypatch,tmp_path):
    sites={f'K{i:03}':(47.61+i*.01,-122.33,str(i)) for i in range(9)}
    monkeypatch.setattr(ae,'_NEXRAD_SITES',sites)
    multisite.scans={site:[hybrid.latest] for site in sites}
    multisite.colors={site:(12,145,16,255) for site in sites}
    emitter=make_emitter();emitter._do_radar();assert emitter._radar_result.site_id=='K000'
    hybrid.mono+=60;multisite.calls.clear();(tmp_path/'radar_center').write_text('48,-122.33')
    emitter._do_radar();r=emitter._build_payload()['radar']
    assert r['sourceMode']=='site' and r['siteId']=='K000'
    assert 'K000' not in {s['id'] for s in r['sites']}
    assert {c[1] for c in multisite.calls if c[0]=='list'}=={'K005','K006','K007','K008'}
    # The four station-timeline listings remain valid across the pan.
    assert not any(c[:2]==('tile','K000') for c in multisite.calls)


def test_mixed_site_failure_and_budget_have_distinct_reasons(make_emitter,hybrid,multisite):
    multisite.scans['KFAR']=[hybrid.latest]
    def failure(site,url):
        if site=='KMID':raise OSError('HTTP tile failed')
        if site=='KFAR':raise ae._RadarBudget('deferred layer')
    multisite.failure=failure;emitter=make_emitter();emitter._do_radar()
    reasons={s['id']:s['reason'] for s in emitter._radar_result.sites}
    assert reasons=={'KNEA':None,'KMID':'scan unavailable','KFAR':'deferred'}
    assert emitter._radar_refresh['state']=='idle'
