"""Offline native mosaic benchmark: an eight-frame loop across a tile grid.

Run: python tools/benchmark_radar_mosaic.py /path/to/n0b.bin /path/to/n0h.bin
Decode, QC and PNG encoding are outside the timer. --cold disables reuse.
--memory measures peak transient Python/NumPy allocations separately from timing.
"""
import argparse
import math
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('n0b', type=Path)
    parser.add_argument('n0h', type=Path)
    parser.add_argument('--frames', type=int, default=8)
    parser.add_argument('--grid', type=int, default=2)
    parser.add_argument('--iterations', type=int, default=1, help='complete loop repetitions')
    parser.add_argument('--zooms', nargs='+', type=int, default=[8, 9, 10])
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
