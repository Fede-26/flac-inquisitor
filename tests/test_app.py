import shutil

import pytest
from fastapi.testclient import TestClient

from flac_inquisitor import db
from flac_inquisitor.main import app
from flac_inquisitor.scanner import scanner


@pytest.fixture
def client(tmp_path, samples, monkeypatch):
    lib = tmp_path / "music"
    album = lib / "Artist" / "Album"
    album.mkdir(parents=True)
    shutil.copy(samples["real"], album / "01. Real.flac")
    shutil.copy(samples["tape"], album / "02. Tape.flac")
    shutil.copy(samples["mp3_128"], album / "03. Fake.flac")
    (album / "03. Fake.lrc").write_text("[00:00.00] la la")
    monkeypatch.setattr(db, "DATA_PATH", tmp_path / "data")
    monkeypatch.setenv("LIBRARY_PATH", str(lib))
    monkeypatch.setenv("QUARANTINE_PATH", str(tmp_path / "quarantine"))
    with TestClient(app) as c:
        yield c


def scan(client):
    assert client.post("/api/scan", json={}).status_code == 200
    scanner._thread.join()


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("url", ["/", "/albums", "/tracks", "/albums?verdict=all", "/quarantine", "/settings"])
def test_pages_render_before_and_after_a_scan(client, url):
    assert client.get(url).status_code == 200
    scan(client)
    assert client.get(url).status_code == 200


def test_scan_finds_the_fake_album(client):
    scan(client)
    albums = client.get("/api/albums").json()["items"]
    assert [a["verdict"] for a in albums] == ["fake"]
    page = client.get(f"/albums/{albums[0]['album_key']}")
    assert page.status_code == 200
    assert "03. Fake.flac" in page.text


def test_ignore_hides_a_track(client):
    scan(client)
    fake = client.get("/api/tracks").json()["items"][0]
    client.post("/api/ignore", json={"track_ids": [fake["id"]]})
    assert client.get("/api/tracks").json()["total"] == 0
    assert client.get("/api/summary").json()["ignored"] == 1


def test_settings_from_environment_are_locked(client):
    settings = {s["key"]: s for s in client.get("/api/settings").json()}
    assert settings["library_path"]["locked"]
    response = client.put("/api/settings", json={"values": {"library_path": "/elsewhere"}})
    assert response.status_code == 400


def test_assets_use_paths_behind_a_tls_proxy(client):
    # Behind a reverse proxy the app sees plain HTTP; absolute http:// asset
    # URLs would be blocked as mixed content on the https page.
    page = client.get("/", headers={"X-Forwarded-Proto": "https"}).text
    assert 'href="/static/style.css' in page and 'src="/static/app.js' in page


def test_scan_of_one_folder(client):
    response = client.post("/api/scan", json={"folder": "Artist/Album"})
    assert response.status_code == 200 and response.json()["folder"] == "Artist/Album"
    scanner._thread.join()
    assert client.get("/api/scan").json()["total"] == 3


@pytest.mark.parametrize("folder", ["../", "Artist/../../", "Nope"])
def test_scan_refuses_folders_outside_the_library(client, folder):
    assert client.post("/api/scan", json={"folder": folder}).status_code == 400


def test_folders_lists_two_levels(client):
    assert client.get("/api/folders").json() == ["Artist", "Artist/Album"]


def test_settings_page_marks_fields_fixed_by_the_environment(client):
    page = client.get("/settings").text
    assert "Fixed by the <code>LIBRARY_PATH</code> environment variable" in page
    assert "Fixed by the <code>WORKERS</code>" not in page


def test_cd_filter_and_badge(client, monkeypatch):
    from flac_inquisitor import cdverify

    # No network in tests: every disc is unknown to the databases.
    monkeypatch.setenv("CD_LOOKUP", "true")
    monkeypatch.setattr(cdverify, "fetch", lambda url: None)
    scan(client)
    assert client.get("/api/tracks?verdict=all&cd=not-found").json()["total"] == 3
    assert client.get("/api/tracks?verdict=all&cd=verified").json()["total"] == 0
    key = client.get("/api/albums").json()["items"][0]["album_key"]
    assert "CD verified 0/3" in client.get(f"/albums/{key}").text


def test_cd_check_can_be_turned_off(client, monkeypatch):
    from flac_inquisitor import cdverify

    monkeypatch.setenv("CD_LOOKUP", "false")
    monkeypatch.setattr(cdverify, "fetch", lambda url: pytest.fail("no lookups when turned off"))
    scan(client)
    assert client.get("/api/tracks?verdict=all&cd=unchecked").json()["total"] == 3


def test_tracks_show_their_verdict_while_the_scan_runs(client):
    scan(client)
    # As during a scan: analyzed, album coherence not computed yet.
    with db.connect() as conn:
        conn.execute("UPDATE tracks SET final_verdict = NULL, final_categories = NULL")
    verdicts = sorted(t["effective_verdict"] for t in client.get("/api/tracks?verdict=all").json()["items"])
    assert verdicts == ["fake", "ok", "ok"]


def test_accuracy_page_lists_cd_verified_tracks_flagged_anyway(client):
    scan(client)
    with db.connect() as conn:
        conn.execute("UPDATE tracks SET cd_status = 'verified'")
    report = client.get("/api/accuracy").json()
    assert report["tracks"] == 3 and report["by_verdict"]["fake"] == 1
    assert [t["name"] for t in report["flagged"]] == ["03. Fake.flac"]
    assert "03. Fake.flac" in client.get("/accuracy").text


def test_ignoring_cd_verified_tracks_keeps_the_accuracy_report(client):
    scan(client)
    with db.connect() as conn:
        conn.execute("UPDATE tracks SET cd_status = 'verified' WHERE path LIKE '%Fake.flac' OR path LIKE '%Real.flac'")
    assert client.post("/api/ignore/cd-verified").json() == {"changed": 2}
    assert client.post("/api/ignore/cd-verified").json() == {"changed": 0}
    assert client.get("/api/summary").json()["ignored"] == 2
    assert client.get("/api/tracks?cd=").json()["total"] == 0
    report = client.get("/api/accuracy").json()
    assert report["ignored"] == 2 and report["by_verdict"]["fake"] == 1
    assert [t["name"] for t in report["flagged"]] == ["03. Fake.flac"]


def test_lists_leave_cd_verified_tracks_out_by_default(client):
    scan(client)
    with db.connect() as conn:
        conn.execute("UPDATE tracks SET cd_status = 'verified'")
    assert client.get("/api/tracks").json()["total"] == 0
    assert client.get("/api/albums").json()["total"] == 0
    assert client.get("/api/tracks?cd=").json()["total"] == 1
    assert client.get("/api/tracks?cd=verified").json()["total"] == 1
    assert "03. Fake.flac" not in client.get("/tracks").text
    assert "03. Fake.flac" in client.get("/tracks?cd=").text


def test_upsampled_setting_regrades_stored_verdicts(client, samples):
    album = db.DATA_PATH.parent / "music" / "Artist" / "Album"
    shutil.copy(samples["upsampled"], album / "04. Upsampled.flac")
    scan(client)

    def upsampled_verdict():
        items = client.get("/api/tracks?verdict=all&category=upsampled").json()["items"]
        return [t["effective_verdict"] for t in items]

    # Default: upsampled is suspect at most.
    assert upsampled_verdict() == ["suspect"]
    assert client.put("/api/settings", json={"values": {"upsampled_verdict": "fake"}}).status_code == 200
    assert upsampled_verdict() == ["fake"]


def test_offset_search_runs_on_albums_that_do_not_match(client, monkeypatch):
    from flac_inquisitor import cdverify

    scan(client)
    key = client.get("/api/albums").json()["items"][0]["album_key"]
    assert client.post(f"/api/albums/{key}/offset-search").status_code == 400
    with db.connect() as conn:
        conn.execute("UPDATE tracks SET cd_status = 'mismatch'")
    assert "Try other offsets" in client.get(f"/albums/{key}").text
    monkeypatch.setattr(cdverify, "fetch", lambda url: None)
    assert client.post(f"/api/albums/{key}/offset-search").status_code == 200
    scanner._thread.join()
    assert "no shift within 2939 samples matches" in client.get("/api/activity").json()[0]["message"]
