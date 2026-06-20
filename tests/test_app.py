import os
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test_scheduler.db"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["SUPERADMIN_USERNAME"] = "testadmin"
os.environ["SUPERADMIN_PASSWORD"] = "testpassword"

from fastapi.testclient import TestClient

from app.main import app


def test_login_and_protected_api():
    with TestClient(app) as client:
        response = client.post("/api/auth/login", json={"username": "testadmin", "password": "testpassword"})
        assert response.status_code == 200
        token = response.json()["access_token"]
        response = client.get("/api/channels", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == []


def test_web_login_and_dashboard():
    with TestClient(app) as client:
        response = client.post("/login", data={"username": "testadmin", "password": "testpassword"}, follow_redirects=True)
        assert response.status_code == 200
        assert "Dashboard" in response.text


def teardown_module():
    from app.database import engine

    engine.dispose()
    Path("test_scheduler.db").unlink(missing_ok=True)
