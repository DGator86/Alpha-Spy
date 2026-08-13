# Alpha-SPY Workstation

The operator front end for the Alpha-SPY dashboard: a React + TypeScript single-page
application that compiles into `src/alpha_spy/dashboard/static/` and is served by the
existing FastAPI app.

## Build

```sh
make frontend-deps    # npm ci
make frontend         # build into src/alpha_spy/dashboard/static/
```

**The compiled bundle is committed.** The wheel ships pre-built assets so a trading VPS
never needs Node installed. After changing anything under `frontend/`, run `make frontend`
and commit the regenerated `static/` output in the same change — CI rebuilds and fails if
the committed bundle does not match the committed sources.

## Develop

```sh
# Terminal 1 — dashboard with synthetic data
DASHBOARD_MODE=demo DASHBOARD_DB=/tmp/alpha-spy-demo.sqlite \
  python -m uvicorn alpha_spy.dashboard.app:app --port 8788

# Terminal 2 — Vite dev server, proxying /api and /ws to the dashboard
cd frontend && npm run dev
```

Demo mode generates a full synthetic state — seven forecast horizons, a regime hierarchy,
a five-structure candidate book, a decision gate ladder that alternates between trading and
standing down, and a thirty-gate validation run with two failures — so every panel is
reachable without a live session. Point at a real dashboard with
`ALPHA_SPY_DASHBOARD=http://host:port npm run dev`.

## Deploying elsewhere

Served by FastAPI on the trading host the API is same-origin and needs no
configuration. Hosted anywhere else — Vercel, a CDN — set the backend origin at
build time and allow that origin through CORS on the dashboard:

```sh
VITE_API_ORIGIN=https://dashboard.example.com npm run build:vercel
# and on the VPS: DASHBOARD_ALLOWED_ORIGINS=https://alpha-spy.vercel.app
```

The engine itself cannot be hosted on a serverless platform — it is a stateful
service with a local SQLite journal and a persistent websocket. See
`docs/BUILD_AND_DEPLOY.md`.

## Stack

| Concern | Choice |
| --- | --- |
| Framework | React 19, TypeScript, Vite |
| Styling | Tailwind CSS v4 (`@theme` tokens in `src/index.css`) |
| State | Zustand (`src/store/workstation.ts`) |
| Routing | React Router |
| Price chart | Lightweight Charts, with a custom series for the forecast cone |
| Analytical charts | ECharts |
| Primitives | Radix (dialog, tooltip) + local `components/ui/primitives.tsx` |

## Layout

```
src/
  store/workstation.ts        websocket client, section merge, command API
  lib/types.ts                wire types mirroring build_dashboard_state
  lib/format.ts               every number formatter; absent values render as —
  components/ui/              Panel, Chip, Metric, Bar, Row, Empty, Button, table parts
  components/chrome/          Sidebar, TopBar
  components/charts/          SpyChart, forecastConeSeries, PayoffChart, EChart
  components/panels/          Decision, HorizonRibbon, RegimeCockpit, Internals,
                              Validation, Position, System
  screens/                    one file per navigation group
  nav.ts                      sidebar structure; route paths live here
```

## Conventions

**Everything is nullable.** The engine publishes a partial state before the first snapshot
matures. Formatters in `lib/format.ts` return `—` for absent values rather than `NaN`, and
`Empty` names the exact state field a panel is waiting on so a blank panel reads as a
diagnosis rather than a bug.

**Three reserved colours.** Green, amber and red only ever mean pass, watch and fail —
never decoration. Neutral chrome uses the slate ramp; anything the model produced uses
cyan, so "the machine thinks this" stays visually distinct from "this is what happened".

**Derived values are labelled.** Panels that compute something client-side from published
state — the live thesis checks, the running mean on the price chart — say so, rather than
implying the engine asserted it.

**Numbers are tabular.** Any live figure carries `.tnum` so digits do not jitter on a tick.

## Websocket protocol

The socket sends `snapshot` once, then `patch` frames carrying only changed sections, and
`heartbeat` when nothing changed. `mergeSections` replaces each section wholesale. See
`docs/ARCHITECTURE.md` for the section list and rationale.
