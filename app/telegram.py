import json
import mimetypes

import httpx

from .config import fernet
from .models import Post, TelegramBot


class TelegramError(RuntimeError):
    pass


def encrypt_token(token: str) -> str:
    return fernet.encrypt(token.encode()).decode()


def decrypt_token(bot: TelegramBot) -> str:
    return fernet.decrypt(bot.encrypted_token.encode()).decode()


async def api_call(
    token: str, method: str, *, data: dict | None = None, files=None
) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, data=data, files=files)
    try:
        payload = response.json()
    except ValueError as exc:
        raise TelegramError(f"Telegram вернул HTTP {response.status_code}") from exc
    if not payload.get("ok"):
        raise TelegramError(payload.get("description", "Неизвестная ошибка Telegram"))
    return payload["result"]


async def verify_token(token: str) -> dict:
    return await api_call(token, "getMe")


async def verify_channel(token: str, chat_id: str) -> dict:
    chat = await api_call(token, "getChat", data={"chat_id": chat_id})
    me = await verify_token(token)
    member = await api_call(
        token, "getChatMember", data={"chat_id": chat_id, "user_id": me["id"]}
    )
    if member.get("status") not in {"administrator", "creator"}:
        raise TelegramError("Бот не является администратором канала")
    return chat


def _markup(post: Post) -> str | None:
    if post.button_text and post.button_url:
        return json.dumps(
            {"inline_keyboard": [[{"text": post.button_text, "url": post.button_url}]]}
        )
    return None


async def _file_tuple(item) -> tuple[str, object, str]:
    mime_type = (
        item.mime_type
        or mimetypes.guess_type(item.original_name)[0]
        or "application/octet-stream"
    )
    if item.file_data:
        return item.original_name, item.file_data, mime_type
    if item.file_path.startswith("https://"):
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            response = await client.get(item.file_path)
            response.raise_for_status()
        return item.original_name, response.content, mime_type
    return item.original_name, open(item.file_path, "rb"), mime_type


async def send_post(post: Post) -> list[int]:
    if not post.channel.is_active:
        raise TelegramError("Канал отключён")
    if not post.channel.bot.is_active:
        raise TelegramError("Telegram-бот отключён")
    token = decrypt_token(post.channel.bot)
    base = {"chat_id": post.channel.chat_id, "parse_mode": post.parse_mode}
    markup = _markup(post)
    media = sorted(post.media, key=lambda item: item.position)
    if not media:
        if not post.text.strip():
            raise TelegramError("Пустое сообщение нельзя отправить")
        result = await api_call(
            token,
            "sendMessage",
            data={**base, "text": post.text, "reply_markup": markup or ""},
        )
        return [result["message_id"]]

    if len(media) == 1:
        item = media[0]
        method, field = {
            "photo": ("sendPhoto", "photo"),
            "video": ("sendVideo", "video"),
            "document": ("sendDocument", "document"),
        }[item.media_type]
        file_value = await _file_tuple(item)
        try:
            caption = post.text if len(post.text) <= 1024 else ""
            result = await api_call(
                token,
                method,
                data={
                    **base,
                    "caption": caption,
                    "reply_markup": markup or "" if caption else "",
                },
                files={field: file_value},
            )
        finally:
            if hasattr(file_value[1], "close"):
                file_value[1].close()
        message_ids = [result["message_id"]]
        if post.text and not caption:
            result = await api_call(
                token,
                "sendMessage",
                data={**base, "text": post.text, "reply_markup": markup or ""},
            )
            message_ids.append(result["message_id"])
        return message_ids

    # Telegram media groups accept up to 10 photos/videos per request. Documents are sent separately.
    group_items = [item for item in media if item.media_type in {"photo", "video"}]
    message_ids: list[int] = []
    text_sent = False
    markup_sent = False
    for chunk_start in range(0, len(group_items), 10):
        chunk = group_items[chunk_start : chunk_start + 10]
        if len(chunk) == 1:
            item = chunk[0]
            method, field = {
                "photo": ("sendPhoto", "photo"),
                "video": ("sendVideo", "video"),
            }[item.media_type]
            file_value = await _file_tuple(item)
            caption = post.text if not text_sent and len(post.text) <= 1024 else ""
            reply_markup = markup if caption and not markup_sent else ""
            try:
                result = await api_call(
                    token,
                    method,
                    data={
                        **base,
                        "caption": caption,
                        "reply_markup": reply_markup or "",
                    },
                    files={field: file_value},
                )
            finally:
                if hasattr(file_value[1], "close"):
                    file_value[1].close()
            message_ids.append(result["message_id"])
            text_sent = text_sent or bool(caption)
            markup_sent = markup_sent or bool(reply_markup)
            continue
        descriptors, streams, files = [], [], {}
        try:
            for index, item in enumerate(chunk):
                key = f"file{index}"
                files[key] = await _file_tuple(item)
                if hasattr(files[key][1], "close"):
                    streams.append(files[key][1])
                descriptor = {"type": item.media_type, "media": f"attach://{key}"}
                if (
                    not text_sent
                    and index == 0
                    and post.text
                    and len(post.text) <= 1024
                ):
                    descriptor.update(
                        {"caption": post.text, "parse_mode": post.parse_mode}
                    )
                    text_sent = True
                descriptors.append(descriptor)
            result = await api_call(
                token,
                "sendMediaGroup",
                data={
                    "chat_id": post.channel.chat_id,
                    "media": json.dumps(descriptors),
                },
                files=files,
            )
            message_ids.extend(item["message_id"] for item in result)
        finally:
            for stream in streams:
                stream.close()
    for item in [item for item in media if item.media_type == "document"]:
        file_value = await _file_tuple(item)
        caption = post.text if not text_sent and len(post.text) <= 1024 else ""
        reply_markup = markup if caption and not markup_sent else ""
        try:
            result = await api_call(
                token,
                "sendDocument",
                data={**base, "caption": caption, "reply_markup": reply_markup or ""},
                files={"document": file_value},
            )
        finally:
            if hasattr(file_value[1], "close"):
                file_value[1].close()
        message_ids.append(result["message_id"])
        if caption:
            text_sent = True
        if reply_markup:
            markup_sent = True
    if post.text and not text_sent:
        result = await api_call(
            token,
            "sendMessage",
            data={**base, "text": post.text, "reply_markup": markup or ""},
        )
        message_ids.append(result["message_id"])
        markup_sent = bool(markup)
    if markup and not markup_sent:
        result = await api_call(
            token, "sendMessage", data={**base, "text": "↗️", "reply_markup": markup}
        )
        message_ids.append(result["message_id"])
    return message_ids
