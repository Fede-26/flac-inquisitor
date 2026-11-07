# Code layout

```text
flac-inquisitor/
├── flac_inquisitor/        the application package
│   ├── main.py             FastAPI app: pages, JSON API, startup
│   ├── settings.py         settings from environment, database, defaults; validation
│   ├── db.py               SQLite schema, connections, activity log
│   ├── queries.py          read-side queries for pages and API; Accuracy report
│   ├── scanner.py          scan job: file discovery, caching, worker pool, album coherence
│   ├── flacmeta.py         FLAC metadata parser (STREAMINFO, Vorbis comment, ID3 presence)
│   ├── analyzer.py         one-pass decode with ffmpeg and every measurement
│   ├── detect.py           checks, thresholds, grading, album coherence
│   ├── cdverify.py         AccurateRip and CTDB checksums, lookups, offset search
│   ├── spectrogram.py      spectrogram images with ffmpeg, cached on disk
│   ├── quarantine.py       moves to and from the quarantine, manifest, conflicts
│   ├── lidarr.py           optional Lidarr rescan and artist links
│   ├── templates/          Jinja2 pages; macros.html holds shared pieces and labels
│   └── static/             app.js (no framework), style.css, favicon
├── scripts/
│   ├── calibrate.py        makes known fakes from real files and measures the detector
│   └── accuraterip.py      AccurateRip ground truth for album folders, outside the app
├── tests/                  pytest suite on synthetic audio (conftest.py builds it)
├── docs/                   this documentation
├── Dockerfile              python:3.15-alpine with ffmpeg, uv-managed environment
├── compose.example.yaml    starting point for a deployment
├── pyproject.toml, uv.lock dependencies, locked
└── .github/workflows/image.yml   tests, then multi-arch image to GHCR
```

## Main entry points

| Function | Module | Called by |
|---|---|---|
| `read_flac_info(path)` | `flacmeta` | scanner, analyzer, quarantine |
| `measure(path, info)` | `analyzer` | `scanner.analyze_track` in a worker process |
| `evaluate(info, m, soft)` | `detect` | `scanner.analyze_track` |
| `analyze_file(path, soft)` | `detect` | calibration script; shortcut for the two above |
| `grade(marks, soft)` | `detect` | `evaluate`, `scanner.recompute_albums` |
| `album_coherence(tracks)` | `detect` | `scanner.recompute_albums` |
| `Scanner.start(full, folder)` | `scanner` | `POST /api/scan` |
| `Scanner.start_offset_search(album_dir)` | `scanner` | `POST /api/albums/{key}/offset-search` |
| `recompute_albums(album_dirs)` | `scanner` | end of each scan; `regrade_if_changed` |
| `regrade_if_changed()` | `scanner` | app start, settings save |
| `verify_albums(...)` | `cdverify` | end of each scan, when `CD_LOOKUP` is on |
| `search_offsets(...)` | `cdverify` | offset search job |
| `quarantine(...)`, `restore(...)`, `delete(...)` | `quarantine` | quarantine API routes |
| `rescan(album_dirs)` | `lidarr` | after quarantine and restore |

## Dependencies

| Package | Use |
|---|---|
| FastAPI, uvicorn, Jinja2 | Web server and pages |
| python-multipart | Form parsing for FastAPI; no current route reads forms |
| NumPy | Spectra, bit analysis, checksums |
| SciPy | Only the test fixtures import it (`scipy.signal`), although it is listed among the runtime dependencies |
| ffmpeg (external program) | Decoding, spectrogram images, test fakes |

Python's standard library covers the rest: `sqlite3`, `urllib` for the CD
databases and Lidarr, `zlib` for CRC-32.
