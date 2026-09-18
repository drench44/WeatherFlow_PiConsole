"""The page tells the engine about a human touch and shows weather on the Radar tab."""
from pathlib import Path
from tests.test_radar_buffer_page import run_page


def test_weather_dot_follows_the_attention_payload():
    run_page(r'''
const tab=document.querySelector('.tab[data-screen="s-radar"]');
const r=manifest();r.attention={tier:'watch',weather:true,waking:false};
renderRadar({radar:r,ts:100900});
assert.equal(tab.dataset.weather,'on');
r.attention={tier:'rest',weather:false,waking:false};
renderRadar({radar:r,ts:100902});
assert.equal(tab.dataset.weather,'off');
''')


def test_touch_flag_rides_the_next_poll_only():
    html = Path('design/almanac/console_live.html').read_text()
    assert 'document.addEventListener("pointerdown", function () { presenceDirty = true; }' in html
    assert '(touched ? "&touch=1" : "")' in html
    assert 'var touched = presenceDirty; presenceDirty = false;' in html   # consumed by the poll that carries it
