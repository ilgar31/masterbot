from __future__ import annotations

import base64
import html
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from bot.config import Settings
from bot.storage import Storage


def _page(title: str, body: str) -> bytes:
    nav = """
    <nav>
      <a href="/">Обзор</a>
      <a href="/promotions">Акции</a>
      <a href="/settings">Правила и контакты</a>
      <a href="/shop">Магазин бонусов</a>
      <a href="/notifications">Уведомления</a>
      <a href="/redemptions">Заявки на списание</a>
    </nav>
    """
    document = f"""
    <!doctype html>
    <html lang="ru">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{html.escape(title)}</title>
      <style>
        :root {{
          color-scheme: light;
          --bg: #f7f7f4;
          --text: #1f2933;
          --muted: #667085;
          --line: #d7dce2;
          --accent: #0f766e;
          --accent-soft: #e6f4f1;
          --danger: #b42318;
        }}
        * {{ box-sizing: border-box; }}
        body {{
          margin: 0;
          background: var(--bg);
          color: var(--text);
          font: 15px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }}
        header {{
          padding: 22px 32px 14px;
          border-bottom: 1px solid var(--line);
          background: #ffffff;
        }}
        h1 {{ margin: 0 0 14px; font-size: 26px; letter-spacing: 0; }}
        h2 {{ margin: 32px 0 12px; font-size: 20px; letter-spacing: 0; }}
        nav {{ display: flex; flex-wrap: wrap; gap: 8px; }}
        nav a {{
          color: var(--accent);
          background: var(--accent-soft);
          border: 1px solid transparent;
          padding: 7px 10px;
          border-radius: 6px;
          text-decoration: none;
        }}
        main {{ max-width: 1120px; margin: 0 auto; padding: 28px 24px 48px; }}
        table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid var(--line); }}
        th, td {{ text-align: left; vertical-align: top; padding: 10px; border-bottom: 1px solid var(--line); }}
        th {{ color: var(--muted); font-weight: 600; background: #fafafa; }}
        form {{ display: grid; gap: 10px; max-width: 760px; margin: 0 0 18px; }}
        label {{ display: grid; gap: 5px; color: var(--muted); }}
        input, textarea {{
          width: 100%;
          border: 1px solid var(--line);
          border-radius: 6px;
          padding: 9px 10px;
          font: inherit;
          background: #fff;
          color: var(--text);
        }}
        textarea {{ min-height: 96px; resize: vertical; }}
        button {{
          width: fit-content;
          border: 0;
          border-radius: 6px;
          background: var(--accent);
          color: #fff;
          padding: 9px 13px;
          font: inherit;
          cursor: pointer;
        }}
        button.danger {{ background: var(--danger); }}
        .muted {{ color: var(--muted); }}
        .status {{ font-weight: 700; }}
      </style>
    </head>
    <body>
      <header>
        <h1>{html.escape(title)}</h1>
        {nav}
      </header>
      <main>{body}</main>
    </body>
    </html>
    """
    return document.encode("utf-8")


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _checkbox(name: str, checked: bool) -> str:
    checked_attr = " checked" if checked else ""
    return f'<label><span>Активно</span><input type="checkbox" name="{name}" value="1"{checked_attr}></label>'


def make_admin_handler(settings: Settings, storage: Storage) -> type[BaseHTTPRequestHandler]:
    class AdminHandler(BaseHTTPRequestHandler):
        server_version = "ClinicBotAdmin/1.0"

        def do_GET(self) -> None:
            if not self._authorized():
                return
            path = urlparse(self.path).path
            if path == "/":
                self._render("Админка бота", self._dashboard())
            elif path == "/promotions":
                self._render("Акции", self._promotions_page())
            elif path == "/settings":
                self._render("Правила и контакты", self._settings_page())
            elif path == "/shop":
                self._render("Магазин бонусов", self._shop_page())
            elif path == "/notifications":
                self._render("Уведомления", self._notifications_page())
            elif path == "/redemptions":
                self._render("Заявки на списание", self._redemptions_page())
            else:
                self._send_text(404, "not found")

        def do_POST(self) -> None:
            if not self._authorized():
                return
            path = urlparse(self.path).path
            form = self._form()
            if path == "/promotions":
                self._save_promotion(form)
                self._redirect("/promotions")
            elif path == "/settings":
                storage.set_setting("referral_rules", form.get("referral_rules", ""))
                storage.set_setting("support_contacts", form.get("support_contacts", ""))
                storage.set_setting("empty_promotions_text", form.get("empty_promotions_text", ""))
                self._redirect("/settings")
            elif path == "/shop":
                self._save_shop_item(form)
                self._redirect("/shop")
            elif path == "/notifications":
                key = form.get("key", "")
                text = form.get("text", "")
                if key:
                    storage.update_notification_template(key, text)
                self._redirect("/notifications")
            else:
                self._send_text(404, "not found")

        def _authorized(self) -> bool:
            expected = "Basic " + base64.b64encode(
                f"{settings.admin_login}:{settings.admin_password}".encode("utf-8")
            ).decode("ascii")
            actual = self.headers.get("Authorization", "")
            if hmac.compare_digest(expected, actual):
                return True
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Clinic bot admin"')
            self.end_headers()
            return False

        def _dashboard(self) -> str:
            promotions_count = len(storage.list_promotions())
            shop_count = len(storage.list_shop_items())
            redemptions_count = len(storage.list_redemptions())
            return f"""
            <p class="muted">Здесь редактируется контент, который бот показывает пациентам.</p>
            <table>
              <tr><th>Раздел</th><th>Количество</th></tr>
              <tr><td>Акции</td><td>{promotions_count}</td></tr>
              <tr><td>Позиции магазина бонусов</td><td>{shop_count}</td></tr>
              <tr><td>Заявки на списание бонусов</td><td>{redemptions_count}</td></tr>
            </table>
            """

        def _promotions_page(self) -> str:
            rows = []
            for promo in storage.list_promotions():
                active = "активна" if promo["is_active"] else "скрыта"
                rows.append(
                    f"""
                    <tr>
                      <td>{promo['id']}</td>
                      <td>
                        <form method="post" action="/promotions">
                          <input type="hidden" name="id" value="{promo['id']}">
                          <label><span>Название</span><input name="title" value="{_escape(promo['title'])}"></label>
                          <label><span>Описание</span><textarea name="body">{_escape(promo['body'])}</textarea></label>
                          <label><span>Ссылка на картинку</span><input name="image_url" value="{_escape(promo['image_url'])}"></label>
                          {_checkbox("is_active", bool(promo["is_active"]))}
                          <button name="action" value="save">Сохранить</button>
                        </form>
                      </td>
                      <td><span class="status">{active}</span></td>
                      <td>
                        <form method="post" action="/promotions">
                          <input type="hidden" name="id" value="{promo['id']}">
                          <button class="danger" name="action" value="delete">Удалить</button>
                        </form>
                      </td>
                    </tr>
                    """
                )
            table = "".join(rows) or '<tr><td colspan="4" class="muted">Акций пока нет.</td></tr>'
            return f"""
            <h2>Добавить акцию</h2>
            <form method="post" action="/promotions">
              <input type="hidden" name="action" value="create">
              <label><span>Название</span><input name="title" required></label>
              <label><span>Описание</span><textarea name="body" required></textarea></label>
              <label><span>Ссылка на картинку</span><input name="image_url"></label>
              {_checkbox("is_active", True)}
              <button>Добавить</button>
            </form>
            <h2>Список акций</h2>
            <table>
              <tr><th>ID</th><th>Данные</th><th>Статус</th><th>Действия</th></tr>
              {table}
            </table>
            """

        def _settings_page(self) -> str:
            return f"""
            <form method="post" action="/settings">
              <label>
                <span>Описание правил реферальной системы</span>
                <textarea name="referral_rules">{_escape(storage.get_setting("referral_rules"))}</textarea>
              </label>
              <label>
                <span>Контакты поддержки</span>
                <textarea name="support_contacts">{_escape(storage.get_setting("support_contacts"))}</textarea>
              </label>
              <label>
                <span>Текст, если акций нет</span>
                <textarea name="empty_promotions_text">{_escape(storage.get_setting("empty_promotions_text"))}</textarea>
              </label>
              <button>Сохранить</button>
            </form>
            """

        def _shop_page(self) -> str:
            rows = []
            for item in storage.list_shop_items():
                active = "активна" if item["is_active"] else "скрыта"
                rows.append(
                    f"""
                    <tr>
                      <td>{item['id']}</td>
                      <td>
                        <form method="post" action="/shop">
                          <input type="hidden" name="id" value="{item['id']}">
                          <label><span>Название</span><input name="title" value="{_escape(item['title'])}"></label>
                          <label><span>Описание</span><textarea name="description">{_escape(item['description'])}</textarea></label>
                          <label><span>Цена в бонусах</span><input type="number" min="0" name="bonus_price" value="{item['bonus_price']}"></label>
                          {_checkbox("is_active", bool(item["is_active"]))}
                          <button name="action" value="save">Сохранить</button>
                        </form>
                      </td>
                      <td><span class="status">{active}</span></td>
                      <td>
                        <form method="post" action="/shop">
                          <input type="hidden" name="id" value="{item['id']}">
                          <button class="danger" name="action" value="delete">Удалить</button>
                        </form>
                      </td>
                    </tr>
                    """
                )
            table = "".join(rows) or '<tr><td colspan="4" class="muted">Позиции пока не добавлены.</td></tr>'
            return f"""
            <h2>Добавить позицию</h2>
            <form method="post" action="/shop">
              <input type="hidden" name="action" value="create">
              <label><span>Название</span><input name="title" required></label>
              <label><span>Описание</span><textarea name="description" required></textarea></label>
              <label><span>Цена в бонусах</span><input type="number" min="0" name="bonus_price" value="0"></label>
              {_checkbox("is_active", True)}
              <button>Добавить</button>
            </form>
            <h2>Список позиций</h2>
            <table>
              <tr><th>ID</th><th>Данные</th><th>Статус</th><th>Действия</th></tr>
              {table}
            </table>
            """

        def _notifications_page(self) -> str:
            rows = []
            for template in storage.list_notification_templates():
                rows.append(
                    f"""
                    <tr>
                      <td>{_escape(template['label'])}<br><span class="muted">{_escape(template['key'])}</span></td>
                      <td>
                        <form method="post" action="/notifications">
                          <input type="hidden" name="key" value="{_escape(template['key'])}">
                          <textarea name="text">{_escape(template['text'])}</textarea>
                          <button>Сохранить</button>
                        </form>
                      </td>
                    </tr>
                    """
                )
            return f"""
            <p class="muted">Можно использовать переменные: {{name}}, {{phone}}, {{subscription}}, {{item}}.</p>
            <table>
              <tr><th>Шаблон</th><th>Текст</th></tr>
              {''.join(rows)}
            </table>
            """

        def _redemptions_page(self) -> str:
            rows = []
            for item in storage.list_redemptions():
                rows.append(
                    f"""
                    <tr>
                      <td>{item['id']}</td>
                      <td>{_escape(item.get('item_title') or item['item_id'])}</td>
                      <td>{_escape(item['phone'])}</td>
                      <td>{_escape(item['client_id'])}</td>
                      <td>{_escape(item['status'])}</td>
                      <td>{_escape(item['created_at'])}</td>
                    </tr>
                    """
                )
            table = "".join(rows) or '<tr><td colspan="6" class="muted">Заявок пока нет.</td></tr>'
            return f"""
            <table>
              <tr><th>ID</th><th>Позиция</th><th>Телефон</th><th>Клиент</th><th>Статус</th><th>Создано</th></tr>
              {table}
            </table>
            """

        def _save_promotion(self, form: dict[str, str]) -> None:
            action = form.get("action")
            promotion_id = int(form["id"]) if form.get("id") else None
            if action == "delete" and promotion_id:
                storage.delete_promotion(promotion_id)
                return
            title = form.get("title", "").strip()
            body = form.get("body", "").strip()
            image_url = form.get("image_url", "").strip()
            is_active = form.get("is_active") == "1"
            if not title or not body:
                return
            if promotion_id:
                storage.update_promotion(promotion_id, title, body, image_url, is_active)
            else:
                storage.create_promotion(title, body, image_url, is_active)

        def _save_shop_item(self, form: dict[str, str]) -> None:
            action = form.get("action")
            item_id = int(form["id"]) if form.get("id") else None
            if action == "delete" and item_id:
                storage.delete_shop_item(item_id)
                return
            title = form.get("title", "").strip()
            description = form.get("description", "").strip()
            try:
                bonus_price = int(form.get("bonus_price", "0"))
            except ValueError:
                bonus_price = 0
            is_active = form.get("is_active") == "1"
            if not title or not description:
                return
            if item_id:
                storage.update_shop_item(item_id, title, description, bonus_price, is_active)
            else:
                storage.create_shop_item(title, description, bonus_price, is_active)

        def _form(self) -> dict[str, str]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            data = parse_qs(raw, keep_blank_values=True)
            return {key: values[0] if values else "" for key, values in data.items()}

        def _render(self, title: str, body: str) -> None:
            payload = _page(title, body)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _redirect(self, location: str) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.end_headers()

        def _send_text(self, status: int, text: str) -> None:
            payload = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            print("%s - %s" % (self.address_string(), format % args))

    return AdminHandler


def run_admin_server(settings: Settings, storage: Storage) -> None:
    server = ThreadingHTTPServer(
        (settings.admin_host, settings.admin_port),
        make_admin_handler(settings=settings, storage=storage),
    )
    print(f"Админка запущена: http://{settings.admin_host}:{settings.admin_port}/")
    server.serve_forever()

