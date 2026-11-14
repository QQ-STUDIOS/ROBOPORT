"""Live subscriptions must replay server evidence, never fabricated snapshots."""
import importlib.util
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_late_watch_client_gets_history_before_new_events(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / 'dashboard'))
    spec = importlib.util.spec_from_file_location('live_contract_serve', REPO / 'dashboard/serve.py')
    serve = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = serve
    spec.loader.exec_module(serve)
    try:
        serve._broadcast({'v': 1, 'seq': 10, 'type': 'snapshot', 'data': {'agents': [{'agent_id': 'real'}]}})
        serve._broadcast({'v': 1, 'seq': 14, 'type': 'log.appended', 'data': {'html': 'past'}})
        late = io.BytesIO()
        serve._add_client(late)
        serve._broadcast({'v': 1, 'seq': 15, 'type': 'log.appended', 'data': {'html': 'new'}})
        events = [json.loads(line[6:]) for line in late.getvalue().decode().splitlines() if line.startswith('data: ')]
        assert [e['seq'] for e in events] == [10, 14, 15]
        assert events[0]['data']['agents'] == [{'agent_id': 'real'}]
    finally:
        sys.modules.pop(spec.name, None)


def test_adapter_keeps_one_stream_and_resyncs_from_server_evidence(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for adapter integration checks')
    driver = tmp_path / 'live.cjs'
    driver.write_text(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const streams=[];
const ctx={window:{},document:{addEventListener(){}},location:{search:''},URLSearchParams,
 EventSource:class{constructor(url){this.url=url;streams.push(this);}close(){this.closed=true;}},console};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(process.argv[2]+'/dashboard/feed_adapter.js','utf8'),ctx);
const src=fs.readFileSync(process.argv[2]+'/dashboard/Roboport Ops Console.dc.html','utf8');
vm.runInContext(src.match(/function createFeed\(backend\)\{[\s\S]*?\n  \}/)[0],ctx);
const backend=ctx.window.ROBOPORT_ADAPTER({apiUrl:'http://localhost:4242'});
const feed=ctx.createFeed(backend),seen=[];feed.onEnvelope(e=>seen.push(e));feed.connect();
assert.equal(streams.length,1);assert.equal(seen.length,0,'no client fixture snapshot in live mode');
for(const [seq,type,data] of [[100,'snapshot',{agents:[{agent_id:'real'}]}],[104,'agent.updated',{agent_id:'real'}],[110,'metrics.tick',{}]])
 streams[0].onmessage({data:JSON.stringify({v:1,seq,type,data})});
assert.deepEqual(seen.map(e=>e.seq),[1,2,3]);assert.equal(streams.length,1);
assert.equal(seen[0].data.agents[0].agent_id,'real');
feed.connect();assert.equal(streams.length,2);assert(streams[0].closed);assert.equal(seen.length,3);
streams[0].onmessage({data:JSON.stringify({v:1,seq:111,type:'snapshot',data:{agents:[{agent_id:'stale'}]}})});
assert.equal(seen.length,3);
streams[1].onmessage({data:JSON.stringify({v:1,seq:200,type:'snapshot',data:{agents:[{agent_id:'server-resync'}]}})});
assert.equal(seen.at(-1).data.agents[0].agent_id,'server-resync');
''', encoding='utf-8')
    result = subprocess.run([node, str(driver), str(REPO)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
