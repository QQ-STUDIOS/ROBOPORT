"""Translate the Claude Design "Roboport Ops Console.dc.html" into station files.

Usage: python -I scripts/build_opsconsole.py <design .dc.html> dashboard

Outputs (in the station repo's dashboard/):
  opsconsole.html          static markup, no inline script, no external fonts
  assets/opsconsole.css    the design's stylesheet plus read-only rules
  assets/opsconsole.js     the design's logic class, run without the dc-runtime,
                           fed only by the Launch Pad over postMessage
"""
import re
import sys
from pathlib import Path

src = Path(sys.argv[1]).read_text(encoding="utf-8")
out = Path(sys.argv[2])

def one(pattern, text, flags=re.S):
    m = list(re.finditer(pattern, text, flags))
    assert len(m) == 1, (pattern, len(m))
    return m[0]

# Editor-injected blocks are not part of the design.
src = re.sub(r"<style data-omelette-injected>.*?</style>", "", src, flags=re.S)
src = re.sub(r"<script data-omelette-injected>.*?</script>", "", src, flags=re.S)

helmet = one(r"<helmet>(.*?)</helmet>", src).group(1)
css = one(r"<style>(.*?)</style>", helmet).group(1)
assert "fonts.googleapis" not in css
markup = one(r"</helmet>(.*?)</x-dc>", src).group(1).strip("\n")
logic = one(r'<script type="text/x-dc" data-dc-script[^>]*>(.*?)</script>', src).group(1)

# Standalone design pages support URL-selected demo/live backends. The generated
# station page gets its props and observations only from the gateway host.
logic = logic.replace(
    'Object.assign({},this.props||{},new URLSearchParams(location.search).get("embedded")==="1"?{embedded:true}:{})',
    'this.props||{}',
)
logic, seams = re.subn(
    r'  const _q=new URLSearchParams\(location.search\),_api=_q.get\("api"\);.*?: createMockBackend\(\);',
    '  const backend=createMockBackend();', logic, flags=re.S,
)
assert seams == 1, seams
logic = re.sub(r'  // BEGIN standalone-readonly\n.*?  // END standalone-readonly\n', '', logic, flags=re.S)
logic = logic.replace('  // ?embedded=1 lets a host page (dashboard/launchpad/) iframe the console in embedded mode.\n', '')
logic = re.sub(r'  /\* Live feed seam \(dashboard/feed_adapter.js\):.*?\*/\n', '', logic, flags=re.S)

# ---- markup -------------------------------------------------------------
assert markup.count("feed · mock") == 1
markup = markup.replace("feed · mock", "feed · gateway")
assert markup.count("wire · /api/feed") == 1
markup = markup.replace("wire · /api/feed", "wire · launch pad")
assert "http://" not in markup and "https://" not in markup

# ---- logic --------------------------------------------------------------
def sub(old, new, text):
    assert text.count(old) == 1, (old[:60], text.count(old))
    return text.replace(old, new)

logic = sub(
    'if(PROPS.embedded){const r0=document.getElementById("rbp");if(r0)r0.classList.add("embedded");}',
    'if(PROPS.embedded){const r0=document.getElementById("rbp");if(r0)r0.classList.add("embedded","readonly");}\n'
    '  const FEED_LABEL=PROPS.embedded?"feed · gateway":"feed · mock";',
    logic,
)
logic = sub('el("feed-status").textContent="feed · mock";', 'el("feed-status").textContent=FEED_LABEL;', logic)

GATEWAY_BACKEND = r'''
  /* ── GATEWAY BACKEND (Roboport station) ──────────────────────────────
     The station never runs the design's mock backend. Envelopes come only
     from the Launch Pad that embeds this page, which derives them from
     gateway observations. Commands are rejected: this view owns no
     authority; missions and approvals go through the Launch Pad. */
  function createGatewayBackend(){
    let subs=[];
    const CHANNEL="roboport-ops-console";
    const embedded=window.parent!==window;
    const deliver=env=>{for(const s of subs)s(env);};
    const empty={config:{stations:[],energy_low_threshold:0,max_agents:0,token_budget_default:60000},
      agents:[],stations:[],tasks:[],alerts:[],
      metrics:{tasks_per_min:0,completed_total:0,failed_total:0,success_rate:1,p95_ms:0,active_agents:0,total_agents:0,queued:0,uptime_s:0}};
    window.addEventListener("message",ev=>{
      if(!embedded||ev.source!==window.parent)return;
      const m=ev.data;
      if(!m||m.channel!==CHANNEL||!Array.isArray(m.envelopes))return;
      for(const env of m.envelopes)if(env&&typeof env.type==="string"&&typeof env.seq==="number")deliver(env);
    });
    let primed=false;
    return{
      subscribe:cb=>{subs.push(cb);return()=>{subs=subs.filter(x=>x!==cb);};},
      getSnapshot(){
        if(!primed){primed=true;deliver({v:1,seq:0,ts:new Date().toISOString(),type:"snapshot",data:empty});}
        if(embedded)window.parent.postMessage({channel:CHANNEL,type:"resync"},"*");
      },
      tick(){},
      handleCommand:cmd=>({command_id:cmd.command_id,status:"rejected",reason:"read-only view · use Launch Pad missions"}),
    };
  }

  /* ── FEED CLIENT'''
logic = sub("\n  /* ── FEED CLIENT", GATEWAY_BACKEND, logic)
logic = sub(
    "const backend=createMockBackend();",
    "const backend=PROPS.embedded?createGatewayBackend():createMockBackend();",
    logic,
)
# The canvas badge and the energy counter describe the mock backend.
logic = sub('const txt=replay?"REPLAY · recorded frames":"DEMO · simulated backend",col=replay?T.dispatch:T.work;',
            'const txt=replay?"REPLAY · recorded frames":(PROPS.embedded?"GATEWAY · observed agents":"DEMO · simulated backend"),col=replay?T.dispatch:(PROPS.embedded?T.return:T.work);',
            logic)
low_cell = """'<div class="sc"><span class="d" style="color:'+T.alert+'"></span><span class="n">'+low+'</span><span class="l">low cell</span></div>'"""
logic = sub(low_cell, "(PROPS.embedded?'':" + low_cell + ")", logic)
energy_field = """<div class="f"><div class="k">energy</div><div class="v">'+Math.round(a.energy)+'%</div></div>"""
logic = sub(energy_field, "'+(PROPS.embedded?'':'" + energy_field + "')+'", logic)
# The DAG grid only knows the JD crew; place any other observed step by its wave.
logic = sub("const g=GRID[sid];if(!g)return null;",
            "let g=GRID[sid];if(!g){const st=M.stations.get(sid);if(!st)return null;const wv=s=>Math.max(0,Math.min(4,s.wave|0));"
            "const same=[...M.stations.values()].filter(x=>wv(x)===wv(st)).sort((a,b)=>a.order-b.order);"
            "g=[wv(st),Math.max(0,same.findIndex(x=>x.station_id===sid)),same.length];}",
            logic)
# The hover card's idle reading is the mock's battery charge; the station has
# no energy, progress or token measurement, so embedded it shows the phase only.
logic = sub(
    ':a.energy/100;\n      const pl=a.state==="working"?"task":a.state==="dispatched"?"transit":a.state==="returning"?"return":"charge";',
    ':(PROPS.embedded?0:a.energy/100);\n      const pl=a.state==="working"?"task":a.state==="dispatched"?"transit":a.state==="returning"?"return":(PROPS.embedded?"docked":"charge");\n'
    '      const measured=!PROPS.embedded||a.state==="dispatched"||a.state==="returning";',
    logic,
)
logic = sub(
    'ctx.fillText(pl+" "+Math.round((frac||0)*100)+"%",x+10,y+59);',
    'ctx.fillText(measured?pl+" "+Math.round((frac||0)*100)+"%":pl,x+10,y+59);',
    logic,
)
logic = sub(
    'ctx.fillText("ctx "+tokPct+"%",x+w-10,y+59);',
    'if(!PROPS.embedded)ctx.fillText("ctx "+tokPct+"%",x+w-10,y+59);',
    logic,
)
# A snapshot replaces the console's state; carry the gateway's open incidents
# (failed steps) into the incident log so a reload or resync keeps them.
logic = sub(
    "    populateInjector();renderStationLoadShell();\n  }",
    "    if(PROPS.embedded&&Array.isArray(d.alerts)){incidents.length=0;for(const al of d.alerts)pushIncident(al);incDirty=true;}\n"
    "    populateInjector();renderStationLoadShell();\n  }",
    logic,
)
# The design's proving-ground panel names a real SharePoint tenant; ship a placeholder.
logic, tenants = re.subn(r"[a-z0-9-]+\.sharepoint\.com", "contoso.sharepoint.com", logic)
assert tenants == 1, tenants
# CREW_EDGES lives inside the mock backend; the gateway emits no dependency edges.
logic = sub("for(const edge of CREW_EDGES){",
            "for(const edge of (typeof CREW_EDGES!==\"undefined\"?CREW_EDGES:[])){",
            logic)
# Dropping run files would replay fixtures as if they were observations.
logic = sub(
    "canvas.addEventListener('drop',e=>{e.preventDefault();canvas.style.outline='';",
    "canvas.addEventListener('drop',e=>{e.preventDefault();canvas.style.outline='';if(PROPS.embedded)return;",
    logic,
)
logic = sub(
    "document.getElementById('run-file-input').addEventListener('change',e=>{",
    "document.getElementById('run-file-input').addEventListener('change',e=>{if(PROPS.embedded){e.target.value='';return;}",
    logic,
)
# The ?api= SSE mode would connect to an arbitrary endpoint; the gateway feed replaces it.
live = one(r"\n  // SSE live mode: \?api=.*?fsEl\.textContent='feed · live \('\+API_BASE\+'\)';\n  \}\n", logic)
logic = logic[: live.start()] + "\n" + logic[live.end():]
assert "EventSource" not in logic and "fetch(" not in logic
assert "location.search" not in logic

js = (
    "/* Roboport Ops Console, translated for the station from the Claude Design\n"
    "   project 7b518148 (\"Roboport Ops Console.dc.html\", 2026-10-12).\n"
    "   Generated by build_opsconsole.py: the design's logic class runs without the\n"
    "   dc-runtime (no eval, no CDN); the mock backend is replaced by the gateway\n"
    "   backend when embedded. See dashboard/README.md. */\n"
    "(function () {\n"
    "class DCLogic { constructor(props) { this.props = props || {}; } }\n"
    + logic.strip("\n")
    + "\nconst props = { embedded: true, initialAgents: 5, autoEnqueue: false, showWire: true };\n"
    "function start() { new Component(props).componentDidMount(); }\n"
    "if (document.readyState === \"loading\") document.addEventListener(\"DOMContentLoaded\", start);\n"
    "else start();\n"
    "})();\n"
)

READONLY_CSS = """
  /* ── Station read-only embedding ── */
  #rbp.readonly #btn-agent,#rbp.readonly .injector,#rbp.readonly #insp-actions,
  #rbp.readonly #run-file-input,#rbp.readonly label[for="run-file-input"],
  #rbp.readonly .legend .it:last-child,#rbp.readonly #roster .agent .cell,
  #rbp.readonly .insp-bar,
  /* APG is a static design panel (fixed counts, named tenant), not an observation. */
  #rbp.readonly .vt[data-view="apg"]{display:none!important}
"""

html = (
    "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\" />\n"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n"
    "<title>Roboport · Ops Console</title>\n"
    "<link rel=\"stylesheet\" href=\"/assets/opsconsole.css\" />\n"
    "<script src=\"/assets/opsconsole.js\" defer></script>\n"
    "</head>\n<body>\n" + markup + "\n</body>\n</html>\n"
)

(out / "assets").mkdir(parents=True, exist_ok=True)
(out / "opsconsole.html").write_text(html, encoding="utf-8", newline="\n")
(out / "assets" / "opsconsole.css").write_text(
    "/* Roboport Ops Console stylesheet, from the Claude Design project 7b518148. */\n"
    + css.strip("\n") + "\n" + READONLY_CSS, encoding="utf-8", newline="\n")
(out / "assets" / "opsconsole.js").write_text(js, encoding="utf-8", newline="\n")
print("ok", len(html), len(css), len(js))
