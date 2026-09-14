"""View-start latency, retained hour and playback intent regressions."""
import json
import os
from pathlib import Path
import subprocess
import time

import pytest
from lib import almanac_emit as ae
from tests.test_radar_hybrid import hybrid  # noqa: F401
from tests.test_emitter_lifecycle import FakeClock


def test_play_intent_during_buffering():
    html = Path('design/almanac/console_live.html').read_text()
    sync = html[html.index('  function radarLoopSync()'):html.index('  document.addEventListener("visibilitychange"', html.index('  function radarLoopSync()'))]
    script = '''const assert=require('assert'); let ready=[],click, reduced=false;
      const elements={}; const $=id=>elements[id] || (elements[id]={style:{},dataset:{},setAttribute(k,v){this[k]=v},removeAttribute(k){delete this[k]},addEventListener(k,v){click=v},animate(){this.dips=(this.dips||0)+1}});
      const radarView={active:true,data:{observedTs:1},paused:true,loaded:[],current:null};
      const radarReady=()=>ready, radarReduced=()=>reduced, radarFrameLabel=()=>"12:00";
      const radarStopLoop=()=>radarView.timer=null, radarCouldLoop=()=>ready.length>=2;
      const radarDraw=()=>{}, radarLoopStep=()=>{}, requestAnimationFrame=()=>1, RAD_HOLD_MS=1100;
    '''+sync+'''
      radarLoopSync(); assert.equal($('rad-play').disabled,false);
      assert.equal($('rad-play').textContent,'▶');
      click(); assert.equal($('rad-play').textContent,'❙❙');
      assert.equal($('rad-frame-time').textContent,'Buffering · 0 of 8');
      ready=[{hasEcho:true},{hasEcho:true}]; radarLoopSync(); assert(radarView.timer);
      ready=[]; radarLoopSync(); click(); assert.equal($('rad-play').textContent,'▶');
      assert.equal($('rad-frame-time').textContent,'Paused · 0 of 8');
      ready=[{hasEcho:true},{hasEcho:true}]; radarLoopSync(); assert(!radarView.timer);
      assert.equal($('rad-play').dips,2);
      reduced=true; click(); assert(radarView.singleSweep && radarView.timer);
      click(); assert(!radarView.singleSweep && !radarView.timer);
    '''
    subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)


def test_view_transition_one_tick_and_single_flight(make_emitter, hybrid, tmp_path, monkeypatch):
    e=make_emitter(); e._running=True; e._radar_zoom_stamp=e._radar_preference_stamp()
    clock=FakeClock(); calls=[]
    monkeypatch.setattr(e,'_spawn',lambda name,fn: calls.append(fn))
    clock.schedule_interval(e._check_radar_zoom,ae.RADAR_INTENT_CHECK_SEC)
    clock.advance(.1); assert not calls
    hybrid.view(); clock.advance(.1); assert len(calls)==1
    for _ in range(30): hybrid.view(); clock.advance(.1)
    assert len(calls)==1
    (tmp_path/'radar_viewed').write_text(str(ae.time.time()-ae.RADAR_VIEW_TTL))
    clock.advance(.1); e._inflight.add('radar'); hybrid.view(); clock.advance(.1)
    assert len(calls)==1 and e._radar_view_pending
    e._inflight.remove('radar'); clock.advance(.1)
    assert len(calls)==2 and not e._radar_view_pending
    # Real server sessions wake a return inside the 15-minute demand TTL.
    for since in (hybrid.now-600,hybrid.now):
        (tmp_path/'radar_viewing').write_text(json.dumps(dict(since=since,last=hybrid.now)))
        clock.advance(.1)
    assert len(calls)==4
    clock.advance(1); assert len(calls)==4


def test_unviewed_hour_accumulates_and_warm_view_has_zero_http(make_emitter, hybrid, tmp_path, monkeypatch):
    e=make_emitter()
    # Cadence passes fetch only newest; two hours demonstrate the steady bound.
    for i in range(62):
        if i: hybrid.latest+=120; hybrid.mono+=120
        hybrid.calls.clear(); e._do_radar(intent_triggered=False)
        assert e._build_payload()['radar']['completeFrameCount']==1
        tile_calls=[c for c in hybrid.calls if '/mrms::' in c[2]]
        assert len(tile_calls)==len(ae._radar_viewport(47.61,-122.33,e._radar_result.zoom,956,490)[0])
        os.utime(Path(ae.RADAR_DIR)/(e._radar_result.latest+'.png'), (ae.time.time(),)*2)
        assert len(list(Path(ae.RADAR_DIR).rglob('*.png'))) <= 33
    files=list(Path(ae.RADAR_DIR).rglob('*.png'))
    current=[p for p in files if hybrid.latest-3600<=int(p.stem)<=hybrid.latest]
    assert len(current)==31
    hybrid.calls.clear(); hybrid.view(); e._running=True
    start=time.perf_counter(); e._check_radar_zoom()
    deadline=start+1
    while 'radar' in e._inflight and time.perf_counter()<deadline: time.sleep(.001)
    elapsed=(time.perf_counter()-start)*1000
    assert e._build_payload()['radar']['completeFrameCount']>=8
    assert elapsed<300 and not hybrid.calls
    print(f'WARM VIEW: {elapsed:.1f} ms, 31 complete frames, 0 HTTP')
    # A retained off-tab hour receives grace when its geometry is superseded.
    (tmp_path/'radar_viewed').unlink(); e._do_radar(intent_triggered=False)
    (tmp_path/'radar_zoom').write_text('7'); e._do_radar()
    assert all(p.exists() for p in current)
    hybrid.mono+=ae.RADAR_CACHE_GRACE_SEC; e._radar_prune(e._radar_result)
    assert not any(p.exists() for p in current)
    e.stop()


@pytest.mark.parametrize('change',['geometry','legend','age'])
def test_prune_retires_other_identity_and_expired_hour(make_emitter, hybrid, tmp_path, change):
    e=make_emitter();e._do_radar(); previous=e._radar_result
    latest=Path(ae.RADAR_DIR)/(previous.latest+'.png')
    parts=previous.latest.split('/')
    if change=='geometry': parts[2]='abandoned'
    elif change=='legend': parts[3]='previous-legend-revision'
    else: parts[-1]=str(previous.ts_frame-ae.RADAR_HISTORY_SEC-120)
    old=Path(ae.RADAR_DIR)/('/'.join(parts)+'.png'); old.parent.mkdir(parents=True,exist_ok=True)
    old.write_bytes(latest.read_bytes()); os.utime(old,(hybrid.now-1000,)*2)
    e._radar_prune(previous);assert not old.exists() and latest.exists()
    e.stop()


def test_view_start_cannot_republish_a_failed_geometry(make_emitter, hybrid, tmp_path, monkeypatch):
    e=make_emitter();hybrid.view();e._do_radar()
    assert sum(f['complete'] for f in e._radar_result.frames)>=8
    old=e._radar_result
    (tmp_path/'radar_zoom').write_text('7')
    def fail(*args): raise OSError('fixture geometry failure')
    with monkeypatch.context() as m:
        m.setattr(e,'_radar_iem_frames',fail)
        m.setattr(e,'_radar_rainviewer_frames',fail)
        e._do_radar()
    assert e._radar_result is old
    assert e._radar_result_stamp!=e._radar_geometry_stamp
    e._do_radar(view_started=True)
    assert e._radar_result.zoom==7 and e._radar_result is not old
    e.stop()


@pytest.mark.parametrize('damage',['missing','corrupt'])
def test_view_start_requires_a_valid_newest_crop(make_emitter, hybrid, damage):
    e=make_emitter();hybrid.view();e._do_radar()
    # Leave at least eight older cached crops so a count alone cannot pass.
    hybrid.mono+=120;hybrid.latest+=120;hybrid.view();e._do_radar(intent_triggered=False)
    assert sum(f['complete'] for f in e._radar_result.frames)>8
    path=Path(ae.RADAR_DIR)/(e._radar_result.latest+'.png')
    if damage=='missing': path.unlink()
    else: path.write_bytes(b'broken png')
    e._do_radar(view_started=True)
    assert path.exists()
    latest=next(f for f in e._radar_result.frames if f['id']==e._radar_result.latest)
    assert ae._radar_cached_frame(dict(latest,complete=False),str(path))['complete']
    e.stop()
