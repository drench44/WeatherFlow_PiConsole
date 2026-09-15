"""Hostile origin scenarios: real HTTPS and health HTTP, loopback only."""
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from lib import almanac_emit as ae, radar_http as http
from lib.radar_fetch import HostHealth, CircuitOpen
from tests.test_radar_keepalive import origin  # noqa: F401
from tests.test_freshness_health import serve_at, _get  # noqa: F401


def batch(emitter, origin, count=10, stamp=1, seconds=9, ctx=None):
    ctx = ctx or dict(zoom=8, tiles=[(i, 1, 0, 0) for i in range(count)], tile_workers=6)
    deadline = time.monotonic()+seconds
    emitter._radar_session.begin_pass(deadline)
    result = list(emitter._radar_tile_batch('iem-mrms-lcref', stamp, ctx, deadline,
                  lambda x, y: origin.url+f'/tile/{stamp}/{x}', None))
    return result, ctx


@pytest.fixture
def engine(make_emitter, origin):
    e = make_emitter()
    e._radar_session = http.RadarSession()
    yield e
    e._radar_session.close()


@pytest.mark.parametrize('behavior', ['hang', 'slow'])
def test_three_bad_tiles_hedged_on_fresh_connections(engine, origin, behavior):
    origin.behavior = lambda path, n: behavior if n == 1 and path.rsplit('/', 1)[-1] in ('0', '1', '2') else 'normal'
    start = time.monotonic()
    result, ctx = batch(engine, origin)
    elapsed = time.monotonic()-start
    h = engine._radar_health.snapshot()
    assert len(result) == 10 and 2 <= elapsed < 3.5
    assert h['hedges'] == h['discardedHedges'] == 3
    assert h['retries'] == 0  # winning hedges are not failure retries
    assert h['successRate60s'] == 10/13
    assert len(engine._radar_request_times) == len(origin.requests) == 13
    for x in range(3):
        ids = [ident for ident, _, path in origin.requests if path == f'/tile/1/{x}']
        assert len(ids) == len(set(ids)) == 2
    assert not engine._radar_session._busy
    print(f'{behavior}: 10/10 tiles; 3 hedges; elapsed={elapsed:.3f}s')


def test_fast_failure_retries_without_waiting_or_third_attempt(engine, origin):
    origin.behavior = lambda path, n: 'fail' if n == 1 else 'normal'
    result, _ = batch(engine, origin, count=2)
    h = engine._radar_health.snapshot()
    assert len(result) == 2 and h['hedges'] == 0 and h['retries'] == 2
    assert len(origin.requests) == len(engine._radar_request_times) == 4


def test_response_byte_suppresses_hedge(engine, origin):
    origin.behavior = lambda path, n: 'body'
    result, _ = batch(engine, origin, count=2)
    assert len(result) == 2 and engine._radar_health.hedges == 0
    assert len(origin.requests) == 2


def test_partial_and_next_pass_fetch_only_missing(engine, origin):
    origin.behavior = lambda path, n: 'fail' if path.endswith('/0') else 'normal'
    result, ctx = batch(engine, origin)
    assert len(result) == 9 and ctx['missing_tiles']
    before = len(origin.requests)
    origin.behavior = None
    result, _ = batch(engine, origin)
    assert len(result) == 10 and len(origin.requests)-before == 1


def test_deadline_and_all_busy_primary_slots(engine, origin):
    origin.behavior = lambda path, n: 'hang'
    start = time.monotonic()
    with pytest.raises((ae._RadarBudget, CircuitOpen)):
        batch(engine, origin, count=12, seconds=2.3)
    assert time.monotonic()-start < 2.8
    # Six primaries cannot prevent the first three fresh rescue leases.
    assert len(origin.requests) == 9
    assert not engine._radar_session._busy
    assert engine._radar_health.hedges == 6  # three leased and three bounded pool waiters
    assert engine._radar_health.retries == 0  # losing hedges are not retries either


def test_hedge_cap_and_rate_gate(engine, origin, monkeypatch):
    monkeypatch.setattr(ae, 'RADAR_REQUESTS_PER_MIN', 7)
    origin.behavior = lambda path, n: 'hang' if n == 1 else 'normal'
    with pytest.raises(ae._RadarBudget):
        batch(engine, origin, count=6, seconds=2.5)
    assert len(engine._radar_request_times) <= 7
    assert len(origin.requests) <= 7
    assert engine._radar_health.hedges <= 3


def test_host_breaker_open_half_close_and_failed_probe(monkeypatch):
    health = HostHealth()
    now = [1.]
    monkeypatch.setattr('lib.radar_fetch.time.monotonic', lambda: now[0])
    url = 'https://host.invalid/meta'
    for i in range(6):
        health.admit('source', url, metadata=True)
        health.record('source', url, i < 2, error=None if i < 2 else 'hang')
    assert health.snapshot()['breaker'] == 'open'
    with pytest.raises(CircuitOpen): health.probes('source')
    now[0] += 30
    assert health.probes('source') == [(url, True)]
    assert health.admit('source', url, metadata=True)
    assert health.snapshot()['breaker'] == 'half'
    with pytest.raises(CircuitOpen): health.admit('source', url, metadata=True)
    health.record('source', url, False, 'probe failed', probe=True)
    assert health.snapshot()['breaker'] == 'open'
    now[0] += 30
    assert health.admit('source', url, metadata=True)
    health.record('source', url, True, probe=True)
    assert health.snapshot()['breaker'] == 'closed'
    now[0] += 60
    assert health.snapshot()['successRate60s'] is None


def test_health_endpoint(engine, serve_at):
    engine._radar_health.last_success = time.time()
    payload = engine._build_payload()
    _, url = serve_at(payload)
    _, response = _get(url+'/health')
    h = response['radar']
    assert h == payload['radar']['health']
    assert set(('lastSuccessTs', 'successRate60s', 'hedges', 'retries', 'breaker', 'lastError')) <= h.keys()


from tests.test_radar_hybrid import hybrid  # noqa: E402,F401


def test_open_host_falls_back_in_same_pass_and_one_probe_recovers(make_emitter, hybrid, monkeypatch):
    e = make_emitter()
    source, url = 'iem-mrms-lcref', ae.RADAR_IEM_METADATA_URL
    for _ in range(6):
        e._radar_health.admit(source, url, metadata=True)
        e._radar_health.record(source, url, False, 'hang')
    e._do_radar()
    assert e._radar_result.source_id == 'rainviewer'
    assert all(c[0] == 'rainviewer' for c in hybrid.calls)
    assert e._radar_health.snapshot()['breaker'] == 'open'
    hybrid.mono += 30
    hybrid.calls.clear()
    # Probe observes the actual half state, and cannot get a second probe lease.
    observed = []
    def observe(req, _):
        if req.full_url == url:
            observed.append(e._radar_health.snapshot()['breaker'])
    hybrid.failure = observe
    e._do_radar(intent_triggered=False)
    assert e._radar_result.source_id == source
    assert observed == ['half']  # adapter consumes the probe bytes without a duplicate GET
    assert e._radar_health.snapshot()['breaker'] == 'closed'


def test_partial_publication_yields_before_history_and_repairs(make_emitter, hybrid, monkeypatch):
    import urllib.error
    e = make_emitter()
    original = e._radar_request
    failed = []
    def request(source, url, *args, **kwargs):
        if 'mrms::' in url:
            if not failed:
                failed.append(url)
            if url == failed[0]:
                raise urllib.error.HTTPError(url, 503, 'one missing tile', {}, None)
        return original(source, url, *args, **kwargs)
    monkeypatch.setattr(e, '_radar_request', request)
    hybrid.view()
    e._do_radar()
    snap = e._radar_result
    newest = next(f for f in snap.frames if f['ts'] == snap.ts_frame)
    assert snap.ts_frame == hybrid.latest and not newest['complete']
    assert e._radar_refresh['state'] == 'failed'
    assert not any('/7/' in c[2] or '/9/' in c[2] for c in hybrid.calls)
    monkeypatch.setattr(e, '_radar_request', original)
    # Stop background demand so the only new wire tile is the previous hole.
    monkeypatch.setattr(e, '_radar_is_viewed', lambda: False)
    hybrid.calls.clear()
    e._do_radar()
    newest = next(f for f in e._radar_frames if f['ts'] == snap.ts_frame)
    assert newest['complete'] and e._radar_refresh['state'] == 'idle'
    assert [c[2] for c in hybrid.calls if 'mrms::' in c[2]] == failed


def test_advertised_newest_has_no_five_minute_holdback(make_emitter, hybrid):
    hybrid.now = hybrid.latest+20
    e = make_emitter()
    e._do_radar()
    assert e._radar_result.ts_frame == hybrid.latest
    assert e._build_payload()['radar']['ageSec'] == 20
