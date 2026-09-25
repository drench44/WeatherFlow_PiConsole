"""NOAA categorical product 165: real layout, independent bearing table."""
import bz2
import struct
from pathlib import Path

import numpy as np
import pytest

from lib import radar_level3 as l3
from tests.test_radar_level3 import product, SITE


def n0h_product(classes=None, **kwargs):
    """The HCA PDB has zero scaling words and word 26 = 255, not N0B scaling."""
    gates = kwargs.setdefault('gates', 1200)
    radials = kwargs.setdefault('radials', 360)
    kwargs.setdefault('width', 10)
    kwargs.setdefault('start', 3597)
    kwargs.setdefault('code', 165)
    kwargs.setdefault('thresholds', (0, 0, 0))
    if classes is None:
        classes = np.full((radials, gates), 60, np.uint8)
    raw = bytearray(product(codes=classes, **kwargs).replace(b'N0BNEA', b'N0HNEA'))
    offset = raw.index(b'\r\r\n', raw.index(b'\r\r\n')+3)+3
    struct.pack_into('>h', raw, offset+18+26*2, 255)
    return bytes(raw)


def test_categorical_layout_and_independent_bearings():
    scan = l3.decode_n0h(n0h_product(), expect_site=SITE)
    assert scan.codes.shape == (360, 1200)
    assert scan.bearing_index[3598] == scan.bearing_index[2] == 0
    assert scan.bearing_index[7] == 1
    assert scan.volume_ts == 1789257600
    assert (scan.codes == 60).all()
    with pytest.raises(ValueError, match='not N0B'):
        l3.decode(n0h_product())
    with pytest.raises(ValueError, match='not N0H'):
        l3.decode_n0h(product())


@pytest.mark.parametrize('kwargs,match', [
    ({'thresholds': (-320, 5, 254)}, 'thresholds'),
    ({'gates': 1202}, 'radial packet'), ({'radials': 200}, 'radial packet'),
    ({'width': 5}, 'azimuth coverage'), ({'elevation': 30}, 'lowest tilt'),
    ({'expanded': 1700000}, 'uncompressed size'), ({'lat': 0}, 'another radar'),
])
def test_refusals(kwargs, match):
    with pytest.raises(ValueError, match=match):
        l3.decode_n0h(n0h_product(**kwargs), expect_site=SITE)


@pytest.mark.parametrize('damage', ['word26', 'volume', 'packet', 'radial', 'length', 'size', 'header', 'compression'])
def test_malformed_n0h(damage):
    raw = bytearray(n0h_product(compress=False))
    offset = raw.index(b'\r\r\n', raw.index(b'\r\r\n')+3)+3
    if damage == 'word26': struct.pack_into('>h', raw, offset+70, 254)
    elif damage == 'volume': struct.pack_into('>I', raw, offset+42, 86400)
    elif damage == 'packet': struct.pack_into('>h', raw, offset+120+16, 1)
    elif damage == 'radial': struct.pack_into('>h', raw, offset+120+30+4, 30)
    elif damage == 'length': raw.pop()
    elif damage == 'size': raw = b'x'*(l3.MAX_RAW_BYTES+1)
    elif damage == 'header': raw[:40] = b'x'*40
    elif damage == 'compression':
        raw = bytearray(n0h_product()); raw[-10:] = b'\0'*10
    with pytest.raises(ValueError):
        l3.decode_n0h(raw)


def test_real_katx_same_volume_fixture():
    path = Path(__file__).with_name('fixtures')/'n0h.bin'
    assert path.stat().st_size <= 32768
    scan = l3.decode_n0h(path.read_bytes(), expect_site=(48.194, -122.496))
    assert scan.codes.shape == (360, 1200)
    from datetime import datetime, timezone
    assert scan.volume_ts == datetime(2026, 9, 25, 13, 7, 14, tzinfo=timezone.utc).timestamp()
    assert scan.elevation_deg == .5
    assert {0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 140} >= set(np.unique(scan.codes))
    assert {10, 20, 60, 140} <= set(np.unique(scan.codes))
