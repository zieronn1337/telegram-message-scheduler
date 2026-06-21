from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .api import router as api_router
from .auth import hash_password
from .config import settings
from .database import Base, SessionLocal, engine
from sqlalchemy import inspect, text
from .models import User, UserRole
from .scheduler import restore_jobs, scheduler
from .web import router as web_router, templates


def bootstrap() -> None:
    Base.metadata.create_all(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("post_media")}
    if "file_data" not in columns:
        column_type = "BYTEA" if engine.dialect.name == "postgresql" else "BLOB"
        with engine.begin() as connection:
            connection.execute(
                text(f"ALTER TABLE post_media ADD COLUMN file_data {column_type}")
            )
    settings.uploads_path.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.role == UserRole.SUPER_ADMIN).first():
            db.add(
                User(
                    username=settings.superadmin_username,
                    password_hash=hash_password(settings.superadmin_password),
                    role=UserRole.SUPER_ADMIN,
                )
            )
            db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    if not settings.is_serverless:
        scheduler.start()
        restore_jobs()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=settings.is_serverless,
    max_age=43200,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(api_router)
app.include_router(web_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    if exc.status_code == 401:
        return RedirectResponse("/login", 303)
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "request": request,
            "current_user": None,
            "status_code": exc.status_code,
            "message": str(exc.detail or "Произошла ошибка"),
        },
        status_code=exc.status_code,
    )
