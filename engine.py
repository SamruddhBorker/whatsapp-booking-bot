"""Conversation engine: turns an incoming text into a reply. No network code here."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Callable, Optional

from storage import Store

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
RESET_WORDS = {"menu", "restart", "stop", "back", "cancel", "start over", "reset"}
YES = {"yes", "y", "yeah", "yep", "confirm", "ok", "okay", "sure", "haan"}
NO = {"no", "n", "nope", "nah"}


# ---------------------------------------------------------------- parsing
def _mk(y: Optional[int], mo: int, d: int, today: date) -> Optional[date]:
    try:
        if y is None:
            cand = date(today.year, mo, d)
            return cand if cand >= today else date(today.year + 1, mo, d)
        return date(2000 + y if y < 100 else y, mo, d)
    except ValueError:
        return None


def parse_date(text: str, today: date) -> Optional[date]:
    t = text.lower().strip().rstrip(".!")
    if t in ("today", "tdy"):
        return today
    if t in ("tomorrow", "tmrw", "tmr", "tommorow"):
        return today + timedelta(days=1)
    for i, w in enumerate(WEEKDAYS):
        if t in (w, w[:3]):
            return today + timedelta(days=(i - today.weekday()) % 7)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return _mk(int(m[1]), int(m[2]), int(m[3]), today)
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?", t)
    if m:
        return _mk(int(m[3]) if m[3] else None, int(m[2]), int(m[1]), today)
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s*([a-z]{3})[a-z]*(?:\s+(\d{4}))?", t)
    if m and m[2] in MONTHS:
        return _mk(int(m[3]) if m[3] else None, MONTHS[m[2]], int(m[1]), today)
    m = re.fullmatch(r"([a-z]{3})[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?", t)
    if m and m[1] in MONTHS:
        return _mk(int(m[3]) if m[3] else None, MONTHS[m[1]], int(m[2]), today)
    return None


def parse_time(text: str) -> Optional[time]:
    """'7pm', '7:30 pm', '19:00', '7' (1-8 without am/pm is read as evening)."""
    t = text.lower().strip().replace(".", ":", 1) if re.search(r"\d\.\d", text) else text.lower().strip()
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", t)
    if not m:
        return None
    h, mi, ap = int(m[1]), int(m[2] or 0), m[3]
    if mi > 59:
        return None
    if ap:
        if not 1 <= h <= 12:
            return None
        if ap.startswith("p") and h < 12:
            h += 12
        elif ap.startswith("a") and h == 12:
            h = 0
    elif 1 <= h <= 8:
        h += 12
    if h > 23:
        return None
    return time(h, mi)


def fmt_time(t: time) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def fmt_date(d: date) -> str:
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _mins(t: time) -> int:
    return t.hour * 60 + t.minute


def _has(low: str, pattern: str) -> bool:
    return re.search(pattern, low) is not None


# ---------------------------------------------------------------- engine
class Bot:
    def __init__(self, cfg: dict, store: Store,
                 clock: Optional[Callable[[], datetime]] = None,
                 notify: Optional[Callable[[str], None]] = None) -> None:
        self.cfg = cfg
        self.store = store
        self.clock = clock or datetime.now
        self.notify = notify or (lambda msg: None)
        self.open = _hm(cfg["open"])
        self.close = _hm(cfg["close"])
        self.last = _hm(cfg["last_booking"])
        self.slot = int(cfg["slot_minutes"])

    # -- public API
    def handle(self, phone: str, text: str) -> str:
        text = (text or "").strip()[:300]
        low = text.lower()
        state, data = self.store.get_session(phone)
        if state != "idle" and low in RESET_WORDS:
            self.store.clear_session(phone)
            return "No problem, I've stopped that.\n\n" + self.help()
        handler = getattr(self, f"_st_{state}", self._st_idle)
        return handler(phone, text, low, data)

    # -- static replies
    def help(self) -> str:
        return (f"Hi! Welcome to {self.cfg['business_name']} 👋\n"
                "Reply with:\n"
                "• BOOK to reserve a table\n"
                "• MY BOOKING to see your reservation\n"
                "• CANCEL to cancel it\n"
                "• HOURS, LOCATION or MENU for info")

    def hours(self) -> str:
        closed = self.cfg.get("closed_days") or []
        extra = f"\nClosed on: {', '.join(d.title() for d in closed)}." if closed else ""
        return (f"We're open daily {fmt_time(self.open)} to {fmt_time(self.close)}."
                f"\nLast booking is at {fmt_time(self.last)}.{extra}")

    # -- validation
    def _check_day(self, d: date) -> Optional[str]:
        today = self.clock().date()
        if d < today:
            return "That date has already passed."
        limit = int(self.cfg["max_days_ahead"])
        if d > today + timedelta(days=limit):
            return f"We take bookings up to {limit} days ahead."
        if d.strftime("%A").lower() in [c.lower() for c in self.cfg.get("closed_days", [])]:
            return f"We're closed on {d.strftime('%A')}s."
        return None

    def _check_time(self, d: date, t: time) -> Optional[str]:
        if t < self.open:
            return f"We open at {fmt_time(self.open)}."
        if t > self.last:
            return f"Our last booking is at {fmt_time(self.last)}."
        if _mins(t) % self.slot:
            return (f"We book in {self.slot}-minute slots, e.g. "
                    f"{fmt_time(self._snap(t))}.")
        lead = timedelta(minutes=int(self.cfg.get("min_lead_minutes", 30)))
        if datetime.combine(d, t) < self.clock() + lead:
            return "That time has passed or is too soon. Please pick a later time."
        return None

    def _snap(self, t: time) -> time:
        m = (_mins(t) // self.slot) * self.slot
        return time(m // 60, m % 60)

    def _free(self, d: date, t: time) -> bool:
        return self.store.slot_count(d.isoformat(), t.strftime("%H:%M")) < int(self.cfg["tables_per_slot"])

    def _alternatives(self, d: date, wanted: time, n: int = 3) -> list[time]:
        slots = []
        m = _mins(self.open)
        while m <= _mins(self.last):
            t = time(m // 60, m % 60)
            if self._check_time(d, t) is None and self._free(d, t):
                slots.append(t)
            m += self.slot
        slots.sort(key=lambda s: abs(_mins(s) - _mins(wanted)))
        return sorted(slots[:n])

    # -- states
    def _st_idle(self, phone, text, low, data) -> str:
        for faq in self.cfg.get("faqs", []):
            if any(k in low for k in faq["keywords"]):
                return faq["answer"]
        if _has(low, r"\bcancel"):
            return self._start_cancel(phone)
        if _has(low, r"my (booking|reservation)|\bstatus\b|\bbooked\b"):
            return self._show(phone)
        if _has(low, r"\b(book|reserv|table)"):
            return self._start_booking(phone)
        if _has(low, r"\b(hours?|open|timings?|close|closing)\b"):
            return self.hours()
        if _has(low, r"\b(where|location|address|direction|map|find you)"):
            return self.cfg["location_text"]
        if _has(low, r"\b(menu|price|food|drink)"):
            return self.cfg["menu_text"]
        return self.help()

    def _start_booking(self, phone: str) -> str:
        today = self.clock().date().isoformat()
        if self.store.active_count(phone, today) >= int(self.cfg["max_active_bookings"]):
            return ("You already have the maximum number of upcoming bookings. "
                    "Reply CANCEL to free one up.")
        self.store.set_session(phone, "ask_date", {})
        return "Great, let's book a table! 🍽️\nWhat date? (e.g. today, tomorrow, Saturday, 25 Nov)"

    def _st_ask_date(self, phone, text, low, data) -> str:
        d = parse_date(text, self.clock().date())
        if d is None:
            return "Sorry, I didn't get the date. Try 'tomorrow', 'Saturday' or '25 Nov'."
        err = self._check_day(d)
        if err:
            return f"{err} Please pick another date."
        data["date"] = d.isoformat()
        self.store.set_session(phone, "ask_time", data)
        return f"{fmt_date(d)}, got it. What time? (e.g. 7pm, 19:30)"

    def _st_ask_time(self, phone, text, low, data) -> str:
        d = date.fromisoformat(data["date"])
        t = parse_time(text)
        if t is None:
            return "Sorry, I didn't get the time. Try '7pm' or '19:30'."
        err = self._check_time(d, t)
        if err:
            return err + " What time would you like?"
        if not self._free(d, t):
            alts = self._alternatives(d, t)
            if alts:
                return (f"{fmt_time(t)} is fully booked. Free around then: "
                        + ", ".join(fmt_time(a) for a in alts) + ". Which time?")
            return "That day looks fully booked. Reply MENU to start over and pick another date."
        data["time"] = t.strftime("%H:%M")
        self.store.set_session(phone, "ask_party", data)
        return f"How many people? (1 to {self.cfg['max_party']})"

    def _st_ask_party(self, phone, text, low, data) -> str:
        m = re.search(r"\d+", text)
        n = int(m[0]) if m else 0
        if n < 1:
            return "Please send the number of people, e.g. 4."
        if n > int(self.cfg["max_party"]):
            return (f"For groups over {self.cfg['max_party']}, please call us on "
                    f"{self.cfg['contact_phone']}. Or send a smaller number to continue.")
        data["party"] = n
        self.store.set_session(phone, "ask_name", data)
        return "And the name for the booking?"

    def _st_ask_name(self, phone, text, low, data) -> str:
        name = re.sub(r"[^\w .'\-]", "", text, flags=re.UNICODE).strip()[:60]
        if not name:
            return "Please send a name for the booking."
        data["name"] = name
        self.store.set_session(phone, "confirm", data)
        d = date.fromisoformat(data["date"])
        t = _hm(data["time"])
        return (f"Please confirm:\n📅 {fmt_date(d)}\n🕖 {fmt_time(t)}\n"
                f"👥 {data['party']} people\n👤 {name}\n\nReply YES to confirm or NO to cancel.")

    def _st_confirm(self, phone, text, low, data) -> str:
        if low in NO:
            self.store.clear_session(phone)
            return "Okay, nothing booked. " + "Reply BOOK any time to start again."
        if low not in YES:
            return "Please reply YES to confirm or NO to cancel."
        d = date.fromisoformat(data["date"])
        t = _hm(data["time"])
        res = self.store.create_booking(
            phone, data["name"], data["date"], data["time"], data["party"],
            int(self.cfg["tables_per_slot"]), int(self.cfg["max_active_bookings"]),
            self.clock().date().isoformat())
        if res == "full":
            self.store.set_session(phone, "ask_time", {"date": data["date"]})
            alts = self._alternatives(d, t)
            tail = (" Free: " + ", ".join(fmt_time(a) for a in alts) + ".") if alts else ""
            return f"Sorry, that slot was just taken.{tail} What time would you like?"
        if res == "limit":
            self.store.clear_session(phone)
            return "You already have the maximum number of upcoming bookings."
        self.store.clear_session(phone)
        ref = f"B{res:04d}"
        self.notify(f"New booking {ref}: {data['name']}, {data['party']} people, "
                    f"{fmt_date(d)} {fmt_time(t)} (+{phone})")
        return (f"✅ Booked! Reference {ref}\n{fmt_date(d)} at {fmt_time(t)} for "
                f"{data['party']}, under {data['name']}.\n"
                f"See you at {self.cfg['business_name']}! Reply CANCEL if plans change.")

    # -- my booking / cancel
    def _describe(self, b) -> str:
        return (f"B{b['id']:04d}: {fmt_date(date.fromisoformat(b['date']))} at "
                f"{fmt_time(_hm(b['time']))}, {b['party']} people ({b['name']})")

    def _show(self, phone: str) -> str:
        rows = self.store.upcoming(phone, self.clock().date().isoformat())
        if not rows:
            return "You have no upcoming bookings. Reply BOOK to make one."
        return "Your upcoming bookings:\n" + "\n".join(self._describe(r) for r in rows)

    def _start_cancel(self, phone: str) -> str:
        rows = self.store.upcoming(phone, self.clock().date().isoformat())
        if not rows:
            return "You have no upcoming bookings to cancel."
        if len(rows) == 1:
            self.store.set_session(phone, "cancel_confirm", {"id": rows[0]["id"]})
            return f"Cancel this booking?\n{self._describe(rows[0])}\nReply YES or NO."
        self.store.set_session(phone, "cancel_pick", {"ids": [r["id"] for r in rows]})
        lines = [f"{i}. {self._describe(r)}" for i, r in enumerate(rows, 1)]
        return "Which booking should I cancel? Reply with the number.\n" + "\n".join(lines)

    def _st_cancel_pick(self, phone, text, low, data) -> str:
        m = re.search(r"\d+", text)
        i = int(m[0]) if m else 0
        if not 1 <= i <= len(data["ids"]):
            return f"Please reply with a number from 1 to {len(data['ids'])}."
        self.store.set_session(phone, "cancel_confirm", {"id": data["ids"][i - 1]})
        return "Cancel it? Reply YES or NO."

    def _st_cancel_confirm(self, phone, text, low, data) -> str:
        if low in NO:
            self.store.clear_session(phone)
            return "Okay, your booking is kept. 👍"
        if low not in YES:
            return "Please reply YES to cancel or NO to keep it."
        ok = self.store.cancel(data["id"], phone)
        self.store.clear_session(phone)
        if ok:
            self.notify(f"Booking B{data['id']:04d} was cancelled by the customer (+{phone})")
            return f"Cancelled B{data['id']:04d}. Reply BOOK to make a new one."
        return "That booking was already cancelled."
