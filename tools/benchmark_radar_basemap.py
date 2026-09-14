#!/usr/bin/env python3
"""Renderer wall-time and PNG size profile (run on Pi for the 250ms criterion)."""
import json
import platform
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lib import radar_basemap as bm
from lib.radar_geometry import world_point

out=dict(machine=platform.machine(),platform=platform.platform(),revision=bm.version(),tiles=[])
for theme in ('paper','night'):
    for z in (4,6,8,10):
        x,y=world_point(47.61,-122.33,z);x,y=int(x//256),int(y//256)
        for dx,dy in ((0,0),(-1,0),(1,0),(0,-1),(0,1)):
            bm.tile(theme,z,x+dx,y+dy);out['tiles'].append(bm.RENDER_TIMES[-1])
times=sorted(t['ms'] for t in out['tiles']);sizes=sorted(t['bytes'] for t in out['tiles'])
out.update(medianMs=times[len(times)//2],p95Ms=times[int((len(times)-1)*.95)],maxMs=max(times),medianBytes=sizes[len(sizes)//2],p95Bytes=sizes[int((len(sizes)-1)*.95)],maxBytes=max(sizes))
print(json.dumps(out,indent=2))
