"""Attention tiers: promotion is immediate, demotion waits, unknown is not dry."""
from datetime import datetime
import pytz
from lib.radar_attention import Attention, Signals, GlanceHistory, WARM_HOLD_SEC, DEMOTE_DWELL_SEC, REST_TO_DORMANT_SEC

T0 = 1_800_000_000.0
QUIET = dict(local_hour=14.0, obs_age=30, rain_rate_mm=0.0, rain_wet=False, lightning_age=None,
             precip_pct=10, conditions='Clear', echo=False, echo_age=60, viewed_age=7200, touch_age=7200)


def sig(now, **over):
    values = dict(QUIET); values.update(over)
    return Signals(now, **values)


def test_startup_is_watch_until_evidence_then_rest_after_dwell():
    a = Attention(T0)
    assert a.tier == 'watch'
    assert a.decide(sig(T0)) == 'watch'                                   # demotion waits 10 min
    assert a.decide(sig(T0 + DEMOTE_DWELL_SEC)) == 'rest'
    assert a.reason.startswith('quiet weather')


def test_open_tab_is_live_and_closing_drops_to_warm_at_once_then_rest():
    a = Attention(T0, 'rest')
    assert a.decide(sig(T0, viewing=True, viewed_age=0)) == 'live'
    assert a.decide(sig(T0 + 5, viewing=False, viewed_age=5)) == 'warm'    # tab closed: immediate, warm holds
    assert a.decide(sig(T0 + WARM_HOLD_SEC - 1, viewed_age=WARM_HOLD_SEC - 1)) == 'warm'
    assert a.decide(sig(T0 + WARM_HOLD_SEC + 1, viewed_age=WARM_HOLD_SEC + 1)) == 'rest'


def test_a_touch_anywhere_warms_the_radar():
    a = Attention(T0, 'rest')
    assert a.decide(sig(T0, touch_age=0)) == 'warm'
    assert 'attention 0 min ago' in a.reason


def test_rain_lightning_echo_and_forecast_hold_watch():
    for over, hold in ((dict(rain_rate_mm=0.4), 'rain'), (dict(rain_wet=True), 'rain'),
                       (dict(lightning_age=60), 'lightning'), (dict(echo=True, echo_age=30), 'echo'),
                       (dict(sentinel_echo=True, sentinel_age=30), 'echo'), (dict(precip_pct=60), 'forecast'),
                       (dict(conditions='Showers until 4 PM'), 'forecast')):
        a = Attention(T0, 'rest')
        assert a.decide(sig(T0, **over)) == 'watch', over
        assert hold in a.reason, (over, a.reason)


def test_rain_hold_lasts_an_hour_then_rest():
    a = Attention(T0, 'rest')
    a.decide(sig(T0, rain_rate_mm=1.0))
    assert a.decide(sig(T0 + 3500)) == 'watch'
    assert a.decide(sig(T0 + 3700)) == 'rest'


def test_forecast_has_hysteresis():
    a = Attention(T0, 'rest')
    a.decide(sig(T0, precip_pct=55)); assert a.forecast_on
    a.decide(sig(T0 + 700, precip_pct=40)); assert a.forecast_on            # between 30 and 50: stays on
    a.decide(sig(T0 + 1400, precip_pct=20)); assert not a.forecast_on


def test_unknown_observations_keep_at_least_watch():
    a = Attention(T0, 'rest')
    assert a.decide(sig(T0, obs_age=None)) == 'watch'
    assert a.reason == 'observations unknown'
    a = Attention(T0, 'rest')
    assert a.decide(sig(T0, obs_age=900, rain_rate_mm=5.0)) == 'watch'      # stale rain is not evidence of rain
    assert 'unknown' in a.reason


def test_lan_viewer_is_weak_evidence():
    a = Attention(T0, 'rest')
    assert a.decide(sig(T0, lan_viewer_age=10)) == 'watch'
    assert a.decide(sig(T0 + 1, lan_viewer_age=10, viewing=False)) != 'live'


def test_quiet_night_goes_dormant_after_two_hours_of_rest_and_away_after_three_days():
    a = Attention(T0, 'rest')
    a.decide(sig(T0, local_hour=1.0))
    assert a.decide(sig(T0 + REST_TO_DORMANT_SEC - 60, local_hour=1.5)) == 'rest'
    assert a.decide(sig(T0 + REST_TO_DORMANT_SEC + 60, local_hour=1.6)) == 'dormant'
    assert a.decide(sig(T0 + REST_TO_DORMANT_SEC + 120, local_hour=1.6, rain_rate_mm=1)) == 'watch'  # rain wakes it
    b = Attention(T0, 'rest')
    assert b.decide(sig(T0, viewed_age=4 * 86400, touch_age=4 * 86400)) == 'rest'
    assert b.decide(sig(T0 + DEMOTE_DWELL_SEC, viewed_age=4 * 86400, touch_age=4 * 86400)) == 'dormant'
    assert b.reason == 'away'


def test_knobs_follow_tier_and_night():
    a = Attention(T0, 'watch')
    assert a.knobs(14.0)['frames'] == 8 and a.knobs(2.0)['frames'] == 1
    a.tier = 'rest'
    k = a.knobs(14.0); assert k['tiles'] is False and k['listing'] == 900 and k['sentinel'] == 3600
    assert a.knobs(2.0)['sentinel'] == 7200
    a.tier = 'live'; assert a.knobs(2.0) == dict(tier='live', frames=8, tiles=True, listing=0, sentinel=0, prefetch=True)
    a.tier = 'dormant'; assert a.knobs(2.0)['listing'] == 3600


def test_forced_tier_and_transitions_log():
    a = Attention(T0, 'rest')
    a.forced = 'live'
    assert a.decide(sig(T0)) == 'live' and a.reason == 'forced live'
    a.forced = None
    a.decide(sig(T0 + 1))
    assert [t[2] for t in a.transitions] == ['live', 'warm'] or [t[2] for t in a.transitions][:1] == ['live']


def test_glance_history_is_inert_until_it_has_data(tmp_path):
    tz = pytz.timezone('America/Los_Angeles')
    g = GlanceHistory(str(tmp_path / 'g.json'))
    start = tz.localize(datetime(2026, 9, 1, 7, 5))
    assert not g.expected(start)
    for day in range(30):                        # 30 days of a 7 am glance, plus noise
        g.record(tz.localize(datetime(2026, 9, 1 + day, 7, 5)))
        if day % 4 == 0:
            g.record(tz.localize(datetime(2026, 9, 1 + day, 19, 30)))
    late = tz.localize(datetime(2026, 10, 2, 7, 10))
    assert g.ready(late.timestamp()) and g.expected(late)
    assert g.expected(tz.localize(datetime(2026, 10, 2, 6, 55)))          # ten minutes before the hour
    assert not g.expected(tz.localize(datetime(2026, 10, 2, 13, 0)))
    reloaded = GlanceHistory(str(tmp_path / 'g.json'))
    assert reloaded.total == g.total and reloaded.expected(late)
