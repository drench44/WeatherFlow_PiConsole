"""Complete provider pins and per-colour, alpha-preserving palette inversion."""
import csv
import logging
from pathlib import Path

import pytest
from PIL import Image

from lib import radar_palette as rp


@pytest.mark.parametrize('source,name,rows', [
    ('iem-mrms-lcref','ramp_mrms_lcref.csv',256),
    ('iem-nexrad-n0b','ramp_n0b.csv',256),
    ('rainviewer','rainviewer_api_colors_table.csv',256)])
def test_complete_pins_and_runtime_copies(source, name, rows):
    path=Path('tests/fixtures')/name
    text=path.read_text()
    assert text.startswith('# Source: https://') and '# Retrieved: 2026-09-13.' in text
    assert path.read_bytes()==(Path('lib/data')/name).read_bytes()
    data=list(csv.DictReader(line for line in text.splitlines() if not line.startswith('#')))
    assert len(data)==rows
    if source!='rainviewer':
        assert [int(r['index']) for r in data]==list(range(256))
        for r in data:
            if r['dbz']:
                assert float(r['dbz'])==int(r['index'])/2-(32 if source=='iem-mrms-lcref' else 33)
    else:
        assert [int(r['dBZ / RGBA']) for r in data]==list(range(-32,96))*2


@pytest.mark.parametrize('source', rp._FILES)
def test_identity_palette_roundtrip_every_unambiguous_native_colour(source):
    exact,_=rp._tables(source)
    # One unified palette cannot reproduce distinct rain/snow RGB at the same
    # dBZ. Identity is defined on the native rain ramp; snow still inverts below.
    if source=='rainviewer':
        path=Path('tests/fixtures/rainviewer_api_colors_table.csv')
        rows=list(csv.DictReader(l for l in path.read_text().splitlines() if not l.startswith('#')))[:128]
        colors=[tuple(bytes.fromhex(row['Universal Blue'][1:])) for row in rows if row['Universal Blue'][-2:]!='00']
    else:
        colors=list(exact)
    stops={rp.native_dbz(source,c):c[:3]+(255,) for c in colors}
    # Repeated RGBA bins have a canonical lowest dBZ; each canonical bin has one
    # colour on these rain/native ramps. Transparent reserved codes remain clear.
    image=Image.new('RGBA',(len(colors)+1,1)); image.putdata(colors+[(0,0,0,0)])
    result=rp.remap(image,source,sorted(stops.items()))
    assert result.tobytes()==image.tobytes()


@pytest.mark.parametrize('source',rp._FILES)
def test_two_stops_alpha_preserved_and_unknown_counted(source,caplog):
    exact,_=rp._tables(source)
    low=next(c for c,d in exact.items() if 5<=d<20 and c[3]==255)
    high=next(c for c,d in exact.items() if 40<=d<60 and c[3]==255)
    unknown=(19,37,53,255)
    assert rp.native_dbz(source,unknown) is None
    image=Image.new('RGBA',(6,1))
    image.putdata([low,high,(0,0,0,0),unknown,unknown,high[:3]+(73,)])
    with caplog.at_level(logging.WARNING,logger='lib.radar_palette'):
        result=rp.remap(image,source,[(-100,(1,2,3,255)),(30,(4,5,6,255))])
    assert list(result.getdata())==[(1,2,3,255),(4,5,6,255),(0,0,0,0),(0,0,0,0),(0,0,0,0),(4,5,6,73)]
    assert len(caplog.records)==1 and '2 unknown pixels (1 colours)' in caplog.text
    opaque=Image.new('RGBA',(3,1)); opaque.putdata([low,high,(0,0,0,0)])
    assert set(rp.remap(opaque,source,[(-100,(1,2,3,255)),(30,(4,5,6,255))]).getdata())=={
        (1,2,3,255),(4,5,6,255),(0,0,0,0)}


def test_exact_first_tolerance_transparent_and_snow():
    assert rp.native_dbz('rainviewer',(146,136,113,100))==5
    assert rp.native_dbz('rainviewer',(0,163,224,255))==20
    assert rp.native_dbz('rainviewer',(0,163,225,90))==20
    assert rp.native_dbz('rainviewer',(19,37,53,80)) is None
    assert rp.native_dbz('rainviewer',(0,163,224,0)) is None
    assert rp.native_dbz('rainviewer',(127,191,255,255))==20
    for source in rp._FILES:
        exact,_=rp._tables(source)
        for color,dbz in exact.items():
            assert rp.native_dbz(source,color)==dbz


def test_native_lookup_once_per_distinct_colour(monkeypatch):
    calls=[]; original=rp.native_dbz
    def count(source,color):
        calls.append(color); return original(source,color)
    monkeypatch.setattr(rp,'native_dbz',count)
    image=Image.new('RGBA',(256,256),(0,163,224,255))
    result=rp.remap(image,'rainviewer',[(0,(1,2,3,255))])
    assert calls==[(0,163,224,255)] and result.getpixel((128,128))==(1,2,3,255)


def test_below_floor_and_transparent_stop():
    image=Image.new('RGBA',(1,1),(0,163,224,255))
    for palette in [[(30,(1,2,3,255))],[(0,(1,2,3,0))]]:
        assert rp.remap(image,'rainviewer',palette).getpixel((0,0))==(0,0,0,0)


@pytest.mark.parametrize('palette',[[],[(10,(1,2,3,255)),(0,(4,5,6,255))],[(0,(1,2,3))],[(float('nan'),(1,2,3,255))]])
def test_invalid_palette_rejected(palette):
    with pytest.raises(ValueError): rp.remap(Image.new('RGBA',(1,1)),'rainviewer',palette)


def test_unknown_source_rejected():
    with pytest.raises(ValueError): rp.native_dbz('mystery',(0,0,0,0))


def test_shared_ramp_geometry_and_computed_contrast():
    bands=rp._RADAR_RAMP['bands']
    assert len(bands)==9 and bands[0]['lo']==10 and bands[-1]['hi']==75
    assert [b['hi']-b['lo'] for b in bands]==[10,5,10,5,5,5,10,10,5]
    assert all(a['hi']==b['lo'] for a,b in zip(bands,bands[1:]))
    assert bands[-1]['start']==bands[-1]['end']
    lut=rp.sample_ramp(); assert len(lut)==26
    def luminance(rgb):
        linear=[v/255/12.92 if v/255<=.04045 else ((v/255+.055)/1.055)**2.4 for v in rgb]
        return sum(a*b for a,b in zip(linear,(.2126,.7152,.0722)))
    for _, color in lut:
        for ground,floor in [('#F2EDE2',2),('#0B0D11',3)]:
            a,b=sorted((luminance(color[:3]),luminance(bytes.fromhex(ground[1:]))))
            assert (b+.05)/(a+.05)>=floor


@pytest.mark.parametrize('source',rp._FILES)
def test_rgba_exact_edges_and_five_percent_alien(source):
    from bisect import bisect_right
    exact,rgb=rp._tables(source)
    colors=[c for c,d in exact.items() if d>=10 and c[3]==255][:19]
    expected=[rp._RADAR_LUT[bisect_right([f for f,c in rp._RADAR_LUT],rp.native_dbz(source,c))-1][1] for c in colors]
    tile=Image.new('RGBA',(19,1));tile.putdata(colors)
    assert list(rp.remap(tile,source,rp._RADAR_LUT).getdata())==expected
    # Choose an unambiguous one-unit edge colour, so a different exact bin cannot win.
    for color,want in zip(colors,expected):
        edge=next((color[:i]+(color[i]+delta,)+color[i+1:] for i in range(3) for delta in (-1,1)
                   if 0<=color[i]+delta<=255 and
                   (color[:i]+(color[i]+delta,)+color[i+1:])[:3] not in rgb and
                   rp.native_dbz(source,color[:i]+(color[i]+delta,)+color[i+1:])==rp.native_dbz(source,color)),None)
        assert edge is not None
        result=rp.remap(Image.new('RGBA',(1,1),edge),source,rp._RADAR_LUT)
        assert result.getpixel((0,0))==want
    tile=Image.new('RGBA',(20,1)); tile.putdata(colors+[(19,37,53,255)])
    result=rp.remap(tile,source,rp._RADAR_LUT)
    assert result.getpixel((19,0))==(0,0,0,0) and result.info['remapped'] is False
    assert result.info['unmatchedColors']==1 and result.info['opaqueColors']==20


@pytest.mark.parametrize('source,offset',[('iem-mrms-lcref',32),('iem-nexrad-n0b',33)])
def test_index_formulas_floor_and_reserved(source,offset):
    for i in (0,1,2,128,255):
        assert rp.INDEX_DBZ[source][i]==(None if source=='iem-nexrad-n0b' and i<2 else i/2-offset)
    exact,_=rp._tables(source)
    below=[c for c,d in exact.items() if d<10]
    for color in below:
        assert rp.remap(Image.new('RGBA',(1,1),color),source,rp._RADAR_LUT).getpixel((0,0))[3]==0
    if source=='iem-nexrad-n0b':
        rows=list(csv.DictReader(l for l in Path('lib/data/ramp_n0b.csv').read_text().splitlines() if not l.startswith('#')))
        for row in rows[:2]:
            color=tuple(int(row[k]) for k in ('r','g','b','a'))
            assert rp.remap(Image.new('RGBA',(1,1),color),source,rp._RADAR_LUT).getpixel((0,0))[3]==0


def test_adaptive_verification_and_exact_alpha_palette():
    colors=[c for c,d in rp._tables('iem-mrms-lcref')[0].items() if d>=10][:100]
    colors += [c[:3]+(73,) for c in colors[:30]]
    tile=Image.new('RGBA',(256,256));tile.putdata((colors*65536)[:65536])
    from bisect import bisect_right
    floors=[f for f,c in rp._RADAR_LUT]
    expected={c:rp._RADAR_LUT[bisect_right(floors,rp.native_dbz('iem-mrms-lcref',c))-1][1][:3]+(c[3],) for c in colors}
    result=rp.remap(tile,'iem-mrms-lcref',rp._RADAR_LUT)
    assert result.tobytes()==bytes(v for c in (colors*65536)[:65536] for v in expected[c])


def test_ambiguity_disclosed_at_independent_boundaries():
    # These expectations come from the pinned rows, not the chosen inverse.
    assert rp.native_range('rainviewer',(255,255,255,255))==(65,74)
    for source,color in [('rainviewer',(255,255,255,255)),('iem-mrms-lcref',(255,144,0,255))]:
        result=rp.remap(Image.new('RGBA',(10,10),color),source,rp._RADAR_LUT)
        assert not result.info['remapped'] and result.info['ambiguousPixels']==100


def test_unknown_coverage_cannot_hide_behind_below_floor_colors():
    colors=[c for c,d in rp._tables('iem-mrms-lcref')[0].items() if d<10][:49]
    tile=Image.new('RGBA',(256,256),(19,37,53,255))
    for i,c in enumerate(colors):tile.putpixel((i,0),c)
    mapped=rp.remap(tile,'iem-mrms-lcref',rp._RADAR_LUT)
    assert not mapped.getbbox() and not mapped.info['remapped']
    assert mapped.info['unmatchedPixels']>=65487


@pytest.mark.parametrize('source',['iem-mrms-lcref','iem-nexrad-n0b'])
def test_verified_index_palette_preserves_dbz(source):
    image=Image.new('P',(256,1));image.putdata(range(256))
    image.putpalette([v for c in rp._indexed_colors(source) for v in c],rawmode='RGBA')
    result=rp.remap(image,source,[(10,(1,2,3,255)),(50,(4,5,6,255)),(70,(7,8,9,255))])
    for index in range(256):
        dbz=rp.INDEX_DBZ[source][index];alpha=rp._indexed_colors(source)[index][3]
        expected=(0,0,0,0) if dbz is None or dbz<10 else ((1,2,3) if dbz<50 else (4,5,6) if dbz<70 else (7,8,9))+(alpha,)
        assert result.getpixel((index,0))==expected
    assert result.info['remapped'] and result.info['ambiguousPixels']==0
