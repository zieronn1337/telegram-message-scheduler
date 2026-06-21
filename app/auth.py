from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from jwt import InvalidTokenError
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import User, UserRole

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=12),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = (
        db.query(User)
        .filter(User.username == username, User.is_active.is_(True))
        .first()
    )
    return user if user and verify_password(password, user.password_hash) else None


def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    user_id = request.session.get("user_id")
    if credentials:
        try:
            user_id = int(
                jwt.decode(
                    credentials.credentials, settings.secret_key, algorithms=["HS256"]
                )["sub"]
            )
        except (InvalidTokenError, KeyError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Недействительный токен",
            )
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация"
        )
    user = db.get(User, int(user_id))
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь не найден"
        )
    return user


def require_roles(*roles: UserRole):
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        return user

    return dependency


def agency_id_for(user: User, requested_agency_id: int | None = None) -> int:
    if user.role == UserRole.SUPER_ADMIN and requested_agency_id:
        return requested_agency_id
    if user.agency_id is None:
        raise HTTPException(
            status_code=400, detail="Пользователь не привязан к агентству"
        )
    return user.agency_id


def enforce_agency(user: User, agency_id: int) -> None:
    if user.role != UserRole.SUPER_ADMIN and user.agency_id != agency_id:
        raise HTTPException(
            status_code=403, detail="Нет доступа к данным другого агентства"
        )
