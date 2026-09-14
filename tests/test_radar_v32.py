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


@pytest.mark.skipif(os.environ.get('RADAR_NET_TEST') != '1', reason='opt in with RADAR_NET_TEST=1')
@pytest.mark.parametrize('name,lat,lon,mode', [('aberdeen',46.98,-123.82,'site'), ('seattle',47.61,-122.33,'mosaic')])
def test_live_v32_crop(make_emitter, tmp_path, monkeypatch, name, lat, lon, mode):
    """Live clipped native returns, independent layer rebuild, and saved evidence."""
    import io
    import json
    import os
    from datetime import datetime, timezone
    from pathlib import Path
    from tests.fixtures.config import make_config

    root = Path(os.environ.get('RADAR_V32_ARTIFACTS', str(tmp_path/'evidence')))/name
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ae, 'RADAR_DIR', str(tmp_path/'radar'))
    monkeypatch.setattr(ae.Logger, 'warning', print)
    (tmp_path/'radar_source').write_text(mode); (tmp_path/'radar_zoom').write_text('7')
    emitter = make_emitter(config=make_config(Station={'Latitude':str(lat),'Longitude':str(lon)}))
    raw_tiles = {}; tile_order = {}; original = emitter._radar_request
    def request(source, url, *args, **kwargs):
        raw = original(source, url, *args, **kwargs)
        if '.png' in url: raw_tiles[url] = raw
        return raw
    monkeypatch.setattr(emitter, '_radar_request', request)
    original_batch = emitter._radar_tile_batch
    def batch(source, stamp, ctx, deadline, url, site=None):
        # Integer paste registration can overlap one boundary row. Preserve
        # actual tile arrival order when reconstructing those shared pixels.
        order = tile_order.setdefault((source, site, stamp), [])
        for item in original_batch(source, stamp, ctx, deadline, url, site):
            order.append(item[0])
            yield item
    monkeypatch.setattr(emitter, '_radar_tile_batch', batch)
    emitter._do_radar(); payload = emitter._build_payload(); r = payload['radar']
    assert r['available'] and r['sourceMode'] == mode and r['zoom'] == 7
    assert r['sourceId'] == ('iem-nexrad-n0b' if mode == 'site' else 'iem-mrms-lcref')
    assert r['legend']['floorDbz'] == (5 if mode == 'site' else 10)
    frame = next(f for f in r['frames'] if f['id'] == r['latest'])
    source = r['sourceId']; native_clear_pixels = 0; mapped_clear_pixels = 0
    # Rebuild the actual newest crop from captured provider bytes in paste order.
    expected = Image.new('RGBA', (956,490))
    pairs = frame['siteScans'] if mode == 'site' else [dict(id=None,ts=frame['ts'])]
    for pair in pairs:
        stamp = datetime.fromtimestamp(pair['ts'],timezone.utc).strftime('%Y%m%d%H%M')
        layer = Image.new('RGBA',expected.size)
        for tx,ty,x,y in tile_order[source, pair['id'], pair['ts']]:
            if mode == 'site':
                url = ae.RADAR_SITE_TILE_TEMPLATE.format(site=pair['id'][1:],stamp=stamp,z=7,x=tx,y=ty)
            else:
                matches = [u for u in raw_tiles if f'/7/{tx}/{ty}.png' in u]
                assert len(matches) == 1, matches
                url = matches[0]
            raw = raw_tiles[url]
            (root/f'native-{pair["id"] or "mrms"}-{tx}-{ty}.png').write_bytes(raw)
            with Image.open(io.BytesIO(raw)) as tile:
                crop=(max(0,-x),max(0,-y),min(256,956-x),min(256,490-y))
                clipped=tile.crop(crop)
                # Native intensity independently establishes the 5–10 dBZ input.
                if clipped.mode == 'P':
                    native_clear_pixels += sum(n for n,i in clipped.getcolors(65536)
                        if rp.INDEX_DBZ[source][i] is not None and 5 <= rp.INDEX_DBZ[source][i] < 10)
                else:
                    native_clear_pixels += sum(n for n,c in clipped.convert('RGBA').getcolors(65536)
                        if (rp.native_dbz(source,c) is not None and 5 <= rp.native_dbz(source,c) < 10))
                mapped=rp.remap(clipped,source,rp.source_palette(source))
                mapped_clear_pixels += sum(n for n,c in mapped.getcolors(65536) if c==(127,130,149,180))
                layer.paste(mapped,(x+crop[0],y+crop[1]))
        expected.alpha_composite(layer)
        layer.save(root/f'layer-{pair["id"] or "mrms"}.png')
    with Image.open(Path(ae.RADAR_DIR)/(r['latest']+'.png')) as actual:
        assert actual.tobytes() == expected.tobytes()
        colors = actual.convert('RGBA').getcolors(956*490)
        exact = sum(n for n,c in colors if c==(127,130,149,180))
        clear_rgb = sum(n for n,c in colors if c[3] and c[:3]==(127,130,149))
        actual.save(root/'crop.png')
    evidence = dict(sourceId=source,zoom=7,center=r['center'],floorDbz=r['legend']['floorDbz'],
                    observedAt=r['observedAt'],observedTs=r['observedTs'],native5to10Pixels=native_clear_pixels,
                    layerClearAir180Pixels=mapped_clear_pixels,cropClearAir180Pixels=exact,cropClearAirRgbPixels=clear_rgb,
                    requests=len(emitter._radar_request_times),sites=r.get('sites'),reconstruction='byte-identical')
    (root/'evidence.json').write_text(json.dumps(evidence,indent=2))
    (root/'payload.json').write_text(json.dumps(payload,indent=2))
    print('V3.2 LIVE',name,json.dumps(evidence),flush=True)
    if mode == 'site':
        assert native_clear_pixels > 0 and mapped_clear_pixels > 0 and exact > 0
    else:
        assert mapped_clear_pixels == exact == clear_rgb == 0
    emitter.stop()
