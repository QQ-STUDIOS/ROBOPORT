# ROBOPORT Launch Pad

Six-section operator shell — **Launch Pad · Missions · Fleet · Evidence · Approvals · Settings** —
implemented from the Claude Design handoff (`Roboport Launch Pad.dc.html`).

Everything runs on **labelled demo fixtures**. No gateway calls are made, no command buttons
mutate anything, and every unavailable action is rendered disabled with the exact reason.

This static demo is separate from the live, authenticated Launch Pad at `dashboard/launchpad.html`,
served by `bridge.py`. The Python files in this directory implement that live gateway;
the demo does not import or invoke them.

## Run

No build step, no dependencies. Serve the `dashboard/` folder (not `launchpad/` — the Fleet tab
loads `../Roboport Ops Console.dc.html`):

```bash
python -m http.server 5500 --directory dashboard
# open http://localhost:5500/launchpad/
```

ES modules need `http://`; opening `index.html` via `file://` will not load `app.js`.

Deep links: `#missions`, `#fleet`, … or `?section=evidence`. Browser back/forward work.

## Files

| File | Purpose |
|---|---|
| `index.html` | Static shell: header (brand, nav slot, environment pill, DEMO badge), `<main>`, footer, toast |
| `launchpad.css` | Design tokens (`:root`) and component classes, values taken 1:1 from the design |
| `fixtures.js` | All demo data — environment, readiness, missions, fleet roster, evidence, approvals, capabilities |
| `app.js` | State, hash routing, per-section renderers, draft store, manifest digest, responsive Fleet logic |

## Behaviour

- **Mission draft** edits update the launch plan, blocker list and reviewer requirement live.
  `Restricted` / `Regulated` classification adds the independent-reviewer blocker.
- **Save draft** writes `rbp.launchpad.draft.v1` to `localStorage` (works offline) and is restored on load.
- **Preview digest** is FNV-1a over the pretty-printed manifest JSON — a change indicator only.
  The design is explicit that the real sha256 is assigned by the gateway at dispatch.
- **Start when ready** is always disabled in demo mode; the missing requirements are listed beneath it.
- **Fleet** embeds the Ops Console in an iframe (`?embedded=1`, Topology) with a Table fallback. Below 720 px the
  table is forced and Topology is disabled; widening restores Topology.

## Swapping in a gateway

Use the existing `dashboard/launchpad.html` and `assets/launchpad.js` implementation for
gateway-backed operation. Its authenticated routes live under `/api/launchpad/`; the demo
is a design reference and must keep its DEMO badge and disabled command actions.

## Known gaps vs. the design

- Font is loaded from Google Fonts (the design notes the spec wants bundled assets). Falls back to
  `ui-monospace` offline.
- The console loads React from unpkg; Topology needs network access to that CDN.

The Fleet iframe loads the console with `?embedded=1`, which trims the console's own chrome
(brand subtitle, global key handling while hidden) to fit inside the shell.

The Fleet iframe runs with scripts and downloads enabled but without same-origin access.
It cannot read the shell's saved drafts. Tab and Shift+Tab retain normal focus navigation.
Replay handoff and Settings detail views remain disabled until implemented.
