"""A history pass refused by the request gate must wait for real headroom.

2026-09-16: after a three-step zoom-out the gate refused a history frame
before its cost was priced, the pass yielded with needed=0, and the retry
fired 2 s later into the same full window: one metadata request per pass for
as long as the window stayed full, with the caption stuck on "Retrying view ·
work budget · next attempt now"."""
from lib import almanac_emit as ae
from tests.test_radar_hybrid import hybrid  # noqa: F401


def test_history_deferred_by_the_gate_waits_for_a_frame_of_headroom(make_emitter, hybrid):
    e = make_emitter()
    e._running = True
    hybrid.view()
    e._do_radar(intent_triggered=True)                 # newest frame lands
    assert e._radar_result.available and e._radar_result.frames
    # Fill the 60 s window just under the cap: the next tile fetch in history is refused.
    now = ae.time.monotonic()
    e._radar_request_times = [now - 1.0] * (ae.RADAR_REQUESTS_PER_MIN - 1)
    e._radar_clear_retry()
    hybrid.latest += 120; hybrid.mono += 0.5
    e._do_radar(intent_triggered=True)
    err = e._radar_pass['error'] or ''
    assert e._radar_pass['outcome'] == 'deferred', (e._radar_pass['outcome'], err)
    assert 'needed=0' not in err, err
    assert 'radar' in e._retries
    delay = e._radar_next_retry - ae.time.time()
    assert delay > 2.5, (delay, err)                  # real headroom, not the 2 s fallback
