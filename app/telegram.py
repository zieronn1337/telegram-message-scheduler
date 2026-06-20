import json
import mimetypes
from pathlib import Path

import httpx

from .config import fernet
from .models import Post, TelegramBot


class TelegramError(RuntimeError):
    pass


def encrypt_token(token: str) -> str:
    return fernet.encrypt(token.encode()).decode()


def decrypt_token(bot: TelegramBot) -> str:
    return fernet.decrypt(bot.encrypted_token.encode()).decode()


async def api_call(token: str, method: str, *, data: dict | None = None, files=None) -> dict:
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
    member = await api_call(token, "getChatMember", data={"chat_id": chat_id, "user_id": me["id"]})
    if member.get("status") not in {"administrator", "creator"}:
        raise TelegramError("Бот не является администратором канала")
    return chat


def _markup(post: Post) -> str | None:
    if post.button_text and post.button_url:
        return json.dumps({"inline_keyboard": [[{"text": post.button_text, "url": post.button_url}]]})
    return None


async def send_post(post: Post) -> list[int]:
    token = decrypt_token(post.channel.bot)
    base = {"chat_id": post.channel.chat_id, "parse_mode": post.parse_mode}
    markup = _markup(post)
    media = sorted(post.media, key=lambda item: item.position)
    if not media:
        result = await api_call(token, "sendMessage", data={**base, "text": post.text, "reply_markup": markup or ""})
        return [result["message_id"]]

    if len(media) == 1:
        item = media[0]
        method, field = {
            "photo": ("sendPhoto", "photo"),
            "video": ("sendVideo", "video"),
            "document": ("sendDocument", "document"),
        }[item.media_type]
        if item.file_path.startswith("https://"):
            async with httpx.AsyncClient(timeout=120) as client:
                content = (await client.get(item.file_path)).raise_for_status().content
            file_value = (item.original_name, content, item.mime_type or "application/octet-stream")
            result = await api_call(token, method, data={**base, "caption": post.text, "reply_markup": markup or ""}, files={field: file_value})
        else:
            with open(item.file_path, "rb") as stream:
                result = await api_call(token, method, data={**base, "caption": post.text, "reply_markup": markup or ""}, files={field: (item.original_name, stream, item.mime_type or "application/octet-stream")})
        return [result["message_id"]]

    # Telegram media groups accept photos and videos. Documents are sent separately.
    group_items = [item for item in media if item.media_type in {"photo", "video"}]
    message_ids: list[int] = []
    if group_items:
        descriptors, streams, files = [], [], {}
        try:
            for index, item in enumerate(group_items[:10]):
                key = f"file{index}"
                if item.file_path.startswith("https://"):
                    async with httpx.AsyncClient(timeout=120) as client:
                        content = (await client.get(item.file_path)).raise_for_status().content
                    files[key] = (item.original_name, content, item.mime_type or "application/octet-stream")
                else:
                    stream = open(item.file_path, "rb")
                    streams.append(stream)
                    files[key] = (item.original_name, stream, item.mime_type or mimetypes.guess_type(item.file_path)[0])
                descriptor = {"type": item.media_type, "media": f"attach://{key}"}
                if index == 0:
                    descriptor.update({"caption": post.text, "parse_mode": post.parse_mode})
                descriptors.append(descriptor)
            result = await api_call(token, "sendMediaGroup", data={"chat_id": post.channel.chat_id, "media": json.dumps(descriptors)}, files=files)
            message_ids.extend(item["message_id"] for item in result)
        finally:
            for stream in streams:
                stream.close()
    for item in [item for item in media if item.media_type == "document"]:
        if item.file_path.startswith("https://"):
            async with httpx.AsyncClient(timeout=120) as client:
                content = (await client.get(item.file_path)).raise_for_status().content
            result = await api_call(token, "sendDocument", data=base, files={"document": (item.original_name, content, item.mime_type)})
        else:
            with open(item.file_path, "rb") as stream:
                result = await api_call(token, "sendDocument", data=base, files={"document": (item.original_name, stream, item.mime_type)})
            message_ids.append(result["message_id"])
    if markup:
        result = await api_call(token, "sendMessage", data={**base, "text": "↗️", "reply_markup": markup})
        message_ids.append(result["message_id"])
    return message_ids
