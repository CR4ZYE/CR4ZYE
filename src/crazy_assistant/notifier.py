from __future__ import annotations

import json
import uuid
from pathlib import Path


class WhatsAppOutbox:
    """Local outbox writer consumed by whatsapp/bridge.js."""

    def __init__(self, outbox_path: str = "data/whatsapp_outbox.jsonl"):
        self.outbox_path = Path(outbox_path)
        self.outbox_path.parent.mkdir(parents=True, exist_ok=True)

    def send_text(self, chat_id: str, text: str) -> None:
        payload = {
            "id": str(uuid.uuid4()),
            "chatId": chat_id,
            "text": text.strip(),
        }
        with self.outbox_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
