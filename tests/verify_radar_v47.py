"""Loopback-only Chromium regression: truncated manifests and a 25-second fetch."""
import argparse
import json
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright
from tests.verify_radar_headless import AUDIT, radar_server
from tests.verify_radar_v46 import SCENARIO


SHRINK = r'''async()=>{
  const {reset,step,read,manifest,warm}=v46Harness,check=(v,m)=>{if(!v)throw Error(m);};
  radarView.active=true;reset();step();step();
  await warm(radarView.good);audit.fetches=[];audit.decodeCount=0;
  const held=radarView.loaded.slice(),bitmaps=held.map(f=>f.bitmap),subject=radarView.current,deadline=radarView.nextAt;
  const r=manifest(0,7);r.tiles.frames=r.tiles.frames.slice(-1);r.frameCount=1;
  renderRadar({radar:r});
  check(radarView.loaded.length===8&&radarReady().length===8,'shrink lost inventory');
  check(held.every((f,i)=>radarView.loaded.includes(f)&&f.bitmap===bitmaps[i]&&f.bitmap.width===956),'shrink closed composites');
  check(radarView.current===subject&&radarView.nextAt===deadline,'shrink reset playback');
  check(!read().includes('Buffering')&&read().includes(subject.at),'read lost scan time');
  for(let i=0;i<12;i++)step();
  check(audit.fetches.length===0&&audit.decodeCount===0,'shrink fetched/decoded resident scans');
  r.tiles.frames=[];renderRadar({radar:r});check(radarView.loaded.length===8&&radarReady().length===8,'empty manifest lost inventory');
  check(bitmaps.every(b=>b.width===956)&&audit.fetches.length===0,'empty manifest lost bitmaps');
  const result={loaded:radarView.loaded.length,ready:radarReady().length,read:read(),fetches:0,retained:bitmaps.length,emptyRetained:true};
  // A shrunken new-stamp manifest can use resident native tiles with no I/O.
  const next=manifest(0,8);next.tiles.frames=next.tiles.frames.slice(-1);next.frameCount=1;
  await warm({...next.tiles.frames[0],revision:next.tiles.revision,sourceId:next.sourceId});
  const before=radarView.current,due=radarView.nextAt;
  renderRadar({radar:next});check(radarView.current===before&&radarView.nextAt===due,'shrunken advance cut');
  radarHistoryWork();check(radarView.good.bitmap&&radarReady().length===8,'shrunken newest not decoded');
  const sequence=[];for(let i=0;i<18&&radarView.current!==radarView.good;i++)sequence.push(step());
  check(radarView.current===radarView.good,'shrunken newest did not join wrap');
  check(bitmaps.every(b=>b.width===956),'shrunken advance closed omitted bitmap');
  check(audit.fetches.length===0&&audit.decodeCount===0,'shrunken advance refetched');
  result.shrunkenAdvance={sequence,loaded:radarView.loaded.length,ready:radarReady().length,read:read(),fetches:0};
  // Each true identity boundary must close every old composite, including cycle.
  result.identity=[];
  for(const key of ['revision','legend','grid','z','center','source','site']){
    reset();const old=radarView.loaded.map(f=>f.bitmap),changed=manifest(0,7);
    if(key==='revision')changed.tiles.revision='changed';
    if(key==='legend')changed.legend={...changed.legend,id:'changed'};
    if(key==='grid')changed.tiles.grid.x0++;
    if(key==='z')changed.tiles.z++;
    if(key==='center')changed.center.lat+=1;
    if(key==='source')changed.sourceId='rainviewer';
    if(key==='site')changed.siteId='different';
    // Exercise the independent reconciliation guard, without source handoff I/O.
    radarPreload(changed);check(old.every(b=>b.width===0),'identity leaked '+key);result.identity.push(key);
    radarView.windowKey=null;
  }
  // Omitted scans expire at the hour boundary, after their last cycle use.
  reset();const old=radarView.loaded[0],b=old.bitmap,r2=manifest(0,31);r2.tiles.frames=r2.tiles.frames.slice(-1);r2.tiles.frames[0].levels={'8':false};
  radarPreload(r2);check(!radarView.loaded.includes(old),'hour did not expire');
  radarView.cycle=[];radarView.current=null;radarView.blend=null;radarPruneFrames();check(b.width===0,'expired bitmap leaked');
  radarView.active=false;return result;
}'''


def verify(browser, server, theme, output):
    context = browser.new_context(viewport=dict(width=1024, height=600))
    context.add_init_script(AUDIT)
    context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(server.url+'/') else route.abort())
    page = context.new_page(); errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(server.url+'/?tabs=1&theme='+theme)
    page.locator('.tab[data-screen="s-radar"]').click()
    page.wait_for_function('radarReady().length===8 && radarTileBusy===0 && radarTileQueue.length===0 && radarGeoBusy===0 && radarGeoQueue.length===0')
    page.route('**/wx.json*', lambda route: route.abort())
    deterministic = page.evaluate(SCENARIO)
    shrink = page.evaluate(SHRINK)
    assert not errors, errors
    context.close()

    # Fresh real clock/browser. Native files for the next scan exist locally;
    # server threads hold those responses behind a 25-second fetch barrier.
    data = json.loads(json.dumps(server.data))
    radar = data['radar']; prior = radar['tiles']['frames'][-1]; newest = dict(prior, ts=prior['ts']+120)
    newest.update(stamp=datetime.fromtimestamp(newest['ts'], timezone.utc).strftime('%Y%m%d%H%M'),
                  at=datetime.fromtimestamp(newest['ts'], timezone.utc).strftime('%H:%M'))
    source = server.root/'radar'/'t'/radar['tiles']['revision']/radar['sourceId']/'-'
    shutil.copytree(source/prior['stamp'], source/newest['stamp'], dirs_exist_ok=True)
    context = browser.new_context(viewport=dict(width=1024, height=600));context.add_init_script(AUDIT)
    context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(server.url+'/') else route.abort())
    page = context.new_page();page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(server.url+'/?tabs=1&theme='+theme);page.locator('.tab[data-screen="s-radar"]').click()
    page.wait_for_function('radarReady().length===8 && radarTileBusy===0 && radarTileQueue.length===0 && radarGeoBusy===0 && radarGeoQueue.length===0')
    page.route('**/wx.json*', lambda route: route.abort())
    radar['tiles']['frames'] = [newest];radar['tiles']['newest']['stamp'] = newest['stamp']
    radar.update(observedTs=newest['ts'], observedAt=newest['at'], frameCount=1, completeFrameCount=0)
    gate=threading.Event();started=threading.Event()
    def delay(path):
        if '/'+newest['stamp']+'/' in path:
            started.set();gate.wait(35)
    server.request_hook = delay
    page.evaluate('r=>{window.heldV47=radarView.loaded.map(f=>({f,b:f.bitmap}));window.seenV47=[];audit.fetches=[];const label=radarFrameLabel;radarFrameLabel=f=>{seenV47.push(f.stamp);return label(f)};renderRadar({radar:r});}', radar)
    # Pump Playwright's route callbacks while waiting for the server event.
    # A blocking Event.wait can prevent route.continue_ from ever running.
    until = time.monotonic()+5
    while not started.is_set() and time.monotonic() < until:
        page.wait_for_timeout(10)
    assert started.is_set(), 'newest fetch did not start'
    start=time.monotonic();timer=threading.Timer(25,gate.set);timer.start();samples=[]
    try:
        for second in range(31):
            if second: page.wait_for_timeout(max(1, (start+second-time.monotonic())*1000))
            row=page.evaluate('''()=>({loaded:radarView.loaded.length,ready:radarReady().length,read:document.getElementById('rad-frame-time').textContent,retained:heldV47.every(({f,b})=>f.bitmap===b&&b.width===956),current:radarView.current.stamp,seen:[...new Set(seenV47)],oldFetches:audit.fetches.filter(u=>u.includes('radar/t/')&&!u.includes('/'+radarView.good.stamp+'/')).length})''')
            row.update(second=second);samples.append(row)
            print(theme, second, 'loaded='+str(row['loaded']), 'ready='+str(row['ready']), 'read='+row['read'], flush=True)
            assert row['retained'] and row['ready']==8 and row['oldFetches']==0, row
            assert 'Buffering' not in row['read'], row
        assert newest['stamp'] in samples[-1]['seen'], 'new newest never joined wrap'
        assert len(samples[24]['seen'])==8, 'old cycle did not keep playing during delay'
        page.screenshot(path=str(output/f'v47-{theme}.png'))
    finally:
        gate.set();timer.cancel();server.request_hook=None
    assert not errors, errors
    result=dict(deterministic=deterministic,shrink=shrink,delaySeconds=25,samples=samples)
    (output/f'v47-{theme}.json').write_text(json.dumps(result,indent=2));context.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,default=Path('/tmp/radar-v47'))
    args=parser.parse_args();args.output_dir.mkdir(parents=True,exist_ok=True)
    with radar_server() as server, sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--disable-gpu'])
        for theme in ('paper','night'): verify(browser,server,theme,args.output_dir)
        browser.close()
    print('RADAR V4.7 HEADLESS PASS: paper + night',flush=True)


if __name__=='__main__': main()
