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
REMAP_REVISION = "native-v5.2-1"
SMOOTH_REVISION = "field-bilinear-2x-box-256-v61-1"
_RGB_TOLERANCE = 3  # Euclidean RGB distance; alpha is coverage, not intensity.
_LOG = logging.getLogger(__name__)


# v3 (2026-09-25): the greens descend in lightness without reversing (L* 63 at
# 15 dBZ to 41 at 32.5, chroma rising), with a deliberate step at 25 dBZ, so
# light, moderate and heavier rain separate at a glance (10 dBZ darkened just
# enough to clear 2:1 on the #EBE6DB paper plate). v2's greens saw-toothed
# between L* 58 and 62: at native resolution 15, 22.5 and 27.5 dBZ read as one
# sheet. Every stop still clears 2:1 on paper and 3:1 on night. 35+ unchanged.
_RADAR_RAMP = dict(id='almanac-reflectivity-v3', floorDbz=10, bands=[
    dict(lo=lo, hi=hi, start=start, end=end) for lo, hi, start, end in (
        (10,20,'#89AB92','#43A05D'), (20,25,'#43A05D','#209143'),
        (25,35,'#088A34','#11672D'), (35,40,'#C79C14','#B0870D'),
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


# What the panel draws. The ramp and its LUT stay the designed 10-75 dBZ scale
# (echo counting and contrast checks use them); below DISPLAY_FLOOR_DBZ nothing
# is drawn. Measured on the panel on a dry afternoon (2026-09-24, 0 % forecast):
# 49 % of drawn site pixels were the 5-10 dBZ clear-air grey and 32 % were
# 10-15 dBZ, insects, birds and ground clutter. Light rain starts above it.
DISPLAY_FLOOR_DBZ = 15


def _clip_ramp(ramp, floor):
    bands = []
    for band in ramp['bands']:
        if band['hi'] <= floor:
            continue
        if band['lo'] < floor:
            start, end = (bytes.fromhex(band[k][1:]) for k in ('start', 'end'))
            t = (floor - band['lo']) / (band['hi'] - band['lo'])
            band = dict(band, lo=floor, start='#%02X%02X%02X' % tuple(round(a+(b-a)*t) for a, b in zip(start, end)))
        bands.append(band)
    return dict(ramp, floorDbz=floor, bands=bands)


_RADAR_DISPLAY_RAMP = _clip_ramp(_RADAR_RAMP, DISPLAY_FLOOR_DBZ)
# A stop with alpha 0 explicitly suppresses its bin (see remap); below the first
# stop is transparent too. (Site mode's grey 5-10 dBZ clear-air band went with
# the floor; its palette and legend were removed with the v3 ramp.)
_RADAR_DISPLAY_LUT = tuple((dbz, rgba if dbz >= DISPLAY_FLOOR_DBZ else rgba[:3] + (0,)) for dbz, rgba in _RADAR_LUT)


def source_palette(source):
    """The drawing palette for every source: the designed LUT above the display floor."""
    return _RADAR_DISPLAY_LUT


WEATHER_FLOOR_DBZ = 25  # rain, not insects: the Puget Sound night sky is thick with 10-20 dBZ that never falls


def weather_pixels(image, floor_dbz=WEATHER_FLOOR_DBZ):
    """Count remapped pixels at or above the floor, excluding clear air.

    Measured on the panel (2026-09-18, dry night): the KATX frame held 315,000
    pixels at 10 dBZ, 6,900 at 20 and none at 30, while real showers in the
    mosaic the evening before showed 10,000 at 30. Reflectivity below 25 dBZ
    at night is biology and clutter far more often than rain. Native opacity
    is coverage, not reflectivity.
    """
    colors = {rgba[:3] for dbz, rgba in _RADAR_LUT if dbz >= floor_dbz}
    with image.convert('RGBA') as rgba:
        return sum(n for n, color in rgba.getcolors(rgba.width * rgba.height) or ()
                   if color[3] and color[:3] in colors)

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
        targets.append(palette[stop][1][:3]+(round(color[3]*palette[stop][1][3]/255),) if stop>=0 and palette[stop][1][3] else (0,0,0,0))
    result=image.copy(); result.info.pop('transparency',None)
    result.putpalette([v for c in targets for v in c],rawmode='RGBA');result=result.convert('RGBA')
    opaque={actual[i] for n,i in colors if actual[i][3]}
    result.info.update(unmatchedColors=0,opaqueColors=len(opaque),unmatchedPixels=0,
                       opaquePixels=sum(n for n,i in colors if actual[i][3]),ambiguousPixels=0,
                       unmatchedColorValues=set(),opaqueColorValues=opaque,remapped=True)
    return result


def remap(image, source, palette):
    """Return RGBA using ordered (dBZ floor, RGBA) steps; top is open-ended.

    Input coverage alpha is multiplied by the selected target alpha, rounded once;
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
        target_opacities = {}
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
                    target = palette[index][1][:3] + (round(color[3]*palette[index][1][3]/255),)
                    target_opacities[color] = palette[index][1][3]
            mapping[color] = target
        # Verify the adaptive RGBA palette before using it: Pillow's octree
        # can merge even fewer than 256 native colours. Exact RGB median-cut
        # plus rounded coverage alpha handles those tiles without loss.
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
                rgb_targets = {c[:3]: t[:3]+(target_opacities[c] if t[3] else 0,)
                               for c,t in mapping.items() if c[3]}
                targets = [rgb_targets.get(tuple(native[i:i+3]), (0,0,0,0)) for i in range(0,len(native),3)]
                indexed.putpalette([v for c in targets for v in c], rawmode='RGBA')
                result = indexed.convert('RGBA')
                target_alpha, coverage = result.getchannel('A'), rgba.getchannel('A')
                alpha = ImageChops.multiply(target_alpha, coverage)
                # ImageChops.multiply truncates; translucent stops require round.
                for opacity in {t[3] for t in rgb_targets.values()} - {0, 255}:
                    mask = target_alpha.point([255 if v == opacity else 0 for v in range(256)])
                    alpha.paste(coverage.point([round(v*opacity/255) for v in range(256)]), (0, 0), mask)
                result.putalpha(alpha)
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


def smooth_remap(image, source, palette):
    """2x pixel-centred bilinear field, box reduced to native size, then LUT.

    Zero coverage / unknown / suppressed gates contribute no intensity. Normalize
    the weighted field by valid coverage; interpolate coverage independently. A
    zero-support output stays transparent. Reduction averages weighted field and
    coverage, never RGB or already-quantized legend colours.
    Edge samples clamp inside this native tile (no invented neighbouring gates).
    All raster arithmetic runs in Pillow; no Python pixel loop or numpy required.
    """
    from PIL import Image, ImageChops, ImageMath
    calculate = getattr(ImageMath, 'unsafe_eval', None) or ImageMath.eval
    mapped = remap(image, source, palette)
    # Half-dBZ linear codes preserve verified N0B/MRMS indices. The same native
    # inverse rules apply to RGBA and RainViewer; no intensity is inferred from
    # an already-rendered legend colour. Preserve each source's full range.
    offset = 33 if source == 'iem-nexrad-n0b' else 32
    numeric_palette = [(i / 2 - offset, (i, i, i, 255)) for i in range(256)]
    numeric = remap(image, source, numeric_palette)
    coverage = ImageChops.multiply(numeric.getchannel('A'),
                                  mapped.getchannel('A').point([0] + [255]*255))
    size = (image.width*2, image.height*2)
    weight = coverage.convert('F')
    field = numeric.getchannel('R').convert('F')
    # Fixed, internal expressions, compatible with the Pi's Pillow as well.
    weighted = calculate('field * weight', field=field, weight=weight)
    weighted = weighted.resize(size, Image.Resampling.BILINEAR)
    weight = weight.resize(size, Image.Resampling.BILINEAR)
    # Area-average each 2x2 group before normalization/quantization. This keeps
    # the 2x field's softened gate edges in a native-sized tile without RGB
    # mixtures, extra page pixels or a half-pixel directional shift.
    weighted = weighted.resize(image.size, Image.Resampling.BOX)
    weight = weight.resize(image.size, Image.Resampling.BOX)
    codes = calculate('convert(value / (weight + (weight == 0)), "L")',
                          value=weighted, weight=weight)
    floors = [floor for floor, _ in palette]
    targets = []
    for i in range(256):
        stop = bisect_right(floors, i/2-offset)-1
        targets.append(palette[stop][1] if stop >= 0 else (0,0,0,0))
    result = codes.convert('P')
    result.putpalette([v for color in targets for v in color], rawmode='RGBA')
    result = result.convert('RGBA')
    alpha = calculate('convert(coverage * opacity / 255 + 0.5, "L")',
                          coverage=weight, opacity=result.getchannel('A').convert('F'))
    result.putalpha(alpha)
    result.paste((0,0,0,0), mask=alpha.point([255]+[0]*255))
    result.info.update(mapped.info, smooth=True)
    mapped.close(); numeric.close()
    return result
