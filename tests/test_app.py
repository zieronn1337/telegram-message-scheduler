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


def test_bot_form_rejects_unknown_agency_without_database_error():
    with TestClient(app) as client:
        client.post("/login", data={"username": "testadmin", "password": "testpassword"})
        response = client.post(
            "/bots",
            data={"name": "Test bot", "token": "not-sent-to-telegram", "agency_id": "123"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "Выбранное агентство не найдено" in response.text
        assert "ForeignKeyViolation" not in response.text


def teardown_module():
    from app.database import engine

    engine.dispose()
    Path("test_scheduler.db").unlink(missing_ok=True)
