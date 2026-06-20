import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    AGENCY_ADMIN = "agency_admin"
    MANAGER = "manager"


class PostStatus(str, enum.Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PROCESSING = "processing"
    SENT = "sent"
    ERROR = "error"
    CANCELLED = "cancelled"


class Agency(Base):
    __tablename__ = "agencies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    contact_info: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    users: Mapped[list["User"]] = relationship(back_populates="agency")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.MANAGER)
    agency_id: Mapped[int | None] = mapped_column(ForeignKey("agencies.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    agency: Mapped[Agency | None] = relationship(back_populates="users")


class TelegramBot(Base):
    __tablename__ = "telegram_bots"
    id: Mapped[int] = mapped_column(primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("agencies.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    username: Mapped[str | None] = mapped_column(String(120))
    encrypted_token: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TelegramChannel(Base):
    __tablename__ = "telegram_channels"
    id: Mapped[int] = mapped_column(primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("agencies.id"), index=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("telegram_bots.id"))
    chat_id: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bot: Mapped[TelegramBot] = relationship()


class Post(Base):
    __tablename__ = "posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    agency_id: Mapped[int] = mapped_column(ForeignKey("agencies.id"), index=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("telegram_channels.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text, default="")
    parse_mode: Mapped[str] = mapped_column(String(20), default="HTML")
    button_text: Mapped[str | None] = mapped_column(String(100))
    button_url: Mapped[str | None] = mapped_column(String(500))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[PostStatus] = mapped_column(Enum(PostStatus), default=PostStatus.DRAFT, index=True)
    telegram_message_ids: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    channel: Mapped[TelegramChannel] = relationship()
    author: Mapped[User] = relationship()
    media: Mapped[list["PostMedia"]] = relationship(back_populates="post", cascade="all, delete-orphan")


class PostMedia(Base):
    __tablename__ = "post_media"
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    file_path: Mapped[str] = mapped_column(String(500))
    original_name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(30))
    mime_type: Mapped[str | None] = mapped_column(String(120))
    position: Mapped[int] = mapped_column(Integer, default=0)
    post: Mapped[Post] = relationship(back_populates="media")


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), unique=True, index=True)
    job_id: Mapped[str] = mapped_column(String(120), unique=True)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Log(Base):
    __tablename__ = "logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    agency_id: Mapped[int | None] = mapped_column(ForeignKey("agencies.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    post_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id"))
    level: Mapped[str] = mapped_column(String(20), default="INFO")
    event: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
