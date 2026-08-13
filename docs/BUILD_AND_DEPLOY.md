# Build and Deploy

How a change in this repository becomes a running suite on the VPS.

```text
repository  ──make verify──►  wheel + archives  ──make release──►  dist/release/
     │                                                                   │
     │  push / PR                                                        │  tag vX.Y.Z
     ▼                                                                   ▼
  CI workflow                                                    Release workflow
  (static, tests, package, smoke)                                (GitHub Release + checksums)
     │                                                                   │
     └──────────────────────► Deploy workflow ◄──────────────────────────┘
                              (manual, gated)
                                     │
                                     ▼
                          scripts/deploy_vps.sh ──ssh──► install.sh on the VPS
```

## Prerequisites

- Python 3.11 or newer
- `make`, `tar`, `gzip`, `zip`, `sha256sum`
- `node` (dashboard JavaScript syntax check) and `systemd-analyze` (unit
  verification) — both optional locally; `make lint` skips them with a notice
  and CI runs them unconditionally

```bash
make venv
source .venv/bin/activate
```

`make venv` installs the `dev` extra, which includes `tabulate`. The research
drivers under `examples/` need it for the markdown reports they write; to
install it without the rest of the dev tooling, use `pip install -e '.[research]'`.
The installed runtime under `src/` does not need it, so it stays off the VPS.

## Local build

| Command | What it does |
|---|---|
| `make lint` | Compiles `src/`, `tests/`, `examples/`; `ruff check`; `bash -n` over the installer and operator scripts; `node --check` on the dashboard JavaScript; `systemd-analyze verify` on every unit and timer |
| `make test` | Runs the test suite (`PYTHONPATH=src:. pytest -q`) |
| `make build` | Builds the application wheel into `dist/` |
| `make smoke` | Installs the built wheel into a throwaway virtualenv and exercises it end to end |
| `make verify` | All four, in order |
| `make release` | Builds the distributable archives into `dist/release/` |
| `make verify-release` | Re-checks the built archives against their checksums and manifest |

### What the smoke test covers

`scripts/smoke_test.sh` runs against the *installed wheel*, not the source
tree, so it catches packaging mistakes the test suite cannot. It is fully
hermetic — no Tradier credentials and no network. The market collector runs in
demo mode (`market.demo_when_unconfigured`) and the constituent universe is
read from the bundled `config/universe.csv` rather than the iShares endpoint.

It verifies configuration load, `PRAGMA quick_check`, universe loading, a demo
market cycle, a feature/prediction/decision cycle, confirmation maturity,
dashboard state assembly, both API servers starting, and the dashboard's token
separation: no token → 401, view token → 200, view token on an administrative
command → 403, admin token → 200, and the queued command applied by the next
engine cycle.

### Release archives

`scripts/build_release.sh` stages an explicit file list — the explicit supported release layout — and produces:

```text
dist/release/alpha-spy-v<version>.tar.gz  (+ .sha256)
dist/release/alpha-spy-v<version>.zip     (+ .sha256)
dist/release/RELEASE_MANIFEST.sha256
```

Repository-only material (`.github/`, `.gitignore`, `release/`, working notes)
is deliberately excluded. The bundled wheel is placed at `dist/` *inside* the
archive, which is where `scripts/install_vps.sh` looks for it.

Builds are reproducible: entries are sorted, ownership is normalised, and every
timestamp is pinned to `SOURCE_DATE_EPOCH` (defaulting to the HEAD commit
time). Building the same tree twice yields byte-identical archives. Set
`SOURCE_DATE_EPOCH` explicitly to reproduce an older build.

`RELEASE_MANIFEST.sha256` covers every file in the archive except itself.
`scripts/verify_release.sh <archive>` extracts the archive, checks the sidecar
checksum, verifies every file against the manifest, confirms no unhashed files
were added, and asserts the installer's prerequisites are present.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:

| Job | Gate | Contents |
|---|---|---|
| `static` | blocking | Python compilation, shell syntax, dashboard JavaScript, systemd units |
| `test` | blocking | Test suite on Python 3.11 and 3.12 |
| `package` | blocking | `make release`, `make verify-release`, `make smoke`; uploads archives as build artifacts (14 days) |
| `lint` | blocking | `ruff check` over `src`, `tests` and `examples` |

Runners are pinned to `ubuntu-24.04` to match the deployment target.

### The ruff rule set is pinned deliberately

`pyproject.toml` selects an explicit rule set (`E`, `W`, `F`, `I`, `UP`, `B`,
`C4`, `SIM`, `RUF`). This matters: with only `line-length` configured, ruff
applies whatever its current default is — 826 rules as of 0.16 — so a ruff
upgrade would reintroduce findings in a tree that was clean the day before.
Pinning makes the gate stable and upgrades a deliberate act.

Four things are excluded on purpose, because clearing them would mean changing
behaviour rather than tidying it:

| Excluded | Why |
|---|---|
| `E501` line-too-long | 542 findings. The research and strategy modules carry long literal tables and report strings; `line-length` still guides formatters. |
| `BLE001` blind-except, `S110` try-except-pass | The market, universe and backup paths catch broad exceptions on purpose, so a bad feed degrades data health instead of killing a service mid-session. |
| `FURB` | Opinionated rewrites over timestamp parsing in the audit tape. |
| `PYI` | No stub files in this project. |

Two narrower exemptions are configured rather than fixed:

- `B008` is waived for `typer.Argument`/`Option`, `pandas.Timedelta` and
  `ScanSettings` via `extend-immutable-calls`. Each is either framework API or
  immutable — `ScanSettings` is a `@dataclass(frozen=True)` that is only ever
  read — so the shared default instance is intentional, not a latent bug.
- `E402` is waived under `examples/`, where the research drivers put
  `<repo>/src` on `sys.path` before importing the package.

## Publishing a release

1. Set `version` in `pyproject.toml` and update `CHANGELOG.md`.
2. Merge to `main`.
3. Tag and push:

   ```bash
   git tag v3.0.0
   git push origin v3.0.0
   ```

`.github/workflows/release.yml` then re-runs lint, tests, the release build,
archive verification and the smoke test, and publishes a GitHub Release with
both archives, both `.sha256` sidecars and `RELEASE_MANIFEST.sha256`.

The workflow fails if the tag does not match the version in `pyproject.toml`,
so a release's artifacts always match the tag they hang off. Re-running against
an existing tag replaces the assets and rewrites the notes.

Verify a published release before installing it:

```bash
curl -fLO https://github.com/<owner>/<repo>/releases/download/v3.0.0/alpha-spy-v3.0.0.tar.gz
curl -fLO https://github.com/<owner>/<repo>/releases/download/v3.0.0/alpha-spy-v3.0.0.tar.gz.sha256
sha256sum -c alpha-spy-v3.0.0.tar.gz.sha256
```

## Deploying to the VPS

Both paths run the suite's own `install.sh`, which stops the running services,
leaves trading data under `/var/lib/alpha-spy` in place, rebuilds
`/opt/alpha-spy`, installs the wheel into a fresh virtualenv, reinstalls
the systemd units, and restarts everything in the shipped fail-closed posture.

Neither path can enable production trading. That still requires the separate,
deliberate steps in [OPERATIONS.md](OPERATIONS.md) and
[SECURITY.md](SECURITY.md).

### From a workstation

```bash
DEPLOY_HOST=203.0.113.10 make deploy
```

| Variable | Default | Meaning |
|---|---|---|
| `DEPLOY_HOST` | *(required)* | Target hostname or IP |
| `DEPLOY_USER` | `root` | SSH user |
| `DEPLOY_PORT` | `22` | SSH port |
| `DEPLOY_SSH_KEY` | *(agent / ssh config)* | Private key path |
| `DEPLOY_REMOTE_DIR` | `/root` | Staging directory on the target |
| `DEPLOY_ARCHIVE` | *(freshly built)* | Deploy a specific archive instead |
| `DEPLOY_SKIP_BUILD` | `0` | Reuse the newest archive in `dist/release` |
| `DEPLOY_ASSUME_YES` | `0` | Skip the interactive confirmation |

`scripts/deploy_vps.sh` builds the release, verifies it locally, checks the
target has passwordless `sudo` and free space, transfers the archive with its
checksum, verifies it *again* on the target, runs the installer, and prints
`scripts/status.sh` output. It asks for the phrase `DEPLOY` before touching the
running system.

### From GitHub Actions

`.github/workflows/deploy.yml` is `workflow_dispatch` only — never on push,
tag or schedule — and additionally requires typing `DEPLOY` into the
confirmation input.

Configure once:

| Secret | Value |
|---|---|
| `VPS_HOST` | Target hostname or IP |
| `VPS_SSH_KEY` | Private key authorised for the deploy user |
| `VPS_KNOWN_HOSTS` | Output of `ssh-keyscan -p <port> <host>` |

| Variable | Default |
|---|---|
| `VPS_USER` | `root` |
| `VPS_PORT` | `22` |
| `VPS_REMOTE_DIR` | `/root` |

Host-key checking is enforced: an empty `VPS_KNOWN_HOSTS` fails the run rather
than falling back to accepting an unknown key.

The job targets the `production-vps` environment. Add required reviewers under
**Settings → Environments → production-vps** so a deploy needs approval, and
scope the environment to the branches you deploy from.

### After deploying

```bash
sudo /opt/alpha-spy/release/scripts/status.sh
sudo /opt/alpha-spy/release/scripts/doctor.sh
sudo cat /root/alpha-spy-credentials.txt
```

Open the Command Center through an SSH tunnel — the dashboard and decision
APIs bind to `127.0.0.1` and are never exposed directly:

```bash
ssh -L 8788:127.0.0.1:8788 -L 8787:127.0.0.1:8787 root@YOUR_VPS_IP
# then browse to http://127.0.0.1:8788
```

Rolling back is covered in [UPGRADE.md](UPGRADE.md); the installer leaves the
data under `/var/lib/alpha-spy` untouched, so rolling back means installing
the older archive.

## GUI preview

`.github/workflows/pages.yml` publishes the workstation to GitHub Pages on
pushes to `main` that touch `frontend/`. It is a real build of the shipping
application (`npm run build:preview`) with the network layer replaced by a
committed synthetic snapshot, so the published page cannot drift away from the
product the way a separately maintained mock does.

The page is static and self-contained — no live data, no credentials, no
connection to a running instance, operator commands disabled — and the workflow
fails if it ever gains an external resource reference or is built without the
snapshot chunk. Enable it once under
**Settings → Pages → Source: GitHub Actions**.

Regenerate the snapshot with:

```sh
PYTHONPATH=src python3 scripts/generate_preview_snapshot.py
```

It is anchored to a fixed timestamp, so re-running it against an unchanged demo
generator produces an identical file.

## Hosting the workstation on Vercel

The engine cannot run on Vercel. The dashboard is a stateful FastAPI service
with a local SQLite journal, a websocket that streams every second, and
systemd-managed collector/engine/validation units. Vercel functions are
stateless and ephemeral and do not hold long-lived websockets. **Vercel hosts
the workstation UI only; the engine stays on the VPS.**

`vercel.json` at the repository root configures this. It builds
`frontend/` with `BUILD_TARGET=vercel` (root base path, `frontend/dist` output),
rewrites unknown paths to `index.html` for client-side routing, and marks the
deployment `noindex`.

Two things about that file worth knowing before editing it:

- **The rewrite deliberately excludes `api/` and `ws/`** as well as `assets/`.
  Nothing serves those on Vercel — the engine is not there — so they must 404.
  Letting them fall through to `index.html` returns `200 text/html`, which any
  caller that checks only the status code reads as a successful API response.
  That is how a wrong `VITE_API_ORIGIN` turns into "the token was accepted"
  followed by an unexplained reconnect loop.
- **It cannot carry comments.** Vercel validates the file against its published
  schema with `additionalProperties: false`, so an explanatory `_comment` key
  fails the deployment outright. Explanations go here instead.

Two settings make it work:

1. **On Vercel**, set an environment variable so the bundle knows where the API
   lives. Without it every request resolves against the CDN instead of the
   engine:

   ```
   VITE_API_ORIGIN=https://dashboard.example.com
   ```

   It is read at build time, so changing it requires a redeploy. It must be
   `https://` if the Vercel page is served over https — browsers block mixed
   content, and the app deliberately does not rewrite the scheme for you.

2. **On the VPS**, allow the Vercel origin through CORS in
   `/etc/alpha-spy/dashboard.env`:

   ```
   DASHBOARD_ALLOWED_ORIGINS=https://alpha-spy.vercel.app
   ```

   Comma separated for several origins (Vercel preview deployments each get
   their own hostname). Unset means no cross-origin access at all, which is the
   correct default for a loopback deployment. A literal `*` is ignored rather
   than honoured.

The dashboard must also be reachable from the internet over HTTPS for this to
work at all — a reverse proxy with a certificate, or a tunnel. It is
loopback-bound by default.

### What protects it

Publishing the UI does not publish the data: the bundle is a static page that
holds no credentials, and every API call carries an explicit `X-Dashboard-Token`
header the operator types in. CORS is configured `allow_credentials=False` and
exact-origin, so a hostile page cannot ride an ambient session even if it learns
the URL.

That leaves the tokens as the control on the dashboard itself. Set
`DASHBOARD_REQUIRE_VIEW_TOKEN=true` and use distinct view and admin tokens.
Vercel's own Deployment Protection is worth enabling on top, so the page is not
publicly reachable at all.

## Front-end builds

The workstation compiles into `src/alpha_spy/dashboard/static/`, and **the
compiled bundle is committed** so the wheel ships pre-built assets and a trading
VPS never needs a Node toolchain. After changing anything under `frontend/`:

```sh
make frontend-deps    # once, or after dependency changes
make frontend         # rebuild into src/alpha_spy/dashboard/static/
```

Commit the regenerated `static/` output in the same change. CI rebuilds the
bundle and fails if the committed output does not match the committed sources.

There are three build targets, all from the same sources:

| Command | Target | Base | Output | API |
| --- | --- | --- | --- | --- |
| `npm run build` | VPS (shipped in the wheel) | `/static/` | `src/alpha_spy/dashboard/static/` | same origin |
| `npm run build:vercel` | Vercel | `/` | `frontend/dist/` | `VITE_API_ORIGIN` |
| `npm run build:preview` | GitHub Pages demo | `./` | `frontend/dist-preview/` | none — committed snapshot |

Only the first is committed. Leaving `VITE_API_ORIGIN` unset preserves the
original same-origin behaviour exactly, so the VPS bundle is unaffected by the
Vercel support.

## Repository layout

```text
src/            alpha_spy (suite), alpha_spy.research (research), alpha_spy.dashboard (GUI)
tests/          test suite
examples/       research drivers
config/         shipped fail-closed configuration and constituent universe
scripts/        build, verify, smoke, deploy and operator scripts
systemd/        service, timer and target units
docs/           architecture, operations, security, data model, this file
preview/        standalone GUI preview and screenshot
release/        archived upstream release drops (immutable record; not a build target)
dist/           build output — wheel and dist/release/ archives (git-ignored)
```
