# Telegram Message Scheduler

FastAPI-сервис для планирования и автоматической публикации сообщений в Telegram-каналах.

## Возможности

- роли Super Admin, Agency Admin и Manager;
- изоляция агентств, пользователей, каналов и постов;
- проверка Telegram Bot Token и доступа к каналу;
- текст, HTML, фото, видео, документы и inline-кнопки;
- черновики, расписание, отправка сразу, отмена и повтор;
- календарь, история, поиск и фильтры;
- REST API со Swagger;
- Vercel, Neon PostgreSQL и GitHub Actions cron;
- локальный режим с SQLite и APScheduler.

## Production

Рабочий deployment: https://telegram-message-scheduler.vercel.app

API: https://telegram-message-scheduler.vercel.app/docs

GitHub Actions вызывает защищённый cron endpoint каждые 5 минут. Небольшие медиа до 4 МБ сохраняются в Neon PostgreSQL. Cloudinary можно подключить опционально для более крупных объёмов.

## Локальный запуск

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

Откройте http://127.0.0.1:8000. Swagger UI доступен на `/docs`.

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `DATABASE_URL` | SQLite локально или PostgreSQL в production |
| `SECRET_KEY` | подпись cookie и JWT |
| `TOKEN_ENCRYPTION_KEY` | Fernet-шифрование Telegram Bot Token |
| `CRON_SECRET` | защита `/api/cron/process` |
| `SUPERADMIN_USERNAME` | первоначальный логин |
| `SUPERADMIN_PASSWORD` | первоначальный пароль |
| `DEFAULT_TIMEZONE` | например `Asia/Baku` |
| `MAX_UPLOAD_MB` | лимит медиа, на Vercel рекомендуется `4` |
| `CLOUDINARY_CLOUD_NAME` | опциональный Cloudinary cloud name |
| `CLOUDINARY_UPLOAD_PRESET` | опциональный unsigned preset |

Не коммитьте `.env`, пароли и токены.

## Telegram

1. Создайте бота через `@BotFather`.
2. Добавьте его администратором канала с правом публикации.
3. В панели откройте «Каналы» и добавьте Bot Token.
4. Добавьте `@username` канала или числовой `chat_id` вида `-100...`.

Bot Token сохраняется в PostgreSQL в зашифрованном виде.

## API

Основные маршруты:

- `POST /api/auth/login`
- `GET /api/channels`
- `GET/POST /api/posts`
- `PATCH/DELETE /api/posts/{id}`
- `POST /api/posts/{id}/schedule`
- `POST /api/posts/{id}/cancel`
- `POST /api/posts/{id}/send-now`
- `POST /api/posts/{id}/duplicate`
- `GET /api/history`

## Тесты

```powershell
pytest -q
```

## Деплой

Подробная инструкция: [VERCEL_DEPLOY.md](VERCEL_DEPLOY.md).

Production использует:

- Vercel Python Function;
- Neon PostgreSQL;
- GitHub Actions cron каждые 5 минут;
- GitHub Secret `CRON_SECRET`;
- опциональный Cloudinary.

## Ограничения MVP

- GitHub Actions cron может запускаться с небольшой задержкой.
- Файлы в PostgreSQL ограничены настройкой `MAX_UPLOAD_MB`.
- Для большого количества медиа рекомендуется Cloudinary/S3.
- Для сложных миграций следует добавить Alembic.
