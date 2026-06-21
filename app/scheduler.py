import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import or_, update

from .database import SessionLocal
from .config import settings
from .models import Log, Post, PostStatus, ScheduledTask
from .telegram import send_post

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="UTC")


async def deliver_post(post_id: int) -> PostStatus | None:
    db = SessionLocal()
    try:
        post = db.get(Post, post_id)
        if not post or post.status not in {
            PostStatus.SCHEDULED,
            PostStatus.PROCESSING,
            PostStatus.ERROR,
        }:
            return None
        try:
            message_ids = await send_post(post)
            post.status = PostStatus.SENT
            post.sent_at = datetime.now(timezone.utc)
            post.telegram_message_ids = json.dumps(message_ids)
            post.error_message = None
            db.add(
                Log(
                    agency_id=post.agency_id,
                    post_id=post.id,
                    level="INFO",
                    event="post_sent",
                    message="Пост успешно отправлен",
                )
            )
        except Exception as exc:
            logger.exception("Failed to send post %s", post_id)
            post.status = PostStatus.ERROR
            post.error_message = str(exc)[:2000]
            db.add(
                Log(
                    agency_id=post.agency_id,
                    post_id=post.id,
                    level="ERROR",
                    event="post_failed",
                    message=str(exc)[:4000],
                )
            )
        task = db.query(ScheduledTask).filter_by(post_id=post_id).first()
        if task:
            db.delete(task)
        db.commit()
        return post.status
    finally:
        db.close()


def _run(post_id: int) -> None:
    asyncio.run(deliver_post(post_id))


def schedule_post(db, post: Post) -> None:
    if not post.scheduled_at:
        raise ValueError("Не указано время публикации")
    run_at = post.scheduled_at
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=timezone.utc)
    if run_at <= datetime.now(timezone.utc):
        raise ValueError("Время публикации должно быть в будущем")
    job_id = f"post-{post.id}"
    if not settings.is_serverless:
        scheduler.add_job(
            _run,
            DateTrigger(run_date=run_at),
            args=[post.id],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=3600,
        )
    task = db.query(ScheduledTask).filter_by(post_id=post.id).first()
    if task:
        task.job_id, task.run_at = job_id, run_at
    else:
        db.add(ScheduledTask(post_id=post.id, job_id=job_id, run_at=run_at))
    post.status = PostStatus.SCHEDULED
    post.error_message = None


def cancel_post(db, post: Post) -> None:
    job_id = f"post-{post.id}"
    if scheduler.running and scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    task = db.query(ScheduledTask).filter_by(post_id=post.id).first()
    if task:
        db.delete(task)
    post.status = PostStatus.CANCELLED


def restore_jobs() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        posts = db.query(Post).filter(Post.status == PostStatus.SCHEDULED).all()
        for post in posts:
            run_at = post.scheduled_at
            if not run_at:
                continue
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            scheduler.add_job(
                _run,
                DateTrigger(run_date=max(run_at, now)),
                args=[post.id],
                id=f"post-{post.id}",
                replace_existing=True,
                misfire_grace_time=3600,
            )
    finally:
        db.close()


async def process_due_posts(limit: int = 20) -> dict:
    """Process due database jobs. Used by HTTP cron in serverless deployments."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        stale = now - timedelta(minutes=15)
        query = (
            db.query(Post)
            .filter(
                Post.scheduled_at <= now,
                or_(
                    Post.status == PostStatus.SCHEDULED,
                    (Post.status == PostStatus.PROCESSING) & (Post.updated_at <= stale),
                ),
            )
            .order_by(Post.scheduled_at)
            .limit(limit)
        )
        post_ids = [post.id for post in query.all()]
        claimed_ids = []
        for post_id in post_ids:
            result = db.execute(
                update(Post)
                .where(
                    Post.id == post_id,
                    or_(
                        Post.status == PostStatus.SCHEDULED,
                        (Post.status == PostStatus.PROCESSING)
                        & (Post.updated_at <= stale),
                    ),
                )
                .values(status=PostStatus.PROCESSING, updated_at=now)
            )
            if result.rowcount:
                claimed_ids.append(post_id)
        db.commit()
    finally:
        db.close()
    for post_id in claimed_ids:
        await deliver_post(post_id)
    return {"processed": len(claimed_ids), "post_ids": claimed_ids}
