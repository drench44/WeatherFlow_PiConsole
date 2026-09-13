"""Shared Web Mercator coordinates for radar crops and offline basemap paths."""
import math
import re

MAX_LAT = 85.05112878


def world_point(lat, lon, zoom):
    lat = max(-MAX_LAT, min(MAX_LAT, lat))
    world = 256 * 2 ** zoom
    return ((lon + 180) / 360 * world,
            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * world)


def plate_point(lat, lon, zoom, left, top):
    """Mirror the compositor's int(tile origin - crop origin) paste offsets.

    Keep local subpixel coordinates until the SVG's final integer quantization.
    Longitude is deliberately unwrapped, just like the viewport tile loop.
    """
    x, y = world_point(lat, lon, zoom)
    tx, ty = math.floor(x / 256), math.floor(y / 256)
    return (int(tx * 256 - left) + (x - tx * 256),
            int(ty * 256 - top) + (y - ty * 256))


def world_inverse(x, y, zoom):
    """Inverse of world_point; longitude remains deliberately unwrapped."""
    world = 256 * 2 ** zoom
    return (math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / world)))),
            x / world * 360 - 180)


def parse_center(value):
    """Strict loopback marker grammar; station/invalid mean no override."""
    if not re.fullmatch(r'-?\d{1,3}(\.\d+)?,-?\d{1,3}(\.\d+)?', value, re.ASCII):
        return None
    lat, lon = map(float, value.split(','))
    return (lat, lon) if -MAX_LAT <= lat <= MAX_LAT and -180 <= lon <= 180 else None
