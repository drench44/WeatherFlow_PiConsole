"""Offline Natural Earth → class-only SVG. Standard library only at runtime."""
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import zlib

from lib.radar_geometry import plate_point, world_point

DATA_PATH = Path(__file__).with_name('data') / 'radar-natural-earth.bin'
CLASSES = ('bm-ocean', 'bm-lake', 'bm-coast', 'bm-admin0', 'bm-admin1', 'bm-road', 'bm-road')


def clip_polygon(points, size):
    """Sutherland–Hodgman, against the four edges of the plate."""
    for axis, bound, sign in ((0,0,1), (0,size,-1), (1,0,1), (1,size,-1)):
        if not points:
            break
        output = []
        a = points[-1]
        for b in points:
            ai, bi = sign*(a[axis]-bound) >= 0, sign*(b[axis]-bound) >= 0
            if ai != bi:
                t = (bound-a[axis]) / (b[axis]-a[axis])
                p = [a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1])]
                p[axis] = bound
                output.append(tuple(p))
            if bi:
                output.append(b)
            a = b
        points = output
    return points


def clip_segment(a, b, size):
    """Liang–Barsky; None for a segment wholly outside the plate."""
    dx, dy = b[0]-a[0], b[1]-a[1]
    lo, hi = 0., 1.
    for p, q in ((-dx,a[0]), (dx,size-a[0]), (-dy,a[1]), (dy,size-a[1])):
        if p == 0:
            if q < 0:
                return None
        elif p < 0:
            lo = max(lo, q/p)
        else:
            hi = min(hi, q/p)
        if lo > hi:
            return None
    return ((a[0]+lo*dx, a[1]+lo*dy), (a[0]+hi*dx, a[1]+hi*dy))


def clip_line(points, size):
    result, current = [], []
    for a, b in zip(points, points[1:]):
        segment = clip_segment(a, b, size)
        if segment is None:
            if current: result.append(current)
            current = []
        else:
            a, b = segment
            if current and current[-1] == a:
                current.append(b)
            else:
                if current: result.append(current)
                current = [a,b]
    if current: result.append(current)
    return result


def simplify(points, tolerance=1.):
    """Iterative Douglas–Peucker; bounded stack, including closed polygon rings."""
    if len(points) < 3:
        return points
    keep = {0,len(points)-1}
    stack = [(0,len(points)-1)]
    while stack:
        start,end = stack.pop()
        a,b = points[start],points[end]
        dx,dy = b[0]-a[0],b[1]-a[1]
        length = dx*dx+dy*dy
        maximum, index = tolerance*tolerance, None
        for i in range(start+1,end):
            p = points[i]
            t = max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/length)) if length else 0
            dist = (p[0]-a[0]-t*dx)**2+(p[1]-a[1]-t*dy)**2
            if dist > maximum:
                maximum,index = dist,i
        if index is not None:
            keep.add(index); stack.extend(((start,index),(index,end)))
    return [points[i] for i in sorted(keep)]


@lru_cache(maxsize=1)
def _index(path):
    with open(path,'rb') as stream:
        if stream.read(8) != b'NEBM0001':
            raise ValueError('invalid basemap artifact')
        length = struct.unpack('<I',stream.read(4))[0]
        metadata = json.loads(stream.read(length))
        index_length = struct.unpack('<I',stream.read(4))[0]
        index = zlib.decompress(stream.read(index_length))
        if len(index) != 360*180*8:
            raise ValueError('truncated basemap index')
        return metadata['quant'], index, stream.tell()


@lru_cache(maxsize=256)
def _cell(path, x, y):
    quant, index, start = _index(path)
    offset, length = struct.unpack_from('<II',index,((y+90)*360+x+180)*8)
    if not length:
        return ((0, (((0,0),(quant,0),(quant,quant),(0,quant),(0,0)),)),) if offset == 1 else ()
    with open(path,'rb') as stream:
        stream.seek(start+offset)
        raw = zlib.decompress(stream.read(length))
    entries, pos = [], 0
    while pos < len(raw):
        layer, count = struct.unpack_from('<BH',raw,pos); pos += 3
        rings = []
        for _ in range(count):
            length = struct.unpack_from('<H',raw,pos)[0]; pos += 2
            ring = list(struct.iter_unpack('<HH',raw[pos:pos+length*4])); pos += length*4
            rings.append(ring)
        entries.append((layer,rings if layer < 2 else rings[0]))
    return entries


def render(lat, lon, zoom, bounds, size=480, data_path=None):
    """Select only intersecting 1° cells, unwrap them, project, clip and simplify."""
    path = str(data_path or DATA_PATH)
    quant, _, _ = _index(path)
    cx,cy = world_point(lat,lon,zoom)
    left,top = cx-size/2,cy-size/2
    # Derive unwrapped longitudes from the crop itself, including -180 windows.
    world = 256*2**zoom
    west,east = left/world*360-180,(left+size)/world*360-180
    layers = [[] for _ in CLASSES]

    def projected(points, x, y):
        return [plate_point(y+dy/quant,x+dx/quant,zoom,left,top) for dx,dy in points]

    def path_data(points, closed=False):
        points = [(round(x),round(y)) for x,y in points]
        points = [p for i,p in enumerate(points) if not i or p != points[i-1]]
        if closed and points and points[0] != points[-1]: points.append(points[0])
        points = simplify(points)
        if len(set(points)) < (3 if closed else 2):
            return ''
        if closed and not sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points,points[1:])):
            return ''
        return 'M'+'L'.join(f'{x},{y}' for x,y in points)+('Z' if closed else '')

    for y in range(max(-86,math.floor(bounds['s'])),min(86,math.ceil(bounds['n']))):
        for x in range(math.floor(west),math.ceil(east)):
            for layer, geometry in _cell(path,(x+180)%360-180,y):
                if layer < 2:
                    rings = [path_data(clip_polygon(projected(r,x,y),size),True) for r in geometry]
                    if rings and rings[0]:
                        layers[layer].append(''.join(rings))
                else:
                    for line in clip_line(projected(geometry,x,y),size):
                        d = path_data(line)
                        if d: layers[layer].append(d)
    # A single compound path per water class prevents alpha seams between cells.
    # Even-odd fill is supplied by the semantic CSS rule (island/lake holes).
    paths = []
    for cls, pieces in zip(CLASSES,layers):
        if pieces:
            paths.append(f'<path class="{cls}" d="{"".join(pieces)}"/>')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">' + ''.join(paths) + '</svg>\n'
    return svg, dict(coast=bool(layers[0] or layers[1]),
        roads='dense' if layers[6] else 'sparse' if layers[5] else 'none')


def ensure(ctx, radar_dir):
    """Reuse an existing viewport even off-tab; create only on a viewed cycle."""
    identity = ctx['identity']
    target = Path(radar_dir) / 'basemap' / (identity+'.svg')
    if target.is_file():
        # Metadata is embedded as a JSON comment so the SVG is the only cache file.
        with target.open() as stream:
            metadata = json.loads(stream.readline()[4:-4])
    elif ctx['viewed']:
        svg, metadata = render(ctx['center']['lat'],ctx['center']['lon'],ctx['zoom'],ctx['bounds'])
        target.parent.mkdir(parents=True,exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w',dir=target.parent,prefix=identity+'.tmp.',delete=False) as stream:
                temp = stream.name
                stream.write('<!--'+json.dumps(metadata,separators=(',',':'))+'-->\n'+svg)
            os.replace(temp,target)
        finally:
            if temp and os.path.exists(temp): os.unlink(temp)
    else:
        return None
    return dict(hash=identity,url='radar/basemap/'+identity+'.svg',**metadata)
