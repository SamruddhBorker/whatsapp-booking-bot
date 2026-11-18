#!/usr/bin/env python3
"""Webhook server for the WhatsApp Cloud API.

    python server.py                 # listens on 127.0.0.1:8080
    python server.py --host 0.0.0.0 --port 8080

Set credentials in .env (see .env.example). Expose it to Meta over HTTPS
(for example with a Cloudflare Tunnel or ngrok) and use <public-url>/webhook
as the callback URL.
"""
from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import whatsapp
from engine import Bot
from storage import Store

MAX_BODY = 1_000_000
HERE = Path(__file__).parent


def make_bot(db_path: str = "bookings.db", cfg_path: str | Path = HERE / "config.json") -> Bot:
    cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))
    owner = os.environ.get("OWNER_PHONE")
    notify = (lambda msg: whatsapp.send_text(owner, msg)) if owner else None
    return Bot(cfg, Store(db_path), notify=notify)


def make_handler(bot: Bot):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: bytes = b"", ctype: str = "text/plain") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # Meta's one-time webhook verification
            url = urlparse(self.path)
            if url.path != "/webhook":
                return self._reply(404, b"not found")
            q = parse_qs(url.query)
            token = os.environ.get("VERIFY_TOKEN")
            if (token and q.get("hub.mode") == ["subscribe"]
                    and q.get("hub.verify_token") == [token]):
                return self._reply(200, q.get("hub.challenge", [""])[0].encode())
            self._reply(403, b"forbidden")

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/webhook":
                return self._reply(404, b"not found")
            n = int(self.headers.get("Content-Length", 0))
            if n > MAX_BODY:
                return self._reply(413, b"too large")
            raw = self.rfile.read(n)
            secret = os.environ.get("APP_SECRET")
            if secret and not whatsapp.verify_signature(
                    secret, raw, self.headers.get("X-Hub-Signature-256")):
                return self._reply(403, b"bad signature")
            try:
                payload = json.loads(raw)
            except ValueError:
                return self._reply(400, b"bad json")
            for msg in whatsapp.extract_messages(payload):
                if not msg["from"] or not bot.store.mark_seen(msg["id"] or f"x-{msg['from']}-{os.urandom(4).hex()}"):
                    continue  # retry of a message we already handled
                text = msg["text"]
                reply = (bot.handle(msg["from"], text) if text is not None
                         else "Sorry, I can only read text messages. Reply BOOK to reserve a table.")
                whatsapp.send_text(msg["from"], reply)
            self._reply(200, b"ok")  # always 200 fast so Meta doesn't retry

        def log_message(self, *a) -> None:
            pass

    return Handler


def main() -> None:
    whatsapp.load_env(HERE / ".env")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--db", default="bookings.db")
    args = ap.parse_args()
    if not os.environ.get("APP_SECRET"):
        print("WARNING: APP_SECRET not set, so request signatures are NOT verified. "
              "Set it before going live.")
    if not os.environ.get("WHATSAPP_TOKEN"):
        print("note: no WHATSAPP_TOKEN set, replies will be printed (dry run), not sent.")
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(make_bot(args.db)))
    print(f"Listening on http://{args.host}:{args.port}/webhook  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
