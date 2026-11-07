# Quarantine and restore files

This guide shows how to move flagged files out of the library, put them back,
and delete them for good.

The quarantine is a folder outside the library. A quarantined file is moved, not
copied and not deleted: you can always restore it until you delete it. Your
library must be writable by the container; see
[Mount the library and the quarantine](mounts.md).

File actions are disabled while a scan runs, because the scan holds a list of
the files it is reading.

## Quarantine single tracks

1. Open the album.
2. Tick the tracks to move.
3. Press **Quarantine selected**. The dialog lists the files; press
   **Quarantine**.

Each track takes its sidecars with it: files in the same folder with the same
name and another extension, such as `03. Song.lrc` next to `03. Song.flac`.

A message confirms the move and offers **Undo** for ten seconds. Undo restores
the files at once.

On the album page you can also focus a track with `j` and `k` and press `q`.

## Quarantine a whole album

On the album page, press **Quarantine album** and confirm. Everything in the
album folder moves: tracks, disc subfolders, covers, logs, cue sheets. The empty
album folder is then removed from the library.

From the **Albums** list, tick several albums and press **Quarantine** in the
bar that appears above the list. The **Tracks** list works the same way for
single files.

## Find the files afterwards

Quarantined files keep their place in the library tree, under a folder named
after the day of the move:

```text
quarantine/
└── 2026-11-07/
    ├── manifest.json
    └── Artist/
        └── Album/
            ├── 03. Song.flac
            └── 03. Song.lrc
```

The folder is readable without the app. `manifest.json` records where each file
came from; see [Quarantine folder and manifest.json](../reference/quarantine-folder.md).

Open **Quarantine** in the top bar to see the quarantined albums, with the
verdict and causes each track had. The tabs at the top show restored and
deleted files too.

## Make a list to download again

On the Quarantine page, press **Copy list**. The clipboard gets one line per
album:

```text
Artist – Album (tracks 3, 7)
Other Artist – Other Album
```

The track numbers appear only when part of an album was quarantined.

## Restore files

On the Quarantine page, press **Restore** on a track, or **Restore all** on an
album.

Before moving anything back, the app looks for conflicts:

| Conflict | Meaning |
|---|---|
| A file with the same name is already in the library | Something new took the old path. |
| The library already has this track under another name | Same disc and track number, different file name: usually a replacement you downloaded. |
| The album folder no longer exists | It will be created. |

If there is a conflict, a dialog shows the spectrograms of the file in the
library and of the quarantined file side by side. Then:

- **Replace (move the current files to quarantine)** moves the files in the way
  to the quarantine first, then restores. Nothing is overwritten.
- **Restore anyway** appears when the only problem is a missing folder.
- **Cancel** leaves everything as it is.

## Delete for good

On the Quarantine page, press **Delete** on a track or **Delete all** on an
album. To confirm, type the name of the album or track shown in the dialog.
The files are removed from the disk and cannot be restored. The quarantine log
keeps a record of them, under the *Deleted* tab.

## If Lidarr manages the library

With [Lidarr connected](connect-lidarr.md), every quarantine and restore asks
Lidarr to rescan the artist folder, so its view of the library stays current.
Lidarr is never asked to search for or download a replacement.
