# Archived release drops

Published release artifacts, kept verbatim as an immutable record of what was
shipped. Nothing here is a build target — `scripts/build_release.sh` writes to
`dist/release/` and never touches this directory.

```text
v2.0.0/
  spy-constituent-alpha-suite-v2.0.0.tar.gz     (+ .sha256)
  spy-constituent-alpha-suite-v2.0.0.zip        (+ .sha256)
  RELEASE_MANIFEST.sha256                        hashes of every file inside
```

The tar.gz and zip carry byte-identical trees. To check one:

```bash
cd release/v2.0.0
sha256sum -c spy-constituent-alpha-suite-v2.0.0.tar.gz.sha256
bash ../../scripts/verify_release.sh spy-constituent-alpha-suite-v2.0.0.tar.gz
```

v2.0.0 predates the build integration, so its archives do not contain
`scripts/build_release.sh`, `scripts/verify_release.sh`,
`scripts/smoke_test.sh`, `scripts/verify_units.sh`, `scripts/deploy_vps.sh` or
`docs/BUILD_AND_DEPLOY.md`. Everything else matches the current source tree.
Archives built from `main` are published as GitHub Releases; see
[docs/BUILD_AND_DEPLOY.md](../docs/BUILD_AND_DEPLOY.md).
