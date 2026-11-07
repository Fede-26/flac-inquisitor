# Reading a spectrogram

The checks measure what a spectrogram shows. Looking at one is the fastest way
to agree or disagree with a verdict.

## What the picture shows

The app draws each spectrogram with ffmpeg:

- **time** runs from left to right, over the whole track;
- **frequency** runs from 0 Hz at the bottom to the Nyquist frequency at the
  top: 22.05 kHz for a 44.1 kHz file, 48 kHz for a 96 kHz file. The scale is
  linear, and the labels on the left mark every few kHz;
- **colour** is loudness, on a logarithmic scale over 120 dB: black is silence,
  then purple, red, orange, up to pale yellow for the loudest parts;
- both channels are combined into one picture.

A dashed line marks the cutoff the analysis found, if any.

Because the frequency scale is linear, the top half of a 44.1 kHz picture
covers 11 to 22 kHz: the region where lossy encoders leave their marks takes up
a large part of the image.

## Patterns

### Genuine CD-quality audio

Colour reaches the top of the picture, or fades out gradually towards it. The
upper edge of the content is soft and changes with the music: cymbals and
breaths reach higher than a bass solo. Between songs or in quiet passages the
top may go dark together with everything else.

### Lossy transcode

A straight, horizontal edge across the whole track, with almost nothing above
it. Typical heights:

| Edge | Typical source |
|---|---|
| 16–17.5 kHz | MP3 at 128–192 kbps, AAC at 128 kbps |
| 18.5–19.5 kHz | MP3 V2 and V0 (LAME's variable bitrate) |
| 20–20.5 kHz | MP3 at 320 kbps, Opus |

The first and last rows come from the calibration runs behind the thresholds;
the V2 and V0 range is approximate and depends on the LAME version.

The edge stays at the same height in loud and quiet passages: a filter, not the
music. Just below it, MP3 often shows a ragged, "shredded" texture: small dark
gaps that come and go in the top bands. Those gaps are the band holes the app
counts.

### Upsampled hi-res

On a 96 or 192 kHz file, real content stops at 20–22 kHz (or 24 kHz for a 48 kHz
source) with a sharp edge. Above it, the picture is a uniform dark band of
noise, or simply black, up to the top. Genuine hi-res usually shows faint,
uneven content above 22 kHz: noise that rises and falls with the music,
harmonics of bright instruments.

When the upsampled master had noise added afterwards, the edge is softer: the
content fades into a flat floor. The floor is still flat from about 23 kHz up,
which is what the second hi-res test looks for.

### Old and analogue recordings

A soft, sloping top that varies with the music, often well below 20 kHz. Tape,
old microphones and vinyl roll off gradually. No hard edge means no lossy
cutoff, even at 15 kHz. These recordings can still trip the band-hole check
when their top bands are close to silence.

### Audio made at 32 kHz

Some broadcast and game audio was produced at 32 kHz. Converted to 44.1 kHz it
shows a hard edge at 16 kHz, exactly like a 128 kbps MP3. The spectrogram alone
cannot tell them apart; band holes and listening can.

### A mixed album

**Compare spectrograms** on the album page puts every track side by side. A
track whose edge sits clearly lower than its neighbours, or whose top looks
different, probably came from another source.

## Tips

- Look at the whole track, not one moment. A filter is constant; music is not.
- Quiet intros and fade-outs often look band-limited. Judge by the loud parts.
- If the spectrogram and the verdict disagree, trust your eyes and ears, and
  mark the track as a false positive if needed.
