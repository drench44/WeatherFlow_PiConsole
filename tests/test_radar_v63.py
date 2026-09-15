"""A retry belongs to a live scheduled callback, never to a past yield."""
from datetime import timezone
import socket

import pytest

from lib import almanac_emit as ae
from lib.radar_http import LocalTransportError
from tests.test_radar_v50 import scheduled  # noqa: F401
from tests.test_radar_hybrid import hybrid  # noqa: F401


def assert_no_retry(e):
    assert e._radar_next_retry is None
    assert e._radar_retry_reason is None
    assert 'radar' not in e._retries
    r = e._radar_payload(e._radar_result, ae.time.time(), timezone.utc, e._radar_refresh)
    for data in (e._radar_refresh, r, r['refresh']):
        assert 'nextRetry' not in data and 'retryReason' not in data


def arm(e, delay=30, reason='budget'):
    e._radar_budget_retry(e._radar_result.source_id, 1, min_delay=delay, reason=reason)
    assert e._radar_refresh['nextRetry'] == ae.time.time()+delay
    assert e._radar_refresh['retryReason'] == reason
    return e._retries['radar']


def test_retry_fires_before_pass_admission_even_when_lane_busy(scheduled):
    e, clock, _ = scheduled
    arm(e, delay=2)
    e._inflight.add('radar')
    clock.advance(2)
    assert_no_retry(e)
    assert e._radar_acquisition_pending  # work survives; the timer has fired


def test_scheduled_pass_consumes_retry_and_completes(scheduled, monkeypatch):
    e, _, _ = scheduled
    old = arm(e)
    adapter = e._radar_iem_frames
    def checked(ctx):
        assert_no_retry(e)  # discovery/periodic pass overtook the armed timer
        return adapter(ctx)
    monkeypatch.setattr(e, '_radar_iem_frames', checked)
    e._do_radar(intent_triggered=False)
    assert e._radar_pass['outcome'] == 'ok'
    assert old not in e._events
    assert_no_retry(e)


def test_completion_clears_retry_that_expires_during_intent_pass(scheduled, monkeypatch):
    e, clock, _ = scheduled
    old = arm(e)
    # Timer dispatch can lag behind the worker's completion/publication.
    monkeypatch.setattr(e, '_radar_prune', lambda previous: setattr(clock, 'now', 31))
    e._do_radar(intent_triggered=True)
    assert e._radar_pass['outcome'] == 'ok'
    assert old not in clock.events
    assert_no_retry(e)


def test_unchanged_cancels_inherited_future_retry(scheduled):
    e, clock, _ = scheduled
    old = arm(e)
    e._do_radar(intent_triggered=True, discovery=True)
    assert e._radar_pass['outcome'] == 'unchanged'
    assert old not in clock.events
    assert_no_retry(e)


def test_intent_pass_preserves_pending_scheduled_validation(scheduled):
    e, _, _ = scheduled
    old = arm(e, reason='deadline')
    due = e._radar_next_retry
    e._do_radar(intent_triggered=True)
    assert e._radar_pass['outcome'] == 'ok'
    assert e._retries['radar'] is old
    r = e._radar_payload(e._radar_result, ae.time.time(), timezone.utc, e._radar_refresh)
    assert r['refresh']['nextRetry'] == r['nextRetry'] == due
    assert r['refresh']['retryReason'] == r['retryReason'] == 'deadline'


def test_cancelled_callback_cannot_clear_replacement(scheduled):
    e, _, _ = scheduled
    old = arm(e)
    new = arm(e, delay=40, reason='local')
    old.callback(0)
    assert e._retries['radar'] is new
    assert e._radar_refresh['retryReason'] == 'local'
    e.stop()
    assert_no_retry(e)


def test_stopped_emitter_cannot_publish_unscheduled_retry(make_emitter):
    e = make_emitter()
    e._radar_budget_retry('iem-mrms-lcref', 1)
    assert_no_retry(e)


@pytest.mark.parametrize('offset', [-30, 0, .25, 30])
def test_payload_filters_expired_retry_without_mutating_snapshot(scheduled, offset):
    e, clock, _ = scheduled
    arm(e)
    refresh = dict(e._radar_refresh)
    clock.now = 30-offset
    r = e._radar_payload(e._radar_result, ae.time.time(), timezone.utc, refresh)
    for data in (r, r['refresh']):
        assert ('nextRetry' in data) == (offset > 0)
        assert ('retryReason' in data) == (offset > 0)
    assert refresh == e._radar_refresh


@pytest.mark.parametrize('error,reason', [
    (ConnectionResetError('provider reset'), 'provider'),
    (socket.gaierror('DNS unavailable'), 'local'),
    (TimeoutError('radar acquisition deadline'), 'deadline'),
    (LocalTransportError('local setup failed'), 'local'),
])
def test_failure_retry_carries_cause(scheduled, error, reason):
    e, _, _ = scheduled
    e._radar_failed_pass(e._radar_result.source_id, error, {})
    assert e._radar_refresh['retryReason'] == reason
