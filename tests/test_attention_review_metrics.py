import io
import json
from http.client import IncompleteRead

import pytest
from lib import almanac_emit as ae
from tests.test_radar_hybrid import hybrid  # noqa: F401


@pytest.mark.parametrize('fail', [False, True])
def test_body_bytes_keep_request_start_tier_including_partial_failure(make_emitter, hybrid, monkeypatch, fail):
    e = make_emitter(); e._radar_attention.tier = 'rest'
    class Response(io.BytesIO):
        status = 200
        headers = {}
        count = 0
        def read(self, size):
            self.count += 1
            e._radar_attention.tier = 'live'
            if self.count == 1: return b'x'*10
            if fail: raise IncompleteRead(b'123', 8)
            return b''
    e._radar_session = ae.RadarSession(); e._radar_session.begin_pass(100)
    monkeypatch.setattr(e._radar_session, 'open', lambda *a, **k: Response())
    try:
        e._radar_request('iem-mrms-lcref', ae.RADAR_IEM_METADATA_URL, 100, metadata=True)
    except IncompleteRead:
        assert fail
    assert e._radar_bytes_by_tier == {'rest': 13 if fail else 10}
    metric = e._radar_request_metrics[-1]
    assert metric['bytes'] == (13 if fail else 10) and metric['tier'] == 'rest'
    health = e._radar_health_payload()
    json.dumps(health, allow_nan=False)
    assert 'response bodies' in health['attention']['byteAccounting']
