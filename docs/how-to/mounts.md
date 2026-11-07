# Mount the library and the quarantine

This guide shows how to mount the music library, the quarantine folder and the
data folder so that quarantine moves are instant and safe.

## Use one mount for library and quarantine

Mount one folder that contains both the library and the quarantine, and point
the two settings at subfolders of it:

```yaml
services:
  flac-inquisitor:
    volumes:
      - /srv/media:/media
      - ./data:/data
    environment:
      LIBRARY_PATH: /media/music
      QUARANTINE_PATH: /media/quarantine
```

With one mount, a quarantine move is a `rename()`: instant, and atomic.

With two mounts, even of the same disk or dataset, the kernel sees two
filesystems inside the container, and `rename()` fails with `EXDEV`. The app
then copies the file, compares the SHA-256 of the copy with the original, and
only then deletes the original. That is safe, but slow for large albums. The
Settings page warns when this happens: "Library and quarantine are on different
mounts".

## Keep the quarantine outside the library

The quarantine folder must not be inside the library, or the next scan would
analyze the quarantined files again. The Settings page refuses such a path.

Good:

```text
/media/music
/media/quarantine
```

Refused:

```text
/media/music
/media/music/quarantine
```

## Give the container the right user

Run the container as the owner of the music files, with the `user:` key of
Compose:

```yaml
    user: "1000:1000"
```

The user needs:

| Folder | Permission | Why |
|---|---|---|
| library | read | scanning |
| library | write | quarantine (moves files out) and restore (moves them back) |
| quarantine | write | receiving quarantined files |
| `/data` | write | database and spectrogram cache |

A read-only library works for scanning. The Settings page then warns that
quarantine and restore do not work.

## Mount the library read-only

If you only want reports and never want the app to move anything, mount the
library read-only:

```yaml
    volumes:
      - /srv/media/music:/music:ro
      - ./data:/data
```

`LIBRARY_PATH` defaults to `/music`, so no environment variable is needed in
this case.

## Check the result

Open **Settings** and press **Check**. The page checks the paths without saving
anything and lists any error or warning. No warning means the library is
readable and writable, and the quarantine is writable and on the same mount.
