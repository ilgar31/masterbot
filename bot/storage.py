from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Storage:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._lock = threading.RLock()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = sqlite3.connect(self.database_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def initialize(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bot_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    platform_user_id TEXT NOT NULL,
                    phone TEXT,
                    full_name TEXT,
                    email TEXT,
                    client_id TEXT,
                    state TEXT NOT NULL DEFAULT 'new',
                    temp_json TEXT NOT NULL DEFAULT '{}',
                    consent_accepted_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(platform, platform_user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_bot_users_phone
                    ON bot_users(phone);

                CREATE TABLE IF NOT EXISTS content_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS promotions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    image_url TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS shop_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    bonus_price INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notification_templates (
                    key TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    text TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bonus_redemptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    platform_user_id TEXT NOT NULL,
                    client_id TEXT,
                    phone TEXT,
                    item_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    subject TEXT,
                    details TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._seed_defaults(conn)

    def _seed_defaults(self, conn: sqlite3.Connection) -> None:
        now = utc_now()
        settings = {
            "referral_rules": (
                "Расскажите знакомым о клинике. Актуальные условия рекомендаций можно обновить в панели управления бота."
            ),
            "support_contacts": "Поддержка: +7 000 000-00-00\nАдрес и график работы уточнит администратор клиники.",
            "empty_promotions_text": "Сейчас активных акций нет. Загляните позже: здесь появятся предложения для подписчиков.",
        }
        for key, value in settings.items():
            conn.execute(
                """
                INSERT OR IGNORE INTO content_settings(key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (key, value, now),
            )

        legacy_settings = {
            "referral_rules": "Расскажите знакомым о клинике. Правила, размер бонусов и условия активации администратор может изменить в этой админке.",
            "support_contacts": "Поддержка: +7 000 000-00-00\nАдрес и график работы можно изменить в админке.",
            "empty_promotions_text": "Сейчас активных акций нет. Мы сообщим, когда появится что-то новое.",
            "support_contacts_v2": "Поддержка: +7 000 000-00-00\nАдрес, график работы и контакты можно изменить в админке.",
        }
        for legacy_key, old_value in legacy_settings.items():
            key = "support_contacts" if legacy_key == "support_contacts_v2" else legacy_key
            conn.execute(
                """
                UPDATE content_settings
                SET value = ?, updated_at = ?
                WHERE key = ? AND value = ?
                """,
                (settings[key], now, key, old_value),
            )

        templates = {
            "registration_success": (
                "Регистрация завершена",
                "Готово, {name}! Теперь можно посмотреть подписку, выбрать абонемент и перейти к оплате.",
            ),
            "subscription_connected": (
                "Заявка на оплату абонемента",
                "✅ Оплата успешно прошла.\n\nАбонемент «{subscription}» активирован. Теперь он доступен в разделе «Моя подписка».",
            ),
            "redemption_requested": (
                "Заявка на списание бонусов",
                "Заявка по позиции «{item}» отправлена администратору.",
            ),
        }
        for key, (label, text) in templates.items():
            conn.execute(
                """
                INSERT OR IGNORE INTO notification_templates(key, label, text, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (key, label, text, now),
            )

        legacy_templates = {
            "registration_success": "Готово, {name}! Теперь можно смотреть подписку и подключать абонементы.",
            "subscription_connected": "Заявка на подключение абонемента «{subscription}» отправлена. Если потребуется уточнение, администратор свяжется с вами.",
            "subscription_connected_v2": "✅ Оплата успешно прошла.\n\nАбонемент «{subscription}» активирован. Теперь он доступен в разделе «Моя подписка».",
            "redemption_requested": "Заявка на списание бонусов по позиции «{item}» отправлена администратору.",
        }
        for legacy_key, old_text in legacy_templates.items():
            key = "subscription_connected" if legacy_key == "subscription_connected_v2" else legacy_key
            label, text = templates[key]
            conn.execute(
                """
                UPDATE notification_templates
                SET label = ?, text = ?, updated_at = ?
                WHERE key = ? AND text = ?
                """,
                (label, text, now, key, old_text),
            )

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def get_or_create_user(self, platform: str, platform_user_id: str) -> dict[str, Any]:
        now = utc_now()
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM bot_users
                WHERE platform = ? AND platform_user_id = ?
                """,
                (platform, platform_user_id),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO bot_users(platform, platform_user_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (platform, platform_user_id, now, now),
                )
                row = conn.execute(
                    """
                    SELECT * FROM bot_users
                    WHERE platform = ? AND platform_user_id = ?
                    """,
                    (platform, platform_user_id),
                ).fetchone()
            return dict(row)

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM bot_users WHERE id = ?", (user_id,)).fetchone()
            return self._row_to_dict(row)

    def find_user_by_phone(self, phone: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM bot_users
                WHERE phone = ? AND client_id IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (phone,),
            ).fetchone()
            return self._row_to_dict(row)

    def update_user(self, user_id: int, **fields: Any) -> dict[str, Any]:
        allowed = {
            "phone",
            "full_name",
            "email",
            "client_id",
            "state",
            "temp_json",
            "consent_accepted_at",
        }
        clean_fields = {key: value for key, value in fields.items() if key in allowed}
        clean_fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in clean_fields)
        values = list(clean_fields.values()) + [user_id]
        with self._connection() as conn:
            conn.execute(f"UPDATE bot_users SET {assignments} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM bot_users WHERE id = ?", (user_id,)).fetchone()
            return dict(row)

    def accept_consent(self, user_id: int, actor: str) -> dict[str, Any]:
        accepted_at = utc_now()
        user = self.update_user(user_id, consent_accepted_at=accepted_at, state="awaiting_phone")
        self.append_audit(actor=actor, action="consent.accepted", subject=str(user_id))
        return user

    def set_temp(self, user_id: int, value: dict[str, Any]) -> dict[str, Any]:
        return self.update_user(user_id, temp_json=json.dumps(value, ensure_ascii=False))

    @staticmethod
    def get_temp(user: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(user.get("temp_json") or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connection() as conn:
            row = conn.execute("SELECT value FROM content_settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO content_settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, utc_now()),
            )

    def list_promotions(self, active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM promotions"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY created_at DESC"
        with self._connection() as conn:
            rows = conn.execute(sql).fetchall()
            return [dict(row) for row in rows]

    def create_promotion(self, title: str, body: str, image_url: str = "", is_active: bool = True) -> None:
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO promotions(title, body, image_url, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (title, body, image_url, int(is_active), now, now),
            )

    def update_promotion(
        self,
        promotion_id: int,
        title: str,
        body: str,
        image_url: str = "",
        is_active: bool = True,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE promotions
                SET title = ?, body = ?, image_url = ?, is_active = ?, updated_at = ?
                WHERE id = ?
                """,
                (title, body, image_url, int(is_active), utc_now(), promotion_id),
            )

    def delete_promotion(self, promotion_id: int) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM promotions WHERE id = ?", (promotion_id,))

    def list_shop_items(self, active_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM shop_items"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY created_at DESC"
        with self._connection() as conn:
            rows = conn.execute(sql).fetchall()
            return [dict(row) for row in rows]

    def get_shop_item(self, item_id: int) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM shop_items WHERE id = ?", (item_id,)).fetchone()
            return self._row_to_dict(row)

    def create_shop_item(
        self,
        title: str,
        description: str,
        bonus_price: int,
        is_active: bool = True,
    ) -> None:
        now = utc_now()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO shop_items(title, description, bonus_price, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (title, description, bonus_price, int(is_active), now, now),
            )

    def update_shop_item(
        self,
        item_id: int,
        title: str,
        description: str,
        bonus_price: int,
        is_active: bool = True,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE shop_items
                SET title = ?, description = ?, bonus_price = ?, is_active = ?, updated_at = ?
                WHERE id = ?
                """,
                (title, description, bonus_price, int(is_active), utc_now(), item_id),
            )

    def delete_shop_item(self, item_id: int) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM shop_items WHERE id = ?", (item_id,))

    def list_notification_templates(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM notification_templates ORDER BY key").fetchall()
            return [dict(row) for row in rows]

    def get_notification_template(self, key: str, default: str = "") -> str:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT text FROM notification_templates WHERE key = ?",
                (key,),
            ).fetchone()
            return row["text"] if row else default

    def update_notification_template(self, key: str, text: str) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE notification_templates
                SET text = ?, updated_at = ?
                WHERE key = ?
                """,
                (text, utc_now(), key),
            )

    def create_redemption(
        self,
        platform: str,
        platform_user_id: str,
        client_id: str | None,
        phone: str | None,
        item_id: int,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO bonus_redemptions(platform, platform_user_id, client_id, phone, item_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (platform, platform_user_id, client_id, phone, item_id, utc_now()),
            )

    def list_redemptions(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT bonus_redemptions.*, shop_items.title AS item_title
                FROM bonus_redemptions
                LEFT JOIN shop_items ON shop_items.id = bonus_redemptions.item_id
                ORDER BY bonus_redemptions.created_at DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def append_audit(
        self,
        actor: str,
        action: str,
        subject: str | None = None,
        details: dict[str, Any] | str | None = None,
    ) -> None:
        if isinstance(details, dict):
            details_value = json.dumps(details, ensure_ascii=False)
        else:
            details_value = details
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO audit_log(actor, action, subject, details, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (actor, action, subject, details_value, utc_now()),
            )
