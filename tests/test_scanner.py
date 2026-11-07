import shutil
import subprocess

import pytest

from flac_inquisitor import db, scanner


@pytest.fixture
def library(tmp_path, samples, monkeypatch):
    lib = tmp_path / "music"
    album = lib / "Artist" / "[2020] Album"
    (album / "Disc 1").mkdir(parents=True)
    (album / "Disc 2").mkdir()
    shutil.copy(samples["real"], album / "Disc 1" / "01. Real.flac")
    shutil.copy(samples["tape"], album / "Disc 1" / "02. Tape.flac")
    shutil.copy(samples["mp3_128"], album / "Disc 2" / "01. Fake.flac")
    other = lib / "Other" / "Single"
    other.mkdir(parents=True)
    # An MP3 renamed to .flac.
    shutil.copy(samples["mp3_128"].with_suffix(".mp3"), other / "renamed.flac")

    monkeypatch.setattr(db, "DATA_PATH", tmp_path / "data")
    monkeypatch.setenv("LIBRARY_PATH", str(lib))
    monkeypatch.setenv("WORKERS", "2")
    db.init()
    return lib


def scan(full=False) -> dict:
    s = scanner.Scanner()
    s.start(full)
    s._thread.join()
    return s.status()


def tracks() -> dict[str, dict]:
    with db.connect() as conn:
        return {r["path"]: db.row_to_dict(r) for r in conn.execute("SELECT * FROM tracks")}


def test_first_scan_analyzes_everything(library):
    status = scan()
    assert (status["total"], status["analyzed"], status["cached"], status["errors"]) == (4, 4, 0, 0)
    t = tracks()
    assert t["Artist/[2020] Album/Disc 2/01. Fake.flac"]["final_verdict"] == "fake"
    assert t["Artist/[2020] Album/Disc 1/01. Real.flac"]["final_verdict"] == "ok"
    assert t["Other/Single/renamed.flac"]["categories"] == ["not-flac"]


def test_disc_folders_belong_to_the_album(library):
    scan()
    dirs = {t["album_dir"] for t in tracks().values()}
    assert dirs == {"Artist/[2020] Album", "Other/Single"}


def test_second_scan_reuses_results(library):
    scan()
    status = scan()
    # The renamed MP3 has no FLAC MD5 to recognise it by, so it is re-checked
    # (cheaply: it is rejected before any decoding).
    assert (status["cached"], status["analyzed"]) == (3, 1)


def test_retagging_does_not_trigger_analysis(library):
    scan()
    path = library / "Artist" / "[2020] Album" / "Disc 1" / "01. Real.flac"
    tmp = path.with_name("tmp.flac")
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-c", "copy", "-metadata", "TITLE=New title",
                    str(tmp)], check=True)
    tmp.replace(path)
    status = scan()
    assert status["cached"] == 3
    assert tracks()["Artist/[2020] Album/Disc 1/01. Real.flac"]["title"] == "New title"


def test_algorithm_change_reanalyzes(library, monkeypatch):
    scan()
    monkeypatch.setattr(scanner, "ALGO_VERSION", scanner.ALGO_VERSION + 1)
    status = scan()
    assert (status["cached"], status["analyzed"]) == (0, 4)


def test_moved_file_keeps_its_analysis(library):
    scan()
    src = library / "Artist" / "[2020] Album" / "Disc 2" / "01. Fake.flac"
    src.rename(library / "Other" / "Single" / "moved.flac")
    status = scan()
    assert status["cached"] == 3
    t = tracks()
    assert t["Other/Single/moved.flac"]["verdict"] == "fake"
    assert t["Artist/[2020] Album/Disc 2/01. Fake.flac"]["missing"] == 1


def test_full_rescan_ignores_cache(library):
    scan()
    status = scan(full=True)
    assert status["analyzed"] == 4


def test_album_with_a_non_flac_file_gets_final_verdicts(library):
    # Album coherence compares formats; a non-FLAC file has none.
    album = library / "Artist" / "[2020] Album" / "Disc 1"
    shutil.copy(library / "Other" / "Single" / "renamed.flac", album / "03. Renamed.flac")
    status = scan()
    assert status["message"] is None
    t = tracks()
    assert t["Artist/[2020] Album/Disc 1/03. Renamed.flac"]["final_verdict"] == "fake"
    assert t["Artist/[2020] Album/Disc 1/01. Real.flac"]["final_verdict"] == "ok"


def scan_folder(folder: str, full=False) -> dict:
    s = scanner.Scanner()
    s.start(full, folder)
    s._thread.join()
    return s.status()


def test_folder_scan_only_touches_that_folder(library):
    status = scan_folder("Artist/[2020] Album")
    # The disc subfolders belong to the album; the other artist is left alone.
    assert (status["total"], status["analyzed"]) == (3, 3)
    assert set(tracks()) == {"Artist/[2020] Album/Disc 1/01. Real.flac", "Artist/[2020] Album/Disc 1/02. Tape.flac",
                             "Artist/[2020] Album/Disc 2/01. Fake.flac"}


def test_folder_scan_marks_missing_only_inside_the_folder(library):
    scan()
    (library / "Artist" / "[2020] Album" / "Disc 2" / "01. Fake.flac").unlink()
    status = scan_folder("Artist/[2020] Album")
    assert status["cached"] == 2
    t = tracks()
    assert t["Artist/[2020] Album/Disc 2/01. Fake.flac"]["missing"] == 1
    assert t["Other/Single/renamed.flac"]["missing"] == 0


def test_folder_scan_can_force_reanalysis(library):
    scan()
    assert scan_folder("Other/Single", full=True)["analyzed"] == 1
