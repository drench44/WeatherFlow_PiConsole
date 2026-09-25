"""The production page's native layer identity, caption and staging in Node."""
from tests.test_radar_buffer_page import run_page
from tests.test_radar_auto_page import controls


def test_mosaic_is_one_layer_and_v1_preserves_painter_order():
    run_page(r'''
const f={ts:100000,stamp:'197001020346',mosaicKey:'M0123456789abcdef01234567',siteScans:[{id:'KLGX',ts:99900},{id:'KATX',ts:100000}]};
assert.deepEqual(radarFrameLayers(f),[{id:f.mosaicKey,ts:f.ts}]);
assert.match(radarTileURL('iem-nexrad-n0b',radarFrameLayers(f)[0].id,f.stamp,8,40,80),/\/M0123456789abcdef01234567\/197001020346\//);
assert.equal(radarExpected(f,{z:8,x:40,y:80}),true);
const v1={...f};delete v1.mosaicKey;assert.deepEqual(radarFrameLayers(v1),f.siteScans);
assert.notEqual(radarFrameKey(f),radarFrameKey({...f,mosaicKey:'M1123456789abcdef01234567'}));
''')


def test_new_qc_identity_replaces_only_that_staged_plate():
    run_page(r'''
const r=manifest('b');r.native=true;r.tiles.variant='native';
r.tiles.frames.forEach((f,i)=>f.mosaicKey='M'+String(i).padStart(24,'0'));
renderRadar({radar:r,ts:100900});
const held=radarView.pendingSource.frames.map(decode),oldBitmap=held[0].bitmap,next=structuredClone(r);
next.tiles.frames[0].mosaicKey='M123456789abcdef012345678';
renderRadar({radar:next,ts:100901});
assert.equal(oldBitmap.closes,1);
assert.ok(held.slice(1).every(f=>radarView.pendingSource.frames.includes(f)&&f.bitmap.closes===0));
radarAcceptSource();assert.equal(radarView.pendingSource,null);
assert.equal(radarView.data.native,true);
''')


def test_caption_uses_newest_qc_and_names_partial_failure():
    controls(r'''
Object.assign(radarView.data,{native:true,sourceMode:'site',sourceId:'iem-nexrad-n0b',siteId:'KATX',sites:[{id:'KATX',contributing:true},{id:'KLGX',contributing:true}]});
const newest={siteScans:[{id:'KATX'},{id:'KLGX'}],unfilteredSites:['KLGX']};
radarView.data.tiles.frames=[newest];radarSourceRender();assert.match(caption(),/· KLGX unfiltered/);
newest.unfilteredSites=['KATX','KLGX'];radarSourceRender();assert.match(caption(),/· unfiltered/);assert.ok(!caption().includes('KLGX unfiltered'));
newest.unfilteredSites=[];radarSourceRender();assert.ok(!caption().includes('unfiltered'));
''')
