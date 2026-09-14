"""Radar v3.1: map-first publication, cancellable tile reuse and persistent pools."""
import io
import json
import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from lib import almanac_emit as ae, radar_http as http
from tests.test_radar_hybrid import hybrid, png  # noqa: F401
from tests.test_emitter_lifecycle import FakeClock
from tests.fixtures.config import make_config


def test_geometry_precedes_every_request(make_emitter, hybrid, tmp_path):
    hybrid.view()
    emitter = make_emitter()
    emitter._do_radar()
    previous = emitter._radar_result
    intent = dict(seq=42, zoom=7, source='mosaic', center=dict(lat=47.7, lon=-122.2))
    (tmp_path/'radar_intent').write_text(json.dumps(intent))
    seen = []
    def inspect(req, timeout):
        if not seen:
            r = emitter._build_payload()['radar']
            assert r['geometryOnly'] and r['frames'] == [] and r['latest'] is None
            assert r['intent'] == intent and r['refresh'] == dict(state='newest', frameIndex=0, frameTotal=1, forSeq=42)
            assert r['zoom'] == 7 and r['center'] == intent['center']
            assert r['bounds'] == ae._radar_viewport(47.7, -122.2, 7, 956, 490)[2]
            assert r['marker'] != dict(x=.5,y=.5) and r['rings'] and r['scaleBar']
            assert (Path(ae.RADAR_DIR)/'basemap'/(r['basemap']['hash']+'.svg')).is_file()
            assert emitter._radar_result is previous
            seen.append(r)
    hybrid.failure = inspect
    emitter._do_radar()
    assert seen and emitter._radar_result.zoom == 7
    assert not emitter._build_payload()['radar']['geometryOnly']


def test_immediate_emit_coalesces_and_stays_on_clock_thread(make_emitter, monkeypatch):
    emitter = make_emitter()
    clock = FakeClock()
    monkeypatch.setattr(ae, 'Clock', clock)
    emitter._running = True
    main = threading.get_ident()
    built = []
    def build():
        assert threading.get_ident() == main
        built.append(emitter._radar_refresh['frameIndex'])
        return {}
    monkeypatch.setattr(emitter, '_build_payload', build)
    monkeypatch.setattr(emitter, '_write_atomic', lambda payload:None)
    ctx = dict(intent=dict(seq=1, zoom=8, source='mosaic', center='station'))
    def burst():
        for i in range(8): emitter._radar_publish_refresh(ctx, frameIndex=i)
    worker = threading.Thread(target=burst)
    worker.start(); worker.join(5)
    assert not worker.is_alive() and not built
    assert len(clock.events) == 1 and clock.events[0].timeout == 0
    assert clock.events[0].callback == emitter._emit
    clock.advance(0)
    assert built == [7] and not clock.events and not emitter._events
    burst(); assert len(clock.events) == 1
    emitter.stop(); assert not clock.events


def test_pan_reuses_native_tiles_without_requests(make_emitter, hybrid, tmp_path, monkeypatch):
    emitter = make_emitter(); emitter._do_radar()
    first = emitter._radar_latest
    cached = set(emitter._radar_tiles)
    assert len(cached) == 12
    hybrid.calls.clear()
    (tmp_path/'radar_center').write_text('47.611,-122.331')
    emitter._do_radar()
    assert emitter._radar_latest != first
    assert set(emitter._radar_tiles) == cached
    assert not any('mrms::' in call[2] for call in hybrid.calls)
    # Palette revisions change cropped PNGs, never immutable native tile bytes.
    monkeypatch.setattr(ae, 'REMAP_REVISION', 'test-next-palette')
    hybrid.calls.clear(); emitter._do_radar()
    assert not any('mrms::' in call[2] for call in hybrid.calls)


def tile_ctx(count=12):
    return dict(zoom=8, tiles=[(i, 1, i*256, 0) for i in range(count)])


def test_pool_supersession_drains_and_reuses_all_completed_tiles(make_emitter, monkeypatch):
    emitter = make_emitter()
    ctx = tile_ctx()
    ready = threading.Barrier(5)
    release = threading.Event()
    superseded = threading.Event()
    calls = []
    lock = threading.Lock()
    active = peak = 0
    def request(source, url, deadline):
        nonlocal active, peak
        with lock:
            active += 1; peak = max(peak, active); calls.append(url)
        ready.wait(5)
        assert release.wait(5)
        with lock: active -= 1
        return png()
    monkeypatch.setattr(emitter, '_radar_request', request)
    def checkpoint(ctx):
        if superseded.is_set(): raise ae._RadarSuperseded('new intent')
    monkeypatch.setattr(emitter, '_radar_checkpoint', checkpoint)
    errors = []
    def run():
        try: list(emitter._radar_tile_batch('iem-mrms-lcref', 100, ctx, time.monotonic()+10, lambda x,y:str(x), None))
        except ae._RadarSuperseded: errors.append('superseded')
    worker = threading.Thread(target=run); worker.start()
    try:
        ready.wait(5)
        superseded.set()
    finally: release.set(); worker.join(10)
    assert not worker.is_alive() and errors == ['superseded']
    assert peak == 4 and len(calls) == len(emitter._radar_tiles) == 4
    superseded.clear(); calls.clear()
    reused = dict(ctx, tiles=ctx['tiles'][:4])
    assert len(list(emitter._radar_tile_batch('iem-mrms-lcref',100,reused,time.monotonic()+5,lambda x,y:str(x),None))) == 4
    assert calls == []  # incomplete/superseded crop still has all four native tiles


def test_pool_rate_gate_is_atomic_and_lru_is_bounded(make_emitter, hybrid, monkeypatch):
    emitter = make_emitter(); emitter._radar_session = http.RadarSession()
    emitter._radar_request_times = [0.] * (ae.RADAR_REQUESTS_PER_MIN - 2)
    monkeypatch.setattr(ae, 'RADAR_TILE_CACHE_SIZE', 2)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(emitter._radar_request, 'iem-mrms-lcref', f'https://example/mrms::lcref-{i}', 10) for i in range(12)]
    assert sum(f.exception() is None for f in futures) == 2
    assert len(emitter._radar_request_times) == ae.RADAR_REQUESTS_PER_MIN
    assert all(f.exception() is None or isinstance(f.exception(),ae._RadarBudget) for f in futures)
    emitter._radar_request_times.clear()
    list(emitter._radar_tile_batch('iem-mrms-lcref',100,tile_ctx(8),10,lambda x,y:f'https://example/mrms::lcref-{x}',None))
    assert len(emitter._radar_tiles) == 2


def test_session_persists_across_passes_and_drops_on_failure(make_emitter, hybrid):
    emitter = make_emitter(); emitter._do_radar()
    session = emitter._radar_session
    emitter._do_radar(); assert emitter._radar_session is session
    def fail(req, timeout): raise OSError('connection broken')
    hybrid.failure = fail
    emitter._do_radar()
    assert session._closed and emitter._radar_session is None
    hybrid.failure = None
    emitter._do_radar()
    assert emitter._radar_session is not session and emitter._radar_available


def test_transport_six_leases_reuse_idle_expiry_and_error(monkeypatch):
    mono = [0.]
    monkeypatch.setattr(http.time, 'monotonic', lambda:mono[0])
    lookup = Mock(return_value=[(2,1,6,'',('192.0.2.1',443))])
    monkeypatch.setattr(http.socket,'getaddrinfo',lookup)
    class Response(io.BytesIO):
        status = 200
        length = 0
    conns = []
    def connection(*args):
        conn=Mock(sock=None)
        conn.getresponse.side_effect=lambda:Response(b'tile')
        conns.append(conn); return conn
    monkeypatch.setattr(http,'_Connection',connection)
    session=http.RadarSession(); req=urllib.request.Request('https://example/tile')
    leased=[session.open(req,10) for _ in range(6)]
    assert len(conns)==6 and lookup.call_count==1
    waiting=threading.Event(); acquired=threading.Event()
    def seventh():
        waiting.set()
        with session.open(req,10): acquired.set()
    worker=threading.Thread(target=seventh);worker.start();assert waiting.wait(5)
    assert not acquired.wait(.03)
    leased[0].close();assert acquired.wait(5);worker.join(5)
    for response in leased: response.close()
    session.begin_pass()
    with session.open(req,10): pass
    assert len(conns)==6 and lookup.call_count==1
    conns[0].request.side_effect=OSError('broken')
    with pytest.raises(OSError): session.open(req,10)
    conns[0].close.assert_called_once()
    assert conns[0] not in session.connections[('example',443)]
    mono[0]=61;session.begin_pass()
    assert not session.connections
    with session.open(req,10): pass
    assert len(conns)==7 and lookup.call_count==1
    session.close()


@pytest.mark.skipif(os.environ.get('RADAR_NET_TEST')!='1',reason='opt in with RADAR_NET_TEST=1')
def test_live_seattle_zoom_sequence(make_emitter, tmp_path, monkeypatch):
    """Real provider, map/newest/history phases; change intent while history begins."""
    monkeypatch.setattr(ae,'RADAR_DIR',str(tmp_path/'radar'))
    monkeypatch.setattr(ae.Logger,'warning',print)
    (tmp_path/'radar_viewed').write_text(str(time.time()))
    emitter=make_emitter(config=make_config(Station={'Latitude':'47.61','Longitude':'-122.33'}))
    original=emitter._radar_publish_refresh
    reports=[]
    for seq,z in enumerate((8,7,6),1):
        (tmp_path/'radar_intent').write_text(json.dumps(dict(seq=seq,zoom=z,source='mosaic',center='station')))
        start=time.perf_counter(); phases={}; requests=len(emitter._radar_request_times)
        def publish(ctx, **changes):
            original(ctx,**changes)
            elapsed=round((time.perf_counter()-start)*1000,1)
            if ctx.get('geometry_result') is not None: phases.setdefault('map_ms',elapsed)
            elif emitter._radar_result.zoom==z and emitter._radar_result.available:
                count=sum(f['complete'] for f in emitter._radar_result.frames)
                phases.setdefault('newest_ms',elapsed)
                if count>=2:
                    phases.setdefault('history_ms',elapsed)
                if z>6:
                    (tmp_path/'radar_intent').write_text(json.dumps(dict(seq=seq+1,zoom=z-1,source='mosaic',center='station')))
        monkeypatch.setattr(emitter,'_radar_publish_refresh',publish)
        emitter._do_radar()
        phases.update(zoom=z,elapsed_ms=round((time.perf_counter()-start)*1000,1),
                      requests=len(emitter._radar_request_times)-requests,tiles_cached=len(emitter._radar_tiles),
                      source=emitter._radar_result.source_id,refresh=emitter._radar_refresh['state'])
        print('Seattle v3.1',json.dumps(phases),flush=True);reports.append(phases)
        assert 'newest_ms' in phases and phases['map_ms']<phases['newest_ms']
        assert emitter._radar_result.source_id=='iem-mrms-lcref'
    assert 'history_ms' in reports[-1]
