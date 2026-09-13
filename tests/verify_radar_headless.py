"""Direct Python Playwright check; run from the repo root (no local server needed).

./venv-test/bin/python -m tests.verify_radar_headless [--browser /path/to/chromium]
Screenshots and recorded fetch URLs go to --output-dir (default /tmp/wfp-radar-headless).
Kept separate from pytest so the unit suite does not require a browser install.
"""
import argparse
import base64
import io
import json
import time
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
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
    _, mpp, bounds, _ = ae._radar_viewport(47.61, -122.33, zoom, 480)
    bar, rings = ae._radar_scale(mpp, 480, 'mi')
    png = io.BytesIO()
    Image.new('RGBA', (480, 480), (0, 163, 224, 100)).save(png, format='PNG')
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
    _, mpp, bounds, _ = ae._radar_viewport(47.61, -122.33, zoom, 480)
    bar, rings = ae._radar_scale(mpp, 480, 'mi')
    def frame(offset, rgb):
        png = io.BytesIO()
        Image.new('RGBA', (480, 480), rgb + (255,)).save(png, format='PNG')   # opaque -> hasEcho
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
            srcs.add(document.getElementById('rad-echo').src);
            times.add(document.getElementById('rad-frame-time').textContent);
            await new Promise(r => setTimeout(r, 250));
        }
        return { srcs: srcs.size, times: [...times].sort() };
    }""")
    assert seen['srcs'] >= 2, f'loop did not cycle the echo: {seen}'
    assert len(seen['times']) >= 2, f'frame-time label did not change: {seen}'
    # pause holds a single frame
    page.locator('#rad-play').click()
    held = page.evaluate('document.getElementById("rad-echo").src')
    page.wait_for_timeout(1400)
    assert page.evaluate('document.getElementById("rad-echo").src') == held, 'pause did not hold the frame'
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
             completeFrameCount=3, updatedAt='21:08', observedAt='21:04')
    legend = settings['legend']
    r['legend'] = dict(id=legend['id'], colorId=legend['colorId'], colorName=legend['colorName'],
        rain=[dict(dbz=d, hex=h, label=l) for d, h, l in legend['rain']],
        snow=dict(zip(('hex', 'label'), legend['snow'])) if legend['snow'] else None)
    for i, frame in enumerate(r['frames']):
        offset = (2 - i) * settings['cadence']
        minutes = 21 * 60 + 4 - offset // 60
        frame.update(id='collision-' + str(i), ts=r['observedTs'] - offset,
                     at=f'{minutes // 60:02d}:{minutes % 60:02d}')
        color = (51, 102, 204) if source.startswith('iem') else (0, 163, 224)
        stream = io.BytesIO()
        Image.new('RGBA', (480, 480), color + (255,)).save(stream, 'PNG')
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
    assert page.locator('#rad-updated').inner_text() == '21:08'
    assert '~2 min' in page.locator('#rad-cadence').inner_text()
    assert page.locator('#rad-legend .rad-legend-row').count() == 7
    assert page.locator('#rad-attrib a').inner_text() == 'IEM / NOAA MRMS'
    assert page.locator('#alert-strip').is_visible()
    # The render must never expose a palette/image mismatch, even for one paint.
    page.evaluate("""() => {
        window.mismatches = [];
        function inspect() {
            const e = document.getElementById('rad-echo'), r = radarView.data;
            if (r && e.complete && e.naturalWidth) {
                const c = document.createElement('canvas'); c.width=1; c.height=1;
                const x=c.getContext('2d'); x.drawImage(e,0,0,1,1);
                const red=x.getImageData(0,0,1,1).data[0];
                const swatch=document.querySelector('#rad-legend .rad-swatch').style.backgroundColor;
                const iem=r.sourceId === 'iem-mrms-lcref';
                if (red !== (iem ? 51 : 0) || (iem ? swatch !== 'rgb(164, 164, 255)' :
                    !swatch.startsWith('rgba(146, 136, 113,'))) mismatches.push([r.sourceId, red, swatch]);
            }
            window.inspectId = requestAnimationFrame(inspect);
        }
        inspect();
    }""")
    for selector in ('#rad-plate', '.rad-rail', '#rad-loop', '#rad-updated-row', '#rad-cadence-row', '#rad-cadence', '#rad-legend'):
        box = page.locator(selector).bounding_box()
        assert box and box['y'] >= 0 and box['y'] + box['height'] <= 568, (theme, selector, box)
        assert box['x'] + box['width'] <= 1024, (theme, selector, box)
    page.wait_for_timeout(500)  # alert entrance animation has settled
    page.screenshot(path=str(output_dir / f'radar-hybrid-{theme}.png'))
    # Pausing preserves the selected frame and uses its emitter-provided local time.
    page.locator('#rad-play').click()
    assert page.locator('#rad-frame-time').inner_text() == page.evaluate('radarView.current.at')
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
    assert page.locator('#rad-attrib a').inner_text() == 'IEM / NOAA MRMS'
    assert '~2 min' in page.locator('#rad-cadence').inner_text()
    assert page.locator('#rad-asof').inner_text() == '21:04'
    assert not page.evaluate('!!radarView.timer')
    stream = io.BytesIO(); Image.new('RGBA', (480, 480), (0, 163, 224, 255)).save(stream, 'PNG')
    pending.pop().fulfill(body=stream.getvalue(), content_type='image/png')
    page.wait_for_function('radarView.data.sourceId === "rainviewer" && !radarView.pending')
    assert page.locator('#rad-attrib a').inner_text() == 'RainViewer'
    assert page.locator('#rad-attrib a').get_attribute('href') == 'https://www.rainviewer.com/'
    assert page.locator('#rad-legend .rad-legend-row').count() == 8
    assert '~10 min' in page.locator('#rad-cadence').inner_text()
    assert page.locator('#rad-updated').inner_text() == '21:08'
    page.wait_for_function('radarView.loaded.every(f => f.ready)')
    for selector in ('#rad-plate', '.rad-rail', '#rad-loop', '#rad-legend'):
        box = page.locator(selector).bounding_box()
        assert box and box['y'] + box['height'] <= 568, (theme, selector, box)
    page.screenshot(path=str(output_dir / f'radar-fallback-{theme}.png'))
    # A late successful load from an abandoned generation cannot resurrect it.
    abandoned = source_payload('iem-mrms-lcref')
    abandoned['radar']['frames'][-1]['url'] = 'https://radar.test/switch-abandoned.png'
    push(abandoned); page.wait_for_timeout(100)
    assert pending
    push(rv)
    stream = io.BytesIO(); Image.new('RGBA', (480, 480), (51, 102, 204, 255)).save(stream, 'PNG')
    pending.pop().fulfill(body=stream.getvalue(), content_type='image/png')
    page.wait_for_timeout(150)
    assert page.evaluate('radarView.data.sourceId') == 'rainviewer'
    # Failed source changes retain every old metadata field along with the image.
    abandoned['radar']['frames'][-1]['url'] = 'https://radar.test/switch-failed.png'
    push(abandoned); page.wait_for_timeout(100)
    pending.pop().fulfill(status=503, body='not ready')
    page.wait_for_function('radarView.failed')
    assert page.locator('#rad-attrib a').inner_text() == 'RainViewer'
    assert '~10 min' in page.locator('#rad-cadence').inner_text()
    assert page.locator('#rad-updated-row').is_visible()
    assert page.locator('#rad-plate').get_attribute('data-state') == 'stale'
    assert page.evaluate('mismatches') == []
    page.evaluate('cancelAnimationFrame(inspectId)')
    # Same-frame Updated changes must not stage or replace the decoded image node.
    page.evaluate('window.previousEcho = document.getElementById("rad-echo")')
    rv['radar']['updatedAt'] = '21:10'
    push(rv)
    assert page.evaluate('previousEcho === document.getElementById("rad-echo")')
    assert page.locator('#rad-updated').inner_text() == '21:10'
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
    stream = io.BytesIO(); Image.new('RGBA', (480, 480)).save(stream, 'PNG')
    clear['radar']['frames'] = [dict(clear['radar']['frames'][-1],
        url='data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode())]
    clear['radar'].update(completeFrameCount=1, frameCount=1, historySpanSec=0, frameSpacingSec=None)
    push(clear)
    page.wait_for_function('document.getElementById("rad-plate").dataset.state === "clear"')
    assert 'no echoes shown' in page.locator('#rad-status').inner_text().lower()
    assert page.locator('#rad-updated-row').is_visible() and page.locator('#rad-asof').is_visible()
    assert page.locator('#rad-cadence').inner_text() == 'Source cadence ~2 min'
    clear['radar'].update(stale=True, ageSec=601); push(clear)
    assert page.locator('#rad-plate').get_attribute('data-state') == 'stale'
    assert page.locator('#rad-updated-row').is_visible()
    # Older RainViewer payloads still render, hiding fields absent from that contract.
    legacy = source_payload('rainviewer')
    for field in ('sourceId', 'cadenceSec', 'frameSpacingSec', 'completeFrameCount',
                  'historySpanSec', 'historyGaps', 'updatedAt', 'attributionUrl'):
        legacy['radar'].pop(field, None)
    legacy['radar']['legend'].pop('id')
    for f in legacy['radar']['frames']: f.pop('at')
    push(legacy)
    page.wait_for_function('radarView.data.sourceId === undefined')
    assert page.locator('#rad-updated-row').is_hidden() and page.locator('#rad-cadence-row').is_hidden()
    assert page.locator('#rad-attrib a').inner_text() == 'RainViewer'
    assert errors == [], errors
    context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser')
    parser.add_argument('--output-dir', type=Path, default=Path('/tmp/wfp-radar-headless'))
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
        for theme in ('light', 'night'):
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
            assert page.locator('#s-radar').is_visible()
            assert page.locator('#rad-echo').evaluate('(e) => e.complete && e.naturalWidth === 480 && !e.hidden')
            box = page.locator('#rad-plate').bounding_box()
            assert box['width'] == box['height'] == 480
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
        check_loop(browser, html)
        for theme in ('light', 'night'):
            check_source_switch(browser, html, args.output_dir, theme)
        browser.close()
    (args.output_dir / 'poll-urls.json').write_text(json.dumps(report, indent=2))
    print('PASS: latest radar light/dark, tab view signal on/off, render heartbeat, other tabs, '
          'no-tabs default, loop (cycle/pause/off-tab idle), hybrid source staging/abandoned loads, '
          'palette fidelity per paint, Updated/relative times, clear/stale/legacy, '
          '1024x600 alert layout in both themes; no page errors')


if __name__ == '__main__':
    main()
