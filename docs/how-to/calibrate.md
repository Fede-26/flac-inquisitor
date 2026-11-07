# Recalibrate the thresholds

This guide shows how to measure the detector on real music and move its
thresholds. Do this when the **Accuracy** page shows a check raising many
alarms on CD-verified tracks, or after changing how a check measures.

All thresholds are constants at the top of `flac_inquisitor/detect.py`. Each
has a comment with the measurements that justify it.

## What you need

- A development checkout ([Development setup](../tutorials/development.md)).
- On your `PATH`: ffmpeg with libmp3lame and libopus, `lame`, and `oggenc`
  (from vorbis-tools). The script makes its fakes with them.
- A folder of FLAC files you trust to be genuine. Tracks with a **CD verified**
  badge are the best choice: they are bit-identical to a pressed CD. Add some
  real hi-res tracks if you calibrate the hi-res checks. Forty to sixty tracks
  of varied genres and eras are a good size.

Copy the files into one flat folder; the script reads `*.flac` from its top
level only.

## 1. Run the calibration

```sh
uv run python scripts/calibrate.py ~/calibration/real ~/calibration/work --jobs 4
```

For every original, the script makes known fakes and stores them in the work
folder:

| Kind | How it is made |
|---|---|
| `mp3-128`, `mp3-192`, `mp3-320`, `mp3-v2`, `mp3-v0` | LAME |
| `aac-256` | ffmpeg's native AAC encoder |
| `opus-160` | libopus |
| `vorbis-q5`, `vorbis-q9` | oggenc |
| `upsampled` | 44.1 or 48 kHz originals resampled to 96 kHz, 24-bit |
| `padded` | 16-bit originals stored as 24-bit |

Hi-res originals are brought down to 48 kHz before the lossy encoders, as
streaming services deliver them. The fakes are cached: a second run only
analyzes again.

Then it analyzes everything and prints a table (the numbers below are only an
example):

```text
kind           n   ok  susp  fake  err
original      58   49     6     3    0
mp3-128       58    0     0    58    0
mp3-v0        58   18    12    28    0
…
Originals flagged:
  suspect  some-track.flac: Hard cutoff at 19.6 kHz: …
```

The goal: every fake row has as few `ok` as possible, the `original` row as many
as possible. Note that the script grades *Upsampled* as fake, ignoring the
setting of the app.

## 2. Look at the numbers

`work/results.tsv` has one row per file with every measured value
(`cutoff_hz`, `hole_ratio`, `hires_floor_spread_db`, `frame_top_hz` and the
rest; see [Checks](../reference/checks.md#measured-values)). Open it in a
spreadsheet or load it with pandas, and compare the distribution of a value on
originals and on each kind of fake.

Before moving a threshold for a flagged original, look at its spectrogram and
listen. A flagged "original" may be a fake you already had.

## 3. Change the threshold

Edit the constant in `detect.py`. Update its comment with the new numbers, in
general terms: how many files, which kind, which range. Do not name albums or
artists of your own library in the comments.

Then:

1. Bump `ALGO_VERSION` in `detect.py`. Stored results with an older version are
   re-analyzed on the next scan.
2. Run the calibration again and compare the table.
3. Run the tests: `uv run pytest`. A test that breaks may mean the change is
   wrong, or that the test encoded the old threshold.

## 4. Check on the whole library

Deploy the new version and scan. Then open the **Accuracy** page: the alarms on
CD-verified tracks should go down, and the counters on the Dashboard should not
lose real fakes you already confirmed.

## Optional: ground truth without the app

`scripts/accuraterip.py` checks album folders against AccurateRip directly,
using the MusicBrainz layout of each disc, and can run the detector on every
track:

```sh
uv run python scripts/accuraterip.py --root /path/to/library --discover --detect --jobs 3 > truth.tsv
```

On tracks AccurateRip confirms, any verdict other than `ok` is a false
positive. The app's own CD check does the same job inside a scan, so you only
need the script when you calibrate outside the app.
