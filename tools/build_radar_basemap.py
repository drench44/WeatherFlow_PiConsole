#!/usr/bin/env python3
"""Build the offline Natural Earth plate data; see tools/RADAR_BASEMAP.md.

Only this script uses Shapely/pyshp. Runtime reads independently compressed 1°
binary cells through a fixed-width binary index (no global geometry allocation).
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import urllib.request
import zipfile
import zlib

import shapefile
from shapely.geometry import shape, box, Polygon
from shapely.ops import unary_union
from shapely import make_valid

LAYERS = [('physical', 'land'), ('physical', 'coastline'), ('physical', 'lakes'),
          ('cultural', 'admin_0_boundary_lines_land'),
          ('cultural', 'admin_1_states_provinces_lines'),
          ('cultural', 'roads'), ('cultural', 'roads_north_america')]
QUANT = 1000
TOL = .003
MAGIC = b'NEBM0001'


def parts(g, kind):
    if g.is_empty:
        return
    if g.geom_type == kind:
        yield g
    elif hasattr(g, 'geoms'):
        for p in g.geoms:
            yield from parts(p, kind)


def significant(p):
    # One square pixel in the z4 Mercator map; remove subpixel islands/lakes.
    return p.area / max(.087, math.cos(math.radians(p.centroid.y))) >= (360 / 4096) ** 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('lib/data/radar-natural-earth.bin'))
    parser.add_argument('--download', action='store_true', help='fetch missing source zips (build time only)')
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    cells = defaultdict(list)
    sources = []

    def records(category, name):
        filename = 'ne_10m_' + name
        path = args.cache / (filename + '.zip')
        url = f'https://naturalearth.s3.amazonaws.com/10m_{category}/{filename}.zip'
        if not path.exists() and args.download:
            print('Download', url, flush=True)
            with urllib.request.urlopen(url, timeout=120) as response:
                path.write_bytes(response.read())
        raw = path.read_bytes()
        sources.append(dict(layer=filename, url=url, sha256=hashlib.sha256(raw).hexdigest()))
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            reader = shapefile.Reader(**{ext: io.BytesIO(archive.read(filename + '.' + ext))
                                         for ext in ('shp', 'shx', 'dbf')}, encoding='utf-8')
            for rec in reader.iterShapeRecords():
                if rec.shape.shapeType != shapefile.NULL:
                    yield rec.record.as_dict(), shape(rec.shape.__geo_interface__)

    def coords(seq, x, y):
        result = []
        for lon, lat in seq:
            p = [round((lon - x) * QUANT), round((lat - y) * QUANT)]
            if not result or result[-1] != p:
                result.append(p)
        return result

    def add(g, layer, polygon=False):
        if g.is_empty:
            return
        w, s, e, n = g.bounds
        for y in range(max(-86, math.floor(s)), min(86, math.ceil(n))):
            for x in range(max(-180, math.floor(w)), min(180, math.ceil(e))):
                clipped = g.intersection(box(x, y, x+1, y+1))
                for p in parts(clipped, 'Polygon' if polygon else 'LineString'):
                    if polygon:
                        rings = [coords(p.exterior.coords, x, y)] + [coords(r.coords, x, y) for r in p.interiors]
                        rings = [r for r in rings if len(r) >= 4]
                        if rings:
                            cells[(x, y)].append([layer, rings])
                    else:
                        line = coords(p.coords, x, y)
                        if len(line) >= 2:
                            cells[(x, y)].append([layer, line])

    land = []
    for _, g in records(*LAYERS[0]):
        land.extend(p.simplify(TOL, preserve_topology=True) for p in parts(g, 'Polygon') if significant(p))
    print('Union retained land', len(land), flush=True)
    land = unary_union(land)
    # Ocean via land complement guarantees complete mid-ocean cells, including
    # both sides of ±180. Interior rings keep retained islands as paper ground.
    from shapely.prepared import prep
    prepared = prep(land)
    for y in range(-86, 86):
        for x in range(-180, 180):
            cell = box(x, y, x+1, y+1)
            if not prepared.intersects(cell):
                cells[(x, y)].append([0, [[[0,0],[QUANT,0],[QUANT,QUANT],[0,QUANT],[0,0]]]])
            elif not prepared.contains(cell):
                add(cell.difference(land), 0, True)
    print('Ocean cells', len(cells), flush=True)
    for category, name in LAYERS[1:]:
        count = 0
        for attrs, g in records(category, name):
            if name == 'lakes':
                for p in parts(make_valid(g), 'Polygon'):
                    if significant(p):
                        p = p.simplify(TOL, preserve_topology=True)
                        add(p, 1, True)
                        add(p.boundary, 2)
            elif name == 'coastline':
                for p in parts(g, 'LineString'):
                    if p.is_ring and not significant(Polygon(p)):
                        continue
                    add(p.simplify(TOL), 2)
            else:
                if name.startswith('roads'):
                    a = {k.lower(): str(v).lower() for k,v in attrs.items()}
                    if name == 'roads_north_america':
                        if a.get('type') not in ('freeway','tollway','primary') or a.get('class') not in ('interstate','federal','state'):
                            continue
                    elif a.get('type') not in ('major highway',) or float(a.get('scalerank', 99)) > 6:
                        continue
                add(g.simplify(TOL), {'admin_0_boundary_lines_land':3,
                    'admin_1_states_provinces_lines':4, 'roads':5, 'roads_north_america':6}[name])
            count += 1
        print(name, count, flush=True)
    # Deterministic order and compression; offset 0 length 0 means bare land.
    data = bytearray(); index = bytearray(); counts = Counter()
    for y in range(-90,90):
        for x in range(-180,180):
            entries = cells.get((x,y), [])
            for layer, _ in entries: counts[layer] += 1
            if entries == [[0, [[[0,0],[QUANT,0],[QUANT,QUANT],[0,QUANT],[0,0]]]]]:
                index.extend(struct.pack('<II', 1, 0))  # full ocean, no payload
                continue
            packed = bytearray()
            for layer, geometry in entries:
                rings = geometry if layer < 2 else [geometry]
                packed.extend(struct.pack('<BH',layer,len(rings)))
                for ring in rings:
                    packed.extend(struct.pack('<H',len(ring)))
                    for dx,dy in ring:
                        packed.extend(struct.pack('<HH',dx,dy))
            raw = zlib.compress(packed,9) if packed else b''
            index.extend(struct.pack('<II', len(data) if raw else 0, len(raw))); data.extend(raw)
    manifest = json.dumps(dict(source='Basemap: Natural Earth', quant=QUANT,
        toleranceDegrees=TOL, sources=sources), separators=(',',':')).encode()
    packed_index = zlib.compress(index,9)
    artifact = (MAGIC + struct.pack('<I',len(manifest)) + manifest +
                struct.pack('<I',len(packed_index)) + packed_index + data)
    if len(artifact) >= 5_000_000:
        raise SystemExit(f'STOP: artifact is {len(artifact):,} bytes; refusing to write >=5 MB')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(artifact)
    print(f'{args.output}: {len(artifact):,} bytes; cell features {dict(counts)}', flush=True)


if __name__ == '__main__':
    main()
