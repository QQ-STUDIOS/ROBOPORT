"""Run the static demo's renderers and embedded keyboard handler in Node."""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_demo_frames_are_isolated_and_unwired_actions_are_disabled(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for demo behavior checks')
    driver = tmp_path / 'demo.cjs'
    driver.write_text(r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const root = process.argv[2];
const fixtures = fs.readFileSync(root+'/dashboard/launchpad/fixtures.js','utf8').replace(/export /g,'');
const app = fs.readFileSync(root+'/dashboard/launchpad/app.js','utf8').replace(/import \{[\s\S]*?\} from "\.\/fixtures.js";/,'');
const elements = new Map();
global.timers=new Map();global.events={};global.nextTimer=0;global.focused=0;
global.setTimeout=fn=>{timers.set(++nextTimer,fn);return nextTimer;};
global.clearTimeout=id=>timers.delete(id);
global.document = {getElementById(id){if(!elements.has(id))elements.set(id,{addEventListener(){},style:{}});return elements.get(id);}};
document.querySelector=()=>({focus(){focused++;}});
global.media={matches:false,addEventListener(type,fn){events.media=fn;}};
global.window = {matchMedia:()=>media,addEventListener(type,fn){events[type]=fn;},scrollTo(){}};
global.location = {hash:'',search:''};
global.history = {pushState(a,b,hash){location.hash=hash;}};
global.assert = assert;
global.localStorage = {getItem:()=>null};
vm.runInThisContext(fixtures+'\n'+app+`
state.section='fleet';
const fleet=renderFleet();
const frame=fleet.match(/<iframe\\b[^>]*>/)[0];
assert(frame.includes('sandbox="allow-scripts allow-downloads"'));
assert(!frame.includes('allow-same-origin'));
assert(frame.includes('embedded=1'));
const replay=renderEvidence().match(/<button\\b[^>]*>Open replay in Fleet<\\/button>/)[0];
assert(replay.includes('disabled'));
for(const button of renderSettings().matchAll(/<button\\b[^>]*>/g))assert(button[0].includes('disabled'));
assert(renderSettings().replace(/<[^>]*>/g,'').includes('This view is not implemented'));
let paints=0;
Object.defineProperty(document.getElementById('main'),'innerHTML',{set(){paints++;}});
go('fleet');
ACTIONS['fleet-mode']({dataset:{mode:'topology'}});
assert.equal(paints,0,'active Fleet controls must preserve the existing iframe');
assert(renderLaunch().includes('<option disabled>REA investigation'));
document.activeElement={closest:()=>({dataset:{section:'fleet'}})};
state.section='missions';renderNav();assert.equal(focused,1);
state.section='fleet';state.fleetMode='topology';watchFleetBoot();
const failedBoot=timers.get(nextTimer);failedBoot();
assert.equal(state.fleetMode,'table');assert(state.consoleFailed);
assert(renderFleet().includes('Topology could not start'));
state.fleetMode='topology';watchFleetBoot();
const frameWindow={};document.getElementById('fleet-console').contentWindow=frameWindow;
events.message({source:{},data:{type:'__dc_booted'}});assert(timers.has(nextTimer));
events.message({source:frameWindow,data:{type:'__dc_booted'}});assert(timers.has(nextTimer));
events.message({source:frameWindow,data:{type:'roboport-demo-ready'}});assert(!timers.has(nextTimer));
state.consoleFailed=true;media.matches=true;events.media();media.matches=false;events.media();
assert(!state.consoleFailed,'a topology retry must clear the prior failure status');
`);
''', encoding='utf-8')
    subprocess.run([node, str(driver), str(REPO)], check=True, capture_output=True, text=True)


def test_embedded_console_preserves_tab_navigation(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for keyboard behavior checks')
    driver = tmp_path / 'keyboard.cjs'
    driver.write_text(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync(process.argv[2],'utf8');
const handler=src.match(/function onKey\(e\)\{[\s\S]*?\n  \}/)[0];
const ctx={window:{parent:{}},document:{activeElement:{tagName:'BUTTON'},getElementById:()=>({offsetParent:{}})},PROPS:{embedded:true},D:{sr:{}},selected:{type:'agent'}};
vm.createContext(ctx);vm.runInContext(handler,ctx);
for(const shiftKey of [false,true]){
 let prevented=false;
 ctx.onKey({key:'Tab',shiftKey,target:{tagName:'BUTTON'},preventDefault(){prevented=true;}});
 assert(!prevented);
}
for(const key of ['r','h']){
 let prevented=false;ctx.onKey({key,target:{tagName:'BUTTON'},preventDefault(){prevented=true;}});
 assert(prevented);assert(ctx.D.sr.textContent.includes('unavailable'));
}
for(const modifier of ['ctrlKey','metaKey','altKey']){
 let prevented=false;ctx.onKey({key:'r',[modifier]:true,target:{tagName:'BUTTON'},preventDefault(){prevented=true;}});
 assert(!prevented,'browser shortcuts must remain available');
}
let prevented=false;
ctx.onKey({key:' ',code:'Space',target:{tagName:'BUTTON',closest:()=>({})},preventDefault(){prevented=true;}});
assert(!prevented,'native button activation must not pause the simulation');
const oldRow={dataset:{aid:'a1'},closest(){return this;}};
oldRow.innerHTML='old';oldRow.className='agent';oldRow.setAttribute=()=>{};oldRow.nextElementSibling=null;
const newRow={dataset:{aid:'a1'},innerHTML:'updated',className:'agent sel',getAttribute:()=> 'true'};
ctx.document.activeElement=oldRow;
ctx.D.roster={children:[oldRow],firstElementChild:oldRow,scrollTop:40,insertBefore(){throw Error('unchanged rows must not move');}};
ctx.document.createElement=()=>({content:{children:[newRow]},set innerHTML(v){}});
vm.runInContext(src.match(/function replaceRoster\(html\)\{[\s\S]*?\n  \}/)[0],ctx);
ctx.replaceRoster('<div>updated roster</div>');
assert.equal(ctx.D.roster.children[0],oldRow);assert.equal(ctx.document.activeElement,oldRow);
assert.equal(oldRow.innerHTML,'updated');assert.equal(ctx.D.roster.scrollTop,40);
''', encoding='utf-8')
    subprocess.run([node, str(driver), str(REPO / 'dashboard/Roboport Ops Console.dc.html')],
                   check=True, capture_output=True, text=True)


def test_embedded_console_rejects_commands(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for embedded command checks')
    driver = tmp_path / 'readonly.cjs'
    driver.write_text(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync(process.argv[2],'utf8');
const code=src.match(/\/\/ BEGIN standalone-readonly([\s\S]*?)\/\/ END standalone-readonly/)[1];
const rejectCapture=src.match(/function rejectEmbeddedImport\(e\)\{[\s\S]*?\n  \}/)[0];
for(const embedded of [false,true]){
 const button={disabled:false,style:{}}, backend={handleCommand:()=>({status:'accepted'})};
 const w={};w.parent=embedded?{}:w;
 const hint={},label={style:{}},input={closest:()=>label},captures=[];
 const root={querySelectorAll:()=>[button],querySelector:selector=>selector==='.hint'?hint:input};
 const ctx={window:w,backend,root,canvas:{style:{}},D:{sr:{}},document:{getElementById:()=>root,addEventListener:(type,fn,capture)=>captures.push({type,fn,capture})},MutationObserver:class{observe(){}}};
 vm.runInNewContext(rejectCapture+'\n'+code,ctx);
 assert.equal(backend.handleCommand({command_id:'c1'}).status,embedded?'rejected':'accepted');
 assert.equal(button.disabled,embedded);
 assert.equal(!!button.hidden,embedded);
 if(embedded){assert(hint.textContent.includes('run imports are unavailable'));assert(input.disabled);assert(label.hidden);}
 if(embedded){let stopped=false;assert(captures[0].capture);captures[0].fn({dataTransfer:{},preventDefault(){},stopImmediatePropagation(){stopped=true;}});assert(stopped,'document-level import handlers must not receive embedded drops');}
}
const change=src.match(/document.getElementById\('run-file-input'\).addEventListener\('change',e=>\{[^\n]+/)[0];
const reject=src.match(/function rejectEmbeddedImport\(e\)\{[\s\S]*?\n  \}/)[0];
const dragover=src.match(/canvas.addEventListener\('dragover',e=>\{[^\n]+/)[0];
for(const embedded of [false,true]){
 let handler,loads=0;const w={};w.parent=embedded?{}:w;
 const hint={},events={},canvas={style:{},addEventListener:(type,fn)=>events[type]=fn};
 const ctx={window:w,document:{getElementById:()=>({addEventListener:(type,fn)=>handler=fn})},loadRunFiles(){loads++;},
 canvas,D:{sr:{}},root:{querySelector:()=>hint}};
 vm.runInNewContext(reject+'\n'+change+'\n'+dragover,ctx);
 handler({target:{files:[{}],value:'file'},preventDefault(){}});assert.equal(loads,embedded?0:1);
 const dataTransfer={};events.dragover({dataTransfer,preventDefault(){}});
 if(embedded){assert.equal(dataTransfer.dropEffect,'none');assert.equal(canvas.style.outline,'');assert(hint.textContent.includes('unavailable'));}
 else assert(canvas.style.outline.includes('dashed'));
}
''', encoding='utf-8')
    subprocess.run([node, str(driver), str(REPO / 'dashboard/Roboport Ops Console.dc.html')],
                   check=True, capture_output=True, text=True)
