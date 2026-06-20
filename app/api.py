from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from .auth import authenticate, create_access_token, current_user, enforce_agency
from .database import get_db
from .models import Post, PostStatus, TelegramChannel, User
from .scheduler import cancel_post, deliver_post, process_due_posts, schedule_post
from .config import settings
from .schemas import ChannelOut, LoginRequest, PostCreate, PostOut, PostUpdate, ScheduleRequest, TokenResponse

router = APIRouter(prefix="/api", tags=["API"])


@router.get("/cron/process", include_in_schema=False)
async def cron_process(authorization: str | None = Header(None)):
    if not settings.cron_secret or authorization != f"Bearer {settings.cron_secret}":
        raise HTTPException(status_code=401, detail="Invalid cron secret")
    return await process_due_posts()


def get_post(db: Session, user: User, post_id: int) -> Post:
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(404, "Пост не найден")
    enforce_agency(user, post.agency_id)
    return post


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate(db, payload.username, payload.password)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/channels", response_model=list[ChannelOut])
def channels(user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = db.query(TelegramChannel)
    if user.agency_id is not None:
        query = query.filter(TelegramChannel.agency_id == user.agency_id)
    return query.order_by(TelegramChannel.title).all()


@router.get("/posts", response_model=list[PostOut])
def posts(
    post_status: PostStatus | None = Query(None, alias="status"),
    channel_id: int | None = None,
    search: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Post)
    if user.agency_id is not None:
        query = query.filter(Post.agency_id == user.agency_id)
    if post_status:
        query = query.filter(Post.status == post_status)
    if channel_id:
        query = query.filter(Post.channel_id == channel_id)
    if search:
        query = query.filter(Post.text.ilike(f"%{search}%"))
    return query.order_by(Post.created_at.desc()).limit(500).all()


@router.post("/posts", response_model=PostOut, status_code=201)
def create_post(payload: PostCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    channel = db.get(TelegramChannel, payload.channel_id)
    if not channel:
        raise HTTPException(404, "Канал не найден")
    enforce_agency(user, channel.agency_id)
    post = Post(
        agency_id=channel.agency_id,
        channel_id=channel.id,
        author_id=user.id,
        text=payload.text,
        button_text=payload.button_text,
        button_url=str(payload.button_url) if payload.button_url else None,
        timezone=payload.timezone,
        scheduled_at=payload.scheduled_at,
    )
    db.add(post)
    db.flush()
    if payload.scheduled_at:
        try:
            schedule_post(db, post)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    db.commit()
    db.refresh(post)
    return post


@router.patch("/posts/{post_id}", response_model=PostOut)
def update_post(post_id: int, payload: PostUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    post = get_post(db, user, post_id)
    if post.status == PostStatus.SENT:
        raise HTTPException(409, "Отправленный пост нельзя изменить")
    values = payload.model_dump(exclude_unset=True)
    if "button_url" in values and values["button_url"]:
        values["button_url"] = str(values["button_url"])
    if "channel_id" in values:
        channel = db.get(TelegramChannel, values["channel_id"])
        if not channel:
            raise HTTPException(404, "Канал не найден")
        enforce_agency(user, channel.agency_id)
    for key, value in values.items():
        setattr(post, key, value)
    if post.status == PostStatus.SCHEDULED:
        try:
            schedule_post(db, post)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
    db.commit()
    db.refresh(post)
    return post


@router.delete("/posts/{post_id}", status_code=204)
def delete_post(post_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    post = get_post(db, user, post_id)
    if post.status == PostStatus.SCHEDULED:
        cancel_post(db, post)
    db.delete(post)
    db.commit()


@router.post("/posts/{post_id}/schedule", response_model=PostOut)
def schedule(post_id: int, payload: ScheduleRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    post = get_post(db, user, post_id)
    post.scheduled_at, post.timezone = payload.scheduled_at, payload.timezone
    try:
        schedule_post(db, post)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    db.commit()
    db.refresh(post)
    return post


@router.post("/posts/{post_id}/cancel", response_model=PostOut)
def cancel(post_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    post = get_post(db, user, post_id)
    cancel_post(db, post)
    db.commit()
    db.refresh(post)
    return post


@router.post("/posts/{post_id}/send-now", status_code=202)
async def send_now(post_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    post = get_post(db, user, post_id)
    if post.status == PostStatus.SCHEDULED:
        cancel_post(db, post)
        post.status = PostStatus.SCHEDULED
        db.commit()
    elif post.status in {PostStatus.DRAFT, PostStatus.ERROR, PostStatus.CANCELLED}:
        post.status = PostStatus.SCHEDULED
        db.commit()
    await deliver_post(post.id)
    return {"detail": "Отправка выполнена"}


@router.post("/posts/{post_id}/duplicate", response_model=PostOut, status_code=201)
def duplicate(post_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    source = get_post(db, user, post_id)
    clone = Post(agency_id=source.agency_id, channel_id=source.channel_id, author_id=user.id, text=source.text, parse_mode=source.parse_mode, button_text=source.button_text, button_url=source.button_url, timezone=source.timezone)
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return clone


@router.get("/history", response_model=list[PostOut])
def history(user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = db.query(Post).filter(Post.status.in_([PostStatus.SENT, PostStatus.ERROR]))
    if user.agency_id is not None:
        query = query.filter(Post.agency_id == user.agency_id)
    return query.order_by(Post.updated_at.desc()).limit(500).all()
