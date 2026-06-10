from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from bot.api_client import ApiError, ClinicApiClient
from bot.keyboards import (
    Keyboard,
    back_to_menu_keyboard,
    consent_keyboard,
    main_menu_keyboard,
    name_keyboard,
    phone_keyboard,
    promotions_keyboard,
    selected_subscription_keyboard,
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
    attachment_path: str | None = None


TEXT_ACTIONS = {
    "/start": "start",
    "start": "start",
    "начать": "start",
    "главное меню": "menu",
    "в главное меню": "menu",
    "🏠 в главное меню": "menu",
    "меню": "menu",
    "моя подписка": "my_subscription",
    "🦷 моя подписка": "my_subscription",
    "выбрать абонемент": "buy_subscription",
    "✨ выбрать абонемент": "buy_subscription",
    "подключить абонемент": "buy_subscription",
    "купить абонемент": "buy_subscription",
    "акции": "promotions",
    "🎁 акции": "promotions",
    "акции и скидки": "promotions",
    "помощь": "support",
    "💬 помощь": "support",
    "поддержка": "support",
    "пропустить email": "skip_email",
    "оставить заявку на оплату": "request_subscription_payment",
    "✅ оставить заявку на оплату": "request_subscription_payment",
    "перейти к оплате": "request_subscription_payment",
    "💳 перейти к оплате": "request_subscription_payment",
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


def first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def safe_format(template: str, **values: Any) -> str:
    class SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    return template.format_map(SafeDict(values))


def money(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    if amount.is_integer():
        return f"{int(amount):,}".replace(",", " ") + " руб."
    return f"{amount:,.2f}".replace(",", " ") + " руб."


def subscription_id(subscription: dict[str, Any]) -> str:
    return str(
        subscription.get("id")
        or subscription.get("subscription_id")
        or subscription.get("uuid")
        or subscription.get("code")
        or ""
    )


def subscription_name(subscription: dict[str, Any]) -> str:
    return str(
        subscription.get("name")
        or subscription.get("title")
        or subscription.get("subscription_name")
        or "Абонемент"
    )


def subscription_is_active(subscription: dict[str, Any] | None) -> bool:
    if not subscription:
        return False
    raw_status = str(subscription.get("status") or subscription.get("state") or "").casefold()
    if raw_status in {"inactive", "expired", "disabled", "cancelled", "canceled", "не активна", "истекла"}:
        return False
    end_date = subscription.get("end_date") or subscription.get("date_end")
    if not end_date:
        return True
    try:
        return date.fromisoformat(str(end_date)[:10]) >= date.today()
    except ValueError:
        return True


class BotEngine:
    def __init__(
        self,
        storage: Storage,
        api: ClinicApiClient,
        consent_file: str,
        consent_pdf_file: str = "Пользовательское соглашение.pdf",
    ) -> None:
        self.storage = storage
        self.api = api
        self.consent_file = consent_file
        self.consent_pdf_file = consent_pdf_file

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
            return [
                self._consent_reply()
            ]
        if not user.get("phone") or not user.get("client_id"):
            self.storage.update_user(user["id"], state="awaiting_phone")
            return [BotReply(self._ask_phone_text(), phone_keyboard())]
        return [self._main_menu_reply("Рада снова видеть вас 🙂 Что посмотрим?")]

    def _consent_text(self) -> str:
        return (
            "Здравствуйте! Я бот клиники Master, помогу быстро разобраться с абонементом: "
            "посмотреть статус, выбрать тариф и приобрести его.\n\n"
            "Нажимая кнопку «Принимаю», вы даете согласие стоматологической клинике "
            "на обработку персональных данных."
        )

    def _consent_reply(self) -> BotReply:
        attachment_path = self.consent_pdf_file if Path(self.consent_pdf_file).exists() else None
        return BotReply(
            self._consent_text(),
            consent_keyboard(),
            attachment_path=attachment_path,
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
            return [
                BotReply(
                    "✅ Спасибо! Теперь привяжем профиль к номеру телефона.",
                    phone_keyboard(),
                ),
                BotReply(self._ask_phone_text(), phone_keyboard()),
            ]

        if action == "decline_consent":
            return [
                BotReply(
                    "Понимаю. Без согласия я не смогу сохранить профиль и показать данные по абонементу. "
                    "Если решите продолжить, просто напишите /start.",
                    consent_keyboard(),
                )
            ]

        return [self._consent_reply()]

    def _ask_phone_text(self) -> str:
        return (
            "Напишите номер телефона, который хотите использовать для входа.\n\n"
            "Пример: +7 900 000-00-00\n"
            "VK не передает номер автоматически, поэтому его нужно отправить сообщением."
        )

    def _auth_flow(
        self,
        user: dict[str, Any],
        message: IncomingMessage,
        action: str | None,
    ) -> list[BotReply]:
        state = user.get("state") or "awaiting_phone"

        if state == "awaiting_name":
            return self._handle_name(user, message, action)

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
            return [self._main_menu_reply(f"✅ Вход выполнен, {name}. Все готово.")]

        self.storage.set_temp(user["id"], {"phone": phone})
        self.storage.update_user(user["id"], state="awaiting_name")
        return [
            BotReply(
                f"✅ Номер {display_phone(phone)} сохранила.\n\n"
                "Теперь укажите имя и фамилию. Так администратор сможет быстро найти заявку.",
                name_keyboard(message.name),
            )
        ]

    def _handle_name(
        self,
        user: dict[str, Any],
        message: IncomingMessage,
        action: str | None,
    ) -> list[BotReply]:
        payload_name = ""
        if message.payload and isinstance(message.payload, dict):
            payload_name = str(message.payload.get("name") or "")
        full_name = (payload_name if action == "use_vk_name" else message.text or message.name or "").strip()
        if len(full_name) < 2:
            return [BotReply("Напишите, пожалуйста, имя и фамилию текстом.", name_keyboard(message.name))]

        temp = self.storage.get_temp(user)
        temp["full_name"] = full_name
        self.storage.set_temp(user["id"], temp)
        self.storage.update_user(user["id"], state="awaiting_email")
        return [
            BotReply(
                "Отлично. Если удобно, укажите email для связи, чека или договора.\n\n"
                "Можно пропустить этот шаг.",
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
                    "Похоже, в email есть ошибка. Напишите его еще раз или нажмите «Пропустить email».",
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
            response = self.api.find_client_by_phone(phone) or self.api.create_client(payload)
            client_id = pick_id(response)
        except ApiError as exc:
            self.storage.append_audit(
                actor=f"{message.platform}:{message.user_id}",
                action="client.register_api_failed",
                subject=phone,
                details={"phone": phone, "error": str(exc), "status_code": exc.status_code},
            )
            return [
                BotReply(
                    "Не получилось сохранить профиль в API. Проверьте, пожалуйста, номер и попробуйте еще раз чуть позже.\n\n"
                    "Данные в подписках берутся только из API, поэтому без успешного сохранения в backend я не буду создавать локальный профиль.",
                    phone_keyboard(),
                )
            ]

        if not client_id:
            self.storage.append_audit(
                actor=f"{message.platform}:{message.user_id}",
                action="client.register_api_missing_id",
                subject=phone,
                details={"phone": phone},
            )
            return [
                BotReply(
                    "API создало или нашло клиента, но не вернуло `id`. Без `id` я не смогу получить подписку и привязать профиль.",
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
            details={"phone": phone, "api_synced": True},
        )
        template = self.storage.get_notification_template(
            "registration_success",
            "✅ Готово, {name}! Теперь можно посмотреть подписку или выбрать абонемент.",
        )
        text = safe_format(template, name=updated.get("full_name") or "готово", phone=display_phone(phone))
        return [self._main_menu_reply(text)]

    def _menu_flow(
        self,
        user: dict[str, Any],
        message: IncomingMessage,
        action: str | None,
    ) -> list[BotReply]:
        if action in {None, "menu"}:
            return [self._main_menu_reply("Выберите раздел. Я рядом и подскажу по абонементу 🙂")]
        if action == "my_subscription":
            return [self._subscription_reply(user)]
        if action == "buy_subscription":
            return [self._subscriptions_list_reply()]
        if action == "select_subscription":
            return [self._select_subscription_reply(message)]
        if action in {"confirm_subscription", "request_subscription_payment"}:
            return [self._request_subscription_payment_reply(user, message)]
        if action == "promotions":
            return [self._promotions_reply()]
        if action == "promo_join":
            return [
                BotReply(
                    "✅ Отлично. Покажу абонементы, а администратор учтет условия акции при подтверждении заявки.",
                    self._subscriptions_list_reply().keyboard,
                )
            ]
        if action == "support":
            return [
                BotReply(
                    self.storage.get_setting("support_contacts"),
                    back_to_menu_keyboard(),
                )
            ]
        return [self._main_menu_reply("Не совсем поняла команду. Открываю главное меню.")]

    def _main_menu_reply(self, text: str) -> BotReply:
        return BotReply(text=text, keyboard=main_menu_keyboard())

    def _subscription_reply(self, user: dict[str, Any]) -> BotReply:
        client_id = str(user["client_id"])
        try:
            subscription = self.api.get_client_subscription(client_id)
        except ApiError as exc:
            self.storage.append_audit(
                actor="bot",
                action="subscription.fetch_failed",
                subject=client_id,
                details={"error": str(exc), "status_code": exc.status_code},
            )
            return BotReply(
                "Не удалось получить подписку из API прямо сейчас. Обычно это временная задержка.\n\n"
                "Можно попробовать еще раз чуть позже или написать в поддержку.",
                back_to_menu_keyboard(),
            )

        if not subscription:
            return BotReply(
                "🦷 Ваша подписка\n"
                "Статус: Не активна\n\n"
                "Пока у вас нет активного абонемента. Выберите подходящий тариф: так проще заранее понимать, "
                "какие услуги уже включены и сколько визитов доступно.",
                main_menu_keyboard(),
            )

        return BotReply(self._format_subscription(subscription), back_to_menu_keyboard())

    def _format_subscription(self, subscription: dict[str, Any]) -> str:
        status = "Активна" if subscription_is_active(subscription) else "Не активна"
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
            "🦷 Ваша подписка",
            f"Статус: {status}",
            f"Тариф: {tariff}",
            f"Начало: {start_date}",
            f"Окончание: {end_date}",
        ]
        service_lines = self._format_services(services)
        if service_lines:
            lines.append("")
            lines.append("Что еще доступно:")
            lines.extend(service_lines)
        lines.append("")
        lines.append("Хотите продлить или перейти на другой тариф? Откройте «Выбрать абонемент».")
        return "\n".join(lines)

    def _format_services(self, services: Any) -> list[str]:
        if not isinstance(services, list):
            return []
        result = []
        for item in services:
            if not isinstance(item, dict):
                continue
            name = (
                item.get("name")
                or item.get("title")
                or item.get("service_name")
                or item.get("service_description")
                or "Услуга"
            )
            left = first_present(item, "left", "remaining", "available", "available_quantity", "quantity")
            total = first_present(item, "total", "quantity", "limit")
            if left is not None and total is not None:
                result.append(f"• {name}: осталось {left} из {total}")
            elif left is not None:
                result.append(f"• {name}: осталось {left}")
            else:
                result.append(f"• {name}")
        return result

    def _subscriptions_list_reply(self) -> BotReply:
        try:
            subscriptions = self.api.list_subscriptions_with_services()
        except ApiError as exc:
            self.storage.append_audit(
                actor="bot",
                action="subscriptions.fetch_failed",
                details={"error": str(exc), "status_code": exc.status_code},
            )
            return BotReply(
                "Не удалось получить список абонементов из API. Попробуйте чуть позже или напишите в поддержку.",
                back_to_menu_keyboard(),
            )

        if not subscriptions:
            return BotReply(
                "Сейчас нет доступных абонементов для подключения. Администратор сможет добавить тарифы через API.",
                back_to_menu_keyboard(),
            )

        lines = [
            "✨ Состав подписок",
            "Выберите тариф, который подходит под ваш план лечения и профилактики.",
            "",
        ]
        for index, subscription in enumerate(subscriptions[:10], start=1):
            name = subscription_name(subscription)
            price_value = money(subscription.get("price") or subscription.get("amount"))
            description = subscription.get("description") or subscription.get("body") or ""
            line = f"{index}. {name}"
            if price_value:
                line += f" — {price_value}"
            lines.append(line)
            if description:
                lines.append(str(description))
            included_services = subscription.get("included_services") or []
            if included_services:
                for service in included_services:
                    if not isinstance(service, dict):
                        continue
                    service_name = service.get("name") or "Услуга"
                    quantity = service.get("quantity")
                    quantity_text = f" — {quantity} шт." if quantity not in (None, "") else ""
                    lines.append(f"   • {service_name}{quantity_text}")
            else:
                lines.append("   • Состав тарифа уточняется")
            lines.append("")
        return BotReply("\n".join(lines), subscriptions_keyboard(subscriptions))

    def _select_subscription_reply(self, message: IncomingMessage) -> BotReply:
        selected_id = str((message.payload or {}).get("subscription_id") or "")
        if not selected_id:
            return BotReply("Не нашла выбранный абонемент. Попробуйте выбрать еще раз.", back_to_menu_keyboard())

        try:
            subscriptions = self.api.list_subscriptions()
        except ApiError:
            subscriptions = []
        selected = next((item for item in subscriptions if subscription_id(item) == selected_id), None)

        if selected:
            name = subscription_name(selected)
            price_value = money(selected.get("price") or selected.get("amount"))
            description = selected.get("description") or selected.get("body") or ""
        else:
            name = "выбранный абонемент"
            price_value = ""
            description = ""

        lines = [f"Вы выбрали: {name}"]
        if price_value:
            lines.append(f"Стоимость: {price_value}")
        if description:
            lines.extend(["", str(description)])
        lines.extend(
            [
                "",
                "Необходимо оплатить выбранный тариф, чтобы закрепить абонемент за вами. "
                "После оплаты он сразу появится в разделе «Моя подписка», а включенные услуги будут видны с остатками.",
            ]
        )
        return BotReply("\n".join(lines), selected_subscription_keyboard(selected_id))

    def _request_subscription_payment_reply(self, user: dict[str, Any], message: IncomingMessage) -> BotReply:
        selected_id = str((message.payload or {}).get("subscription_id") or "")
        if not selected_id:
            return BotReply("Не нашла выбранный абонемент. Откройте список тарифов еще раз.", back_to_menu_keyboard())

        subscription_title = self._subscription_title_by_id(selected_id)
        try:
            response = self.api.connect_subscription(str(user["client_id"]), selected_id)
        except ApiError as exc:
            self.storage.append_audit(
                actor=f"{message.platform}:{message.user_id}",
                action="subscription.payment_stub_api_failed",
                subject=str(user.get("client_id") or ""),
                details={"subscription_id": selected_id, "error": str(exc), "status_code": exc.status_code},
            )
            return BotReply(
                "Не получилось сохранить заявку на абонемент в API. Попробуйте чуть позже или напишите в поддержку.\n\n"
                "Оплата пока заглушка, но сама заявка все равно должна попасть в backend.",
                back_to_menu_keyboard(),
            )

        name = (
            response.get("name")
            or response.get("title")
            or response.get("subscription_name")
            or subscription_title
            or selected_id
        )
        self.storage.append_audit(
            actor=f"{message.platform}:{message.user_id}",
            action="subscription.payment_stub_requested",
            subject=str(user.get("client_id") or ""),
            details={"subscription_id": selected_id, "api_synced": True},
        )
        template = self.storage.get_notification_template(
            "subscription_connected",
            "✅ Оплата успешно прошла.\n\nАбонемент «{subscription}» активирован. Теперь он доступен в разделе «Моя подписка».",
        )
        text = safe_format(template, subscription=name)
        return self._main_menu_reply(text)

    def _subscription_title_by_id(self, selected_id: str) -> str | None:
        try:
            subscriptions = self.api.list_subscriptions()
        except ApiError:
            return None
        selected = next((item for item in subscriptions if subscription_id(item) == selected_id), None)
        return subscription_name(selected) if selected else None

    def _promotions_reply(self) -> BotReply:
        promotions = self.storage.list_promotions(active_only=True)
        if not promotions:
            return BotReply(
                self.storage.get_setting("empty_promotions_text"),
                back_to_menu_keyboard(),
            )
        lines = ["🎁 Актуальные предложения"]
        for promotion in promotions:
            lines.append("")
            lines.append(str(promotion["title"]))
            lines.append(str(promotion["body"]))
            if promotion.get("image_url"):
                lines.append(str(promotion["image_url"]))
        lines.append("")
        lines.append("Если предложение подходит, нажмите «Хочу воспользоваться».")
        return BotReply("\n".join(lines), promotions_keyboard(promotions))
