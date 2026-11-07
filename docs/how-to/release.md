# Release a new version

This guide shows how to publish a versioned Docker image.

The GitHub Actions workflow `.github/workflows/image.yml` runs the tests and
builds the image for `linux/amd64` and `linux/arm64`:

| Event | Image tags pushed to `ghcr.io/fede-26/flac-inquisitor` |
|---|---|
| push to `main` | `latest`, `sha-<commit>` |
| push of a tag `vX.Y.Z` | `X.Y.Z`, `X.Y`, `sha-<commit>` |
| pull request | none: build and tests only |

## Steps

1. Update the version in both places, with the same value:
   - `version` in `pyproject.toml`;
   - `__version__` in `flac_inquisitor/__init__.py`. The app shows it on the
     Settings page, in `/healthz`, and sends it in the `User-Agent` of CD
     database lookups.
2. If a check or threshold changed since the last release, make sure
   `ALGO_VERSION` in `detect.py` was bumped too.
3. Run the tests: `uv run pytest`.
4. Commit, then tag and push:

   ```sh
   git tag -a v0.2.0 -m "flac-inquisitor 0.2.0"
   git push origin main v0.2.0
   ```

5. Wait for the `image` workflow to finish on GitHub, then check that the new
   tags are listed under the repository's **Packages**.

## Versions to know about

There are two version numbers, and they mean different things:

- The **app version** (`0.2.0`) names a release.
- `ALGO_VERSION` names the detector. When it increases, stored results from the
  older detector are analyzed again on the next scan. A release that only
  changes the interface keeps the same `ALGO_VERSION`, so nobody has to wait for
  a full re-analysis.

Users who pin `X.Y` get bug-fix releases automatically; users on `latest` get
every push to `main`.
