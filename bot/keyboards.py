from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Button:
    label: str
    action: str
    color: str = "secondary"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Keyboard:
    rows: list[list[Button]]
    one_time: bool = False
    inline: bool = False


def _button(label: str, action: str, color: str = "secondary", **data: Any) -> Button:
    return Button(label=label, action=action, color=color, data=data)


def consent_keyboard() -> Keyboard:
    return Keyboard(
        rows=[
            [
                _button("Принимаю", "accept_consent", "positive"),
                _button("Не принимаю", "decline_consent", "negative"),
            ]
        ],
        one_time=True,
    )


def phone_keyboard() -> Keyboard:
    return Keyboard(rows=[[_button("В главное меню", "menu")]])


def skip_email_keyboard() -> Keyboard:
    return Keyboard(rows=[[_button("Пропустить email", "skip_email")]], one_time=True)


def main_menu_keyboard() -> Keyboard:
    return Keyboard(
        rows=[
            [_button("Моя подписка", "my_subscription", "primary")],
            [_button("Выбрать абонемент", "buy_subscription", "positive")],
            [_button("Акции", "promotions"), _button("Помощь", "support")],
        ]
    )


def back_to_menu_keyboard() -> Keyboard:
    return Keyboard(rows=[[_button("В главное меню", "menu", "primary")]])


def subscriptions_keyboard(subscriptions: list[dict[str, Any]]) -> Keyboard:
    rows: list[list[Button]] = []
    for subscription in subscriptions[:10]:
        subscription_id = str(
            subscription.get("id")
            or subscription.get("subscription_id")
            or subscription.get("uuid")
            or subscription.get("code")
            or ""
        )
        if not subscription_id:
            continue
        label = str(subscription.get("name") or subscription.get("title") or "Абонемент")
        rows.append([_button(label[:40], "select_subscription", "primary", subscription_id=subscription_id)])
    rows.append([_button("В главное меню", "menu")])
    return Keyboard(rows=rows)


def selected_subscription_keyboard(subscription_id: str) -> Keyboard:
    return Keyboard(
        rows=[
            [
                _button(
                    "Оставить заявку на оплату",
                    "request_subscription_payment",
                    "positive",
                    subscription_id=subscription_id,
                )
            ],
            [_button("Выбрать другой", "buy_subscription"), _button("В главное меню", "menu")],
        ],
        one_time=True,
    )


def promotions_keyboard(promotions: list[dict[str, Any]]) -> Keyboard:
    rows: list[list[Button]] = []
    for promotion in promotions[:10]:
        rows.append(
            [
                _button(
                    "Хочу воспользоваться",
                    "promo_join",
                    "positive",
                    promotion_id=promotion["id"],
                )
            ]
        )
    rows.append([_button("В главное меню", "menu")])
    return Keyboard(rows=rows)


def to_vk_keyboard(keyboard: Keyboard | None) -> str | None:
    if keyboard is None:
        return None
    rows = []
    for row in keyboard.rows:
        vk_row = []
        for button in row:
            payload = {"action": button.action, **button.data}
            vk_row.append(
                {
                    "action": {
                        "type": "text",
                        "label": button.label,
                        "payload": json.dumps(payload, ensure_ascii=False),
                    },
                    "color": button.color,
                }
            )
        rows.append(vk_row)
    return json.dumps(
        {"one_time": keyboard.one_time, "inline": keyboard.inline, "buttons": rows},
        ensure_ascii=False,
    )


def render_cli_options(keyboard: Keyboard | None) -> str:
    if keyboard is None:
        return ""
    labels = [button.label for row in keyboard.rows for button in row]
    if not labels:
        return ""
    return "Кнопки: " + " | ".join(labels)
