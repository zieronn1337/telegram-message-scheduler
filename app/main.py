import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .api import router as api_router
from .auth import hash_password
from .config import settings
from .database import Base, SessionLocal, engine
from .models import Agency, User, UserRole
from .scheduler import restore_jobs, scheduler
from .web import router as web_router


def bootstrap() -> None:
    Base.metadata.create_all(engine)
    settings.uploads_path.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.role == UserRole.SUPER_ADMIN).first():
            db.add(User(username=settings.superadmin_username, password_hash=hash_password(settings.superadmin_password), role=UserRole.SUPER_ADMIN))
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
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax", https_only=settings.is_serverless, max_age=43200)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(api_router)
app.include_router(web_router)


@app.exception_handler(401)
async def unauthorized(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": str(exc.detail)}, status_code=401)
    return RedirectResponse("/login", 303)
