"""Fork-only settled-camera source policy and spherical viewport coverage."""
import math
from pathlib import Path

from lib.radar_attention import WARM_HOLD_SEC
from lib.radar_geometry import EARTH_RADIUS_METERS, world_point, world_inverse

UP_ZOOM = 8
DOWN_ZOOM = 6
MIN_COVERAGE = .85
SWITCH_GUARD_SEC = 10
MANUAL_HOLD_SEC = WARM_HOLD_SEC


def choose(settled_zoom, showing=None, site_available=False, coverage=0.,
           last_switch_age=None, zoom_moved=0):
    """Choose a source without side effects. Safety takes precedence over dwell.

    ``showing`` is the published source, never a pending acquisition. Movement
    is measured from the zoom at the last committed automatic source switch.
    Unknown, stale or refused closest-site evidence means unavailable.
    """
    current = showing if showing in ('site', 'mosaic') else 'mosaic'
    if not site_available or not coverage >= MIN_COVERAGE:
        return 'mosaic'
    target = 'site' if settled_zoom >= UP_ZOOM else 'mosaic' if settled_zoom <= DOWN_ZOOM else current
    if (target != current and last_switch_age is not None and
            last_switch_age < SWITCH_GUARD_SEC and abs(zoom_moved) < 2):
        return current
    return target


def coverage_fraction(bounds, sites, radius_meters, rows=512):
    """Union of range discs as a fraction of the Web Mercator viewport.

    Integrate exact spherical longitude intervals over 512 screen-space rows.
    Overlaps count once; wrapped longitudes and high latitudes use the same
    earth radius as radar_geometry. No tile-margin area enters this calculation.
    """
    width = (bounds['e'] - bounds['w']) % 360
    if not sites or not width or bounds['n'] <= bounds['s']:
        return 0.
    top = world_point(bounds['n'], 0, 0)[1]
    bottom = world_point(bounds['s'], 0, 0)[1]
    discs = [(math.radians(s['lat']), (s['lon']-bounds['w']) % 360) for s in sites]
    cos_range = math.cos(radius_meters / EARTH_RADIUS_METERS)
    total = 0.
    for row in range(rows):
        lat = math.radians(world_inverse(0, top+(bottom-top)*(row+.5)/rows, 0)[0])
        intervals = []
        for site_lat, lon in discs:
            q = (cos_range-math.sin(lat)*math.sin(site_lat))/(math.cos(lat)*math.cos(site_lat))
            if q > 1:
                continue
            half = math.degrees(math.acos(max(-1., q)))
            for center in (lon-360, lon, lon+360):
                left, right = max(0., center-half), min(width, center+half)
                if right > left:
                    intervals.append((left, right))
        end = 0.
        for left, right in sorted(intervals):
            total += max(0., right-max(left, end))
            end = max(end, right)
    return min(1., total/(rows*width))


def source_preference(directory, record=None, now=0):
    """Read the manual lease using the existing presence clock, never view polls.

    The server materializes an expired lease as Auto before recording any new
    touch. The engine can therefore apply expiry even while the page is closed.
    A legacy preference without presence gets its first hold from its mtime.
    """
    root = Path(directory)
    try:
        marker = root / 'radar_source'
        pref = marker.read_text()[:128].strip()
        selected = marker.stat().st_mtime
    except (OSError, UnicodeError):
        pref, selected = 'auto', 0.
    if record is not None:
        # Accepted camera moves do not renew the source lease. Until the
        # debounce persists a new source, its transaction supplies the start.
        if record['source'] != pref or not selected:
            selected = record.get('acceptedAt', selected)
        pref = record['source']
    if pref not in ('mosaic', 'site'):
        return 'auto'
    try:
        touched = float((root / 'presence').read_text()[:128].split()[0])
        if not math.isfinite(touched):
            touched = 0.
    except (OSError, ValueError, IndexError):
        touched = 0.
    return 'auto' if now-max(touched, selected) >= MANUAL_HOLD_SEC else pref
