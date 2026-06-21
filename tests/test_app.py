import os
import asyncio
from types import SimpleNamespace
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///./test_scheduler.db"
os.environ["SECRET_KEY"] = "test-secret-that-is-longer-than-thirty-two-bytes"
os.environ["SUPERADMIN_USERNAME"] = "testadmin"
os.environ["SUPERADMIN_PASSWORD"] = "testpassword"

from fastapi.testclient import TestClient

from app.main import app
from app.auth import hash_password
from app.database import SessionLocal
from app.models import (
    Agency,
    Post,
    PostMedia,
    TelegramBot,
    TelegramChannel,
    User,
    UserRole,
)
from app.telegram import encrypt_token, send_post


def test_login_and_protected_api():
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": "testadmin", "password": "testpassword"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]
        response = client.get(
            "/api/channels", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json() == []


def test_web_login_and_dashboard():
    with TestClient(app) as client:
        response = client.post(
            "/login",
            data={"username": "testadmin", "password": "testpassword"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "Dashboard" in response.text
        for path in [
            "/channels",
            "/posts",
            "/posts/new",
            "/calendar",
            "/history",
            "/users",
            "/settings",
        ]:
            response = client.get(path)
            assert response.status_code == 200, path


def test_bot_form_rejects_unknown_agency_without_database_error():
    with TestClient(app) as client:
        client.post(
            "/login", data={"username": "testadmin", "password": "testpassword"}
        )
        response = client.post(
            "/bots",
            data={
                "name": "Test bot",
                "token": "not-sent-to-telegram",
                "agency_id": "123",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert "Выбранное агентство не найдено" in response.text
        assert "ForeignKeyViolation" not in response.text


def test_api_duplicate_copies_media():
    with TestClient(app) as client:
        db = SessionLocal()
        admin = db.query(User).filter_by(username="testadmin").one()
        agency = Agency(name="Duplicate Test Agency")
        db.add(agency)
        db.flush()
        bot = TelegramBot(
            agency_id=agency.id,
            name="Test",
            username="duplicate_test_bot",
            encrypted_token=encrypt_token("token"),
        )
        db.add(bot)
        db.flush()
        channel = TelegramChannel(
            agency_id=agency.id,
            bot_id=bot.id,
            chat_id="@duplicate_test",
            title="Duplicate test",
        )
        db.add(channel)
        db.flush()
        post = Post(
            agency_id=agency.id,
            channel_id=channel.id,
            author_id=admin.id,
            text="Original",
        )
        db.add(post)
        db.flush()
        db.add(
            PostMedia(
                post_id=post.id,
                file_path="database",
                file_data=b"image",
                original_name="test.jpg",
                media_type="photo",
                mime_type="image/jpeg",
            )
        )
        db.commit()
        post_id = post.id
        db.close()

        token = client.post(
            "/api/auth/login",
            json={"username": "testadmin", "password": "testpassword"},
        ).json()["access_token"]
        response = client.post(
            f"/api/posts/{post_id}/duplicate",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201
        db = SessionLocal()
        clone = db.get(Post, response.json()["id"])
        assert len(clone.media) == 1
        assert clone.media[0].file_data == b"image"
        db.close()


def test_mixed_media_sends_all_message_ids(monkeypatch):
    calls = []

    async def fake_api_call(token, method, **kwargs):
        calls.append(method)
        return {"message_id": len(calls)}

    monkeypatch.setattr("app.telegram.decrypt_token", lambda bot: "token")
    monkeypatch.setattr("app.telegram.api_call", fake_api_call)
    media = [
        SimpleNamespace(
            position=0,
            media_type="photo",
            original_name="a.jpg",
            mime_type="image/jpeg",
            file_data=b"a",
            file_path="database",
        ),
        SimpleNamespace(
            position=1,
            media_type="document",
            original_name="a.pdf",
            mime_type="application/pdf",
            file_data=b"b",
            file_path="database",
        ),
    ]
    post = SimpleNamespace(
        text="Test caption",
        parse_mode="HTML",
        button_text=None,
        button_url=None,
        media=media,
        channel=SimpleNamespace(
            is_active=True, chat_id="@test", bot=SimpleNamespace(is_active=True)
        ),
    )
    result = asyncio.run(send_post(post))
    assert result == [1, 2]
    assert calls == ["sendPhoto", "sendDocument"]


def test_preview_does_not_assign_untrusted_inner_html():
    script = Path("app/static/js/editor.js").read_text(encoding="utf-8")
    assert "previewText.innerHTML = text" not in script
    assert "safePreviewNode" in script


def test_manager_cannot_modify_another_agency_post():
    with TestClient(app) as client:
        db = SessionLocal()
        agency_a = Agency(name="Scoped Agency A")
        agency_b = Agency(name="Scoped Agency B")
        db.add_all([agency_a, agency_b])
        db.flush()
        manager = User(
            username="scoped-manager",
            password_hash=hash_password("manager-password"),
            role=UserRole.MANAGER,
            agency_id=agency_a.id,
        )
        admin = db.query(User).filter_by(username="testadmin").one()
        bot = TelegramBot(
            agency_id=agency_b.id,
            name="Scoped",
            username="scoped_bot",
            encrypted_token=encrypt_token("token"),
        )
        db.add_all([manager, bot])
        db.flush()
        channel = TelegramChannel(
            agency_id=agency_b.id,
            bot_id=bot.id,
            chat_id="@scoped",
            title="Scoped",
        )
        db.add(channel)
        db.flush()
        post = Post(
            agency_id=agency_b.id,
            channel_id=channel.id,
            author_id=admin.id,
            text="Private",
        )
        db.add(post)
        db.commit()
        post_id = post.id
        db.close()

        token = client.post(
            "/api/auth/login",
            json={"username": "scoped-manager", "password": "manager-password"},
        ).json()["access_token"]
        response = client.patch(
            f"/api/posts/{post_id}",
            json={"text": "Forbidden"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


def teardown_module():
    from app.database import engine

    engine.dispose()
    Path("test_scheduler.db").unlink(missing_ok=True)
