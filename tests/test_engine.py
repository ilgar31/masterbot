from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from bot.handlers import BotEngine, IncomingMessage, normalize_phone
from bot.storage import Storage


class FakeApi:
    def __init__(self) -> None:
        self.clients: dict[str, dict[str, Any]] = {}
        self.connected: list[tuple[str, str]] = []

    def create_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        client_id = "client-1"
        self.clients[client_id] = {"id": client_id, **payload}
        return {"id": client_id}

    def list_subscriptions(self) -> list[dict[str, Any]]:
        return [{"id": "sub-1", "name": "Премиум", "price": 5000}]

    def get_client_subscription(self, client_id: str) -> dict[str, Any]:
        return {
            "status": "Активна",
            "name": "Премиум",
            "start_date": "2026-02-26",
            "end_date": "2026-08-26",
            "services_availability": [{"name": "Проф. гигиена", "remaining": 1, "total": 2}],
        }

    def connect_subscription(self, client_id: str, subscription_id: str) -> dict[str, Any]:
        self.connected.append((client_id, subscription_id))
        return {"subscription_name": "Премиум"}


class EngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = Path.cwd() / "test_data"
        self.test_dir.mkdir(exist_ok=True)
        db_path = self.test_dir / "bot_test.sqlite3"
        consent_path = self.test_dir / "consent_test.txt"
        for path in (db_path, self.test_dir / "bot_test.sqlite3-wal", self.test_dir / "bot_test.sqlite3-shm"):
            path.unlink(missing_ok=True)
        consent_path.write_text("Текст согласия", encoding="utf-8")
        self.storage = Storage(str(db_path))
        self.storage.initialize()
        self.api = FakeApi()
        self.engine = BotEngine(self.storage, self.api, str(consent_path))  # type: ignore[arg-type]

    def tearDown(self) -> None:
        for path in self.test_dir.glob("bot_test.sqlite3*"):
            path.unlink(missing_ok=True)
        (self.test_dir / "consent_test.txt").unlink(missing_ok=True)
        try:
            self.test_dir.rmdir()
        except OSError:
            pass

    def test_normalize_phone(self) -> None:
        self.assertEqual(normalize_phone("+7 900 000-00-00"), "79000000000")
        self.assertEqual(normalize_phone("8 (900) 000-00-00"), "79000000000")
        self.assertIsNone(normalize_phone("123"))

    def test_registration_and_subscription_flow(self) -> None:
        first = self.engine.handle(IncomingMessage("vk", "42", "/start"))
        self.assertIn("Текст согласия", first[0].text)

        accepted = self.engine.handle(
            IncomingMessage("vk", "42", payload={"action": "accept_consent"})
        )
        self.assertIn("Введите номер", accepted[0].text)

        phone = self.engine.handle(IncomingMessage("vk", "42", "+7 900 000-00-00"))
        self.assertIn("имя", phone[0].text.lower())

        name = self.engine.handle(IncomingMessage("vk", "42", "Иван Иванов"))
        self.assertIn("email", name[0].text.lower())

        done = self.engine.handle(
            IncomingMessage("vk", "42", payload={"action": "skip_email"})
        )
        self.assertIn("Готово", done[0].text)

        subscription = self.engine.handle(
            IncomingMessage("vk", "42", payload={"action": "my_subscription"})
        )
        self.assertIn("Премиум", subscription[0].text)
        self.assertIn("Проф. гигиена", subscription[0].text)

        choose = self.engine.handle(
            IncomingMessage(
                "vk",
                "42",
                payload={"action": "select_subscription", "subscription_id": "sub-1"},
            )
        )
        self.assertIn("Подтвердите", choose[0].text)

        connected = self.engine.handle(
            IncomingMessage(
                "vk",
                "42",
                payload={"action": "confirm_subscription", "subscription_id": "sub-1"},
            )
        )
        self.assertIn("Премиум", connected[0].text)
        self.assertEqual(self.api.connected, [("client-1", "sub-1")])


if __name__ == "__main__":
    unittest.main()
