from __future__ import annotations

import json
import random
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

    def send_message(self, peer_id: int, text: str, keyboard: str | None = None) -> None:
        payload: dict[str, Any] = {
            "access_token": self.token,
            "v": self.api_version,
            "peer_id": peer_id,
            "random_id": random.randint(1, 2_000_000_000),
            "message": text,
        }
        if keyboard:
            payload["keyboard"] = keyboard

        response = self.session.post(
            "https://api.vk.com/method/messages.send",
            data=payload,
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(f"VK API error: {data['error']}")


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
                self._handle_message_new(event)
                self._send_text(200, "ok")
                return

            self._send_text(200, "ok")

        def _handle_message_new(self, event: dict[str, Any]) -> None:
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
            )
            replies = engine.handle(incoming)
            for reply in replies:
                messenger.send_message(
                    peer_id=peer_id,
                    text=reply.text,
                    keyboard=to_vk_keyboard(reply.keyboard),
                )

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

