#!/usr/bin/env python3
"""Chat with the bot in your terminal. No WhatsApp account needed.

    python simulate.py
    python simulate.py --phone 919800000000 --db demo.db
Type 'quit' to exit.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from engine import Bot
from storage import Store


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # piped output on Windows defaults to cp1252 and can't print emoji
        if not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phone", default="919800000000")
    ap.add_argument("--db", default=":memory:")
    ap.add_argument("--config", default=str(Path(__file__).parent / "config.json"))
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    bot = Bot(cfg, Store(args.db), notify=lambda m: print(f"\n  [owner alert] {m}"))
    print(f"--- {cfg['business_name']} (simulator). Say 'hi'. 'quit' exits. ---")
    while True:
        try:
            text = input("\nyou > ")
        except (EOFError, KeyboardInterrupt):
            break
        if text.strip().lower() in ("quit", "exit"):
            break
        print("\nbot > " + bot.handle(args.phone, text).replace("\n", "\n      "))


if __name__ == "__main__":
    main()
