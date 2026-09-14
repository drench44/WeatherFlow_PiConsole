"""Native colour inverses and shared reflectivity remapping, without Python pixel loops."""
from bisect import bisect_right
import csv
from functools import lru_cache
import logging
import math
from pathlib import Path


_FILES = {'iem-mrms-lcref': 'ramp_mrms_lcref.csv',
          'iem-nexrad-n0b': 'ramp_n0b.csv',
          'rainviewer': 'rainviewer_api_colors_table.csv'}
REMAP_REVISION = "native-v3-fix-2"
_RGB_TOLERANCE = 3  # Euclidean RGB distance; alpha is coverage, not intensity.
_LOG = logging.getLogger(__name__)


_RADAR_RAMP = dict(id='almanac-reflectivity-v1', floorDbz=10, bands=[
    dict(lo=lo, hi=hi, start=start, end=end) for lo, hi, start, end in (
        (10,20,'#8AA3C6','#4E79B4'), (20,25,'#2E93A8','#227F92'),
        (25,35,'#3FA65E','#2A8448'), (35,40,'#C79C14','#B0870D'),
        (40,45,'#E5871A','#D2700F'), (45,50,'#DE5C17','#C94C0C'),
        (50,60,'#DD4530','#BC2A1A'), (60,70,'#CE4E88','#A9389B'),
        (70,75,'#8A46C2','#8A46C2'))])

def sample_ramp():
    result = []
    for i in range(26):
        dbz = 10 + i * 2.5
        band = next(b for b in _RADAR_RAMP['bands'] if b['lo'] <= dbz < b['hi'])
        start, end = (bytes.fromhex(band[k][1:]) for k in ('start', 'end'))
        t = (dbz-band['lo'])/(band['hi']-band['lo'])
        result.append((dbz, tuple(round(a+(b-a)*t) for a,b in zip(start,end))+(255,)))
    return tuple(result)

_RADAR_LUT = sample_ramp()
# Reserved N0B codes still have a documented numeric formula, but no echo.
INDEX_DBZ = {'iem-mrms-lcref': tuple(i/2-32 for i in range(256)),
             'iem-nexrad-n0b': tuple(None if i < 2 else i/2-33 for i in range(256))}


@lru_cache(maxsize=3)
def _tables(source):
    try:
        filename = _FILES[source]
    except KeyError:
        raise ValueError('unknown radar palette source: ' + str(source)) from None
    exact, rgb = {}, {}
    with (Path(__file__).parent / 'data' / filename).open() as stream:
        for row in csv.DictReader(line for line in stream if not line.startswith('#')):
            if source == 'rainviewer':
                dbz = float(row['dBZ / RGBA'])
                rgba = tuple(bytes.fromhex(row['Universal Blue'].lstrip('#')))
            else:
                if not row['dbz']:
                    continue
                dbz = float(row['dbz'])
                rgba = tuple(int(row[k]) for k in ('r', 'g', 'b', 'a'))
            if rgba[3]:
                exact.setdefault(rgba, []).append(dbz)
                rgb.setdefault(rgba[:3], []).append(dbz)
    return ({c: min(v) for c,v in exact.items()}, {c: min(v) for c,v in rgb.items()})


def native_dbz(source, rgba):
    """Exact RGBA first, then RGB within 3 units; unknown/transparent -> None.

    Coverage alpha may differ at antialiased edges without changing intensity.
    Ties use table order (lowest bin, with RainViewer rain preceding snow).
    """
    exact, rgb = _tables(source)
    rgba = tuple(rgba)
    if len(rgba) != 4 or any(not isinstance(c, int) or not 0 <= c <= 255 for c in rgba):
        raise ValueError('rgba must contain four byte values')
    if not rgba[3]:
        return None
    if rgba in exact:
        return exact[rgba]
    if rgba[:3] in rgb:
        return rgb[rgba[:3]]
    nearest = min(rgb, key=lambda c: sum((a-b)**2 for a, b in zip(c, rgba[:3])))
    if sum((a-b)**2 for a, b in zip(nearest, rgba[:3])) <= _RGB_TOLERANCE**2:
        return rgb[nearest]
    return None


@lru_cache(maxsize=3)
def _ranges(source):
    values = {}
    with (Path(__file__).parent / 'data' / _FILES[source]).open() as stream:
        for row in csv.DictReader(l for l in stream if not l.startswith('#')):
            if source == 'rainviewer':
                dbz = float(row['dBZ / RGBA'])
                color = tuple(bytes.fromhex(row['Universal Blue'].lstrip('#')))
            else:
                if not row['dbz']: continue
                dbz = float(row['dbz'])
                color = tuple(int(row[k]) for k in ('r','g','b','a'))
            if color[3]: values.setdefault(color[:3], []).append(dbz)
    return values

@lru_cache(maxsize=4096)
def native_range(source, color):
    values = _ranges(source)
    if color[:3] in values:
        candidates = values[color[:3]]
    else:
        candidates = [d for c, ds in values.items()
                      if sum((a-b)**2 for a,b in zip(c,color[:3])) <= _RGB_TOLERANCE**2
                      for d in ds]
    return (min(candidates), max(candidates)) if candidates else None


@lru_cache(maxsize=2)
def _indexed_colors(source):
    if source not in INDEX_DBZ: return ()
    with (Path(__file__).parent / 'data' / _FILES[source]).open() as stream:
        return tuple(tuple(int(row[k]) for k in ('r','g','b','a'))
                     for row in csv.DictReader(l for l in stream if not l.startswith('#')))


def _remap_indexed(image, source, palette, floors):
    """Preserve numeric intensity only for a verified provider index palette."""
    if image.mode != 'P' or source not in INDEX_DBZ: return None
    native = _indexed_colors(source)
    colors = image.getcolors() or []
    flat = image.getpalette('RGBA')
    # Transparency may live in PNG info rather than the palette itself.
    from PIL import Image
    swatch = Image.new('P', (256,1)); swatch.putpalette(flat, rawmode='RGBA')
    swatch.info.update(image.info); swatch.putdata(range(256))
    actual = list(swatch.convert('RGBA').getdata())
    if any(actual[index] != native[index] for count,index in colors): return None
    targets=[]
    for index,color in enumerate(actual):
        dbz=INDEX_DBZ[source][index]
        stop=bisect_right(floors,dbz)-1 if dbz is not None else -1
        targets.append(palette[stop][1][:3]+(color[3],) if stop>=0 and palette[stop][1][3] else (0,0,0,0))
    result=image.copy(); result.info.pop('transparency',None)
    result.putpalette([v for c in targets for v in c],rawmode='RGBA');result=result.convert('RGBA')
    opaque={actual[i] for n,i in colors if actual[i][3]}
    result.info.update(unmatchedColors=0,opaqueColors=len(opaque),unmatchedPixels=0,
                       opaquePixels=sum(n for n,i in colors if actual[i][3]),ambiguousPixels=0,
                       unmatchedColorValues=set(),opaqueColorValues=opaque,remapped=True)
    return result


def remap(image, source, palette):
    """Return RGBA using ordered (dBZ floor, RGBA) steps; top is open-ended.

    Input alpha is preserved for visible stops (never multiplied a second time);
    a stop with alpha=0 explicitly suppresses its bin. Below the first floor and
    unknown colours become transparent. Warn once with unknown pixel/colour
    counts. Native inverse/stop selection runs once per distinct colour, and the
    pixel pass uses verified palette swaps, with no numpy dependency.
    """
    from PIL import Image, ImageChops
    _tables(source)  # validate even an empty/transparent image
    palette = [(float(floor), tuple(rgba)) for floor, rgba in palette]
    if (not palette or any(not math.isfinite(f) or len(c) != 4 or
            any(not isinstance(v, int) or not 0 <= v <= 255 for v in c) for f, c in palette)
            or any(a[0] >= b[0] for a, b in zip(palette, palette[1:]))):
        raise ValueError('palette requires strictly increasing finite floors and RGBA bytes')
    floors = [f for f, _ in palette]
    indexed = _remap_indexed(image,source,palette,floors)
    if indexed is not None: return indexed
    with image.convert('RGBA') as rgba:
        mapping, unknown_pixels, unknown_colors, opaque_colors = {}, 0, 0, 0
        opaque_pixels, ambiguous_pixels = 0, 0
        unknown_values = set()
        # maxcolors=pixel count guarantees a full histogram even for an unusual
        # input with >256 colours; normal native radar tiles have <100.
        histogram = rgba.getcolors(rgba.width * rgba.height) or ()
        if len(histogram) > 1024:
            raise ValueError('radar tile exceeds native colour work limit')
        for count, color in histogram:
            opaque_colors += bool(color[3])
            opaque_pixels += count if color[3] else 0
            dbz = native_dbz(source, color)
            target = (0, 0, 0, 0)
            if dbz is None:
                if color[3]:
                    unknown_pixels += count
                    unknown_colors += 1
                    unknown_values.add(color)
            else:
                span = native_range(source, color)
                if span and bisect_right(floors, span[0]) != bisect_right(floors, span[1]):
                    ambiguous_pixels += count
                index = bisect_right(floors, dbz) - 1
                if index >= 0 and palette[index][1][3]:
                    target = palette[index][1][:3] + (color[3],)
            mapping[color] = target
        # Verify the adaptive RGBA palette before using it: Pillow's octree
        # can merge even fewer than 256 native colours. Exact RGB median-cut
        # plus the untouched alpha channel handles those tiles without loss.
        indexed = None
        if len(mapping) <= 256:
            candidate = rgba.convert('P', palette=Image.Palette.ADAPTIVE,
                                     colors=256, dither=Image.Dither.NONE)
            if candidate.convert('RGBA').tobytes() == rgba.tobytes():
                indexed = candidate
                native = indexed.getpalette('RGBA')
                targets = [mapping.get(tuple(native[i:i+4]), (0,0,0,0)) for i in range(0,len(native),4)]
                indexed.putpalette([v for c in targets for v in c], rawmode='RGBA')
                result = indexed.convert('RGBA')
            else:
                rgb = rgba.convert('RGB')
                candidate = rgb.quantize(colors=256, dither=Image.Dither.NONE)
                if candidate.convert('RGB').tobytes() != rgb.tobytes():
                    raise ValueError('native palette conversion was not lossless')
                indexed = candidate
                native = indexed.getpalette('RGB')
                rgb_targets = {c[:3]: t[:3]+(255 if t[3] else 0,) for c,t in mapping.items() if c[3]}
                targets = [rgb_targets.get(tuple(native[i:i+3]), (0,0,0,0)) for i in range(0,len(native),3)]
                indexed.putpalette([v for c in targets for v in c], rawmode='RGBA')
                result = indexed.convert('RGBA')
                result.putalpha(ImageChops.multiply(result.getchannel('A'), rgba.getchannel('A')))
        if indexed is None:
            result = Image.new('RGBA', rgba.size)
            channels = rgba.split()
            for color, target in mapping.items():
                if not target[3]: continue
                masks = [channel.point([255 if i == value else 0 for i in range(256)])
                         for channel, value in zip(channels, color)]
                mask = masks[0]
                for other in masks[1:]: mask = ImageChops.multiply(mask, other)
                result.paste(target, (0, 0, rgba.width, rgba.height), mask)
        result = Image.alpha_composite(Image.new('RGBA', result.size), result)
        result.info.update(unmatchedColors=unknown_colors, opaqueColors=opaque_colors,
                           unmatchedPixels=unknown_pixels, opaquePixels=opaque_pixels, ambiguousPixels=ambiguous_pixels,
                           unmatchedColorValues=unknown_values, opaqueColorValues={c for c in mapping if c[3]},
                           remapped=unknown_pixels <= .02 * opaque_pixels and not ambiguous_pixels)
    if unknown_pixels:
        _LOG.warning('radar palette %s: %d unknown pixels (%d colours) made transparent',
                     source, unknown_pixels, unknown_colors)
    return result
