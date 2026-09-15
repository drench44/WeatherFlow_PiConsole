"""v4.3 real touch/click/keyboard and engine-to-canvas loopback acceptance.

Run: PYTHONPATH=. venv-test/bin/python -m tests.verify_radar_picker
Provider responses are deterministic; browser fetch, serve.py, intent watcher,
listing reuse, native/remapped caches, publication and canvas draws are real.
"""
import argparse
import copy
import io
import json
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from tests.verify_radar_headless import radar_server, payload, ae, make_config
from tests.test_radar_hybrid import png

AUDIT = r"""(()=>{
 window.pickerAudit={posts:[],polls:[],samples:[],paint:null,start:0};
 const fetch0=window.fetch;window.fetch=(url,...args)=>{const u=String(url),a=pickerAudit;
 if(u.includes('wx.json'))a.polls.push(performance.now());
 if(u.includes('radarSource='))a.posts.push({at:performance.now(),url:u});
 return fetch0(url,...args);};
 document.addEventListener('pointerdown',e=>{if(e.target.closest('.rad-seg')){
   pickerAudit.start=performance.now();requestAnimationFrame(()=>{
    pickerAudit.samples.push({ms:performance.now()-pickerAudit.start,state:e.target.dataset.state,
      busy:document.getElementById('rad-src').getAttribute('aria-busy'),pressed:e.target.getAttribute('aria-pressed'),
      cap:document.getElementById('rad-src-cap').textContent});});}},true);
})();"""


def setup(browser, server, theme, reduced=False):
    context=browser.new_context(viewport=dict(width=1024,height=600),has_touch=True,
                                reduced_motion='reduce' if reduced else 'no-preference')
    context.add_init_script(AUDIT)
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(server.url+'/?tabs=1&theme='+theme)
    page.locator('.tab[data-screen="s-radar"]').click()
    page.wait_for_function('radarView.good && radarMetrics.drawnTiles.length>0')
    page.evaluate("radarView.paused=true;radarView.current=radarView.good;radarEchoDirty=true;radarLoopSync()")
    page.wait_for_timeout(250)
    return context,page,errors


def write(server, data):
    temp=server.root/'wx.tmp';temp.write_text(json.dumps(data));temp.replace(server.root/'wx.json')


def site_payload(original):
    data=copy.deepcopy(original);r=data['radar'];r.update(sourceId='iem-nexrad-n0b',sourceMode='site',sourcePref='site',siteId='KATX',sitePreferred=True,
        legend=dict(ae._RADAR_SITE_RAMP,remapped=True),cadenceSec=300,sites=[dict(id='KATX',lat=48.1947,lon=-122.4957,contributing=True,reporting=True)],zoomMin=7,zoomMax=10)
    r['tiles'].update(source=r['sourceId'],site='KATX')
    for f in r['tiles']['frames']:f['siteScans']=[dict(id='KATX',ts=f['ts'])]
    return data


def wait_ack(page, mode):
    page.wait_for_function("mode=>radarView.data.sourceMode===mode && !radarSource.desired && !radarView.pendingSource && radarMetrics.drawnTiles.some(k=>k.includes(mode==='site'?'iem-nexrad-n0b':'iem-mrms-lcref'))",arg=mode)
    assert page.locator('#rad-src').get_attribute('aria-busy')=='false'
    assert page.locator('#rad-src-'+mode).get_attribute('aria-pressed')=='true'
    assert page.locator('#rad-src-'+mode).get_attribute('data-state')=='confirmed'
    assert 'switching' not in page.locator('#rad-src-cap').inner_text()


def controls(browser,server,theme,output):
    original=copy.deepcopy(server.data);original['radar'].update(sourceMode='mosaic',sourcePref='mosaic',refresh=dict(state='newest',frameIndex=0,frameTotal=8))
    site=site_payload(original);write(server,original)
    context,page,errors=setup(browser,server,theme,reduced=theme=='night')
    assert page.locator('#rad-src-site').inner_text()=='KATX'
    assert page.locator('#rad-src-site').get_attribute('aria-label')=='KATX: Camano Island radar, high resolution, 39 mi NE'
    timings=[]
    for mode,data in [('site',site),('mosaic',original)]:
        page.evaluate('pickerAudit.posts=[];pickerAudit.samples=[]')
        received_at=len(server.request_times)
        page.locator('#rad-src-'+mode).tap()
        page.wait_for_function('pickerAudit.samples.length && pickerAudit.posts.length')
        sample=page.evaluate('pickerAudit.samples[0]');post=page.evaluate('pickerAudit.posts[0].at-pickerAudit.start')
        assert sample['state']=='pending' and sample['busy']=='true' and sample['pressed']=='false',sample
        assert sample['cap']==('Many radars blended · new image every 2 min · IEM / NOAA' if mode=='site' else 'Camano Island radar, high resolution · 39 mi NE · new scan every ~5 min · IEM / NOAA'),sample
        assert post<100,post
        epoch=page.evaluate('performance.timeOrigin+pickerAudit.start')
        received=next(t['at']-epoch for t in server.request_times[received_at:] if 'radarSource='+mode in t['path'])
        assert received<100,received
        assert page.locator('#rad-src-'+mode).evaluate("e=>getComputedStyle(e).textDecorationStyle")=='dotted'
        page.screenshot(path=str(output/f'picker-pending-{mode}-{theme}.png'))
        page.wait_for_timeout(650)  # old payload must not erase intent/caption
        assert page.locator('#rad-src-'+mode).get_attribute('data-state')=='pending'
        assert page.locator('#rad-note').inner_text()=='Refreshing · newest frame'
        assert page.locator('#rad-src-cap').inner_text()==sample['cap']+' · switching'
        assert len(page.evaluate('pickerAudit.posts'))==1  # pointer + compatibility click
        if mode=='site':page.screenshot(path=str(output/f'radar-v43b-switching-{theme}.png'))
        write(server,data);wait_ack(page,mode)
        page.wait_for_timeout(120)
        page.screenshot(path=str(output/f'radar-v43b-{mode}-{theme}.png'))
        timings.append(dict(mode=mode,postMs=post,serverReceiptMs=received,firstFrame=sample))
    for mode,action,key in [('site','click',None),('mosaic','keyboard','Enter'),('site','keyboard',' '),('mosaic','mouse',None)]:
        button=page.locator('#rad-src-'+mode)
        if action=='click':button.evaluate('e=>e.click()')
        elif action=='mouse':button.click()
        else:button.focus();page.keyboard.press(key)
        assert button.get_attribute('data-state')=='pending'
        write(server,site if mode=='site' else original);wait_ack(page,mode)
    # An ordinary stalled poll cannot hold the source intent behind its deadline.
    page.evaluate("""()=>{const f=window.fetch;window.fetch=(u,...a)=>{
      window.fetch=f;if(String(u).includes('wx.json'))return new Promise(()=>{});return f(u,...a);};poll();pickerAudit.posts=[];}""")
    page.locator('#rad-src-site').tap();page.wait_for_function('pickerAudit.posts.length')
    assert page.evaluate('pickerAudit.posts[0].at-pickerAudit.start')<100
    # Coalesce a synchronous burst into the final mode; no intermediate transaction.
    page.evaluate("pickerAudit.posts=[];document.getElementById('rad-src-mosaic').click();document.getElementById('rad-src-site').click();document.getElementById('rad-src-mosaic').click()")
    page.wait_for_function('pickerAudit.posts.length')
    assert len(page.evaluate('pickerAudit.posts'))==1
    assert 'radarSource=mosaic' in page.evaluate('pickerAudit.posts[0].url')
    wait_ack(page,'mosaic')
    # With no acknowledgement, the fast window is bounded; presentation stays honest.
    page.locator('#rad-src-site').tap();page.wait_for_timeout(21000)
    assert page.locator('#rad-src-site').get_attribute('data-state')=='pending'
    assert page.evaluate('Date.now()>=radarSource.fastUntil')
    polls=page.evaluate('pickerAudit.polls.slice(-3)')
    assert polls[-1]-polls[-2]>=290
    page.wait_for_timeout(2200)
    polls=page.evaluate('pickerAudit.polls.slice(-2)');assert polls[-1]-polls[-2]>=1900,polls
    write(server,site);wait_ack(page,'site')
    # A closest-site outage changes the actual frame owner, never the control.
    dark=copy.deepcopy(site);r=dark['radar'];r['siteId']='KLGX';r['tiles']['site']='KLGX'
    r['sites']=[dict(id='KLGX',contributing=True,reporting=True),
                dict(id='KATX',contributing=False,reporting=False,reason='not reporting')]
    for frame in r['tiles']['frames']:frame['siteScans']=[dict(id='KLGX',ts=frame['ts'])]
    native=server.root/'radar'/'t'/ae._radar_render_revision()/'iem-nexrad-n0b'
    for source in (native/'KATX').rglob('*.png'):
        target=native/'KLGX'/source.relative_to(native/'KATX')
        target.parent.mkdir(parents=True,exist_ok=True);target.hardlink_to(source)
    write(server,dark)
    page.wait_for_function("radarView.data.siteId==='KLGX' && radarView.current?.drawnSites?.some(s=>s.id==='KLGX')")
    assert page.locator('#rad-src-site').inner_text()=='KATX'
    assert page.locator('#rad-src-site').get_attribute('aria-label')=='KATX: Camano Island radar, high resolution, 39 mi NE'
    cap=page.locator('#rad-src-cap').inner_text()
    assert cap.startswith('Langley Hill radar') and cap.endswith(' · KATX not reporting'),cap
    assert '39 mi' not in cap
    page.screenshot(path=str(output/f'radar-v51-dark-closest-{theme}.png'))
    assert not errors,errors
    print('PICKER',theme,json.dumps(timings),flush=True)
    context.close();write(server,original)


def engine_warm(browser,server,theme):
    # Discard fixture echoes: every warm tile below must be generated by the real
    # emitter's mosaic idle tier, using native provider bytes and metadata.
    shutil.rmtree(server.root/'radar'/'t')
    for name in ('radar_intent','radar_source','radar_zoom','radar_viewed','radar_viewing','radar_center'):
        (server.root/name).unlink(missing_ok=True)
    app=SimpleNamespace(config=make_config(),obsParser=SimpleNamespace(api_data={}))
    e=ae.AlmanacEmitter(SimpleNamespace(app=app,Obs={},Met={},Astro={},Sager={}),output_path=str(server.root/'wx.json'))
    latest=int(time.time())//120*120-240;calls=[];raw=png();site_raw=png((12,145,16,255))
    def provider(session,req,timeout):
        url=req.full_url;calls.append((time.perf_counter(),url))
        if url==ae.RADAR_IEM_METADATA_URL:
            body=json.dumps(dict(meta=dict(end_valid=datetime.fromtimestamp(latest,timezone.utc).isoformat(),product='lcref',units='0.5 dBZ'))).encode()
        elif 'operation=list' in url:
            body=json.dumps(dict(scans=[dict(ts=datetime.fromtimestamp(latest-60-offset,timezone.utc).isoformat()) for offset in (600,300,0)])).encode()
        elif req.get_method()=='HEAD':body=b''
        else:body=site_raw if "ridge::" in url else raw
        response=io.BytesIO(body);response.status=200;response.headers={};return response
    stop=threading.Event();enabled=threading.Event()
    def watch():
        while not stop.wait(ae.RADAR_INTENT_CHECK_SEC):
            if enabled.is_set():e._check_radar_zoom()
    with patch.object(ae,'RADAR_DIR',str(server.root/'radar')),patch.object(ae.RadarSession,'open',provider):
        # Emission replaces Kivy's schedule-once delivery, not acquisition logic.
        e._radar_emit_now=lambda:e._emit(0)
        e._do_radar();e._emit(0)
        context,page,errors=setup(browser,server,theme)
        e._do_radar(intent_triggered=True,view_started=True)
        assert any(k[0]=='iem-nexrad-n0b' for k in e._radar_tiles)
        assert list((server.root/'radar'/'t').glob('*/iem-nexrad-n0b/KATX/*/8/*/*.png'))
        assert e._radar_result.source_mode=='mosaic'
        page.wait_for_timeout(400)
        e._running=True
        worker=threading.Thread(target=watch,daemon=True);worker.start()
        try:
            # Keep the listing cache age real; do not pre-publish a site payload.
            page.evaluate("""()=>{const paint=radarEchoPaint;radarEchoPaint=function(f,...a){const result=paint(f,...a);
              if(!pickerAudit.paint&&radarView.data.sourceMode==='site'&&f?.hasEcho&&radarMetrics.drawnTiles.some(k=>k.includes('iem-nexrad-n0b')))pickerAudit.paint=performance.now();return result;};pickerAudit.posts=[];}""")
            calls.clear();enabled.set()
            page.locator('#rad-src-site').tap()
            try:page.wait_for_function('pickerAudit.paint!==null',timeout=1000)
            except Exception:
                print('WARM DEBUG',theme,e._radar_result.source_mode,e._radar_refresh,len(calls),page.evaluate('({source:radarView.data.sourceMode,desired:radarSource,pending:!!radarView.pendingSource,good:radarView.good,drawn:radarMetrics.drawnTiles,posts:pickerAudit.posts})'),errors,flush=True)
                raise
            elapsed=page.evaluate('pickerAudit.paint-pickerAudit.start')
            listings=[u for _,u in calls if 'operation=list' in u]
            assert elapsed<=1000 and not listings,(elapsed,listings)
            wait_ack(page,'site');assert not errors,errors
            print('ENGINE WARM',theme,json.dumps(dict(tapToSiteEchoMs=elapsed,listingRequests=len(listings),postMs=page.evaluate('pickerAudit.posts[0].at-pickerAudit.start'))),flush=True)
            # v4.6: returning to the retained Region window may read/decode local
            # PNGs, but must not acquire native provider tiles again.
            page.wait_for_function("radarView.data.refresh.state==='idle'")
            native_count=lambda:sum('mrms::' in u or 'ridge::' in u for _,u in calls)
            before=native_count()
            page.locator('#rad-src-mosaic').tap();wait_ack(page,'mosaic')
            try:
                page.wait_for_function("radarView.data.sourceMode==='mosaic' && radarReady().length>=2 && radarReady().length===radarView.loaded.filter(f=>f.levels[String(radarLevel())]).length && radarTileBusy===0 && radarTileQueue.length===0",timeout=20000)
            except Exception:
                print('REGION DEBUG',theme,page.evaluate('({data:radarView.data,ready:radarReady().length,job:radarCompositeJob&&{done:radarCompositeJob.done.size,total:radarCompositeJob.tiles.length},pending:radarTileBusy,absent:[...radarTileAbsent]})'),calls,errors,flush=True)
                raise
            after=native_count()
            assert after==before,(before,after,calls)
            assert not errors,errors
            print('REGION NATIVE REUSE',theme,json.dumps(dict(before=before,after=after,decoded=page.evaluate('radarReady().length'))),flush=True)
        finally:
            enabled.clear();stop.set();worker.join(3)
            deadline=time.monotonic()+10
            while e._inflight and time.monotonic()<deadline:time.sleep(.05)
            e.stop();context.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,default=Path('/tmp/radar-v43-picker'));parser.add_argument('--engine-only',action='store_true');args=parser.parse_args();args.output_dir.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--disable-gpu'])
        for theme in ('paper','night'):
            if not args.engine_only:
                with radar_server() as server:controls(browser,server,theme,args.output_dir)
            with radar_server() as server:engine_warm(browser,server,theme)
        browser.close()
    print('RADAR V4.3 PICKER PASS: paper + night',flush=True)

if __name__=='__main__':main()
