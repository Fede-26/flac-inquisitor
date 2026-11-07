# Bit depth, integrity and the other measurements

The spectral checks look at frequencies. The checks on this page look at the
sample values themselves, at the file's own metadata, and at a few weak hints.
All of them come from the same single decode as the spectra.

## Bit depth

### Counting unused low bits

The analyzer splits the audio into blocks of 4,096 frames. For each block it
takes the bitwise OR of every sample of every channel. A low bit that is zero in
the OR is zero in every sample: the block never uses it. The number of trailing
zero bits of the OR is the number of unused low bits.

This works for negative samples too: in two's complement, a negative number
has as many trailing zeros as its absolute value.

Only **loud** blocks count: blocks whose peak reaches $2^{\text{bits} - 8}$,
about −42 dBFS. Quiet fades and silence legitimately use few bits and say
nothing about the source. At least 10 loud blocks are needed.

### What the counts reveal

Let $z$ be the unused low bits of each loud block.

| Pattern | Meaning | Result |
|---|---|---|
| A file of more than 16 bits with $\text{bits} - \min z \le 16$ | No block uses more than 16 bits: 16-bit audio in a bigger container. | fail, `padded-bits` |
| $z > 0$ in ≥ 90 % of loud blocks, always the same value | A constant number of empty bits: padding to any depth. | fail, `padded-bits` |
| $z > 0$ in ≥ 25 % of loud blocks, varying | lossyWAV, which removes as many low bits per block as it judges inaudible. | fail, `lossywav` |
| $z > 0$ in ≥ 5 % | Possibly lossyWAV. | warn, `lossywav` |
| otherwise | All bits in use. | pass |

Genuine audio uses its lowest bit almost everywhere: noise and dither make it
random. A block of real audio with an always-zero low bit is very unlikely.

## Integrity

### MD5 of the decoded audio

FLAC stores in its STREAMINFO block an MD5 of the original samples:
interleaved, signed, little-endian, $\lceil \text{bits} / 8 \rceil$ bytes per
sample. The analyzer hashes the decoded samples in the same layout while it
decodes. A difference means the audio changed after encoding: the file is
damaged.

The same hash, `audio_md5`, serves two more purposes:

- it identifies the audio regardless of tags and file names, for the scan cache
  and for ignores (see [Architecture](architecture.md));
- it is computed even when the encoder left the STREAMINFO MD5 empty (16 zero
  bytes), which some encoders do.

### Length and decoder errors

The decoded sample count must equal the total in STREAMINFO. ffmpeg runs with
`-v error`; anything it writes to its error output is reported. Its error
output goes to a temporary file, not a pipe: a badly damaged file can print
more than a pipe buffer holds.

Any of these is a fail in the `corrupt` category.

### Files that are not FLAC

`flacmeta.read_flac_info()` reads the header without decoding. A file that does
not start with `fLaC` (after an optional ID3v2 tag) is not FLAC. The first bytes
then name the real format: `ID3` or an MPEG sync word for MP3, `OggS` for Ogg,
`RIFF` for WAV, an ADTS sync word for AAC.

## Mono stored as stereo

For stereo files the analyzer counts non-silent frames and frames where left and
right differ. If they differ in fewer than 0.1 % of non-silent frames, the file
is mono audio in two identical channels. The tolerance lets a click or an edit
at one point pass.

On its own this is only a hint: mono recordings are released like this on CD.
[Album coherence](verdicts.md#album-coherence) flags it when the rest of the
album is stereo.

## Weak hints

These are `info` evidence. They never change a verdict, but they help a human
decide.

| Hint | Why it matters |
|---|---|
| ID3 tags | FLAC uses Vorbis comments. ID3 tags in a FLAC file mean it went through a tool made for MP3s. |
| `lame` or `.mp3` in tags or vendor string | A tagger or converter left a trace of an MP3 source. |
| Vendor string | Names the encoder library that wrote the file. |
| Whole number of CD sectors | A 44.1 kHz track whose length is a multiple of 588 samples was most likely cut from a CD. |
| 1,000–1,300 samples of digital silence at the start | Close to the delay an MP3 encoder and decoder add together (about 1,105 samples for LAME). |
| FLAC bitrate | Very low bitrates for the format can hint at band-limited content. |
