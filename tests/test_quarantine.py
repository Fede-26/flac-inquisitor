import errno
import json
import os
import shutil

import pytest

from flac_inquisitor import db, quarantine
from tests.test_scanner import scan, tracks


@pytest.fixture
def lib(tmp_path, samples, monkeypatch):
    root = tmp_path / "music"
    album = root / "Artist" / "Album"
    album.mkdir(parents=True)
    shutil.copy(samples["real"], album / "01. Real.flac")
    shutil.copy(samples["tape"], album / "02. Tape.flac")
    shutil.copy(samples["mp3_128"], album / "03. Fake.flac")
    (album / "03. Fake.lrc").write_text("lyrics")
    (album / "cover.jpg").write_bytes(b"jpg")
    monkeypatch.setattr(db, "DATA_PATH", tmp_path / "data")
    monkeypatch.setenv("LIBRARY_PATH", str(root))
    monkeypatch.setenv("QUARANTINE_PATH", str(tmp_path / "quarantine"))
    db.init()
    scan()
    return root


def track_id(name: str) -> int:
    return next(t["id"] for p, t in tracks().items() if p.endswith(name))


def stored(tmp_path) -> list[str]:
    q = tmp_path / "quarantine"
    return sorted(p.relative_to(q).as_posix().split("/", 1)[1] for p in q.rglob("*") if p.is_file()
                  and p.name != "manifest.json")


def test_track_moves_with_its_sidecar(lib, tmp_path):
    result = quarantine.quarantine(track_ids=[track_id("03. Fake.flac")])
    assert result["tracks"] == 1 and result["errors"] == []
    assert stored(tmp_path) == ["Artist/Album/03. Fake.flac", "Artist/Album/03. Fake.lrc"]
    assert (lib / "Artist" / "Album" / "cover.jpg").exists()
    manifest = json.loads(next((tmp_path / "quarantine").glob("*/manifest.json")).read_text())
    assert {e["original_path"] for e in manifest} == {"Artist/Album/03. Fake.flac", "Artist/Album/03. Fake.lrc"}


def test_album_moves_everything_and_removes_the_folder(lib, tmp_path):
    key = tracks()["Artist/Album/01. Real.flac"]["album_key"]
    quarantine.quarantine(album_keys=[key])
    assert not (lib / "Artist").exists()
    assert len(stored(tmp_path)) == 5


def test_restore_puts_files_back(lib):
    result = quarantine.quarantine(track_ids=[track_id("03. Fake.flac")])
    restored = quarantine.restore(result["entry_ids"])
    assert restored["tracks"] == 1
    assert (lib / "Artist" / "Album" / "03. Fake.flac").exists()
    assert (lib / "Artist" / "Album" / "03. Fake.lrc").exists()
    assert tracks()["Artist/Album/03. Fake.flac"]["missing"] == 0


def test_restore_refuses_to_overwrite(lib, samples):
    result = quarantine.quarantine(track_ids=[track_id("03. Fake.flac")])
    # The album was downloaded again with the same file names.
    shutil.copy(samples["real"], lib / "Artist" / "Album" / "03. Fake.flac")
    check = quarantine.restore(result["entry_ids"])
    assert check["restored"] == 0
    assert [c["type"] for c in check["conflicts"]] == ["path-taken"]


def test_restore_detects_same_track_under_another_name(lib, samples):
    result = quarantine.quarantine(track_ids=[track_id("03. Fake.flac")])
    shutil.copy(samples["real"], lib / "Artist" / "Album" / "03 - Fake (new).flac")
    check = quarantine.restore(result["entry_ids"])
    assert [c["type"] for c in check["conflicts"]] == ["same-track"]


def test_replace_quarantines_the_new_file_first(lib, samples, tmp_path):
    result = quarantine.quarantine(track_ids=[track_id("03. Fake.flac")])
    newer = lib / "Artist" / "Album" / "03. Fake.flac"
    shutil.copy(samples["real"], newer)
    done = quarantine.restore(result["entry_ids"], mode="replace")
    assert done["tracks"] == 1
    # The old file is back and the newer one waits in quarantine, nothing lost.
    assert newer.read_bytes() == samples["mp3_128"].read_bytes()
    assert "Artist/Album/03. Fake (2).flac" in stored(tmp_path)
    # ...and it is listed on the Quarantine page, ready to be restored.
    assert any(e["stored_path"].endswith("03. Fake (2).flac") and e["is_audio"] for e in quarantine.entries())


def test_missing_folder_needs_force(lib):
    key = tracks()["Artist/Album/01. Real.flac"]["album_key"]
    result = quarantine.quarantine(album_keys=[key])
    check = quarantine.restore(result["entry_ids"][:1])
    assert [c["type"] for c in check["conflicts"]] == ["folder-missing"]
    assert quarantine.restore(result["entry_ids"][:1], mode="force")["tracks"] == 1


def test_move_across_filesystems_copies_and_verifies(lib, tmp_path, monkeypatch):
    real_rename = os.rename

    def no_cross_device(src, dst):
        if "quarantine" in str(dst) or "quarantine" in str(src):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_rename(src, dst)

    monkeypatch.setattr(os, "rename", no_cross_device)
    src = lib / "Artist" / "Album" / "03. Fake.flac"
    data = src.read_bytes()
    quarantine.quarantine(track_ids=[track_id("03. Fake.flac")])
    assert not src.exists()
    moved = next((tmp_path / "quarantine").rglob("03. Fake.flac"))
    assert moved.read_bytes() == data


def test_delete_is_final(lib, tmp_path):
    result = quarantine.quarantine(track_ids=[track_id("03. Fake.flac")])
    quarantine.delete(result["entry_ids"])
    assert stored(tmp_path) == []
    assert quarantine.entries("deleted")


def test_copy_list(lib):
    quarantine.quarantine(track_ids=[track_id("03. Fake.flac")])
    assert quarantine.copy_list().startswith("Artist – Album")
