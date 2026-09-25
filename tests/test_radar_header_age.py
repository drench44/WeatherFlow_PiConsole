"""The radar header's 'min old' measures the data, not the frame the loop is playing."""
import json
import subprocess
from pathlib import Path

import pytest

HTML = Path('design/almanac/console_live.html').read_text()


def radar_state_source():
    start = HTML.index('function radarState(')
    return HTML[start:HTML.index('\n  function radarActivate(', start)]


@pytest.mark.parametrize('frame_offset,received_age,retained,expect_old', [
    (-2700, 30, False, False),   # a 45-minute-old loop frame of a fresh loop
    (0, 30, False, False),       # the newest frame, fresh
    (0, 780, False, True),       # the newest frame itself is 13 min old
    (-900, 30, True, True),      # a retained frame from an abandoned window, 15.5 min old
])
def test_header_age_follows_the_data(frame_offset, received_age, retained, expect_old):
    script = """
const nodes={};
function el(id){return nodes[id]||(nodes[id]={id,dataset:{},text:'',replaceChildren(...c){this.text=c.map(n=>n.text||'').join('')},append(...c){this.text+=c.map(n=>n.text||'').join('')},set textContent(v){this.text=v},get textContent(){return this.text}});}
const $=el;const document={createTextNode:t=>({text:t}),createElement:()=>({text:'',set textContent(v){this.text=v}})};
const performance={now:()=>1000};
function radarFrameLabel(f){return 'T'+f.ts}
const args=%s;
const newest=100000,f={ts:newest+args.offset};
var radarView={data:{staleSec:900,observedTs:newest,tiles:{frames:args.retained?[]:[{ts:f.ts}]},refresh:null},current:f,receivedAge:args.received,receivedAt:1000,holdingWindow:false,clear:false};
%s
radarState();console.log(JSON.stringify(el('rad-status').text));
""" % (json.dumps(dict(offset=frame_offset, received=received_age, retained=retained)), radar_state_source())
    out = subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True).stdout
    assert ('min old' in json.loads(out)) == expect_old, out
