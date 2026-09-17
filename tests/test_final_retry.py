"""Retry admission and slow boot timing, with deterministic offline providers."""
import socket

import pytest

from lib import almanac_emit as ae
from tests.test_radar_hybrid import hybrid  # noqa: F401
from tests.test_radar_local_backoff import armed_delay


def test_discovery_cannot_overtake_local_retry_floor(make_emitter, hybrid):
    e = make_emitter()
    e._running = True
    e._radar_local_failure_streak = 6
    e._radar_discovery.due = ae.time.time()+1
    e._radar_budget_retry('iem-mrms-lcref', 1)
    e._radar_arm_discovery()
    assert armed_delay(e) == ae.RADAR_LOCAL_RETRY_MAX_SEC
    assert e._radar_discovery.due >= e._radar_next_retry


def test_user_intent_gets_an_immediate_attempt_during_backoff(make_emitter, hybrid, monkeypatch):
    e = make_emitter()
    e._running = True
    e._radar_local_failure_streak = 6
    e._radar_budget_retry('iem-mrms-lcref', 1)
    spawned = []
    monkeypatch.setattr(e, '_spawn', lambda lane, worker: spawned.append((lane, worker)))
    e._radar_zoom_stamp = None
    e._check_radar_zoom()
    assert len(spawned) == 1 and spawned[0][0] == 'radar'
    calls = []
    monkeypatch.setattr(e, '_do_radar', lambda **kw: calls.append(kw))
    spawned[0][1]()
    assert calls[0]['intent_triggered'] is True


def test_inventory_wait_does_not_consume_provider_deadline(make_emitter, hybrid, monkeypatch):
    e = make_emitter()
    e._radar_start_inventory()
    assert e._radar_cache_ready.wait(5)
    class SlowInventory:
        def wait(self, timeout):
            hybrid.mono += 26  # legal 30-second wait exceeds the 25-second pass
            return True
        def is_set(self):
            return True
    e._radar_cache_ready = SlowInventory()
    e._do_radar(intent_triggered=False)
    assert e._radar_result.ts_frame is not None, e._radar_pass


@pytest.mark.parametrize('code', [socket.EAI_NONAME, socket.EAI_FAIL, socket.EAI_AGAIN, -2, -3])
def test_every_dns_failure_is_local_whatever_the_errno(code):
    # A Pi with its network down reports EAI_NONAME (-2 on Linux) as readily as
    # EAI_AGAIN. Classifying any of them as a provider failure flapped the
    # fallback chain and tripled the pass log on Linux (test_radar_v62 Site).
    from lib import radar_http as http
    error = socket.gaierror(code, 'name resolution failed')
    assert http.failure_class(error) == 'local' and http.local_backoff_failure(error)


@pytest.mark.parametrize('scope', ['listing', 'tiles'])
def test_dns_failures_back_off_and_never_advance_fallback(make_emitter, hybrid, scope):
    e = make_emitter()
    e._running = True
    def fail(req, timeout):
        if 'iastate.edu' in req.full_url and (scope == 'listing' or '/mrms::lcref-' in req.full_url):
            raise socket.gaierror(socket.EAI_NONAME, 'Name or service not known')
    hybrid.failure = fail
    delays = []
    for _ in range(5):
        e._do_radar(intent_triggered=False)
        delays.append(armed_delay(e))
        hybrid.mono += 120
        hybrid.latest += 120
    assert delays == [2, 4, 8, 16, 32]
    assert e._radar_local_failure_streak == 5
    assert 'iem-mrms-lcref' not in e._radar_transport_failures   # no fallback from a resolver failure
    assert e._radar_result.source_id != 'rainviewer'
    hybrid.failure = None
    e._do_radar(intent_triggered=False)
    assert e._radar_result.ts_frame is not None and e._radar_local_failure_streak == 0


def test_resolver_timeout_is_local_and_backs_off(make_emitter, hybrid, monkeypatch):
    from lib import radar_http as http
    monkeypatch.setattr(http, '_shared_resolve', lambda *args: None)
    session = http.RadarSession()
    try:
        with pytest.raises(http.LocalTransportError) as raised:
            session._addresses_for(('unresolved.invalid', 443), 0)
        error = raised.value
        assert http.failure_class(error) == 'local'
        e = make_emitter()
        e._running = True
        delays = []
        for _ in range(4):
            e._radar_failed_pass('iem-mrms-lcref', error, {})
            delays.append(armed_delay(e))
        assert delays == [2, 4, 8, 16] and e._radar_local_failure_streak == 4
    finally:
        session.close()
