"""Execute the page's pure hatch eligibility rules against independent cases."""
import json
import subprocess
from pathlib import Path

import pytest

HTML = Path('design/almanac/console_live.html').read_text()


def function(name):
    start = HTML.index('  function '+name+'(')
    return HTML[start:HTML.index('\n  function ', start+1)]


@pytest.mark.parametrize('mask,covered,missing,expected,holes', [
    ('1f', True, 2, 5, 2),  # exactly 40%: a few holes
    ('1f', True, 3, 5, 0),  # above 40%: acquiring
    ('00', True, 2, 0, 0),  # geographic range does not request tiles
    ('1f', False, 2, 5, 0),  # requested does not imply drawn coverage
    (None, True, 2, 0, 0),  # no guessed expectations for a legacy manifest
])
def test_expected_mask_and_coverage(mask, covered, missing, expected, holes):
    script = '\n'.join(function(n) for n in ('radarManifestExpected', 'radarHatchCells'))
    script += '''
const radarView={data:{tiles:{z:8,grid:{x0:255,y0:90,w:5,h:1},newest:{stamp:'scan',expectedMask:MASK}}}},radarMissingSince=new Map(),radarMetrics={};
const radarExpected=()=>true,radarDrawnCoverage=()=>({sites:[]}),radarCoverageIncludes=()=>COVERED,radarTileKey=(f,z,x,y)=>f.stamp+'/'+z+'/'+x+'/'+y;
const cells=Array.from({length:5},(_,i)=>({t:{z:8,x:(255+i)%256,y:90},missing:i<MISSING,rect:[i,0,1,1]})),f={stamp:'scan'};
const early=radarHatchCells(f,cells,0).rects.length;
const grace=radarHatchCells(f,cells,399).rects.length;
const holes=radarHatchCells(f,cells,400).rects.length;
const bad=[{z:7,x:255,y:90},{z:8,x:4,y:90},{z:8,x:255,y:91}].map(t=>radarManifestExpected(f,t));
console.log(JSON.stringify({early,grace,holes,expected:radarMetrics.expectedCells,bad,old:radarManifestExpected({stamp:'old'},cells[0].t)}));
'''.replace('MASK', json.dumps(mask)).replace('COVERED', json.dumps(covered)).replace('MISSING', str(missing))
    result = subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == dict(early=0, grace=0, holes=holes,
                                           expected=expected, bad=[False]*3, old=False)


@pytest.mark.parametrize('lat,lon,mrms,site', [
    (48.19, -122.5, True, True),
    (48.19, -115, True, False),
    (48.19, -135, False, False),
    (58, -120, False, False),
    (15, -100, False, False),
    (40, -55, False, False),
])
def test_geographic_coverage_is_independent_of_manifest(lat, lon, mrms, site):
    script = '\n'.join(function(n) for n in ('radarWorldPoint', 'radarWorldInverse',
                                              'radarSiteCovers', 'radarCoverageIncludes'))
    script += f"""
const RAD_MAX_LAT=85.05112878,clamp=(x,a,b)=>Math.max(a,Math.min(b,x)),radarWrap=(x,z)=>(x%2**z+2**z)%2**z;
const radarSiteTable=[{{id:'KATX',lat:48.19,lon:-122.5}}],radarView={{data:{{sites:[]}}}};
const p=radarWorldPoint({lat},{lon},8),t={{z:8,x:Math.floor(p[0]/256),y:Math.floor(p[1]/256)}};
console.log(JSON.stringify([radarCoverageIncludes({{bounds:{{w:-130,e:-60,s:20,n:55}}}},t),radarCoverageIncludes({{sites:radarSiteTable}},t)]));
"""
    result = subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == [mrms, site]
