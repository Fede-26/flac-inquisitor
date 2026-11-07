# Add or change a check

This guide shows where a new check goes in the code, from the measurement to
the page. Read [Development setup](../tutorials/development.md) first.

A check has two halves:

- a **measurement**, taken while the analyzer decodes the file
  (`analyzer.py`);
- an **evaluation** that compares the measurement with thresholds and returns
  evidence (`detect.py`).

Keep them apart. The measurement should be a plain number or array; all
judgement and every threshold belong to `detect.py`.

## 1. Measure

Each file is decoded exactly once, in `analyzer.measure()`. The loop receives
the audio in chunks of 262,144 frames as `int32` samples (`native` holds them at
their real bit depth). If your check needs something the existing
`Measurements` fields do not give, add a field and compute it inside that
loop, chunk by chunk:

```python
@dataclass
class Measurements:
    ...
    my_value: float | None = None  # what it is, and its unit
```

Rules for the loop:

- **Constant memory.** Accumulate sums, counts or small per-frame arrays, never
  the whole track. A ten-minute 24/192 file is over 1 GB as `float32`.
- **No second decode.** Everything shares the one ffmpeg stream.

Many checks need no new field at all: `mid_power` (average spectrum),
`band_db` (per-frame levels of 500 Hz bands above 11 kHz) and
`block_trailing_zeros` (unused low bits per loud block) already cover a lot.

## 2. Evaluate

In `detect.py`:

1. Add the thresholds as constants near the others, with a comment that says
   where the numbers come from (calibration counts, in general terms).
2. Add the evaluation to `evaluate()`. Append one `Evidence` per check:

   ```python
   ev.append(Evidence(
       "my-check",            # check id, shown in the evidence table
       "fail",                # pass | warn | fail | info
       "One sentence a user understands: what was seen and what it means.",
       "lossy-transcode",     # category; None for pass and info
       value,                 # the measured value
       f">= {MY_THRESHOLD:g}",  # the threshold, as text
   ))
   ```

   Always append something, also when the file passes: the evidence table is
   how users judge a verdict.
3. Store the raw value in `metrics["my_value"]`, so the Accuracy page and the
   calibration script can see it.

The verdict follows from the evidence by itself (`grade()`): one `fail` makes a
track fake, two `warn` of the same category make it fake, one `warn` makes it
suspect. See [Verdicts](../explanation/verdicts.md). Use `info` for hints that
should never change a verdict.

## 3. A new category

If the check introduces a new category:

- add its label to `CATEGORY_LABELS` in `templates/macros.html`;
- list it in [Checks, categories and verdicts](../reference/checks.md).

If it should make files suspect at most, like *Upsampled* with the default
setting, see `settings.soft_categories()`.

## 4. An album-level check

Checks that compare a track with the rest of its album live in
`album_coherence()`. It receives one small dict per track. To give it a new
value, add the value to `metrics` in `evaluate()` and pass it on in
`scanner.recompute_albums()`, where the dicts are built. Album evidence only
raises an **OK** track to **Suspect**, never further.

## 5. Show it on the Accuracy page

To see how the value spreads over CD-verified tracks, add a row to
`ACCURACY_METRICS` in `queries.py`: key, label, unit, which tail matters
(`low` or `high`) and the thresholds to count against.

## 6. Bump the version and test

1. Increase `ALGO_VERSION` in `detect.py`. On the next scan every stored result
   with an older version is analyzed again. Without the bump, existing results
   never get the new check.
2. Add a test in `tests/test_detect.py`. The fixtures in `tests/conftest.py`
   build synthetic files (pink noise and fakes made from it with ffmpeg); add a
   new fixture if your fake needs another encoder or processing step.
3. Run `uv run pytest`.
4. Run the calibration on real music to set the thresholds:
   [Recalibrate the thresholds](calibrate.md).

No database migration is needed: evidence and metrics are stored as JSON.
