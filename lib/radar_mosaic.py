"""Immutable native frame inputs and lowest-beam, categorical-QC mosaics.

Terrain at a pixel is common to every candidate: subtracting it from all
beam altitudes leaves their order unchanged. No DEM or MRMS mask is needed.
"""
import hashlib
import json
import math
from functools import lru_cache

import numpy as np

from lib.radar_level3 import (Scan, NATIVE_REVISION, EARTH_RADIUS_M,
    EFFECTIVE_RADIUS_M, GATE_METERS, _tile_lonlat, colour_table)


def mosaic_key(pairs, revision=NATIVE_REVISION):
    """Pairs are (site, exact N0B volume second, has N0H), never arrival order."""
    wire = json.dumps([revision, sorted(pairs)], separators=(',', ':'))
    return 'M' + hashlib.sha256(wire.encode()).hexdigest()[:24]


def quality_control(scan, classification):
    """Apply HCA to N0B gate centres using each product's actual azimuth table.

    Code 1 is our existing no-data/range-folded sentinel. Biological becomes
    code 0, a measured clear gate. Missing HCA bearings/range retain N0B.
    """
    if classification is None:
        return scan
    if scan.volume_ts != classification.volume_ts:
        raise ValueError('N0H volume differs from N0B')
    codes = scan.codes.copy()
    # Circular mean handles the ray straddling north without assuming row//2.
    bearings = np.arange(3600) * np.pi / 1800
    valid = scan.bearing_index >= 0
    rows = scan.bearing_index[valid]
    sine = np.bincount(rows, weights=np.sin(bearings[valid]), minlength=scan.radials)
    cosine = np.bincount(rows, weights=np.cos(bearings[valid]), minlength=scan.radials)
    bearing = (np.degrees(np.arctan2(sine, cosine)) % 360 * 10).round().astype(int) % 3600
    hrow = classification.bearing_index[bearing]
    # Both products use 250 m slant gates at the same lowest tilt.
    if abs(scan.elevation_deg - classification.elevation_deg) > .01:
        raise ValueError('N0H elevation differs from N0B')
    gates = min(scan.gates, classification.gates)
    classes = classification.codes[np.maximum(hrow, 0), :gates]
    target = codes[:, :gates]
    present = hrow[:, None] >= 0
    target[present & ((classes == 20) | (classes == 150))] = 1
    # Never turn N0B range folding into a valid clear measurement.
    target[present & (classes == 10) & (target != 1)] = 0
    return Scan(scan.lat, scan.lon, scan.height_m, scan.elevation_deg, scan.vcp,
                scan.volume_ts, codes, scan.bearing_index)


@lru_cache(maxsize=3)
def _geometry(sites, z, x, y, size, radius):
    """Bounded geometry cache: distances and rank only, no scan data retained."""
    lat, lon = _tile_lonlat(z, x, y, size)
    la, lo = np.radians(lat)[:, None], np.radians(lon)[None, :]
    ranges, heights = [], []
    for a, b, height, elevation in sites:
        a, b, elevation = map(math.radians, (a, b, elevation))
        hav = np.sin((la-a)/2)**2 + math.cos(a)*np.cos(la)*np.sin((lo-b)/2)**2
        ground = 2*EARTH_RADIUS_M*np.arcsin(np.minimum(1, np.sqrt(hav)))
        central = ground/EFFECTIVE_RADIUS_M
        slant = EFFECTIVE_RADIUS_M*np.sin(central)/np.cos(elevation+central)
        altitude = height + (EFFECTIVE_RADIUS_M + slant*np.sin(elevation))/np.cos(central)-EFFECTIVE_RADIUS_M
        altitude[ground > radius] = np.inf
        ranges.append(slant.ravel())
        heights.append(altitude.ravel())
    order = np.argsort(np.asarray(heights), axis=0, kind='stable').astype(np.uint8)
    covered = np.isfinite(heights)
    return np.asarray(ranges), order, covered


def mosaic_codes(scans, z, x, y, size=256, radius=230000):
    """Select once, including clear. Only unresolved pixels get gate lookups."""
    result = np.zeros(size*size, np.uint8)
    if not scans:
        return result.reshape(size, size)
    sites = tuple((s.lat, s.lon, s.height_m, s.elevation_deg) for s in scans)
    ranges, order, covered = _geometry(sites, z, x, y, size, radius)
    lat, lon = _tile_lonlat(z, x, y, size)
    lat, lon = np.radians(lat), np.radians(lon)
    unresolved = np.ones(size*size, bool)
    for rank in range(len(scans)):
        for index, scan in enumerate(scans):
            pixels = np.flatnonzero(unresolved & (order[rank] == index) & covered[index])
            if not pixels.size:
                continue
            gates = (ranges[index, pixels]/GATE_METERS).astype(np.int32)
            inside = gates < scan.gates
            pixels, gates = pixels[inside], gates[inside]
            la, dl, a = lat[pixels//size], lon[pixels % size]-math.radians(scan.lon), math.radians(scan.lat)
            bearing = np.degrees(np.arctan2(np.sin(dl)*np.cos(la),
                math.cos(a)*np.sin(la)-math.sin(a)*np.cos(la)*np.cos(dl))) % 360
            rows = scan.bearing_index[(bearing*10).astype(np.int32) % 3600]
            values = scan.codes[np.maximum(rows, 0), gates]
            good = (rows >= 0) & (values != 1)
            result[pixels[good]] = values[good]
            unresolved[pixels[good]] = False
        if not unresolved.any():
            break
    return result.reshape(size, size)


def render_mosaic(scans, z, x, y, palette, radius=230000):
    from PIL import Image
    factor = 2 if z < 8 else 1
    codes = mosaic_codes(scans, z, x, y, 256*factor, radius)
    if factor > 1:
        codes = codes.reshape(256, factor, 256, factor).max(axis=(1, 3))
    slots, colours = colour_table(palette)
    pixels = slots[codes]
    image = Image.fromarray(pixels, 'P')
    flat = [v for colour in colours for v in colour[:3]]
    image.putpalette(flat + [0]*(768-len(flat)))
    image.info['transparency'] = bytes(c[3] for c in colours)
    return image, int(np.count_nonzero(pixels))
