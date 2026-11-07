# Connect Lidarr

This guide shows how to connect flac-inquisitor to [Lidarr](https://lidarr.audio/)
when Lidarr manages the same library. The connection is optional.

## What the connection does

- After every quarantine and restore, the app asks Lidarr to rescan the artist
  folders involved. Lidarr then drops the files that left and picks up the ones
  that came back.
- The Quarantine page links each album to its artist page in Lidarr
  (**Open in Lidarr**), where you can look for a better release yourself.

What it does **not** do: it never marks anything as wanted, never starts a
search, never downloads. Replacing a file stays your decision.

## Steps

1. In Lidarr, open **Settings → General** and copy the **API Key**.
2. In flac-inquisitor, open **Settings** and fill in the Lidarr group:
   - **Lidarr URL**: the address the container can reach, such as
     `http://lidarr:8686` when both run in the same Compose project.
   - **Lidarr API key**: the key from step 1.
   - **Library path in Lidarr**: only if Lidarr sees the library under another
     path. If flac-inquisitor reads the library at `/media/music` and Lidarr at
     `/music`, enter `/music`.
3. Press **Test connection**. The answer should be `Connected to Lidarr` with
   its version.
4. Press **Save**.

You can set the same values with the `LIDARR_URL`, `LIDARR_API_KEY` and
`LIDARR_PATH_PREFIX` environment variables instead. The API key is never shown
again on the Settings page once saved.

## Check that it works

Quarantine one track, then open **Recent activity** on the Dashboard. A line
`Asked Lidarr to rescan /music/Artist` should follow the quarantine line, and
the track should disappear from the album in Lidarr.

If the line says `Lidarr rescan failed`, the message gives the reason: wrong
URL, refused API key, or Lidarr not reachable. The file move itself is never
undone because of a Lidarr problem.

## Folder layout Lidarr needs

The app takes the first folder of a track's path as the artist folder: for
`Artist/Album/01. Song.flac` it asks Lidarr to rescan `<library path in
Lidarr>/Artist`. This matches Lidarr's own layout of one folder per artist
directly under the root folder.
