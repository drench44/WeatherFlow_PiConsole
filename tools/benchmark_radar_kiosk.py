#!/usr/bin/env python3
"""Benchmark the actual raster kiosk through localhost CDP; print summary first.

Run on the Pi: python3 tools/benchmark_radar_kiosk.py > radar-v42-pi.json
Requires websocket-client (the same dependency as pi_gpu2.py). Uses the page's
real renderer, tiles, camera and rAF loop. No reload, replacement canvas or mocked
fetch. Temporarily activates Radar and moves its camera, then restores it.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import urllib.request

try:
    import websocket  # websocket-client: needed only to talk to the kiosk; the summary code is importable without it
except ImportError:  # pragma: no cover - exercised in CI without the optional dependency
    websocket = None

BENCH = r"""(async()=>{
 if(typeof radarGeoTiles==='undefined'||typeof radarBacking!=='undefined')throw Error('Page is not radar v4.1');
 if(!radarView.data?.available)throw Error('No station map in this page');
 const sleep=ms=>new Promise(r=>setTimeout(r,ms)),saved={camera:radarCamera&&{...radarCamera},paused:radarView.paused,auto:radarZoom.auto,requested:radarZoom.requested,screen:document.querySelector('[id^="s-"].active')?.id||'s-obs'};
 const out={userAgent:navigator.userAgent,theme:radarBaseStyle.theme,frames:[],memory:[],baseDrawMs:[],fetches:[],decodeMs:[],phaseWindows:{}};
 const originals=[],draws={count:0,baseMs:0},startFetch=window.fetch,startDecode=window.createImageBitmap,startFrame=radarFrame;
 let phase='warm',lastDrawCount=0,lastFrameAt=null;
 for(const proto of [CanvasRenderingContext2D.prototype,OffscreenCanvasRenderingContext2D.prototype]){const old=proto.drawImage;originals.push([proto,old]);proto.drawImage=function(...a){const t=performance.now();try{return old.apply(this,a)}finally{draws.count++;if(this.canvas.id==='rad-base')draws.baseMs+=performance.now()-t;}};}
 window.fetch=(u,...a)=>{out.fetches.push({phase,url:String(u)});return startFetch(u,...a)};
 window.createImageBitmap=async(...a)=>{const t=performance.now();try{return await startDecode(...a)}finally{out.decodeMs.push({phase,ms:performance.now()-t});}};
 radarFrame=function(now){const n=draws.count,b=draws.baseMs,t=performance.now(),paint=radarMetrics.basePaints;try{return startFrame(now)}finally{const f={phase,ms:performance.now()-t,drawImages:draws.count-n,baseDrawMs:draws.baseMs-b,basePainted:radarMetrics.basePaints!==paint,drawImagesSincePreviousFrame:draws.count-lastDrawCount,frameGapMs:lastFrameAt===null?null:t-lastFrameAt};lastDrawCount=draws.count;lastFrameAt=t;out.frames.push(f);out.memory.push(radarMemory());}};
 try{
  activate('s-radar');radarView.paused=true;radarLoopSync();await sleep(2500);
  out.warm={geoTiles:radarGeoTiles.size,echoTiles:radarTiles.size,history:radarView.loaded.filter(f=>f.bitmap).length,revision:radarView.data.geo?.version};
  phase='cached-entry';activate('s-obs');await sleep(100);const t=performance.now();activate('s-radar');
  while(radarMetrics.firstPaintMs===null&&performance.now()-t<2000)await sleep(5);
  out.cachedFirstPaintMs=radarMetrics.firstPaintMs;out.cachedGeographyFetches=out.fetches.filter(x=>x.phase==='cached-entry'&&x.url.includes('radar/geo/')).length;
  await sleep(1000);const origin={...radarCamera},p=document.getElementById('rad-plate'),b=p.getBoundingClientRect();
  const pointer=(type,id,x,y)=>p.dispatchEvent(new PointerEvent(type,{pointerId:id,pointerType:'touch',clientX:b.x+p.clientLeft+x*p.clientWidth/956,clientY:b.y+p.clientTop+y*p.clientHeight/490,bubbles:true}));
  phase='pan';out.phaseWindows.pan=[Date.now()/1000];pointer('pointerdown',901,400,245);for(let i=1;i<=30;i++){pointer('pointermove',901,400+200*i/30,245);await sleep(34);}await sleep(40);pointer('pointerup',901,600,245);await sleep(800);out.phaseWindows.pan.push(Date.now()/1000);phase='reset';
  radarGestureCancel();radarCameraSet(origin);radarSettle();await sleep(1000);
  phase='pinch';out.phaseWindows.pinch=[Date.now()/1000];const focal={x:178,y:145};pointer('pointerdown',902,138,145);pointer('pointerdown',903,218,145);
  for(let i=1;i<=20;i++){const s=2**(.63*i/20);pointer('pointermove',902,focal.x-40*s,focal.y);pointer('pointermove',903,focal.x+40*s,focal.y);await sleep(34);}
  pointer('pointerup',902,focal.x-40*2**.63,focal.y);pointer('pointerup',903,focal.x+40*2**.63,focal.y);await sleep(350);out.phaseWindows.pinch.push(Date.now()/1000);
  out.memoryCounterBytes=radarMemory();out.memoryPeakBytes=Math.max(...out.memory);out.memoryPeakMiB=out.memoryPeakBytes/1048576;
  out.lru={echo:radarTiles.size,basemap:radarGeoTiles.size,history:radarView.loaded.filter(f=>f.bitmap).length};
  for(const label of ['pan','pinch']){const frames=out.frames.filter(f=>f.phase===label&&f.drawImages);const times=frames.map(f=>f.ms).sort((a,b)=>a-b);out[label]={paintedFrames:frames.length,medianMs:times[Math.floor(times.length/2)],p95Ms:times[Math.floor((times.length-1)*.95)],maxMs:Math.max(...times),maxDrawImages:Math.max(...frames.map(f=>f.drawImages)),maxDrawImagesIncludingDecodeCallbacks:Math.max(...frames.map(f=>f.drawImagesSincePreviousFrame)),maxFrameGapMs:Math.max(...frames.map(f=>f.frameGapMs||0)),maxBasemapDrawMs:Math.max(...frames.map(f=>f.baseDrawMs))};}
  out.fences={steadyPan:out.pan.maxDrawImages<=32,absolute:Math.max(out.pan.maxDrawImagesIncludingDecodeCallbacks,out.pinch.maxDrawImagesIncludingDecodeCallbacks)<=56,memory:out.memoryPeakBytes<=41943040,cachedPaint:out.cachedFirstPaintMs!==null&&out.cachedFirstPaintMs<100};
  delete out.memory;return out;
 }finally{phase='restore';radarGestureCancel();if(saved.camera){radarCameraSet(saved.camera);radarZoom.auto=saved.auto;radarZoom.requested=saved.requested;radarSettle();}radarView.paused=saved.paused;radarLoopSync();if(saved.screen!=='s-radar')activate(saved.screen);radarFrame=startFrame;window.fetch=startFetch;window.createImageBitmap=startDecode;for(const [p,fn] of originals)p.drawImage=fn;}
})()"""


def call(ws,method,params=None):
    call.seq+=1;seq=call.seq
    ws.send(json.dumps(dict(id=seq,method=method,params=params or {})))
    while True:
        response=json.loads(ws.recv())
        if response.get('method','').startswith('Network.'):
            call.events.append(response)
        if response.get('id')==seq:
            if 'error' in response:raise RuntimeError(response['error'])
            return response['result']
call.seq=0
call.events=[]


def server_requests(events,windows):
    """Actual CDP requests, excluding browser cache/service-worker responses."""
    requests={};cached=set()
    for event in events:
        method,p=event['method'],event['params'];ident=p.get('requestId')
        if method=='Network.requestWillBeSent':
            if p['request']['url'].startswith(('http://','https://')):
                requests[ident]=p['wallTime']
        elif method=='Network.requestServedFromCache':cached.add(ident)
        elif method=='Network.responseReceived':
            r=p['response']
            if any(r.get(k) for k in ('fromDiskCache','fromPrefetchCache','fromServiceWorker')):
                cached.add(ident)
    return {label:sum(start<=at<end and ident not in cached for ident,at in requests.items())
            for label,(start,end) in windows.items()}


def benchmark_output(value,radar_dir,verbose=False):
    def gesture(label):
        frames=[f for f in value['frames'] if f['phase']==label and f['drawImages']]
        times=sorted(f['ms'] for f in frames)
        return dict(frames=len(frames),maxDrawMs=max(times,default=None),
                    p95DrawMs=times[int((len(times)-1)*.95)] if times else None,
                    maxDrawImages=max((f['drawImages'] for f in frames),default=0),
                    serverRequests=value['serverRequests'][label])
    root=Path(radar_dir)
    # These are disk counts on the machine running this script, not JS LRU sizes.
    summary=dict(firstPaintMs=value['cachedFirstPaintMs'],pan=gesture('pan'),pinch=gesture('pinch'),
                 memoryPeakMiB=value['memoryPeakMiB'],
                 geoTilesOnDisk=sum(1 for _ in (root/'geo').rglob('*.png')) if root.is_dir() else None,
                 radarTilesOnDisk=sum(1 for _ in (root/'t').rglob('*.png')) if root.is_dir() else None,
                 fences=value['fences'])
    result=dict(summary=summary)
    result.update(value if verbose else {k:v for k,v in value.items()
                  if k not in ('frames','fetches','gpu','decodeMs','baseDrawMs')})
    return result


def main():
    if websocket is None:
        raise SystemExit('websocket-client is required to drive the kiosk: pip install websocket-client')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=9222)
    parser.add_argument('--url-contains',default='')
    parser.add_argument('--radar-dir',type=Path,default=Path(os.environ.get('WFP_RADAR_DIR','~/almanac_web/radar')).expanduser())
    parser.add_argument('--verbose',action='store_true',help='Include individual frames, fetches, decode times and GPU details')
    args=parser.parse_args();base=f'http://127.0.0.1:{args.port}'
    def get(path):
        with urllib.request.urlopen(base+path,timeout=5) as response:return json.load(response)
    version=get('/json/version');gpu={}
    with closing(websocket.create_connection(version['webSocketDebuggerUrl'],timeout=60,suppress_origin=True)) as ws:
        gpu=call(ws,'SystemInfo.getInfo').get('gpu',{})
    pages=[p for p in get('/json') if p.get('type')=='page' and args.url_contains in p.get('url','')]
    for page in pages:
        with closing(websocket.create_connection(page['webSocketDebuggerUrl'],timeout=60,suppress_origin=True)) as ws:
            check=call(ws,'Runtime.evaluate',dict(expression="typeof radarGeoTiles!=='undefined'",returnByValue=True))
            if not check.get('result',{}).get('value'):continue
            call(ws,'Network.enable');call.events=[]
            result=call(ws,'Runtime.evaluate',dict(expression=BENCH,returnByValue=True,awaitPromise=True,timeout=55000))
            if 'exceptionDetails' in result:raise RuntimeError(result['exceptionDetails'])
            value=result['result']['value'];value.update(pageUrl=page['url'],gpu=gpu,browser=version)
            value['serverRequests']=server_requests(call.events,value['phaseWindows'])
            print(json.dumps(benchmark_output(value,args.radar_dir,args.verbose),indent=2));return
    raise SystemExit('No live v4.1 page found on localhost CDP')


if __name__=='__main__':main()
