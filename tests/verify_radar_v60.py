"""Both-theme picker evidence/refusal and deterministic paint-deadline collision."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright
from tests.verify_radar_headless import radar_server
from tests.verify_radar_v59 import SCENARIO, context_page


PICKER = r'''()=>{
  const check=(ok,msg)=>{if(!ok)throw Error(msg);};
  radarPostIntent=()=>{radarIntent.postedAt=Date.now();radarIntent.generation++;};
  const base=structuredClone(radarView.data),cap=document.getElementById('rad-src-cap'),button=document.getElementById('rad-src-site'),note=document.getElementById('rad-note');
  radarSource.desired=null;clearTimeout(radarSwitch?.timer);radarSwitch=null;radarSourceRender();
  const region=cap.textContent,read=document.getElementById('rad-frame-time').textContent;
  const publish=(state,extra={})=>{const r=structuredClone(base);r.nexrad={...r.nexrad,...state};Object.assign(r,extra);renderRadar({radar:r,ts:Date.now()/1000,alerts:[]});return r;};
  const dark={reporting:false,newestTs:null,ageSec:null,reason:'not reporting',checkedTs:1000,checkedAt:'09:12',nextCheckTs:2000,nextCheckAt:'09:32'};
  publish(dark);
  check(!button.disabled,'dark site must stay tappable');
  check(button.textContent.startsWith('KATX')&&button.dataset.detail==='off air · checked 09:12','before-tap evidence');
  check(button.getAttribute('aria-label').includes('off air'),'accessible evidence');
  check(cap.textContent===region,'Region caption changed');
  radarChooseSource('site');
  check(note.dataset.shown==='true'&&note.textContent==='KATX is off air · showing Region · Checking again at 09:32','immediate dark refusal');
  check(!radarSwitch&&!radarSource.desired,'dark site entered hysteresis');
  check(document.getElementById('rad-frame-time').textContent===read,'dark tap changed read');
  const generation=radarIntent.generation;
  publish(dark,{sourcePref:'site',sourceFallback:'site-not-reporting',refresh:{state:'idle',reason:'not reporting',intent:{session:radarIntent.session,generation}}});
  check(!radarSwitch&&note.textContent.includes('Checking again at 09:32'),'same-poll engine refusal');
  // A stale empty-listing refusal must never cancel a later Region choice.
  radarSource.refused=false;radarIntent.generation++;radarSource.desired='mosaic';
  publish(dark,{refresh:{state:'idle',reason:'not reporting',intent:{session:radarIntent.session,generation}}});
  check(!radarSource.refused,'old refusal crossed generation');radarSource.desired=null;
  publish({...dark,newestTs:500,ageSec:1620});
  check(button.dataset.detail==='last scan 27 min ago'&&!button.disabled,'stale scan mislabeled off air');
  radarChooseSource('site');check(radarSwitch&&!radarSource.refused,'stale listing treated as empty');
  clearTimeout(radarSwitch.timer);radarSwitch=null;radarSource.desired=null;
  publish({...dark,reason:'scan unavailable'});
  check(button.dataset.detail==='scan unavailable','listing error mislabeled off air');
  radarChooseSource('site');check(radarSwitch&&!radarSource.refused,'transport failure treated as empty');
  clearTimeout(radarSwitch.timer);radarSwitch=null;radarSource.desired=null;
  publish({...dark,reporting:null,reason:null,checkedTs:null});
  check(button.dataset.detail==='','unknown site guessed');
  // Knowledge can first arrive in the reply to an already pending tap.
  radarChooseSource('site');check(radarSwitch,'unknown should try');
  publish(dark,{sourcePref:'site',sourceFallback:'site-not-reporting',refresh:{state:'idle',reason:'not reporting',intent:{session:radarIntent.session,generation:radarIntent.generation}}});
  check(!radarSwitch&&!radarSource.desired&&note.dataset.shown==='true'&&note.textContent.includes('KATX is off air'),'new evidence not applied in same poll');
  check(cap.textContent===region,'refusal changed Region caption');
  const refusal=note.textContent;
  radarChooseSource('mosaic');check(!radarSource.refused&&radarIntent.preferredMode==='mosaic','cannot cancel refused site preference');
  radarSource.desired=null;clearTimeout(radarSwitch?.timer);radarSwitch=null;
  const beforeTap=radarIntent.generation;
  button.dispatchEvent(new PointerEvent('pointerdown',{isPrimary:true,button:0}));
  button.dispatchEvent(new MouseEvent('click',{detail:1}));
  check(radarIntent.generation===beforeTap+1&&note.textContent===refusal,'compatibility click duplicated dark intent');
  check(note.scrollWidth<=note.clientWidth,'refusal note clipped');
  return {detail:button.dataset.detail,note:note.textContent,caption:cap.textContent};
}'''


def main():
    output = Path('/private/tmp/radar-v60-headless')
    output.mkdir(exist_ok=True)
    results = []
    # Force camera/tile damage to coincide with every playback deadline. The
    # real v5.9 compositor, retained pixels and adoption checks still run.
    scenario = SCENARIO.replace("radarSource.desired=null;", "radarSource.desired=null;radarSource.refused=false;").replace(
        '    radarFrame(clock);',
        '''    let echoCalls=0;const echo=radarEchoPaint;radarEchoPaint=(...args)=>{echoCalls++;return echo(...args);};
    if(radarView.nextAt&&clock>=radarView.nextAt)radarEchoDirty=true;
    radarFrame(clock);radarEchoPaint=echo;check(echoCalls<=1,'duplicate deadline repaint '+echoCalls);''')
    with radar_server() as server, sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--disable-gpu'])
        for theme in ('paper', 'night'):
            context, page = context_page(browser, server, theme)
            page.route('**/wx.json*', lambda route: route.abort())
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            result = page.evaluate(PICKER)
            page.wait_for_function("getComputedStyle(document.getElementById('rad-note')).opacity==='1'")
            page.screenshot(path=str(output/f'{theme}-picker.png'))
            result.update(theme=theme, geometry=page.evaluate(scenario, 'zoom'))
            assert not errors, errors
            results.append(result)
            context.close()
            print(theme, 'RADAR V6.0 PASS: evidence, immediate refusal, generation fence, truthful acquisition, one deadline paint', flush=True)
        browser.close()
    (output/'assertions.json').write_text(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
