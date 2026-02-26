from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .models import Message, TaskItem

URGENT_KEYWORDS = {"urgent", "asap", "today", "tomorrow", "immediately", "deadline"}
BUSINESS_KEYWORDS = {"po", "samples", "shipment", "courier", "wash test", "approval", "invoice", "vendor", "order"}
NOISE_PATTERNS = [r"\botp\b", r"promotion", r"sale", r"bank alert", r"social"]


@dataclass(slots=True)
class StatusReport:
    urgent: list[TaskItem]
    replies_needed: list[TaskItem]
    follow_ups: list[TaskItem]
    low_priority: list[TaskItem]


class TaskStore:
    def __init__(self, db_path: str = "data/crazy_assistant.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
              task_id TEXT PRIMARY KEY,
              source TEXT NOT NULL,
              from_name TEXT NOT NULL,
              description TEXT NOT NULL,
              priority TEXT NOT NULL,
              due_at TEXT,
              needs_reply INTEGER NOT NULL,
              follow_up INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              completed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS control (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS contacts (
              name TEXT PRIMARY KEY,
              message_count INTEGER NOT NULL DEFAULT 0,
              last_seen TEXT NOT NULL,
              work_contact INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self.conn.commit()

    def upsert_tasks(self, tasks: Iterable[TaskItem]) -> None:
        for task in tasks:
            self.conn.execute(
                """
                INSERT INTO tasks(task_id, source, from_name, description, priority, due_at, needs_reply, follow_up, created_at, completed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                  priority=excluded.priority,
                  due_at=excluded.due_at,
                  needs_reply=excluded.needs_reply,
                  follow_up=excluded.follow_up
                """,
                (
                    task.task_id,
                    task.source,
                    task.from_name,
                    task.description,
                    task.priority,
                    task.due_at.isoformat() if task.due_at else None,
                    int(task.needs_reply),
                    int(task.follow_up),
                    task.created_at.isoformat(),
                    int(task.completed),
                ),
            )
        self.conn.commit()


    def update_contact_stats(self, messages: Iterable[Message]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for msg in messages:
            lower_text = f"{msg.subject} {msg.body}".lower()
            is_work = int(any(k in lower_text for k in BUSINESS_KEYWORDS))
            self.conn.execute(
                """
                INSERT INTO contacts(name, message_count, last_seen, work_contact)
                VALUES(?, 1, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                  message_count = contacts.message_count + 1,
                  last_seen = excluded.last_seen,
                  work_contact = MAX(contacts.work_contact, excluded.work_contact)
                """,
                (msg.sender[:200], now, is_work),
            )
        self.conn.commit()

    def is_enabled(self) -> bool:
        row = self.conn.execute("SELECT value FROM control WHERE key='enabled'").fetchone()
        if not row:
            return True
        return row["value"] == "1"

    def set_enabled(self, enabled: bool) -> None:
        self.conn.execute(
            "INSERT INTO control(key, value) VALUES('enabled', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("1" if enabled else "0",),
        )
        self.conn.commit()

    def open_tasks(self) -> list[TaskItem]:
        rows = self.conn.execute("SELECT * FROM tasks WHERE completed = 0 ORDER BY created_at DESC").fetchall()
        tasks = []
        for r in rows:
            tasks.append(
                TaskItem(
                    task_id=r["task_id"],
                    source=r["source"],
                    from_name=r["from_name"],
                    description=r["description"],
                    priority=r["priority"],
                    due_at=datetime.fromisoformat(r["due_at"]) if r["due_at"] else None,
                    needs_reply=bool(r["needs_reply"]),
                    follow_up=bool(r["follow_up"]),
                    created_at=datetime.fromisoformat(r["created_at"]),
                    completed=bool(r["completed"]),
                )
            )
        return tasks


class TaskEngine:
    def _is_noise(self, text: str) -> bool:
        lower_text = text.lower()
        return any(re.search(pat, lower_text) for pat in NOISE_PATTERNS)

    def _dedupe_id(self, msg: Message, action: str) -> str:
        key = f"{msg.source}|{msg.message_id}|{action}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _extract_due(self, text: str, received_at: datetime) -> datetime | None:
        lower = text.lower()
        if "today" in lower:
            return received_at.replace(hour=18, minute=0, second=0, microsecond=0)
        if "tomorrow" in lower:
            due = received_at + timedelta(days=1)
            return due.replace(hour=18, minute=0, second=0, microsecond=0)
        if "48 hour" in lower or "48 hours" in lower:
            return received_at + timedelta(hours=48)
        return None

    def _priority(self, text: str, due_at: datetime | None) -> str:
        lower = text.lower()
        if any(k in lower for k in ("shipment", "courier", "purchase order", "po")):
            return "HIGH"
        if any(word in lower for word in URGENT_KEYWORDS):
            return "HIGH"
        if due_at and due_at <= datetime.now(timezone.utc) + timedelta(hours=48):
            return "HIGH"
        if any(word in lower for word in ("vendor", "confirm", "sample", "approval")):
            return "MEDIUM"
        return "LOW"

    def detect_tasks(self, messages: Iterable[Message]) -> list[TaskItem]:
        tasks: list[TaskItem] = []
        for msg in messages:
            text = f"{msg.subject} {msg.body}".strip()
            if not text or self._is_noise(text):
                continue

            lower = text.lower()
            task_signals = {
                "please",
                "can you",
                "kindly",
                "need",
                "send",
                "check",
                "confirm",
                "reply",
                "follow up",
                "i will",
                "we will",
            }
            if not any(signal in lower for signal in (task_signals | BUSINESS_KEYWORDS)):
                continue

            due_at = self._extract_due(lower, msg.received_at)
            priority = self._priority(lower, due_at)
            needs_reply = "?" in text or "reply" in lower or "confirm" in lower
            follow_up = "i will" in lower or "we will" in lower
            action = self._to_action(text)
            tasks.append(
                TaskItem(
                    task_id=self._dedupe_id(msg, action),
                    source=msg.source,
                    from_name=msg.sender,
                    description=action,
                    priority=priority,
                    due_at=due_at,
                    needs_reply=needs_reply,
                    follow_up=follow_up,
                )
            )
        return tasks

    def _to_action(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= 140:
            return text
        return text[:137] + "..."


def build_status(tasks: list[TaskItem]) -> StatusReport:
    urgent = [t for t in tasks if t.priority == "HIGH"]
    replies = [t for t in tasks if t.needs_reply]
    follow = [t for t in tasks if t.follow_up]
    low = [t for t in tasks if t.priority == "LOW"]
    return StatusReport(urgent=urgent[:10], replies_needed=replies[:10], follow_ups=follow[:10], low_priority=low[:10])


def render_status(report: StatusReport) -> str:
    def lines(items: list[TaskItem]) -> str:
        return "\n".join(f"• [{i.source}] {i.description} (from {i.from_name}, {i.priority})" for i in items) or "• None"

    return (
        "WORK STATUS\n\n"
        "URGENT\n"
        f"{lines(report.urgent)}\n\n"
        "REPLIES NEEDED\n"
        f"{lines(report.replies_needed)}\n\n"
        "FOLLOW-UP\n"
        f"{lines(report.follow_ups)}\n\n"
        "END REPORT"
    )


def render_daily_summary(tasks: list[TaskItem]) -> str:
    report = build_status(tasks)
    to_do = [t for t in tasks if t.priority in {"HIGH", "MEDIUM"}][:10]

    def num(items: list[TaskItem]) -> str:
        if not items:
            return "1. None"
        return "\n".join(f"{idx+1}. [{t.source}] {t.description}" for idx, t in enumerate(items))

    return (
        "DAILY WORK SUMMARY\n\n"
        "URGENT TASKS\n" + num(report.urgent) + "\n\n"
        "TASKS TO DO\n" + num(to_do) + "\n\n"
        "REPLIES NEEDED\n" + num(report.replies_needed) + "\n\n"
        "FOLLOW UPS\n" + num(report.follow_ups) + "\n\n"
        "LOW PRIORITY\n" + num(report.low_priority)
    )
