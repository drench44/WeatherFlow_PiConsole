"""Clock compatibility through the production formatter, parser and DOM path."""
from pathlib import Path
import subprocess

import pytest

from lib import almanac_emit as ae
from tests.test_clock_format import TZ, local
from tests.test_radar_buffer_page import run_page


@pytest.mark.parametrize('text,expected', [
    (None, None), ('-', '-'), ('Clear and calm', 'Clear and calm'),
    ('Wind 12 mph', 'Wind 12 mph'), ('12 AM', '12\u00a0AM'),
    ('Clear until 12 am tomorrow', 'Clear until 12\u00a0AM tomorrow'),
    ('6:53 pm', '6:53\u00a0PM'), ('23:59', '23:59'),
])
def test_upstream_text_normalization(text, expected):
    assert ae._clock_case(text) == expected


def test_nbsp_sun_clocks_preserve_python_daylight_math():
    result = ae.AlmanacEmitter._sun_fraction('6:00\u00a0AM', '6:00\u00a0PM', local(2026,9,16,12,0), TZ)
    assert result == (.5, '12h 0m', '6h 0m')


def function(html, name, following):
    start = html.index('function '+name+'(')
    return html[start:html.index(following, start)]


def test_real_page_parsers_forecast_and_masthead_both_clock_styles():
    html = Path('design/almanac/console_live.html').read_text()
    script = r'''
const assert=require('node:assert/strict');
const DASH='—',ANIM_NUM={},FMT={};let data={time:'12:00\u00a0AM'};
const node={dataset:{k:'time'},classList:{contains:()=>false},closest:()=>true,
  children:[],set textContent(s){this.text=s;this.children=[]},append(n){this.children.push(n)}};
const document={querySelectorAll:()=>[node],createElement:()=>({})};
FUNCTIONS
assert.equal(hm('12:00\u00a0AM'),0);assert.equal(toMin('12:00\u00a0PM'),720);
assert.equal(hm('1:30\u00a0PM'),13.5);assert.equal(hm('23:59'),23+59/60);
assert.equal(hm('-'),null);assert.equal(hm(null),null);
assert.equal(fcClock(0),'12\u00a0AM');assert.equal(fcClock(12),'12\u00a0PM');
assert.equal(fcClock(17.5),'5:30\u00a0PM');
renderText(data);assert.equal(node.text,'12:00');assert.equal(node.children[0].textContent,'AM');
data={time:'23:59'};renderText(data);assert.equal(node.text,'23:59');assert.equal(node.children.length,0);
assert.equal(fcClock(0),'00:00');assert.equal(fcClock(17.5),'17:30');
'''
    definitions = '\n'.join([
        function(html, 'hm', '  function clamp'),
        function(html, 'toMin', '  function isDaytime'),
        function(html, 'fcClock', '    function fcLabel'),
        function(html, 'renderText', '  /* ═'),
    ])
    result = subprocess.run(['node'], input=script.replace('FUNCTIONS', definitions), text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_radar_fallback_rolls_midnight_in_both_clock_styles():
    html = Path('design/almanac/console_live.html').read_text()
    hm = function(html, 'hm', '  function clamp')
    run_page(hm+r'''
radarView.data.observedTs=100000;
radarView.data.observedAt='12:05\u00a0AM';
assert.equal(radarFrameLabel({ts:99400}),'11:55\u00a0PM');
radarView.data.observedAt='00:05';
assert.equal(radarFrameLabel({ts:99400}),'23:55');
assert.equal(radarFrameLabel({ts:99400,at:'11:55\u00a0PM'}),'11:55\u00a0PM');
''')
