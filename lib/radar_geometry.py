"""Shared Web Mercator coordinates for radar crops and offline basemap paths."""
import math

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
