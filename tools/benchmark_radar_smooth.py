#!/usr/bin/env python3
"""Offline per-tile CPU cost: deterministic reflectivity fields, no network."""
import io
import json
import statistics
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw
from lib import radar_palette as rp


def fixture(source,rgba=False):
    tile=Image.new('P',(256,256));tile.putpalette([v for c in rp._indexed_colors(source) for v in c],rawmode='RGBA')
    draw=ImageDraw.Draw(tile);offset=66 if source.endswith('n0b') else 64
    for x,y in [(55,70),(155,120),(205,195)]:
        for radius,dbz in [(68,10),(52,15),(41,20),(32,30),(24,40),(16,50),(9,60)]:
            draw.ellipse((x-radius,y-radius,x+radius,y+radius),fill=offset+2*dbz)
    draw.rectangle((120,0,126,255),fill=0)
    return tile.convert('RGBA') if rgba else tile


def main():
    results=[]
    for source in rp.INDEX_DBZ:
        for rgba in (False,True):
            tile=fixture(source,rgba);native=io.BytesIO();tile.save(native,'PNG');raw=native.getvalue()
            row=dict(source=source,mode=tile.mode,inputBytes=len(raw))
            for smooth in (False,True):
                remap=rp.smooth_remap if smooth else rp.remap
                timings=[];raster=[]
                for i in range(85):
                    begin=time.thread_time()
                    with Image.open(io.BytesIO(raw)) as image:
                        image.load();start=time.thread_time()
                        with remap(image,source,rp.source_palette(source)) as mapped:
                            cost=time.thread_time()-start
                            assert mapped.size == (256,256)
                            output_size = mapped.size
                            out=io.BytesIO();mapped.save(out,'PNG')
                    if i>=5:timings.append((time.thread_time()-begin)*1000);raster.append(cost*1000)
                row['smooth' if smooth else 'nearest']=dict(remapMedianMs=statistics.median(raster),
                    totalMedianMs=statistics.median(timings),totalP95Ms=sorted(timings)[75],pngBytes=len(out.getvalue()),outputSize=output_size)
            row['extraCpuMs']=row['smooth']['totalMedianMs']-row['nearest']['totalMedianMs'];results.append(row)
    print(json.dumps(dict(iterations=80,clock='thread_time',results=results),indent=2))

if __name__=='__main__':main()
