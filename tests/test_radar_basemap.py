"""Hermetic basemap registration, clipping, geography, lifecycle and failure tests."""
import hashlib
import math
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import pytest

from lib import almanac_emit as ae
from lib import radar_basemap as bm
from lib.radar_geometry import plate_point, world_point
from tests.test_radar_hybrid import hybrid  # noqa: F401


def context(lat=47.61, lon=-122.33, zoom=7, viewed=True):
    tiles, _, bounds, _ = ae._radar_viewport(lat,lon,zoom,480)
    identity=hashlib.sha256(repr((lat,lon,zoom,480,tiles)).encode()).hexdigest()[:20]
    return dict(center=dict(lat=lat,lon=lon),zoom=zoom,bounds=bounds,tiles=tiles,identity=identity,viewed=viewed)


@pytest.mark.parametrize('lat,lon',[(47.61,-122.33),(52.52,13.4),(-33.87,151.21),(-16.8,179.7),(-16.8,-179.7)])
@pytest.mark.parametrize('zoom',[4,7,9])
def test_real_vertices_match_echo_crop(lat,lon,zoom):
    ctx=context(lat,lon,zoom)
    cx,cy=world_point(lat,lon,zoom); left,top=cx-240,cy-240
    path=str(bm.DATA_PATH); q,_,_=bm._index(path)
    world=256*2**zoom; tested=0
    # A 6° neighbourhood includes real coastline/admin vertices even at z9.
    for y in range(math.floor(lat)-3,math.floor(lat)+4):
        for x in range(math.floor(lon)-3,math.floor(lon)+4):
            for layer,points in bm._cell(path,(x+180)%360-180,y):
                if layer not in (2,3,4): continue
                for dx,dy in points:
                    vlat,vlon=y+dy/q,x+dx/q
                    wx=(vlon+180)/360*world
                    wy=(1-math.asinh(math.tan(math.radians(vlat)))/math.pi)/2*world
                    tx,ty=math.floor(wx/256),math.floor(wy/256)
                    # Independently reproduce the echo compositor's tile placement.
                    expected=(round(int(tx*256-left)+(wx-tx*256)),
                              round(int(ty*256-top)+(wy-ty*256)))
                    actual=tuple(round(v) for v in plate_point(vlat,vlon,zoom,left,top))
                    assert actual==expected
                    if 0<=actual[0]<480 and 0<=actual[1]<480:
                        tile=next(t for t in ctx['tiles'] if t[:2]==(tx%(2**zoom),ty))
                        assert actual==(round(tile[2]+wx-tx*256),round(tile[3]+wy-ty*256))
                    tested+=1
    assert tested>20


def test_clip_edges_and_reentry():
    polygon=bm.clip_polygon([(-10,-10),(490,-10),(490,490),(-10,490)],480)
    assert set(polygon)=={(0,0),(480,0),(480,480),(0,480)}
    assert bm.clip_polygon([(-10,0),(-5,0),(-5,10)],480)==[]
    assert bm.clip_segment((-10,240),(490,240),480)==((0,240),(480,240))
    assert bm.clip_segment((-1,0),(-1,480),480) is None
    assert bm.clip_segment((0,-10),(0,490),480)==((0,0),(0,480))
    lines=bm.clip_line([(10,10),(490,10),(490,30),(10,30)],480)
    assert lines==[[(10,10),(480,10)],[(480,30),(10,30)]]
    assert bm.simplify([(0,0),(1,.1),(2,0)])==[(0,0),(2,0)]


@pytest.mark.parametrize('lat,lon,zoom,coast,roads',[
    (47.61,-122.33,7,True,'dense'), (-33.87,151.21,7,True,'sparse'),
    (51.0,10.0,9,False,'sparse'), (0,-140,7,True,'none'),
    (0,179.5,7,True,'none'),(0,-179.5,7,True,'none'),
    (90,0,7,True,'none'),(-90,0,7,False,'none')])
def test_geography_and_svg(lat,lon,zoom,coast,roads):
    ctx=context(lat,lon,zoom)
    svg,metadata=bm.render(lat,lon,zoom,ctx['bounds'])
    assert metadata==dict(coast=coast,roads=roads)
    assert bm.render(lat,lon,zoom,ctx['bounds'])==(svg,metadata)
    root=ET.fromstring(svg)
    assert root.attrib=={'viewBox':'0 0 480 480'}
    for node in root:
        assert set(node.attrib)=={'class','d'}
        assert node.attrib['class'] in bm.CLASSES
        assert re.fullmatch(r'[MLZ0-9,]+',node.attrib['d'])
        assert all(0<=int(v)<=480 for v in re.findall(r'\d+',node.attrib['d']))
    if not coast: assert 'bm-ocean' not in svg and 'bm-lake' not in svg
    if lat==0:
        ocean=next(iter(root)); assert ocean.attrib['class']=='bm-ocean'
        assert '0,0' in ocean.attrib['d'] and '480,480' in ocean.attrib['d']
    assert not re.search('fill=|stroke=|style=|#[0-9a-fA-F]|accent',svg)


def test_artifact_size_and_demand_cache(tmp_path,monkeypatch):
    assert bm.DATA_PATH.stat().st_size<5_000_000
    ctx=context(viewed=False)
    assert bm.ensure(ctx,tmp_path) is None
    assert not (tmp_path/'basemap').exists()
    ctx['viewed']=True
    first=bm.ensure(ctx,tmp_path); target=tmp_path/first['url'].removeprefix('radar/')
    stamp=target.stat().st_mtime_ns
    original=bm.render; calls=[]
    def render(*a,**kw): calls.append(a); return original(*a,**kw)
    monkeypatch.setattr(bm,'render',render)
    assert bm.ensure(ctx,tmp_path)==first and not calls
    assert target.stat().st_mtime_ns==stamp
    ctx['viewed']=False
    assert bm.ensure(ctx,tmp_path)==first
    target.unlink(); assert bm.ensure(ctx,tmp_path) is None
    ctx['viewed']=True; bm.ensure(ctx,tmp_path); assert len(calls)==1
    new=bm.ensure(context(zoom=8),tmp_path)
    assert new['hash']!=first['hash'] and len(calls)==2
    assert not list(tmp_path.rglob('*.tmp.*'))


def test_emitter_gating_zoom_prune_failure(make_emitter,hybrid,tmp_path,monkeypatch):
    emitter=make_emitter(); emitter._do_radar()
    assert 'basemap' not in emitter._build_payload()['radar']
    hybrid.view(); emitter._do_radar()
    old=emitter._radar_result; first=old.basemap
    assert first and first['hash']==old.latest.split('/')[2]
    old_path=Path(ae.RADAR_DIR)/'basemap'/(first['hash']+'.svg')
    unrelated=old_path.parent/'unowned.svg'; unrelated.write_text('untouched')
    hybrid.mono+=60; (tmp_path/'radar_zoom').write_text('9'); emitter._do_radar()
    new=emitter._radar_result
    assert new.basemap['hash']!=first['hash'] and old_path.exists()
    hybrid.mono+=ae.RADAR_CACHE_GRACE_SEC
    emitter._do_radar()
    assert not old_path.exists() and unrelated.exists()
    hybrid.mono+=60; hybrid.view(); (tmp_path/'radar_zoom').write_text('8')
    monkeypatch.setattr(bm,'DATA_PATH',tmp_path/'absent-data.bin')
    emitter._do_radar()
    assert emitter._radar_available and emitter._radar_result.basemap is None
    assert emitter._radar_zoom==8


def test_atomic_failure_leaves_no_partial(tmp_path,monkeypatch):
    monkeypatch.setattr(bm.os,'replace',lambda *a: (_ for _ in ()).throw(OSError('write failed')))
    with pytest.raises(OSError): bm.ensure(context(),tmp_path)
    assert not list(tmp_path.rglob('*.svg')) and not list(tmp_path.rglob('*.tmp.*'))
