# ROBOPORT Launch Pad

Six-section operator shell — **Launch Pad · Missions · Fleet · Evidence · Approvals · Settings** —
implemented from the Claude Design handoff (`Roboport Launch Pad.dc.html`).

Everything runs on **labelled demo fixtures**. No gateway calls are made, no command buttons
mutate anything, and every unavailable action is rendered disabled with the exact reason.

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

Replace the imports from `fixtures.js` with gateway reads (same shapes), and wire the disabled
buttons to `POST /api/command` once the gateway enforces auth, approvals and re-checks at dispatch.
Keep the DEMO badge driven by the active mode — demo must never render as live.

## Known gaps vs. the design

- Font is loaded from Google Fonts (the design notes the spec wants bundled assets). Falls back to
  `ui-monospace` offline.
- The console loads React from unpkg; Topology needs network access to that CDN.

The Fleet iframe loads the console with `?embedded=1`, which trims the console's own chrome
(brand subtitle, global key handling while hidden) to fit inside the shell.
