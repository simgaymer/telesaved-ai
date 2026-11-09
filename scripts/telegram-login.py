"""One-time interactive Telegram login for TeleSaved.

Creates/authorizes the same session file app.py uses
(/opt/telesaved/telesaved_session.session) so the systemd service can start.

Run as root with the TeleSaved venv, with the service stopped:
    sudo systemctl stop telesaved
    sudo /opt/telesaved/venv/bin/python /opt/telesaved/scripts/telegram-login.py
    sudo systemctl start telesaved
"""
import json
import os
import sys

from telethon.sync import TelegramClient

APP_DIR = os.environ.get("APP_DIR", "/opt/telesaved")
CONFIG = os.path.join(APP_DIR, "config.json")
SESSION_FILE = os.path.join(APP_DIR, "telesaved_session")  # matches app.py line 112

with open(CONFIG, encoding="utf-8") as fh:
    cfg = json.load(fh)

api_id = int(cfg.get("api_id") or 0)
api_hash = str(cfg.get("api_hash") or "").strip()
if api_id in (0, 123456) or not api_hash or api_hash.startswith("replace_"):
    sys.exit(f"{CONFIG} still has placeholder credentials. "
             "Get api_id/api_hash from https://my.telegram.org -> API development tools.")

# start() prompts for phone number, login code, and 2FA password if enabled.
with TelegramClient(SESSION_FILE, api_id, api_hash) as client:
    me = client.get_me()
    print(f"Authorized as {me.first_name} (@{me.username or 'no-username'}). "
          f"Session saved to {SESSION_FILE}.session")
