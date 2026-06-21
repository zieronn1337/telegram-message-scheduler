import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import authenticate, hash_password, verify_password
from .config import settings
from .database import get_db
from .models import (
    Agency,
    Log,
    Post,
    PostMedia,
    PostStatus,
    TelegramBot,
    TelegramChannel,
    User,
    UserRole,
)
from .scheduler import cancel_post, deliver_post, schedule_post
from .telegram import TelegramError, encrypt_token, verify_channel, verify_token

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def flash(request: Request, message: str, category: str = "success") -> None:
    request.session["flash"] = {"message": message, "category": category}


def viewer(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    return db.get(User, user_id) if user_id else None


def require_viewer(request: Request, db: Session) -> User | RedirectResponse:
    user = viewer(request, db)
    return user if user and user.is_active else RedirectResponse("/login", 303)


def scoped(query, model, user: User):
    if user.role != UserRole.SUPER_ADMIN:
        return query.filter(model.agency_id == user.agency_id)
    return query


def context(request: Request, user: User | None = None, **kwargs):
    return {
        "request": request,
        "current_user": user,
        "flash": request.session.pop("flash", None),
        "statuses": PostStatus,
        **kwargs,
    }


def validate_post_form(
    text: str, button_text: str, button_url: str, files: list[UploadFile] | None = None
) -> None:
    if len(text) > 4096:
        raise ValueError("Текст превышает лимит Telegram 4096 символов")
    if bool(button_text.strip()) != bool(button_url.strip()):
        raise ValueError("Для кнопки укажите одновременно текст и ссылку")
    if button_url:
        parsed = urlparse(button_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Ссылка кнопки должна начинаться с http:// или https://")
    if files is not None and len([item for item in files if item.filename]) > 10:
        raise ValueError("Можно прикрепить не более 10 файлов к одной публикации")


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "auth/login.html", context(request))


@router.post("/login")
def login(
    request: Request,
    username: str = Form(),
    password: str = Form(),
    db: Session = Depends(get_db),
):
    user = authenticate(db, username, password)
    if not user:
        flash(request, "Неверный логин или пароль", "danger")
        return RedirectResponse("/login", 303)
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/", 303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    base = scoped(db.query(Post), Post, user)
    counts = {
        status.value: base.filter(Post.status == status).count()
        for status in PostStatus
    }
    channels_count = (
        scoped(db.query(TelegramChannel), TelegramChannel, user)
        .filter(TelegramChannel.is_active.is_(True))
        .count()
    )
    upcoming = (
        base.filter(Post.status == PostStatus.SCHEDULED)
        .order_by(Post.scheduled_at)
        .limit(6)
        .all()
    )
    recent_logs = (
        scoped(db.query(Log), Log, user).order_by(Log.created_at.desc()).limit(8).all()
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        context(
            request,
            user,
            counts=counts,
            channels_count=channels_count,
            upcoming=upcoming,
            logs=recent_logs,
        ),
    )


@router.get("/channels")
def channel_list(request: Request, db: Session = Depends(get_db)):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    channels = (
        scoped(db.query(TelegramChannel), TelegramChannel, user)
        .order_by(TelegramChannel.title)
        .all()
    )
    bots = (
        scoped(db.query(TelegramBot), TelegramBot, user)
        .order_by(TelegramBot.name)
        .all()
    )
    has_active_bots = any(bot.is_active for bot in bots)
    agencies = (
        db.query(Agency).order_by(Agency.name).all()
        if user.role == UserRole.SUPER_ADMIN
        else []
    )
    return templates.TemplateResponse(
        request,
        "channels/list.html",
        context(
            request,
            user,
            channels=channels,
            bots=bots,
            has_active_bots=has_active_bots,
            agencies=agencies,
        ),
    )


@router.post("/bots")
async def bot_add(
    request: Request,
    name: str = Form(),
    token: str = Form(),
    agency_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    target_agency = (
        agency_id if user.role == UserRole.SUPER_ADMIN and agency_id else user.agency_id
    )
    if not target_agency:
        flash(request, "Сначала создайте агентство", "danger")
        return RedirectResponse("/settings", 303)
    if not db.get(Agency, target_agency):
        flash(
            request,
            "Выбранное агентство не найдено. Сначала создайте его в настройках.",
            "danger",
        )
        return RedirectResponse("/channels", 303)
    try:
        info = await verify_token(token.strip())
        existing = (
            db.query(TelegramBot)
            .filter(
                TelegramBot.agency_id == target_agency,
                TelegramBot.username == info.get("username"),
            )
            .first()
        )
        if existing:
            flash(
                request,
                f"Бот @{info.get('username')} уже подключён к этому агентству",
                "warning",
            )
            return RedirectResponse("/channels", 303)
        db.add(
            TelegramBot(
                agency_id=target_agency,
                name=name,
                username=info.get("username"),
                encrypted_token=encrypt_token(token.strip()),
            )
        )
        db.commit()
        flash(request, f"Бот @{info.get('username', name)} подключен")
    except TelegramError as exc:
        db.rollback()
        flash(request, f"Telegram не принял токен: {exc}", "danger")
    except Exception:
        db.rollback()
        flash(
            request,
            "Не удалось сохранить бота. Проверьте данные или повторите попытку позже.",
            "danger",
        )
    return RedirectResponse("/channels", 303)


@router.post("/channels")
async def channel_add(
    request: Request,
    bot_id: int = Form(),
    chat_id: str = Form(),
    title: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    bot = db.get(TelegramBot, bot_id)
    if not bot or (
        user.role != UserRole.SUPER_ADMIN and bot.agency_id != user.agency_id
    ):
        raise HTTPException(403, "Нет доступа к боту")
    try:
        from .telegram import decrypt_token

        info = await verify_channel(decrypt_token(bot), chat_id.strip())
        existing = (
            db.query(TelegramChannel)
            .filter(
                TelegramChannel.agency_id == bot.agency_id,
                TelegramChannel.chat_id == chat_id.strip(),
            )
            .first()
        )
        if existing:
            flash(request, "Этот канал уже подключён", "warning")
            return RedirectResponse("/channels", 303)
        channel = TelegramChannel(
            agency_id=bot.agency_id,
            bot_id=bot.id,
            chat_id=chat_id.strip(),
            title=title or info.get("title") or chat_id,
            verified_at=datetime.now(timezone.utc),
        )
        db.add(channel)
        db.commit()
        flash(request, "Канал проверен и добавлен")
    except TelegramError as exc:
        db.rollback()
        flash(request, f"Не удалось подключить канал: {exc}", "danger")
    except Exception:
        db.rollback()
        flash(request, "Не удалось сохранить канал. Повторите попытку позже.", "danger")
    return RedirectResponse("/channels", 303)


@router.post("/bots/{bot_id}/toggle")
def bot_toggle(bot_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    bot = db.get(TelegramBot, bot_id)
    if not bot or (
        user.role != UserRole.SUPER_ADMIN and bot.agency_id != user.agency_id
    ):
        raise HTTPException(404)
    bot.is_active = not bot.is_active
    db.commit()
    flash(request, "Статус бота изменён")
    return RedirectResponse("/channels", 303)


@router.post("/channels/{channel_id}/toggle")
def channel_toggle(channel_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    channel = db.get(TelegramChannel, channel_id)
    if not channel or (
        user.role != UserRole.SUPER_ADMIN and channel.agency_id != user.agency_id
    ):
        raise HTTPException(404)
    channel.is_active = not channel.is_active
    db.commit()
    flash(request, "Статус канала изменён")
    return RedirectResponse("/channels", 303)


@router.get("/posts")
def post_list(
    request: Request,
    status: str = "",
    channel_id: int | None = None,
    search: str = "",
    db: Session = Depends(get_db),
):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    query = scoped(db.query(Post), Post, user)
    if status:
        try:
            query = query.filter(Post.status == PostStatus(status))
        except ValueError:
            pass
    if channel_id:
        query = query.filter(Post.channel_id == channel_id)
    if search:
        query = query.filter(Post.text.ilike(f"%{search}%"))
    channels = scoped(db.query(TelegramChannel), TelegramChannel, user).all()
    return templates.TemplateResponse(
        request,
        "posts/list.html",
        context(
            request,
            user,
            posts=query.order_by(Post.created_at.desc()).all(),
            channels=channels,
            filters={"status": status, "channel_id": channel_id, "search": search},
        ),
    )


@router.get("/posts/new")
def post_new(request: Request, db: Session = Depends(get_db)):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    channels = (
        scoped(db.query(TelegramChannel), TelegramChannel, user)
        .filter(TelegramChannel.is_active.is_(True))
        .all()
    )
    return templates.TemplateResponse(
        request,
        "posts/form.html",
        context(
            request,
            user,
            post=None,
            channels=channels,
            timezones=sorted(available_timezones()),
            default_timezone=settings.default_timezone,
        ),
    )


@router.get("/posts/{post_id}/edit")
def post_edit_page(post_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    post = owned_post(db, user, post_id)
    if post.status == PostStatus.SENT:
        raise HTTPException(409, "Отправленный пост нельзя изменить")
    channels = (
        scoped(db.query(TelegramChannel), TelegramChannel, user)
        .filter(TelegramChannel.is_active.is_(True))
        .all()
    )
    local_time = ""
    if post.scheduled_at:
        aware = (
            post.scheduled_at.replace(tzinfo=timezone.utc)
            if post.scheduled_at.tzinfo is None
            else post.scheduled_at
        )
        local_time = aware.astimezone(ZoneInfo(post.timezone)).strftime(
            "%Y-%m-%dT%H:%M"
        )
    return templates.TemplateResponse(
        request,
        "posts/edit.html",
        context(
            request,
            user,
            post=post,
            channels=channels,
            timezones=sorted(available_timezones()),
            local_time=local_time,
        ),
    )


@router.post("/posts/{post_id}/edit")
def post_edit(
    post_id: int,
    request: Request,
    channel_id: int = Form(),
    text: str = Form(""),
    button_text: str = Form(""),
    button_url: str = Form(""),
    scheduled_local: str = Form(""),
    timezone_name: str = Form(settings.default_timezone),
    action: str = Form("draft"),
    db: Session = Depends(get_db),
):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    post = owned_post(db, user, post_id)
    if post.status == PostStatus.SENT:
        raise HTTPException(409, "Отправленный пост нельзя изменить")
    channel = db.get(TelegramChannel, channel_id)
    if not channel or channel.agency_id != post.agency_id:
        raise HTTPException(403)
    if not channel.is_active or not channel.bot.is_active:
        flash(request, "Выбранный канал или бот отключён", "danger")
        return RedirectResponse(f"/posts/{post_id}/edit", 303)
    if post.status == PostStatus.SCHEDULED:
        cancel_post(db, post)
    post.channel_id, post.text = channel_id, text
    post.button_text, post.button_url, post.timezone = (
        button_text or None,
        button_url or None,
        timezone_name,
    )
    try:
        validate_post_form(text, button_text, button_url)
        if not text.strip() and not post.media:
            raise ValueError("Добавьте текст или медиафайл")
        if action == "schedule":
            if not scheduled_local:
                raise ValueError("Укажите дату и время")
            post.scheduled_at = (
                datetime.fromisoformat(scheduled_local)
                .replace(tzinfo=ZoneInfo(timezone_name))
                .astimezone(timezone.utc)
            )
            schedule_post(db, post)
        else:
            post.status = PostStatus.DRAFT
            post.scheduled_at = None
        db.commit()
        flash(request, "Изменения сохранены")
    except (ValueError, ZoneInfoNotFoundError) as exc:
        db.rollback()
        flash(request, str(exc), "danger")
        return RedirectResponse(f"/posts/{post_id}/edit", 303)
    except Exception:
        db.rollback()
        flash(request, "Не удалось сохранить изменения", "danger")
        return RedirectResponse(f"/posts/{post_id}/edit", 303)
    return RedirectResponse("/posts", 303)


def save_uploads(db: Session, post: Post, files: list[UploadFile]) -> None:
    target = settings.uploads_path / str(post.agency_id) / str(post.id)
    if not settings.is_serverless:
        target.mkdir(parents=True, exist_ok=True)
    for index, upload in enumerate(files):
        if not upload.filename:
            continue
        ext = Path(upload.filename).suffix.lower()
        kind = (
            "photo"
            if (upload.content_type or "").startswith("image/")
            else "video"
            if (upload.content_type or "").startswith("video/")
            else "document"
        )
        if settings.is_serverless:
            content = upload.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
            if len(content) > settings.max_upload_mb * 1024 * 1024:
                raise ValueError(f"Файл {upload.filename} превышает лимит")
            if settings.cloudinary_cloud_name and settings.cloudinary_upload_preset:
                import httpx

                response = httpx.post(
                    f"https://api.cloudinary.com/v1_1/{settings.cloudinary_cloud_name}/auto/upload",
                    data={
                        "upload_preset": settings.cloudinary_upload_preset,
                        "folder": f"telegram-scheduler/{post.agency_id}/{post.id}",
                    },
                    files={"file": (upload.filename, content, upload.content_type)},
                    timeout=120,
                )
                response.raise_for_status()
                file_path, file_data = response.json()["secure_url"], None
            else:
                file_path, file_data = "database", content
        else:
            path = target / f"{uuid.uuid4().hex}{ext}"
            with path.open("wb") as output:
                shutil.copyfileobj(upload.file, output)
            if path.stat().st_size > settings.max_upload_mb * 1024 * 1024:
                path.unlink(missing_ok=True)
                raise ValueError(f"Файл {upload.filename} превышает лимит")
            file_path = str(path)
            file_data = None
        db.add(
            PostMedia(
                post_id=post.id,
                file_path=file_path,
                file_data=file_data,
                original_name=upload.filename,
                media_type=kind,
                mime_type=upload.content_type,
                position=index,
            )
        )


@router.post("/posts/new")
def post_create(
    request: Request,
    channel_id: int = Form(),
    text: str = Form(""),
    button_text: str = Form(""),
    button_url: str = Form(""),
    scheduled_local: str = Form(""),
    timezone_name: str = Form(settings.default_timezone),
    action: str = Form("draft"),
    media: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    channel = db.get(TelegramChannel, channel_id)
    if not channel or (
        user.role != UserRole.SUPER_ADMIN and channel.agency_id != user.agency_id
    ):
        raise HTTPException(403)
    if not channel.is_active or not channel.bot.is_active:
        flash(request, "Выбранный канал или бот отключён", "danger")
        return RedirectResponse("/posts/new", 303)
    if not text.strip() and not any(item.filename for item in media):
        flash(request, "Добавьте текст или медиафайл", "danger")
        return RedirectResponse("/posts/new", 303)
    post = Post(
        agency_id=channel.agency_id,
        channel_id=channel.id,
        author_id=user.id,
        text=text,
        button_text=button_text or None,
        button_url=button_url or None,
        timezone=timezone_name,
    )
    db.add(post)
    db.flush()
    try:
        validate_post_form(text, button_text, button_url, media)
        save_uploads(db, post, media)
        if action == "schedule":
            if not scheduled_local:
                raise ValueError("Укажите дату и время")
            post.scheduled_at = (
                datetime.fromisoformat(scheduled_local)
                .replace(tzinfo=ZoneInfo(timezone_name))
                .astimezone(timezone.utc)
            )
            schedule_post(db, post)
        elif action == "send":
            post.status = PostStatus.SCHEDULED
        db.commit()
        if action == "send":
            import asyncio

            result = asyncio.run(deliver_post(post.id))
            flash(
                request,
                "Публикация отправлена"
                if result == PostStatus.SENT
                else "Telegram не принял публикацию. Откройте историю для подробностей.",
                "success" if result == PostStatus.SENT else "danger",
            )
        else:
            flash(request, "Пост сохранён")
    except (ValueError, ZoneInfoNotFoundError) as exc:
        db.rollback()
        flash(request, str(exc), "danger")
        return RedirectResponse("/posts/new", 303)
    except Exception:
        db.rollback()
        flash(request, "Не удалось сохранить публикацию", "danger")
        return RedirectResponse("/posts/new", 303)
    return RedirectResponse("/posts", 303)


def owned_post(db: Session, user: User, post_id: int) -> Post:
    post = db.get(Post, post_id)
    if not post or (
        user.role != UserRole.SUPER_ADMIN and post.agency_id != user.agency_id
    ):
        raise HTTPException(404)
    return post


@router.post("/posts/{post_id}/action")
async def post_action(
    post_id: int, request: Request, action: str = Form(), db: Session = Depends(get_db)
):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    post = owned_post(db, user, post_id)
    if action == "cancel":
        if post.status != PostStatus.SCHEDULED:
            flash(
                request, "Отменить можно только запланированную публикацию", "warning"
            )
        else:
            cancel_post(db, post)
            db.commit()
            flash(request, "Публикация отменена")
    elif action == "send":
        if post.status in {PostStatus.SENT, PostStatus.PROCESSING}:
            flash(request, "Публикация уже отправлена или отправляется", "warning")
            return RedirectResponse("/posts", 303)
        if post.status == PostStatus.SCHEDULED:
            cancel_post(db, post)
        post.status = PostStatus.SCHEDULED
        db.commit()
        result = await deliver_post(post.id)
        flash(
            request,
            "Публикация отправлена"
            if result == PostStatus.SENT
            else "Telegram не принял публикацию. Проверьте историю.",
            "success" if result == PostStatus.SENT else "danger",
        )
    elif action == "duplicate":
        clone = Post(
            agency_id=post.agency_id,
            channel_id=post.channel_id,
            author_id=user.id,
            text=post.text,
            parse_mode=post.parse_mode,
            button_text=post.button_text,
            button_url=post.button_url,
            timezone=post.timezone,
        )
        db.add(clone)
        db.flush()
        for item in post.media:
            db.add(
                PostMedia(
                    post_id=clone.id,
                    file_path=item.file_path,
                    file_data=item.file_data,
                    original_name=item.original_name,
                    media_type=item.media_type,
                    mime_type=item.mime_type,
                    position=item.position,
                )
            )
        db.commit()
        flash(request, "Создана копия поста")
    elif action == "delete":
        if post.status in {PostStatus.SENT, PostStatus.PROCESSING}:
            flash(request, "Отправленные публикации сохраняются в истории", "warning")
        else:
            if post.status == PostStatus.SCHEDULED:
                cancel_post(db, post)
            db.query(Log).filter(Log.post_id == post.id).delete(
                synchronize_session=False
            )
            db.delete(post)
            db.commit()
            flash(request, "Публикация удалена")
    return RedirectResponse("/posts", 303)


@router.get("/calendar")
def calendar(
    request: Request,
    channel_id: int | None = None,
    status: str = "",
    db: Session = Depends(get_db),
):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    query = scoped(db.query(Post), Post, user).filter(Post.scheduled_at.is_not(None))
    if channel_id:
        query = query.filter(Post.channel_id == channel_id)
    if status:
        try:
            query = query.filter(Post.status == PostStatus(status))
        except ValueError:
            pass
    channels = scoped(db.query(TelegramChannel), TelegramChannel, user).all()
    return templates.TemplateResponse(
        request,
        "calendar.html",
        context(request, user, posts=query.all(), channels=channels),
    )


@router.get("/history")
def history(request: Request, db: Session = Depends(get_db)):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    posts = (
        scoped(db.query(Post), Post, user)
        .filter(Post.status.in_([PostStatus.SENT, PostStatus.ERROR]))
        .order_by(Post.updated_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        request, "history.html", context(request, user, posts=posts)
    )


@router.get("/users")
def users(request: Request, db: Session = Depends(get_db)):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role == UserRole.MANAGER:
        raise HTTPException(403)
    query = db.query(User)
    if user.role != UserRole.SUPER_ADMIN:
        query = query.filter(User.agency_id == user.agency_id)
    agencies = (
        db.query(Agency).order_by(Agency.name).all()
        if user.role == UserRole.SUPER_ADMIN
        else []
    )
    return templates.TemplateResponse(
        request,
        "users/list.html",
        context(request, user, users=query.all(), agencies=agencies),
    )


@router.post("/users")
def user_add(
    request: Request,
    username: str = Form(),
    password: str = Form(),
    role: UserRole = Form(),
    email: str = Form(""),
    agency_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role == UserRole.MANAGER or (
        user.role == UserRole.AGENCY_ADMIN and role == UserRole.SUPER_ADMIN
    ):
        raise HTTPException(403)
    target = agency_id if user.role == UserRole.SUPER_ADMIN else user.agency_id
    if role != UserRole.SUPER_ADMIN and (not target or not db.get(Agency, target)):
        flash(request, "Для пользователя выберите существующее агентство", "danger")
        return RedirectResponse("/users", 303)
    if role == UserRole.SUPER_ADMIN:
        target = None
    db.add(
        User(
            username=username,
            email=email or None,
            password_hash=hash_password(password),
            role=role,
            agency_id=target,
        )
    )
    try:
        db.commit()
        flash(request, "Пользователь создан")
    except Exception:
        db.rollback()
        flash(request, "Логин или email уже используется", "danger")
    return RedirectResponse("/users", 303)


@router.post("/users/{user_id}/toggle")
def user_toggle(user_id: int, request: Request, db: Session = Depends(get_db)):
    actor = require_viewer(request, db)
    if isinstance(actor, RedirectResponse):
        return actor
    target = db.get(User, user_id)
    if (
        not target
        or actor.role == UserRole.MANAGER
        or (actor.role != UserRole.SUPER_ADMIN and target.agency_id != actor.agency_id)
    ):
        raise HTTPException(404)
    if target.id == actor.id:
        flash(request, "Нельзя отключить собственную учётную запись", "warning")
    elif target.role == UserRole.SUPER_ADMIN and actor.role != UserRole.SUPER_ADMIN:
        raise HTTPException(403)
    else:
        target.is_active = not target.is_active
        db.commit()
        flash(request, "Статус пользователя изменён")
    return RedirectResponse("/users", 303)


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    agencies = (
        db.query(Agency).all()
        if user.role == UserRole.SUPER_ADMIN
        else ([db.get(Agency, user.agency_id)] if user.agency_id else [])
    )
    return templates.TemplateResponse(
        request, "settings/index.html", context(request, user, agencies=agencies)
    )


@router.post("/agencies")
def agency_add(
    request: Request,
    name: str = Form(),
    contact_info: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(403)
    db.add(Agency(name=name, contact_info=contact_info))
    try:
        db.commit()
        flash(request, "Агентство создано")
    except Exception:
        db.rollback()
        flash(request, "Агентство с таким названием уже существует", "danger")
    return RedirectResponse("/settings", 303)


@router.post("/agencies/{agency_id}/edit")
def agency_edit(
    agency_id: int,
    request: Request,
    name: str = Form(),
    contact_info: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    agency = db.get(Agency, agency_id)
    if not agency or (
        user.role != UserRole.SUPER_ADMIN and user.agency_id != agency.id
    ):
        raise HTTPException(404)
    agency.name, agency.contact_info = name.strip(), contact_info.strip() or None
    try:
        db.commit()
        flash(request, "Настройки агентства обновлены")
    except IntegrityError:
        db.rollback()
        flash(request, "Агентство с таким названием уже существует", "danger")
    return RedirectResponse("/settings", 303)


@router.post("/profile/password")
def change_password(
    request: Request,
    current_password: str = Form(),
    new_password: str = Form(),
    new_password_confirm: str = Form(),
    db: Session = Depends(get_db),
):
    user = require_viewer(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if not verify_password(current_password, user.password_hash):
        flash(request, "Текущий пароль указан неверно", "danger")
    elif len(new_password) < 10:
        flash(request, "Новый пароль должен содержать минимум 10 символов", "danger")
    elif new_password != new_password_confirm:
        flash(request, "Подтверждение нового пароля не совпадает", "danger")
    else:
        user.password_hash = hash_password(new_password)
        db.commit()
        flash(request, "Пароль успешно изменён")
    return RedirectResponse("/settings", 303)
