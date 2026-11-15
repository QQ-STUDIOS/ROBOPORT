"""Live snapshots from bridge.py carry sparse station configs; the console must
render them, and the DAG view must not depend on the mock backend's scope."""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SOURCES = [REPO / 'dashboard' / 'Roboport Ops Console.dc.html', REPO / 'dashboard' / 'assets' / 'opsconsole.js']


@pytest.mark.parametrize('source', SOURCES, ids=lambda p: p.name)
def test_crew_edges_live_outside_mock_backend(source):
    src = source.read_text(encoding='utf-8')
    edges = src.index('const CREW_EDGES=[')
    mock = src.index('function createMockBackend(){')
    assert edges < mock, 'drawDAG reads CREW_EDGES, so it cannot be scoped inside createMockBackend'


@pytest.mark.parametrize('source', SOURCES, ids=lambda p: p.name)
def test_hydrate_fills_display_fields_for_sparse_live_config(source, tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for console behavior checks')
    driver = tmp_path / 'hydrate.cjs'
    driver.write_text(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync(process.argv[2],'utf8');
function fn(name){return src.match(new RegExp('  function '+name+'\\([^]*?\\n  \\}'))[0];}
function line(prefix){const i=src.indexOf(prefix);assert(i>=0,prefix);return src.slice(i,src.indexOf('\n',i));}
const ctx={M:{stations:new Map(),agents:new Map(),tasks:new Map()},resetSnapshotEvidence(){},positionStations(){},
  addClientAgent(){},populateInjector(){},renderStationLoadShell(){},prevDone:0};
vm.createContext(ctx);
vm.runInContext(line('  const STATION_HUES=')+'\n'+line('  function normalizeStation(')+'\n'+fn('hydrate'),ctx);
// Shape of bridge.py's snapshot: config stations have only id/order/wave, and a
// station update may name a station outside the config.
ctx.hydrate({config:{stations:[{station_id:'job_scout',order:0,wave:0},{station_id:'custom_step',order:1,wave:1},{station_id:'constructor',order:2,wave:2},{station_id:'__proto__',order:3,wave:3}]},
  stations:[{station_id:'job_scout',state:'working',rev:3},{station_id:'unknown',state:'idle',rev:1}],
  agents:[],tasks:[],metrics:{completed_total:0}});
const a=ctx.M.stations.get('job_scout'),b=ctx.M.stations.get('custom_step');
assert.equal(a.hue,'#7ea6ff');assert.equal(a.name,'job_scout');assert.equal(a.state,'working');
assert.equal(b.hue,'#8a9da8');assert.equal(b.label,'custom_step');assert.deepEqual([...b.sources],[]);
assert(!ctx.M.stations.has('unknown'));
// Station IDs that name Object.prototype members must not pick up inherited values as hues.
assert.equal(ctx.M.stations.get('constructor').hue,'#8a9da8');assert.equal(ctx.M.stations.get('__proto__').hue,'#8a9da8');
assert(ctx.M.config.stations.every(s=>typeof s.hue==='string'));
''', encoding='utf-8')
    subprocess.run([node, str(driver), str(source)], check=True, cwd=REPO)
