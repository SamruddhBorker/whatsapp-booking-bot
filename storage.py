"""SQLite storage for the booking bot. Standard library only."""
from __future__ import annotations

import json
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  name TEXT NOT NULL,
  date TEXT NOT NULL,          -- YYYY-MM-DD
  time TEXT NOT NULL,          -- HH:MM
  party INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'confirmed',   -- confirmed | cancelled
  reminded INTEGER NOT NULL DEFAULT 0,
  created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_slot ON bookings(date, time, status);
CREATE INDEX IF NOT EXISTS idx_phone ON bookings(phone, status);
CREATE TABLE IF NOT EXISTS sessions (
  phone TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY);
"""


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.db.executescript(SCHEMA)

    # ---- sessions -------------------------------------------------------
    def get_session(self, phone: str) -> tuple[str, dict]:
        with self.lock:
            r = self.db.execute("SELECT state, data FROM sessions WHERE phone=?",
                                (phone,)).fetchone()
        return (r["state"], json.loads(r["data"])) if r else ("idle", {})

    def set_session(self, phone: str, state: str, data: dict) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO sessions(phone,state,data) VALUES(?,?,?) "
                "ON CONFLICT(phone) DO UPDATE SET state=excluded.state, data=excluded.data",
                (phone, state, json.dumps(data)))

    def clear_session(self, phone: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM sessions WHERE phone=?", (phone,))

    # ---- webhook de-duplication ----------------------------------------
    def mark_seen(self, message_id: str) -> bool:
        """True if this message id is new; False if it was already processed."""
        with self.lock:
            cur = self.db.execute("INSERT OR IGNORE INTO seen(id) VALUES(?)", (message_id,))
            return cur.rowcount == 1

    # ---- bookings -------------------------------------------------------
    def slot_count(self, date: str, time: str) -> int:
        with self.lock:
            return self.db.execute(
                "SELECT COUNT(*) FROM bookings WHERE date=? AND time=? AND status='confirmed'",
                (date, time)).fetchone()[0]

    def create_booking(self, phone: str, name: str, date: str, time: str, party: int,
                       max_per_slot: int, max_active: int, today: str):
        """Atomically create a booking. Returns the new id, or 'full' / 'limit'."""
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                taken = self.db.execute(
                    "SELECT COUNT(*) FROM bookings WHERE date=? AND time=? AND status='confirmed'",
                    (date, time)).fetchone()[0]
                if taken >= max_per_slot:
                    self.db.execute("ROLLBACK")
                    return "full"
                active = self.db.execute(
                    "SELECT COUNT(*) FROM bookings WHERE phone=? AND status='confirmed' AND date>=?",
                    (phone, today)).fetchone()[0]
                if active >= max_active:
                    self.db.execute("ROLLBACK")
                    return "limit"
                cur = self.db.execute(
                    "INSERT INTO bookings(phone,name,date,time,party) VALUES(?,?,?,?,?)",
                    (phone, name, date, time, party))
                self.db.execute("COMMIT")
                return cur.lastrowid
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def active_count(self, phone: str, today: str) -> int:
        with self.lock:
            return self.db.execute(
                "SELECT COUNT(*) FROM bookings WHERE phone=? AND status='confirmed' AND date>=?",
                (phone, today)).fetchone()[0]

    def upcoming(self, phone: str, today: str) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(
                "SELECT * FROM bookings WHERE phone=? AND status='confirmed' AND date>=? "
                "ORDER BY date, time", (phone, today)).fetchall()

    def cancel(self, booking_id: int, phone: str) -> bool:
        with self.lock:
            cur = self.db.execute(
                "UPDATE bookings SET status='cancelled' WHERE id=? AND phone=? AND status='confirmed'",
                (booking_id, phone))
            return cur.rowcount == 1

    def for_date(self, date: str) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(
                "SELECT * FROM bookings WHERE date=? AND status='confirmed' ORDER BY time",
                (date,)).fetchall()

    def mark_reminded(self, booking_id: int) -> None:
        with self.lock:
            self.db.execute("UPDATE bookings SET reminded=1 WHERE id=?", (booking_id,))
