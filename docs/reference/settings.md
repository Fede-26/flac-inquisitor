# Settings and environment variables

Every setting has three possible sources, in this order of priority:

1. **Environment variable.** It wins, and the Settings page shows the field as
   read-only, "Fixed by the `NAME` environment variable".
2. **Value saved from the Settings page**, stored in the database.
3. **Default.**

## Settings

| Environment variable | Settings page label | Default | Values |
|---|---|---|---|
| `LIBRARY_PATH` | Library | `/music` | Folder scanned for FLAC files, as the container sees it. |
| `QUARANTINE_PATH` | Quarantine | `/quarantine` | Where quarantined files go. Must not be inside the library. Created on first use. |
| `WORKERS` | Workers | `2` | Files analyzed in parallel, whole number ≥ 1. Each worker uses about one CPU core. |
| `EXCLUDE` | Exclude | empty | Glob patterns relative to the library, one per line, such as `*/Podcasts/*`. Matching files are not scanned. |
| `CD_LOOKUP` | Check CD databases | `true` | `true` or `false`. After each scan, look up CD-format albums in AccurateRip and CTDB. |
| `UPSAMPLED_VERDICT` | Upsampled files count as | `suspect` | `suspect` or `fake`. The worst verdict an *Upsampled* finding can give. |
| `LIDARR_URL` | Lidarr URL | empty | `http://` or `https://` address of Lidarr, such as `http://lidarr:8686`. Empty turns the integration off. |
| `LIDARR_API_KEY` | Lidarr API key | empty | From **Settings → General** in Lidarr. Never shown again once saved. |
| `LIDARR_PATH_PREFIX` | Library path in Lidarr | empty | The library path as Lidarr sees it, if different from `LIBRARY_PATH`. |

## Environment only

| Variable | Default | Meaning |
|---|---|---|
| `DATA_PATH` | `/data` | Folder for the database (`flac-inquisitor.db`) and the spectrogram cache (`spectrograms/`). It locates the database itself, so it cannot be a database setting. |

## Validation

The Settings page checks the values on **Check** and on **Save**. **Save**
refuses to store anything while there are errors.

Errors:

- the library folder does not exist or is not readable;
- the quarantine path is a file, or is not writable;
- the quarantine folder is inside the library;
- workers is not a whole number ≥ 1;
- `CD_LOOKUP` is not `true` or `false`;
- `UPSAMPLED_VERDICT` is not `suspect` or `fake`;
- the Lidarr URL does not start with `http://` or `https://`.

Warnings:

- the quarantine folder does not exist yet (it is created on first use);
- the library is read-only (scanning works, quarantine and restore do not);
- library and quarantine are on different mounts (moves are copy, verify,
  delete instead of an instant rename).

## Effects of a change

| Setting | When it takes effect |
|---|---|
| `LIBRARY_PATH`, `EXCLUDE`, `WORKERS` | Next scan. Files that disappear from the scan are marked missing; their results are kept. |
| `CD_LOOKUP` | Next scan. |
| `UPSAMPLED_VERDICT` | At once when saved from the page; at the next start when changed in the environment. Every stored verdict is graded again from its evidence, without decoding. |
| Lidarr settings | Next quarantine or restore. |
