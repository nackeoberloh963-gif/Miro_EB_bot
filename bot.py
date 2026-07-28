"""
Telegram -> Miro inbox bot.

Polls Telegram for new text messages and drops each one onto a Miro board
as a sticky note, arranged in a simple left-to-right, top-to-bottom grid.
Designed to run as a short-lived script from a GitHub Actions cron job:
all state (Telegram offset, next grid slot) is persisted to files in
state/ and committed back to the repo after each run.
"""

import json
import os
import sys
from pathlib import Path

import requests

STATE_DIR = Path(__file__).parent / "state"
OFFSET_FILE = STATE_DIR / "offset.txt"
POSITION_FILE = STATE_DIR / "position.json"

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
MIRO_TOKEN = os.environ["MIRO_ACCESS_TOKEN"]
MIRO_BOARD_ID = os.environ["MIRO_BOARD_ID"]
ALLOWED_CHAT_ID = os.environ.get("ALLOWED_CHAT_ID", "").strip()

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
MIRO_API = f"https://api.miro.com/v2/boards/{MIRO_BOARD_ID}/sticky_notes"

# Grid layout for sticky notes.
COLS = 6
COL_WIDTH = 260
ROW_HEIGHT = 260
STICKY_WIDTH = 199


def load_offset() -> int:
    if OFFSET_FILE.exists():
        return int(OFFSET_FILE.read_text().strip() or "0")
    return 0


def save_offset(offset: int) -> None:
    OFFSET_FILE.write_text(str(offset))


def load_position() -> dict:
    if POSITION_FILE.exists():
        return json.loads(POSITION_FILE.read_text())
    return {"index": 0}


def save_position(position: dict) -> None:
    POSITION_FILE.write_text(json.dumps(position))


def next_slot(position: dict) -> tuple[float, float]:
    index = position["index"]
    col = index % COLS
    row = index // COLS
    position["index"] = index + 1
    return col * COL_WIDTH, row * ROW_HEIGHT


def get_updates(offset: int) -> list[dict]:
    resp = requests.get(
        f"{TELEGRAM_API}/getUpdates",
        params={"offset": offset, "timeout": 0},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["result"]


def send_message(chat_id: int, text: str) -> None:
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
    except requests.RequestException:
        pass


def create_sticky(text: str, x: float, y: float) -> None:
    resp = requests.post(
        MIRO_API,
        headers={"Authorization": f"Bearer {MIRO_TOKEN}"},
        json={
            "data": {"content": text},
            "position": {"x": x, "y": y, "origin": "center"},
            "geometry": {"width": STICKY_WIDTH},
            "style": {"fillColor": "red"},
        },
        timeout=30,
    )
    resp.raise_for_status()


def main() -> None:
    offset = load_offset()
    position = load_position()

    updates = get_updates(offset)
    if not updates:
        return

    for update in updates:
        message = update.get("message")
        if not message or "text" not in message:
            offset = update["update_id"] + 1
            continue

        chat_id = message["chat"]["id"]
        if ALLOWED_CHAT_ID and str(chat_id) != ALLOWED_CHAT_ID:
            offset = update["update_id"] + 1
            continue

        text = message["text"]
        x, y = next_slot(position)

        try:
            create_sticky(text, x, y)
        except requests.RequestException as exc:
            print(f"Failed to create sticky for update {update['update_id']}: {exc}", file=sys.stderr)
            send_message(chat_id, "⚠️ Не получилось создать стикер, попробую позже")
            position["index"] -= 1  # give the slot back so it's reused on retry
            save_position(position)
            save_offset(offset)  # do not advance past the failed message
            raise

        send_message(chat_id, "✅ Добавлено на доску")
        offset = update["update_id"] + 1

    save_position(position)
    save_offset(offset)


if __name__ == "__main__":
    main()
