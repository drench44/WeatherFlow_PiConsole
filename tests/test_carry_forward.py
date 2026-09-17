"""A restarted engine republishes the previous run's last observations, aged, until
live data lands. The kiosk wipes the browser profile on every start, so the page
cannot bridge a restart; the engine reads its own last wx.json."""
import json
import time
import pytest
from lib import almanac_emit as ae


def previous(tmp_path, **over):
    data = dict(ts=int(time.time())-90, obsTs=int(time.time())-120, obsAgeSec=30, temp=60.4, humidity=71,
                fcHigh=72.0, fcLow=46.0, aqi=21, sagerText='Fair', time='7:40 PM', alerts=[{'event':'old'}],
                radar={'available': True}, updateAvailable=True)
    data.update(over)
    (tmp_path/'wx.json').write_text(json.dumps(data))
    return data


def test_cold_start_republishes_the_last_run_aged(make_emitter, tmp_path):
    prev = previous(tmp_path)
    e = make_emitter()
    p = e._build_payload()
    assert p['carried'] is True
    assert p['temp'] == 60.4 and p['humidity'] == 71 and p['fcHigh'] == 72.0 and p['aqi'] == 21 and p['sagerText'] == 'Fair'
    assert p['obsTs'] == prev['obsTs'] and 115 <= p['obsAgeSec'] <= 130
    assert p['time'] != prev['time']                       # the clock is live
    assert p['alerts'] == [] or p['alerts'] != prev['alerts']   # alerts expire; never carried
    assert p['radar']['available'] is False and p['radar']['starting']   # radar has its own starting state
    assert p['updateAvailable'] is not True or p['updateAvailable'] == e._build_payload()['updateAvailable']


def test_live_observation_ends_the_observation_carry_but_fills_unfetched_fields(make_emitter, tmp_path):
    from tests.fixtures import obs_scenarios as scn
    previous(tmp_path, temp=60.4)
    e = make_emitter(scn.clear_day())
    p = e._build_payload()
    if p['obsTs'] is not None:                              # a live observation: its own numbers and age
        assert p['temp'] != 60.4 or p['obsAgeSec'] < 100
    assert p['fcHigh'] == 72.0 and p['carried'] is True     # forecast not fetched yet: still carried


def test_carry_is_bounded_by_age_and_window(make_emitter, tmp_path):
    previous(tmp_path, obsTs=int(time.time())-ae.CARRY_MAX_SEC-60)
    e = make_emitter()
    p = e._build_payload()
    assert p['carried'] is False and p['temp'] is None and e._carried is None
    previous(tmp_path)
    e = make_emitter()
    e._started_at -= ae.CARRY_WINDOW_SEC+1
    p = e._build_payload()
    assert p['carried'] is False and p['temp'] is None


def test_junk_or_missing_previous_file_is_ignored(make_emitter, tmp_path):
    (tmp_path/'wx.json').write_text('{not json')
    assert make_emitter()._build_payload()['carried'] is False
    (tmp_path/'wx.json').unlink()
    assert make_emitter()._build_payload()['carried'] is False
    (tmp_path/'wx.json').write_text(json.dumps(dict(ts=1, temp=5)))   # no obsTs: nothing to age by
    assert make_emitter()._build_payload()['carried'] is False
