# Verify CD rips and search for drive offsets

This guide shows how to check CD rips against the AccurateRip and CTDB
databases, read the outcome, and recover rips that were not corrected for the
drive's read offset.

A match proves that a file is bit-identical to rips other people made from a
pressed CD. [CD verification](../explanation/cd-verification.md) explains how
it works and what it cannot prove.

## Turn the check on or off

The check runs after every scan when **Check CD databases** is `true` (the
default). Set it in **Settings**, or with the `CD_LOOKUP` environment variable.

When it runs, the app sends each disc's layout (the track lengths) to
`accuraterip.com` and `db.cue.tools`, and for some discs to `musicbrainz.org`.
No audio and no file names leave the computer. Answers are cached for 30 days.

## Make an album checkable

Only albums that look like a CD rip are looked up. Each track must be:

- 44,100 Hz, 16-bit, stereo;
- a whole number of CD sectors long (588 samples each), as a track-by-track rip
  is;
- at least 5,880 samples long (about 0.13 s).

The tracks of one disc must all be there, in order. The app numbers them by the
`TRACKNUMBER` tag, and groups discs by the `DISCNUMBER` tag. Disc folders named
like `Disc 1`, `CD2` or `disk 3` are treated as part of their album.

Albums that do not qualify are listed as *Not checked*.

## Read the outcome

The CD badge on each track and the **CD check** filter of the lists use four
outcomes:

| Outcome | Meaning |
|---|---|
| **CD verified** | AccurateRip or CTDB has the same checksum. The tooltip gives the confidence: how many other rips agree. |
| **Not matching** | The databases know a disc with this layout, but the checksum differs. |
| **Unknown disc** | No database knows a disc with these track lengths. |
| **Not checked** | Not in CD format, not cut on sector boundaries, or the check is off. |

**Not matching** is not proof of a fake. Common harmless causes:

- another pressing of the same album, with a different master;
- a rip not corrected for the drive's read offset (see below);
- a read error on a scratched disc.

**Unknown disc** says nothing either way. The disc may be rare, or the rip may
have left out a pregap. In the second case, the app asks MusicBrainz for the
original layout if the files carry a `MUSICBRAINZ_ALBUMID` tag.

The **Recent activity** list on the Dashboard has one line per scan with the
counts of each outcome.

## Search for the drive offset

Every CD drive reads a little early or late. Ripping software corrects for that
when it is set up correctly. An uncorrected rip holds the right audio, shifted by
a few hundred samples, and matches nothing.

To check an album that is **Not matching**:

1. Open the album.
2. Press **Try other offsets**. The button only appears when at least one disc
   of the album is known to the databases but does not match.
3. Wait. The app decodes each track again and tries every shift up to 2,939
   samples in both directions. The button shows how many tracks it has read.
4. Read the result in **Recent activity**, for example:

   ```text
   Offset search for Artist/Album: 12 of 12 tracks match when shifted by +667 samples.
   ```

   or `no shift within 2939 samples matches`.

Matching tracks become **CD verified**. The track details say which shift made
the match. The shift changes nothing audible: at most 2,939 samples, about 67
ms, move from the start of each track to the end of the one before (or the
other way), and the first or last samples of the disc are lost or silent.

The result stays when you scan again. Analyzing the files again
(*Re-analyze*) clears it, and the next scan checks the album at offset zero.

## Known limits

- Discs with a hidden track before track one, and enhanced CDs with a data
  track, are not found.
- The offset search cannot find a disc that AccurateRip only knows through its
  newer checksum format (v2) and that CTDB does not know.

## Use the result

A **CD verified** track that is still flagged as fake or suspect is a real CD
rip whose master carries the traces. Mark it as a false positive if you are
happy with that edition; see [Mark a false positive](false-positives.md).

The **Accuracy** page uses all CD-verified tracks to show how the checks behave
on your library; see [Review the results](review-results.md#check-how-the-detector-fares-on-your-library).
