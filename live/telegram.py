"""Telegram-Nachrichten. Fehler beim Senden stoppen den Bot nie."""
import os
import requests

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT = os.environ.get("TELEGRAM_CHAT")


def senden(text):
    print("TELEGRAM:", text.replace("\n", " | "))
    if not TOKEN or not CHAT: return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          data={"chat_id": CHAT, "text": text, "disable_web_page_preview": "true"}, timeout=15)
        return r.status_code == 200
    except requests.RequestException:
        return False
