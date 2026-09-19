"""WFP_RADAR=0: a kiosk that cannot show radar (tabs off) runs none of it."""
from lib import almanac_emit as ae


def test_disabled_radar_schedules_nothing_and_says_so(make_emitter, monkeypatch):
    monkeypatch.setattr(ae, 'RADAR_ENABLED', False)
    e = make_emitter()
    scheduled = []
    monkeypatch.setattr(e, '_schedule', lambda cb, delay, interval=False: scheduled.append(getattr(cb, '__name__', str(cb))) or object())
    e.start()
    assert not any(name.startswith('_check_radar') for name in scheduled), scheduled
    assert e._radar_cache_thread is None                     # no inventory scan thread
    p = e._build_payload()
    assert {k: v for k, v in p['radar'].items() if k != 'health'} == dict(available=False, reason='radar off', enabled=False, starting=None, attention=None)
    assert p['radar']['health']['enabled'] is False               # /health reads this block
    assert e._radar_health_payload()['enabled'] is False
    e.stop()


def test_enabled_radar_is_the_default(make_emitter, monkeypatch):
    assert ae.RADAR_ENABLED is True
    e = make_emitter()
    scheduled = []
    monkeypatch.setattr(e, '_schedule', lambda cb, delay, interval=False: scheduled.append(getattr(cb, '__name__', str(cb))) or object())
    monkeypatch.setattr(e, '_radar_start_inventory', lambda: scheduled.append('inventory'))
    e.start()
    assert 'inventory' in scheduled and any(name.startswith('_check_radar') for name in scheduled)
    e.stop()
