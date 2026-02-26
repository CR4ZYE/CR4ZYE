from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class Message:
    source: str
    message_id: str
    sender: str
    subject: str
    body: str
    received_at: datetime
    thread_id: Optional[str] = None
    starred: bool = False
    unread: bool = True


@dataclass(slots=True)
class TaskItem:
    task_id: str
    source: str
    from_name: str
    description: str
    priority: str
    due_at: Optional[datetime]
    needs_reply: bool
    follow_up: bool
    created_at: datetime = field(default_factory=datetime.utcnow)
    completed: bool = False
