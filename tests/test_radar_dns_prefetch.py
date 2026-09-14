"""DNS has a session lifetime; idle adjacent zooms only warm native tiles."""
import io
import json
import socket
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from lib import almanac_emit as ae, radar_http as http
from tests.test_radar_hybrid import hybrid, png  # noqa: F401


@pytest.fixture
def dns(monkeypatch):
    lookup = Mock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443))])
    monkeypatch.setattr(http.socket, 'getaddrinfo', lookup)
    class Response(io.BytesIO):
        status = 200
        length = 0
    def connection(*args):
        conn = Mock(sock=None)
        conn.getresponse.side_effect = lambda: Response(b'tile')
        return conn
    monkeypatch.setattr(http, '_Connection', connection)
    session = http.RadarSession()
    yield session, lookup
    session.close()


def get(session, host='radar.example'):
    with session.open(urllib.request.Request(f'https://{host}/tile'), 5) as response:
        return response.read()


def test_dns_survives_expired_connections_across_ten_passes(dns):
    session, lookup = dns
    for _ in range(10):
        session.begin_pass()
        assert get(session) == b'tile'
        for conn in session._used:
            session._used[conn] -= session.IDLE_SEC + 1
    assert lookup.call_count == 1
    assert lookup.call_args.args == ('radar.example', 443, socket.AF_INET, socket.SOCK_STREAM)


def test_two_second_refresh_never_delays_cached_pass_and_updates_ttl(dns):
    session, lookup = dns
    get(session)
    key = ('radar.example', 443)
    old = session.addresses[key]
    entered, finished = threading.Event(), threading.Event()
    replacement = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.2', 443))]
    def resolve(*args):
        entered.set()
        time.sleep(2)
        finished.set()
        return replacement
    lookup.side_effect = resolve
    session._resolved_at[key] -= session.DNS_TTL_SEC + 1
    start = time.perf_counter()
    for _ in range(8):
        session.begin_pass()
        get(session)
    assert time.perf_counter() - start < .3
    assert entered.wait(1) and not finished.is_set()
    assert session.addresses[key] == old and lookup.call_count == 2
    event = session._resolving[key]
    assert event.wait(3)
    assert session.addresses[key] == replacement
    assert time.monotonic() - session._resolved_at[key] < 1
    get(session)
    assert lookup.call_count == 2


def test_refresh_failure_keeps_stale_and_retries_next_pass(dns):
    session, lookup = dns
    get(session)
    key = ('radar.example', 443)
    old, timestamp = session.addresses[key], session._resolved_at[key] - session.DNS_TTL_SEC - 1
    session._resolved_at[key] = timestamp
    lookup.side_effect = socket.gaierror('resolver unavailable')
    get(session)
    # Synchronize whether the fast failing thread has already removed its event.
    with session._condition:
        event = session._resolving.get(key)
    if event: assert event.wait(2)
    for _ in range(5): get(session)
    assert lookup.call_count == 2
    assert session.addresses[key] == old and session._resolved_at[key] == timestamp
    session.begin_pass()
    lookup.side_effect = None
    get(session)
    with session._condition:
        event = session._resolving.get(key)
    if event: assert event.wait(2)
    assert lookup.call_count == 3 and session._resolved_at[key] > timestamp


@pytest.mark.parametrize('failure', [False, True])
def test_cold_singleflight_does_not_hold_pool_lock_or_block_other_host(dns, failure):
    session, lookup = dns
    entered, release = threading.Event(), threading.Event()
    addresses = lookup.return_value
    def resolve(host, *args):
        if host == 'radar.example':
            entered.set()
            assert release.wait(3)
            if failure: raise socket.gaierror('offline')
        return addresses
    lookup.side_effect = resolve
    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = [pool.submit(get, session) for _ in range(6)]
        try:
            assert entered.wait(1)
            assert pool.submit(get, session, 'other.example').result(timeout=1) == b'tile'
            assert lookup.call_count == 2
        finally:
            release.set()
        for future in futures:
            if failure:
                with pytest.raises(socket.gaierror): future.result(2)
            else: assert future.result(2) == b'tile'
    assert lookup.call_count == 2
    if failure:
        with pytest.raises(socket.gaierror): get(session)
        assert lookup.call_count == 2
        session.begin_pass()
        lookup.side_effect = None
        get(session)
        assert lookup.call_count == 3


def setup_prefetch(emitter, hybrid, monkeypatch):
    # A short completed history leaves idle budget; production history limits stay
    # untouched. This runs the real adapters, final idle hook and tile transport.
    monkeypatch.setattr(ae, 'RADAR_HISTORY_SEC', 120)
    monkeypatch.setattr(ae, '_NEXRAD_SITES', {})  # isolate own-source tiers
    hybrid.view()
    contexts = []
    original = emitter._radar_prefetch
    def capture(source, ctx):
        contexts.append((source, dict(ctx)))
        original(source, ctx)
    monkeypatch.setattr(emitter, '_radar_prefetch', capture)
    return contexts, original


@pytest.mark.parametrize('zoom', [7, 9])
def test_idle_prefetch_reused_by_adjacent_pass_without_tile_requests(make_emitter, hybrid, monkeypatch, tmp_path, zoom):
    emitter = make_emitter()
    contexts, prefetch = setup_prefetch(emitter, hybrid, monkeypatch)
    emitter._do_radar()
    snap = emitter._radar_result
    assert len(snap.frames) == 2 and sum(f['complete'] for f in snap.frames) == 2
    assert emitter._radar_refresh['state'] == 'idle'
    keys = [k for k in emitter._radar_tiles if k[3] == snap.ts_frame and k[4] == zoom]
    assert len(keys) == len(ae._radar_viewport(47.61, -122.33, zoom, 956, 490)[0])
    before = len(hybrid.calls)
    prefetch(*contexts[-1])
    assert len(hybrid.calls) == before and emitter._radar_result is snap
    (tmp_path/'radar_viewed').unlink()  # next pass only newest, no history traffic
    (tmp_path/'radar_zoom').write_text(str(zoom))
    hybrid.calls.clear()
    emitter._do_radar()
    assert emitter._radar_result.zoom == zoom
    assert not any('mrms::' in c[2] for c in hybrid.calls)


@pytest.mark.parametrize('blocked', ['unviewed', 'expired', 'busy', 'budget', 'cooldown', 'deadline'])
def test_prefetch_requires_fresh_view_idle_and_headroom(make_emitter, hybrid, monkeypatch, tmp_path, blocked):
    emitter = make_emitter()
    emitter._do_radar()  # unviewed; must fetch only current zoom
    assert {k[4] for k in emitter._radar_tiles} == {8}
    source = emitter._radar_result.source_id
    ctx = dict(viewed=True, zoom=8, center=emitter._radar_result.center,
               sources=[dict(available=True), dict(available=False)],
               refresh=dict(state='idle'), deadline=100, preference_stamp=emitter._radar_preference_stamp())
    hybrid.view()
    if blocked == 'unviewed': (tmp_path/'radar_viewed').unlink()
    if blocked == 'expired': (tmp_path/'radar_viewed').write_text(str(hybrid.now - ae.RADAR_VIEW_TTL))
    if blocked == 'busy': ctx['refresh']['state'] = 'history'
    if blocked == 'budget':
        emitter._radar_request_times = [0.] * (ae.RADAR_REQUESTS_PER_MIN-ae.RADAR_HISTORY_RESERVE-60+1)
    if blocked == 'cooldown': emitter._radar_cooldowns[source] = 10
    if blocked == 'deadline': ctx['deadline'] = 0
    hybrid.calls.clear()
    emitter._radar_prefetch(source, ctx)
    assert not hybrid.calls and {k[4] for k in emitter._radar_tiles} == {8}


def test_prefetch_exact_headroom_and_new_intent_cancels_round(make_emitter, hybrid, tmp_path, monkeypatch):
    emitter = make_emitter(); emitter._do_radar(); hybrid.view()
    source = emitter._radar_result.source_id
    ctx = dict(viewed=True, zoom=8, center=emitter._radar_result.center,
               sources=[dict(available=True), dict(available=False)],
        refresh=dict(state='idle'), deadline=100, preference_stamp=emitter._radar_preference_stamp())
    emitter._radar_request_times = [0.] * (ae.RADAR_REQUESTS_PER_MIN-ae.RADAR_HISTORY_RESERVE-60)
    snap, refresh = emitter._radar_result, dict(emitter._radar_refresh)
    def change(req, timeout):
        (tmp_path/'radar_intent').write_text(json.dumps(dict(seq=2,zoom=6,source='mosaic',center='station')))
    hybrid.failure = change
    hybrid.calls.clear()
    with pytest.raises(ae._RadarSuperseded): emitter._radar_prefetch(source, ctx)
    assert 1 <= len(hybrid.calls) <= 4
    assert all('/7/' in c[2] for c in hybrid.calls)
    assert emitter._radar_result is snap and emitter._radar_refresh == refresh
    assert any(k[4] == 7 for k in emitter._radar_tiles)
    assert emitter._radar_prefetched[(source, 7, snap.center['lat'], snap.center['lon'])] == ((None, snap.ts_frame),)


def test_rainviewer_prefetch_once_per_stamp_and_source_bounds(make_emitter, hybrid, monkeypatch, tmp_path):
    monkeypatch.setattr(ae, '_radar_iem_eligible', lambda *args: False)
    emitter = make_emitter()
    contexts, prefetch = setup_prefetch(emitter, hybrid, monkeypatch)
    (tmp_path/'radar_zoom').write_text(str(ae._RADAR_SOURCES['rainviewer']['max_zoom']))
    emitter._do_radar()
    stamp = emitter._radar_result.ts_frame
    limit = ae._RADAR_SOURCES['rainviewer']['max_zoom']
    assert {k[4] for k in emitter._radar_tiles} == {limit, limit-1}
    hybrid.calls.clear()
    emitter._do_radar()
    assert len(hybrid.calls) == 1  # manifest only, including no repeated prefetch
    hybrid.rv += 600; hybrid.now += 600; hybrid.view()
    emitter._do_radar()
    assert emitter._radar_prefetched[('rainviewer', limit-1, 47.61, -122.33)] == ((None, stamp+600),)
    assert any(k[3] == stamp+600 and k[4] == limit-1 for k in emitter._radar_tiles)


@pytest.mark.parametrize('workers', [6, 4])
def test_newest_history_concurrency_bound(make_emitter, monkeypatch, workers):
    emitter = make_emitter()
    barrier, release = threading.Barrier(workers+1), threading.Event()
    active = peak = 0
    lock = threading.Lock()
    def request(*args):
        nonlocal active, peak
        with lock:
            active += 1; peak = max(active, peak)
        if not release.is_set():
            barrier.wait(3); assert release.wait(3)
        with lock: active -= 1
        return png()
    monkeypatch.setattr(emitter, '_radar_request', request)
    ctx = dict(zoom=8, tiles=[(i, 1, 0, 0) for i in range(15)], tile_workers=workers)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: list(emitter._radar_tile_batch('iem-mrms-lcref', 1, ctx, 100, lambda x,y:str(x), None)))
        try:
            barrier.wait(3)
            assert peak == workers
        finally: release.set()
        assert len(future.result(5)) == 15
    assert peak == workers


def test_adapters_select_six_newest_then_four_history(make_emitter, hybrid, monkeypatch):
    emitter = make_emitter()
    monkeypatch.setattr(ae, 'RADAR_HISTORY_SEC', 120)
    monkeypatch.setattr(ae, '_NEXRAD_SITES', {})  # isolate own-source tiers
    hybrid.view()
    batches = []
    original = emitter._radar_tile_batch
    def record(source, stamp, ctx, *args):
        batches.append((ctx.get('prefetch', False), ctx['tile_workers']))
        yield from original(source, stamp, ctx, *args)
    monkeypatch.setattr(emitter, '_radar_tile_batch', record)
    emitter._do_radar()
    assert batches == [(False, 6), (False, 4), (True, 4), (True, 4)]


def test_prefetch_error_does_not_change_published_frame_or_retry(make_emitter, hybrid, monkeypatch):
    emitter = make_emitter()
    contexts, prefetch = setup_prefetch(emitter, hybrid, monkeypatch)
    def fail_prefetch(source, ctx):
        before = emitter._radar_result, dict(emitter._radar_refresh), dict(emitter._retries)
        hybrid.failure = lambda *args: (_ for _ in ()).throw(ConnectionResetError('optional tile'))
        prefetch(source, ctx)
        assert (emitter._radar_result, emitter._radar_refresh, emitter._retries) == before
    monkeypatch.setattr(emitter, '_radar_prefetch', fail_prefetch)
    emitter._do_radar()
    assert emitter._radar_result.source_id == 'iem-mrms-lcref'
    assert emitter._radar_refresh['state'] == 'idle' and not emitter._radar_negative


def test_prefetch_view_expiry_stops_submissions(make_emitter, hybrid, monkeypatch, tmp_path):
    emitter = make_emitter()
    contexts, prefetch = setup_prefetch(emitter, hybrid, monkeypatch)
    def expire(source, ctx):
        hybrid.calls.clear()
        hybrid.failure = lambda *args: (tmp_path/'radar_viewed').unlink(missing_ok=True)
        prefetch(source, ctx)
        assert 1 <= len(hybrid.calls) <= 4
        assert all('/7/' in c[2] for c in hybrid.calls)
    monkeypatch.setattr(emitter, '_radar_prefetch', expire)
    emitter._do_radar()
    assert emitter._radar_refresh['state'] == 'idle'


def test_prefetch_requests_and_retries_preserve_atomic_reserve(make_emitter, hybrid):
    emitter = make_emitter(); emitter._radar_session = http.RadarSession()
    source = 'iem-mrms-lcref'
    emitter._radar_request_times = [0.] * (ae.RADAR_REQUESTS_PER_MIN-ae.RADAR_HISTORY_RESERVE-2)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(emitter._radar_request, source,
            f'https://example/mrms::lcref-{i}', 10, reserve=ae.RADAR_HISTORY_RESERVE) for i in range(6)]
    assert sum(f.exception() is None for f in futures) == 2
    with pytest.raises(ae._RadarBudget):
        emitter._radar_transport_retry(source, 10, ae.RADAR_HISTORY_RESERVE)
    assert len(emitter._radar_request_times) == ae.RADAR_REQUESTS_PER_MIN-ae.RADAR_HISTORY_RESERVE
    assert emitter._radar_transport_retries == 0
