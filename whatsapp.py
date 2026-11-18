"""WhatsApp Cloud API helpers: .env loading, signature check, sending. Standard library only."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

API_VERSION = "v20.0"


def load_env(path: str | Path = ".env") -> None:
    """Load KEY=VALUE lines from a .env file into os.environ (existing vars win)."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    """Check Meta's X-Hub-Signature-256 header against the raw request body."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def extract_messages(payload: dict) -> list[dict]:
    """Pull (id, from, text) out of a WhatsApp webhook payload. Non-text messages get text=None."""
    out: list[dict] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []):
                text = msg.get("text", {}).get("body") if msg.get("type") == "text" else None
                out.append({"id": msg.get("id", ""), "from": msg.get("from", ""), "text": text})
    return out


def send_text(to: str, body: str) -> bool:
    """Send a text message. With no credentials configured, prints it (dry run) and returns False."""
    token = os.environ.get("WHATSAPP_TOKEN")
    phone_id = os.environ.get("WHATSAPP_PHONE_ID")
    body = body[:4000]
    if not token or not phone_id:
        print(f"[dry run -> {to}] {body}")
        return False
    req = urllib.request.Request(
        f"https://graph.facebook.com/{API_VERSION}/{phone_id}/messages",
        data=json.dumps({"messaging_product": "whatsapp", "to": to, "type": "text",
                         "text": {"body": body}}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15):
            return True
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"send failed to {to}: {e}")
        return False
