# Getting started

In this tutorial you install flac-inquisitor with Docker Compose, scan your
music library and look at the first results. It takes about ten minutes, plus
the time of the scan itself.

You need:

- a computer with Docker and the Compose plugin (`docker compose version` prints
  a version);
- a folder of FLAC files that this computer can read;
- a second, empty folder next to it for the quarantine.

Nothing in this tutorial changes your music files. The scan only reads them.

## 1. Prepare the folders

The app needs three places:

| Inside the container | What it holds |
|---|---|
| the library | your FLAC files, read by the scan |
| the quarantine | files you decide to move out of the library |
| `/data` | the app's database and cached spectrograms |

Put the library and the quarantine under one parent folder. Then the container
sees them on a single mount, and moving a file to the quarantine is an instant
rename. For example:

```text
/srv/media/
├── music/          ← your library
└── quarantine/     ← empty for now
```

Create a folder for the app itself and its data:

```sh
mkdir -p ~/flac-inquisitor/data
cd ~/flac-inquisitor
```

## 2. Write the Compose file

Download the example and rename it:

```sh
curl -LO https://raw.githubusercontent.com/Fede-26/flac-inquisitor/main/compose.example.yaml
mv compose.example.yaml compose.yaml
```

Open `compose.yaml` and change three things:

1. Under `volumes`, replace `/srv/media` with the parent folder of your library
   and quarantine.
2. Under `environment`, make `LIBRARY_PATH` and `QUARANTINE_PATH` point to the
   two folders as the container sees them: `/media/` followed by their names.
3. Set `user` to the owner of your music files. Find the numbers with:

   ```sh
   id -u    # user id, for example 1000
   id -g    # group id, for example 1000
   ```

The `data` folder you created in step 1 belongs to you, so the app can write
its database there.

## 3. Start the app

```sh
docker compose up -d
docker compose logs -f
```

Wait for a line like `Uvicorn running on http://0.0.0.0:8080`, then press
Ctrl+C to stop following the log. The app keeps running.

Open `http://localhost:8080`, or `http://<address of the computer>:8080` from
another device. You see the **Dashboard** with the message "The library has not
been scanned yet".

If a red banner says the library folder does not exist, the paths in step 2 do
not match. Fix `compose.yaml` and run `docker compose up -d` again.

## 4. Check the settings

Open **Settings** in the top bar. The fields you set in `compose.yaml` are
locked, with the note "Fixed by the `LIBRARY_PATH` environment variable": the
Compose file stays the single source of truth for them.

Leave everything else as it is for now. Two defaults are worth knowing:

- **Check CD databases** is on. After each scan, the app looks up CD-format
  albums in AccurateRip and CTDB. This sends track lengths, not audio, to those
  services. Set it to `false` if you do not want that.
- **Upsampled files count as** is `suspect`. An upsampled file has lost nothing
  compared with the CD; it is only not the hi-res it claims to be.

## 5. Run the first scan

Go back to the **Dashboard** and press **Scan**. Leave the folder field empty
to scan the whole library.

A progress bar shows how many files are done and which file is being read.
Every file is decoded once from start to end, so the first scan takes a while:
roughly the time ffmpeg needs to decode your library, divided by the number of
workers. You can close the page; the scan continues.

When the analysis ends, the scan checks CD-format albums against the CD
databases. The progress line then says "checking CDs".

Later scans are fast: they only analyze new or changed files.

## 6. Read the results

When the scan is done, the Dashboard shows counters:

- **fake tracks** and **suspect tracks**: files the checks flagged. The lists
  they link to leave out the ones that are verified CD rips.
- **albums to review**: albums with at least one flagged track.
- **tracks verified as CD rips**: files bit-identical to a pressed CD.
- **By cause**: flagged tracks per category, such as *Lossy transcode* or
  *Upsampled*.

**Recent activity**, at the bottom, has one line for the scan and one for the
CD check.

Press **albums to review**. The list starts with the worst albums. Open one.

## 7. Look at one track

On the album page, each row is a track with its verdict: **OK**, **Suspect**,
**Fake** or **Error**. Click a track title to open its details:

- a one-line summary of the strongest evidence;
- the **spectrogram**: time from left to right, frequency from bottom to top,
  loudness as colour. A dashed line marks the cutoff the analysis measured;
- an audio player;
- the **evidence table**: every check that ran, its result, the measured value,
  the threshold, and an explanation.

Pick a track marked **Fake** with the cause *Lossy transcode*. Its spectrogram
usually stops at a straight horizontal edge around 16 to 20 kHz: above it,
nothing. Compare it with a track marked **OK**, whose content fades out
gradually up to the top of the image.

Press **Compare spectrograms** to see all the tracks of the album side by side.
A track that stops lower than its neighbours stands out at once.

[Reading a spectrogram](../explanation/spectrograms.md) explains what else to
look for.

## What you have done

You installed the app, scanned the library and read the evidence behind a
verdict. From here:

- decide what to do with flagged albums: [Review the results](../how-to/review-results.md);
- move bad files out of the library: [Quarantine and restore files](../how-to/quarantine.md);
- accept a verdict you disagree with: [Mark a false positive](../how-to/false-positives.md);
- keep the app private if it is reachable from outside: [Run behind a reverse proxy](../how-to/reverse-proxy.md).
