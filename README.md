# Telegram Message Scheduler

Веб-сервис для планирования и автоматической публикации сообщений в Telegram-каналах. Подходит рекламным агентствам и командам, которым нужно управлять каналами, контентом, расписанием и историей отправок из одной панели.

## Возможности

- роли `Super Admin`, `Agency Admin`, `Manager`;
- изоляция пользователей, каналов и публикаций по агентствам;
- подключение Telegram-ботов с проверкой токена;
- проверка административного доступа бота к каналу;
- текст, HTML-форматирование, фото, видео, документы и inline-кнопки;
- медиагруппы до 10 фото или видео;
- черновики, отложенная публикация, отправка сразу и отмена;
- повторная отправка ошибочных публикаций и дублирование постов;
- календарь, история, поиск и фильтры;
- REST API и Swagger UI;
- локальный режим с SQLite и APScheduler;
- serverless-режим для Vercel с PostgreSQL, Cloudinary и HTTP cron;
- Docker-конфигурация и автоматические тесты.

## Технологии

- Python 3.11+
- FastAPI
- SQLAlchemy 2
- SQLite или PostgreSQL
- APScheduler
- Telegram Bot API
- Jinja2, Bootstrap 5, FullCalendar
- Cloudinary для production-медиа
- Docker и Vercel

## Структура проекта

```text
.
├── api/index.py                 # точка входа Vercel
├── app/
│   ├── main.py                 # создание FastAPI-приложения
│   ├── api.py                  # REST API и cron endpoint
│   ├── web.py                  # маршруты админ-панели
│   ├── models.py               # SQLAlchemy-модели
│   ├── auth.py                 # пароли, JWT, роли и доступ
│   ├── telegram.py             # Telegram Bot API
│   ├── scheduler.py            # APScheduler и serverless cron
│   ├── templates/              # HTML-шаблоны
│   └── static/                 # CSS и JavaScript
├── tests/                      # автоматические тесты
├── Dockerfile
├── docker-compose.yml
├── vercel.json
└── VERCEL_DEPLOY.md            # полный deployment tutorial
```

## Локальный запуск

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

Откройте:

- панель: http://127.0.0.1:8000
- API: http://127.0.0.1:8000/docs

Начальный логин и пароль берутся из `.env`. Значения по умолчанию предназначены только для локальной разработки.

## Конфигурация

Скопируйте `.env.example` в `.env` и измените значения:

| Переменная | Назначение | Локально | Vercel |
|---|---|---:|---:|
| `DATABASE_URL` | SQLite или PostgreSQL connection string | обязательно | обязательно |
| `SECRET_KEY` | подпись cookie и JWT | обязательно | обязательно |
| `TOKEN_ENCRYPTION_KEY` | шифрование Bot Token через Fernet | рекомендуется | обязательно |
| `SUPERADMIN_USERNAME` | первоначальный Super Admin | обязательно | обязательно |
| `SUPERADMIN_PASSWORD` | первоначальный пароль | обязательно | обязательно |
| `DEFAULT_TIMEZONE` | часовой пояс по умолчанию | обязательно | обязательно |
| `CRON_SECRET` | защита serverless cron endpoint | нет | обязательно |
| `CLOUDINARY_CLOUD_NAME` | Cloudinary cloud name | нет | для медиа |
| `CLOUDINARY_UPLOAD_PRESET` | unsigned upload preset | нет | для медиа |
| `UPLOAD_DIR` | локальная папка загрузок | опционально | не используется |
| `MAX_UPLOAD_MB` | лимит файла приложения | опционально | рекомендуется `4` |

Сгенерировать секреты:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Используйте разные случайные строки для `SECRET_KEY` и `CRON_SECRET`. После сохранения Telegram-токенов не меняйте `TOKEN_ENCRYPTION_KEY`: старые токены перестанут расшифровываться.

## Подключение Telegram

1. Откройте `@BotFather` в Telegram.
2. Выполните `/newbot` и скопируйте Bot Token.
3. Добавьте бота администратором нужного Telegram-канала.
4. Разрешите боту публикацию сообщений.
5. В панели создайте агентство и пользователя Agency Admin.
6. Откройте **Каналы → Подключить бота** и вставьте Bot Token.
7. Добавьте канал через `@username` или числовой `chat_id` вида `-100...`.

Bot Token не хранится открытым текстом: приложение шифрует его перед записью в БД.

## REST API

После запуска откройте `/docs`. Авторизация API использует Bearer JWT.

```bash
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"your-password"}'
```

Основные маршруты:

| Метод | URL | Назначение |
|---|---|---|
| `POST` | `/api/auth/login` | получить Bearer-токен |
| `GET` | `/api/channels` | список доступных каналов |
| `GET` | `/api/posts` | публикации и фильтры |
| `POST` | `/api/posts` | создать публикацию |
| `PATCH` | `/api/posts/{id}` | изменить публикацию |
| `DELETE` | `/api/posts/{id}` | удалить публикацию |
| `POST` | `/api/posts/{id}/schedule` | запланировать |
| `POST` | `/api/posts/{id}/cancel` | отменить |
| `POST` | `/api/posts/{id}/send-now` | отправить сразу |
| `POST` | `/api/posts/{id}/duplicate` | создать копию |
| `GET` | `/api/history` | история отправок |

## Docker

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Приложение будет доступно на http://127.0.0.1:8000.

## Деплой на Vercel

Vercel не должен использовать локальную SQLite, локальную папку `uploads` или постоянно работающий APScheduler. Production-конфигурация использует:

- PostgreSQL в Neon или Supabase;
- Cloudinary для медиа;
- `/api/cron/process` для обработки запланированных публикаций;
- внешний cron или Vercel Cron для регулярного вызова обработчика.

Полная пошаговая инструкция: **[VERCEL_DEPLOY.md](VERCEL_DEPLOY.md)**.

Полезные официальные ссылки:

- [FastAPI on Vercel](https://vercel.com/docs/frameworks/backend/fastapi)
- [Vercel Cron Jobs](https://vercel.com/docs/cron-jobs)
- [Vercel environment variables](https://vercel.com/docs/environment-variables)
- [Telegram Bot API](https://core.telegram.org/bots/api)

## Тесты

```powershell
pytest -q
```

Тесты проверяют API-авторизацию, защищённые маршруты и вход в веб-панель.

## Production-заметки

- Не коммитьте `.env`, токены и пароли.
- Используйте отдельную production-базу и резервное копирование.
- После изменения environment variables в Vercel выполните Redeploy.
- Для больших видео нужна прямая загрузка из браузера в Cloudinary: serverless-запросы имеют ограничение размера.
- В текущем MVP таблицы создаются автоматически. Для дальнейшей разработки рекомендуется подключить Alembic.
- Для высокой нагрузки лучше вынести отправку в отдельный worker с Celery/Redis или аналогичной очередью.

## Лицензия

Перед публичной публикацией выберите и добавьте подходящую лицензию. Если проект закрытый или коммерческий, не добавляйте MIT автоматически.
