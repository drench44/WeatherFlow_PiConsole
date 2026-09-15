"""v4.5 supersedes hatch gates: no renderer, timer or coverage-only machinery."""
import json
import subprocess
from pathlib import Path

import pytest

HTML = Path('design/almanac/console_live.html').read_text()
RADAR = HTML[HTML.index('  /* V4:'):HTML.index('  function renderRadar(')]


def function(name):
    start = HTML.index('  function '+name+'(')
    return HTML[start:HTML.index('\n  }', start)+4]


def test_radar_has_no_hatch_machinery_or_token():
    for obsolete in ('hatch', 'Hatch', 'radarMissingSince', 'radarManifestExpected',
                     'radarDrawnCoverage', 'radarCoverage',
                     'partial coverage', 'expectedCells', 'missingCells',
                     'radarMetrics.acquiring'):
        assert obsolete not in RADAR
    assert "--rule-faint" not in function("radarEchoPaint")


@pytest.mark.parametrize('count', range(9))
@pytest.mark.parametrize('paused', [False, True])
def test_incomplete_inventory_is_visible_even_with_partial_echoes(count, paused):
    script = function('radarLoopSync') + '''
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{dataset:{},style:{},setAttribute(){},removeAttribute(){}});return nodes.get(id);};
const frames=Array.from({length:COUNT},()=>({ready:true,hasEcho:true}));
const radarView={data:{observedTs:1,frameCount:COUNT},active:true,paused:PAUSED,current:{ts:1,ready:true},nextAt:0,loaded:frames};
const radarReady=()=>frames,radarReduced=()=>false,radarCouldLoop=()=>false,radarWake=()=>{},radarFrameLabel=()=>'17:12';
radarLoopSync();console.log(JSON.stringify($('rad-frame-time').textContent));
'''.replace('COUNT', str(count)).replace('PAUSED', json.dumps(paused))
    result = subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
    # The inventory is measured against the scans that exist (a 10-min site has six an hour),
    # and the loop plays from four: below that the read is the inventory, above it the scan time.
    total = min(8, count) or 8
    expected = (('Paused' if paused else 'Buffering') + f' · {count} of {total}' if count < min(4, total)
                else ('Paused · ' if paused else '') + '17:12 · newest')
    assert json.loads(result.stdout) == expected
