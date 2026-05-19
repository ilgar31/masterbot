from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.api_client import ApiError, ClinicApiClient
from bot.keyboards import (
    Keyboard,
    back_to_menu_keyboard,
    confirm_subscription_keyboard,
    consent_keyboard,
    main_menu_keyboard,
    phone_keyboard,
    promotions_keyboard,
    shop_keyboard,
    skip_email_keyboard,
    subscriptions_keyboard,
)
from bot.storage import Storage


@dataclass(frozen=True)
class IncomingMessage:
    platform: str
    user_id: str
    text: str = ""
    payload: dict[str, Any] | None = None
    phone: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class BotReply:
    text: str
    keyboard: Keyboard | None = None


TEXT_ACTIONS = {
    "/start": "start",
    "start": "start",
    "начать": "start",
    "главное меню": "menu",
    "меню": "menu",
    "моя подписка": "my_subscription",
    "подключить абонемент": "buy_subscription",
    "купить абонемент": "buy_subscription",
    "акции и скидки": "promotions",
    "акции": "promotions",
    "магазин бонусов": "bonus_shop",
    "правила рекомендаций": "referral_rules",
    "помощь": "support",
    "поддержка": "support",
    "пропустить email": "skip_email",
}


def normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D+", "", value)
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return digits
    return None


def display_phone(phone: str | None) -> str:
    if not phone or len(phone) != 11:
        return phone or ""
    return f"+{phone[0]} {phone[1:4]} {phone[4:7]}-{phone[7:9]}-{phone[9:11]}"


def pick_id(data: dict[str, Any]) -> str | None:
    for key in ("id", "client_id", "uuid", "pk"):
        value = data.get(key)
        if value:
            return str(value)
    nested = data.get("client")
    if isinstance(nested, dict):
        return pick_id(nested)
    return None


def safe_format(template: str, **values: Any) -> str:
    class SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    return template.format_map(SafeDict(values))


def first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


class BotEngine:
    def __init__(self, storage: Storage, api: ClinicApiClient, consent_file: str) -> None:
        self.storage = storage
        self.api = api
        self.consent_file = consent_file

    def handle(self, message: IncomingMessage) -> list[BotReply]:
        user = self.storage.get_or_create_user(message.platform, message.user_id)
        action = self._action(message)

        if action == "start":
            return self._start(user)

        if not user.get("consent_accepted_at"):
            return self._consent_flow(user, message, action)

        if not user.get("phone") or not user.get("client_id"):
            return self._auth_flow(user, message, action)

        return self._menu_flow(user, message, action)

    def _action(self, message: IncomingMessage) -> str | None:
        if message.payload and isinstance(message.payload, dict):
            action = message.payload.get("action")
            if isinstance(action, str):
                return action
        normalized = " ".join((message.text or "").strip().casefold().split())
        return TEXT_ACTIONS.get(normalized)

    def _start(self, user: dict[str, Any]) -> list[BotReply]:
        if not user.get("consent_accepted_at"):
            return [BotReply(self._consent_text(), consent_keyboard())]
        if not user.get("phone") or not user.get("client_id"):
            self.storage.update_user(user["id"], state="awaiting_phone")
            return [BotReply(self._ask_phone_text(), phone_keyboard())]
        return [self._main_menu_reply("Здравствуйте! Главное меню уже открыто.")]

    def _consent_text(self) -> str:
        try:
            return Path(self.consent_file).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return (
                "Для работы бота нужно согласие на обработку персональных данных. "
                "Текст согласия хранится в отдельном файле consent.txt."
            )

    def _consent_flow(
        self,
        user: dict[str, Any],
        message: IncomingMessage,
        action: str | None,
    ) -> list[BotReply]:
        if action == "accept_consent":
            actor = f"{message.platform}:{message.user_id}"
            self.storage.accept_consent(user["id"], actor=actor)
            return [BotReply(self._ask_phone_text(), phone_keyboard())]

        if action == "decline_consent":
            return [
                BotReply(
                    "Без согласия бот не может хранить профиль и показывать данные подписки. "
                    "Если передумаете, нажмите /start.",
                    consent_keyboard(),
                )
            ]

        return [BotReply(self._consent_text(), consent_keyboard())]

    def _ask_phone_text(self) -> str:
        return (
            "Введите номер телефона, который будет использоваться для входа. "
            "Например: +7 900 000-00-00."
        )

    def _auth_flow(
        self,
        user: dict[str, Any],
        message: IncomingMessage,
        action: str | None,
    ) -> list[BotReply]:
        state = user.get("state") or "awaiting_phone"

        if state == "awaiting_name":
            return self._handle_name(user, message)

        if state == "awaiting_email":
            return self._handle_email(user, message, action)

        phone = normalize_phone(message.phone or message.text)
        if not phone:
            self.storage.update_user(user["id"], state="awaiting_phone")
            return [BotReply(self._ask_phone_text(), phone_keyboard())]

        existing = self.storage.find_user_by_phone(phone)
        if existing and existing.get("client_id"):
            updated = self.storage.update_user(
                user["id"],
                phone=phone,
                full_name=existing.get("full_name"),
                email=existing.get("email"),
                client_id=existing.get("client_id"),
                state="menu",
                temp_json="{}",
            )
            name = updated.get("full_name") or "рады видеть вас снова"
            return [self._main_menu_reply(f"Вход выполнен, {name}.")]

        self.storage.set_temp(user["id"], {"phone": phone})
        self.storage.update_user(user["id"], state="awaiting_name")
        return [
            BotReply(
                f"Номер {display_phone(phone)} принят. Теперь напишите имя и фамилию.",
                phone_keyboard(),
            )
        ]

    def _handle_name(self, user: dict[str, Any], message: IncomingMessage) -> list[BotReply]:
        full_name = (message.name or message.text or "").strip()
        if len(full_name) < 2:
            return [BotReply("Напишите, пожалуйста, имя и фамилию текстом.", phone_keyboard())]

        temp = self.storage.get_temp(user)
        temp["full_name"] = full_name
        self.storage.set_temp(user["id"], temp)
        self.storage.update_user(user["id"], state="awaiting_email")
        return [
            BotReply(
                "Если хотите, укажите email для связи. Можно пропустить.",
                skip_email_keyboard(),
            )
        ]

    def _handle_email(
        self,
        user: dict[str, Any],
        message: IncomingMessage,
        action: str | None,
    ) -> list[BotReply]:
        raw_email = (message.text or "").strip()
        email = "" if action == "skip_email" else raw_email
        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return [
                BotReply(
                    "Email выглядит некорректно. Напишите его еще раз или нажмите «Пропустить email».",
                    skip_email_keyboard(),
                )
            ]

        temp = self.storage.get_temp(user)
        phone = temp.get("phone")
        full_name = temp.get("full_name")
        if not phone or not full_name:
            self.storage.update_user(user["id"], state="awaiting_phone", temp_json="{}")
            return [BotReply(self._ask_phone_text(), phone_keyboard())]

        payload = {
            "phone": phone,
            "full_name": full_name,
            "name": full_name,
            "email": email,
        }
        try:
            response = self.api.create_client(payload)
        except ApiError as exc:
            return [
                BotReply(
                    "Не получилось создать профиль через API. "
                    f"Попробуйте чуть позже или обратитесь в поддержку.\n\nТехническая причина: {exc}",
                    phone_keyboard(),
                )
            ]

        client_id = pick_id(response)
        if not client_id:
            return [
                BotReply(
                    "API создало профиль, но не вернуло идентификатор клиента. "
                    "Нужно, чтобы POST /clients/ возвращал id или client_id.",
                    phone_keyboard(),
                )
            ]

        updated = self.storage.update_user(
            user["id"],
            phone=phone,
            full_name=full_name,
            email=email,
            client_id=client_id,
            state="menu",
            temp_json="{}",
        )
        self.storage.append_audit(
            actor=f"{message.platform}:{message.user_id}",
            action="client.registered",
            subject=client_id,
            details={"phone": phone},
        )
        template = self.storage.get_notification_template("registration_success")
        return [
            self._main_menu_reply(
                safe_format(template, name=updated.get("full_name") or "готово", phone=display_phone(phone))
            )
        ]

    def _menu_flow(
        self,
        user: dict[str, Any],
        message: IncomingMessage,
        action: str | None,
    ) -> list[BotReply]:
        if action in {None, "menu"}:
            return [self._main_menu_reply("Выберите нужный раздел.")]
        if action == "my_subscription":
            return [self._subscription_reply(user)]
        if action == "buy_subscription":
            return [self._subscriptions_list_reply()]
        if action == "select_subscription":
            return [self._select_subscription_reply(message)]
        if action == "confirm_subscription":
            return [self._connect_subscription_reply(user, message)]
        if action == "promotions":
            return [self._promotions_reply()]
        if action == "promo_join":
            return [
                BotReply(
                    "Отлично, отметила интерес к акции. Чтобы подключить абонемент, выберите тариф.",
                    self._subscriptions_list_reply().keyboard,
                )
            ]
        if action == "bonus_shop":
            return [self._bonus_shop_reply()]
        if action == "redeem_bonus_item":
            return [self._redeem_bonus_item_reply(user, message)]
        if action == "referral_rules":
            return [
                BotReply(
                    self.storage.get_setting("referral_rules"),
                    back_to_menu_keyboard(),
                )
            ]
        if action == "support":
            return [
                BotReply(
                    self.storage.get_setting("support_contacts"),
                    back_to_menu_keyboard(),
                )
            ]
        return [self._main_menu_reply("Не совсем поняла команду. Открою главное меню.")]

    def _main_menu_reply(self, text: str) -> BotReply:
        return BotReply(text=text, keyboard=main_menu_keyboard())

    def _subscription_reply(self, user: dict[str, Any]) -> BotReply:
        client_id = str(user["client_id"])
        try:
            subscription = self.api.get_client_subscription(client_id)
        except ApiError as exc:
            return BotReply(
                f"Не удалось получить подписку через API: {exc}",
                back_to_menu_keyboard(),
            )

        if not subscription:
            return BotReply(
                "Активной подписки пока нет. Можно выбрать и подключить абонемент в меню.",
                main_menu_keyboard(),
            )

        return BotReply(self._format_subscription(subscription), back_to_menu_keyboard())

    def _format_subscription(self, subscription: dict[str, Any]) -> str:
        status = subscription.get("status") or subscription.get("state") or "не указан"
        tariff = (
            subscription.get("tariff")
            or subscription.get("name")
            or subscription.get("title")
            or subscription.get("subscription_name")
            or "не указан"
        )
        start_date = subscription.get("start_date") or subscription.get("date_start") or "не указана"
        end_date = subscription.get("end_date") or subscription.get("date_end") or "не указана"
        services = (
            subscription.get("services_availability")
            or subscription.get("services")
            or subscription.get("available_services")
            or []
        )

        lines = [
            "Ваша подписка:",
            f"Статус: {status}",
            f"Тариф: {tariff}",
            f"Дата начала: {start_date}",
            f"Дата окончания: {end_date}",
        ]
        service_lines = self._format_services(services)
        if service_lines:
            lines.append("")
            lines.append("Доступные услуги:")
            lines.extend(service_lines)
        return "\n".join(lines)

    def _format_services(self, services: Any) -> list[str]:
        if not isinstance(services, list):
            return []
        result = []
        for item in services:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("title") or item.get("service_name") or "Услуга"
            left = first_present(item, "left", "remaining", "available")
            total = first_present(item, "total", "quantity", "limit")
            if left is not None and total is not None:
                result.append(f"- {name}: осталось {left} из {total}")
            elif left is not None:
                result.append(f"- {name}: осталось {left}")
            else:
                result.append(f"- {name}")
        return result

    def _subscriptions_list_reply(self) -> BotReply:
        try:
            subscriptions = self.api.list_subscriptions()
        except ApiError as exc:
            return BotReply(
                f"Не удалось получить список абонементов через API: {exc}",
                back_to_menu_keyboard(),
            )

        if not subscriptions:
            return BotReply(
                "Сейчас нет доступных абонементов для подключения.",
                back_to_menu_keyboard(),
            )

        lines = ["Доступные абонементы:"]
        for index, subscription in enumerate(subscriptions[:10], start=1):
            name = subscription.get("name") or subscription.get("title") or "Абонемент"
            price = subscription.get("price") or subscription.get("amount")
            description = subscription.get("description") or subscription.get("body") or ""
            suffix = f" — {price} руб." if price else ""
            lines.append(f"{index}. {name}{suffix}")
            if description:
                lines.append(str(description))
        return BotReply("\n".join(lines), subscriptions_keyboard(subscriptions))

    def _select_subscription_reply(self, message: IncomingMessage) -> BotReply:
        subscription_id = str((message.payload or {}).get("subscription_id") or "")
        if not subscription_id:
            return BotReply("Не нашла выбранный абонемент. Попробуйте выбрать еще раз.", back_to_menu_keyboard())
        return BotReply(
            "Подтвердите подключение абонемента. Оплата в боте не проводится.",
            confirm_subscription_keyboard(subscription_id),
        )

    def _connect_subscription_reply(self, user: dict[str, Any], message: IncomingMessage) -> BotReply:
        subscription_id = str((message.payload or {}).get("subscription_id") or "")
        if not subscription_id:
            return BotReply("Не нашла выбранный абонемент. Попробуйте выбрать еще раз.", back_to_menu_keyboard())

        try:
            response = self.api.connect_subscription(str(user["client_id"]), subscription_id)
        except ApiError as exc:
            return BotReply(
                f"Не удалось подключить абонемент через API: {exc}",
                back_to_menu_keyboard(),
            )

        subscription_name = (
            response.get("name")
            or response.get("title")
            or response.get("subscription_name")
            or subscription_id
        )
        template = self.storage.get_notification_template("subscription_connected")
        return self._main_menu_reply(safe_format(template, subscription=subscription_name))

    def _promotions_reply(self) -> BotReply:
        promotions = self.storage.list_promotions(active_only=True)
        if not promotions:
            return BotReply(
                self.storage.get_setting("empty_promotions_text"),
                back_to_menu_keyboard(),
            )
        lines = ["Актуальные акции:"]
        for promotion in promotions:
            lines.append("")
            lines.append(str(promotion["title"]))
            lines.append(str(promotion["body"]))
            if promotion.get("image_url"):
                lines.append(str(promotion["image_url"]))
        return BotReply("\n".join(lines), promotions_keyboard(promotions))

    def _bonus_shop_reply(self) -> BotReply:
        items = self.storage.list_shop_items(active_only=True)
        if not items:
            return BotReply(
                "В магазине бонусов пока нет активных товаров или услуг.",
                back_to_menu_keyboard(),
            )
        lines = ["Магазин бонусов:"]
        for item in items:
            lines.append("")
            lines.append(f"{item['title']} — {item['bonus_price']} бонусов")
            lines.append(str(item["description"]))
        return BotReply("\n".join(lines), shop_keyboard(items))

    def _redeem_bonus_item_reply(self, user: dict[str, Any], message: IncomingMessage) -> BotReply:
        raw_item_id = (message.payload or {}).get("item_id")
        try:
            item_id = int(raw_item_id)
        except (TypeError, ValueError):
            return BotReply("Не нашла позицию магазина. Откройте магазин еще раз.", back_to_menu_keyboard())

        item = self.storage.get_shop_item(item_id)
        if not item or not item.get("is_active"):
            return BotReply("Эта позиция сейчас недоступна.", back_to_menu_keyboard())

        self.storage.create_redemption(
            platform=message.platform,
            platform_user_id=message.user_id,
            client_id=str(user.get("client_id") or ""),
            phone=user.get("phone"),
            item_id=item_id,
        )
        self.storage.append_audit(
            actor=f"{message.platform}:{message.user_id}",
            action="bonus.redemption_requested",
            subject=str(item_id),
            details={"client_id": user.get("client_id")},
        )
        template = self.storage.get_notification_template("redemption_requested")
        return self._main_menu_reply(safe_format(template, item=item["title"]))
