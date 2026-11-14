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
events.message({source:frameWindow,data:{type:'__dc_booted'}});assert(!timers.has(nextTimer));
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
const ctx={window:{parent:{}},document:{activeElement:{tagName:'BUTTON'},getElementById:()=>({offsetParent:{}})},PROPS:{embedded:true}};
vm.createContext(ctx);vm.runInContext(handler,ctx);
for(const shiftKey of [false,true]){
 let prevented=false;
 ctx.onKey({key:'Tab',shiftKey,target:{tagName:'BUTTON'},preventDefault(){prevented=true;}});
 assert(!prevented);
}
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
for(const embedded of [false,true]){
 const button={disabled:false}, backend={handleCommand:()=>({status:'accepted'})};
 const w={};w.parent=embedded?{}:w;
 const ctx={window:w,backend,document:{getElementById:()=>({querySelectorAll:()=>[button]})},MutationObserver:class{observe(){}}};
 vm.runInNewContext(code,ctx);
 assert.equal(backend.handleCommand({command_id:'c1'}).status,embedded?'rejected':'accepted');
 assert.equal(button.disabled,embedded);
}
''', encoding='utf-8')
    subprocess.run([node, str(driver), str(REPO / 'dashboard/Roboport Ops Console.dc.html')],
                   check=True, capture_output=True, text=True)
