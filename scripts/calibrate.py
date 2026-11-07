"""Calibrate the detector on real music.

Takes a folder of FLAC files assumed to be genuine, makes known fakes from each
of them with real encoders, analyzes everything and reports, per kind of fake,
how many were caught, plus every original that was flagged (either a false
positive or a fake already in the library: listen and look before deciding).

    uv run python scripts/calibrate.py SAMPLES_DIR WORK_DIR [--jobs N]

Needs ffmpeg (with libmp3lame and libopus), lame and oggenc on PATH. The AAC
fake uses ffmpeg's native encoder: AudioToolbox (aac_at) refused most inputs.
Fakes are cached in WORK_DIR between runs.
"""

import argparse
import csv
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from flac_inquisitor.detect import analyze_file
from flac_inquisitor.flacmeta import read_flac_info


def run(cmd: str) -> bool:
    proc = subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if proc.returncode:
        print(f"FAILED ({proc.returncode}): {cmd}\n{proc.stderr.strip()}", file=sys.stderr)
    return proc.returncode == 0


def q(path: Path) -> str:
    return "'" + str(path).replace("'", "'\\''") + "'"


AAC = "aac"

# name -> shell template; {src} is the original FLAC, {tmp} a scratch file
# prefix, {dst} the resulting fake FLAC. Lossy files are decoded back with
# ffmpeg, which honours encoder delay and padding the way players do. {ar}
# brings hi-res sources down to 48 kHz first, as streaming services deliver
# them (AudioToolbox AAC refuses 192 kHz input).
TO_WAV16 = "ffmpeg -nostdin -v error -y -i {src} {ar} -sample_fmt s16 -f wav {tmp}.wav"
BACK = "ffmpeg -nostdin -v error -y -i {tmp}.{ext} -sample_fmt s16 -c:a flac {dst}"
VARIANTS = {
    "mp3-128": (TO_WAV16 + " && lame --quiet -b 128 {tmp}.wav {tmp}.mp3", "mp3"),
    "mp3-192": (TO_WAV16 + " && lame --quiet -b 192 {tmp}.wav {tmp}.mp3", "mp3"),
    "mp3-v2": (TO_WAV16 + " && lame --quiet -V 2 {tmp}.wav {tmp}.mp3", "mp3"),
    "mp3-v0": (TO_WAV16 + " && lame --quiet -V 0 {tmp}.wav {tmp}.mp3", "mp3"),
    "mp3-320": (TO_WAV16 + " && lame --quiet -b 320 {tmp}.wav {tmp}.mp3", "mp3"),
    "aac-256": ("ffmpeg -nostdin -v error -y -i {src} {ar} -map 0:a -c:a " + AAC + " -b:a 256k {tmp}.m4a", "m4a"),
    "opus-160": ("ffmpeg -nostdin -v error -y -i {src} {ar} -map 0:a -c:a libopus -b:a 160k {tmp}.opus", "opus"),
    "vorbis-q5": (TO_WAV16 + " && oggenc --quiet -q 5 -o {tmp}.ogg {tmp}.wav", "ogg"),
    "vorbis-q9": (TO_WAV16 + " && oggenc --quiet -q 9 -o {tmp}.ogg {tmp}.wav", "ogg"),
}


def make_fakes(src: Path, work: Path) -> list[tuple[str, Path]]:
    info = read_flac_info(src)
    ar = "-ar 48000" if info.sample_rate > 48_000 else ""
    out = []
    for name, (encode, ext) in VARIANTS.items():
        dst = work / f"{src.stem}.{name}.flac"
        if not dst.exists():
            tmp = work / f"{src.stem}.{name}.tmp"
            ok = run(encode.format(src=q(src), tmp=q(tmp), ar=ar) + " && "
                     + BACK.format(tmp=q(tmp), ext=ext, dst=q(dst)))
            for leftover in work.glob(f"{src.stem}.{name}.tmp.*"):
                leftover.unlink()
            if not ok:
                dst.unlink(missing_ok=True)
                continue
        out.append((name, dst))
    if info.sample_rate <= 48_000:
        dst = work / f"{src.stem}.upsampled.flac"
        if not dst.exists():
            run(f"ffmpeg -nostdin -v error -y -i {q(src)} -ar 96000 -sample_fmt s32 -bits_per_raw_sample 24 "
                f"-c:a flac {q(dst)}")
        out.append(("upsampled", dst))
    if info.bits_per_sample == 16:
        dst = work / f"{src.stem}.padded.flac"
        if not dst.exists():
            run(f"ffmpeg -nostdin -v error -y -i {q(src)} -sample_fmt s32 -bits_per_raw_sample 24 "
                f"-c:a flac {q(dst)}")
        out.append(("padded", dst))
    return out


def analyze(item: tuple[str, str, Path]) -> dict:
    source, kind, path = item
    try:
        r = analyze_file(path)
    except Exception as exc:  # report and keep going: one bad file must not stop the run
        return {"source": source, "kind": kind, "verdict": "error", "summary": str(exc)}
    info = read_flac_info(path)
    row = {"source": source, "kind": kind, "verdict": r.verdict, "categories": ",".join(r.categories),
           "format": f"{info.sample_rate}/{info.bits_per_sample}", "summary": r.summary}
    row.update({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.metrics.items()})
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("samples", type=Path)
    parser.add_argument("work", type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)

    originals = sorted(args.samples.glob("*.flac"))
    items = [(p.name, "original", p) for p in originals]
    with ProcessPoolExecutor(args.jobs) as pool:
        for src, fakes in zip(originals, pool.map(make_fakes, originals, [args.work] * len(originals))):
            items += [(src.name, kind, path) for kind, path in fakes]
        rows = list(pool.map(analyze, items))

    keys = sorted({k for row in rows for k in row}, key=lambda k: (k not in ("source", "kind", "verdict"), k))
    with (args.work / "results.tsv").open("w", newline="") as f:
        writer = csv.DictWriter(f, keys, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    print(f"{'kind':12} {'n':>3} {'ok':>4} {'susp':>5} {'fake':>5} {'err':>4}")
    for kind in ["original", *VARIANTS, "upsampled", "padded"]:
        sub = [r for r in rows if r["kind"] == kind]
        if sub:
            count = {v: sum(r["verdict"] == v for r in sub) for v in ("ok", "suspect", "fake", "error")}
            print(f"{kind:12} {len(sub):>3} {count['ok']:>4} {count['suspect']:>5} {count['fake']:>5} {count['error']:>4}")
    print("\nOriginals flagged:")
    for r in rows:
        if r["kind"] == "original" and r["verdict"] != "ok":
            print(f"  {r['verdict']:8} {r['source']}: {r['summary']}")
    print(f"\nFull table: {args.work / 'results.tsv'}")


if __name__ == "__main__":
    sys.exit(main())
