# Полный деплой Telegram Message Scheduler на Vercel

Эта инструкция рассчитана на первый деплой. Выполняйте шаги по порядку: GitHub → PostgreSQL → Vercel → cron → Telegram. Cloudinary опционален.

## Что понадобится

Заранее создайте аккаунты:

- [GitHub](https://github.com) — хранение кода;
- [Vercel](https://vercel.com) — запуск FastAPI;
- [Neon](https://neon.tech) или [Supabase](https://supabase.com) — PostgreSQL;
- [Cloudinary](https://cloudinary.com) — опциональное внешнее хранение медиа;
- [cron-job.org](https://cron-job.org) — минутный cron, если ваш тариф Vercel не поддерживает нужную частоту;
- Telegram и `@BotFather` — создание бота.

## Почему нужны внешние сервисы

Vercel запускает FastAPI как serverless-функцию. Между HTTP-запросами процесс может завершаться, поэтому:

- SQLite на диске Vercel не подходит для постоянных данных;
- файлы из локальной папки не являются постоянным хранилищем;
- APScheduler не может надёжно работать как бесконечный фоновый процесс.

Проект уже адаптирован:

- PostgreSQL хранит пользователей, каналы и публикации;
- Neon хранит небольшие медиа; Cloudinary можно подключить позднее;
- cron вызывает `/api/cron/process` и запускает готовые публикации;
- `api/index.py` экспортирует FastAPI-приложение для Vercel;
- `vercel.json` отправляет все маршруты в Python Function.

---

## Шаг 1. Проверить проект локально

В PowerShell откройте папку проекта:

```powershell
cd "C:\Users\user\Desktop\5\проект\планировщик"
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
pytest -q
uvicorn app.main:app --reload
```

Проверьте:

- http://127.0.0.1:8000 открывает панель;
- http://127.0.0.1:8000/docs открывает Swagger UI;
- вход работает с логином и паролем из локального `.env`.

Остановить сервер: `Ctrl+C`.

---

## Шаг 2. Создать GitHub-репозиторий

На GitHub:

1. Нажмите **New repository**.
2. Название: например `telegram-message-scheduler`.
3. Выберите **Private**, если не хотите публиковать исходный код.
4. Не добавляйте README, `.gitignore` и license через GitHub — они уже есть локально.
5. Нажмите **Create repository**.

Для этой рабочей копии отдельный Git-репозиторий уже создан в папке `планировщик`. Проверьте его корень:

```powershell
git rev-parse --show-toplevel
```

Результат должен заканчиваться на `проект/планировщик`, а не указывать на `C:/Users/user`. Затем выполните команды, подставив свой GitHub username:

```powershell
git add .
git commit -m "Initial Telegram Scheduler release"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/telegram-message-scheduler.git
git push -u origin main
```

Перед `git push` проверьте:

```powershell
git status
git ls-files | Select-String -Pattern "\.env$|scheduler\.db$|uploads/"
```

Команда не должна показывать `.env`, `scheduler.db` и пользовательские загрузки. Они исключены в `.gitignore`.

Если Git попросит авторизацию, войдите через окно браузера или используйте GitHub Personal Access Token вместо пароля.

---

## Шаг 3. Создать PostgreSQL

Выберите один вариант. Для Vercel рекомендуется serverless connection string или pooler.

### Вариант A: Neon

1. Откройте https://console.neon.tech.
2. Нажмите **New project**.
3. Выберите ближайший регион.
4. После создания откройте **Dashboard → Connect**.
5. Выберите pooled/serverless connection, если интерфейс предлагает этот вариант.
6. Скопируйте строку вида:

```text
postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require
```

Сохраните её временно в менеджере паролей. Это значение `DATABASE_URL`.

### Вариант B: Supabase

1. Создайте проект на https://supabase.com/dashboard.
2. Откройте **Project Settings → Database** или кнопку **Connect**.
3. Найдите connection string для SQLAlchemy/Postgres.
4. Для serverless выберите pooler connection string.
5. Подставьте пароль базы, если интерфейс оставил placeholder.

Строку запишите в `DATABASE_URL` без кавычек.

Приложение автоматически создаст таблицы при первом успешном запуске. Отдельно запускать SQL-файл не нужно.

---

## Шаг 4. Настроить Cloudinary

Cloudinary опционален. Без него медиа до `MAX_UPLOAD_MB` сохраняются в Neon PostgreSQL.

1. Откройте Cloudinary Console.
2. На Dashboard найдите **Cloud name**.
3. Скопируйте его — это `CLOUDINARY_CLOUD_NAME`.
4. Откройте **Settings → Upload → Upload presets**.
5. Нажмите **Add upload preset**.
6. Выберите режим **Unsigned**.
7. Укажите уникальное имя, например `telegram_scheduler`.
8. Сохраните preset.
9. Имя preset — это `CLOUDINARY_UPLOAD_PRESET`.

Cloudinary API Secret в этот проект добавлять не нужно. Никогда не помещайте API Secret в GitHub.

Текущая форма отправляет файл через Vercel Function, поэтому для Vercel задайте `MAX_UPLOAD_MB=4`. Для крупных видео позже потребуется прямая загрузка браузер → Cloudinary.

---

## Шаг 5. Сгенерировать секреты

В активированном Python-окружении выполните:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Используйте:

1. первую случайную строку как `SECRET_KEY`;
2. вторую случайную строку как `CRON_SECRET`;
3. Fernet-строку как `TOKEN_ENCRYPTION_KEY`.

Храните значения в менеджере паролей. `TOKEN_ENCRYPTION_KEY` нельзя менять после подключения Telegram-ботов без повторного ввода их токенов.

---

## Шаг 6. Создать проект Vercel

1. Откройте https://vercel.com/new.
2. Подключите GitHub, если он ещё не подключён.
3. Найдите `telegram-message-scheduler` и нажмите **Import**.
4. В поле **Framework Preset** выберите `Other`.
5. **Root Directory** оставьте корнем репозитория.
6. Не задавайте собственные Build Command и Output Directory.
7. До нажатия Deploy раскройте **Environment Variables**.

## Шаг 7. Добавить Environment Variables в Vercel

Если проект уже создан: **Vercel Dashboard → Project → Settings → Environment Variables**.

Добавьте следующие переменные:

| Name | Value |
|---|---|
| `DATABASE_URL` | connection string из Neon или Supabase |
| `SECRET_KEY` | первая случайная строка |
| `TOKEN_ENCRYPTION_KEY` | Fernet-ключ |
| `CRON_SECRET` | вторая случайная строка |
| `CLOUDINARY_CLOUD_NAME` | Cloud name из Cloudinary |
| `CLOUDINARY_UPLOAD_PRESET` | имя unsigned preset |
| `SUPERADMIN_USERNAME` | ваш начальный логин |
| `SUPERADMIN_PASSWORD` | сильный пароль, минимум 12–16 символов |
| `DEFAULT_TIMEZONE` | `Asia/Baku` или другой IANA timezone |
| `MAX_UPLOAD_MB` | `4` |

Для каждой переменной включите минимум **Production**. Если будете тестировать Preview deployment, включите также **Preview**.

Не добавляйте:

- кавычки вокруг значений;
- пробел в конце connection string;
- Telegram Bot Token — он вводится позже в самой панели;
- переменную `VERCEL` — Vercel создаёт её самостоятельно.

После добавления или изменения переменных всегда выполняйте новый **Redeploy**: старый deployment не получает новые значения автоматически.

---

## Шаг 8. Выполнить первый деплой

1. Нажмите **Deploy**.
2. Дождитесь статуса **Ready**.
3. Откройте выданный домен, например:

```text
https://telegram-message-scheduler.vercel.app
```

4. Войдите через `SUPERADMIN_USERNAME` и `SUPERADMIN_PASSWORD`.
5. Откройте `/docs` и убедитесь, что Swagger UI работает.

Если получена ошибка 500:

1. откройте **Vercel Project → Logs**;
2. найдите первый traceback Python;
3. сначала проверьте `DATABASE_URL`;
4. убедитесь, что база доступна и содержит `sslmode=require`, если это требует провайдер;
5. после исправления переменной сделайте Redeploy.

---

## Шаг 9. Настроить cron

Без cron запланированные посты останутся в статусе `scheduled`.

Cron должен вызывать:

```text
GET https://YOUR_DOMAIN.vercel.app/api/cron/process
Authorization: Bearer YOUR_CRON_SECRET
```

### Вариант A: cron-job.org

Этот вариант не зависит от ограничений частоты вашего Vercel-плана.

1. Войдите на https://console.cron-job.org.
2. Нажмите **Create cronjob**.
3. Title: `Telegram Scheduler`.
4. URL: `https://YOUR_DOMAIN.vercel.app/api/cron/process`.
5. Schedule: каждую минуту.
6. Method: `GET`.
7. В расширенных настройках запроса добавьте header:

```text
Authorization: Bearer YOUR_CRON_SECRET
```

8. Сохраните задачу.
9. Запустите её вручную один раз.

Успешный ответ:

```json
{"processed":0,"post_ids":[]}
```

Ответ `401` означает, что header отсутствует либо не совпадает с `CRON_SECRET`.

### Вариант B: Vercel Cron

Доступная частота зависит от текущего Vercel-плана. Проверьте актуальные ограничения в [официальной документации Cron Jobs](https://vercel.com/docs/cron-jobs).

Для минутного cron замените `vercel.json` содержимым файла `vercel-pro-cron.example.json`, затем:

```powershell
git add vercel.json
git commit -m "Enable Vercel cron"
git push
```

Vercel создаёт новый deployment после push. При наличии `CRON_SECRET` Vercel отправляет его в заголовке Authorization при cron-вызове.

Не включайте одновременно Vercel Cron и cron-job.org на одной частоте без необходимости. Код защищён от большинства одновременных запусков, но один понятный источник cron проще контролировать.

---

## Шаг 10. Создать Telegram-бота

1. Откройте `@BotFather`.
2. Отправьте `/newbot`.
3. Укажите имя и username бота.
4. Скопируйте токен вида `123456:ABC...`.
5. Откройте нужный Telegram-канал.
6. Добавьте бота администратором.
7. Разрешите публикацию сообщений.

Bot Token не добавляется в GitHub или Vercel Environment Variables.

В развернутом приложении:

1. войдите как Super Admin;
2. откройте **Настройки** и создайте агентство;
3. при необходимости откройте **Пользователи** и создайте Agency Admin;
4. откройте **Каналы**;
5. добавьте Bot Token в форме подключения бота;
6. затем добавьте `@channel_username` или `chat_id` вида `-100...`.

Приложение проверит токен и административный доступ через Telegram Bot API.

---

## Шаг 11. Финальная проверка

Выполните весь чек-лист:

- [ ] главная страница открывается по HTTPS;
- [ ] вход Super Admin работает;
- [ ] `/docs` открывается;
- [ ] агентство создаётся;
- [ ] Telegram Bot Token успешно проверяется;
- [ ] канал добавляется без ошибки доступа;
- [ ] текстовый пост «Отправить сейчас» появляется в канале;
- [ ] небольшое изображение сохраняется и отправляется;
- [ ] тестовый пост запланирован на 2–3 минуты вперёд;
- [ ] cron возвращает HTTP 200;
- [ ] запланированный пост появляется в Telegram;
- [ ] статус меняется на `sent`;
- [ ] история отправок показывает результат.

---

## Проверка REST API

Swagger UI:

```text
https://YOUR_DOMAIN.vercel.app/docs
```

Проверка через PowerShell:

```powershell
$baseUrl = "https://YOUR_DOMAIN.vercel.app"
$body = @{
    username = "YOUR_ADMIN_USERNAME"
    password = "YOUR_ADMIN_PASSWORD"
} | ConvertTo-Json

$login = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/api/auth/login" `
    -ContentType "application/json" `
    -Body $body

$headers = @{ Authorization = "Bearer $($login.access_token)" }
Invoke-RestMethod -Uri "$baseUrl/api/channels" -Headers $headers
```

---

## Обновление приложения после деплоя

После изменения кода:

```powershell
git add .
git commit -m "Describe the change"
git push
```

Vercel автоматически создаст новый deployment. Изменения базы данных в будущем следует оформлять миграциями Alembic, а не ручным удалением production-таблиц.

---

## Частые ошибки

### `500 Internal Server Error`

- откройте Vercel Logs;
- проверьте `DATABASE_URL`;
- убедитесь, что пароль с особыми символами корректно URL-encoded;
- используйте serverless/pooler connection string;
- выполните Redeploy после изменения переменных.

### `Invalid cron secret` или HTTP 401

- header должен называться `Authorization`;
- значение должно быть `Bearer ` плюс точное значение `CRON_SECRET`;
- не добавляйте кавычки;
- после изменения секрета обновите cron-сервис и сделайте Redeploy.

### Публикация остаётся `scheduled`

- проверьте историю запусков cron;
- вызовите endpoint вручную;
- проверьте часовой пояс публикации;
- откройте Vercel Logs на момент cron-запроса.

### Ошибка Cloudinary

- проверьте `CLOUDINARY_CLOUD_NAME`;
- preset должен быть Unsigned;
- имя preset чувствительно к регистру;
- файл должен укладываться в лимит serverless-запроса.

### Бот не имеет доступа

- бот должен быть администратором именно выбранного канала;
- должно быть разрешено размещение сообщений;
- для публичного канала используйте `@username`;
- для приватного канала используйте корректный `chat_id`.

### Новый пароль Super Admin не применяется

`SUPERADMIN_PASSWORD` используется только при создании первого Super Admin. Если пользователь уже существует в постоянной PostgreSQL-базе, изменение переменной не меняет его пароль автоматически.

---

## Где находятся секреты

| Секрет | Где хранить |
|---|---|
| PostgreSQL connection string | Vercel Environment Variables |
| `SECRET_KEY` | Vercel Environment Variables |
| `TOKEN_ENCRYPTION_KEY` | Vercel Environment Variables и менеджер паролей |
| `CRON_SECRET` | Vercel Environment Variables и настройка cron |
| Cloudinary cloud/preset | Vercel Environment Variables |
| Telegram Bot Token | вводится в панели, хранится зашифрованным в PostgreSQL |
| локальные секреты | только `.env`, не GitHub |

Официальная документация:

- [FastAPI on Vercel](https://vercel.com/docs/frameworks/backend/fastapi)
- [Vercel Cron Jobs](https://vercel.com/docs/cron-jobs)
- [Environment Variables](https://vercel.com/docs/environment-variables)
- [Telegram Bot API](https://core.telegram.org/bots/api)
