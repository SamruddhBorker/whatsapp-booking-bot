#!/usr/bin/env python3
"""Send tomorrow's booking reminders. Run once a day (cron / Task Scheduler).

    python reminders.py            # send reminders for tomorrow
    python reminders.py --dry-run  # just print them

NOTE: WhatsApp only lets a business message a customer first using a
pre-approved template once the 24-hour customer-service window has closed.
Free-text reminders like these work inside that window; for day-before
reminders at scale, create an approved "booking reminder" template in Meta
Business Manager and switch send_text() to the template API.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import whatsapp
from engine import fmt_time, _hm
from storage import Store

HERE = Path(__file__).parent


def build_reminders(store: Store, cfg: dict, today: date) -> list[tuple[int, str, str]]:
    tomorrow = (today + timedelta(days=1)).isoformat()
    out = []
    for b in store.for_date(tomorrow):
        if b["reminded"]:
            continue
        msg = (f"Reminder 🍽️ Your table at {cfg['business_name']} is tomorrow at "
               f"{fmt_time(_hm(b['time']))} for {b['party']} (ref B{b['id']:04d}). "
               "Reply CANCEL if you can't make it.")
        out.append((b["id"], b["phone"], msg))
    return out


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # reminder text contains emoji; piped Windows output is cp1252
        if not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")
    whatsapp.load_env(HERE / ".env")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="bookings.db")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    store = Store(args.db)
    items = build_reminders(store, cfg, datetime.now().date())
    for bid, phone, msg in items:
        if args.dry_run:
            print(f"[dry run -> {phone}] {msg}")
        elif whatsapp.send_text(phone, msg):
            store.mark_reminded(bid)
    print(f"{len(items)} reminder(s) {'previewed' if args.dry_run else 'processed'}")


if __name__ == "__main__":
    main()
