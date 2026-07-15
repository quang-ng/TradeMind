from admin_api.app.main import app
from fastapi.testclient import TestClient


def test_health_returns_200_ok():
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
