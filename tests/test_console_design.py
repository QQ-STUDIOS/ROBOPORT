"""Execute standalone console behavior with controlled canvas and network seams."""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_console_modes_apg_layout_and_single_live_connection(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for design behavior checks')
    driver = tmp_path / 'design.cjs'
    driver.write_text(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync(process.argv[2],'utf8');
function fn(name){return src.match(new RegExp('  function '+name+'\\([^]*?\\n  \\}'))[0];}
const badge={replay:false,sourceMode:'demo'};vm.createContext(badge);vm.runInContext(fn('modeBadge'),badge);
for(const [mode,label] of [['demo','DEMO'],['live','LIVE'],['file','FILE'],['import','IMPORTED'],['gateway','GATEWAY']]){
 badge.sourceMode=mode;assert(badge.modeBadge().startsWith(label+' ·'));
}
badge.replay=true;assert(badge.modeBadge().startsWith('REPLAY ·'));
const hit={view:'apg'};vm.createContext(hit);vm.runInContext(fn('hitTest'),hit);
assert.equal(hit.hitTest(10,10),null); // No invisible port state is needed or consulted.
for(const [W,H] of [[900,700],[600,400]]){
 const text=[], operations=[];
 const ctx=new Proxy({}, {get(o,k){if(k==='fillText')return(t,x,y)=>text.push({t,x,y});
 if(k==='scale')return()=>operations.push('scale');
 if(k==='measureText')return()=>({width:20});if(k==='createLinearGradient')return()=>({addColorStop(){}});
 return()=>{};},set(){return true;}});
 const draw={W,H,ctx,T:{},performance:{now:()=>0},roundRect(){},drawGrid(){operations.push('grid');}};
 vm.createContext(draw);vm.runInContext(fn('drawAPGContent')+'\n'+fn('drawAPG'),draw);draw.drawAPG();
 const phase=text.find(x=>x.t==='RAG / Pinecone'), roadmap=text.find(x=>x.t==='PHASE 0 · BASELINE HARDENING');
 assert.equal(operations[0],'grid','clear the full canvas before scaling APG content');
 assert(phase && roadmap && phase.y+20<roadmap.y-14,'pipeline must end before roadmap panel');
}
let adapters=0,sse=0,fetches=0;
const net={sourceMode:'demo',location:{search:'?api=http://localhost:4242'},URLSearchParams,
 window:{ROBOPORT_ADAPTER(){adapters++;return{};}},ResizeObserver:class{observe(){}},canvas:{addEventListener(){}},
 document:{getElementById:()=>({addEventListener(){}})},layout(){},route(){},createMockBackend(){throw Error('wrong backend');},
 createFeed:()=>({onEnvelope(){},connect(){}}),performance:{now:()=>0},requestAnimationFrame(){},setInterval(){},
 EventSource:class{constructor(){sse++;}},fetch(){fetches++;return{then(){return{then(){return{catch(){}}}}}};},
 el:()=>({addEventListener(){}})};
const start=src.indexOf('  new ResizeObserver('),end=src.indexOf('    window.__feed=feed;',start);
net.window.parent=net.window;
const status={};net.el=()=>status;
vm.runInNewContext(fn('feedLabel')+'\n'+fn('updateSourceLabel')+'\n'+src.slice(start,end)+'\nwindow.__feed=feed;',net);
assert.equal(adapters,1);assert.equal(sse,0);assert.equal(fetches,0);
assert.equal(status.textContent,'feed · live');
const unavailable={...net,window:{},document:{getElementById:()=>status}};unavailable.window.parent=unavailable.window;
assert.throws(()=>vm.runInNewContext(fn('feedLabel')+'\n'+fn('updateSourceLabel')+'\n'+src.slice(start,end),unavailable),/adapter unavailable/);
assert(status.textContent.includes('No demo data has been substituted'));
const posted=[];
const ready={readySent:false,step(){throw Error('render failed');},requestAnimationFrame(){},window:{parent:{postMessage:m=>posted.push(m)}}};
vm.createContext(ready);vm.runInContext(src.match(/function signalReady\(\)\{[^\n]+/)[0]+'\n'+src.match(/function rafLoop\(now\)\{[^\n]+/)[0],ready);
assert.throws(()=>ready.rafLoop(0));assert.equal(posted.length,0);
ready.step=()=>{};ready.rafLoop(1);ready.rafLoop(2);
assert.equal(posted.length,1);assert.equal(posted[0].type,'roboport-demo-ready');
ready.readySent=false;posted.length=0;ready.performance={now:()=>0};ready.feed={};
vm.runInContext(src.slice(src.indexOf('    window.__feed=feed;'),src.indexOf('\n  }\n}',src.indexOf('    window.__feed=feed;'))),ready);
assert.equal(posted.length,1,'readiness must not depend on a foreground animation callback');
const evidence={stnThru:new Map(),stnLat:new Map(),agentHistory:new Map(),agentCtx:new Map(),nodeAlerts:new Map(),
 sparks:[1],srcPackets:[1],chainPackets:[1],logEntries:[1],toasts:[1],incidents:[1],hist:{rate:[99],queued:[99]},
 performance:{now:()=>1000},Date,hhmm:()=> 'now'};
evidence.pushIncident=al=>evidence.incidents.unshift(al);evidence.pushNodeAlert=()=>{};
vm.createContext(evidence);vm.runInContext(fn('resetSnapshotEvidence'),evidence);
const snapshot={observed_at_ms:1000000,derived:{throughput_ms:{s:[999900]},latencies:{s:[3]},agent_history:{a:[{task_id:'t'}]}},
 recent_logs:[{html:'server log'}],recent_incidents:[{alert_id:'new'},{alert_id:'old'}],metrics_history:[{tasks_per_min:1,queued:0}]};
evidence.resetSnapshotEvidence(snapshot);evidence.resetSnapshotEvidence(snapshot);
assert.equal(evidence.stnThru.get('s').length,1);assert.equal(evidence.agentHistory.get('a').length,1);
assert.equal(evidence.stnThru.get('s')[0],900,'use server observation time despite browser clock skew');
assert.equal(evidence.logEntries.length,1);assert.equal(evidence.incidents.length,2);assert.equal(evidence.toasts.length,0);
assert.equal(evidence.incidents[0].alert_id,'new');
assert.equal(evidence.hist.rate.length,1);assert.equal(evidence.chainPackets.length,0);
''', encoding='utf-8')
    result = subprocess.run([node, str(driver), str(REPO / 'dashboard/Roboport Ops Console.dc.html')],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
