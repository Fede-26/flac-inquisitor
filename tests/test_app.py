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
