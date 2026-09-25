"""Offline native mosaic render benchmark; no downloads or application startup.

Run: python tools/benchmark_radar_mosaic.py /path/to/n0b.bin /path/to/n0h.bin
Cold includes geometry; warm reuses geometry. Decode, QC and PNG encoding are
outside the timer, matching the existing native render_tile measurement.
"""
import argparse
import math
from pathlib import Path
import platform
import statistics
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.radar_level3 import Scan, decode, decode_n0h
from lib.radar_mosaic import _geometry, quality_control, render_mosaic
from lib.radar_palette import source_palette


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('n0b', type=Path)
    parser.add_argument('n0h', type=Path)
    parser.add_argument('--iterations', type=int, default=50)
    args = parser.parse_args()
    scan = quality_control(decode(args.n0b.read_bytes(), speckle_dbz=15), decode_n0h(args.n0h.read_bytes()))
    # Deliberately synthetic nearby sites: four overlapping discs and a
    # progressive missing-gate pattern exercise fallback through all candidates.
    scans = []
    for index in range(4):
        codes = scan.codes.copy()
        if index < 3:
            codes[:, (np.arange(scan.gates) % 4) > index] = 1
        scans.append(Scan(scan.lat, scan.lon, scan.height_m + index*200,
            scan.elevation_deg, scan.vcp, scan.volume_ts, codes, scan.bearing_index))
    palette = source_palette('iem-nexrad-n0b')
    print(platform.platform(), platform.machine(), 'Python', platform.python_version())
    print('Sites z cold_median_ms warm_median_ms warm_p95_ms')
    for count, candidates in ((1, [scan]), (4, scans)):
        for z in (8, 9, 10):
            x = int((-121.985+180)/360*2**z)
            y = int((1-math.asinh(math.tan(math.radians(47.74)))/math.pi)/2*2**z)
            timings = {}
            for mode in ('cold', 'warm'):
                values = []
                for _ in range(args.iterations):
                    if mode == 'cold':
                        _geometry.cache_clear()
                    start = time.perf_counter()
                    image, _ = render_mosaic(candidates, z, x, y, palette)
                    image.close()
                    values.append((time.perf_counter()-start)*1000)
                timings[mode] = sorted(values)
            print(count, z, *(round(v, 3) for v in (statistics.median(timings['cold']),
                statistics.median(timings['warm']), timings['warm'][int(.95*(args.iterations-1))])))


if __name__ == '__main__':
    main()
