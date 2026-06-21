from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .models import PostStatus, UserRole


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    chat_id: str
    is_active: bool


class PostCreate(BaseModel):
    channel_id: int
    text: str = ""
    button_text: str | None = None
    button_url: HttpUrl | None = None
    timezone: str = "UTC"
    scheduled_at: datetime | None = None


class PostUpdate(BaseModel):
    channel_id: int | None = None
    text: str | None = None
    button_text: str | None = None
    button_url: HttpUrl | None = None
    timezone: str | None = None
    scheduled_at: datetime | None = None


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    channel_id: int
    author_id: int
    text: str
    button_text: str | None
    button_url: str | None
    timezone: str
    scheduled_at: datetime | None
    sent_at: datetime | None
    status: PostStatus
    error_message: str | None
    created_at: datetime


class ScheduleRequest(BaseModel):
    scheduled_at: datetime
    timezone: str = Field(default="UTC", max_length=64)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8)
    email: str | None = None
    role: UserRole = UserRole.MANAGER
    agency_id: int | None = None
