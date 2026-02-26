from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from dateutil import parser as date_parser
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import msal

from .models import Message

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
OUTLOOK_SCOPES = ["Mail.Read"]


@dataclass(slots=True)
class ConnectorConfig:
    gmail_credentials_path: str
    gmail_token_path: str
    msal_client_id: str
    msal_tenant_id: str
    msal_cache_path: str
    whatsapp_events_file: str


class GmailConnector:
    def __init__(self, config: ConnectorConfig):
        self.config = config

    def _credentials(self) -> Credentials:
        creds = None
        token_path = Path(self.config.gmail_token_path)
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.config.gmail_credentials_path,
                    GMAIL_SCOPES,
                )
                creds = flow.run_local_server(port=0)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")
            token_path.chmod(0o600)
        return creds

    def fetch_messages(self, max_results: int = 25) -> list[Message]:
        service = build("gmail", "v1", credentials=self._credentials(), cache_discovery=False)
        label_queries = ["is:unread", "is:important", "is:starred", "in:inbox"]
        seen_ids: set[str] = set()
        messages: list[Message] = []

        for query in label_queries:
            response = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
            for item in response.get("messages", []):
                message_id = item["id"]
                if message_id in seen_ids:
                    continue
                seen_ids.add(message_id)
                full_msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=message_id, format="metadata", metadataHeaders=["From", "Subject", "Date"])
                    .execute()
                )
                headers = {h["name"]: h["value"] for h in full_msg.get("payload", {}).get("headers", [])}
                snippet = full_msg.get("snippet", "")
                labels = set(full_msg.get("labelIds", []))
                dt = date_parser.parse(headers.get("Date", datetime.now(timezone.utc).isoformat()))
                messages.append(
                    Message(
                        source="Gmail",
                        message_id=message_id,
                        sender=headers.get("From", "Unknown"),
                        subject=headers.get("Subject", ""),
                        body=snippet,
                        received_at=dt,
                        thread_id=full_msg.get("threadId"),
                        starred="STARRED" in labels,
                        unread="UNREAD" in labels,
                    )
                )
        return messages


class OutlookConnector:
    def __init__(self, config: ConnectorConfig):
        self.config = config

    def _access_token(self) -> str:
        authority = f"https://login.microsoftonline.com/{self.config.msal_tenant_id}"
        cache = msal.SerializableTokenCache()
        cache_path = Path(self.config.msal_cache_path)
        if cache_path.exists():
            cache.deserialize(cache_path.read_text(encoding="utf-8"))

        app = msal.PublicClientApplication(
            self.config.msal_client_id,
            authority=authority,
            token_cache=cache,
        )
        accounts = app.get_accounts()
        result = app.acquire_token_silent(OUTLOOK_SCOPES, account=accounts[0] if accounts else None)
        if not result:
            flow = app.initiate_device_flow(scopes=OUTLOOK_SCOPES)
            if "user_code" not in flow:
                raise RuntimeError("Could not create Microsoft device flow")
            print(flow["message"])
            result = app.acquire_token_by_device_flow(flow)

        if "access_token" not in result:
            raise RuntimeError(f"Outlook auth failed: {result.get('error_description', 'Unknown error')}")

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(cache.serialize(), encoding="utf-8")
        cache_path.chmod(0o600)
        return result["access_token"]

    def fetch_messages(self, top: int = 30) -> list[Message]:
        token = self._access_token()
        endpoint = (
            "https://graph.microsoft.com/v1.0/me/messages"
            "?$top={top}&$orderby=receivedDateTime desc"
            "&$select=id,subject,from,bodyPreview,receivedDateTime,isRead,importance,flag"
        ).format(top=top)
        response = requests.get(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        response.raise_for_status()

        messages: list[Message] = []
        for m in response.json().get("value", []):
            importance = str(m.get("importance", "")).lower()
            flagged = m.get("flag", {}).get("flagStatus") == "flagged"
            messages.append(
                Message(
                    source="Outlook",
                    message_id=m["id"],
                    sender=(m.get("from", {}).get("emailAddress", {}).get("name") or "Unknown"),
                    subject=m.get("subject", ""),
                    body=m.get("bodyPreview", ""),
                    received_at=date_parser.parse(m.get("receivedDateTime", datetime.now(timezone.utc).isoformat())),
                    starred=flagged or importance == "high",
                    unread=not m.get("isRead", True),
                )
            )
        return messages


class WhatsAppConnector:
    """Reads events generated by local Baileys bridge into a JSONL file."""

    def __init__(self, config: ConnectorConfig):
        self.events_file = Path(config.whatsapp_events_file)

    def fetch_messages(self) -> list[Message]:
        if not self.events_file.exists():
            return []
        events = self.events_file.read_text(encoding="utf-8").splitlines()
        messages: list[Message] = []
        for line in events[-200:]:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "message":
                continue
            messages.append(
                Message(
                    source="WhatsApp",
                    message_id=event["id"],
                    sender=event.get("sender", "Unknown"),
                    subject="",
                    body=event.get("body", ""),
                    received_at=date_parser.parse(event["timestamp"]),
                    thread_id=event.get("chatId"),
                    starred=bool(event.get("starred", False)),
                    unread=bool(event.get("unread", True)),
                )
            )
        return messages


def load_config_from_env() -> ConnectorConfig:
    return ConnectorConfig(
        gmail_credentials_path=os.environ.get("CRAZY_GMAIL_CREDENTIALS", "secrets/gmail_credentials.json"),
        gmail_token_path=os.environ.get("CRAZY_GMAIL_TOKEN", "secrets/gmail_token.json"),
        msal_client_id=os.environ.get("CRAZY_MS_CLIENT_ID", ""),
        msal_tenant_id=os.environ.get("CRAZY_MS_TENANT_ID", "common"),
        msal_cache_path=os.environ.get("CRAZY_MS_CACHE", "secrets/msal_cache.bin"),
        whatsapp_events_file=os.environ.get("CRAZY_WHATSAPP_EVENTS", "data/whatsapp_events.jsonl"),
    )
