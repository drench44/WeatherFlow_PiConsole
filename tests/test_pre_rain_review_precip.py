"""Real transport envelopes, parser validation and device/Pi clock separation."""
import asyncio
from types import SimpleNamespace

import pytest

from lib import almanac_emit as ae, observation_parser as op
from tests.test_pre_rain_fixes import parser_app


@pytest.mark.parametrize('evt', [12, True, {}, {'0': 12}, [True], [float('nan')], [float('inf')], [-1], [10**400], ['12']])
def test_malformed_events_are_harmless(make_parser, evt):
    app, cc = parser_app(); parser = make_parser(app)
    parser.parse_evt_precip({'evt': evt}, app.config)
    assert parser.display_obs['precipStartTs'] is None


def test_out_of_order_and_duplicate_events_do_not_regress_or_extend(make_parser, monkeypatch):
    app, cc = parser_app(); parser = make_parser(app)
    monkeypatch.setattr(op.time, 'time', lambda: 1000)
    parser.parse_evt_precip({'evt': [900]}, app.config)
    monkeypatch.setattr(op.time, 'time', lambda: 1200)
    parser.parse_evt_precip({'evt': [800]}, app.config)
    parser.parse_evt_precip({'evt': [900]}, app.config)
    assert cc.Obs['precipStartTs'] == 900
    assert cc.Obs['precipStartReceivedTs'] == 1000


@pytest.mark.parametrize('skew', [-600, 600])
def test_device_skew_does_not_change_onset_lifetime(make_parser, make_emitter, monkeypatch, skew):
    app, cc = parser_app(); parser = make_parser(app)
    now = [1_800_000_000.]
    monkeypatch.setattr(op.time, 'time', lambda: now[0])
    parser.display_obs.update(obsTs=now[0]+skew-30, RainRate=['0.00', 'in/hr', 'Currently Dry'])
    parser.update_display('obs_st')
    parser.parse_evt_precip({'evt': [now[0]+skew]}, app.config)
    e = make_emitter({'Obs': cc.Obs})
    payload = e._build_payload()
    assert payload['rainStatus'] == 'Rain Starting'
    assert payload['radar']['attention']['weather'] is True
    now[0] += 301
    assert e._build_payload()['rainStatus'] == 'Currently Dry'
    now[0] -= 300
    cc.Obs['obsTs'] += 60
    assert e._build_payload()['rainStatus'] == 'Currently Dry'


def test_deferred_event_update_does_not_publish_half_built_observation(make_parser):
    app, cc = parser_app(); parser = make_parser(app)
    cc.Obs.update(obsTs=1000, RainRate=['0', 'in/hr', 'Currently Dry'])
    queued = []
    update = parser.update_display
    parser.update_display = queued.append  # emulate Kivy mainthread scheduling
    parser.parse_evt_precip({'evt': [1010]}, app.config)
    parser.display_obs['obsTs'] = 1060  # obs_st worker has started, not formatted rain yet
    parser.display_obs['RainRate'] = ['99', 'in/hr', 'Heavy Rain']
    update(queued.pop())
    assert cc.Obs['precipStartTs'] == 1010
    assert cc.Obs['obsTs'] == 1000
    assert cc.Obs['RainRate'][0] == '0'


@pytest.mark.parametrize('transport,device,accepted', [('ws',111,True), ('udp','ST-1',True), ('udp','SK-1',True), ('udp','ST-other',False)])
def test_real_transport_envelopes(make_parser, transport, device, accepted):
    from service.udp import udp_client
    from service.websocket import websocketClient
    app, cc = parser_app(); app.config['Station'].update(TempestSN='ST-1', SkySN='SK-1')
    app.obsParser = make_parser(app)
    cls = websocketClient if transport == 'ws' else udp_client
    client = cls.__new__(cls)
    client.app=app; client.config=app.config
    client.message={'type':'evt_precip', 'evt':[1_800_000_000], **({'device_id':device} if transport=='ws' else {'serial_number':device,'hub_sn':'HB-1'})}
    decode = client._websocketClient__async__decodeMessage if transport=='ws' else client._udp_client__async__decode_message
    asyncio.run(decode())
    assert (cc.Obs['precipStartTs'] == 1_800_000_000) is accepted


def test_event_then_real_dry_obs_st_governs(make_parser, make_emitter, monkeypatch):
    from tests.test_freshness_emitter import _obs_st, _app
    app, cc = _app(); parser = make_parser(app)
    now = 1_800_000_000
    monkeypatch.setattr(op.time, 'time', lambda: now)
    parser.parse_obs_st(_obs_st(now-30), app.config)
    parser.parse_evt_precip({'type':'evt_precip', 'device_id':111, 'evt':[now]}, app.config)
    e = make_emitter({'Obs':cc.Obs})
    assert e._build_payload()['rainStatus'] == 'Rain Starting'
    parser.parse_obs_st(_obs_st(now+30), app.config)
    assert cc.Obs['obsTs'] == now+30  # seconds, not milliseconds
    assert e._build_payload()['rainStatus'] == 'Currently Dry'


def test_evt_precip_does_not_invoke_classic_panel_graphics(make_parser):
    app, cc = parser_app(); parser = make_parser(app)
    class Panel:
        def __getattr__(self, key):
            raise AssertionError('unexpected classic panel effect: ' + key)
    for name in ('RainfallPanel', 'TemperaturePanel', 'WindSpeedPanel', 'LightningPanel'):
        setattr(app, name, [Panel()])
    parser.parse_evt_precip({'type':'evt_precip', 'device_id':111, 'evt':[1_800_000_000]}, app.config)
    assert cc.Obs['precipStartTs'] == 1_800_000_000


