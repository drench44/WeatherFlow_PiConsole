"""The page keeps the Radar tab and explains itself while the engine starts, and
infers the same when an engine restarts underneath it. No screen bounce."""
from tests.test_radar_buffer_page import run_page


def test_engine_starting_keeps_the_tab_and_explains():
    run_page(r'''
const tab=document.querySelector('.tab[data-screen="s-radar"]');
renderRadar({radar:{available:false,reason:'no data yet',starting:{phase:'cache',cacheFiles:9128,sinceSec:12}},ts:1});
assert.equal(tab.hidden,false,'tab hidden while starting');
assert.match($('rad-src-cap').textContent,/Radar is starting .* saved tiles/);
assert.equal($('rad-status').textContent,'Starting');
assert.equal(radarView.data,null);
renderRadar({radar:{available:false,reason:'no data yet',starting:{phase:'acquire',cacheFiles:9128,sinceSec:60}},ts:2});
assert.match($('rad-src-cap').textContent,/fetching the first scan/);
''')


def test_an_engine_restart_under_a_running_page_is_inferred():
    run_page(r'''
const tab=document.querySelector('.tab[data-screen="s-radar"]');
renderRadar({radar:manifest(),ts:100800});assert.equal(radarView.everAvailable,true,'an available manifest was rendered');
renderRadar({radar:{available:false,reason:'no data yet'},ts:100900});   // old engine: no starting field
assert.equal(tab.hidden,false);  // the bounce lives on the hidden-tab path only
assert.match($('rad-src-cap').textContent,/Radar is starting/);
''')


def test_no_radar_here_still_hides_the_tab():
    run_page(r'''
const tab=document.querySelector('.tab[data-screen="s-radar"]');
renderRadar({radar:{available:false,reason:'outside coverage'},ts:100900});
assert.equal(tab.hidden,true);
''')


def test_a_page_that_never_saw_radar_trusts_only_the_engine():
    run_page(r'''
radarView.everAvailable=false;const tab=document.querySelector('.tab[data-screen="s-radar"]');
renderRadar({radar:{available:false,reason:'no data yet'},ts:100900});
assert.equal(tab.hidden,true,'a fresh page with an old engine and no data has no radar to promise');
''')
