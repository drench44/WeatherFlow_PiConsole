"""Reservation ownership across deterministic acquisition outcomes; no wall sleeps."""
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize('geo', [False, True])
@pytest.mark.parametrize('outcome', ['success', '404', 'deadline', 'headers', 'body', 'queued-decode', 'decode', 'decode-failure', 'cap'])
def test_four_job_reservations_always_return(geo, outcome):
    html = Path('design/almanac/console_live.html').read_text()
    def section(start, end):
        return html[html.index('  '+start):html.index('  '+end)]
    code = section('function radarCancelJobs(', 'function radarBasemapLevel(')
    code += section('async function radarReadPNG(', 'function radarBasePaint(')
    code += section('function radarFrameLayers(', 'function radarFindTile(')
    code += section('function radarUnreserve(', 'function radarGeoClear(')
    script = r'''
const assert=require('node:assert/strict');
const outcome=OUTCOME,geo=GEO,timers=new Map();let timer=0;
globalThis.setTimeout=fn=>{timers.set(++timer,fn);return timer};globalThis.clearTimeout=id=>timers.delete(id);
let radarJobs=new Set(),radarReserved=0,radarTileBusy=0,radarGeoBusy=0;
let radarTileQueue=[],radarGeoQueue=[],radarTilePending=new Set(),radarGeoPending=new Set();
let radarTileAbsent=new Map(),radarGeoAbsent=new Map(),radarGeoTiles=new Map(),radarGeoDamage=[];
let radarTileAbsentTries=new Map();function radarTileAbsentMark(k){radarTileAbsent.set(k,1)}function radarTileAbsentClear(k){radarTileAbsent.delete(k)}
let radarBaseStyle={theme:'paper'},radarView={active:true,data:{geo:{version:'v'}}},radarEchoEpoch=0;
const RAD_TILE_MS=2500,RAD_TILE_BYTES=262144;
let radarPumpTiles=()=>{},radarTrace=()=>{},radarMemory=()=>radarReserved,radarBasemapLevel=()=>7;
let radarSiteCovers=()=>true,radarStamp=()=>'',radarTileURL=()=>'/tile';
let radarPNGMeta=()=>({remapped:true,visiblePixels:1,opaquePixels:1,unmatchedPixels:0,ambiguousPixels:0,unmatchedColors:0,opaqueColors:1});
const live=new Set();const bitmap=()=>{const b={width:256,height:256,close(){live.delete(b)}};live.add(b);return b};
const radarTileScratch={getContext:()=>({clearRect(){},drawImage(){}}),transferToImageBitmap:bitmap};
const retained=[];let radarTileRemember=(key,tile,job)=>{retained.push(tile.bitmap);radarJobTransfer(job,262144)};
let decodeNotify;const decoding=new Promise(r=>decodeNotify=r);
let notify;let ready=new Promise(r=>notify=r),events=0;
let radarWake=()=>{if(++events>=4)notify()};
const bytes=new Uint8Array(24),view=new DataView(bytes.buffer);view.setUint32(0,0x89504e47);view.setUint32(16,256);view.setUint32(20,256);
const never=()=>new Promise(()=>{});
globalThis.fetch=(url)=>{
  if(url==='radar-bad-tile')return Promise.resolve({});
  if(['headers','deadline','cap'].includes(outcome))return never();
  let read=0;
  return Promise.resolve({ok:outcome!=='404',status:404,headers:{get:()=>null},body:{getReader:()=>({
    read:()=>{if(outcome==='body'){if(++events===4)notify();return never()};return Promise.resolve(read++?{done:true}:{value:bytes})},
    cancel:()=>Promise.resolve(),releaseLock(){}
  })}});
};
CODE
radarDecodeImage=async(buf,job)=>{
  if(outcome==='decode-failure')throw Error('decode failure');
  if(outcome==='decode'){decodeNotify();return radarAwait(never(),job);}
  return bitmap();
};
(async()=>{
  const jobs=Array.from({length:4},(_,i)=>({key:String(i),f:{ts:1,smooth:true,revision:'v'},source:'x',z:7,x:0,y:0,epoch:0,geo,version:'v',theme:'paper',reservation:786432}));
  const promises=jobs.map(job=>{radarReserved+=job.reservation;radarJobStart(job);if(geo){radarGeoBusy++;radarGeoPending.add(job.key)}else{radarTileBusy++;radarTilePending.add(job.key)};return (geo?radarFetchGeo:radarFetchTile)(job)});
  assert.equal(radarReserved,4*786432);
  if(['body','queued-decode','decode','decode-failure','success'].includes(outcome))await ready;
  if(['success','decode-failure'].includes(outcome)){
    for(const pending of promises){radarDecodeTurn();await pending;}
  }else if(outcome==='decode'){radarDecodeTurn();await decoding;radarCancelJobs();}
  else if(outcome==='deadline'){for(const fn of [...timers.values()])fn();}
  else if(outcome!=='404'){
    if(outcome==='cap')radarMemory=()=>{throw Error('radar raster memory cap')};
    radarCancelJobs();
  }
  await Promise.allSettled(promises);
  assert.equal(radarReserved,0);assert.equal(radarTileBusy,0);assert.equal(radarGeoBusy,0);
  assert.equal(radarJobs.size,0);assert.equal(radarTilePending.size+radarGeoPending.size,0);
  assert.equal(timers.size,0);
  for(const b of retained)b.close();for(const t of radarGeoTiles.values())t.bitmap.close();
  assert.equal(live.size,0);
  console.log('PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
'''.replace('OUTCOME', repr(outcome)).replace('GEO', str(geo).lower()).replace('CODE', code)
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == 'PASS'
