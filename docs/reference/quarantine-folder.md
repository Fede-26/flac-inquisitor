# Quarantine folder and manifest.json

## Layout

```text
<QUARANTINE_PATH>/
└── <YYYY-MM-DD>/                 date of the move
    ├── manifest.json
    └── <path relative to the library>
```

A file at `<library>/Artist/Album/03. Song.flac` quarantined on 7 November 2026
goes to `<quarantine>/2026-11-07/Artist/Album/03. Song.flac`.

- **Sidecars.** Quarantining a track also moves the files next to it with the
  same name and another extension (`03. Song.lrc`, `03. Song.txt`).
- **Whole albums.** Quarantining an album moves every file under its folder,
  including disc subfolders, covers, logs and cue sheets.
- **Same name twice on one day.** The second file gets a suffix:
  `03. Song (2).flac`. Nothing is ever overwritten.
- **Empty folders.** Folders emptied by a move are removed, in the library and
  in the quarantine, up to (not including) the root.
- **Cross-mount moves.** When library and quarantine are on different mounts,
  the file is copied to `<name>.partial`, its SHA-256 is compared with the
  original, then it is renamed and the original deleted. A mismatch leaves the
  original in place.

## manifest.json

One file per date folder: a JSON array with one object per moved file. It lets
you see what each file is and where it came from without the app.

```json
[
  {
    "batch": "3f2a9c1b7d4e",
    "original_path": "Artist/Album/03. Song.flac",
    "stored_path": "2026-11-07/Artist/Album/03. Song.flac",
    "is_audio": 1,
    "album_dir": "Artist/Album",
    "artist": "Artist",
    "album": "Album",
    "title": "Song",
    "disc": 1,
    "track": 3,
    "audio_md5": "9b1d…",
    "verdict": "fake",
    "categories": "[\"lossy-transcode\"]",
    "created_at": "2026-11-07T18:02:11+00:00",
    "status": "quarantined"
  }
]
```

| Field | Meaning |
|---|---|
| `batch` | Id of one quarantine action. Files moved together share it. |
| `original_path` | Path relative to the library. |
| `stored_path` | Path relative to the quarantine folder. |
| `is_audio` | `1` for a FLAC track, `0` for a sidecar or other album file. |
| `album_dir` | Album folder relative to the library; disc folders are folded into it. |
| `artist`, `album`, `title`, `disc`, `track` | From the tags, when the file was scanned. Sidecars take them from their track. |
| `audio_md5` | MD5 of the decoded audio. |
| `verdict`, `categories` | Final verdict and categories at the time of the move. `categories` is a JSON string. |
| `created_at` | Time of the move, UTC, ISO 8601. |
| `status` | `quarantined`, then `restored` after a restore. |

The database keeps the same records in its `quarantine` table, plus
`resolved_at` and the `deleted` status. The manifest is a convenience copy; the
app reads the database.
