# flac-inquisitor documentation

The documentation follows [Diátaxis](https://diataxis.fr/). The four parts answer
different needs:

| You want to… | Read |
|---|---|
| learn by doing, step by step | [Tutorials](#tutorials) |
| get a specific job done | [How-to guides](#how-to-guides) |
| look up an exact name, value or field | [Reference](#reference) |
| understand how and why it works | [Explanation](#explanation) |

If you only want to use the app, start with
[Getting started](tutorials/getting-started.md) and then
[Reading a spectrogram](explanation/spectrograms.md). The rest is there when you
need it.

## Tutorials

- [Getting started](tutorials/getting-started.md): install with Docker Compose,
  run the first scan, read the results.
- [Development setup](tutorials/development.md): run the app and the tests from a
  checkout, and follow one track through the code.

## How-to guides

Using the app:

- [Mount the library and the quarantine](how-to/mounts.md)
- [Run behind a reverse proxy](how-to/reverse-proxy.md)
- [Review the results](how-to/review-results.md)
- [Quarantine and restore files](how-to/quarantine.md)
- [Mark a false positive](how-to/false-positives.md)
- [Verify CD rips and search for drive offsets](how-to/cd-verification.md)
- [Connect Lidarr](how-to/connect-lidarr.md)

Working on the code:

- [Recalibrate the thresholds](how-to/calibrate.md)
- [Add or change a check](how-to/add-a-check.md)
- [Release a new version](how-to/release.md)

## Reference

- [Settings and environment variables](reference/settings.md)
- [Checks, categories and verdicts](reference/checks.md)
- [Pages and HTTP API](reference/api.md)
- [Quarantine folder and manifest.json](reference/quarantine-folder.md)
- [Database schema](reference/database.md)
- [Code layout](reference/code-layout.md)

## Explanation

- [How a fake FLAC is recognised](explanation/detection.md): the idea behind
  every check, with no maths.
- [Reading a spectrogram](explanation/spectrograms.md)
- [The spectral algorithms](explanation/spectral-algorithms.md): cutoff, band
  holes, per-frame bandwidth, hi-res content.
- [Bit depth, integrity and the other measurements](explanation/bit-depth-and-integrity.md)
- [Verdicts and album coherence](explanation/verdicts.md)
- [CD verification: AccurateRip, CTDB and the offset search](explanation/cd-verification.md)
- [Architecture: scans, caching and storage](explanation/architecture.md)
- [Limits and false positives](explanation/limits.md)
