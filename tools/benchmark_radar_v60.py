"""Local Chromium zoom-step counters and renderer CPU, using the v5.9 fixture."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright
from tests.verify_radar_headless import radar_server
from tests.verify_radar_v59 import context_page


INSTRUMENT = r'''()=>{
  window.step60={frames:[],rows:[],frame:0};
  const frame=radarFrame,echo=radarEchoPaint,history=radarHistoryWork,trace=radarTrace;
  radarFrame=now=>{const row={echo:0,retained:0,history:0,decode:0,composites:0,draws:0};step60.row=row;step60.frame++;const start=performance.now();frame(now);row.ms=performance.now()-start;step60.frames.push(row);step60.row=null;};
  radarEchoPaint=(...args)=>{if(step60.row){step60.row.echo++;step60.row.retained+=Number(!!args[0]?.bitmap&&JSON.stringify(args[0].camera)!==JSON.stringify(radarCamera));}return echo(...args);};
  radarHistoryWork=(...args)=>{if(step60.row)step60.row.history++;return history(...args);};
  radarTrace=(phase,...args)=>{if(step60.row){if(phase==='decodeStart')step60.row.decode++;if(phase==='decoded')step60.row.composites++;}return trace(phase,...args);};
  const draw=CanvasRenderingContext2D.prototype.drawImage;
  CanvasRenderingContext2D.prototype.drawImage=function(...args){if(step60.row&&this.canvas.id==='rad-echo')step60.row.draws++;return draw.apply(this,args);};
}'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', required=True)
    parser.add_argument('--html', type=Path, help='local baseline HTML; default is the working tree')
    args = parser.parse_args()
    results = []
    with radar_server() as server, sync_playwright() as p:
        if args.html:
            (server.root/'index.html').write_text(args.html.read_text())
        browser = p.chromium.launch(headless=True, args=['--disable-gpu'])
        for theme in ('paper', 'night'):
            context, page = context_page(browser, server, theme)
            page.route('**/wx.json*', lambda route: route.abort())
            page.wait_for_function('radarView.cycle.length===8')
            page.evaluate(INSTRUMENT)
            cdp = context.new_cdp_session(page)
            cdp.send('Performance.enable')
            # Align both runs to an eight-scan cycle with a deadline during ease.
            for z in (7, 8):
                before = {m['name']: m['value'] for m in cdp.send('Performance.getMetrics')['metrics']}
                page.evaluate('''z=>{step60.frames=[];step60.start=performance.now();radarView.blend=null;radarView.current=radarView.cycle[1];radarView.nextAt=step60.start+150;radarLoopSync();radarZoomChange(z-Math.round(radarCamera.zoom));}''', z)
                # Real RAF and acquisition; completion condition instead of sleeps.
                page.wait_for_function('performance.now()-step60.start>=600')
                after = {m['name']: m['value'] for m in cdp.send('Performance.getMetrics')['metrics']}
                row = page.evaluate('''()=>({frames:step60.frames,elapsedMs:performance.now()-step60.start,ready:radarReady().length,note:document.getElementById('rad-note').textContent})''')
                row.update(theme=theme, zoom=z, chromium=browser.version, retainedWindow=8, startingDeadlineMs=150, taskMs=1000*(after['TaskDuration']-before['TaskDuration']), scriptMs=1000*(after['ScriptDuration']-before['ScriptDuration']))
                results.append(row)
                page.wait_for_function('!radarView.holdingWindow && radarView.cycle.length===8 && radarView.loaded.every(f=>f.bitmap) && !radarJobs.size && !radarTileQueue.length', timeout=60000)
            context.close()
        browser.close()
    Path(args.o).write_text(json.dumps(results, indent=2))
    for row in results:
        frames = row.pop('frames')
        row.update(raf=len(frames), echo=sum(f['echo'] for f in frames), maxEcho=max(f['echo'] for f in frames), retained=sum(f['retained'] for f in frames), maxDecode=max(f['decode'] for f in frames), maxComposites=max(f['composites'] for f in frames), echoDraws=sum(f['draws'] for f in frames), frameCpuMs=sum(f['ms'] for f in frames))
        print(json.dumps(row), flush=True)


if __name__ == '__main__':
    main()
