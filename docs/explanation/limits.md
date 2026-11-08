# Limits and false positives

flac-inquisitor looks for traces that lossy encoders, resamplers and padding
leave behind. Some genuine files carry similar traces, and some fakes carry
none. This page lists the known cases, so a verdict can be read for what it is:
a reason to look, not a proof.

## False positives: genuine files that get flagged

### The CD itself was made from a lossy source

Some CDs were mastered from MP3s or from band-limited digital masters: cheap
reissues, some compilations, some soundtracks. A perfect rip of such a CD
carries every trace of the lossy source. The **CD verified** badge tells this
case apart: the rip is fine, the master is not. Mark it as a false positive, or
look for another edition.

### Audio produced at 32 kHz

Some broadcast, game and early digital audio was produced at 32 kHz, which
holds nothing above 16 kHz. Converted to 44.1 kHz, it shows a hard edge at
16 kHz, exactly like a 128 kbps MP3. The cutoff check reports it as a fail and
says so in its message: "Audio resampled from 32 kHz looks the same."

### Old, analogue and dark recordings

Tape, vinyl transfers and old microphones roll off gently. A gentle roll-off is
not a cutoff, so the cutoff check passes them. But their top bands can sit close
to silence, where small fluctuations look like band holes, and where the
per-frame bandwidth falls low. Dark recordings were the 4 genuine tracks out of
42 that the per-frame bandwidth caught in calibration, which is why that
measurement does not affect the verdict yet.

### Mastering low-pass filters

Some masters are low-passed at 19 to 20 kHz on purpose. This gives a hard
cutoff in the warn range (19 to 20.5 kHz), the same range as MP3 at 320 kbps.
A single warning gives **Suspect**, not **Fake**.

### Flat, noisy hi-res recordings

The second hi-res test flags a flat noise floor above about 23 kHz. A genuine
hi-res recording with very little ultrasonic content and a flat noise floor
could pass for upsampled. The test also requires a drop of at least 10 dB
between the 16–20 kHz content and the floor, which a flat full-band recording
does not have. Still, its threshold rests on six genuine hi-res tracks, and the
flattest of them was only 2.1 dB above it.

### Tonal passages with sparse harmonics

Synth leads and chiptune voices can leave empty bands between their harmonics,
which look like encoder holes. The band-hole check ignores holes that stay
within one passage, but a track made of such passages from start to end can
still be flagged.

### Mono in a stereo album

A mono track in a stereo album is flagged as *Fake stereo*. Some albums mix
mono and stereo recordings on purpose, such as reissues with bonus tracks from
mono masters.

## False negatives: fakes that pass

### High-bitrate lossy files

MP3 at 320 kbps or V0, AAC at 256 kbps and Vorbis at high quality keep almost
the full band. Their cutoff is at 19 to 20.5 kHz or absent, and they drop few
bands, briefly. The short-frame hole check catches most of them, but in
calibration one in twenty LAME V0, a quarter of the AAC 256 and over a third
of the Vorbis q9 fakes still passed. Opus keeps every band up to its 20 kHz
limit: only its cutoff, a warning, gives it away.

These files are also the ones that sound closest to the original.

### Fakes processed after decoding

Noise, EQ or a resampling step added after the lossy decode can fill the band
above the cutoff. The cliff disappears, and so does the evidence.

### Short sections

The cutoff check uses the spectrum averaged over the whole track. A lossy insert
of a few seconds inside a genuine track barely moves the average. The per-frame
measurements see individual moments, but count them as a share of the track.

### Lossy upsampled to hi-res

A 96 kHz file made from an MP3 is flagged as *Upsampled*. The hi-res test fires
first and gives the verdict; the lossy origin may also show as a cutoff below
19 kHz, but not always.

## What the measurements do not see

- **The side channel, except for holes.** The cutoff and the per-frame
  bandwidth analyze the mid channel, $(L + R) / 2$. Only the short-frame hole
  check also looks at the difference between the channels.
- **Content below 11 kHz.** Band holes are only looked for from 11 kHz up.
- **Very short tracks.** Band holes and per-frame bandwidth need at least 20
  loud frames, about one second of loud music at 44.1 kHz. Bit depth needs 10
  loud blocks of 4,096 frames, also about one second.

## CD verification

- **No match is not a verdict.** A different pressing, an uncorrected drive
  offset, a read error, a missing pregap, or a disc that nobody submitted all
  give `mismatch` or `not-found`.
- **Not every disc can be checked.** Enhanced CDs with a data track, discs with
  hidden track one audio, and rips not cut at sector boundaries are not
  supported.
- **The offset search has a blind spot.** Discs known to AccurateRip only by
  v2 checksums, and absent from CTDB, are not found.
- **A match does not clear the master.** See the first false positive above.

## Biases of the thresholds

The thresholds were calibrated on one real library and on fakes made from it
with current encoders (LAME, ffmpeg's AAC, libopus, oggenc). Music very
different from that set, older encoders with other low-pass defaults, or
encoders not tested (Musepack, WMA, ATRAC) may behave differently. The
**Accuracy** page shows how the checks behave on your own CD-verified tracks;
[Recalibrate the thresholds](../how-to/calibrate.md) shows how to adjust them.
