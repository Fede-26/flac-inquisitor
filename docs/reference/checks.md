# Checks, categories and verdicts

This page lists every piece of evidence the detector can produce, with its
thresholds, as of `ALGO_VERSION` 5. The constants live in
`flac_inquisitor/detect.py`. For why each check works, see
[The spectral algorithms](../explanation/spectral-algorithms.md) and
[Bit depth, integrity and the other measurements](../explanation/bit-depth-and-integrity.md).

## Verdicts

| Verdict | Shown as | Meaning |
|---|---|---|
| `ok` | OK | No check found a problem, or the track is ignored. |
| `suspect` | Suspect | At least one warning, or a fail in a soft category. |
| `fake` | Fake | At least one fail, or two warnings of the same category. |
| `error` | Error / Unreadable | The analysis itself failed (the file could not be decoded at all). |

Grading rules, in `detect.grade()`:

1. A `fail` in a category that is not soft makes the track **fake**.
2. Two or more `warn` in the same non-soft category make it **fake**.
3. Otherwise any `warn`, or any `fail` in a soft category, makes it
   **suspect**.
4. Otherwise it is **ok**. `pass` and `info` never change the verdict.

The only category that can be soft is `upsampled`, when
`UPSAMPLED_VERDICT=suspect` (the default).

Album coherence evidence is added afterwards: it raises an **ok** track to
**suspect** and leaves other verdicts as they are. An ignored track always
counts as **ok**.

## Categories

| Category | Label | Raised by |
|---|---|---|
| `lossy-transcode` | Lossy transcode | `cutoff`, `hf-holes` |
| `upsampled` | Upsampled | `hires-content` |
| `padded-bits` | Padded bits | `bit-depth` |
| `lossywav` | lossyWAV | `bit-depth` |
| `corrupt` | Corrupt | `decode`, `md5`, `length` |
| `not-flac` | Not FLAC | `format` |
| `mixed-sources` | Mixed sources | `album-format`, `album-cutoff` |
| `fake-stereo` | Fake stereo | `album-mono` |

## Evidence by check

Each row is one possible outcome. *Value* is what the evidence table shows in
the **Measured** column.

### File format

| Check | Result | Category | Condition | Value |
|---|---|---|---|---|
| `format` | fail | `not-flac` | The file does not start with a FLAC header. The message names the real content when its magic bytes are known: MP3, Ogg, WAV or AAC. | detected format |

A `not-flac` file gets no other check.

### Integrity

| Check | Result | Category | Condition | Value |
|---|---|---|---|---|
| `decode` | fail | `corrupt` | ffmpeg wrote anything to its error output while decoding. | — |
| `md5` | fail | `corrupt` | The MD5 of the decoded audio differs from the one in STREAMINFO. | decoded MD5 |
| `length` | fail | `corrupt` | The number of decoded samples differs from STREAMINFO's total. | decoded samples |
| `integrity` | pass | — | None of the above. | — |

### Cutoff

| Check | Result | Category | Condition |
|---|---|---|---|
| `cutoff` | fail | `lossy-transcode` | Hard cutoff below 19,000 Hz (`CUTOFF_FAKE_HZ`). |
| `cutoff` | warn | `lossy-transcode` | Hard cutoff below 20,500 Hz (`CUTOFF_SUSPECT_HZ`). |
| `cutoff` | pass | — | No hard cutoff, or one at 20,500 Hz or above. |

A hard cutoff is a 500 Hz stretch whose level is at least 20 dB
(`CUTOFF_MIN_DROP_DB`) above everything from 250 Hz higher up to Nyquist. Value:
the cutoff in Hz.

### High-frequency band holes

| Check | Result | Category | Condition |
|---|---|---|---|
| `hf-holes` | fail | `lossy-transcode` | ≥ 4 % of loud frames have an isolated hole (`HOLES_FAKE`). |
| `hf-holes` | warn | `lossy-transcode` | ≥ 0.5 % (`HOLES_SUSPECT`). |
| `hf-holes` | pass | — | Below 0.5 %. |
| `hf-holes` | info | — | Fewer than 20 loud frames or fewer than 3 bands with content. |
| `hf-holes` | info | — | ≥ 0.5 %, but the holes sit in one passage: in fewer than 30 % of the 10-second stretches (`HOLES_MIN_COVER`), or, in tracks of at least 6 stretches, ≥ 90 % of them within 30 seconds (`HOLE_BURST_SHARE`). |

A hole: a 500 Hz band at least 25 dB (`HOLE_DROP_DB`) below its typical level
while both neighbours are within 10 dB (`HOLE_NEIGHBOUR_DB`) of theirs. Value:
the share of loud frames, 0 to 1.

### Per-frame bandwidth

| Check | Result | Category | Condition |
|---|---|---|---|
| `frame-bandwidth` | info | — | In 10 % of loud frames nothing reaches above 20,500 Hz (`FRAME_TOP_SHOWN_HZ`). |

Measured only; it does not affect the verdict yet. A band counts as reached
when it is above −70 dB (`FRAME_TOP_FLOOR_DB`) relative to the frame's 1–10 kHz
level. Value: the 10th percentile of the top band edge, in Hz.

### Hi-res content

Only for files above 48 kHz.

| Check | Result | Category | Condition |
|---|---|---|---|
| `hires-content` | fail | `upsampled` | Step ≥ 20 dB (`HIRES_STEP_FAKE_DB`), or step ≥ 12 dB and flatness ≤ 5 dB (`HIRES_FLAT_DB`). |
| `hires-content` | fail | `upsampled` | Floor spread ≤ 4.5 dB (`PLATEAU_SPREAD_DB`), floor at least 10 dB below the 16–20 kHz content (`PLATEAU_BELOW_DB`), and the content reaches the floor at or below 25,000 Hz (`PLATEAU_EDGE_MAX_HZ`). |
| `hires-content` | warn | `upsampled` | Step ≥ 12 dB (`HIRES_STEP_SUSPECT_DB`). |
| `hires-content` | pass | — | None of the above. |

- *Step*: the largest level drop from a 2 kHz stretch to the 2 kHz above it, for
  edges between 20 and 26 kHz.
- *Flatness*: the range of 1 kHz band levels from 27 kHz up to 40 kHz (or
  Nyquist − 1 kHz).
- *Floor spread*: the 10th-to-90th percentile range of 500 Hz band levels from
  26 kHz up to 40 kHz or 85 % of Nyquist, whichever is lower.

Value: the step in dB, or the floor spread for the second fail.

### Bit depth

Needs at least 10 loud blocks (`MIN_LOUD_BLOCKS`). A block is 4,096 frames; it
is loud when its peak reaches 2^(bits − 8), about −42 dBFS.

| Check | Result | Category | Condition | Value |
|---|---|---|---|---|
| `bit-depth` | fail | `padded-bits` | A file of more than 16 bits uses 16 bits or fewer. | bits used |
| `bit-depth` | fail | `padded-bits` | ≥ 90 % of loud blocks leave low bits unused, always the same number. | bits used |
| `bit-depth` | fail | `lossywav` | ≥ 25 % of loud blocks leave a varying number of low bits unused (`LOSSYWAV_FAKE_RATIO`). | share of blocks |
| `bit-depth` | warn | `lossywav` | ≥ 5 % (`LOSSYWAV_SUSPECT_RATIO`). | share of blocks |
| `bit-depth` | pass | — | All bits in use. | bits used |

### Hints

`info` evidence. It never changes a verdict.

| Check | Condition |
|---|---|
| `channels` | Stereo file whose left and right differ in fewer than 0.1 % of non-silent frames (`MONO_MAX_DIFF`). |
| `id3` | The file carries ID3v1 or ID3v2 tags. |
| `tags` | The vendor string or a tag mentions `lame` or `.mp3`. |
| `vendor` | Always, when there is a vendor string: the encoder that wrote the file. |
| `cd-length` | 44.1 kHz file whose length is a whole number of CD sectors (588 samples). |
| `leading-silence` | 1,000 to 1,300 samples of digital silence at the start, close to an MP3 decoder delay. |
| `bitrate` | Always: the FLAC bitrate in kbps. |

### Album coherence

Computed per album folder when it has at least 3 analyzed tracks. Each track
gets at most one of these, in this order of priority.

| Check | Result | Category | Condition |
|---|---|---|---|
| `album-format` | warn | `mixed-sources` | The track's sample rate and bit depth differ from those of a strict majority of the album. |
| `album-mono` | warn | `fake-stereo` | The track is mono stored as stereo, and a strict majority of the album is real stereo. |
| `album-cutoff` | warn | `mixed-sources` | The track's effective cutoff is at least 2,000 Hz (`OUTLIER_CUTOFF_HZ`) below the album median. The effective cutoff is Nyquist when the track has no hard cutoff. |

## Measured values

Stored per track in `metrics` (JSON), shown on the Accuracy page and in the
calibration table.

| Key | Unit | Meaning |
|---|---|---|
| `cutoff_hz` | Hz | Hard cutoff, or `null` when there is none. |
| `cutoff_drop_db` | dB | Level drop at the cutoff, or the largest drop found when there is no cutoff. |
| `hole_ratio` | 0–1 | Share of loud frames with an isolated band hole; `null` when not measurable. |
| `hole_cover` | 0–1 | Share of the 10-second stretches with loud frames that contain a hole. |
| `hole_burst` | 0–1 | Largest share of the holes within 30 seconds; `null` without holes or for tracks under 6 stretches. |
| `frame_top_hz` | Hz | 10th percentile of the per-frame top band edge. |
| `hires_step_db` | dB | Hi-res only: largest step between 20 and 26 kHz. |
| `hires_flat_db` | dB | Hi-res only: range of levels from 27 kHz up. |
| `hires_floor_spread_db` | dB | Hi-res only: 10th-to-90th percentile range of the floor. |
| `hires_edge_hz` | Hz | Hi-res only: where the content reaches the floor, from 18 kHz in 250 Hz steps; `null` when it does not below 26 kHz. |
| `effective_bits` | bits | Bit depth minus the fewest unused low bits found in any loud block. |
| `blocks_with_unused_bits` | 0–1 | Share of loud blocks with unused low bits. |
| `mono` | bool | Stereo files only: left and right identical. |
| `bitrate_kbps` | kbps | FLAC bitrate. |
| `cd` | object | CD database checksums, CD-format files only. See [CD verification](../explanation/cd-verification.md). |
