# Pages and HTTP API

The app serves HTML pages and a JSON API from the same process, on port 8080.
There is no authentication. FastAPI also serves an interactive description of
the API at `/docs` and the OpenAPI schema at `/openapi.json`.

Errors from `/api/` paths are JSON: `{"detail": "message"}` with the HTTP
status. Errors on pages render an error page.

## Pages

| Path | Page |
|---|---|
| `/` | Dashboard: scan form and progress, counters, causes, recent activity. |
| `/albums` | Album list. Takes the list filters below. |
| `/tracks` | Track list. Takes the list filters below. |
| `/albums/{album_key}` | One album with its tracks, evidence and spectrograms. |
| `/quarantine?status=` | Quarantined files by album. `status`: `quarantined` (default), `restored`, `deleted`. |
| `/accuracy` | How the checks behave on CD-verified tracks. |
| `/settings` | Settings, with validation. |
| `/healthz` | `{"status": "ok", "version": "…"}`. For health checks. |

## List filters

`/albums`, `/tracks`, `/api/albums` and `/api/tracks` take the same query
parameters:

| Parameter | Values | Default |
|---|---|---|
| `verdict` | Repeatable: `fake`, `suspect`, `ok`, `error`; or `all` for no filter. | `fake` and `suspect` |
| `category` | A category id, such as `lossy-transcode`. Ignored tracks never match. | any |
| `fmt` | `<sample rate>/<bits>`, such as `44100/16`. | any |
| `cd` | `verified`, `mismatch`, `not-found`, `unchecked`. | any |
| `q` | Text found in the path, artist, album or title. | none |
| `sort` | `severity`, `artist`, `album`, `cutoff`. | `severity` |
| `page` | Page number, 50 rows per page. | `1` |

An album matches when at least one of its tracks matches; the album row still
describes all its tracks.

## Scans

| Method and path | Body | Result |
|---|---|---|
| `GET /api/scan` | — | Scan status, see below. |
| `POST /api/scan` | `{"full": false, "folder": "Artist/Album"}`, both optional | Starts a scan; returns the status. `full` re-analyzes unchanged files. `folder` limits the scan to a library-relative folder. 400 if the library or the folder does not exist; 409 if a job is running. |
| `DELETE /api/scan` | — | Asks the running job to stop; returns the status. |
| `GET /api/folders` | — | Library folders two levels deep: `["Artist", "Artist/Album", …]`. |

Scan status fields: `state` (`idle`, `running`, `stopping`), `phase`
(`analyzing`, `checking CDs`, `searching offsets` or `null`), `total`, `done`,
`cached`, `analyzed`, `errors`, `current` (file or album being worked on),
`cd_done`, `cd_total`, `started_at`, `finished_at` (Unix seconds), `folder`,
`message` (error of the last job), and `eta_seconds` while running.

## Results

| Method and path | Result |
|---|---|
| `GET /api/summary` | `tracks`, `by_verdict`, `by_category`, `flagged_albums`, `outdated`, `ignored`, `cd_verified`, `cd_albums`. |
| `GET /api/albums` | `{"items": [...], "total", "page", "pages"}`, one item per album. |
| `GET /api/albums/{album_key}` | One album: `album_dir`, `artist`, `album`, `verdict`, `summary`, `categories`, `cd_verified`, `cd_checked`, `cd_mismatch`, `tracks`. 404 if unknown. |
| `GET /api/tracks` | `{"items": [...], "total", "page", "pages"}`, one item per track. |
| `GET /api/tracks/{id}` | One track with `evidence`, `album_evidence`, `metrics`, `effective_verdict`, `ignored` and the CD columns. 404 if unknown. |
| `GET /api/tracks/{id}/spectrogram.png?width=&height=` | Spectrogram image. Defaults 1200 × 480, at most 2400 × 1200. Cached on disk. |
| `GET /api/tracks/{id}/audio` | The FLAC file, for the player. |
| `GET /api/accuracy` | Accuracy report: `tracks`, `albums`, `outdated`, `by_verdict`, `checks`, `metrics`, `flagged`. |
| `GET /api/activity?limit=50` | Recent activity, newest first, at most 500. |

## Actions

| Method and path | Body | Result |
|---|---|---|
| `POST /api/ignore` | `{"track_ids": [1, 2], "ignore": true, "note": null}` | Ignores (or with `ignore: false`, un-ignores) the tracks by audio MD5. Returns `{"changed": n}`. |
| `POST /api/albums/{album_key}/offset-search` | — | Starts an offset search for the album. 400 if no disc of the album is in `mismatch`; 409 if a job is running. |
| `POST /api/quarantine` | `{"track_ids": [...]}` or `{"album_keys": [...]}` | Moves the tracks (with sidecars) or whole album folders to the quarantine. Returns `batch`, `moved`, `tracks`, `errors`, `entry_ids`, `album_dirs`. 409 during a scan. |
| `GET /api/quarantine?status=quarantined` | — | Quarantine entries grouped by album. |
| `GET /api/quarantine/list.txt` | — | `{"text": "Artist – Album (tracks 3, 7)\n…"}`. |
| `POST /api/quarantine/restore` | `{"ids": [...], "mode": "check"}` | Restores entries. `mode`: `check` refuses on any conflict, `replace` quarantines whatever is in the way first, `force` only creates missing folders. Conflicts return 409 with a `conflicts` list. 409 during a scan. |
| `POST /api/quarantine/delete` | `{"ids": [...]}` | Deletes quarantined files for good. Returns `deleted` and `tracks`. |
| `GET /api/quarantine/{id}/spectrogram.png` | — | Spectrogram of a quarantined file. |
| `GET /api/library-file/spectrogram.png?path=` | — | Spectrogram of any library file by relative path, for restore conflicts. |

Restore conflict types: `path-taken` (a file has the same path), `same-track`
(a file in the folder has the same disc and track number), `folder-missing`.

## Settings

| Method and path | Body | Result |
|---|---|---|
| `GET /api/settings` | — | One item per setting: `key`, `env`, `label`, `help`, `value`, `source` (`env`, `db`, `default`), `locked`, `secret`, `has_value`. Secret values are returned empty. |
| `POST /api/settings/validate` | `{"values": {"key": "value"}}` | `{"errors": [...], "warnings": [...]}` without saving. |
| `PUT /api/settings` | `{"values": {"key": "value"}}` | Validates and saves. 400 on errors or on a key fixed by the environment. |
| `POST /api/lidarr/test` | `{"values": {...}}`, optional | Tests the connection with the given or saved values. 400 if not configured, 502 if Lidarr fails. |
