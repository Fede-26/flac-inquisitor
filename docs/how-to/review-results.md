# Review the results

This guide shows how to go through flagged albums after a scan and decide what
each one needs.

## Start from the worst albums

1. Open **Albums** in the top bar. By default it lists albums with at least one
   **Fake** or **Suspect** track that is not a verified CD rip, most serious
   first.
2. Narrow the list with the filters, then press **Apply**:
   - **Verdict** chips: *Fake*, *Suspect*, *Unreadable*, *OK*.
   - **Cause**: one category, such as *Lossy transcode* or *Upsampled*.
   - **Format**: one sample rate and bit depth, such as `96000 Hz / 24 bit`.
   - **CD check**: *Not CD verified* (the default), *Any*, *CD verified*,
     *Not matching*, *Unknown disc*, *Not checked*. Choose *Any* to see
     flagged tracks that are verified CD rips too.
   - **Sort**: *Most serious first*, *Artist*, *Album*, *Lowest cutoff*.
   - **Search**: part of an artist, album, title or path.
3. Switch to **Tracks** at the top right to see the same filter as single files.

The Dashboard counters and the **By cause** table link to these lists with the
filter already set. The counters include verified CD rips, so a list can be
shorter than the number that links to it.

## Judge one album

Open the album. For each flagged track:

1. Read the summary under the title: it is the strongest piece of evidence.
2. Open the track and read the evidence table. Look at the **Result** column:
   `fail` decides the verdict, `warn` raises a doubt, `info` is only a hint.
3. Look at the spectrogram. Does it agree with the evidence? A cutoff should
   show as a sharp horizontal edge at the measured frequency. See
   [Reading a spectrogram](../explanation/spectrograms.md).
4. If in doubt, listen with the player, ideally on headphones.

Press **Compare spectrograms** to see every track of the album at once. A track
from another source stands out.

Keyboard shortcuts on the album page: `j` and `k` move between tracks, `Enter`
opens or closes a track, `s` opens its spectrogram (then `←` and `→` move to the
previous or next track), `q` quarantines it after a confirmation.

## Decide

| What you see | What it usually means | What to do |
|---|---|---|
| *CD verified* badge on a flagged track | The file is a perfect copy of a pressed CD. The CD itself was mastered from a lossy or band-limited source. | Nothing better exists unless another edition does. Mark it as a false positive. |
| *Lossy transcode*, with a clear edge at 16–19 kHz | An MP3, AAC or similar file saved as FLAC. | Quarantine and find a real lossless copy. |
| *Upsampled* on a 96 or 192 kHz file | CD-quality audio stored as hi-res. | Keep it (nothing is lost against the CD) or replace it with the 44.1 kHz edition. |
| *Padded bits* | 16-bit audio in a 24-bit container. | Same as upsampled. |
| *Corrupt* | Decode errors or MD5 mismatch. | Replace the file. |
| *Mixed sources* or *Fake stereo* | One track differs from the rest of the album. | Compare its spectrogram with the others. |
| A **Suspect** verdict from a single warning on an old or lo-fi recording | Often a false positive. | Listen; if it is fine, mark it as a false positive. |

Then:

- move bad files out: [Quarantine and restore files](quarantine.md);
- accept a verdict you disagree with: [Mark a false positive](false-positives.md);
- confirm a CD rip: [Verify CD rips](cd-verification.md).

## Scan again after changes

- **Scan one album or artist**: on the Dashboard, type part of the name in the
  folder field and pick it from the list, then press **Scan**. On an album
  page, **Rescan album** does the same.
- **Re-analyze**: tick *Re-analyze everything* on the Dashboard, or
  *Re-analyze* on the album page, to decode files again even when they did not
  change. You only need this to retry a file after a decoder error: a new
  version of the detector re-analyzes old results by itself.

## Check how the detector fares on your library

Open **Accuracy** in the top bar once the CD check has verified some tracks. It
uses them as ground truth: a CD-verified track is not a transcode made by
whoever shared it, whatever the detector says.

The page lists:

- which checks raised an alarm on CD-verified tracks, and how often;
- the distribution of each measured value against its thresholds;
- every CD-verified track judged fake or suspect.

Many alarms from one check on CD-verified tracks mean that check is too strict
for your music. [Recalibrate the thresholds](calibrate.md) explains what to do
with that.
