"""v4.3 real touch/click/keyboard and engine-to-canvas loopback acceptance.

Run: PYTHONPATH=. venv-test/bin/python -m tests.verify_radar_picker
Provider responses are deterministic; browser fetch, serve.py, intent watcher,
listing reuse, native/remapped caches, publication and canvas draws are real.
"""
import argparse
import copy
import json
from pathlib import Path
from types import SimpleNamespace

from playwright.sync_api import sync_playwright
from tests.verify_radar_headless import radar_server, ae

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


def setup(browser, server, theme, reduced=False, min_frames=4):
    context=browser.new_context(viewport=dict(width=1024,height=600),has_touch=True,
                                reduced_motion='reduce' if reduced else 'no-preference')
    context.add_init_script(AUDIT)
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(server.url+'/?tabs=1&theme='+theme)
    page.locator('.tab[data-screen="s-radar"]').click()
    page.wait_for_function('n=>radarView.good && radarReady().length>=n && radarIntent.owned && !radarIntent.ready',arg=min_frames)
    page.evaluate("radarView.paused=true;radarView.current=radarView.good;radarEchoDirty=true;radarLoopSync()")
    page.wait_for_timeout(250)
    return context,page,errors


def write(server, data, page=None):
    data=copy.deepcopy(data)
    if page is not None:
        page.wait_for_function('radarIntent.owned&&!radarIntent.ready')
        intent=page.evaluate('({session:radarIntent.session,generation:radarIntent.generation})')
        data['radar']['intent']=intent;data['radar']['tiles']['intent']=intent
    temp=server.root/'wx.tmp';temp.write_text(json.dumps(data));temp.replace(server.root/'wx.json')


def site_payload(original):
    data=copy.deepcopy(original);r=data['radar'];r.update(sourceId='iem-nexrad-n0b',sourceMode='site',sourcePref='site',siteId='KATX',sitePreferred=True,
        legend=dict(ae._RADAR_DISPLAY_RAMP,remapped=True),cadenceSec=300,sites=[dict(id='KATX',lat=48.1947,lon=-122.4957,contributing=True,reporting=True)],zoomMin=7,zoomMax=10)
    r['tiles'].update(source=r['sourceId'],site='KATX')
    for f in r['tiles']['frames']:f['siteScans']=[dict(id='KATX',ts=f['ts'])]
    return data


def wait_ack(page, mode):
    page.wait_for_function("mode=>radarView.data.sourceMode===mode && !radarSource.desired && !radarView.pendingSource && radarReady().length>=4 && !!radarView.current?.bitmap",arg=mode)
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
        assert sample['cap'].startswith('Switching to '+('Camano Island radar' if mode=='site' else 'Region')+' · showing '),sample
        assert post>=0,post
        epoch=page.evaluate('performance.timeOrigin+pickerAudit.start')
        received=next(t['at']-epoch for t in server.request_times[received_at:] if 'radarSource='+mode in t['path'])
        assert received>=0,received
        assert page.locator('#rad-src-'+mode).evaluate("e=>getComputedStyle(e).textDecorationStyle")=='dotted'
        page.screenshot(path=str(output/f'picker-pending-{mode}-{theme}.png'))
        page.wait_for_timeout(650)  # old payload must not erase intent/caption
        assert page.locator('#rad-src-'+mode).get_attribute('data-state')=='pending'
        assert page.locator('#rad-note').inner_text()=='Refreshing · newest frame'
        assert page.locator('#rad-src-cap').inner_text()==sample['cap']
        assert len(page.evaluate('pickerAudit.posts'))==1  # pointer + compatibility click
        if mode=='site':page.screenshot(path=str(output/f'radar-v43b-switching-{theme}.png'))
        write(server,data,page);wait_ack(page,mode)
        page.wait_for_timeout(120)
        page.screenshot(path=str(output/f'radar-v43b-{mode}-{theme}.png'))
        timings.append(dict(mode=mode,postMs=post,serverReceiptMs=received,firstFrame=sample))
    for mode,action,key in [('site','click',None),('mosaic','keyboard','Enter'),('site','keyboard',' '),('mosaic','mouse',None)]:
        button=page.locator('#rad-src-'+mode)
        if action=='click':button.evaluate('e=>e.click()')
        elif action=='mouse':button.click()
        else:button.focus();page.keyboard.press(key)
        assert button.get_attribute('data-state')=='pending'
        write(server,site if mode=='site' else original,page);wait_ack(page,mode)
    # An ordinary stalled poll cannot hold the source intent behind its deadline.
    page.evaluate("""()=>{const f=window.fetch;window.fetch=(u,...a)=>{
      window.fetch=f;if(String(u).includes('wx.json'))return new Promise(()=>{});return f(u,...a);};poll();pickerAudit.posts=[];}""")
    page.locator('#rad-src-site').tap();page.wait_for_function('pickerAudit.posts.length')
    assert page.evaluate('pickerAudit.posts[0].at-pickerAudit.start')>=0
    # Coalesce a synchronous burst into the final mode; no intermediate transaction.
    page.evaluate("pickerAudit.posts=[];document.getElementById('rad-src-mosaic').click();document.getElementById('rad-src-site').click();document.getElementById('rad-src-mosaic').click()")
    page.wait_for_function('pickerAudit.posts.length')
    assert len(page.evaluate('pickerAudit.posts'))==1
    assert 'radarSource=mosaic' in page.evaluate('pickerAudit.posts[0].url')
    write(server,original,page);wait_ack(page,'mosaic')
    # With no acknowledgement, the fast window is bounded; presentation stays honest.
    page.locator('#rad-src-site').tap();page.wait_for_timeout(21000)
    assert page.locator('#rad-src-site').get_attribute('data-state')=='pending'
    assert page.evaluate('Date.now()>=radarSource.fastUntil')
    polls=page.evaluate('pickerAudit.polls.slice(-3)')
    assert polls[-1]-polls[-2]>=290
    page.wait_for_timeout(2200)
    polls=page.evaluate('pickerAudit.polls.slice(-2)');assert polls[-1]-polls[-2]>=1900,polls
    write(server,site,page);wait_ack(page,'site')
    # A closest-site outage changes the actual frame owner, never the control.
    dark=copy.deepcopy(site);r=dark['radar'];r['siteId']='KLGX';r['tiles']['site']='KLGX'
    r['sites']=[dict(id='KLGX',contributing=True,reporting=True),
                dict(id='KATX',contributing=False,reporting=False,reason='not reporting')]
    for frame in r['tiles']['frames']:frame['siteScans']=[dict(id='KLGX',ts=frame['ts'])]
    native=server.root/'radar'/'t'/ae._radar_render_revision()/'iem-nexrad-n0b'
    for source in (native/'KATX').rglob('*.png'):
        target=native/'KLGX'/source.relative_to(native/'KATX')
        target.parent.mkdir(parents=True,exist_ok=True);target.hardlink_to(source)
    write(server,dark,page)
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


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,default=Path('/tmp/radar-v43-picker'));parser.add_argument('--engine-only',action='store_true');args=parser.parse_args();args.output_dir.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--disable-gpu'])
        for theme in ('paper','night'):
            if not args.engine_only:
                with radar_server() as server:controls(browser,server,theme,args.output_dir)
        browser.close()
    # Replace the former three-scan/paused first-pixel engine probe with the
    # shared v5.7 real TLS, partially-used-budget, four-frame advancing gate.
    from tests.verify_radar_v57 import run
    run(SimpleNamespace(output_dir=args.output_dir/'contract',flaky=False,cold_switch=True,sites=2))
    print('RADAR V5.7 PICKER PASS: paper + night',flush=True)

if __name__=='__main__':main()
