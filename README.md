# flac-inquisitor

A self-hosted web app that scans a music library for FLAC files that are not
really lossless:

- **lossy transcodes**: MP3, AAC, Opus or Vorbis decoded and saved as FLAC;
- **upsampled "hi-res"**: 44.1 or 48 kHz audio stored as 88.2, 96 or 192 kHz;
- **padded bits**: 16-bit audio stored as 24-bit, and lossyWAV-processed files;
- **corrupt files**: decode errors, MD5 mismatches, truncated audio;
- **files that are not FLAC at all**: an MP3 or Ogg file renamed to `.flac`;
- **mixed sources**: a track that does not match the rest of its album.

Each verdict comes with the evidence behind it: what was measured, the threshold
it was compared with, and a spectrogram to check it by eye. CD rips can also be
checked against AccurateRip and CTDB, which prove that a file is bit-identical
to a pressed CD.

Suspicious files can be moved to a quarantine folder and restored later. The
app never deletes anything unless asked to, and never asks Lidarr or any other
tool to download a replacement.

## What it cannot promise

The analysis looks for traces that lossy encoders and resamplers leave in the
audio. It is good at the common cases and wrong on some files:

- **False positives.** Old tape recordings, lo-fi productions and music made at
  32 kHz can look like transcodes. A CD that was itself mastered from MP3s is
  flagged even though the rip is perfect.
- **False negatives.** Some high-bitrate MP3, AAC and Vorbis files, and most
  Opus files, leave no trace beyond a cutoff that genuine masters can share.

Treat a verdict as a reason to look and listen, not as a proof. See
[Limits and false positives](docs/explanation/limits.md).

## Quick start

```sh
mkdir -p flac-inquisitor/data && cd flac-inquisitor
curl -LO https://raw.githubusercontent.com/Fede-26/flac-inquisitor/main/compose.example.yaml
mv compose.example.yaml compose.yaml
# Edit compose.yaml: paths to your music, user id.
docker compose up -d
```

Then open `http://<host>:8080` and press **Scan**. The full walk-through is in
the [getting started tutorial](docs/tutorials/getting-started.md).

> **No authentication.** Anyone who can open the page can move and delete
> files. Keep it on your local network, or put it behind a reverse proxy that
> asks for a login. See [Run behind a reverse proxy](docs/how-to/reverse-proxy.md).

## Documentation

The [documentation](docs/README.md) has four parts:

- [Tutorials](docs/README.md#tutorials): first install and first scan; setting up
  a development environment.
- [How-to guides](docs/README.md#how-to-guides): reverse proxy, mounts,
  quarantine, CD verification, Lidarr, recalibrating the thresholds.
- [Reference](docs/README.md#reference): settings, checks and verdicts, HTTP API,
  database, quarantine folder.
- [Explanation](docs/README.md#explanation): how each check works and why, the
  CD verification algorithms, the limits of the analysis.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).

The Docker image contains ffmpeg from the Alpine Linux packages, which runs as
a separate program. Its source code is available from the Alpine Linux
[aports](https://gitlab.alpinelinux.org/alpine/aports) repository.
