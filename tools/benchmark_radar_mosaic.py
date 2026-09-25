"""Offline native mosaic benchmark: an eight-frame loop across a tile grid.

Run: python tools/benchmark_radar_mosaic.py /path/to/n0b.bin /path/to/n0h.bin
Use --viewport --background --zooms 7 8 9 10 for the review workload.
Decode, QC and PNG encoding are outside the timer. --cold disables reuse.
--memory measures peak transient Python/NumPy allocations separately from timing.
"""
import argparse
import math
import inspect
from pathlib import Path
import platform
import statistics
import sys
import time
import tracemalloc

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.radar_level3 import Scan, decode, decode_n0h
from lib import radar_mosaic as mosaic
from lib.radar_palette import source_palette


def viewport_grid(z, camera_zoom=None, margin=0):
    """The emitter's 956x490 camera at Seattle, including scaled warm levels."""
    from lib.radar_geometry import world_point
    px, py = world_point(47.6, -122.3, z)
    scale = 2**(z-(z if camera_zoom is None else camera_zoom))
    width, height = 956*scale+margin*512, 490*scale+margin*512
    return [(x, y) for y in range(math.floor((py-height/2)/256), math.ceil((py+height/2)/256))
            for x in range(math.floor((px-width/2)/256), math.ceil((px+width/2)/256))]


def background_grids(z):
    """Engine margin-1 build, then its 2x2 same-source z±1 warm targets."""
    from lib.radar_geometry import world_point
    yield z, viewport_grid(z, margin=1)
    for warm_z in (z-1, z+1):
        if 7 <= warm_z <= 10:
            px, py = world_point(47.6, -122.3, warm_z)
            yield warm_z, [(x, y) for y in (int(py//256)-1, int(py//256))
                           for x in (int(px//256)-1, int(px//256))]


def viewport_benchmark(args, scan, palette):
    # Distinct physical sites; overlapping copies at one antenna hide off-disc cost.
    sites = [(47.68, -122.50, 196), (46.12, -122.43, 500),
             (47.12, -124.1, 80), (48.3, -121.0, 1000)]
    scans = [Scan(a, b, h, scan.elevation_deg, scan.vcp, scan.volume_ts,
                  scan.codes, scan.bearing_index) for a, b, h in sites]
    admission = 'cache_geometry' in inspect.signature(mosaic.render_mosaic).parameters
    print('z tiles mean_ms median_ms p95_ms foreground_hit_rate held_bytes peak_bytes')
    for z in args.zooms:
        mosaic.clear_geometry_cache()
        samples, hits, misses = [], 0, 0
        grid = viewport_grid(z)
        for iteration in range(args.iterations):
            for frame in range(args.frames):
                volumes = [Scan(s.lat, s.lon, s.height_m, s.elevation_deg, s.vcp,
                                s.volume_ts+120*frame, s.codes, s.bearing_index) for s in scans]
                before = mosaic.geometry_cache_info()
                for x, y in grid:
                    start = time.perf_counter()
                    image, _ = mosaic.render_mosaic(volumes, z, x, y, palette)
                    image.close()
                    samples.append((time.perf_counter()-start)*1000)
                after = mosaic.geometry_cache_info()
                hits += after['hits']-before['hits']
                misses += after['misses']-before['misses']
                if args.background and frame < args.frames-1:
                    for warm_z, warm_grid in background_grids(z):
                        for x, y in warm_grid:
                            options = dict(cache_geometry=False) if admission else {}
                            image, _ = mosaic.render_mosaic(volumes, warm_z, x, y, palette, **options)
                            image.close()
        held = mosaic.geometry_cache_info()['bytes']
        peak = 0
        if args.memory:
            # Measure the worst cold tile, with Pillow already imported; include
            # cold projections in the peak, even when they become retained.
            for x, y in grid:
                mosaic.clear_geometry_cache()
                tracemalloc.start()
                image, _ = mosaic.render_mosaic(scans, z, x, y, palette)
                image.close()
                peak = max(peak, tracemalloc.get_traced_memory()[1])
                tracemalloc.stop()
        samples.sort()
        print(z, len(grid), round(statistics.mean(samples), 3),
              round(statistics.median(samples), 3), round(samples[int(.95*(len(samples)-1))], 3),
              '%.2f%%' % (100*hits/max(1, hits+misses)), held, peak, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('n0b', type=Path)
    parser.add_argument('n0h', type=Path)
    parser.add_argument('--frames', type=int, default=8)
    parser.add_argument('--grid', type=int, default=2)
    parser.add_argument('--iterations', type=int, default=1, help='complete loop repetitions')
    parser.add_argument('--zooms', nargs='+', type=int, default=[8, 9, 10])
    parser.add_argument('--viewport', action='store_true', help='real 956x490 four-site viewport')
    parser.add_argument('--background', action='store_true', help='margin and adjacent zoom rounds between frames')
    parser.add_argument('--cold', action='store_true')
    parser.add_argument('--memory', action='store_true')
    args = parser.parse_args()
    scan = mosaic.quality_control(decode(args.n0b.read_bytes(), speckle_dbz=15), decode_n0h(args.n0h.read_bytes()))
    scans = []
    for index in range(4):
        codes = scan.codes.copy()
        if index < 3:
            codes[:, (np.arange(scan.gates) % 4) > index] = 1
        scans.append(Scan(scan.lat, scan.lon, scan.height_m + index*200,
            scan.elevation_deg, scan.vcp, scan.volume_ts, codes, scan.bearing_index))
    palette = source_palette('iem-nexrad-n0b')
    print(platform.platform(), platform.machine(), 'Python', platform.python_version())
    if args.viewport:
        viewport_benchmark(args, scan, palette)
        return
    print('Sites z median_ms p95_ms hit_rate cache_MiB')
    clear = getattr(mosaic, 'clear_geometry_cache', lambda: None)
    for count, candidates in ((1, [scan]), (4, scans)):
        clear()
        values = {z: [] for z in args.zooms}
        for _ in range(args.iterations):
            for frame in range(args.frames):
                # Different volumes, same physical geometry and azimuth table.
                volumes = [Scan(s.lat, s.lon, s.height_m, s.elevation_deg, s.vcp,
                    s.volume_ts+frame*120, s.codes, s.bearing_index) for s in candidates]
                for z in values:
                    cx = int((scan.lon+180)/360*2**z)
                    cy = int((1-math.asinh(math.tan(math.radians(scan.lat)))/math.pi)/2*2**z)
                    for y in range(cy-args.grid//2, cy-args.grid//2+args.grid):
                        for x in range(cx-args.grid//2, cx-args.grid//2+args.grid):
                            if args.cold:
                                clear()
                            start = time.perf_counter()
                            image, _ = mosaic.render_mosaic(volumes, z, x, y, palette)
                            image.close()
                            values[z].append((time.perf_counter()-start)*1000)
        info = getattr(mosaic, 'geometry_cache_info', lambda: dict(hits=0, misses=0, bytes=0))()
        rate = info['hits']/max(1, info['hits']+info['misses'])
        for z, samples in values.items():
            samples.sort()
            print(count, z, round(statistics.median(samples), 3),
                  round(samples[int(.95*(len(samples)-1))], 3),
                  '%.1f%%' % (100*rate), round(info['bytes']/1024**2, 2))
    if args.memory:
        # Worst supported sampling: four overlapping sites, cold z7 geometry.
        clear()
        z = 7
        x = int((scan.lon+180)/360*2**z)
        y = int((1-math.asinh(math.tan(math.radians(scan.lat)))/math.pi)/2*2**z)
        tracemalloc.start()
        image, _ = mosaic.render_mosaic(scans, z, x, y, palette)
        image.close()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        retained = mosaic.geometry_cache_info()['bytes']
        print('Cold z7 peak MiB', round(peak/1024**2, 2), 'retained MiB',
              round(retained/1024**2, 2), 'transient MiB', round((peak-retained)/1024**2, 2))


if __name__ == '__main__':
    main()
