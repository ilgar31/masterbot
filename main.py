from __future__ import annotations

import argparse
import threading

from bot.admin_server import run_admin_server
from bot.api_client import ClinicApiClient
from bot.config import Settings
from bot.handlers import BotEngine, IncomingMessage
from bot.keyboards import render_cli_options
from bot.storage import Storage
from bot.vk_adapter import run_vk_callback_server, run_vk_longpoll


def build_engine(settings: Settings) -> BotEngine:
    storage = Storage(settings.database_path)
    storage.initialize()
    api = ClinicApiClient(
        base_url=settings.api_base_url,
        token=settings.api_token,
        token_header=settings.api_token_header,
        token_prefix=settings.api_token_prefix,
        api_key=settings.api_key,
        api_key_header=settings.api_key_header,
        timeout=settings.api_timeout,
    )
    return BotEngine(
        storage=storage,
        api=api,
        consent_file=settings.consent_file,
        consent_pdf_file=settings.consent_pdf_file,
    )


def run_cli(engine: BotEngine) -> None:
    print("Локальный режим бота. Напишите /start, чтобы начать. Для выхода: /exit")
    while True:
        text = input("> ").strip()
        if text in {"/exit", "exit", "quit"}:
            break

        replies = engine.handle(IncomingMessage(platform="cli", user_id="local", text=text))
        for reply in replies:
            print(reply.text)
            if reply.attachment_path:
                print(f"Файл: {reply.attachment_path}")
            options = render_cli_options(reply.keyboard)
            if options:
                print(options)


def main() -> None:
    parser = argparse.ArgumentParser(description="Чат-бот клиники")
    parser.add_argument(
        "--mode",
        choices=("vk", "longpoll", "admin", "all", "cli"),
        default="longpoll",
        help="Что запускать: VK Long Poll, VK Callback сервер, админку, оба сервиса или CLI-режим.",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    engine = build_engine(settings)

    if args.mode == "cli":
        run_cli(engine)
        return

    if args.mode == "admin":
        run_admin_server(settings=settings, storage=engine.storage)
        return

    if args.mode == "longpoll":
        run_vk_longpoll(settings=settings, engine=engine)
        return

    if args.mode == "all" and settings.admin_enabled:
        admin_thread = threading.Thread(
            target=run_admin_server,
            kwargs={"settings": settings, "storage": engine.storage},
            daemon=True,
        )
        admin_thread.start()
        print(f"Админка запущена: http://{settings.admin_host}:{settings.admin_port}/")

    if args.mode == "all":
        run_vk_longpoll(settings=settings, engine=engine)
        return

    run_vk_callback_server(settings=settings, engine=engine)


if __name__ == "__main__":
    main()
