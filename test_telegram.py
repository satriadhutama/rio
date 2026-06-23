"""Test kirim pesan ke Telegram via HTTP POST (requests, tanpa python-telegram-bot)."""

import os
import sys

import requests

MESSAGE = "Halo Rio, alert trading IDX aktif!"


def send_telegram_message(token: str, chat_id: str, text: str) -> dict:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        sys.exit(
            "Set environment variable TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID "
            "terlebih dahulu, lalu jalankan ulang script ini."
        )

    result = send_telegram_message(token, chat_id, MESSAGE)
    print("Pesan terkirim ke chat", chat_id)
    print("Isi pesan:", result["result"]["text"])
