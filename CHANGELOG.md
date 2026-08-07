# Changelog

## Unreleased

- Cleared all ruff findings across `src/`, `tests/` and `examples/`, and
  promoted the ruff CI job from advisory to gating.
- Pinned the ruff rule set explicitly in `pyproject.toml`. Previously only
  `line-length` was configured, so ruff applied its full current default
  (826 rules) and any upgrade could reintroduce findings.
- `OptionRight` is now a `StrEnum` rather than `(str, Enum)`. This is a
  behaviour change: `str(OptionRight.CALL)` returns `"C"` instead of
  `"OptionRight.CALL"`, which is what the codebase's
  `str(row.right).startswith("C")` idiom already assumed.
- Removed dead locals in the research drivers, including a `holdout` /
  `holdout_worlds` pair superseded by the `holdout_selected` path, and dropped
  a vestigial always-true report filter (`if line != "" or True`).
- Replaced `zip(bounds[:-1], bounds[1:])` with `itertools.pairwise`, and made
  the remaining `zip()` calls state `strict=` explicitly.

- Integrated the v2.0.0 suite source tree into the repository; the shipped
  release archives are preserved verbatim under `release/v2.0.0/`.
- Added `scripts/build_release.sh`: reproducible tar.gz and zip archives with a
  `RELEASE_MANIFEST.sha256` covering every file.
- Added `scripts/verify_release.sh`: checksum, manifest and installer-prerequisite
  verification for a release archive, runnable on the VPS before installing.
- Added `scripts/smoke_test.sh`: hermetic end-to-end exercise of the built wheel
  covering configuration, database integrity, a demo market and decision cycle,
  both API servers, and dashboard view/admin token separation.
- Added `scripts/deploy_vps.sh` and `make deploy` for SSH deployment.
- Added CI, release, deploy and GUI-preview GitHub Actions workflows.
- Extended the Makefile with `venv`, `smoke`, `release`, `verify-release`,
  `deploy` and `help` targets.
- Added [docs/BUILD_AND_DEPLOY.md](docs/BUILD_AND_DEPLOY.md).

## 2.0.0 — 2026-08-06

- Unified research engine, live runtime, audit tape and Command Center GUI.
- Added one-command Ubuntu VPS installer.
- Added separate v2 databases to preserve prior system data.
- Added point-in-time constituent quote collection.
- Added rotating constituent IV/skew collection and SPY reference surface.
- Added immutable T+15 predictions and formal non-overlapping anchors.
- Added defined-risk strategy generator and deterministic maximum-loss checks.
- Added paper execution and guarded Tradier preview/reprice/cancel workflow.
- Added managed position monitor and broker-confirmed live exits.
- Added historical-data revision checks and counterfactual candidate scoring.
- Added daily 5:00 PM Eastern Google Drive backup.
- Added hardened systemd deployment and separate GUI tokens.
