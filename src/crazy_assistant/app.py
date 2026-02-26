from __future__ import annotations

import os
import time
from datetime import datetime

import schedule
from dotenv import load_dotenv

from .connectors import GmailConnector, OutlookConnector, WhatsAppConnector, load_config_from_env
from .engine import TaskEngine, TaskStore, build_status, render_daily_summary, render_status
from .notifier import WhatsAppOutbox


class CrazyAssistant:
    def __init__(self):
        load_dotenv()
        self.config = load_config_from_env()
        self.gmail = GmailConnector(self.config)
        self.outlook = OutlookConnector(self.config)
        self.whatsapp = WhatsAppConnector(self.config)
        self.engine = TaskEngine()
        self.store = TaskStore(os.environ.get("CRAZY_DB_PATH", "data/crazy_assistant.db"))
        self.outbox = WhatsAppOutbox(os.environ.get("CRAZY_WHATSAPP_OUTBOX", "data/whatsapp_outbox.jsonl"))
        self.owner_chat_id = os.environ.get("CRAZY_OWNER_CHAT_ID", "")

    def scan(self) -> None:
        all_messages = []

        for name, collector in (("WhatsApp", self.whatsapp.fetch_messages),):
            try:
                all_messages.extend(collector())
            except Exception as exc:  # noqa: BLE001
                error_message = f"{name} connection failed. Retrying next cycle. Error: {exc}"
                print(error_message)
                self._notify_owner(error_message)

        self._process_commands(all_messages)

        if not self.store.is_enabled():
            print("Crazy assistant paused.")
            return

        for name, collector in (
            ("Gmail", self.gmail.fetch_messages),
            ("Outlook", self.outlook.fetch_messages),
        ):
            try:
                all_messages.extend(collector())
            except Exception as exc:  # noqa: BLE001
                error_message = f"{name} connection failed. Retrying next cycle. Error: {exc}"
                print(error_message)
                self._notify_owner(error_message)

        tasks = self.engine.detect_tasks(all_messages)
        self.store.upsert_tasks(tasks)
        self.store.update_contact_stats(all_messages)
        print(f"[{datetime.now().isoformat()}] scanned {len(all_messages)} messages, stored {len(tasks)} tasks")

    def _notify_owner(self, message: str) -> None:
        if self.owner_chat_id:
            self.outbox.send_text(self.owner_chat_id, message)

    def _process_commands(self, messages):
        for msg in messages:
            if msg.source != "WhatsApp":
                continue
            if self.owner_chat_id and msg.thread_id != self.owner_chat_id:
                continue
            body = msg.body.strip().upper()
            if body == "STOP ASSISTANT":
                self.store.set_enabled(False)
                self.outbox.send_text(msg.thread_id or self.owner_chat_id, "Crazy paused. Send START ASSISTANT to resume.")
            elif body == "START ASSISTANT":
                self.store.set_enabled(True)
                self.outbox.send_text(msg.thread_id or self.owner_chat_id, "Crazy resumed. Monitoring is active.")
            elif body == "STATUS":
                self.outbox.send_text(msg.thread_id or self.owner_chat_id, self.current_status())

    def current_status(self) -> str:
        report = build_status(self.store.open_tasks())
        return render_status(report)

    def daily_summary(self) -> None:
        if not self.store.is_enabled():
            return
        summary = render_daily_summary(self.store.open_tasks())
        print(summary)
        self._notify_owner(summary)


def run() -> None:
    assistant = CrazyAssistant()
    assistant.scan()
    schedule.every(15).minutes.do(assistant.scan)
    schedule.every().day.at("09:00").do(assistant.daily_summary)

    while True:
        schedule.run_pending()
        time.sleep(2)


if __name__ == "__main__":
    run()
