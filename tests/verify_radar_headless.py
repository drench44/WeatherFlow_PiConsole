"""Radar v4.1 real-loopback Playwright and timing regression checks, both themes."""
import argparse
import copy
import io
import json
import importlib.util
import threading
import tempfile
import time
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo
from playwright.sync_api import sync_playwright
from tests import conftest  # noqa: F401
from tests.fixtures.config import make_config
from lib import almanac_emit as ae, radar_basemap as bm


# Independent observed graphics allocations. This excludes implementation counters
# and JS/driver heap overhead; it is a cross-check, not a process-memory claim.
AUDIT = r"""(()=>{
 const live=new Map(),surfaces=new Set(),proto=ImageBitmap.prototype,close=proto.close;
 window.audit={fetches:[],frames:[],smoothing:[],decodeCount:0,peak:0,live,surfaces};
 const own=b=>{live.set(b,b.width*b.height*4);audit.peak=Math.max(audit.peak,audit.bytes());return b;};
 proto.close=function(){live.delete(this);return close.call(this)};
 const Off=window.OffscreenCanvas;window.OffscreenCanvas=class extends Off{constructor(w,h){super(w,h);surfaces.add(this)}};
 const transfer=Off.prototype.transferToImageBitmap;Off.prototype.transferToImageBitmap=function(){return own(transfer.call(this))};
 audit.bytes=()=>[...live.values()].reduce((a,b)=>a+b,0)+[...surfaces].reduce((a,c)=>a+c.width*c.height*4,0)+[...document.querySelectorAll('#rad-base,#rad-echo')].reduce((a,c)=>a+c.width*c.height*4,0);
 const decode=window.createImageBitmap;window.createImageBitmap=async(...a)=>{audit.decodeCount++;return own(await decode(...a))};
 const fetch0=window.fetch;window.fetch=(u,...a)=>{audit.fetches.push(String(u));return fetch0(u,...a)};
 let current=null;
 for(const P of [CanvasRenderingContext2D.prototype,OffscreenCanvasRenderingContext2D.prototype]){const draw=P.drawImage;P.drawImage=function(...a){if(current)current.draws++;
   if(this.canvas.id==='rad-base'||this.canvas.id==='rad-echo')audit.smoothing.push({layer:this.canvas.id,smooth:this.imageSmoothingEnabled,args:a.slice(1),w:a[0].width});
   return draw.apply(this,a);};}
 const raf=window.requestAnimationFrame;window.requestAnimationFrame=cb=>raf(t=>{const prior=current;current={draws:0,ms:0};const start=performance.now();try{return cb(t)}finally{current.ms=performance.now()-start;audit.frames.push(current);if(audit.frames.length>4000)audit.frames.shift();current=prior;}});
})();"""


def basemap_pixels(page,theme):
    return page.evaluate(r"""async theme=>{
      radarGestureCancel();radarView.active=false;radarCamera={...radarView.data.center,zoom:8};
      const saved=radarGeoTiles;radarGeoTiles=new Map();const ctx=document.getElementById('rad-base').getContext('2d');
      const pixels=()=>Array.from(ctx.getImageData(0,0,956,490).data),different=(a,b)=>a.some((n,i)=>n!==b[i]);
      radarBasePaint();const grid=pixels();const ground=theme==='paper'?[235,230,219]:[11,13,17];
      let marks=0;for(let i=0;i<grid.length;i+=4)if(grid[i]!==ground[0]||grid[i+1]!==ground[1]||grid[i+2]!==ground[2])marks++;
      if(!marks)throw Error('no graticule pixels');
      const t=radarTileSet(radarCamera,7)[0],canvas=new OffscreenCanvas(256,256),x=canvas.getContext('2d');x.fillStyle='#123456';x.fillRect(0,0,256,256);
      const bitmap=await createImageBitmap(canvas),key=radarGeoKey(7,t.x,t.y);radarGeoTiles.set(key,{bitmap});radarBasePaint();const ancestor=pixels();
      radarGeoTiles.clear();radarGeoTiles.set(radarGeoKey(7,t.x,t.y,undefined,theme==='paper'?'night':'paper'),{bitmap});radarBasePaint();const otherTheme=pixels();
      radarGeoTiles.clear();radarGeoTiles.set(radarGeoKey(7,t.x,t.y,'000000000000'),{bitmap});radarBasePaint();const otherVersion=pixels();
      if(!different(grid,ancestor)||different(grid,otherTheme)||different(grid,otherVersion))throw Error('ancestor identity');
      radarGeoTiles.clear();for(const t of radarTileSet(radarCamera,8))radarGeoTiles.set(radarGeoKey(8,t.x,t.y),{bitmap});radarBasePaint();
      const covered=pixels();for(let i=0;i<covered.length;i+=4)if(covered[i]!==18||covered[i+1]!==52||covered[i+2]!==86)throw Error('graticule leaked through tile');
      bitmap.close();canvas.width=canvas.height=0;radarGeoTiles=saved;radarView.active=true;radarBaseDirty=true;radarWake();return {marks,ancestor:true,wrongTheme:false,wrongVersion:false};
    }""",theme)

def payload():
    app=SimpleNamespace(config=make_config(),obsParser=SimpleNamespace(api_data={}))
    e=ae.AlmanacEmitter(SimpleNamespace(app=app,Obs={},Met={},Astro={},Sager={}))
    data=e._build_payload(); now=int(time.time())//120*120-360
    r=ae.AlmanacEmitter._radar_payload(ae._RADAR_NONE._replace(available=True,reason=None,
        center=dict(lat=47.61,lon=-122.33),zoom=8,zoom_auto_level=8,max_zoom=9,source_id='iem-mrms-lcref',
        provider='iem',cadence=120,stale_sec=600,legend=ae._RADAR_RAMP,ts_frame=now,ts_fetch=now+300,
        sources=(dict(mode='mosaic',available=True),dict(mode='site',siteId='KATX',available=True))),time.time(),timezone.utc)
    r.update(geo=dict(version=bm.version(),base='radar/geo/',sites='radar/sites-'+ae._radar_sites_revision()+'.json'),
        rings=[dict(meters=40233.6,label='25 mi'),dict(meters=80467.2,label='50 mi')])
    frames=[dict(ts=now-offset,at=datetime.fromtimestamp(now-offset,timezone.utc).strftime('%H:%M'),
                 stamp=datetime.fromtimestamp(now-offset,timezone.utc).strftime('%Y%m%d%H%M'),siteScans=[],levels={'7':True,'8':True,'9':True}) for offset in range(840,-1,-120)]
    r['tiles']=dict(base='radar/t/',revision=ae._radar_render_revision(),remapRevision=ae.REMAP_REVISION,source=r['sourceId'],site='-',z=8,levels=[7,8,9],grid=dict(x0=38,y0=86,w=7,h=5),newest=dict(stamp=frames[-1]['stamp'],mask='7ffffffff'),frames=frames)
    r.update(frameCount=8,completeFrameCount=8,historySpanSec=840);data['radar']=r;return data


def tile_png(color=(138,163,198,255)):
    image=Image.new('RGBA',(256,256));d=ImageDraw.Draw(image)
    d.ellipse((35,15,200,150),fill=color);d.polygon([(110,90),(230,160),(200,220),(90,160)],fill=color)
    info=PngInfo();info.add_text('radarRemap',json.dumps(dict(remapped=True,unmatchedColors=0,opaqueColors=1,
        unmatchedPixels=0,opaquePixels=sum(p[3]>0 for p in image.getdata()),ambiguousPixels=0,revision=ae.REMAP_REVISION)))
    info.add_text('radarVisiblePixels',str(sum(p[3]>0 for p in image.getdata())))
    stream=io.BytesIO();image.save(stream,'PNG',pnginfo=info);return stream.getvalue()


@contextmanager
def radar_server():
    spec=importlib.util.spec_from_file_location('radar_v4_serve',Path('design/almanac/kiosk/serve.py'))
    server_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(server_module)
    with tempfile.TemporaryDirectory(prefix='radar-v41-') as temp:
        root=Path(temp);radar=root/'radar';radar.mkdir();data=payload();(root/'index.html').write_text(Path('design/almanac/console_live.html').read_text());(root/'wx.json').write_text(json.dumps(data))
        bm.warm(radar,(47.61,-122.33),data['radar']['center'],8,limit=490)
        (radar/'.geo-revision').write_text(bm.version())
        (radar/'.tile-revision').write_text(ae._radar_render_revision())
        (radar/'.sites-revision').write_text(ae._radar_sites_revision())
        (radar/('sites-'+ae._radar_sites_revision()+'.json')).write_text(json.dumps([dict(id=i,lat=a,lon=b) for i,(a,b,_) in ae._NEXRAD_SITES.items()]))
        # All fixture tiles are genuine 256-pixel immutable PNGs, not fetch shims.
        for z in (6,7,8,9,10):
            center=ae.world_point(47.61,-122.33,z);cx,cy=int(center[0]//256),int(center[1]//256)
            for n,frame in enumerate(data['radar']['tiles']['frames']):
                raw=tile_png(ae._RADAR_LUT[n*3][1])
                for y in range(cy-4,cy+5):
                    for x in range(cx-5,cx+6):
                        p=radar/'t'/ae._radar_render_revision()/'iem-mrms-lcref'/'-'/frame['stamp']/str(z)/str(x)/f'{y}.png';p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
                        site=radar/'t'/ae._radar_render_revision()/'iem-nexrad-n0b'/'KATX'/frame['stamp']/str(z)/str(x)/f'{y}.png';site.parent.mkdir(parents=True,exist_ok=True);site.hardlink_to(p)
        requests=[];request_times=[]
        class Handler(server_module.Handler):
            def do_GET(self):
                requests.append(self.path);request_times.append(dict(path=self.path,at=time.time()*1000))
                return super().do_GET()
            def log_message(self,*args): pass
        server_module.WEB=str(root);server_module.DATA=str(root/'wx.json')
        server=server_module.http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:yield SimpleNamespace(root=root,data=data,requests=requests,request_times=request_times,url=f'http://127.0.0.1:{server.server_port}',module=server_module)
        finally:server.shutdown();server.server_close();thread.join()


def smoke(browser,server,theme,output):
    context=browser.new_context(viewport=dict(width=1024,height=600),has_touch=True)
    context.add_init_script(AUDIT)
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(server.url+'/?tabs=1&theme='+theme);page.locator('.tab[data-screen="s-radar"]').click()
    try:
        page.wait_for_function('radarView.good && radarReady().length===8',timeout=20000)
    except Exception:
        print('SMOKE DEBUG',theme,errors,page.evaluate('({n:radarReady().length,tiles:radarTiles.size,busy:radarTileBusy,queue:radarTileQueue.length,absent:[...radarTileAbsent],geo:radarGeoTiles.size,job:radarCompositeJob&&{done:radarCompositeJob.done.size,need:radarCompositeJob.tiles.length},f:radarView.good})'))
        raise
    page.evaluate('radarView.paused=true;radarView.current=radarView.good;radarEchoDirty=true;radarLoopSync()')
    page.wait_for_timeout(100)
    page.evaluate('audit.fetches=[];audit.frames=[]')
    start=len(server.requests)
    motion=page.evaluate('''async()=>{
      const p=document.getElementById('rad-plate'),box=p.getBoundingClientRect();
      const point=(type,x)=>p.dispatchEvent(new PointerEvent(type,{pointerId:1,pointerType:'touch',clientX:box.x+400+x,clientY:box.y+250,bubbles:true}));
      let nodes=0,attrs=0;const observer=new MutationObserver(ms=>ms.forEach(m=>{if(m.type==='childList')nodes+=m.addedNodes.length+m.removedNodes.length;else attrs++;}));observer.observe(document.getElementById('rad-over'),{subtree:true,attributes:true,childList:true});
      const paints=radarMetrics.basePaints;radarMetrics.drawMs=[];point('pointerdown',0);
      for(let i=1;i<=30;i++){point('pointermove',200*i/30);await new Promise(r=>setTimeout(r,34));}
      observer.disconnect();return {paints:radarMetrics.basePaints-paints,nodes,attrs,ms:radarMetrics.drawMs,center:radarCamera};
    }''')
    requests=server.requests[start:]
    print('MOTION',theme,motion,'requests',requests,flush=True)
    assert motion['paints']>=25,motion
    assert not any('/wx.json' in u or '/radar/t/' in u for u in requests),requests
    assert not page.evaluate("audit.fetches.filter(u=>/radar\\/(t|geo)\\//.test(u)).length"),page.evaluate('audit.fetches')
    assert not motion['nodes'] and motion['attrs']<=12*motion['paints'],motion
    # Wall time is diagnostic; only panel measurements establish performance.
    assert page.evaluate('Math.max(...audit.frames.map(f=>f.draws))')<=32
    page.screenshot(path=str(output/f'radar-v41-mid-pan-{theme}.png'))
    page.evaluate('radarGestureCancel()')
    page.screenshot(path=str(output/f'radar-v41-{theme}.png'))
    print('SMOKE',theme,page.evaluate('({memory:radarMemory(),n:radarReady().length,points:radarMetrics.lastPoints,firstPaint:radarMetrics.firstPaintMs})'),errors,flush=True)
    # R13: decoded geography survives tab closure; network is measured at the
    # actual server, so HTTP cache reads are not misreported as round trips.
    page.evaluate('radarGestureCancel();radarCameraSet({...radarView.data.center,zoom:8});radarBaseDirty=true')
    page.wait_for_timeout(120)
    page.locator('.tab[data-screen="s-obs"]').click();before=len(server.requests)
    page.locator('.tab[data-screen="s-radar"]').click()
    page.wait_for_function('radarMetrics.firstPaintMs!==null')
    cached=page.evaluate('radarMetrics.firstPaintMs')
    assert cached<100,cached
    assert not any('/radar/geo/' in u for u in server.requests[before:]),server.requests[before:]
    page.wait_for_function('radarReady().length===8 && radarTileBusy===0')
    page.evaluate('radarView.paused=true;radarView.current=radarView.good;radarEchoDirty=true;radarLoopSync()')
    page.wait_for_timeout(100)
    # R14/15: translate the pinch midpoint and preserve its actual Mercator
    # coordinate, including every intermediate frame of integer snapping.
    pinch=page.evaluate('''async()=>{
      radarGestureCancel();radarCameraSet({...radarView.data.center,zoom:8});
      const p=document.getElementById('rad-plate'),b=p.getBoundingClientRect(),c={...radarCamera},f={x:178,y:145};
      const w=radarWorldPoint(c.lat,c.lon,c.zoom),anchor=[w[0]+f.x-478,w[1]+f.y-245],errors=[];
      const point=(type,id,x,y)=>p.dispatchEvent(new PointerEvent(type,{pointerId:id,pointerType:'touch',clientX:b.x+p.clientLeft+x*p.clientWidth/956,clientY:b.y+p.clientTop+y*p.clientHeight/490,bubbles:true}));
      point('pointerdown',11,f.x-40,f.y);point('pointerdown',12,f.x+40,f.y);radarMetrics.drawMs=[];
      function error(){let z=radarCamera.zoom,q=radarWorldPoint(radarCamera.lat,radarCamera.lon,z),s=2**(z-8);return Math.hypot(anchor[0]*s-q[0]+478-f.x,anchor[1]*s-q[1]+245-f.y);}
      for(let i=1;i<=15;i++){let s=2**(.63*i/15);point('pointermove',11,f.x-40*s,f.y);point('pointermove',12,f.x+40*s,f.y);await new Promise(r=>setTimeout(r,34));errors.push(error());}
      let fractional=radarCamera.zoom,t0=performance.now();point('pointerup',11,f.x-40*2**.63,f.y);point('pointerup',12,f.x+40*2**.63,f.y);
      while(radarGesture.state!=='idle'){await new Promise(requestAnimationFrame);errors.push(error());}
      return {fractional,zoom:radarCamera.zoom,duration:performance.now()-t0,maxError:Math.max(...errors),maxDraw:Math.max(...radarMetrics.drawMs)};
    }''')
    assert abs(pinch['fractional']-8.63)<1e-8 and pinch['zoom']==9 and pinch['maxError']<=.5,pinch
    assert 150<=pinch['duration']<=220,pinch
    assert page.evaluate('Math.max(...audit.frames.map(f=>f.draws))')<=56
    resampling=page.evaluate('audit.smoothing.filter(d=>d.layer==="rad-echo"&&d.smooth)');assert not resampling,resampling
    base_sampling=page.evaluate('audit.smoothing.filter(d=>d.layer==="rad-base"&&d.smooth!==(d.args[2]!==d.w||d.args[3]!==d.w))');assert not base_sampling,base_sampling
    print('DRAW FENCE',theme,page.evaluate('({max:Math.max(...audit.frames.map(f=>f.draws)),peakCounter:radarMetrics.peakMemoryBytes,peakObserved:audit.peak})'),flush=True)
    page.emulate_media(reduced_motion='reduce')
    instant=page.evaluate('''()=>{radarCameraSet({...radarCamera,zoom:8.63});const f={x:178,y:145},target=radarFocal(radarCamera,9,f);radarEase(target,160,f);return {state:radarGesture.state,z:radarCamera.zoom};}''')
    assert instant==dict(state='idle',z=9),instant
    page.emulate_media(reduced_motion='no-preference');page.wait_for_timeout(350)
    # R17-19: deterministic pixels, including strict same-stamp ancestry and
    # independently timed hatch. Freeze only fixture delivery, not draw logic.
    page.wait_for_function('radarTileBusy===0')
    pixels=page.evaluate('''async()=>{
      radarGestureCancel();radarRelease();radarView.active=false;radarCamera={...radarView.data.center,lon:radarView.data.center.lon-.8,zoom:8};radarBaseDirty=true;radarBasePaint();radarView.paused=false;
      const ctx=document.getElementById('rad-echo').getContext('2d'),tile=radarTileSet(radarCamera,8)[0],f={ts:946684800,stamp:'200001010000',at:'17:12',siteScans:[]},old={...f,ts:946684740,stamp:'199912312359'};
      radarView.current=f;radarView.good=f;radarView.loaded=[f];radarView.data.observedTs=f.ts;radarView.data.observedAt=f.at;radarOverlayBuild();radarZoomRender();
      const c=new OffscreenCanvas(256,256),x=c.getContext('2d');x.fillStyle='#8AA3C6';x.fillRect(0,0,256,256);
      const meta={remapped:true,opaquePixels:65536,unmatchedPixels:0,ambiguousPixels:0};
      const parent=async frame=>{const key=radarTileKey(frame,7,Math.floor(tile.x/2),Math.floor(tile.y/2));radarTileRemember(key,{key,z:7,x:Math.floor(tile.x/2),y:Math.floor(tile.y/2),bitmap:await createImageBitmap(c),meta,hasEcho:true});};
      let p=radarWorldPoint(radarCamera.lat,radarCamera.lon,8),px=Math.round(478+(tile.x+.5)*256-p[0]),py=Math.round(245+(tile.y+.5)*256-p[1]);px=Math.max(2,Math.min(953,px));py=Math.max(2,Math.min(487,py));
      await parent(f);radarEchoPaint(f);const same=Array.from(ctx.getImageData(px,py,1,1).data);
      radarTiles.forEach(t=>t.bitmap.close());radarTiles.clear();await parent(old);radarEchoPaint(f);const other=Array.from(ctx.getImageData(px,py,1,1).data);
      radarTiles.forEach(t=>t.bitmap.close());radarTiles.clear();let key=radarTileKey(f,8,tile.x,tile.y);radarTileRemember(key,{key,z:8,x:tile.x,y:tile.y,bitmap:await createImageBitmap(c),meta,hasEcho:true});
      radarMissingSince.clear();radarEchoPaint(f);await new Promise(r=>setTimeout(r,300));radarEchoPaint(f);let hatch300=radarMetrics.hatchRects;
      await new Promise(r=>setTimeout(r,300));radarEchoPaint(f);radarState();radarView.active=true;radarLoopSync();radarView.active=false;let hatch600=radarMetrics.hatchRects,aria=document.getElementById('rad-plate').getAttribute('aria-label'),asof=document.getElementById('rad-asof').textContent;
      return {same,other,hatch300,hatch600,aria,asof,read:document.getElementById('rad-frame-time').textContent,color:radarBaseStyle['--rule-faint']};
    }''')
    assert pixels['same']==[138,163,198,255] and pixels['other']==[0,0,0,0],pixels
    assert pixels['hatch300']==0 and pixels['hatch600']>0 and pixels['aria'].endswith('· partial coverage') and pixels['asof']=='17:12' and pixels['read']=='17:12 · newest',pixels
    assert not any(t in page.locator('#s-radar').inner_text() for t in ('Loading','Fetching'))
    # At the displaced mid-pan camera, hatch is shown only after fingers lift.
    # P4 explicitly forbids a hatch during an active gesture.
    page.screenshot(path=str(output/f'radar-v41-mid-pan-hatch-{theme}.png'))
    hatch_color=page.evaluate('''()=>{const c=new OffscreenCanvas(8,8),x=c.getContext('2d');x.fillStyle=radarHatchPattern(x);x.fillRect(0,0,8,8);let a=x.getImageData(0,0,8,8).data;return Array.from({length:64},(_,i)=>Array.from(a.slice(i*4,i*4+4))).filter(p=>p[3]);}''')
    expected_hatch=page.evaluate('''()=>{const c=new OffscreenCanvas(8,8),x=c.getContext('2d');x.strokeStyle=radarBaseStyle['--rule-faint'];x.lineWidth=1;x.beginPath();x.moveTo(-1,1);x.lineTo(1,-1);x.moveTo(0,8);x.lineTo(8,0);x.moveTo(7,9);x.lineTo(9,7);x.stroke();let a=x.getImageData(0,0,8,8).data;return Array.from({length:64},(_,i)=>Array.from(a.slice(i*4,i*4+4))).filter(p=>p[3]);}''')
    assert hatch_color==expected_hatch
    assert hatch_color and all(p[:3]!=list(bytes.fromhex(h[1:])) for p in hatch_color for band in ae._RADAR_RAMP['bands'] for h in (band['start'],band['end']))
    absent=page.evaluate('''()=>{radarTiles.forEach(t=>t.bitmap.close());radarTiles.clear();radarEchoPaint(radarView.current);radarUpdateReady();radarView.active=true;radarLoopSync();radarView.active=false;return {hatch:radarMetrics.hatchRects,read:document.getElementById('rad-frame-time').textContent};}''')
    assert absent==dict(hatch=0,read='Buffering · 0 of 8'),absent
    # R22 plus the cache lifecycle: base pigment changes, raster bytes do not.
    page.evaluate('d=>{radarRelease();radarView.data=d.radar;radarView.active=true;radarActivate();radarView.paused=true}',server.data)
    page.wait_for_function('radarReady().length===8 && radarTileBusy===0')
    page.evaluate('radarView.current=radarView.good;radarEchoDirty=true')
    page.wait_for_timeout(100)
    old_pixels=page.evaluate('[document.getElementById("rad-base").toDataURL(),document.getElementById("rad-echo").toDataURL()]')
    other_theme='night' if theme=='paper' else 'paper'
    page.evaluate('t=>document.documentElement.dataset.theme=t',other_theme);page.wait_for_timeout(150)
    new_pixels=page.evaluate('[document.getElementById("rad-base").toDataURL(),document.getElementById("rad-echo").toDataURL()]')
    assert old_pixels[0]!=new_pixels[0] and old_pixels[1]==new_pixels[1]
    assert page.locator('#rad-echo').evaluate('e=>getComputedStyle(e).filter')=='none'
    page.evaluate('t=>document.documentElement.dataset.theme=t',theme)
    print('R13-R19/R22',theme,dict(cachedMs=cached,pinch=pinch,partial=pixels),flush=True)
    # R20: actual camera travel after release, with reduced-motion suppression.
    inertia=page.evaluate('''async()=>{
      radarGestureCancel();radarCameraSet({...radarView.data.center,zoom:8});
      const p=document.getElementById('rad-plate'),b=p.getBoundingClientRect(),point=(type,x)=>p.dispatchEvent(new PointerEvent(type,{pointerId:30,pointerType:'touch',clientX:b.x+400+x,clientY:b.y+250,bubbles:true}));
      point('pointerdown',0);for(let i=1;i<=5;i++){await new Promise(r=>setTimeout(r,15));point('pointermove',i*13.5);}point('pointerup',67.5);
      let start=radarWorldPoint(radarCamera.lat,radarCamera.lon,8),positions=[],velocities=[];
      while(radarGesture.inertia){await new Promise(r=>setTimeout(r,100));positions.push(radarWorldPoint(radarCamera.lat,radarCamera.lon,8)[0]);if(radarGesture.inertia)velocities.push(Math.hypot(...radarGesture.inertia.v)*Math.exp(-(performance.now()-radarGesture.inertia.start)/325));}
      let end=radarWorldPoint(radarCamera.lat,radarCamera.lon,8);return {travel:Math.hypot(end[0]-start[0],end[1]-start[1]),positions,velocities,state:radarGesture.state};
    }''')
    assert 50<inertia['travel']<=780 and inertia['state']=='idle',inertia
    assert all(b<a for a,b in zip(inertia['velocities'],inertia['velocities'][1:])),inertia
    assert all(b<=a for a,b in zip(inertia['positions'],inertia['positions'][1:])),inertia
    page.emulate_media(reduced_motion='reduce')
    no_inertia=page.evaluate('''async()=>{radarGestureCancel();radarCameraSet({...radarView.data.center,zoom:8});const p=document.getElementById('rad-plate'),b=p.getBoundingClientRect();for(let [type,x] of [['pointerdown',400],['pointermove',490],['pointerup',490]]){p.dispatchEvent(new PointerEvent(type,{pointerId:31,pointerType:'touch',clientX:b.x+x,clientY:b.y+250,bubbles:true}));await new Promise(r=>setTimeout(r,20));}const before={...radarCamera};await new Promise(r=>setTimeout(r,200));return JSON.stringify(before)===JSON.stringify(radarCamera);}''')
    assert no_inertia
    page.emulate_media(reduced_motion='no-preference');page.wait_for_timeout(400)
    # R23: starting the pinch while the pan is still coasting coalesces both
    # inputs into one final report, with no acknowledgement response header.
    before=len(server.requests)
    page.evaluate('''async()=>{radarGestureCancel();radarCameraSet({...radarView.data.center,zoom:8});const p=document.getElementById('rad-plate'),b=p.getBoundingClientRect(),point=(type,id,x,y=250)=>p.dispatchEvent(new PointerEvent(type,{pointerId:id,pointerType:'touch',clientX:b.x+x,clientY:b.y+y,bubbles:true}));
      point('pointerdown',40,400);await new Promise(r=>setTimeout(r,20));point('pointermove',40,418);await new Promise(r=>setTimeout(r,20));point('pointermove',40,436);point('pointerup',40,436);await new Promise(r=>setTimeout(r,80));
      point('pointerdown',41,300);point('pointerdown',42,500);point('pointermove',41,260);point('pointermove',42,540);await new Promise(r=>setTimeout(r,50));point('pointerup',41,260);point('pointerup',42,540);
      while(radarGesture.state!=='idle')await new Promise(requestAnimationFrame);
      await new Promise(r=>setTimeout(r,250));
    }''')
    reports=[u for u in server.requests[before:] if 'radarZoom=' in u]
    assert len(reports)==1 and 'radarCenter=' in reports[0],reports
    from urllib.request import urlopen
    with urlopen(server.url+'/wx.json') as response:assert response.headers.get('X-Radar-Intent-Seq') is None
    print('R20/R23',theme,dict(inertiaTravel=inertia['travel'],reports=len(reports)),flush=True)

    extended(page,server,theme,output)
    assert not errors,errors
    context.close()


def extended(page,server,theme,output):
    # R16 measures each new visible URL, while holding the pointer down. Idle
    # margin replenishment belongs to the following settled view, not this drag.
    page.wait_for_function('radarTileBusy===0')
    page.evaluate('radarGestureCancel();radarCameraSet({...radarView.data.center,zoom:8});radarView.paused=true;radarView.current=radarView.good;radarBaseDirty=true;radarEchoDirty=true')
    page.wait_for_function('radarTileBusy===0 && !radarCameraDirty')
    page.wait_for_timeout(700)
    reposition=page.evaluate('''async()=>{
      const start={...radarCamera},resident=new Set(radarTiles.keys()),urls=[];const fetch0=window.fetch;
      window.fetch=(u,...a)=>{if(String(u).includes('radar/t/'))urls.push(String(u));return fetch0(u,...a);};
      radarBegin();const p=radarWorldPoint(start.lat,start.lon,8);
      radarCameraSet({...radarWorldInverse(p[0]-100,p[1],8),zoom:8});await new Promise(r=>setTimeout(r,160));const small=urls.slice();
      const end={...radarWorldInverse(p[0]-400,p[1],8),zoom:8},f=radarView.good;
      const needed=radarTileSet(end,8).filter(t=>!resident.has(radarTileKey(f,8,t.x,t.y))).map(t=>radarTileURL(radarView.data.sourceId,'-',f.stamp,8,t.x,t.y)).sort();
      radarCameraSet(end);await new Promise(r=>setTimeout(r,600));window.fetch=fetch0;
      const large=urls.slice().sort();radarGestureCancel();return {small,large,needed};
    }''')
    assert not reposition['small'] and reposition['large']==reposition['needed'],reposition
    fallback=basemap_pixels(page,theme)
    # R11: real elapsed sixty seconds, continuously sampling all accounted
    # graphics storage. The actions include the R10/R14 gesture shapes, two
    # discrete zooms, a genuine source/tile identity switch and complete loops.
    page.evaluate('radarCameraSet({...radarView.data.center,zoom:8});radarBaseDirty=true;radarView.paused=false;radarLoopSync()')
    page.wait_for_function('radarReady().length===8')
    initial=copy.deepcopy(server.data);site=copy.deepcopy(initial);r=site['radar']
    r.update(sourceId='iem-nexrad-n0b',sourceMode='site',sourcePref='site',siteId='KATX',
             legend=dict(ae._RADAR_SITE_RAMP,remapped=True),cadenceSec=300,staleSec=1200,
             sites=[dict(id='KATX',lat=48.194611,lon=-122.49569,primary=True,contributing=True,reason=None)])
    r['tiles'].update(source=r['sourceId'],site='KATX')
    for frame in r['tiles']['frames']:frame['siteScans']=[dict(id='KATX',ts=frame['ts'])]
    page.evaluate('''()=>{window.memorySamples=[];window.loopSeen=new Set();window.memoryTimer=setInterval(()=>{memorySamples.push({bytes:radarMemory(),independent:audit.bytes(),geo:radarGeoTiles.size,tiles:radarTiles.size,history:radarView.loaded.filter(f=>f.bitmap).length});if(radarView.current)loopSeen.add(radarFrameKey(radarView.current));},50);}''')
    started=time.monotonic()
    page.evaluate('''async()=>{const p=document.getElementById('rad-plate'),b=p.getBoundingClientRect(),point=(type,id,x,y)=>p.dispatchEvent(new PointerEvent(type,{pointerId:id,pointerType:'touch',clientX:b.x+x,clientY:b.y+y,bubbles:true}));
      point('pointerdown',80,400,250);for(let i=1;i<=12;i++){point('pointermove',80,400+i*8,250);await new Promise(r=>setTimeout(r,34));}point('pointerup',80,496,250);await new Promise(r=>setTimeout(r,800));
      point('pointerdown',81,300,240);point('pointerdown',82,500,240);for(let i=1;i<=10;i++){point('pointermove',81,300-i*4,240+i);point('pointermove',82,500+i*4,240+i);await new Promise(r=>setTimeout(r,34));}point('pointerup',81,260,250);point('pointerup',82,540,250);await new Promise(r=>setTimeout(r,350));radarRecenter();}''')
    page.wait_for_timeout(700)
    page.evaluate('radarZoomChange(1)');page.wait_for_timeout(900);page.evaluate('radarZoomChange(-1)');page.wait_for_timeout(900)
    # Hold all provider metadata on this source while its actual loopback tiles load.
    (server.root/'wx.json').write_text(json.dumps(site));page.evaluate('d=>renderRadar(d)',site)
    page.wait_for_function("radarView.data.sourceId==='iem-nexrad-n0b' && radarReady().length===8",timeout=20000)
    page.evaluate('radarView.paused=false;radarLoopSync()')
    while time.monotonic()-started<(2 if QUICK else 60):page.wait_for_timeout(min(1000,max(1,(60-(time.monotonic()-started))*1000)))
    memory=page.evaluate('''()=>{clearInterval(memoryTimer);return {samples:memorySamples.length,independent:Math.max(...memorySamples.map(s=>s.independent)),geo:Math.max(...memorySamples.map(s=>s.geo)),bytes:Math.max(...memorySamples.map(s=>s.bytes)),tiles:Math.max(...memorySamples.map(s=>s.tiles)),history:Math.max(...memorySamples.map(s=>s.history)),loopFrames:loopSeen.size};}''')
    assert (QUICK or memory['samples']>=1000) and memory['bytes']<=40*1024*1024 and memory['tiles']<=40 and memory['history']<=7 and memory['geo']<=36 and memory['independent']<=40*1024*1024 and (QUICK or memory['loopFrames']>=8),memory
    print('R6/R11/R16',theme,dict(memory=memory,reposition=reposition,fallback=fallback),flush=True)
    (output/f'memory-{theme}.json').write_text(json.dumps(memory,indent=2))
    (server.root/'wx.json').write_text(json.dumps(initial));page.evaluate('d=>renderRadar(d)',initial)
    page.wait_for_function("radarView.data.sourceId==='iem-mrms-lcref' && radarReady().length===8")
    chrome(page,theme,initial)


def chrome(page,theme,data):
    """Surviving G2.18–38/K7–17 assertions; crop/ack/fence cases retired."""
    page.wait_for_function('!polling');page.evaluate('clearTimeout(pollTimer);radarView.paused=true;radarView.current=radarView.good;radarLoopSync()')
    box=page.locator('#rad-plate').bounding_box();assert box==dict(x=34,y=76,width=956,height=490),box
    for selector in ('#rad-src','#rad-src-cap','#rad-legend','#rad-loop','#rad-zoom','#rad-note'):
        if not page.locator(selector).is_visible():continue
        b=page.locator(selector).bounding_box();x=b['x']-box['x'];y=b['y']-box['y']
        assert y>=0 and (y+b['height']<=73 or y>=418) and y+b['height']<=490,(selector,b)
        dx=max(x-478,0,478-x-b['width']);dy=max(y-245,0,245-y-b['height']);assert dx*dx+dy*dy>150**2
    assert page.locator('#rad-legend').bounding_box()['width']==414
    assert page.locator('.rad-legend-unit').bounding_box()['width']==34
    assert page.locator('.rad-legend-unit').inner_text()=='dBZ'
    widths=page.locator('#rad-ramp i').evaluate_all('es=>es.map(e=>e.getBoundingClientRect().width)')
    assert len(widths)==9 and all(abs(a-b)<1 for a,b in zip(widths,[57.2,28.6,57.2,28.6,28.6,28.6,57.2,57.2,28.6]))
    assert page.locator('#rad-ramp').bounding_box()['width']==372
    ticks=page.locator('.rad-tick').evaluate_all("es=>es.map(e=>({text:e.textContent,left:parseFloat(e.style.left),w:getComputedStyle(e,'::before').width,h:getComputedStyle(e,'::before').height}))")
    assert [t['text'] for t in ticks]==['10','20','30','40','50','60','70']
    assert all(abs(t['left']-x)<1 and t['w']=='1px' and t['h']=='3px' for t,x in zip(ticks,[0,57.2,114.5,171.7,228.9,286.2,343.4]))
    assert page.locator('.rad-snow-ramp,.rad-snow-label,#rad-updating').count()==0
    assert page.locator('#rad-plate').get_attribute('aria-busy') is None
    assert page.locator('#s-radar [role="status"]').count()==1
    page.evaluate('radarIntent.postedAt=0;radarView.zoomNote="Closest view for MRMS"')
    for state,copy_ in [('newest','Refreshing · newest frame'),('history','Refreshing · frame 4 of 12'),('failed',"Couldn't refresh · showing "+page.evaluate('radarFrameLabel(radarView.current)')),('idle','Closest view for MRMS')]:
        page.evaluate("s=>{radarView.refresh={state:s,frameIndex:4,frameTotal:12};radarNoteRender()}",state)
        assert page.locator('#rad-note').inner_text()==copy_
        assert page.locator('#rad-note').bounding_box()['height']==14
    assert page.locator('#rad-note').evaluate('e=>getComputedStyle(e).pointerEvents')=='none'
    page.evaluate("radarView.refresh={state:'newest'};radarIntent.postedAt=Date.now()-400;radarNoteRender()")
    assert page.locator('#rad-note').get_attribute('data-shown')=='false'
    page.evaluate('radarIntent.postedAt=Date.now()-800;radarNoteRender()');assert page.locator('#rad-note').get_attribute('data-shown')=='true'
    for age in (360,480,660):
        page.evaluate('age=>{radarView.data.ageSec=age;radarView.data.stale=age>=600;radarState()}',age)
        assert ('min old' in page.locator('#rad-status').inner_text().lower())==(age>=480)
    assert page.locator('#rad-echo').evaluate('e=>getComputedStyle(e).opacity')=='0.66'
    page.evaluate('radarView.data.stale=false;radarState()')
    cluster=page.locator('#rad-loop').bounding_box()
    # The frame inventory changes, never the control box or intent glyph.
    page.evaluate('window.savedReady=radarView.readyFrames;radarView.active=false')
    for n in (0,1,2,4,8):
        page.evaluate('n=>{radarView.active=true;radarView.readyFrames=savedReady.slice(-n||8);if(!n)radarView.readyFrames=[];radarView.paused=true;radarLoopSync();radarView.active=false}',n)
        assert page.locator('#rad-loop').bounding_box()==cluster and page.locator('#rad-play').is_enabled()
        if n<2:assert page.locator('#rad-frame-time').inner_text()==f'Paused · {n} of 8'
        assert page.locator('#rad-frame-time').get_attribute('role') is None
        assert page.locator('#rad-frame-time').get_attribute('aria-live') is None
    page.evaluate('radarView.readyFrames=savedReady;radarView.active=true;radarView.paused=true;radarLoopSync()')
    page.wait_for_function('radarTileBusy===0')
    page.evaluate('radarView.paused=false;radarView.current=radarView.good;radarView.nextAt=0;radarLoopSync()')
    timing=page.evaluate('''async()=>{let events=[],last=null,fetches=0,decodes=0;const fetch0=window.fetch,decode0=window.createImageBitmap;
      window.fetch=(u,...a)=>{if(String(u).includes('radar/t/'))fetches++;return fetch0(u,...a)};window.createImageBitmap=(...a)=>{decodes++;return decode0(...a)};
      await new Promise(resolve=>{let start=performance.now();function tick(now){let f=radarView.current;if(f&&f.stamp!==last){events.push({stamp:f.stamp,at:now});last=f.stamp;}if(now-start>8500)resolve();else requestAnimationFrame(tick);}requestAnimationFrame(tick)});
      window.fetch=fetch0;window.createImageBitmap=decode0;radarView.paused=true;radarLoopSync();return {events,fetches,decodes,newest:radarView.good.stamp};}''')
    intervals=[];holds=[]
    for a,b in list(zip(timing['events'],timing['events'][1:]))[1:]:
        (holds if a['stamp']==timing['newest'] else intervals).append(b['at']-a['at'])
    assert timing['fetches']==timing['decodes']==0,timing
    assert len(holds)>=2 and all(1030<=v<=1170 for v in holds),holds
    assert intervals and 100<=sum(intervals)/len(intervals)<=125,intervals
    print('PLAYBACK',theme,dict(intervalMs=sum(intervals)/len(intervals),holds=holds,fetches=timing['fetches'],decodes=timing['decodes']),flush=True)
    page.emulate_media(reduced_motion='reduce')
    page.wait_for_timeout(100)
    for i in range(8):
        page.evaluate('i=>{radarView.current=radarReady()[i];radarEchoPaint(radarView.current);radarLoopSync()}',i)
        left=page.locator('#rad-tick').evaluate('e=>parseFloat(getComputedStyle(e).left)');assert abs(left-i/7*218)<.1
    page.locator('#rad-play').click();page.wait_for_function('radarView.singleSweep');page.wait_for_function('!radarView.singleSweep && radarView.paused',timeout=10000)
    assert page.evaluate('radarView.current===radarView.good')
    page.emulate_media(reduced_motion='no-preference')
    for target in page.locator('.rad-play,.rad-step,.rad-reset,.rad-seg').all():
        if target.is_visible():b=target.bounding_box();assert b['height']>=44 and b['width']>=44
    colors=[b[k] for b in ae._RADAR_RAMP['bands'] for k in ('start','end')]
    purity=page.evaluate('''colors=>{const rgb=colors.map(c=>{let e=document.createElement('i');e.style.color=c;document.body.append(e);let v=getComputedStyle(e).color;e.remove();return v});return [...document.querySelectorAll(RAD_CONTROLS+',.rad-tick,.rad-legend-unit,#rad-src-cap,#rad-note')].every(e=>!rgb.includes(getComputedStyle(e).color));}''',colors);assert purity
    contrast=page.evaluate('''()=>{function rgba(s){let c=document.createElement('canvas'),x=c.getContext('2d');x.fillStyle=s;x.fillRect(0,0,1,1);let a=Array.from(x.getImageData(0,0,1,1).data);a[3]/=255;return a}function lum(c){let a=c.slice(0,3).map(v=>v/255<=.04045?v/255/12.92:((v/255+.055)/1.055)**2.4);return a[0]*.2126+a[1]*.7152+a[2]*.0722}let ground=document.documentElement.dataset.theme==='night'?[255,255,255]:[0,0,0];return ['#rad-note','#rad-src-cap','.rad-loop-read','.rad-zoom-read'].map(sel=>{let style=getComputedStyle(document.querySelector(sel)),fg=rgba(style.color),bg=rgba(style.backgroundColor),a=bg[3]??1,mixed=bg.slice(0,3).map((v,i)=>v*a+ground[i]*(1-a)),x=lum(fg),y=lum(mixed);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);});}''')
    assert min(contrast)>=4.5,contrast
    # Site legend keeps exactly the same outer and unit geometry.
    page.evaluate('r=>{radarView.data={...radarView.data,...r};radarView.current=null;radarLegendRender();radarSourceRender()}',dict(sourceId='iem-nexrad-n0b',sourceMode='site',siteId='KATX',legend=dict(ae._RADAR_SITE_RAMP,remapped=True),sites=[dict(id='KATX',contributing=True)]))
    widths=page.locator('#rad-ramp i').evaluate_all('es=>es.map(e=>e.getBoundingClientRect().width)')
    assert len(widths)==10 and all(abs(a-b)<1 for a,b in zip(widths,[26.6,53.1,26.6,53.1,26.6,26.6,26.6,53.1,53.1,26.6]))
    swatch=page.locator('#rad-ramp i').first.evaluate('e=>({color:getComputedStyle(e).backgroundColor,image:getComputedStyle(e).backgroundImage})')
    assert swatch==dict(color='rgb(159, 159, 170)' if theme=='paper' else 'rgb(93, 96, 110)',image='none'),swatch
    assert page.locator('#rad-legend').bounding_box()['width']==414 and page.locator('.rad-legend-unit').bounding_box()['width']==34
    assert page.locator('#rad-ramp').get_attribute('aria-label')=='Reflectivity scale, 5 to 75 dBZ. Below 10 dBZ in grey: clear-air return, not precipitation.'
    assert page.locator('#rad-src-cap').inner_text().endswith('· from 5 dBZ')
    # Independent layer count and label-zone assertions, including dark sites.
    site_rows=[dict(id=i,lat=lat,lon=lon,primary=n==0,contributing=True,reason=None) for n,(i,lat,lon) in enumerate([('KATX',47.65,-122.33),('KLGX',46.98,-123.82),('KRTX',49.2,-122.33)])]
    page.evaluate('sites=>{radarView.active=false;radarCamera={...radarView.data.center,zoom:7};radarView.data.sites=sites;radarOverlayBuild();radarSourceRender()}',site_rows)
    assert page.locator('.rad-site-edge').count()==3
    assert page.locator('#rad-src-cap').inner_text()=='NEXRAD · KATX +2 · ~5 min volumes · IEM / NOAA · from 5 dBZ'
    for label in page.locator('.rad-site-label').all():assert 88<=float(label.get_attribute('y'))<=412
    assert 'KRTX' not in page.locator('.rad-site-label').all_text_contents()
    site_rows[0].update(contributing=False,reason='not reporting',primary=False);site_rows[1]['primary']=True
    page.evaluate('sites=>{radarView.data.sites=sites;radarView.data.siteId="KLGX";radarSourceRender()}',site_rows)
    assert page.locator('#rad-src-cap').inner_text().endswith('· KATX not reporting')
    # Actual raster channels stay on the same 26-entry LUT after browser blits.
    lut=page.evaluate('''async colors=>{radarRelease();radarView.active=false;radarCamera={...radarView.data.center,zoom:8};radarView.data.sourceId='iem-mrms-lcref';
      const f={ts:0,stamp:'197001010000',siteScans:[]},c=new OffscreenCanvas(256,256),x=c.getContext('2d'),t=radarTileSet(radarCamera,8)[0];
      colors.forEach((v,i)=>{x.fillStyle='rgba('+v[0]+','+v[1]+','+v[2]+','+(v[3]/255)+')';x.fillRect(i*9,0,9,256)});
      let key=radarTileKey(f,8,t.x,t.y);radarTileRemember(key,{key,z:8,x:t.x,y:t.y,bitmap:await createImageBitmap(c),meta:{opaquePixels:65536,unmatchedPixels:0,ambiguousPixels:0},hasEcho:true});radarEchoPaint(f);
      let p=radarWorldPoint(radarCamera.lat,radarCamera.lon,8),left=Math.round(478+t.x*256-p[0]),top=Math.round(245+t.y*256-p[1]);return colors.map((_,i)=>Array.from(document.getElementById('rad-echo').getContext('2d').getImageData(left+i*9+4,top+100,1,1).data));}''',[list(c) for _,c in ae._RADAR_LUT])
    assert lut==[list(c) for _,c in ae._RADAR_LUT],lut
    print('R24',theme,dict(contrast=contrast,legend='9 + 10 bands',cluster=cluster),flush=True)


def forecast_blend_payload():
    """Generic reproduction of the live 13:03 cold-model report (no station data)."""
    midnight = 1789257600
    temperatures = [52.6, 53.0, 53.4, 54.1, 55.3, 56.5, 56.4,
                    56.2, 56.0, 55.6, 56.1, 54.4, 54.5]
    return dict(ts=midnight + 13 * 3600 + 3 * 60, time='13:03', dayStartTs=midnight,
                temp=55.0, obsLow=53.4, obsLowTime='01:39', obsHigh=56.7,
                obsHighTime='00:00', fcLow=54.0, fcHigh=57.0, station='Test',
                tempUnit='°F', fcHourly=[[midnight + h * 3600, t]
                                       for h, t in enumerate(temperatures, 12)])


def check_forecast_blend(browser, html, data=None):
    """Sample the rendered nowcast blend, including the actual live-payload shape.

    Optional data permits verification of a saved real payload without checking its
    station metadata into the repo. The observed-only golden SVG was captured from
    5e0c4f4 before v2; it must remain byte-identical in both themes.
    """
    data = data or forecast_blend_payload()
    golden = (Path(__file__).parent / 'fixtures/forecast_observed.svg').read_text()
    context = browser.new_context(viewport={'width': 1024, 'height': 600})
    context.route('https://blend.test/**', lambda r: r.fulfill(body=html, content_type='text/html'))
    context.route('https://blend.test/wx.json**', lambda r: r.fulfill(status=404, body='missing'))
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto('https://blend.test/index.html?tabs=1')
    probe = r"""pl => {
        render(pl);
        const g = document.getElementById('spark-dyn'), fx = g.querySelector('.spark-fx');
        const dot = g.querySelector('.spark-now');
        const d = fx ? fx.getAttribute('d') : '';
        const nums = d.match(/-?\d+(?:\.\d+)?/g) || [];
        const vertices = nums.length ? [[+nums[0], +nums[1]]] : [];
        const stride = d.includes(' C ') ? 6 : 2;
        for (let i = 2; i < nums.length; i += stride)
            vertices.push([+nums[i + stride - 2], +nums[i + stride - 1]]);
        const samples = [];
        if (fx) {
            const length = fx.getTotalLength();
            // Dense sampling catches sub-hour spline retraces, not just vertices.
            for (let i = 0; i <= 2400; i++) {
                const p = fx.getPointAtLength(length * i / 2400);
                if (p.x <= 350 * 17 / 24 + .05) samples.push([p.x, p.y]);
            }
        }
        const low = [...g.querySelectorAll('.spark-pt')].find(e => +e.getAttribute('cx') > 0);
        const high = [...g.querySelectorAll('.spark-pt')].find(e => +e.getAttribute('cx') === 0);
        const css = getComputedStyle(document.documentElement);
        const swatch = document.createElement('span');
        swatch.style.color = css.getPropertyValue('--ink-soft'); document.body.appendChild(swatch);
        const ink = getComputedStyle(swatch).color; swatch.remove();
        return {d, vertices, samples, nowY: +dot.getAttribute('cy'),
            lowY: +low.getAttribute('cy'), highY: +high.getAttribute('cy'),
            labels: [...g.querySelectorAll('.spark-lbl')].map(e => e.textContent),
            high: document.querySelector('[data-k=fcHigh]').textContent,
            seam: !!g.querySelector('.spark-seam'),
            stroke: fx ? getComputedStyle(fx).stroke : null, ink,
            svg: g.closest('svg').outerHTML};
    }"""

    def measure(pl):
        result = page.evaluate(probe, pl)
        # Calibrate pixels from two observed facts, independently of the blend's
        # scale implementation (the now-dot is the authoritative start pixel).
        scale = (result['lowY'] - result['highY']) / (pl['obsHigh'] - pl['obsLow'])
        def y(temp):
            return result['lowY'] - (temp - pl['obsLow']) * scale
        return result, scale, y

    def rising(result, scale):
        running_min_y = result['samples'][0][1]
        retrace = 0
        for _, y in result['samples']:
            running_min_y = min(running_min_y, y)
            retrace = max(retrace, (y - running_min_y) / scale)
        assert retrace <= .2, f'fabricated dip before peak: {retrace:.4f}°'
        return retrace

    try:
        metrics = {}
        for theme in ('paper', 'night'):
            page.evaluate('(t) => document.documentElement.dataset.theme = t', theme)
            cold, scale, y = measure(data)
            assert cold['vertices'], 'forecast path missing'
            first_y = cold['vertices'][0][1]
            assert abs(first_y - cold['nowY']) < .6, (
                f'forecast must start at sensor: firstY={first_y}, nowY={cold["nowY"]}')
            assert abs(first_y - 84) > .6, 'forecast start pinned to chart floor'
            assert abs(first_y - y(53.02)) > .6, 'forecast still anchored at fc0'
            assert not cold['seam'], 'obsolete seam element remains'
            assert 'spark-seam' not in html, 'obsolete seam CSS/code remains'
            metrics[theme] = rising(cold, scale)
            peak = next(v for v in cold['vertices'] if abs(v[0] - 350 * 17 / 24) < .1)
            assert abs(peak[1] - y(56.5)) < .15, '17:00 peak differs from model 56.5'
            assert '57° · 17:00' in cold['labels'] and cold['high'] == '57°' and data['fcHigh'] == 57
            assert not any('14:00' in label or label.startswith('53°') for label in cold['labels']), cold['labels']
            assert cold['stroke'] == cold['ink'], (theme, cold['stroke'], cold['ink'])

            warm, warm_scale, warm_y = measure({**data, 'temp': 51.0})
            assert abs(warm['vertices'][0][1] - warm['nowY']) < .6, 'warm model must start at sensor'
            rising(warm, warm_scale)
            assert '57° · 17:00' in warm['labels']
            # The late model low is ABOVE the drawn start: it must not print as a low.
            assert not any(label.startswith('54°') for label in warm['labels']), warm['labels']
            for hour, temp in [(14, 51.674502), (15, 53.070824), (16, 54.977191), (17, 56.5)]:
                point = next(v for v in warm['vertices'] if abs(v[0] - 350 * hour / 24) < .1)
                assert abs(point[1] - warm_y(temp)) < .2, (hour, point, temp)

            agree, _, raw_y = measure({**data, 'temp': 53.02})
            model = [(13.05, 53.02)] + [((ts - data['dayStartTs']) / 3600, temp)
                for ts, temp in data['fcHourly'] if 13.05 + 1 / 60 < (ts - data['dayStartTs']) / 3600 <= 24]
            assert len(agree['vertices']) == len(model)
            for point, (hour, temp) in zip(agree['vertices'], model):
                assert abs(point[0] - 350 * hour / 24) < .1 and abs(point[1] - raw_y(temp)) < .2, (point, hour, temp)
            raw_temps = [data['obsLow'], data['obsHigh']] + [t for _, t in model]
            lo, hi = min(raw_temps), max(raw_temps)
            raw_points = [[350 * hour / 24, 84 - (temp - lo) / max(1, hi - lo) * 64]
                          for hour, temp in model]
            raw_path = page.evaluate('points => smooth(points)', raw_points)
            assert agree['d'] == raw_path, 'zero bias must produce the exact raw-model path'
            # Missing sensor disables the blend too. With fc0 already in the raw
            # range, this independently produces exactly the same forecast path.
            raw, _, _ = measure({**data, 'temp': None})
            # nowX falls back to the last observed anchor when temp is absent;
            # compare all hourly vertices, whose geometry must still be identical.
            assert agree['vertices'][1:] == raw['vertices'][1:]

            unbracketed, _, _ = measure({**data, 'fcHourly': data['fcHourly'][2:]})
            assert abs(unbracketed['vertices'][0][0] - 350 * 14 / 24) < .1, 'no bracket must leave a gap'
            close, _, _ = measure({**data, 'time': '13:58'})
            assert abs(close['vertices'][0][0] - 350 * 14 / 24) < .1, '2px guard must skip near-coincident start'
            # A turn within 1.5h retains bias. Its drawn peak rounds to 58, while
            # the model rounds to 57: the truth gate must suppress the label.
            soon_data = {**data, 'temp': 57.0, 'fcHourly': [[data['dayStartTs'] + h * 3600, t]
                for h, t in [(12, 52.6), (13, 53.0), (14, 56.5), (15, 54.0), (16, 53.5)]]}
            soon, _, soon_y = measure(soon_data)
            turn = next(v for v in soon['vertices'] if abs(v[0] - 350 * 14 / 24) < .1)
            assert abs(turn[1] - soon_y(57.66505)) < .2, 'imminent turn must retain the 1.5h horizon floor'
            assert not any('14:00' in label for label in soon['labels']), soon['labels']

            missing = {k: v for k, v in data.items() if k != 'fcHourly'}
            variants = [missing] + [{**data, 'fcHourly': hourly}
                for hourly in (None, [], [[data['dayStartTs'] - 3600, 10]])]
            for observed_data in variants:
                observed, _, _ = measure(observed_data)
                assert not observed['d'] and not observed['seam']
                assert observed['svg'] == golden, 'observed-only SVG changed from pre-v2'
        assert errors == [], errors
        return metrics
    finally:
        context.close()


def check_cold_load_no_data(browser, html):
    """Cold boot / engine down: with no wx.json the page must paint the no-data
    pose, never the design artboard's sample values (64.0°, High 82°, "Clear &
    Sunny", a July date) that ship in the markup. Guards the render({}) that runs
    before the first poll — without it those read as a real report behind only a
    small STALE mark."""
    context = browser.new_context(viewport={'width': 1024, 'height': 600})
    # Playwright runs the LAST registered matching route first: page first, wx.json last.
    context.route('https://cold.test/**', lambda route: route.fulfill(body=html, content_type='text/html'))
    context.route('https://cold.test/wx.json**', lambda route: route.fulfill(status=404, body='missing'))
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('https://cold.test/index.html?tabs=1')
    page.wait_for_timeout(300)   # well before any poll could settle
    shown = page.evaluate("""() => {
        const t = k => (document.querySelector('[data-k=' + k + ']') || {}).textContent;
        return {temp: t('temp'), hi: t('fcHigh'), lo: t('fcLow'), date: t('date'),
                cond: t('conditions'), trend: document.getElementById('trend-word').textContent};
    }""")
    assert all(v == '—' for v in shown.values()), f'design placeholders leaked on cold load: {shown}'
    body = page.evaluate('document.body.innerText').lower()
    for sample in ('82°', '64.0', 'clear & sunny', '31 jul', '4.6°'):
        assert sample not in body, f'design sample value {sample!r} visible with no data'
    page.wait_for_timeout(2500)  # freshness settles: a never-loaded page must read as stale
    assert page.locator('#staleflag').evaluate('e => e.classList.contains("on")'), 'no-data page not marked stale'
    assert errors == [], errors
    context.close()


def geography_reposition(page,theme):
    page.wait_for_function('radarGeoBusy===0')
    result=page.evaluate(r"""async()=>{
      radarGestureCancel();radarCameraSet({...radarView.data.center,zoom:8});radarView.paused=true;radarView.current=radarView.good;radarSettle();
      await new Promise(r=>setTimeout(r,1500));const start={...radarCamera},resident=new Set(radarGeoTiles.keys()),urls=[],fetch0=window.fetch;
      window.fetch=(u,...a)=>{if(String(u).includes('radar/geo/'))urls.push(String(u));return fetch0(u,...a)};
      radarBegin();const p=radarWorldPoint(start.lat,start.lon,8);radarCameraSet({...radarWorldInverse(p[0]-100,p[1],8),zoom:8});await new Promise(r=>setTimeout(r,200));const small=urls.slice();
      const end={...radarWorldInverse(p[0]-400,p[1],8),zoom:8};const needed=radarTileSet(end,8).filter(t=>!resident.has(radarGeoKey(8,t.x,t.y))).map(t=>'radar/geo/'+radarGeoKey(8,t.x,t.y)+'.png').sort();
      radarCameraSet(end);await new Promise(r=>setTimeout(r,500));window.fetch=fetch0;radarGestureCancel();return {small,large:urls.sort(),needed};
    }""")
    assert not result['small'] and result['large']==result['needed'],result
    print('GEOGRAPHY REPOSITION',theme,result,flush=True)


def review_cases(browser,server,theme):
    context=browser.new_context(viewport=dict(width=1024,height=600),has_touch=True)
    context.add_init_script(AUDIT);page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    original=copy.deepcopy(server.data);cold=copy.deepcopy(original);r=cold['radar']
    r.update(observedAt=None,observedTs=None,frameCount=0,completeFrameCount=0,zoomAuto=False,zoomDesired=5,refresh=dict(state='failed'))
    r['tiles']['frames']=[];r['tiles']['newest']=dict(stamp=None,mask='0')
    (server.root/'wx.json').write_text(json.dumps(cold))
    page.goto(server.url+'/?tabs=1&theme='+theme);page.locator('.tab[data-screen="s-radar"]').click()
    page.wait_for_function('radarMetrics.firstPaintMs!==null')
    assert page.evaluate('radarCamera.zoom')==5
    assert page.locator('#s-radar').evaluate("e=>e.classList.contains('active')")
    assert page.locator('#rad-asof').inner_text()=='—'
    # Genuine browser input: pointer capture, stationary release, then cancellation.
    page.mouse.move(500,300);page.mouse.down();page.mouse.move(620,300,steps=6)
    assert page.evaluate('radarGesture.pointers.size')==1
    assert page.locator('#rad-plate').evaluate('e=>[...radarGesture.pointers.keys()].every(id=>e.hasPointerCapture(id))')
    page.wait_for_timeout(200);page.mouse.up();before=page.evaluate('JSON.stringify(radarCamera)');page.wait_for_timeout(250)
    assert page.evaluate('JSON.stringify(radarCamera)')==before and not page.evaluate('!!radarGesture.inertia')
    cdp=context.new_cdp_session(page)
    cdp.send('Input.dispatchTouchEvent',dict(type='touchStart',touchPoints=[dict(x=400,y=300,id=1),dict(x=500,y=300,id=2)]))
    cdp.send('Input.dispatchTouchEvent',dict(type='touchMove',touchPoints=[dict(x=370,y=300,id=1),dict(x=530,y=300,id=2)]))
    page.wait_for_timeout(100);assert page.evaluate('radarCamera.zoom%1')!=0
    cdp.send('Input.dispatchTouchEvent',dict(type='touchCancel',touchPoints=[]));page.wait_for_timeout(150)
    assert page.evaluate("radarGesture.state==='idle'&&radarCamera.zoom%1===0&&radarGesture.pointers.size===0")
    # The latest viewport remains pending after failure and is delivered on retry.
    page.route('**/wx.json*',lambda route:route.abort())
    page.evaluate('radarPostIntent()');page.wait_for_timeout(300);assert page.evaluate('radarIntent.ready')
    page.unroute('**/wx.json*');page.evaluate('poll()');page.wait_for_function('!radarIntent.ready')
    # Geography receives the displayed camera even with a worker/source clamp
    # or a durable request that differs from the effective zoom.
    activity=json.loads((server.root/'radar_activity').read_text())
    camera=page.evaluate('radarCamera')
    assert activity['zoom']==camera['zoom'] and activity['theme']==theme,activity
    assert abs(activity['center']['lat']-camera['lat'])<1e-8,activity
    assert abs(activity['center']['lon']-camera['lon'])<1e-8,activity
    # Restore actual eight-frame acquisition, then stage and abandon a source.
    (server.root/'wx.json').write_text(json.dumps(original));page.evaluate('d=>{sessionStorage.removeItem("radarCamera");radarCamera=null;radarRelease();renderRadar(d);radarActivate()}',original)
    page.wait_for_function('radarReady().length===8',timeout=20000)
    findings=page.evaluate(r"""async d=>{
      radarView.paused=true;radarGestureCancel();const old=radarView.data,site=structuredClone(old);
      site.sourceId='iem-nexrad-n0b';site.sourceMode='site';site.siteId='KATX';site.tiles.frames.forEach(f=>f.siteScans=[{id:'KATX',ts:f.ts}]);
      renderRadar({radar:site});const staged=!!radarView.pendingSource;renderRadar({radar:old});await new Promise(r=>setTimeout(r,300));
      if(!staged||radarView.pendingSource||radarView.data.sourceId!==old.sourceId)throw Error('obsolete transition installed');
      const primary=structuredClone(site);radarView.acceptingSource=true;renderRadar({radar:primary});radarView.acceptingSource=false;const older=structuredClone(primary);older.siteId='KLGX';older.observedTs-=60;renderRadar({radar:older});if((radarView.pendingSource?.data||radarView.data).siteId!=='KLGX'||(radarView.pendingSource?.data||radarView.data).observedTs!==older.observedTs)throw Error('primary rewind rejected');radarView.pendingSource=null;radarView.acceptingSource=true;renderRadar({radar:old});radarView.acceptingSource=false;
      const newest=radarView.data.observedAt;radarView.current=radarView.loaded[0];radarView.data.ageSec=480;radarState();
      const header=document.getElementById('rad-status').textContent;if(!header.includes(newest)||!header.includes('8 min old'))throw Error('header measurement mismatch');
      // Historical translucency remains alpha 180 with component tiles present or absent during pan.
      const c=new OffscreenCanvas(956,490),cx=c.getContext('2d');cx.fillStyle='rgba(138,163,198,'+(180/255)+')';cx.fillRect(0,0,956,490);
      const f={...radarView.loaded[0],bitmap:await createImageBitmap(c),camera:{...radarCamera},hasEcho:true,ready:true};
      radarBegin();radarCameraSet({...radarCamera,lon:radarCamera.lon-.1});const tile=radarTileSet(radarCamera,radarLevel())[0],tileKey=radarTileKey(f,tile.z,tile.x,tile.y);radarTileRemember(tileKey,{key:tileKey,z:tile.z,x:tile.x,y:tile.y,bitmap:await createImageBitmap(c,0,0,256,256),meta:{opaquePixels:65536,unmatchedPixels:0,ambiguousPixels:0},hasEcho:true});radarEchoPaint(f);const ec=document.getElementById('rad-echo').getContext('2d');const alpha=ec.getImageData(478,245,1,1).data[3];
      if(alpha!==180||radarView.clear)throw Error('history alpha/clear changed');f.bitmap.close();c.width=c.height=0;radarGestureCancel();
      const bytes=radarMemory(),admitted=radarAdmit(41943041);if(admitted||radarMemory()>41943040)throw Error('memory admission overflow');
      return {staged,obsoleteRejected:true,header,historyAlpha:alpha,admissionBlocked:!admitted,bytes};
    }""",original)
    assert not errors,errors
    print('REVIEW CASES',theme,findings,flush=True)
    # This sequence intentionally evicted tiles in its admission-refusal probe.
    page.evaluate('d=>{radarRelease();radarView.data=d.radar;radarCamera={...d.radar.center,zoom:8};radarView.active=true;radarActivate()}',original)
    page.wait_for_function('radarReady().length===8',timeout=20000)
    geography_reposition(page,theme)
    (server.root/'wx.json').write_text(json.dumps(original));context.close()


def main():
    global QUICK
    p=argparse.ArgumentParser();p.add_argument('--browser');p.add_argument('--output-dir',type=Path,default=Path('/tmp/wfp-radar-v4'));p.add_argument('--smoke',action='store_true');args=p.parse_args();args.output_dir.mkdir(parents=True,exist_ok=True)
    QUICK=args.smoke
    with radar_server() as server, sync_playwright() as p:
        options=dict(headless=True,args=['--disable-gpu'])
        if args.browser:options['executable_path']=args.browser
        browser=p.chromium.launch(**options)
        for theme in ('paper','night'):
            smoke(browser,server,theme,args.output_dir)
            review_cases(browser,server,theme)
        check_forecast_blend(browser,Path('design/almanac/console_live.html').read_text())
        check_cold_load_no_data(browser,Path('design/almanac/console_live.html').read_text())
        browser.close()
    print('RADAR V4.1 HEADLESS PASS: paper + night',flush=True)


if __name__=='__main__':main()
