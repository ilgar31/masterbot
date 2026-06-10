from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from bot.api_client import ApiError
from bot.handlers import BotEngine, IncomingMessage, normalize_phone
from bot.storage import Storage


CONSENT_PDF = "Пользовательское соглашение.pdf"


class FakeApi:
    def __init__(self) -> None:
        self.clients: dict[str, dict[str, Any]] = {}
        self.connected: list[tuple[str, str]] = []

    def create_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        client_id = "client-1"
        self.clients[client_id] = {"id": client_id, **payload}
        return {"id": client_id}

    def find_client_by_phone(self, phone: str) -> dict[str, Any] | None:
        for client in self.clients.values():
            if str(client.get("phone")) == phone:
                return client
        return None

    def list_subscriptions(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "sub-1",
                "name": "Премиум",
                "price": 5000,
                "description": "Для тех, кто хочет закрыть базовую профилактику заранее.",
            }
        ]

    def list_services(self) -> list[dict[str, Any]]:
        return [
            {"id": "srv-1", "description": "Проф. гигиена", "price": 3000},
            {"id": "srv-2", "description": "Лечение кариеса", "price": 4000},
        ]

    def list_subscription_services(self) -> list[dict[str, Any]]:
        return [
            {"subscription_id": "sub-1", "service_id": "srv-1", "quantity": 2},
            {"subscription_id": "sub-1", "service_id": "srv-2", "quantity": 1},
        ]

    def list_subscriptions_with_services(self) -> list[dict[str, Any]]:
        subscription = dict(self.list_subscriptions()[0])
        subscription["included_services"] = [
            {"name": "Проф. гигиена", "quantity": 2},
            {"name": "Лечение кариеса", "quantity": 1},
        ]
        return [subscription]

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


class FailingCreateApi(FakeApi):
    def find_client_by_phone(self, phone: str) -> dict[str, Any] | None:
        return None

    def create_client(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise ApiError('API вернуло 404: {"detail":"Not Found"}', status_code=404)


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
        self.engine = BotEngine(self.storage, self.api, str(consent_path), CONSENT_PDF)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        for path in self.test_dir.glob("bot_test.sqlite3*"):
            path.unlink(missing_ok=True)
        (self.test_dir / "consent_test.txt").unlink(missing_ok=True)
        try:
            self.test_dir.rmdir()
        except OSError:
            pass

    def register_user(self, engine: BotEngine | None = None) -> None:
        target = engine or self.engine
        target.handle(IncomingMessage("vk", "42", "/start"))
        target.handle(IncomingMessage("vk", "42", payload={"action": "accept_consent"}))
        target.handle(IncomingMessage("vk", "42", "+7 900 000-00-00", name="Иван Иванов"))
        target.handle(
            IncomingMessage(
                "vk",
                "42",
                payload={"action": "use_vk_name", "name": "Иван Иванов"},
                name="Иван Иванов",
            )
        )
        target.handle(IncomingMessage("vk", "42", payload={"action": "skip_email"}))

    def test_normalize_phone(self) -> None:
        self.assertEqual(normalize_phone("+7 900 000-00-00"), "79000000000")
        self.assertEqual(normalize_phone("8 (900) 000-00-00"), "79000000000")
        self.assertIsNone(normalize_phone("123"))

    def test_registration_and_payment_stub_flow(self) -> None:
        first = self.engine.handle(IncomingMessage("vk", "42", "/start"))
        self.assertIn("Master", first[0].text)
        self.assertIn("Принимаю", first[0].text)
        self.assertEqual(first[0].attachment_path, CONSENT_PDF)

        accepted = self.engine.handle(
            IncomingMessage("vk", "42", payload={"action": "accept_consent"})
        )
        self.assertIn("номер телефона", accepted[-1].text)

        phone = self.engine.handle(IncomingMessage("vk", "42", "+7 900 000-00-00", name="Иван Иванов"))
        self.assertIn("Иван Иванов", phone[0].keyboard.rows[0][0].label)

        name = self.engine.handle(
            IncomingMessage(
                "vk",
                "42",
                payload={"action": "use_vk_name", "name": "Иван Иванов"},
                name="Иван Иванов",
            )
        )
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
            IncomingMessage("vk", "42", payload={"action": "buy_subscription"})
        )
        self.assertIn("Состав подписок", choose[0].text)
        self.assertIn("Проф. гигиена", choose[0].text)
        self.assertNotIn("ЮKassa", choose[0].text)

        selected = self.engine.handle(
            IncomingMessage(
                "vk",
                "42",
                payload={"action": "select_subscription", "subscription_id": "sub-1"},
            )
        )
        self.assertIn("Перейти к оплате", selected[0].keyboard.rows[0][0].label)
        self.assertIn("Необходимо", selected[0].text)

        connected = self.engine.handle(
            IncomingMessage(
                "vk",
                "42",
                payload={"action": "request_subscription_payment", "subscription_id": "sub-1"},
            )
        )
        self.assertIn("Премиум", connected[0].text)
        self.assertIn("Оплата успешно прошла", connected[0].text)
        self.assertEqual(self.api.connected, [("client-1", "sub-1")])

    def test_registration_stops_when_create_client_404(self) -> None:
        engine = BotEngine(
            self.storage,
            FailingCreateApi(),
            str(self.test_dir / "consent_test.txt"),
            CONSENT_PDF,
        )  # type: ignore[arg-type]
        self.register_user(engine)
        user = self.storage.get_or_create_user("vk", "42")
        self.assertIsNone(user["client_id"])
        self.assertEqual(user["state"], "awaiting_email")


if __name__ == "__main__":
    unittest.main()
