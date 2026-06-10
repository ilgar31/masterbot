from __future__ import annotations

import json
from pathlib import Path
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import requests

from bot.config import Settings
from bot.handlers import BotEngine, IncomingMessage
from bot.keyboards import to_vk_keyboard


class VkMessenger:
    def __init__(self, token: str, api_version: str) -> None:
        self.token = token
        self.api_version = api_version
        self.session = requests.Session()
        self._name_cache: dict[int, str] = {}
        self._document_cache: dict[str, str] = {}

    def send_message(
        self,
        peer_id: int,
        text: str,
        keyboard: str | None = None,
        attachment_path: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "access_token": self.token,
            "v": self.api_version,
            "peer_id": peer_id,
            "random_id": random.randint(1, 2_000_000_000),
            "message": text,
        }
        if keyboard:
            payload["keyboard"] = keyboard
        if attachment_path:
            attachment = self._document_attachment(peer_id, attachment_path)
            if attachment:
                payload["attachment"] = attachment

        response = self.session.post(
            "https://api.vk.com/method/messages.send",
            data=payload,
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            error = data["error"]
            if error.get("error_code") == 912 and keyboard:
                self.send_message(
                    peer_id=peer_id,
                    text=text,
                    keyboard=None,
                    attachment_path=attachment_path,
                )
                return
            raise RuntimeError(f"VK API error: {data['error']}")

    def _document_attachment(self, peer_id: int, file_path: str) -> str | None:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return None

        cache_key = str(path.resolve())
        cached = self._document_cache.get(cache_key)
        if cached:
            return cached

        try:
            upload_server = _vk_api_call(
                self.session,
                "docs.getMessagesUploadServer",
                {
                    "access_token": self.token,
                    "v": self.api_version,
                    "type": "doc",
                    "peer_id": peer_id,
                },
            )
            if not isinstance(upload_server, dict) or not upload_server.get("upload_url"):
                raise RuntimeError(f"Unexpected upload server response: {upload_server}")

            with path.open("rb") as file_obj:
                upload_response = self.session.post(
                    str(upload_server["upload_url"]),
                    files={"file": (path.name, file_obj, "application/pdf")},
                    timeout=30,
                )
            upload_response.raise_for_status()
            upload_data = upload_response.json()
            uploaded_file = upload_data.get("file")
            if not uploaded_file:
                raise RuntimeError(f"VK document upload failed: {upload_data}")

            saved = _vk_api_call(
                self.session,
                "docs.save",
                {
                    "access_token": self.token,
                    "v": self.api_version,
                    "file": uploaded_file,
                    "title": path.stem,
                },
            )
            doc = _extract_saved_doc(saved)
            if not doc:
                raise RuntimeError(f"VK docs.save returned unexpected response: {saved}")
            access_key = doc.get("access_key")
            attachment = f"doc{doc['owner_id']}_{doc['id']}"
            if access_key:
                attachment += f"_{access_key}"
            self._document_cache[cache_key] = attachment
            return attachment
        except Exception as exc:
            print(f"Не удалось загрузить документ VK {path}: {exc}")
            return None

    def get_user_name(self, user_id: int) -> str | None:
        if user_id in self._name_cache:
            return self._name_cache[user_id]
        try:
            data = _vk_api_call(
                self.session,
                "users.get",
                {
                    "access_token": self.token,
                    "v": self.api_version,
                    "user_ids": str(user_id),
                },
            )
        except Exception:
            return None
        if not isinstance(data, list) or not data:
            return None
        profile = data[0]
        if not isinstance(profile, dict):
            return None
        full_name = " ".join(
            part.strip()
            for part in (str(profile.get("first_name") or ""), str(profile.get("last_name") or ""))
            if part.strip()
        )
        if not full_name:
            return None
        self._name_cache[user_id] = full_name
        return full_name


def _vk_api_call(
    session: requests.Session,
    method: str,
    payload: dict[str, Any],
    timeout: float = 10,
) -> Any:
    response = session.post(
        f"https://api.vk.com/method/{method}",
        data=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise RuntimeError(f"VK API error: {data['error']}")
    result = data.get("response")
    if not isinstance(result, (dict, list)):
        raise RuntimeError(f"VK API returned unexpected response for {method}: {data}")
    return result


def _parse_payload(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
    return None


def _extract_saved_doc(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        doc = data.get("doc")
        if isinstance(doc, dict) and doc.get("id") and doc.get("owner_id"):
            return doc
        if data.get("type") == "doc" and data.get("id") and data.get("owner_id"):
            return data
    if isinstance(data, list):
        for item in data:
            doc = _extract_saved_doc(item)
            if doc:
                return doc
    return None


def _extract_phone(message: dict[str, Any]) -> str | None:
    for attachment in message.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        contact = attachment.get("contact")
        if isinstance(contact, dict):
            phone = contact.get("phone") or contact.get("phone_number")
            if phone:
                return str(phone)
    return None


def handle_vk_message_event(
    event: dict[str, Any],
    engine: BotEngine,
    messenger: VkMessenger,
) -> None:
    obj = event.get("object") or {}
    message = obj.get("message") if isinstance(obj, dict) else {}
    if not isinstance(message, dict):
        return

    peer_id = int(message.get("peer_id") or message.get("from_id"))
    user_id = str(message.get("from_id") or peer_id)
    incoming = IncomingMessage(
        platform="vk",
        user_id=user_id,
        text=str(message.get("text") or ""),
        payload=_parse_payload(message.get("payload")),
        phone=_extract_phone(message),
        name=messenger.get_user_name(int(user_id)),
    )
    replies = engine.handle(incoming)
    for reply in replies:
        messenger.send_message(
            peer_id=peer_id,
            text=reply.text,
            keyboard=to_vk_keyboard(reply.keyboard),
            attachment_path=reply.attachment_path,
        )


def make_vk_handler(settings: Settings, engine: BotEngine) -> type[BaseHTTPRequestHandler]:
    if not settings.vk_group_token:
        raise RuntimeError("Не задан VK_GROUP_TOKEN.")
    messenger = VkMessenger(settings.vk_group_token, settings.vk_api_version)

    class VkCallbackHandler(BaseHTTPRequestHandler):
        server_version = "ClinicBotVK/1.0"

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send_text(200, "ok")
                return
            self._send_text(404, "not found")

        def do_POST(self) -> None:
            if self.path != "/vk/callback":
                self._send_text(404, "not found")
                return

            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length)
            try:
                event = json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_text(400, "bad json")
                return

            if settings.vk_secret_key and event.get("secret") != settings.vk_secret_key:
                self._send_text(403, "forbidden")
                return

            event_type = event.get("type")
            if event_type == "confirmation":
                if not settings.vk_confirmation_token:
                    self._send_text(500, "confirmation token is not configured")
                    return
                self._send_text(200, settings.vk_confirmation_token)
                return

            if event_type == "message_new":
                handle_vk_message_event(event, engine, messenger)
                self._send_text(200, "ok")
                return

            self._send_text(200, "ok")

        def _send_text(self, status: int, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            print("%s - %s" % (self.address_string(), format % args))

    return VkCallbackHandler


def run_vk_callback_server(settings: Settings, engine: BotEngine) -> None:
    handler = make_vk_handler(settings=settings, engine=engine)
    server = ThreadingHTTPServer((settings.host, settings.port), handler)
    print(f"VK Callback сервер запущен: http://{settings.host}:{settings.port}/vk/callback")
    server.serve_forever()


class VkLongPollRunner:
    def __init__(self, settings: Settings, engine: BotEngine) -> None:
        if not settings.vk_group_token:
            raise RuntimeError("Не задан VK_GROUP_TOKEN.")
        if not settings.vk_group_id:
            raise RuntimeError("Не задан VK_GROUP_ID. Для вашего сообщества это 238688218.")

        self.settings = settings
        self.engine = engine
        self.session = requests.Session()
        self.messenger = VkMessenger(settings.vk_group_token, settings.vk_api_version)
        self.server = ""
        self.key = ""
        self.ts = ""

    def refresh_server(self) -> None:
        data = _vk_api_call(
            self.session,
            "groups.getLongPollServer",
            {
                "access_token": self.settings.vk_group_token,
                "v": self.settings.vk_api_version,
                "group_id": self.settings.vk_group_id,
            },
        )
        self.server = str(data["server"])
        self.key = str(data["key"])
        self.ts = str(data["ts"])

    def poll_once(self) -> None:
        response = self.session.get(
            self.server,
            params={
                "act": "a_check",
                "key": self.key,
                "ts": self.ts,
                "wait": 25,
            },
            timeout=35,
        )
        response.raise_for_status()
        data = response.json()

        failed = data.get("failed")
        if failed == 1:
            self.ts = str(data["ts"])
            return
        if failed in {2, 3}:
            self.refresh_server()
            return
        if failed:
            raise RuntimeError(f"VK Long Poll failed: {data}")

        self.ts = str(data["ts"])
        updates = data.get("updates") or []
        for update in updates:
            if isinstance(update, dict) and update.get("type") == "message_new":
                handle_vk_message_event(update, self.engine, self.messenger)

    def run_forever(self) -> None:
        self.refresh_server()
        print("VK Long Poll запущен. Домен и Callback API не нужны.")
        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                print("VK Long Poll остановлен.")
                return
            except Exception as exc:
                print(f"Ошибка VK Long Poll: {exc}")
                time.sleep(5)
                self.refresh_server()


def run_vk_longpoll(settings: Settings, engine: BotEngine) -> None:
    VkLongPollRunner(settings=settings, engine=engine).run_forever()
