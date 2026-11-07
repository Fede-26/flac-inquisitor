from fastapi.testclient import TestClient

from flac_inquisitor.main import app

client = TestClient(app)


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_index_renders():
    response = client.get("/")
    assert response.status_code == 200
    assert "flac-inquisitor" in response.text
