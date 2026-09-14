"""Fable v3.2 K1–K6: source floor, computed contrast and alpha fidelity."""
import hashlib
import os

import pytest
from PIL import Image

from lib import almanac_emit as ae, radar_palette as rp
from tests.test_radar_hybrid import hybrid  # noqa: F401


def indexed(source):
    tile = Image.new('P', (256, 1)); tile.putdata(range(256))
    tile.putpalette([v for c in rp._indexed_colors(source) for v in c], rawmode='RGBA')
    return tile


def test_k1_source_legends():
    for source, settings in ae._RADAR_SOURCES.items():
        legend = settings['legend']
        if source == 'iem-nexrad-n0b':
            assert legend['floorDbz'] == 5
            assert legend['bands'][0] == dict(lo=5, hi=10, start='#7F8295', end='#7F8295', alpha=180, kind='clear-air')
            assert legend['bands'][1:] == rp._RADAR_RAMP['bands']
        else:
            assert legend['floorDbz'] == 10
            assert all('kind' not in b and 'alpha' not in b for b in legend['bands'])
    assert len(rp._RADAR_LUT) == 26
    assert rp._RADAR_SITE_PALETTE[1:] == rp._RADAR_LUT


def luminance(rgb):
    return sum(w*(v/255/12.92 if v/255 <= .04045 else ((v/255+.055)/1.055)**2.4)
               for w, v in zip((.2126, .7152, .0722), rgb))


def contrast(a, b):
    low, high = sorted((luminance(a), luminance(b)))
    return (high+.05)/(low+.05)


def test_k2_computed_contrast():
    for hexground, floor in [('EBE6DB', 2), ('F2EDE2', 2), ('0B0D11', 3)]:
        ground = bytes.fromhex(hexground)
        composite = [round(c*180/255+g*75/255) for c, g in zip((127,130,149), ground)]
        assert contrast(composite, ground) >= floor
        for _, color in rp._RADAR_LUT:
            assert contrast(color[:3], ground) >= floor


def test_k3_k4_index_floor_and_coverage():
    tile = indexed('iem-nexrad-n0b')
    site = rp.remap(tile, 'iem-nexrad-n0b', rp.source_palette('iem-nexrad-n0b'))
    for i in (0, 1, 2, 75):
        assert site.getpixel((i, 0))[3] == 0
    for i in range(76, 86):
        coverage = rp._indexed_colors('iem-nexrad-n0b')[i][3]
        assert site.getpixel((i, 0)) == (127,130,149,round(coverage*180/255))
    assert site.getpixel((86, 0)) == (138,163,198,255)
    mosaic = rp.remap(tile, 'iem-nexrad-n0b', rp.source_palette('iem-mrms-lcref'))
    assert all(mosaic.getpixel((i, 0))[3] == 0 for i in range(76, 86))
    mrms = rp.remap(indexed('iem-mrms-lcref'), 'iem-mrms-lcref', rp.source_palette('iem-mrms-lcref'))
    assert all(mrms.getpixel((i, 0))[3] == 0 for i in range(76, 84))
    # RGBA coverage is independent of intensity, including fractional edge alpha.
    color = rp._indexed_colors('iem-nexrad-n0b')[76]
    for coverage in (1, 73, 128, 180, 254, 255):
        rgba = Image.new('RGBA', (1, 1), color[:3]+(coverage,))
        assert rp.remap(rgba, 'iem-nexrad-n0b', rp._RADAR_SITE_PALETTE).getpixel((0, 0)) == (127,130,149,round(coverage*180/255))


@pytest.mark.parametrize('source', rp._FILES)
@pytest.mark.parametrize('mode', ['P', 'RGBA'])
def test_k5_opaque_rain_byte_identity(source, mode):
    """Independent pre-v3.2 selection: targets replace RGB and retain coverage."""
    if mode == 'P' and source in rp.INDEX_DBZ:
        tile = indexed(source)
    else:
        colors = list(rp._tables(source)[0])
        colors += [c[:3]+(73,) for c in colors[:30]]
        colors += [(0,0,0,0), (19,37,53,255)]
        tile = Image.new('RGBA', (len(colors), 1)); tile.putdata(colors)
        if mode == 'P':
            tile = tile.convert('P', palette=Image.Palette.ADAPTIVE, colors=256)
    # SHA256 of raw RGBA bytes from d5ea7bf's remapper on these exact tiles.
    gold = {
        ('P', 'iem-mrms-lcref'): '79591d3d35100d1b1897c0abb4af27dbe262d90e5f89c01828d79b709ff8f0a3',
        ('P', 'iem-nexrad-n0b'): '74d77ef15de6f526dfe599fb4044b37b533263f01eb50ce50e253ca5179e2082',
        ('P', 'rainviewer'): 'c02e74c611194097262cffc054d4d613e19a93ef852f1907cde6d8d5eb12044e',
        ('RGBA', 'iem-mrms-lcref'): 'dd4b16c07654641619e10a2a41606665db295cc6ce34131cf363d597c4b89d60',
        ('RGBA', 'iem-nexrad-n0b'): '71d9f703997fe5dd01e2759afa54cb7dc13f61d7fe37f0758ec91daae8880287',
        ('RGBA', 'rainviewer'): 'cd9770a6941b20e0c954b6b90b97ff504f4cdb13c24dfa81c782844c3bd3aef9',
    }
    assert hashlib.sha256(rp.remap(tile, source, rp._RADAR_LUT).tobytes()).hexdigest() == gold[mode, source]



def test_translucent_rgb_fallback_rounds_coverage(monkeypatch):
    # Force lossless RGB fallback, including the same native RGB at many alphas.
    native = rp._indexed_colors('iem-nexrad-n0b')[76][:3]
    colors = [native+(a,) for a in range(1, 256)]
    tile = Image.new('RGBA', (len(colors), 1)); tile.putdata(colors)
    convert = Image.Image.convert
    def lose_alpha(self, mode=None, *args, **kwargs):
        if self.mode == 'RGBA' and mode == 'P':
            return convert(convert(self, 'RGB'), mode, *args, **kwargs)
        return convert(self, mode, *args, **kwargs)
    monkeypatch.setattr(Image.Image, 'convert', lose_alpha)
    result = rp.remap(tile, 'iem-nexrad-n0b', rp._RADAR_SITE_PALETTE)
    assert list(result.getdata()) == [(127,130,149,round(a*180/255)) for a in range(1, 256)]


def test_k6_site_zoom_floor(make_emitter, hybrid, tmp_path):
    (tmp_path/'radar_source').write_text('site')
    (tmp_path/'radar_zoom').write_text('5')
    emitter = make_emitter(); emitter._do_radar()
    r = emitter._build_payload()['radar']
    assert r['sourcePref'] == 'site' and r['sourceMode'] == 'mosaic'
    assert r['legend']['floorDbz'] == 10
    assert all(b.get('kind') != 'clear-air' for b in r['legend']['bands'])




@pytest.mark.skipif(os.environ.get('RADAR_NET_TEST')!='1',reason='opt in with RADAR_NET_TEST=1')
@pytest.mark.parametrize('place,lat,lon,mode',[('Seattle',47.61,-122.33,'mosaic'),('Aberdeen',46.975,-123.815,'site')])
def test_live_tile_set_matches_provider_bytes(make_emitter,tmp_path,place,lat,lon,mode):
    """Real source PNGs remap byte-for-byte to independently stored XYZ tiles."""
    import io,json,time
    from pathlib import Path
    from tests.fixtures.config import make_config
    config=make_config();config['Station']['Latitude']=str(lat);config['Station']['Longitude']=str(lon)
    (tmp_path/'radar_source').write_text(mode)
    emitter=make_emitter(config=config)
    started=time.perf_counter();emitter._do_radar()
    expected_source='iem-nexrad-n0b' if mode=='site' else 'iem-mrms-lcref'
    assert emitter._radar_result.source_id==expected_source and emitter._radar_result.ts_frame,emitter._build_payload()['radar']
    count=visible=0;sites=set();max_error=0
    try:
        for key,native in emitter._radar_tiles.items():
            source,site,_,stamp,z,x,y=key
            if source!=expected_source:continue
            path=ae._radar_tile_path(source,site,stamp,z,x,y)
            if not path.exists():continue
            with Image.open(io.BytesIO(native)) as im:
                with rp.remap(im,source,rp.source_palette(source)) as mapped:
                    with Image.open(path) as cached:
                        assert cached.size==(256,256)
                        assert cached.convert('RGBA').tobytes()==mapped.tobytes(),str(path)
                        meta=json.loads(cached.info['radarRemap'])
                        assert set(meta)=={'remapped','unmatchedColors','opaqueColors','unmatchedPixels','opaquePixels','ambiguousPixels','revision'}
                        assert meta['revision']==ae.REMAP_REVISION
                        visible+=sum(p[3]>0 for p in cached.convert('RGBA').getdata())
                        max_error=max(max_error,meta['unmatchedPixels']/max(1,meta['opaquePixels']))
            count+=1;sites.add(site or '-')
        assert count>=4,count
        print('LIVE TILE SET',json.dumps(dict(place=place,source=expected_source,tiles=count,sites=sorted(sites),visiblePixels=visible,maxUnmatchedFraction=max_error,stamp=emitter._radar_result.ts_frame,seconds=round(time.perf_counter()-started,3),byteIdentical=True)),flush=True)
    finally:
        if emitter._radar_session:emitter._radar_session.close()
