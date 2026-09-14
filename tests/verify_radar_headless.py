"""Direct Python Playwright check; run from the repo root (no local server needed).

./venv-test/bin/python -m tests.verify_radar_headless [--browser /path/to/chromium]
Screenshots and recorded fetch URLs go to --output-dir (default /tmp/wfp-radar-basemap).
Kept separate from pytest so the unit suite does not require a browser install.
"""
import argparse
import base64
import io
import importlib.util
import threading
import tempfile
from contextlib import contextmanager
from unittest.mock import patch
import json
import time
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

from tests import conftest  # noqa: F401 - the same Kivy stubs used by pytest
from tests.fixtures.config import make_config
from lib import almanac_emit as ae


def payload():
    config = make_config()
    app = SimpleNamespace(config=config, obsParser=SimpleNamespace(api_data={}))
    screen = SimpleNamespace(app=app, Obs={}, Met={}, Astro={}, Sager={})
    emitter = ae.AlmanacEmitter(screen)
    now = int(time.time())
    zoom = ae._radar_zoom_for(47.61)
    _, mpp, bounds, _ = ae._radar_viewport(47.61, -122.33, zoom, 956, 490)
    bar, rings = ae._radar_scale(mpp, 490, 'mi')
    png = io.BytesIO()
    Image.new('RGBA', (956, 490), (0, 163, 224, 100)).save(png, format='PNG')
    url = 'data:image/png;base64,' + base64.b64encode(png.getvalue()).decode()
    frames = (dict(id=str(now - 600), ts=now - 600, complete=False),
              dict(id=str(now), ts=now, complete=True, url=url))
    emitter._radar_result = ae._RadarResult(True, None, frames, str(now), now,
        dict(lat=47.61, lon=-122.33), zoom, mpp, bounds, bar, rings,
        ae._radar_nexrad(47.61, -122.33, 'mi'), now)
    return emitter._build_payload()


def loop_payload():
    """Three complete, opaque (echo-bearing) frames so the Phase 2 loop runs."""
    config = make_config()
    app = SimpleNamespace(config=config, obsParser=SimpleNamespace(api_data={}))
    screen = SimpleNamespace(app=app, Obs={}, Met={}, Astro={}, Sager={})
    emitter = ae.AlmanacEmitter(screen)
    now = int(time.time())
    zoom = ae._radar_zoom_for(47.61)
    _, mpp, bounds, _ = ae._radar_viewport(47.61, -122.33, zoom, 956, 490)
    bar, rings = ae._radar_scale(mpp, 490, 'mi')
    def frame(offset, rgb):
        png = io.BytesIO()
        Image.new('RGBA', (956, 490), rgb + (255,)).save(png, format='PNG')   # opaque -> hasEcho
        return dict(id=str(now - offset), ts=now - offset, complete=True,
                    url='data:image/png;base64,' + base64.b64encode(png.getvalue()).decode())
    frames = (frame(1200, (60, 180, 220)), frame(600, (40, 120, 200)), frame(0, (210, 70, 60)))
    emitter._radar_result = ae._RadarResult(True, None, frames, str(now), now,
        dict(lat=47.61, lon=-122.33), zoom, mpp, bounds, bar, rings,
        ae._radar_nexrad(47.61, -122.33, 'mi'), now)
    return emitter._build_payload()


def check_loop(browser, html):
    """Phase 2: the past-hour loop cycles on the tab, pauses, and idles off-tab."""
    shim = ("window.fetch=function(u){var d=PAYLOAD;d.ts=Date.now()/1000;"
            "return Promise.resolve({ok:true,headers:{get:function(){return new Date().toUTCString();}},"
            "json:function(){return Promise.resolve(d);}});};").replace('PAYLOAD', json.dumps(loop_payload()))
    context = browser.new_context(viewport={'width': 1024, 'height': 600})
    context.add_init_script(shim)
    context.route('https://radar.test/**', lambda route: route.fulfill(body=html, content_type='text/html'))
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('https://radar.test/index.html?tabs=1')
    page.wait_for_function('window.fetch && document.querySelector(".tab[data-screen=\\"s-radar\\"]")')
    page.locator('.tab[data-screen="s-radar"]').click()
    page.wait_for_function('radarView.loaded.length === 3 && radarView.loaded.every(f => f.ready && f.hasEcho)')
    assert page.locator('#rad-loop').is_visible(), 'loop control should show for a 3-frame history'
    # the echo cycles: collect distinct sources and frame-time labels over ~2.5 s
    seen = page.evaluate("""async () => {
        const srcs = new Set(), times = new Set();
        for (let i = 0; i < 12; i++) {
            srcs.add(radarView.current.id);
            times.add(document.getElementById('rad-frame-time').textContent);
            await new Promise(r => setTimeout(r, 250));
        }
        return { srcs: srcs.size, times: [...times].sort() };
    }""")
    assert seen['srcs'] >= 2, f'loop did not cycle the echo: {seen}'
    assert len(seen['times']) >= 2, f'frame-time label did not change: {seen}'
    # pause holds a single frame
    page.locator('#rad-play').click()
    held = page.evaluate('radarView.current.id')
    page.wait_for_timeout(1400)
    assert page.evaluate('radarView.current.id') == held, 'pause did not hold the frame'
    page.locator('#rad-play').click()   # resume
    # leaving the tab stops the loop and hides the control
    page.locator('.tab[data-screen="s-obs"]').click()
    page.wait_for_timeout(200)
    assert page.locator('#rad-loop').is_hidden(), 'loop control should hide off-tab'
    assert not page.evaluate('!!radarView.timer'), 'loop timer should be cleared off-tab'
    assert errors == [], errors
    context.close()


def source_payload(source):
    """Same latest ID/timestamp across providers deliberately exercises collision safety."""
    data = loop_payload()
    r = data['radar']
    settings = ae._RADAR_SOURCES[source]
    r.update(sourceId=source, provider=settings['provider'], attribution=settings['attribution'],
             attributionUrl=settings['attribution_url'], cadenceSec=settings['cadence'],
             frameSpacingSec=settings['cadence'], historySpanSec=2 * settings['cadence'],
             completeFrameCount=3, updatedAt='21:08', observedAt='21:04',
             zoomMax=settings['max_zoom'], zoomSource='MRMS' if source.startswith('iem') else 'RainViewer')
    legend = settings['legend']
    r['legend'] = dict(legend, remapped=True)
    for i, frame in enumerate(r['frames']):
        offset = (2 - i) * settings['cadence']
        minutes = 21 * 60 + 4 - offset // 60
        frame.update(id='collision-' + str(i), ts=r['observedTs'] - offset,
                     at=f'{minutes // 60:02d}:{minutes % 60:02d}')
        color = (51, 102, 204) if source.startswith('iem') else (0, 163, 224)
        stream = io.BytesIO()
        Image.new('RGBA', (956, 490), color + (255,)).save(stream, 'PNG')
        frame['url'] = 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()
    r['latest'] = r['frames'][-1]['id']
    data['alerts'] = [dict(event='Flood Watch', level=3, levelName='watch',
                           areaShort='King County', untilText='11 PM')]
    data['alerts'][0]['class'] = 'water'
    data['alertsStale'] = False
    return data


def check_source_switch(browser, html, output_dir, theme):
    initial = source_payload('iem-mrms-lcref')
    initial['radar']['frameCount'] = 31  # history warming: longest cadence caption
    shim = """
        window.testPayload = PAYLOAD;
        window.fetch = function() {
            testPayload.ts = Date.now() / 1000;
            return Promise.resolve({ok:true, headers:{get:()=>new Date().toUTCString()},
                                    json:()=>Promise.resolve(testPayload)});
        };
    """.replace('PAYLOAD', json.dumps(initial))
    context = browser.new_context(viewport={'width': 1024, 'height': 600}, device_scale_factor=2)
    context.add_init_script(shim)
    context.route('https://radar.test/**', lambda route: route.fulfill(body=html, content_type='text/html'))
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(f'https://radar.test/index.html?tabs=1&theme={theme}')
    page.locator('.tab[data-screen="s-radar"]').click()
    page.wait_for_function('radarView.loaded.length === 3 && radarView.loaded.every(f => f.ready)')
    page.wait_for_function('document.getElementById("rad-frame-time").textContent.includes("newest")')
    assert page.locator('#rad-asof').inner_text() == '21:04'
    assert page.locator('#rad-asof').evaluate('(e) => getComputedStyle(e).fontSize') == '17px'
    assert page.evaluate('radarView.data.updatedAt') == '21:08'
    assert '2 min' in page.locator('#rad-src-cap').inner_text()
    assert page.locator('#rad-ramp i').count() == 9
    assert page.locator('#rad-attrib').inner_text() == 'IEM / NOAA'
    assert page.locator('#alert-strip').is_visible()
    # The render must never expose a palette/image mismatch, even for one paint.
    page.evaluate("""() => {
        window.mismatches = [];
        function inspect() {
            const e = document.getElementById('rad-echo'), r = radarView.data;
            if (r && !e.hidden) {
                const c = document.createElement('canvas'); c.width=1; c.height=1;
                const x=c.getContext('2d'); x.drawImage(e,0,0,1,1);
                const red=x.getImageData(0,0,1,1).data[0];
                const swatch=document.querySelector('#rad-ramp i').style.background;
                const iem=r.sourceId === 'iem-mrms-lcref';
                if (red !== (iem ? 51 : 0) || !swatch.includes('rgb(138, 163, 198)')) mismatches.push([r.sourceId, red, swatch]);
            }
            window.inspectId = requestAnimationFrame(inspect);
        }
        inspect();
    }""")
    for selector in ('#rad-plate', '#rad-src', '#rad-loop', '#rad-src-cap', '#rad-legend'):
        box = page.locator(selector).bounding_box()
        assert box and box['y'] >= 0 and box['y'] + box['height'] <= 568, (theme, selector, box)
        assert box['x'] + box['width'] <= 1024, (theme, selector, box)
    page.wait_for_timeout(500)  # alert entrance animation has settled
    page.screenshot(path=str(output_dir / f'radar-hybrid-{theme}.png'))
    # Pausing preserves the selected frame and uses its emitter-provided local time.
    page.locator('#rad-play').click()
    assert page.locator('#rad-frame-time').inner_text().startswith(page.evaluate('radarView.current.at') + ' · ')
    assert page.locator('#rad-asof').inner_text() == '21:04'
    page.locator('#rad-play').click()

    pending = []
    page.route('https://radar.test/switch-*.png', lambda route: pending.append(route))
    rv = source_payload('rainviewer')
    rv['radar']['frameCount'] = 7
    rv['radar']['frames'][-1]['url'] = 'https://radar.test/switch-rv.png'
    def push(data):
        page.evaluate('(d) => { window.testPayload=d; render(d); }', data)
    push(rv)
    page.wait_for_function('!!radarView.pending')
    page.wait_for_timeout(100)
    assert pending
    assert page.locator('#rad-attrib').inner_text() == 'IEM / NOAA'
    assert '2 min' in page.locator('#rad-src-cap').inner_text()
    assert page.locator('#rad-asof').inner_text() == '21:04'
    assert not page.evaluate('!!radarView.timer')
    stream = io.BytesIO(); Image.new('RGBA', (956, 490), (0, 163, 224, 255)).save(stream, 'PNG')
    pending.pop().fulfill(body=stream.getvalue(), content_type='image/png')
    page.wait_for_function('radarView.data.sourceId === "rainviewer" && !radarView.pending')
    assert page.locator('#rad-attrib').inner_text() == 'RainViewer'
    # Attribution is text only: a kiosk has no way back from a provider's site.
    assert page.locator('#rad-attrib').get_attribute('href') is None
    assert page.locator('#rad-ramp i').count() == 9 and page.locator('.rad-snow-ramp').count()==0
    assert '10 min' in page.locator('#rad-src-cap').inner_text()
    assert page.evaluate('radarView.data.updatedAt') == '21:08'
    page.wait_for_function('radarView.loaded.every(f => f.ready)')
    for selector in ('#rad-plate', '#rad-loop', '#rad-legend'):
        box = page.locator(selector).bounding_box()
        assert box and box['y'] + box['height'] <= 568, (theme, selector, box)
    page.screenshot(path=str(output_dir / f'radar-fallback-{theme}.png'))
    # A late successful load from an abandoned generation cannot resurrect it.
    abandoned = source_payload('iem-mrms-lcref')
    abandoned['radar']['frames'][-1]['url'] = 'https://radar.test/switch-abandoned.png'
    push(abandoned); page.wait_for_timeout(100)
    assert pending
    push(rv)
    stream = io.BytesIO(); Image.new('RGBA', (956, 490), (51, 102, 204, 255)).save(stream, 'PNG')
    pending.pop().fulfill(body=stream.getvalue(), content_type='image/png')
    page.wait_for_timeout(150)
    assert page.evaluate('radarView.data.sourceId') == 'rainviewer'
    # Failed source changes retain every old metadata field along with the image.
    abandoned['radar']['frames'][-1]['url'] = 'https://radar.test/switch-failed.png'
    push(abandoned); page.wait_for_timeout(100)
    pending.pop().fulfill(status=503, body='not ready')
    page.wait_for_function('radarView.failed')
    assert page.locator('#rad-attrib').inner_text() == 'RainViewer'
    assert '10 min' in page.locator('#rad-src-cap').inner_text()
    assert page.locator('#rad-updated-row').count() == 0
    assert page.locator('#rad-plate').get_attribute('data-state') == 'stale'
    assert page.evaluate('mismatches') == []
    page.evaluate('cancelAnimationFrame(inspectId)')
    # Same-frame Updated changes must not stage or replace the decoded image node.
    page.evaluate('window.previousEcho = document.getElementById("rad-echo")')
    rv['radar']['updatedAt'] = '21:10'
    push(rv)
    assert page.evaluate('previousEcho === document.getElementById("rad-echo")')
    assert page.evaluate('radarView.data.updatedAt') == '21:10'
    assert page.locator('#rad-asof').inner_text() == '21:04'
    # Reduced motion and page visibility both idle the loop.
    page.emulate_media(reduced_motion='reduce'); push(rv)
    assert not page.evaluate('!!radarView.timer')
    page.emulate_media(reduced_motion='no-preference'); push(rv)
    page.wait_for_function('!!radarView.timer')
    page.evaluate('Object.defineProperty(document, "hidden", {value:true, configurable:true}); document.dispatchEvent(new Event("visibilitychange"))')
    assert not page.evaluate('!!radarView.timer')
    page.evaluate('Object.defineProperty(document, "hidden", {value:false, configurable:true}); document.dispatchEvent(new Event("visibilitychange"))')
    # Clear and stale states keep As-of and Updated; short histories disclose cadence.
    clear = source_payload('iem-mrms-lcref')
    stream = io.BytesIO(); Image.new('RGBA', (956, 490)).save(stream, 'PNG')
    clear['radar']['frames'] = [dict(clear['radar']['frames'][-1],
        url='data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode())]
    clear['radar'].update(completeFrameCount=1, frameCount=1, historySpanSec=0, frameSpacingSec=None)
    push(clear)
    page.wait_for_function('document.getElementById("rad-plate").dataset.state === "clear"')
    assert page.locator('#rad-frame-time').inner_text() == 'No echoes · clear'
    incomplete=json.loads(json.dumps(clear));incomplete['radar']['legend']['remapped']=False
    incomplete['radar']['frames'][-1]['legend']=dict(remapped=False)
    push(incomplete)
    assert page.locator('#rad-plate').get_attribute('data-state')!='clear'
    assert page.locator('#rad-frame-time').inner_text()!='No echoes · clear'
    assert 'palette incomplete' in page.locator('#rad-src-cap').inner_text()
    push(clear);page.wait_for_function('radarView.clear')
    assert page.locator('#rad-updated-row').count() == 0 and page.locator('#rad-asof').is_visible()
    assert '2 min' in page.locator('#rad-src-cap').inner_text()
    clear['radar'].update(stale=True, ageSec=601); push(clear)
    assert page.locator('#rad-plate').get_attribute('data-state') == 'stale'
    assert page.locator('#rad-updated-row').count() == 0
    # Older RainViewer payloads still render, hiding fields absent from that contract.
    legacy = source_payload('rainviewer')
    for field in ('sourceId', 'cadenceSec', 'frameSpacingSec', 'completeFrameCount',
                  'historySpanSec', 'historyGaps', 'updatedAt', 'attributionUrl', 'zoomAuto',
                  'zoomMin', 'zoomMax', 'zoomSource', 'zoomCapped', 'zoomDesired', 'zoomAutoLevel'):
        legacy['radar'].pop(field, None)
    legacy['radar']['legend'].pop('id')
    for f in legacy['radar']['frames']: f.pop('at')
    push(legacy)
    page.wait_for_function('radarView.data.sourceId === undefined')
    assert page.locator('#rad-updated-row, #rad-cadence-row').count() == 0
    assert page.locator('#rad-attrib').inner_text() == 'RainViewer'
    assert page.locator('#rad-zoom').is_hidden()
    assert errors == [], errors
    context.close()



ZOOM_SITES = (
    ('seattle', 'Seattle', 47.61, -122.33),
    ('berlin', 'Berlin', 52.52, 13.40),
    ('sydney', 'Sydney', -33.87, 151.21),
    ('ocean', 'Mid-ocean', 0, -140),
    ('antimeridian', 'Antimeridian', 0, 179.5),
)


@contextmanager
def zoom_site_server(html, site):
    """Real server + compositor, deterministic provider bytes, no live weather.

    Geometry/cache/PNG publication and browser HTTP are real. Provider transport
    is stubbed here; rate/cooldown/deadline behavior is tested by pytest instead.
    """
    slug, name, lat, lon = site
    with tempfile.TemporaryDirectory(prefix='wfp-zoom-ux-') as directory:
        root = Path(directory)
        (root / 'index.html').write_text(html)
        cfg = make_config(Station={'Name': name, 'Latitude': str(lat), 'Longitude': str(lon)})
        app = SimpleNamespace(config=cfg, obsParser=SimpleNamespace(api_data={}))
        emitter = ae.AlmanacEmitter(SimpleNamespace(app=app, Obs={}, Met={}, Astro={}, Sager={}),
                                     output_path=str(root / 'wx.json'))
        state = SimpleNamespace(source_down=False, clear=slug == 'ocean', calls=[])
        def request(source, url, deadline, method='GET', metadata=False):
            state.calls.append(url)
            if source.startswith('iem') and state.source_down:
                raise OSError('fixture primary outage')
            newest = int(time.time() - 120) // 120 * 120
            if url == ae.RADAR_IEM_METADATA_URL:
                from datetime import datetime, timezone
                return json.dumps(dict(meta=dict(product='lcref', units='0.5 dBZ',
                    end_valid=datetime.fromtimestamp(newest, timezone.utc).isoformat()))).encode()
            if url == ae.RADAR_RAINVIEWER_MANIFEST_URL:
                newest = newest // 600 * 600
                return json.dumps(dict(host='https://tiles.example', radar=dict(past=[
                    dict(time=newest-offset, path='/v2/'+str(newest-offset))
                    for offset in range(0,3601,600)]))).encode()
            if method == 'HEAD': return b''
            # Synthetic echoes exercise palette/alpha and clear detection. They
            # deliberately make no claim about live conditions at these sites.
            tile = Image.new('RGBA', (256,256))
            if not state.clear:
                draw = ImageDraw.Draw(tile)
                colors = ((51,102,204,255),(0,204,0,255),(255,204,0,255)) if source.startswith('iem') else (
                    (146,136,113,100),(0,163,224,255),(0,85,136,255))
                for i, color in enumerate(colors):
                    draw.ellipse((25+i*24,40+i*20,218-i*15,205-i*15), fill=color)
            stream=io.BytesIO(); tile.save(stream,'PNG'); return stream.getvalue()
        emitter._radar_request = request
        spec=importlib.util.spec_from_file_location('radar_ux_serve',
            Path(__file__).resolve().parents[1]/'design/almanac/kiosk/serve.py')
        server_module=importlib.util.module_from_spec(spec); spec.loader.exec_module(server_module)
        server_module.WEB=str(root); server_module.DATA=emitter.output_path
        server_module.Handler.log_message=lambda *args: None
        # Keep synthetic UI scans fresh independent of the wall-clock minute.
        with patch.object(ae,'RADAR_IEM_READY_LAG_SEC',120), patch.object(ae,'RADAR_DIR',str(root/'radar')), patch.object(ae,'RADAR_MAX_FRAME_BUILDS_PER_PASS',2):
            emitter._do_radar(); emitter._emit(0)
            with server_module.Server(('127.0.0.1',0),server_module.Handler) as server:
                worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
                try:
                    yield emitter, state, root, f'http://127.0.0.1:{server.server_address[1]}'
                finally:
                    server.shutdown(); worker.join(timeout=5)


def check_zoom_site(browser, html, output_dir, theme, site):
    slug, name, lat, lon = site
    with zoom_site_server(html, site) as (emitter, state, root, url):
        context=browser.new_context(viewport={'width':1024,'height':600},device_scale_factor=2)
        context.add_init_script("""window.pollURLs=[]; const realFetch=window.fetch;
            window.fetch=function(u,o){pollURLs.push(String(u));return realFetch(u,o);};""")
        page=context.new_page(); errors=[]
        page.on('pageerror',lambda error: errors.append(str(error)))
        page.goto(url+f'/index.html?tabs=1&theme={theme}')
        page.locator('.tab[data-screen="s-radar"]').click()
        page.wait_for_function('radarView.data && !radarView.pending')

        def poll():
            page.wait_for_timeout(140)
            page.wait_for_function('!polling'); page.evaluate('poll()'); page.wait_for_function('!polling')

        def build(desired=None, history=True, scheduled=False):
            poll()  # real GET persists preference before the separate worker reads it
            if desired is not None:
                assert (root/'radar_zoom').read_text().strip() == str(desired)
            if not history:
                (root/'radar_viewed').unlink(missing_ok=True)
            emitter._do_radar(intent_triggered=False if scheduled else None); emitter._emit(0)
            expected=emitter._build_payload()['radar']
            poll()
            # Pass JSON text: Playwright's wait_for_function argument conversion
            # drops dict entries whose value is None; Auto requires a real null.
            page.wait_for_function("""raw => {
                const r=JSON.parse(raw), d=radarView.data;
                return d && d.zoom === r.zoom && d.zoomDesired === r.zoomDesired && d.latest === r.latest &&
                    !radarView.pending && document.getElementById('rad-zoom').dataset.state === 'confirmed';
            }""", arg=json.dumps({k:expected[k] for k in ('zoom','zoomDesired','latest')}))
            assert page.locator('#rad-range, .rad-rail').count() == 0
            assert page.evaluate('(r)=>Math.abs(parseFloat(document.querySelector("#rad-over .rad-scale")'
                '.getAttribute("d").split("H")[1]) - 340 - r.scaleBar.pixels) < 1e-8', expected)
            assert expected['scaleBar']['distDisp'] in page.locator('#rad-over').text_content()
            actual_rings=page.locator('#rad-over .ring').evaluate_all('(els)=>els.map(e=>+e.getAttribute("r"))')
            assert actual_rings == [ring['px'] for ring in expected['rings']]
            assert page.locator('#rad-echo').evaluate('(e)=>getComputedStyle(e).transform') == 'none'
            return expected

        def shot(label):
            page.wait_for_timeout(100)
            for selector in ('#rad-plate','#rad-zoom','#rad-legend','#rad-zoom-in','#rad-zoom-out'):
                b=page.locator(selector).bounding_box()
                assert b and b['y']>=0 and b['y']+b['height']<=568 and b['x']+b['width']<=1024, (site,theme,label,selector,b)
            for selector in ('#rad-zoom-in','#rad-zoom-out'):
                b=page.locator(selector).bounding_box(); assert b['width']==b['height']==44
            if page.locator('#rad-loop').is_visible():
                b=page.locator('#rad-loop').bounding_box(); assert b['y']+b['height']<=568,(slug,theme,label,b)
            page.screenshot(path=str(output_dir/f'zoom-{slug}-{theme}-{label}.png'))

        assert page.locator('#rad-zoom-mode').inner_text() == 'Auto'
        assert page.locator('#rad-zoom-reset').is_hidden()
        assert emitter._radar_zoom == (8 if slug=='seattle' else 7)
        assert emitter._radar_result.source_id == ('iem-mrms-lcref' if slug=='seattle' else 'rainviewer')
        if slug=='antimeridian': assert emitter._radar_bounds['e'] < emitter._radar_bounds['w']
        if slug!='seattle':
            assert page.locator('#rad-zoom-in').is_disabled()
            assert page.locator('#rad-note').inner_text() == 'Closest view for RainViewer'
        if slug=='ocean':
            page.wait_for_function('document.getElementById("rad-plate").dataset.state === "clear"')
            assert page.locator('#rad-frame-time').inner_text() == 'No echoes · clear'
        shot('auto')
        build()
        page.wait_for_function('document.getElementById("rad-base").dataset.basemapHash === radarView.data.basemap.hash')
        shot('basemap')
        # Steppers are native, focusable buttons; keyboard activation is real.
        page.keyboard.press('Tab')  # establish keyboard modality for :focus-visible
        page.locator('#rad-zoom-out').focus()
        assert page.locator('#rad-zoom-out').evaluate('(e)=>e.matches(":focus-visible")')
        assert page.locator('#rad-zoom-out').evaluate('(e)=>getComputedStyle(e).outlineStyle') == 'solid'
        page.keyboard.press('Enter')
        assert page.locator('#rad-zoom').get_attribute('data-state') == 'pending'
        assert page.locator('#rad-zoom-mode').inner_text() == 'Manual'
        shot('pending')
        step = 7 if slug=='seattle' else 6
        r=build(step)
        assert not r['zoomAuto'] and r['zoom']==step
        shot('manual')
        # A new browser document recovers Manual from the server, no localStorage.
        page.reload(); page.locator('.tab[data-screen="s-radar"]').click()
        page.wait_for_function('z => radarView.data && radarView.data.zoom === z', arg=step)
        assert page.locator('#rad-zoom-mode').inner_text()=='Manual'
        assert page.evaluate('radarZoom.desired') is None
        # Unicode minus and underscore; rapid input coalesces, floor no-ops stay disabled.
        page.evaluate('pollURLs=[]')
        page.keyboard.press('_'); page.evaluate('document.dispatchEvent(new KeyboardEvent("keydown",{key:"−",bubbles:true}))')
        page.keyboard.press('-')
        if slug=='seattle': page.keyboard.press('-')
        assert page.locator('#rad-zoom-out').is_disabled()
        build(4)
        values=page.evaluate('pollURLs.filter(u=>u.includes("radarZoom=")).map(u=>new URL(u,location.href).searchParams.get("radarZoom"))')
        assert values and set(values)=={'4'}, values
        page.keyboard.press('='); build(5)
        page.keyboard.press('+'); build(6)
        page.keyboard.press('+'); build(7)
        assert page.locator('#rad-zoom-mode').inner_text()=='Manual'
        page.locator('#rad-zoom-reset').focus(); page.keyboard.press('Space'); build('auto')
        assert page.locator('#rad-zoom-mode').inner_text()=='Auto' and page.locator('#rad-zoom-reset').is_hidden()
        # Reset supersedes a sent manual request, even while displayed Auto matches reset.
        page.keyboard.press('-'); poll()
        assert (root/'radar_zoom').read_text().strip()==str(step)
        page.keyboard.press('0')
        assert page.locator('#rad-zoom').get_attribute('data-state')=='pending'
        build('auto')
        # Key shortcuts must be inert off-tab and zoom intent absent from those polls.
        page.locator('.tab[data-screen="s-obs"]').click(); page.keyboard.press('-'); poll()
        assert page.evaluate('radarZoom.desired') is None
        assert 'radarZoom=' not in page.evaluate('pollURLs.at(-1)')
        page.locator('.tab[data-screen="s-radar"]').click()
        if slug=='seattle':
            # Pause survives a one-frame zoom rebuild and subsequent history warm-up.
            build(); page.wait_for_function('radarView.loaded.filter(f=>f.ready).length>=2')
            page.locator('#rad-play').click(); assert page.evaluate('radarView.paused')
            page.keyboard.press('+'); r=build(9,history=False)
            assert r['completeFrameCount']==1 and page.locator('#rad-loop').is_hidden()
            assert page.evaluate('radarView.paused')
            build(); page.wait_for_function('radarView.loaded.filter(f=>f.ready).length>=2')
            assert page.evaluate('radarView.paused') and not page.evaluate('!!radarView.timer')
            shot('closer')
            page.keyboard.press('0'); build('auto')
            page.keyboard.press('+')  # requested z9 differs from retained z8, so acquire fallback
            # Scheduled validation discovers an outage even with a warm crop.
            state.source_down=True; r=build(9, scheduled=True)
            assert r['zoomDesired']==9 and r['zoom']==7 and r['zoomCapped']
            assert page.locator('#rad-zoom-in').is_disabled()
            page.wait_for_function("document.getElementById('rad-note').textContent==='Set closer than RainViewer reaches — showing its closest.'")
            assert page.locator('#rad-note').inner_text()=='Set closer than RainViewer reaches — showing its closest.'
            shot('capped')
            state.source_down=False; r=build()
            assert r['zoom']==9 and not r['zoomCapped']
            page.wait_for_function("document.getElementById('rad-note').textContent.includes('MRMS')")
            assert 'MRMS' in page.locator('#rad-note').inner_text()
            page.keyboard.press('+'); build(9)
            assert page.locator('#rad-zoom-in').is_disabled()
            page.wait_for_function("document.getElementById('rad-note').textContent==='Closest view for MRMS'")
            assert page.locator('#rad-note').inner_text()=='Closest view for MRMS'
            shot('ceiling')
            page.keyboard.press('0'); build('auto')
            page.locator('#rad-play').click(); page.wait_for_function('!!radarView.timer')
        # Staleness stays honest, including on the clear mid-ocean plate.
        emitter._radar_result=emitter._radar_result._replace(stale_sec=1)  # same scan, clock age crosses the source's stale_sec
        emitter._emit(0); poll()
        page.wait_for_function('document.getElementById("rad-plate").dataset.state === "stale"')
        assert page.locator('#rad-updated-row').count() == 0
        shot('stale')
        # No zoom-control CSS rule can reference accent; inherited colors match ink.
        assert page.evaluate("""() => [...document.styleSheets].flatMap(s=>[...s.cssRules]).filter(
            r=>r.selectorText && /rad-(zoom|step|reset)/.test(r.selectorText)).every(r=>!r.cssText.includes('--accent'))""")
        if theme=='night':
            explicit=page.locator('#rad-zoom-in').evaluate('(e)=>getComputedStyle(e).color')
            page.emulate_media(color_scheme='dark')
            page.evaluate('document.documentElement.removeAttribute("data-theme")')
            assert page.locator('#rad-zoom-in').evaluate('(e)=>getComputedStyle(e).color')==explicit
        assert errors==[], errors
        context.close()
        print(f'UX PASS: {name} ({theme})', flush=True)

def check_basemap(browser, html, output_dir, theme):
    """Real local SVG fetches, missing/legacy fallback, token and DOM invariants."""
    with zoom_site_server(html, ZOOM_SITES[0]) as (emitter, state, root, url):
        context = browser.new_context(viewport={'width':1024,'height':600}, device_scale_factor=2)
        context.add_init_script("""window.mapURLs=[]; const original=window.fetch;
            window.fetch=function(u,o){if(String(u).includes('basemap/'))mapURLs.push(String(u));return original(u,o);};""")
        page=context.new_page(); errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(url+f'/index.html?tabs=1&theme={theme}')
        page.locator('.tab[data-screen="s-radar"]').click()
        page.wait_for_function('radarView.data && !radarView.pending')
        assert page.locator('#rad-base line').count()==17
        assert not page.evaluate('mapURLs.length')
        page.screenshot(path=str(output_dir/f'basemap-{theme}-before.png'))
        page.locator('.tab[data-screen="s-obs"]').click()
        # A viewed heartbeat already exists; worker emits geometry while another
        # tab is active. The browser must defer fetching it until radar reopens.
        (root/'radar_viewed').write_text(str(time.time()))
        emitter._do_radar(); emitter._emit(0)
        page.wait_for_function('!polling'); page.evaluate('poll()'); page.wait_for_function('!polling')
        assert emitter._radar_result.basemap
        assert not page.evaluate('mapURLs.length')
        page.locator('.tab[data-screen="s-radar"]').click()
        page.wait_for_function('document.getElementById("rad-base").dataset.basemapHash')
        assert page.locator('#rad-base line').count()==0
        for cls in ('bm-ocean','bm-coast','bm-road'):
            assert page.locator('#rad-base .'+cls).count()>0
        assert page.evaluate("""() => [...document.querySelectorAll('#rad-base path')].every(
            p=>p.getAttributeNames().sort().join(',')==='class,d')""")
        assert page.evaluate("""() => [...document.styleSheets].flatMap(s=>[...s.cssRules]).filter(
            r=>r.selectorText && r.selectorText.includes('.bm-')).every(r=>!r.cssText.includes('--accent'))""")
        assert page.locator('#rad-base').evaluate('(e)=>+getComputedStyle(e).zIndex') < page.locator('#rad-echo').evaluate('(e)=>+getComputedStyle(e).zIndex')
        def colors():
            return page.evaluate("""() => ['.bm-ocean','.bm-coast','.bm-road'].map((c,i)=> {
                let e=document.querySelector('#rad-base '+c), p=document.createElementNS('http://www.w3.org/2000/svg','path');
                p.style[i?'stroke':'fill']='var('+['--water-tint','--water','--ink-soft'][i]+')';
                e.parentNode.append(p); let expected=getComputedStyle(p)[i?'stroke':'fill'];p.remove();
                let actual=getComputedStyle(e)[i?'stroke':'fill'];if(actual!==expected)throw Error('wrong token');return actual;
            })""")
        first=colors()
        page.evaluate("document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'night' ? 'paper' : 'night'")
        assert colors()!=first
        page.evaluate('(t)=>document.documentElement.dataset.theme=t',theme)
        if theme=='night':
            page.emulate_media(color_scheme='dark')
            page.evaluate('document.documentElement.removeAttribute("data-theme")')
            assert colors()==first
            page.evaluate('document.documentElement.dataset.theme="night"')
        page.wait_for_function('!!radarView.timer')
        page.evaluate("""window.baseMutations=0;window.baseNode=document.getElementById('rad-base').firstChild;
            window.mapObserver=new MutationObserver(m=>baseMutations+=m.length);
            mapObserver.observe(document.getElementById('rad-base'),{subtree:true,childList:true,attributes:true});
            window.mapFetchCount=mapURLs.length;window.echoBefore=radarView.current.id;""")
        page.wait_for_function('radarView.current.id !== echoBefore')
        assert page.evaluate('baseMutations===0 && mapURLs.length===mapFetchCount && baseNode===document.getElementById("rad-base").firstChild')
        page.evaluate('mapObserver.disconnect()')
        page.screenshot(path=str(output_dir/f'basemap-{theme}-after.png'))
        assert page.evaluate('mapURLs.length')==1
        # Both malformed and missing artifacts degrade to the exact old graticule.
        for suffix,body in (('e','<svg>broken'),('f',None)):
            fake=suffix*20
            r=dict(emitter._radar_result.basemap,hash=fake,url='radar/basemap/'+fake+'.svg')
            if body:
                page.route('**/'+fake+'.svg',lambda route:route.fulfill(body=body,content_type='image/svg+xml'))
            emitter._radar_result=emitter._radar_result._replace(basemap=r)
            emitter._emit(0)
            page.wait_for_function('!polling');page.evaluate('poll()');page.wait_for_function('!polling')
            page.wait_for_function('(h)=>radarBasemaps.has(h)',arg=fake)
            page.wait_for_timeout(150)
            assert page.locator('#rad-base line').count()==17
            assert page.locator('#rad-echo').evaluate('(e)=>!e.hidden && e.width===956')
        page.screenshot(path=str(output_dir/f'basemap-{theme}-missing.png'))
        emitter._radar_result=emitter._radar_result._replace(basemap=None);emitter._emit(0)
        page.wait_for_function('!polling');page.evaluate('poll()');page.wait_for_function('!polling')
        assert page.locator('#rad-base line').count()==17
        page.screenshot(path=str(output_dir/f'basemap-{theme}-legacy.png'))
        assert errors==[],errors
        context.close()
        print(f'BASEMAP PASS: {theme}; active-only fetch, tokens/theme switch, echo stacking, untouched loop DOM, missing/malformed/legacy fallback',flush=True)


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


def check_radar_v2(browser, html, output_dir, theme):
    """Measured rAF timing, zero playback image work, protected zones and source states."""
    import copy
    initial=source_payload('iem-mrms-lcref')
    r=initial['radar']; newest=r['observedTs']
    r.update(sourceMode='mosaic',sources=[dict(mode='mosaic',available=True),
        dict(mode='site',siteId='KATX',available=True,reason=None)],ageSec=180,stale=False)
    # Sixteen distinguishable scans at the memory cap; sample every rAF, not a coarse timer.
    frame=r['frames'][-1]
    r['frames']=[dict(frame,id=f'v2-{i}',ts=newest-(15-i)*120,at=f'20:{i:02}') for i in range(16)]
    r['latest']='v2-15'; r['frames'][-1]['at']=r['observedAt']
    r.update(frameCount=16,completeFrameCount=16,historySpanSec=1800)
    context=browser.new_context(viewport={'width':1024,'height':600},has_touch=True)
    context.add_init_script('window.testPayload='+json.dumps(initial)+''';
        window.fetchCount=0; window.decodeCount=0; window.sourceWrites=0;
        const decode=HTMLImageElement.prototype.decode;
        HTMLImageElement.prototype.decode=function(){decodeCount++;return decode.call(this);};
        const desc=Object.getOwnPropertyDescriptor(HTMLImageElement.prototype,'src');
        Object.defineProperty(HTMLImageElement.prototype,'src',{...desc,set(v){sourceWrites++;desc.set.call(this,v);}});
        window.fetch=function(u){fetchCount++;testPayload.ts=Date.now()/1000;return Promise.resolve({ok:true,
          headers:{get:()=>new Date().toUTCString()},json:()=>Promise.resolve(testPayload)});};''')
    context.route('https://radar.test/**',lambda route:route.fulfill(body=html,content_type='text/html'))
    page=context.new_page(); errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(f'https://radar.test/?tabs=1&theme={theme}');page.locator('.tab[data-screen="s-radar"]').tap()
    page.wait_for_function('radarReady().length===16 && radarView.bufferDone && !!radarView.timer')
    assert page.locator('.rad-rail, .rad-place, .rad-sub, #rad-range, #rad-updated-row, #rad-cadence-row').count()==0
    box=page.locator('#rad-plate').bounding_box()
    assert box==dict(x=34,y=76,width=956,height=490),box
    for selector in ('#rad-src','#rad-src-cap','#rad-legend','#rad-loop','#rad-zoom','#rad-note'):
        if not page.locator(selector).is_visible():continue
        b=page.locator(selector).bounding_box();x=b['x']-box['x'];y=b['y']-box['y']
        assert y>=0 and (y+b['height']<=73 or y>=418) and y+b['height']<=490,(selector,b)
        dx=max(x-478,0,478-x-b['width']);dy=max(y-245,0,245-y-b['height'])
        assert dx*dx+dy*dy>150**2,(selector,b)
    assert page.locator('#rad-legend').bounding_box()['width']==414
    assert page.locator('#rad-ramp i').count()==9
    assert page.locator('.rad-legend-unit').inner_text()=='dBZ'          # unit, mixed case, no duplicate title
    assert page.locator('.rad-legend-title').count()==0
    assert page.locator('.mast-station .sub').is_hidden()               # station subtitle dropped on the radar tab
    for selector in ('#rad-src-mosaic','#rad-src-site','#rad-play','#rad-zoom-in','#rad-zoom-out'):
        b=page.locator(selector).bounding_box();assert b['width']>=44 and b['height']>=44,(selector,b)
    contrast=page.evaluate('''() => {
      const css=getComputedStyle(document.documentElement);
      const rgb=s=>s.match(/[\d.]+/g).map(Number);
      const ink=rgb(getComputedStyle(document.body).color), scrim=rgb(css.getPropertyValue('--plate-scrim'));
      const under=document.documentElement.dataset.theme==='night'?[255,255,255]:[217,0,0];
      const bg=under.map((c,i)=>c*(1-scrim[3])+scrim[i]*scrim[3]);
      const lum=c=>c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;})
        .reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
      const a=lum(ink),b=lum(bg);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    }''')
    assert contrast>=4.5,(theme,contrast)
    timing=page.evaluate('''async () => {
      const before={decode:decodeCount,src:sourceWrites}, events=[];
      let previous=null;
      await new Promise(resolve=>{const start=performance.now();function frame(now){
        const f=radarView.current;
        if(f.id!==previous){events.push({id:f.id,at:now});previous=f.id;}
        if(now-start>8500)resolve();else requestAnimationFrame(frame);
      }requestAnimationFrame(frame);});
      return {events,decode:decodeCount-before.decode,src:sourceWrites-before.src};
    }''')
    assert timing['decode']==timing['src']==0,timing
    intervals=[];holds=[]
    for a,b in zip(timing['events'],timing['events'][1:]):
        dt=b['at']-a['at']
        (holds if a['id']=='v2-15' else intervals).append(dt)
    # The first sample can begin partway through a scan/hold; exclude it.
    intervals=intervals[1:];holds=holds[1:]
    assert len(holds)>=2 and all(1030<=v<=1170 for v in holds),holds
    assert intervals and 100<=sum(intervals)/len(intervals)<=125,intervals
    for a,b in zip(timing['events'],timing['events'][1:]):
        assert int(b['id'].split('-')[1])==(int(a['id'].split('-')[1])+1)%16
    page.locator('#rad-play').tap();held=page.evaluate('radarView.current.id')
    page.wait_for_timeout(300);assert page.evaluate('radarView.current.id')==held
    page.screenshot(path=str(output_dir/f'radar-v2-{theme}.png'))
    # Reduced motion is opt-in one sweep, stopping at newest with no wrap dip.
    page.emulate_media(reduced_motion='reduce');page.wait_for_timeout(50)
    assert not page.evaluate('!!radarView.timer') and page.locator('#rad-play').is_visible()
    page.locator('#rad-play').tap();page.wait_for_function('radarView.singleSweep')
    page.wait_for_function('!radarView.singleSweep && radarView.paused')
    assert page.evaluate('radarView.current.id')=='v2-15'
    assert page.locator('#rad-echo').evaluate('(e)=>e.style.opacity')==''
    page.emulate_media(reduced_motion='no-preference')
    def push(data):page.evaluate('(d)=>{testPayload=d;render(d)}',data)
    # Age suffix is gated on 80% of staleSec, not a bare cadence multiple: a routine
    # MRMS frame (skipped until ~5 min old) reads clean; the suffix forecasts stale.
    routine=copy.deepcopy(initial);routine['radar'].update(ageSec=360,staleSec=600,stale=False);push(routine)
    assert 'min old' not in page.locator('#rad-status').inner_text().lower()
    warn=copy.deepcopy(initial);warn['radar'].update(ageSec=480,staleSec=600,stale=False);push(warn)
    assert '8 min old' in page.locator('#rad-status').inner_text().lower()
    # Honest age and anti-rewind independent of nominal caption.
    late=copy.deepcopy(initial);late['radar'].update(ageSec=660,staleSec=600,stale=True);push(late)
    assert '11 min old' in page.locator('#rad-status').inner_text().lower()
    backwards=copy.deepcopy(late);backwards['radar']['observedTs']-=120
    backwards['radar']['latest']='rewind';backwards['radar']['frames']=[dict(frame,id='rewind',ts=newest-120)]
    push(backwards);assert page.evaluate('radarView.good.ts')==newest
    # Optimistic tap preserves old presentation until authoritative site pixels decode.
    push(initial);page.locator('#rad-src-site').tap()
    assert page.locator('#rad-src').get_attribute('data-state')=='pending'
    assert page.locator('#rad-src-site').get_attribute('aria-pressed')=='false'
    assert page.evaluate('radarView.data.sourceId')=='iem-mrms-lcref'
    page.evaluate('radarSource.sent=true')
    site=source_payload('iem-nexrad-n0b');site['radar'].update(sourceMode='site',sourcePref='site',siteId='KATX',
        sources=r['sources'],zoomMin=4,zoomMax=10,ageSec=720,stale=False,
        sites=[dict(id='KATX',lat=48.194611,lon=-122.49569,primary=True,contributing=True,reason=None)])
    for item in site['radar']['frames']:item['siteScans']=[dict(id='KATX',ts=item['ts'])]
    delayed=[]
    page.route('https://radar.test/site-history.png',lambda route:delayed.append(route))
    site['radar']['frames'][0]['url']='https://radar.test/site-history.png'
    push(site);page.wait_for_timeout(100)
    assert delayed and page.evaluate('radarView.data.sourceId')=='iem-mrms-lcref'
    assert page.locator('#rad-src').get_attribute('data-state')=='pending'
    stream=io.BytesIO();Image.new('RGBA',(956,490),(51,102,204,255)).save(stream,'PNG')
    delayed.pop().fulfill(body=stream.getvalue(),content_type='image/png')
    page.wait_for_function('radarView.data.sourceMode==="site" && !radarView.pending')
    assert page.locator('#rad-src').get_attribute('data-state')=='confirmed'
    assert 'NEXRAD · KATX' in page.locator('#rad-src-cap').inner_text()
    assert '~5 min volume' in page.locator('#rad-src-cap').inner_text()
    assert page.locator('#rad-plate').get_attribute('data-state')!='stale'
    page.screenshot(path=str(output_dir/f'radar-v2-site-{theme}.png'))
    none=copy.deepcopy(initial);none['radar']['sources'][1].update(available=False,reason='no site in range')
    push(none);page.wait_for_function('radarView.data.sourceMode==="mosaic" && !radarView.pending')
    assert page.locator('#rad-src-site').is_disabled() and page.locator('#rad-src-site').text_content()=='No site'
    push(initial);page.locator('#rad-src-site').tap();page.evaluate('radarSource.sent=true')
    failed=copy.deepcopy(initial);failed['radar']['sourcePref']='site';failed['radar']['sources'][1].update(available=False,reason='not reporting')
    push(failed)
    assert page.locator('#rad-src-mosaic').get_attribute('aria-pressed')=='true'
    assert 'KATX not reporting' in page.locator('#rad-src-cap').inner_text()
    page.wait_for_timeout(8100);assert 'unavailable' not in page.locator('#rad-src-cap').inner_text()
    rv=source_payload('rainviewer');push(rv);page.wait_for_function('radarView.data.sourceId==="rainviewer"')
    assert page.locator('#rad-src').is_hidden() and page.locator('.rad-snow-ramp').count()==0
    # Return releases every history bitmap, then re-decodes on entry.
    page.locator('.tab[data-screen="s-obs"]').tap();assert page.evaluate('radarView.loaded.length')==0
    page.locator('.tab[data-screen="s-radar"]').tap();page.wait_for_function('radarReady().length===3')
    assert errors==[],errors
    context.close()
    result=dict(theme=theme,contrast=contrast,intervalMs=sum(intervals)/len(intervals),holdsMs=holds,
                decodedDuringPlayback=timing['decode'],srcWritesDuringPlayback=timing['src'])
    (output_dir/f'playback-{theme}.json').write_text(json.dumps(result,indent=2))
    print('RADAR V2 PASS: '+json.dumps(result),flush=True)


def check_radar_touch(browser, html, output_dir, theme):
    """Real loopback center/zoom writes and emitter crops; synthetic captured gestures."""
    from lib.radar_geometry import world_point, world_inverse, plate_point
    import math
    with zoom_site_server(html, ('touch', 'Touch fixture', 47.61, -122.33)) as (emitter, state, root, url):
        context = browser.new_context(viewport={'width': 1024, 'height': 600}, has_touch=True,
                                      device_scale_factor=2)
        context.add_init_script("""window.pollURLs=[]; const realFetch=window.fetch;
            window.fetch=function(u,o){pollURLs.push(String(u));return realFetch(u,o);};""")
        page = context.new_page(); errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url + f'/?tabs=1&theme={theme}')
        page.locator('.tab[data-screen="s-radar"]').tap()
        page.wait_for_function('radarView.good && !radarView.pending')
        page.evaluate("""() => { window.pointer = (type,id,x,y,selector='#rad-plate') => {
            const plate=document.getElementById('rad-plate'), b=plate.getBoundingClientRect();
            const e=new PointerEvent(type,{bubbles:true,cancelable:true,pointerId:id,
                pointerType:'touch',isPrimary:id===1,clientX:b.x+x,clientY:b.y+y,button:0,buttons:type==='pointerup'?0:1});
            document.querySelector(selector).dispatchEvent(e); return e.defaultPrevented;
        }; }""")
        def pointer(kind, ident, x, y, selector='#rad-plate'):
            return page.evaluate('(a)=>pointer(...a)', [kind, ident, x, y, selector])
        def drag(dx, dy):
            pointer('pointerdown', 1, 478, 245)
            pointer('pointermove', 1, 478 + dx, 245 + dy)
            pointer('pointerup', 1, 478 + dx, 245 + dy)
        def poll():
            page.wait_for_timeout(140)
            page.wait_for_function('!polling'); page.evaluate('poll()'); page.wait_for_function('!polling')
        def build():
            poll(); emitter._do_radar(); emitter._emit(0); poll()
            expected = emitter._build_payload()['radar']
            page.wait_for_function('(id)=>radarView.good.id===id && !radarView.pending && radarGesture.state==="idle"', arg=expected['latest'])
            return expected
        build(); build()
        page.wait_for_function('radarReady().length>=2 && !!radarView.timer')
        assert page.locator('#rad-plate').evaluate('e=>getComputedStyle(e).touchAction') == 'none'
        assert page.locator('#rad-stack > #rad-base, #rad-stack > #rad-echo, #rad-stack > #rad-over').count() == 3
        assert page.locator('#rad-recenter').is_hidden()
        # Geometry mirror: high/low zoom, hemispheres, poles, and independently stretched axes.
        for lat, lon, zoom in [(47.61,-122.33,8), (0,179.5,4), (-33.87,151.21,10), (85.05112878,-180,4)]:
            xy = page.evaluate('(a)=>radarWorldPoint(...a)', [lat,lon,zoom])
            assert all(abs(a-b)<1e-8 for a,b in zip(xy,world_point(lat,lon,zoom)))
            inverse = page.evaluate('(a)=>radarWorldInverse(...a)', [*xy,zoom])
            assert abs(inverse['lat']-lat)<1e-8 and abs(inverse['lon']-lon)<1e-8
        for selector in ('#rad-src-mosaic', '#rad-src-cap', '#rad-legend', '#rad-play', '#rad-zoom-in'):
            pointer('pointerdown',1,478,245,selector)
            pointer('pointermove',1,550,275,selector); pointer('pointerup',1,550,275,selector)
            assert page.evaluate('radarGesture.state==="idle" && radarGesture.pointers.size===0 && radarCenter.desired===null')
        # A captured mouse leaving the plate still commits when lifted.
        box=page.locator('#rad-plate').bounding_box()
        page.mouse.move(box['x']+478,box['y']+245); page.mouse.down()
        assert page.evaluate('document.getElementById("rad-plate").hasPointerCapture(1)')
        page.mouse.move(box['x']+518,box['y']+265); page.mouse.up()
        build(); page.locator('#rad-recenter').tap(); build()
        # Known CSS offset uses separate clientWidth/956 and clientHeight/490.
        r=page.evaluate('radarView.data'); client=page.locator('#rad-plate').evaluate('e=>[e.clientWidth,e.clientHeight]')
        dx,dy=80,36
        cx,cy=world_point(r['center']['lat'],r['center']['lon'],r['zoom'])
        lat,lon=world_inverse(cx-dx/(client[0]/956),cy-dy/(client[1]/490),r['zoom'])
        pointer('pointerdown',1,478,245); pointer('pointermove',1,478+dx,245+dy)
        assert page.evaluate('radarGesture.state==="gesturing" && !radarView.timer && radarView.current.id===radarView.good.id')
        pointer('pointerup',1,478+dx,245+dy)
        desired=page.evaluate('radarCenter.desired')
        assert abs(desired['lat']-lat)<1e-9 and abs(desired['lon']-lon)<1e-9, desired
        assert page.evaluate('radarGesture.state==="committing" && !radarView.timer')
        held=page.locator('#rad-echo').get_attribute('style'); poll()
        assert page.evaluate('pollURLs.some(u=>u.includes("radarCenter="))')
        record=json.loads((root/'radar_intent').read_text());saved=(record['center']['lat'],record['center']['lon'])
        assert abs(saved[0]-lat)<1e-9 and abs(saved[1]-lon)<1e-9
        assert not (root/'radar_intent').is_symlink()
        page.wait_for_timeout(2600)
        assert page.locator('#rad-note').get_attribute('data-shown')=='false'  # worker is still idle
        assert page.locator('#rad-echo').evaluate('e=>e.style.transform')
        panned=build()
        page.wait_for_function('!!radarView.timer')
        assert page.locator('#rad-echo').evaluate('e=>e.style.transform') == ''
        assert page.locator('#rad-updating').count()==0
        assert page.locator('#rad-recenter').is_visible() and page.locator('.rad-station-accent').count()==1
        assert page.locator('.rad-cross').count()==0
        glyph=page.locator('.rad-station').evaluate('e=>[+e.getAttribute("cx"),+e.getAttribute("cy")]')
        assert abs(glyph[0]-panned['marker']['x']*956)<1e-8 and abs(glyph[1]-panned['marker']['y']*490)<1e-8
        ring=page.locator('.rad-station-accent').evaluate('e=>getComputedStyle(e).stroke')
        assert ring == ('rgb(224, 123, 85)' if theme=='night' else 'rgb(174, 58, 39)')
        page.screenshot(path=str(output_dir/f'radar-touch-panned-{theme}.png'))
        # Recenter is also gated; the button alone clears the runtime marker.
        pointer('pointerdown',1,478,245,'#rad-recenter'); pointer('pointerup',1,478,245,'#rad-recenter')
        assert page.evaluate('radarGesture.state==="idle"')
        page.locator('#rad-recenter').tap(); centered=build()
        assert json.loads((root/'radar_intent').read_text())['center']=='station' and centered['centered']
        assert page.locator('#rad-recenter').is_hidden() and page.locator('.rad-cross').count()==1
        # Pinch 2x, freeze newest, whole-stack midpoint transform, integer snap/zoom channel.
        z=page.evaluate('radarView.data.zoom')
        pointer('pointerdown',1,428,245); pointer('pointerdown',2,528,245)
        pointer('pointermove',1,378,245); pointer('pointermove',2,578,245)
        assert page.evaluate('radarGesture.scale===2 && !radarView.timer && radarView.current.id===radarView.good.id')
        page.screenshot(path=str(output_dir/f'radar-touch-mid-pinch-{theme}.png'))
        pointer('pointerup',1,378,245); pointer('pointerup',2,578,245)
        assert page.evaluate('radarZoom.desired')==z+1
        build(); assert (root/'radar_zoom').read_text().strip()==str(z+1)
        assert page.evaluate('radarView.data.zoom')==z+1
        # Source-cap rubber band; reduced motion snaps immediately without a transition.
        page.emulate_media(reduced_motion='reduce')
        pointer('pointerdown',1,428,245); pointer('pointerdown',2,528,245)
        pointer('pointermove',1,328,245); pointer('pointermove',2,628,245)
        assert abs(page.evaluate('radarGesture.scale')-1.6)<1e-8
        pointer('pointerup',1,328,245); pointer('pointerup',2,628,245)
        assert page.locator('#rad-echo').evaluate('e=>e.style.transform==="" && e.style.transition===""')
        page.emulate_media(reduced_motion='no-preference')
        # Wheel and ctrl-wheel coalesce each burst to one integer step, no browser zoom.
        for ctrl,delta in [(False,100),(True,-100)]:
            prevented=page.evaluate('''a=>{let results=[];for(let i=0;i<4;i++){
                let e=new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:a[1],ctrlKey:a[0]});
                document.getElementById('rad-plate').dispatchEvent(e);results.push(e.defaultPrevented);}return results;}''',[ctrl,delta])
            assert all(prevented)
            old=page.evaluate('radarView.data.zoom'); page.wait_for_timeout(170)
            assert page.evaluate('radarZoom.desired')==old+(-1 if delta>0 else 1)
            build()
        # Rapid pans accumulate against desired center, never re-label intermediate crops.
        drag(35,10); poll(); emitter._do_radar(); intermediate=emitter._build_payload()
        first=page.evaluate('radarCenter.desired'); old_id=page.evaluate('radarView.good.id')
        drag(25,15); second=page.evaluate('radarCenter.desired')
        assert second['lon']<first['lon'] and second['lat']>first['lat']
        page.evaluate('(d)=>render(d)',intermediate)
        assert page.evaluate('radarView.good.id')==old_id and page.evaluate('radarGesture.state')=='committing'
        build()
        # A superseded image that FINISHES decoding late must never clear the preview.
        drag(20,5); poll(); emitter._do_radar(); emitter._emit(0)
        abandoned=emitter._build_payload()['radar']; delayed=[]
        delayed_url=url+'/'+next(f['url'] for f in abandoned['frames'] if f['id']==abandoned['latest'])
        page.route(delayed_url,lambda route:delayed.append(route))
        poll(); page.wait_for_function('!!radarView.pending')
        page.wait_for_timeout(100); assert delayed
        frozen_id=page.evaluate('radarView.good.id')
        drag(15,5)
        delayed.pop().fulfill(body=(root/'radar'/(abandoned['latest']+'.png')).read_bytes(),content_type='image/png')
        page.wait_for_timeout(100)
        assert page.evaluate('radarView.good.id')==frozen_id
        assert page.evaluate('radarGesture.state')=='committing'
        build()
        # Cancelling a two-to-one transition never posts its provisional zoom.
        pointer('pointerdown',1,428,245); pointer('pointerdown',2,528,245)
        pointer('pointermove',1,453,245); pointer('pointermove',2,503,245)
        pointer('pointerup',1,453,245)
        before_urls=len(page.evaluate('pollURLs')); poll()
        assert all('radarZoom=' not in u for u in page.evaluate('pollURLs')[before_urls:])
        pointer('pointercancel',2,503,245)
        assert page.evaluate('radarZoom.desired===null && radarCenter.desired===null && radarGesture.state==="idle"')
        # Hidden-document lifecycle cancels the same captured gesture and idle timer.
        pointer('pointerdown',1,478,245); pointer('pointermove',1,488,250)
        page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))")
        assert page.evaluate('!radarGesture.idleTimer && !radarView.timer && radarGesture.pointers.size===0 && radarGesture.state==="idle"')
        page.evaluate("delete document.hidden;document.dispatchEvent(new Event('visibilitychange'))")
        assert page.evaluate('!!radarGesture.idleTimer')
        # Bounds resist (not hard-lock), and the committed center obeys the soft cap.
        pointer('pointerdown',1,478,245); pointer('pointermove',1,20478,10245)
        assert page.evaluate('radarGesture.pan.previewX>radarGesture.pan.x')
        cap_data=page.evaluate('''()=>({center:radarGesture.pan.center,home:radarStationCenter(),z:radarEffectiveZoom()})''')
        p1=world_point(cap_data['center']['lat'],cap_data['center']['lon'],cap_data['z'])
        p2=world_point(cap_data['home']['lat'],cap_data['home']['lon'],cap_data['z'])
        assert math.dist(p1,p2)<=1.5*math.hypot(956,490)+1e-6
        pointer('pointercancel',1,20478,10245)
        assert page.evaluate('radarGesture.state==="idle" && radarGesture.pointers.size===0')
        # Unequal axes at z4 expose any mpp/tangent or uniform-scale approximation.
        page.evaluate('radarZoom.desired=4;radarZoom.sent=false;radarPostIntent()'); build()
        page.locator('#rad-plate').evaluate('e=>{e.style.width="800px";e.style.height="400px"}')
        r=page.evaluate('radarView.data'); client=page.locator('#rad-plate').evaluate('e=>[e.clientWidth,e.clientHeight]')
        cx,cy=world_point(r['center']['lat'],r['center']['lon'],4)
        lat,lon=world_inverse(cx-20/(client[0]/956),cy-15/(client[1]/490),4)
        drag(20,15); desired=page.evaluate('radarCenter.desired')
        assert abs(desired['lat']-lat)<1e-8 and abs(desired['lon']-lon)<1e-8
        build(); page.locator('#rad-plate').evaluate('e=>{e.style.width="";e.style.height=""}')
        # Off-tab cancel clears captured input and idle timer. Returning re-arms it.
        pointer('pointerdown',1,478,245); pointer('pointermove',1,488,250)
        page.evaluate('activate("s-obs")')
        assert page.evaluate('radarGesture.state==="idle" && !radarGesture.idleTimer && !radarView.timer && radarGesture.pointers.size===0')
        page.evaluate('activate("s-radar")'); page.wait_for_function('!!radarGesture.idleTimer')
        # Deterministic browser clock verifies 90s, reset by control/plate interaction.
        page.clock.install(); page.evaluate('radarIdleSync(true)')
        page.clock.run_for(89000); assert page.evaluate('radarCenter.desired') is None
        pointer('pointerdown',1,478,245,'#rad-legend'); pointer('pointerup',1,478,245,'#rad-legend')
        page.clock.run_for(89000); assert page.evaluate('radarCenter.desired') is None
        page.clock.run_for(1001); assert page.evaluate('radarCenter.desired')=='station'
        assert errors==[],errors
        context.close()
    print(f'RADAR TOUCH PASS: {theme}; real loopback/emitter pan+pinch, exact Mercator, independent axes, '
          'pointer capture/gating, freeze/settle, delayed/superseded intents, wheel/ctrl-wheel, '
          'caps/reduced-motion, glyph/recenter, 90s idle reset, off-tab cancel; no page errors',flush=True)


def check_radar_v3(browser, html, output_dir, theme):
    """G2: instrument geometry, honest copy, hit testing and overlapping intents."""
    import copy
    data=source_payload('iem-mrms-lcref');r=data['radar']
    r.update(zoom=7,zoomDesired=7,zoomAuto=False,zoomMin=4,zoomMax=9,zoomCapped=False,
             sourceMode='mosaic',sourcePref='mosaic',sourceFallback=None,sitePreferred=False,
             siteResumeZoom=7,sources=[dict(mode='mosaic',available=True),dict(mode='site',siteId='KATX',available=True)],
             intent=dict(seq=0,zoom=7,center='station',source='mosaic'),
             refresh=dict(state='idle',frameIndex=1,frameTotal=1,forSeq=0))
    # A known row of all 26 LUT samples tests actual canvas bytes in either theme.
    image=Image.new('RGBA',(956,490));draw=ImageDraw.Draw(image)
    for i,(_,color) in enumerate(ae._RADAR_LUT):draw.rectangle((i*36,100,i*36+35,390),fill=color)
    stream=io.BytesIO();image.save(stream,'PNG')
    frame=dict(id='v3-ramp',ts=r['observedTs'],at='17:12',complete=True,legend=dict(remapped=True),
               url='data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode())
    r.update(frames=[frame],latest=frame['id'],observedAt='17:12')
    context=browser.new_context(viewport=dict(width=1024,height=600),has_touch=True)
    context.add_init_script("""window.pollURLs=[];window.testPayload=PAYLOAD;
      window.fetch=u=>{pollURLs.push(String(u));return Promise.resolve({ok:true,
        headers:{get:()=>new Date().toUTCString()},json:()=>Promise.resolve(testPayload)});};""".replace('PAYLOAD',json.dumps(data)))
    context.route('https://radar.test/**',lambda route:route.fulfill(body=html,content_type='text/html'))
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('https://radar.test/?tabs=1&theme='+theme);page.locator('.tab[data-screen="s-radar"]').click()
    page.wait_for_function('radarView.good && !radarView.pending')
    page.evaluate('radarView.paused=true;radarStopLoop(false)')
    def push(d):
        page.evaluate('(d)=>{if(d.radar && d.radar.intent){d.radar.intent.seq=radarIntent.targetSeq || 0;d.radar.refresh.forSeq=d.radar.intent.seq;}testPayload=d;render(d)}',d)
    def note(state,index=0,total=1,seq=0):
        page.evaluate("""([state,index,total,seq])=>{radarIntent.postedAt=0;radarView.refresh=
          {state,frameIndex:index,frameTotal:total,forSeq:seq};radarNoteRender()}""",[state,index,total,seq])
    widths=page.locator('#rad-ramp i').evaluate_all('es=>es.map(e=>e.getBoundingClientRect().width)')
    assert len(widths)==9 and all(abs(a-b)<1 for a,b in zip(widths,[57.2,28.6,57.2,28.6,28.6,28.6,57.2,57.2,28.6]))
    assert page.locator('#rad-ramp').bounding_box()['width']==372
    ticks=page.locator('.rad-tick').evaluate_all("""es=>es.map(e=>({text:e.textContent,left:parseFloat(e.style.left),
      w:getComputedStyle(e,'::before').width,h:getComputedStyle(e,'::before').height}))""")
    assert [t['text'] for t in ticks]==['10','20','30','40','50','60','70']
    assert all(abs(t['left']-x)<1 and t['w']=='1px' and t['h']=='3px' for t,x in zip(ticks,[0,57.2,114.5,171.7,228.9,286.2,343.4]))
    pixels=page.locator('#rad-echo').evaluate('(e)=>Array.from({length:26},(_,i)=>Array.from(e.getContext("2d").getImageData(i*36+10,200,1,1).data))')
    assert pixels==[list(c) for _,c in ae._RADAR_LUT]
    assert page.locator('#rad-echo').evaluate('e=>getComputedStyle(e).filter')=='none'
    for source in ae._RADAR_SOURCES:
        changed=copy.deepcopy(data);changed['radar']['sourceId']=source;push(changed)
        assert page.locator('.rad-snow-ramp,.rad-snow-label').count()==0
    push(data)
    assert page.locator('#rad-updating').count()==0 and page.locator('#rad-plate').get_attribute('aria-busy') is None
    page.evaluate("""window.busyWrites=[];new MutationObserver(ms=>ms.forEach(m=>{
      if(m.attributeName==='aria-busy')busyWrites.push(m.target.getAttribute('aria-busy'));
      })).observe(document.getElementById('rad-plate'),{attributes:true});""")
    page.evaluate("radarView.zoomNote='Closest view for MRMS';radarView.data.sourceFallback='site-zoom-floor'")
    note('newest');assert page.locator('#rad-note').inner_text()=='Refreshing · newest frame'
    note('history',4,12);assert page.locator('#rad-note').inner_text()=='Refreshing · frame 4 of 12'
    page.evaluate("radarView.zoomNote='';radarView.data.sourceFallback=null")
    note('idle');assert page.locator('#rad-note').get_attribute('data-shown')=='false'
    page.evaluate('radarIntent.localSeq=41');note('newest',0,1,40)
    assert page.locator('#rad-note').inner_text()=='Refreshing · restarted'
    note('failed',0,1,41);assert page.locator('#rad-note').inner_text()=="Couldn't refresh · showing 17:12"
    for state in ('newest','history','failed','idle'):
        note(state,4,12,41)
        assert page.locator('#rad-note').bounding_box()['height']==14
        assert page.locator('#rad-note').bounding_box()['y']-page.locator('#rad-plate').bounding_box()['y']>=418
    assert page.locator('#rad-note').evaluate('e=>getComputedStyle(e).pointerEvents')=='none'
    # At the specified y418, the note is ABOVE the steppers. Verify the real
    # hit surface, then overlap it temporarily with + to test pointer pass-through.
    assert page.locator('#rad-note').evaluate('e=>{let b=e.getBoundingClientRect();return document.elementFromPoint(b.x+b.width/2,b.y+b.height/2)!==e}')
    page.evaluate("""let n=document.getElementById('rad-note'),b=document.getElementById('rad-zoom-in').getBoundingClientRect(),p=document.getElementById('rad-plate').getBoundingClientRect();
      n.style.top=(b.y-p.y+15)+'px';n.style.width='44px';n.style.right='auto';n.style.left=(b.x-p.x)+'px';""")
    b=page.locator('#rad-note').bounding_box();page.mouse.click(b['x']+b['width']/2,b['y']+7)
    page.wait_for_timeout(150)
    assert page.evaluate('pollURLs.filter(u=>u.includes("radarSeq=")).length')==1
    page.locator('#rad-note').evaluate("e=>e.removeAttribute('style')")
    page.evaluate("radarView.refresh={state:'newest',forSeq:radarIntent.localSeq,frameIndex:0,frameTotal:1};radarIntent.postedAt=Date.now()-400;radarNoteRender()");assert page.locator('#rad-note').get_attribute('data-shown')=='false'
    page.evaluate('radarIntent.postedAt=Date.now()-800;radarNoteRender()');assert page.locator('#rad-note').get_attribute('data-shown')=='true'
    # Reset only test intents before the next independent case.
    page.evaluate("radarZoom.desired=null;radarCenter.desired=null;radarSource.desired=null;radarGestureReset(false);radarIntent.localSeq=0;radarIntent.targetSeq=0;radarIntent.pending=null;radarIntent.postedAt=0")
    fallback=copy.deepcopy(data);fallback['radar'].update(zoom=5,zoomDesired=5,sourcePref='site',sitePreferred=True,sourceFallback='site-zoom-floor')
    push(fallback);page.wait_for_function('radarView.data.zoom===5 && !radarView.pending')
    assert page.locator('#rad-src-mosaic').get_attribute('aria-pressed')=='true'
    assert page.locator('#rad-src-site').get_attribute('aria-pressed')=='false'
    assert page.locator('#rad-src-site').get_attribute('data-preferred')=='true'
    assert page.locator('#rad-src-site').evaluate('e=>getComputedStyle(e).borderBottomStyle')=='dotted'
    assert page.locator('#rad-src-cap').inner_text().endswith('· wider than KATX reaches')
    assert page.locator('#rad-note').inner_text()=='KATX resumes at zoom 7'
    assert not page.locator('#rad-src-site').is_disabled()
    page.locator('#rad-src-mosaic').tap()
    assert page.locator('#rad-src-site').get_attribute('data-preferred')=='false'
    assert 'resumes' not in page.locator('#rad-note').inner_text()
    assert not page.locator('#rad-src-cap').inner_text().endswith('· wider than KATX reaches')
    page.wait_for_timeout(200)
    cancelled=copy.deepcopy(fallback);cancelled['radar'].update(sourcePref='mosaic',sitePreferred=False,sourceFallback=None)
    push(cancelled);page.wait_for_function('radarSource.desired===null')
    count=page.evaluate('pollURLs.filter(u=>u.includes("radarSeq=")).length')
    page.locator('#rad-src-site').tap();page.wait_for_timeout(250)
    urls=page.evaluate('pollURLs.filter(u=>u.includes("radarSeq="))')
    assert len(urls)==count+1 and 'radarSource=site' in urls[-1] and 'radarZoom=7' in urls[-1]
    page.evaluate("radarZoom.desired=null;radarSource.desired=null;radarGestureReset(false);radarIntent.localSeq=0;radarIntent.targetSeq=0;radarIntent.pending=null;radarIntent.postedAt=0")
    push(data);page.wait_for_function('radarView.data.zoom===7 && !radarView.pending')
    count=page.evaluate('pollURLs.filter(u=>u.includes("radarSeq=")).length')
    page.evaluate('radarZoomChange(1)');page.wait_for_timeout(60);page.evaluate('radarZoomChange(-1)');page.wait_for_timeout(180)
    assert page.evaluate('pollURLs.filter(u=>u.includes("radarSeq=")).length')==count+1
    page.evaluate('radarZoomChange(1)');page.wait_for_timeout(200);page.evaluate('radarZoomChange(-1)');page.wait_for_timeout(200)
    assert page.evaluate('pollURLs.filter(u=>u.includes("radarSeq=")).length')==count+3
    page.evaluate("radarZoom.desired=8;radarTransform(2,-478,-245,false);radarGesturePending()")
    # Record every rAF in the drag; a frame landing for the old geometry is frozen.
    motion=page.evaluate("""async d=>{
      const plate=document.getElementById('rad-plate'),stack=document.getElementById('rad-echo');
      const point=(type,x)=>plate.dispatchEvent(new PointerEvent(type,{pointerId:8,pointerType:'touch',clientX:x,clientY:260,bubbles:true}));
      const pixel=()=>Array.from(document.getElementById('rad-echo').getContext('2d').getImageData(20,200,1,1).data);
      let before=pixel(),matrices=[];point('pointerdown',400);
      for(let x=410;x<=480;x+=10){point('pointermove',x);renderRadar(d);await new Promise(requestAnimationFrame);matrices.push(new DOMMatrix(getComputedStyle(stack).transform).m41);}
      let held=pixel(),transform=stack.style.transform;point('pointerup',480);renderRadar(d);
      return {before,held,after:pixel(),matrices,transform,afterTransform:stack.style.transform};
    }""",data)
    assert motion['before']==motion['held']==motion['after']
    assert all(b>=a for a,b in zip(motion['matrices'],motion['matrices'][1:]))
    assert all(x!=0 for x in motion['matrices']) and motion['transform']==motion['afterTransform']
    page.evaluate("radarZoom.desired=null;radarCenter.desired=null;radarSource.desired=null;radarGestureReset(false);radarIntent.localSeq=0;radarIntent.targetSeq=0;radarIntent.pending=null;radarIntent.postedAt=0")
    # A crop arriving under fingers is evaluated on release and clears in the
    # decoded commit, with no spring back or intervening identity frame.
    push(data);page.wait_for_function('!radarView.pending')
    page.evaluate("""()=>{let p=document.getElementById('rad-plate');
      for(let [type,x] of [['pointerdown',400],['pointermove',440]])p.dispatchEvent(new PointerEvent(type,{pointerId:9,pointerType:'touch',clientX:x,clientY:260,bubbles:true}));}""")
    center=page.evaluate('radarGesture.pan.center')
    matching=copy.deepcopy(data);mr=matching['radar'];mr.update(center=center,centered=False)
    _,mr['metersPerPixel'],mr['bounds'],_=ae._radar_viewport(center['lat'],center['lon'],7,956,490)
    mr['latest']='v3-matching';mr['frames'][0]['id']=mr['latest']
    old=page.evaluate('radarView.good.id');push(matching)
    assert page.evaluate('radarView.good.id')==old
    page.evaluate("document.getElementById('rad-plate').dispatchEvent(new PointerEvent('pointerup',{pointerId:9,pointerType:'touch',clientX:440,clientY:260,bubbles:true}))")
    page.wait_for_function("radarView.good.id==='v3-matching' && radarGesture.state==='idle' && !radarView.pending")
    assert page.locator('#rad-echo').evaluate("e=>e.style.transform==='' && e.style.transition===''")
    page.evaluate("radarCenter.desired=null;radarSource.desired=null;clearTimeout(radarIntent.timer);radarIntent.ready=false;radarIntent.localSeq=0;radarIntent.targetSeq=0;radarIntent.pending=null;radarIntent.postedAt=0")
    sites=copy.deepcopy(data);sr=sites['radar'];sr.update(sourceMode='site',sourceId='iem-nexrad-n0b',sourcePref='site',sitePreferred=True,siteId='KATX')
    sr['sites']=[dict(id=i,lat=lat,lon=lon,primary=n==0,contributing=True,reason=None) for n,(i,lat,lon) in enumerate([
      ('KATX',47.65,-122.33),('KLGX',46.98,-123.82),('KRTX',49.2,-122.33)])]
    push(sites);page.wait_for_function('radarView.data.sourceMode==="site" && !radarView.pending')
    page.evaluate('window.heldOverlay=document.getElementById("rad-over").firstChild')
    aged=copy.deepcopy(sites)
    for item in aged['radar']['sites']:item['ageSec']=123
    push(aged)
    assert page.evaluate('document.getElementById("rad-over").firstChild===heldOverlay')
    assert page.locator('.rad-site-edge').count()==3
    assert page.evaluate("[...document.styleSheets].flatMap(s=>[...s.cssRules]).some(r=>r.selectorText==='.rad-over .rad-site-edge' && r.style.stroke==='var(--rule-faint)')")
    assert page.locator('#rad-src-cap').inner_text()=='NEXRAD · KATX +2 · ~5 min volumes · IEM / NOAA'
    for label in page.locator('.rad-site-label').all():
        assert 88<=float(label.get_attribute('y'))<=412
    assert 'KRTX' not in page.locator('.rad-site-label').all_text_contents()
    dark=copy.deepcopy(sites);dark['radar']['siteId']='KLGX'
    dark['radar']['sites'][0].update(contributing=False,primary=False,reason='not reporting')
    dark['radar']['sites'][1]['primary']=True
    dark['radar']['sites'].append(dict(id='KPDT',lat=47,lon=-121,contributing=True,primary=False,reason=None))
    push(dark);page.wait_for_function('!radarView.pending && radarView.data.siteId==="KLGX"')
    assert page.locator('#rad-src-site').inner_text()=='KLGX +2'
    assert page.locator('#rad-src-cap').inner_text().endswith('· KATX not reporting')
    secondary=copy.deepcopy(sites);sr=secondary['radar'];sr['observedTs']-=60
    sr['latest']='recovered-primary';sr['frames'][0].update(id=sr['latest'],ts=sr['observedTs'],siteScans=[dict(id='KLGX',ts=sr['observedTs'])])
    sr['sites'][0].update(contributing=False,reason='scan unavailable')
    for item in sr['sites'][1:]:item['contributing']=item['id']=='KLGX'
    push(secondary);page.wait_for_function('!radarView.pending && radarView.good.id==="recovered-primary"')
    assert page.locator('#rad-src-site').inner_text()=='KLGX'
    assert 'timeline KATX' in page.locator('#rad-src-cap').inner_text()
    assert 'KATX scan unavailable' in page.locator('#rad-src-cap').inner_text()
    colors=[b[k] for b in ae._RADAR_RAMP['bands'] for k in ('start','end')]
    purity=page.evaluate("""colors=>{
      const rgb=colors.map(c=>{let e=document.createElement('i');e.style.color=c;document.body.append(e);let v=getComputedStyle(e).color;e.remove();return v});
      return [...document.querySelectorAll(RAD_CONTROLS+',.rad-tick,.rad-legend-unit,#rad-src-cap,#rad-note')].every(e=>!rgb.includes(getComputedStyle(e).color));
    }""",colors)
    assert purity
    for target in page.locator('.rad-play,.rad-step,.rad-reset,.rad-seg').all():
        if target.is_visible():
            b=target.bounding_box();assert b['height']>=44 and b['width']>=44
    contrast=page.evaluate("""()=>{
      function rgba(s){let c=document.createElement('canvas'),x=c.getContext('2d');x.fillStyle=s;x.fillRect(0,0,1,1);let a=Array.from(x.getImageData(0,0,1,1).data);a[3]/=255;return a}
      function lum(c){let a=c.slice(0,3).map(v=>v/255<=.04045?v/255/12.92:((v/255+.055)/1.055)**2.4);return a[0]*.2126+a[1]*.7152+a[2]*.0722}
      let root=getComputedStyle(document.documentElement),ground=document.documentElement.dataset.theme==='night'?[255,255,255]:[0,0,0];
      return ['#rad-note','#rad-src-cap','.rad-loop-read','.rad-zoom-read'].map(sel=>{
        let e=document.querySelector(sel),style=getComputedStyle(e),fg=rgba(style.color),bg=rgba(style.backgroundColor),a=bg[3]??1;
        let mixed=bg.slice(0,3).map((v,i)=>v*a+ground[i]*(1-a)),x=lum(fg),y=lum(mixed);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);
      });
    }""")
    assert min(contrast)>=4.5,contrast
    assert page.evaluate('busyWrites.length')==0 and not errors,errors
    page.screenshot(path=str(output_dir/f'radar-v3-{theme}.png'))
    print('RADAR V3 PASS:',theme,'; G2 legend/ticks, LUT pixels, single note/suppression/priority, fallback, debounce, overlapping gestures, multisite/dark chrome, targets and contrast',flush=True)
    context.close()


def check_radar_recovery(browser, html, theme):
    """Deterministic delivery, debounce, reload and frozen-canvas regressions."""
    import copy
    data=source_payload('iem-mrms-lcref');r=data['radar']
    r.update(zoom=7,zoomDesired=7,zoomAuto=False,zoomMin=4,zoomMax=9,sourceMode='mosaic',sourcePref='mosaic',
             intent=dict(seq=40,zoom=7,source='mosaic',center='station'),
             refresh=dict(state='idle',frameIndex=1,frameTotal=1,forSeq=41),
             sources=[dict(mode='mosaic',available=True),dict(mode='site',siteId='KATX',available=True)])
    context=browser.new_context(viewport=dict(width=1024,height=600))
    context.add_init_script("""window.testPayload=PAYLOAD;window.pollURLs=[];window.serverSeq=42;
      window.fetch=u=>{pollURLs.push(String(u));return Promise.resolve({ok:true,
        headers:{get:k=>k==='X-Radar-Intent-Seq'?String(serverSeq):new Date().toUTCString()},
        json:()=>Promise.resolve(testPayload)});};""".replace('PAYLOAD',json.dumps(data)))
    context.route('https://radar.test/**',lambda route:route.fulfill(body=html,content_type='text/html'))
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('https://radar.test/?tabs=1&theme='+theme);page.locator('.tab[data-screen="s-radar"]').click()
    page.wait_for_function('radarView.good && !radarView.pending && !polling')
    assert page.evaluate('radarIntent.localSeq')==42
    page.clock.install();page.clock.pause_at(page.evaluate('Date.now()'))
    page.evaluate("()=>{radarView.paused=true;radarStopLoop(false);window.requests=[];window.fetch=u=>{pollURLs.push(String(u));return new Promise((resolve,reject)=>requests.push({u,resolve,reject}));};}")
    # Exact debounce boundary and coalescing, independent of host scheduling.
    page.evaluate('radarZoomChange(1)');page.clock.run_for(60);page.evaluate('radarZoomChange(-1)')
    page.clock.run_for(119);assert page.evaluate('requests.length')==0
    page.clock.run_for(1);assert page.evaluate('requests.length')==1
    query=page.evaluate('String(requests[0].u).split("&radarZoom=")[1]')
    assert query.endswith('radarSeq=43')
    page.evaluate('requests[0].reject(new Error("offline"))');page.wait_for_function('!polling')
    assert page.evaluate('radarIntent.pending.seq')==43
    page.evaluate('poll()');assert page.evaluate('requests.length')==2
    assert page.evaluate('String(requests[1].u).split("&radarZoom=")[1]')==query
    # Suppression has a hard boundary; retain the failed request while checking it.
    page.evaluate("radarView.refresh={state:'newest',forSeq:43,frameIndex:0,frameTotal:1}")
    page.clock.run_for(599);assert page.locator('#rad-note').get_attribute('data-shown')=='false'
    page.clock.run_for(2);assert page.locator('#rad-note').get_attribute('data-shown')=='true'
    # A second gesture queues behind the outstanding poll and flushes on finish.
    page.evaluate('radarZoomChange(1)');page.clock.run_for(120)
    assert page.evaluate('requests.length')==2
    page.evaluate("requests[1].resolve({ok:true,headers:{get:k=>k==='X-Radar-Intent-Seq'?'43':null},json:()=>Promise.resolve(testPayload)})")
    page.wait_for_function('requests.length===3')
    assert page.evaluate('requests[2].u.includes("radarSeq=44")')
    page.evaluate("requests[2].resolve({ok:true,headers:{get:k=>k==='X-Radar-Intent-Seq'?'44':null},json:()=>Promise.resolve(testPayload)})")
    page.wait_for_function('!polling');assert page.evaluate('radarIntent.pending') is None
    page.clock.resume()
    # Reset intent state only; a fresh deferred fixture must show no failure note.
    page.evaluate("radarZoom.desired=null;radarSource.desired=null;radarCenter.desired=null;radarGestureReset(false);radarIntent.targetSeq=0;radarIntent.postedAt=0;radarIntent.restartUntil=0")
    page.evaluate("renderRadar({radar:Object.assign({},radarView.data,{refresh:{state:'idle',forSeq:40,frameIndex:1,frameTotal:1}})})")
    assert page.locator('#rad-note').get_attribute('data-shown')=='false'
    # Draw a historical image distinct from newest, then hold replacement decode.
    page.evaluate("""()=>{let canvas=document.createElement('canvas');canvas.width=956;canvas.height=490;
      let ctx=canvas.getContext('2d');ctx.fillStyle='#112233';ctx.fillRect(0,0,956,490);
      window.historical=Object.assign({},radarView.good,{id:'historical',at:'17:10',ts:radarView.good.ts-120,img:canvas});
      radarDraw(historical);radarTransform(2,-478,-245,false);radarGesture.state='committing';
      window.nativeDecode=Image.prototype.decode;Image.prototype.decode=function(){return new Promise(resolve=>window.finishDecode=resolve)};
      window.sample=()=>Array.from(document.getElementById('rad-echo').getContext('2d').getImageData(20,200,1,1).data);
    }""")
    held=page.evaluate('sample()')
    replacement=copy.deepcopy(data);replacement['radar']['latest']='replacement';replacement['radar']['frames'][-1]['id']='replacement'
    replacement['radar']['intent']['seq']=44
    page.evaluate('d=>renderRadar(d)',replacement);page.wait_for_function('!!window.finishDecode')
    assert page.evaluate('sample()')==held and page.evaluate('radarView.current.id')=='historical'
    page.evaluate('()=>{finishDecode();Image.prototype.decode=nativeDecode;}');page.wait_for_function('!radarView.pending')
    assert page.evaluate('radarView.good.id')=='replacement' and page.evaluate('sample()')!=held
    page.evaluate("radarView.refreshFailure=true;radarDraw(historical)")
    assert page.locator('#rad-note').inner_text()=="Couldn't refresh · showing 17:10"
    page.evaluate('radarDraw(radarView.good)')
    assert page.locator('#rad-note').inner_text().endswith(page.evaluate('radarFrameLabel(radarView.good)'))
    page.evaluate("radarView.data.zoom=5;radarZoom.desired=null;radarSource.desired=null;radarCenter.desired={lat:47.8,lon:-122.3};radarTransform(1,40,20,false)")
    size=page.locator('#rad-plate').evaluate('e=>[e.clientWidth,e.clientHeight]')
    page.locator('#rad-src-site').click()
    actual=page.evaluate('[radarGesture.scale,radarGesture.x,radarGesture.y]')
    assert actual==[4,160-1.5*size[0],80-1.5*size[1]],actual
    assert page.evaluate('radarCenter.desired')==dict(lat=47.8,lon=-122.3)
    assert not errors,errors
    context.close()
    print('RADAR RECOVERY PASS:',theme,'; exact debounce/suppression clock, rejected delivery retry, authoritative reload seq, queued poll flush, idle deferral, historical decode fence, dynamic failure time',flush=True)


def check_radar_fast(browser, html, theme):
    """Sample every paint while geometry lands ahead of a delayed echo decode."""
    import copy
    data=source_payload('iem-mrms-lcref');r=data['radar']
    r.update(zoom=8,zoomDesired=8,zoomAuto=False,zoomMin=4,zoomMax=9,sourceMode='mosaic',sourcePref='mosaic',
             intent=dict(seq=40,zoom=8,source='mosaic',center='station'),
             refresh=dict(state='idle',frameIndex=1,frameTotal=1,forSeq=40))
    old_hash='a'*20;new_hash='b'*20
    r['basemap']=dict(hash=old_hash,url='radar/basemap/'+old_hash+'.svg')
    context=browser.new_context(viewport=dict(width=1024,height=600))
    context.add_init_script("""window.testPayload=PAYLOAD;window.pollTimes=[];
      window.fetch=u=>{
        if(String(u).includes('basemap/'))return Promise.resolve({ok:true,text:()=>Promise.resolve(
          '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 956 490"><path class="bm-coast" d="'+
          (String(u).includes('bbbb')?'M10,10L900,450':'M20,20L800,400')+'"/></svg>')});
        pollTimes.push(Date.now());return Promise.resolve({ok:true,headers:{get:()=>null},json:()=>Promise.resolve(testPayload)});
      };""".replace('PAYLOAD',json.dumps(data)))
    context.route('https://radar.test/**',lambda route:route.fulfill(body=html,content_type='text/html'))
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('https://radar.test/?tabs=1&theme='+theme);page.locator('.tab[data-screen="s-radar"]').click()
    page.wait_for_function('radarView.good && !radarView.pending && document.getElementById("rad-base").dataset.basemapHash')
    page.evaluate("""()=>{
      radarView.paused=true;radarStopLoop(false);clearTimeout(pollTimer);
      window.samples=[];window.sampling=true;
      function sample(){let e=document.getElementById('rad-echo'),b=document.getElementById('rad-base');
        let pixel=Array.from(e.getContext('2d').getImageData(478,245,1,1).data);
        samples.push({pixel,hidden:e.hidden,map:b.childElementCount,echo:e.style.transform,base:b.style.transform});
        if(sampling)requestAnimationFrame(sample);
      }requestAnimationFrame(sample);
      window.oldPixels=Array.from(document.getElementById('rad-echo').getContext('2d').getImageData(478,245,1,1).data);
      radarZoomChange(-1);
    }""")
    page.wait_for_function('radarIntent.targetSeq>40 && !polling')
    seq=page.evaluate('radarIntent.targetSeq')
    geometry=copy.deepcopy(data);g=geometry['radar']
    _,mpp,bounds,_=ae._radar_viewport(47.61,-122.33,7,956,490)
    bar,rings=ae._radar_scale(mpp,490,'mi',max_fraction=.25)
    g.update(geometryOnly=True,frames=[],latest=None,observedTs=None,observedAt=None,frameCount=0,completeFrameCount=0,
             zoom=7,zoomDesired=7,metersPerPixel=mpp,bounds=bounds,scaleBar=bar,rings=rings,
             basemap=dict(hash=new_hash,url='radar/basemap/'+new_hash+'.svg'),
             intent=dict(seq=seq,zoom=7,source='mosaic',center='station'),
             refresh=dict(state='newest',frameIndex=0,frameTotal=1,forSeq=seq))
    before=page.evaluate('oldPixels')
    page.evaluate('d=>{testPayload=d;renderRadar(d)}',geometry)
    page.wait_for_function('document.getElementById("rad-base").dataset.basemapHash==="'+new_hash+'"')
    assert page.locator('#rad-stack').evaluate("e=>e.style.transform===''")
    assert page.locator('#rad-base').evaluate("e=>e.style.transform==='' && e.style.transition===''")
    assert page.locator('#rad-over').evaluate("e=>e.style.transform===''")
    assert page.locator('#rad-echo').evaluate("e=>!!e.style.transform && getComputedStyle(e).opacity==='0.66' && !e.hidden")
    assert page.evaluate('radarGesture.state')=='committing'
    assert page.locator('.rad-scale').get_attribute('d').endswith('H'+str(340+bar['pixels'])+'v-8')
    # The next press composes from the NEW map while the same old echo is held.
    page.evaluate('radarZoomChange(-1)');page.wait_for_function('radarIntent.targetSeq>'+str(seq)+' && !polling')
    seq=page.evaluate('radarIntent.targetSeq')
    g['zoom']=g['zoomDesired']=g['intent']['zoom']=6;g['intent']['seq']=g['refresh']['forSeq']=seq
    _,g['metersPerPixel'],g['bounds'],_=ae._radar_viewport(47.61,-122.33,6,956,490)
    g['scaleBar'],g['rings']=ae._radar_scale(g['metersPerPixel'],490,'mi',max_fraction=.25)
    page.evaluate('d=>{testPayload=d;renderRadar(d)}',geometry)
    assert page.evaluate('radarGesture.scale')==.25
    assert page.locator('#rad-base').evaluate("e=>e.style.transform===''")
    page.evaluate("""()=>{window.nativeDecode=Image.prototype.decode;window.decodes=[];
      Image.prototype.decode=function(){return new Promise(resolve=>decodes.push(resolve))};}""")
    replacement=copy.deepcopy(geometry);fresh=replacement['radar']
    fresh.update(geometryOnly=False,latest='fresh',observedTs=r['observedTs'],observedAt=r['observedAt'],frameCount=1,completeFrameCount=1)
    raw=io.BytesIO();Image.new('RGBA',(956,490),(17,34,51,255)).save(raw,format='PNG')
    fresh['frames']=[dict(id='fresh',ts=r['observedTs'],at=r['observedAt'],complete=True,
                         url='data:image/png;base64,'+base64.b64encode(raw.getvalue()).decode())]
    fresh['refresh']['frameIndex']=1
    page.evaluate('d=>{testPayload=d;renderRadar(d)}',replacement)
    page.wait_for_function('decodes.length===1')
    page.wait_for_timeout(180)
    assert page.locator('#rad-echo').evaluate("e=>!!e.style.transform && !e.hidden")
    assert page.evaluate('samples.every(s=>JSON.stringify(s.pixel)===JSON.stringify(oldPixels))')
    # Old sequence cannot settle or replace the current map while decode is held.
    stale=copy.deepcopy(data);page.evaluate('d=>renderRadar(d)',stale)
    assert page.evaluate('radarView.data.zoom')==6
    page.evaluate('()=>{Image.prototype.decode=nativeDecode;decodes[0]()}')
    page.wait_for_function('!radarView.pending && radarView.good.id==="fresh"')
    page.wait_for_timeout(100)
    assert page.locator('#rad-echo').evaluate("e=>e.style.transform==='' && e.style.transition==='' && !e.hidden")
    assert page.evaluate('radarGesture.state')=='idle'
    page.evaluate('sampling=false')
    samples=page.evaluate('samples')
    assert len(samples)>10 and all(not x['hidden'] and x['map']>0 and x['pixel'][3]>0 for x in samples)
    assert samples[-1]['pixel']==[17,34,51,255] and samples[0]['pixel']==before
    # A periodic same-geometry refresh may reuse the already-decoded newest.
    # It must release stale opacity/loop fencing without another image decode.
    page.evaluate('d=>renderRadar(d)',geometry)
    assert page.evaluate('radarView.holdingGeometry')
    page.evaluate('d=>renderRadar(d)',replacement)
    assert page.evaluate('!radarView.holdingGeometry && !radarView.pending')
    # Controlled clock: fast cadence follows the payload acknowledgement, not
    # the POST response header, and cannot continue beyond twenty seconds.
    page.clock.install();page.clock.pause_at(page.evaluate('Date.now()'))
    page.evaluate('()=>{clearTimeout(pollTimer);pollTimes=[];radarZoomChange(1)}')
    page.clock.run_for(120);page.wait_for_function('!polling');assert page.evaluate('pollTimes.length')==1
    page.clock.run_for(399);assert page.evaluate('pollTimes.length')==1
    page.clock.run_for(1);page.wait_for_function('!polling');assert page.evaluate('pollTimes.length')==2
    page.clock.run_for(400);page.wait_for_function('!polling');assert page.evaluate('pollTimes.length')==3
    assert page.evaluate('pollTimes[2]-pollTimes[1]')==400
    page.evaluate("testPayload=Object.assign({},testPayload,{radar:Object.assign({},testPayload.radar,{intent:Object.assign({},testPayload.radar.intent,{seq:radarIntent.targetSeq})})})")
    page.clock.run_for(400);page.wait_for_function('!polling');count=page.evaluate('pollTimes.length')
    assert page.evaluate('radarFastUntil')==0
    page.clock.run_for(1999);assert page.evaluate('pollTimes.length')==count
    page.clock.run_for(1);page.wait_for_function('!polling');assert page.evaluate('pollTimes.length')==count+1
    page.evaluate('()=>{clearTimeout(pollTimer);pollTimes=[];radarZoomChange(-1)}')
    page.clock.run_for(120);page.wait_for_function('!polling')
    page.clock.run_for(20000);page.wait_for_function('!polling');count=page.evaluate('pollTimes.length')
    page.clock.run_for(1999);assert page.evaluate('pollTimes.length')==count
    page.clock.run_for(1);page.wait_for_function('!polling');assert page.evaluate('pollTimes.length')==count+1
    assert not errors,errors
    print('RADAR FAST PASS:',theme,'; geometry-only map/overlay untransformed, old echo reprojection/stale opacity, second zoom, matching decode hard cut,',len(samples),'nonblank paints, 400ms/2s polling and 20s cap',flush=True)
    context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser')
    parser.add_argument('--output-dir', type=Path, default=Path('/tmp/wfp-radar-v2'))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    html = (Path(__file__).resolve().parents[1] / 'design/almanac/console_live.html').read_text()
    shim = """
        window.pollURLs = [];
        window.fetch = function(url) {
            window.pollURLs.push(String(url));
            var data = PAYLOAD;
            data.ts = Date.now() / 1000;
            return Promise.resolve({ok:true, headers:{get:()=>new Date().toUTCString()},
                                    json:()=>Promise.resolve(data)});
        };
    """.replace('PAYLOAD', json.dumps(payload()))
    report = {}
    with sync_playwright() as p:
        options = dict(headless=True)
        if args.browser:
            options['executable_path'] = args.browser
        browser = p.chromium.launch(**options)
        for theme in ('paper', 'night'):
            context = browser.new_context(viewport={'width': 1024, 'height': 600}, device_scale_factor=2)
            context.add_init_script(shim)
            context.route('https://radar.test/**', lambda route: route.fulfill(body=html, content_type='text/html'))
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'https://radar.test/index.html?tabs=1&theme={theme}')
            page.wait_for_function('window.pollURLs.length >= 2')
            assert all('&view=radar' not in u for u in page.evaluate('pollURLs'))
            page.locator('.tab[data-screen="s-radar"]').click()
            count = page.evaluate('pollURLs.length')
            page.wait_for_function('n => pollURLs.length > n', arg=count)
            assert '&view=radar' in page.evaluate('pollURLs.at(-1)')
            assert '&r=1' in page.evaluate('pollURLs.at(-1)')
            page.wait_for_function('document.querySelector("#rad-plate").dataset.state === "live"')
            # Progress must remain observable even while decoded pixels are
            # frozen by an outstanding gesture intent. This adds no visual UI.
            refresh = dict(state='history', frameIndex=2, frameTotal=8, forSeq=0)
            actual=page.evaluate("""f => {
                const good=radarView.good; radarGesture.state='gesturing';
                renderRadar({radar:Object.assign({},radarView.data,{refresh:f})});
                const result={refresh:radarView.refresh,held:radarView.good===good};
                renderRadar({radar:radarView.data});
                result.reset=radarView.refresh.state==='idle'; radarGesture.state='idle';
                return result;
            }""",refresh)
            assert actual==dict(refresh=refresh,held=True,reset=True)
            assert page.locator('#s-radar').is_visible()
            assert page.locator('#rad-echo').evaluate('(e) => e.width === 956 && e.height === 490 && !e.hidden')
            box = page.locator('#rad-plate').bounding_box()
            assert box['width'] == 956 and box['height'] == 490
            assert box['y'] >= 0 and box['y'] + box['height'] <= 568
            page.screenshot(path=str(args.output_dir / f'radar-{theme}.png'))
            for tab in page.locator('.tab[data-screen]:not([data-screen="s-radar"]):not(.dim)').all():
                if not tab.is_visible():
                    continue
                screen = tab.get_attribute('data-screen')
                tab.click()
                count = page.evaluate('pollURLs.length')
                page.wait_for_function('n => pollURLs.length > n', arg=count)
                assert '&view=radar' not in page.evaluate('pollURLs.at(-1)')
                assert '&r=1' in page.evaluate('pollURLs.at(-1)')
                assert page.locator('#' + screen).is_visible()
            report[theme] = page.evaluate('pollURLs')
            assert errors == [], errors
            context.close()
        context = browser.new_context(viewport={'width': 1024, 'height': 600})
        context.add_init_script(shim)
        context.route('https://radar.test/**', lambda route: route.fulfill(body=html, content_type='text/html'))
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('https://radar.test/index.html')
        page.wait_for_function('window.pollURLs.length >= 2')
        assert page.locator('#s-obs').is_visible()
        assert not page.locator('.tabs').is_visible()
        assert all('&view=radar' not in u for u in page.evaluate('pollURLs'))
        assert '&r=1' in page.evaluate('pollURLs.at(-1)')
        assert errors == [], errors
        report['no-tabs'] = page.evaluate('pollURLs')
        context.close()
        check_cold_load_no_data(browser, html)
        check_forecast_blend(browser, html)
        check_loop(browser, html)
        for theme in ('paper', 'night'):
            check_radar_fast(browser, html, theme)
            check_radar_recovery(browser, html, theme)
            check_radar_v3(browser, html, args.output_dir, theme)
            check_source_switch(browser, html, args.output_dir, theme)
        for theme in ('paper', 'night'):
            check_radar_touch(browser, html, args.output_dir, theme)
            check_radar_v2(browser, html, args.output_dir, theme)
            check_basemap(browser, html, args.output_dir, theme)
            for site in ZOOM_SITES:
                check_zoom_site(browser, html, args.output_dir, theme, site)
        browser.close()
    (args.output_dir / 'poll-urls.json').write_text(json.dumps(report, indent=2))
    print('PASS: latest radar light/dark, tab view signal on/off, render heartbeat, other tabs, '
          'no-tabs default, cold-load no-data pose (no design sample values), '
          'forecast blend (starts at the sensor, converges to the model, no phantom dip/label), '
          'loop (cycle/pause/off-tab idle), hybrid source staging/abandoned loads, '
          'palette fidelity per paint, scan/relative times and telemetry, clear/stale/legacy, '
          '1024x600 alert layout in both themes; five-site zoom paper/night, real loopback persistence, '
          'pending/confirmed/coalescing/reset, keyboard/focus, bounds/caps/restore, geometry/paused history, '
          'refresh persistence, clear/stale, system dark theme, no accent zoom chrome; '
          'v3 shared reflectivity, intent/refresh note and G2 in both themes; no page errors')


if __name__ == '__main__':
    main()
