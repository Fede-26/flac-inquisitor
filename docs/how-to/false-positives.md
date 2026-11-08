# Mark a false positive

This guide shows how to tell the app that a flagged track is fine, so it stops
showing up among the results.

## Ignore one track

1. Open the album and open the track.
2. Press **Ignore (false positive)**.

The track now counts as **OK** everywhere: in the counters, the lists and the
album verdict. Its analysis is kept, and the album page still shows the
evidence, with an *ignored* tag next to the verdict.

## Ignore many tracks

- On an album page, tick the tracks and press **Ignore selected**.
- In the **Albums** or **Tracks** list, tick rows and press **Ignore** in the
  bar above the list. For albums, every track of the album is ignored.

## Undo

Open the track and press **Stop ignoring**. It gets its verdict back at once.

## What an ignore is tied to

An ignore belongs to the **audio**, not to the file name: the app stores the MD5
of the decoded samples. So the ignore survives:

- retagging the file;
- renaming or moving it inside the library;
- a new detector version that re-analyzes it.

It does not survive a change of the audio itself, such as replacing the file
with another rip. That is on purpose: the new file deserves its own verdict.

## When to ignore

Ignore a track when you have looked at the evidence and the spectrogram,
listened, and decided the file is as good as you can get. Typical cases:

- a track with a *CD verified* badge: the file is a perfect copy of the CD, and
  the CD itself carries the lossy traces. The lists already leave these tracks
  out unless the **CD check** filter is set to *Any*;
- old tape or vinyl transfers whose high frequencies end in a natural roll-off;
- lo-fi music that is band-limited on purpose.

[Limits and false positives](../explanation/limits.md) explains why these cases
get flagged.
